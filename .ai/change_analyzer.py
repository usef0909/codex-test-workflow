#!/usr/bin/env python3
"""Record factual Git changes between commits as Markdown."""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


class AnalyzerError(Exception):
    """A user-facing Git, state, or filesystem error."""


def run_git_result(args, cwd):
    """Run Git without a shell and return the completed process."""
    try:
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise AnalyzerError("Unable to run Git: {}".format(exc))


def run_git(args, cwd):
    result = run_git_result(args, cwd)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise AnalyzerError(message or "Git command failed.")
    return result.stdout


def repository_root():
    script_directory = Path(__file__).resolve().parent
    raw_root = run_git(["rev-parse", "--show-toplevel"], script_directory)
    return Path(os.fsdecode(raw_root.strip())).resolve()


def resolve_commit(reference, root):
    expression = reference + "^{commit}"
    try:
        raw_sha = run_git(
            ["rev-parse", "--verify", "--end-of-options", expression], root
        )
    except AnalyzerError as exc:
        raise AnalyzerError(
            "Invalid commit reference {!r}: {}".format(reference, exc)
        )
    return raw_sha.decode("ascii", "strict").strip()


def commit_metadata(sha, root):
    raw = run_git(
        ["show", "-s", "--format=%H%x00%s%x00%cI", sha], root
    ).rstrip(b"\n")
    fields = raw.split(b"\x00", 2)
    if len(fields) != 3:
        raise AnalyzerError("Git returned incomplete metadata for {}.".format(sha))
    return {
        "sha": fields[0].decode("ascii", "strict"),
        "subject": fields[1].decode("utf-8", "replace"),
        "committer_date": fields[2].decode("utf-8", "replace"),
    }


def commit_parents(sha, root):
    raw = run_git(["rev-list", "--parents", "-n", "1", sha], root)
    fields = raw.decode("ascii", "strict").split()
    if not fields:
        raise AnalyzerError("Git returned no commit data for {}.".format(sha))
    return fields[1:]


def is_ancestor(ancestor, descendant, root):
    result = run_git_result(
        ["merge-base", "--is-ancestor", ancestor, descendant], root
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    message = result.stderr.decode("utf-8", "replace").strip()
    raise AnalyzerError(message or "Git could not check commit ancestry.")


def commits_after(previous_sha, head_sha, root):
    raw = run_git(
        ["rev-list", "--reverse", "--topo-order",
         previous_sha + ".." + head_sha],
        root,
    )
    return [line.decode("ascii", "strict") for line in raw.splitlines() if line]


def decode_path(raw_path):
    text = raw_path.decode("utf-8", "surrogateescape")
    output = []
    for character in text:
        codepoint = ord(character)
        if 0xDC80 <= codepoint <= 0xDCFF:
            output.append("\\x{:02x}".format(codepoint - 0xDC00))
        else:
            output.append(character)
    return "".join(output)


def read_changes(old_sha, new_sha, root):
    raw = run_git(
        ["diff", "--name-status", "-z", "--find-renames", "--find-copies",
         old_sha, new_sha],
        root,
    )
    fields = raw.split(b"\x00")
    if fields and fields[-1] == b"":
        fields.pop()

    changes = {
        "added": [], "modified": [], "deleted": [],
        "renamed": [], "copied": [],
    }
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii", "replace")
        index += 1
        code = status[:1]
        if code in ("R", "C"):
            if index + 1 >= len(fields):
                raise AnalyzerError("Git returned an incomplete {} status.".format(code))
            source = decode_path(fields[index])
            destination = decode_path(fields[index + 1])
            index += 2
            changes["renamed" if code == "R" else "copied"].append(
                (source, destination)
            )
        else:
            if index >= len(fields):
                raise AnalyzerError("Git returned an incomplete file status.")
            path = decode_path(fields[index])
            index += 1
            if code == "A":
                changes["added"].append(path)
            elif code == "D":
                changes["deleted"].append(path)
            else:
                changes["modified"].append(path)

    for key in ("added", "modified", "deleted", "renamed", "copied"):
        changes[key].sort()
    return changes


def changed_paths(changes):
    paths = []
    for key in ("added", "modified", "deleted"):
        paths.extend(changes[key])
    for source, destination in changes["renamed"]:
        paths.extend((source, destination))
    for _source, destination in changes["copied"]:
        paths.append(destination)
    return sorted(set(paths))


def category_for(path):
    normalized = path.replace("\\", "/")
    lower = normalized.lower()
    basename = lower.rsplit("/", 1)[-1]
    if lower == ".ai" or lower.startswith(".ai/"):
        return "Automation"
    if lower.startswith("knowledge/") and lower.endswith(".md"):
        return "Knowledge"
    if basename == "project.godot":
        return "Project Configuration"
    if lower.endswith(".tscn"):
        return "Godot Scenes"
    if lower.endswith(".gd"):
        return "Godot Scripts"
    if lower.endswith((".tres", ".res")):
        return "Godot Resources"
    return "Other"


def is_within(path, directory):
    normalized = path.replace("\\", "/").strip("/")
    prefix = directory.strip("/")
    return normalized == prefix or normalized.startswith(prefix + "/")


def markdown_code(value):
    safe = (
        str(value)
        .replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    longest_run = 0
    current_run = 0
    for character in safe:
        if character == "`":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    delimiter = "`" * (longest_run + 1)
    return delimiter + safe + delimiter


def render_change_list(title, entries, paired=False):
    if not entries:
        return []
    lines = ["### " + title, ""]
    if paired:
        lines.extend(
            "- {} → {}".format(markdown_code(source), markdown_code(destination))
            for source, destination in entries
        )
    else:
        lines.extend("- " + markdown_code(path) for path in entries)
    lines.append("")
    return lines


def build_record(old_metadata, new_metadata, changes, shortstat):
    lines = [
        "# Change Record", "", "## Commits", "",
        "- Previous commit: {}".format(markdown_code(old_metadata["sha"])),
        "- New commit: {}".format(markdown_code(new_metadata["sha"])),
        "- Commit message: {}".format(markdown_code(new_metadata["subject"])),
        "- Previous commit message: {}".format(markdown_code(old_metadata["subject"])),
        "", "## Summary", "",
    ]

    total = sum(len(changes[key]) for key in changes)
    if total == 0:
        lines.append("Git reports no file changes between these commits.")
    else:
        counts = []
        for key, label in (
            ("added", "added"), ("modified", "modified"),
            ("deleted", "deleted"), ("renamed", "renamed"),
            ("copied", "copied"),
        ):
            if changes[key]:
                counts.append("{} {}".format(len(changes[key]), label))
        lines.append(
            "Git reports {} changed file record(s): {}.".format(
                total, ", ".join(counts)
            )
        )
    lines.append("")

    lines.extend(["## Changed Files", ""])
    for key, label in (
        ("added", "Added"), ("modified", "Modified"), ("deleted", "Deleted")
    ):
        lines.extend(render_change_list(label, changes[key]))
    for key, label in (("renamed", "Renamed"), ("copied", "Copied")):
        lines.extend(render_change_list(label, changes[key], paired=True))

    categories = {
        "Godot Scenes": set(), "Godot Scripts": set(),
        "Godot Resources": set(), "Project Configuration": set(),
        "Knowledge": set(), "Automation": set(), "Other": set(),
    }
    for path in changed_paths(changes):
        categories[category_for(path)].add(path)

    lines.extend(["## File Categories", ""])
    for category in (
        "Godot Scenes", "Godot Scripts", "Godot Resources",
        "Project Configuration", "Knowledge", "Automation", "Other",
    ):
        paths = sorted(categories[category])
        if paths:
            lines.extend(render_change_list(category, paths))

    paths = changed_paths(changes)
    areas = {
        "Godot project": any(is_within(path, "(Game Name)") for path in paths),
        "Knowledge vault": any(is_within(path, "Knowledge") for path in paths),
        "Automation": any(is_within(path, ".ai") for path in paths),
    }
    lines.extend(["## Project Areas Affected", ""])
    for area, affected in areas.items():
        lines.append("- {}: {}".format(area, "yes" if affected else "no"))
    lines.append("")

    lines.extend(["## Diff Statistics", ""])
    if shortstat:
        lines.append("- Git shortstat: {}".format(markdown_code(shortstat)))
    else:
        lines.append("- Git shortstat: unavailable (Git produced no shortstat output).")
    lines.extend(["", "## Notes", "", "No additional Git-derived notes.", ""])
    return "\n".join(lines)


def prepare_record(old_sha, new_sha, root, allow_matching_existing=False):
    old_metadata = commit_metadata(old_sha, root)
    new_metadata = commit_metadata(new_sha, root)
    changes = read_changes(old_sha, new_sha, root)
    shortstat = run_git(
        ["diff", "--shortstat", "--find-renames", "--find-copies",
         old_sha, new_sha],
        root,
    ).decode("utf-8", "replace").strip()
    record = build_record(old_metadata, new_metadata, changes, shortstat)
    output_path = root / ".ai" / "changes" / (new_metadata["sha"] + ".md")
    expected = record.encode("utf-8", "backslashreplace")
    if output_path.exists():
        if allow_matching_existing:
            try:
                existing = output_path.read_bytes()
            except OSError as exc:
                raise AnalyzerError(
                    "Could not read existing change record {}: {}".format(
                        output_path.relative_to(root).as_posix(), exc
                    )
                )
            if existing == expected:
                return output_path, record, True
        raise AnalyzerError(
            "Change record already exists and will not be replaced: {}".format(
                output_path.relative_to(root).as_posix()
            )
        )
    return output_path, record, False


def write_record(output_path, record, root):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output_path.open("xb") as output:
            output.write(record.encode("utf-8", "backslashreplace"))
    except FileExistsError:
        raise AnalyzerError(
            "Change record already exists; refusing to replace it: {}".format(
                output_path.relative_to(root).as_posix()
            )
        )
    except OSError as exc:
        raise AnalyzerError(
            "Could not write {}: {}".format(
                output_path.relative_to(root).as_posix(), exc
            )
        )


def read_state(state_path, root):
    try:
        raw = state_path.read_bytes()
    except OSError as exc:
        raise AnalyzerError("Could not read analyzer state: {}".format(exc))
    match = re.fullmatch(rb"([0-9a-f]{40}|[0-9a-f]{64})\r?\n", raw)
    if not match:
        raise AnalyzerError(
            "State file must contain one full lowercase commit SHA followed by a newline: "
            ".ai/state/last_processed_commit"
        )
    return resolve_commit(match.group(1).decode("ascii"), root)


def write_state(state_path, head_sha):
    state_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        state_path.write_bytes((head_sha + "\n").encode("ascii"))
    except OSError as exc:
        raise AnalyzerError("Could not update analyzer state: {}".format(exc))


def manual_mode(old_reference, new_reference, root):
    old_sha = resolve_commit(old_reference, root)
    new_sha = resolve_commit(new_reference, root)
    output_path, record, already_exists = prepare_record(old_sha, new_sha, root)
    if already_exists:
        raise AnalyzerError("Manual mode will not reuse an existing change record.")
    write_record(output_path, record, root)
    print("Wrote {}".format(output_path.relative_to(root).as_posix()))
    return 0


def automatic_mode(root):
    head_sha = resolve_commit("HEAD", root)
    state_path = root / ".ai" / "state" / "last_processed_commit"

    if state_path.exists():
        previous_sha = read_state(state_path, root)
        if previous_sha == head_sha:
            print("Nothing new to analyze; HEAD matches the last processed commit.")
            return 0
        if not is_ancestor(previous_sha, head_sha, root):
            raise AnalyzerError(
                "Last processed commit is not an ancestor of HEAD; state was not changed."
            )
    else:
        parents = commit_parents(head_sha, root)
        if not parents:
            print(
                "HEAD has no parent commit; no previous commit was invented. "
                "No records or state were written."
            )
            return 0
        previous_sha = parents[0]

    new_commits = commits_after(previous_sha, head_sha, root)
    if not new_commits:
        print("Nothing new to analyze.")
        return 0

    prepared_records = []
    for commit_sha in new_commits:
        parents = commit_parents(commit_sha, root)
        if not parents:
            raise AnalyzerError(
                "Commit {} has no parent; automatic mode cannot invent one.".format(
                    commit_sha
                )
            )
        # Merge commits are compared with their first parent.
        prepared_records.append(
            prepare_record(
                parents[0], commit_sha, root, allow_matching_existing=True
            )
        )

    # All records are prepared before writing. Matching existing records are reused
    # without replacement, which also permits safe recovery after a partial prior run.
    for output_path, record, already_exists in prepared_records:
        if already_exists:
            print(
                "Reusing matching record {}".format(
                    output_path.relative_to(root).as_posix()
                )
            )
        else:
            write_record(output_path, record, root)

    write_state(state_path, head_sha)
    print(
        "Analyzed {} commit(s); updated state to {}.".format(
            len(prepared_records), head_sha
        )
    )
    for output_path, _record, _already_exists in prepared_records:
        if not _already_exists:
            print("Wrote {}".format(output_path.relative_to(root).as_posix()))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Record factual Git changes between commits."
    )
    parser.add_argument("old_commit", nargs="?", help="previous commit reference")
    parser.add_argument("new_commit", nargs="?", help="new commit reference")
    arguments = parser.parse_args(argv)

    if (arguments.old_commit is None) != (arguments.new_commit is None):
        parser.error("supply both commit references or no references for automatic mode")

    try:
        root = repository_root()
        if arguments.old_commit is None:
            return automatic_mode(root)
        return manual_mode(arguments.old_commit, arguments.new_commit, root)
    except AnalyzerError as exc:
        print("change_analyzer: {}".format(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print("change_analyzer: filesystem error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())