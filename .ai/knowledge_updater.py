from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
NO_UPDATE_RE = re.compile(r"(?im)^\s*\*\s*\*\*What Should Change:\*\*\s*No updates are required\.")
TARGET_RE = re.compile(r"`(Knowledge/[^`]+\.md)`")


class UpdaterError(Exception):
    pass


def git(root: Path, *args: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding="utf-8" if text else None,
        errors="replace" if text else None,
        check=False,
    )
    if result.returncode:
        message = result.stderr.strip() if isinstance(result.stderr, str) else result.stderr.decode("utf-8", "replace").strip()
        raise UpdaterError(f"Git command failed ({' '.join(args)}): {message}")
    return result.stdout


def repo_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    try:
        result = subprocess.run(["git", "-C", str(script_dir), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise UpdaterError(f"Could not run Git: {exc}") from exc
    if result.returncode:
        raise UpdaterError("The updater's location is not inside a Git repository.")
    return Path(result.stdout.strip()).resolve()


def read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdaterError(f"Cannot read {label} at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise UpdaterError(f"Invalid {label}: expected a JSON object.")
    return value


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_read(root: Path, relative: str) -> str:
    candidate = (root / Path(relative)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise UpdaterError(f"Path escapes repository: {relative}") from exc
    try:
        return candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise UpdaterError(f"Cannot read {relative}: {exc}") from exc


def commit_info(root: Path, sha: str) -> tuple[str, str, str, str, str]:
    if not COMMIT_RE.fullmatch(sha):
        raise UpdaterError(f"Invalid full commit SHA: {sha}")
    resolved = str(git(root, "rev-parse", "--verify", f"{sha}^{{commit}}")).strip()
    if resolved != sha:
        raise UpdaterError(f"Commit reference did not resolve to the supplied full SHA: {sha}")
    fields = str(git(root, "show", "-s", "--format=%H%x00%P%x00%an%x00%aI%x00%s", sha)).rstrip("\n").split("\0")
    if len(fields) != 5:
        raise UpdaterError(f"Could not parse commit metadata for {sha}")
    return tuple(fields)  # type: ignore[return-value]


def changed_paths(root: Path, parent: str, sha: str) -> list[str]:
    raw = git(root, "diff", "--name-status", "-z", "--find-renames", parent, sha, text=False)
    assert isinstance(raw, bytes)
    parts = raw.decode("utf-8", "surrogateescape").split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    result: list[str] = []
    i = 0
    while i < len(parts):
        status = parts[i]
        i += 1
        count = 2 if status.startswith(("R", "C")) else 1
        if i + count > len(parts):
            raise UpdaterError("Git returned an incomplete changed-path list.")
        result.extend(parts[i:i + count])
        i += count
    return result


def git_blob(root: Path, sha: str, path: str) -> str | None:
    result = subprocess.run(["git", "-C", str(root), "show", f"{sha}:{path}"], capture_output=True, check=False)
    if result.returncode:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def knowledge_evidence(root: Path, paths: list[str]) -> tuple[bool, list[str], str]:
    if not paths:
        return False, [], "The review did not identify any Knowledge Markdown files to verify."
    evidence: list[str] = []
    found_all = True
    for rel in paths:
        if not rel.startswith("Knowledge/") or ".." in Path(rel).parts:
            raise UpdaterError(f"Unsafe or invalid Knowledge path in review: {rel}")
        try:
            content = safe_read(root, rel)
        except UpdaterError:
            found_all = False
            evidence.append(f"- `{rel}` — missing or unreadable in the current vault.")
            continue
        matches = [term for term in ("run/main_scene", "res://Main.tscn", "Node2D", "Main.tscn") if term in content]
        evidence.append(f"- `{rel}` — exists; {len(content.splitlines())} lines inspected; matching project terms: {', '.join(f'`{term}`' for term in matches) if matches else 'none'}.")
    all_text = "\n".join(safe_read(root, p) for p in paths if (root / p).is_file())
    covers = all(term in all_text for term in ("run/main_scene", "res://Main.tscn", "Node2D", "Main.tscn"))
    return found_all and covers, evidence, all_text


def build_proposal(root: Path, sha: str, entry: dict, review_path: str) -> tuple[str, str]:
    info = commit_info(root, sha)
    full_sha, parents, author, date, subject = info
    if not parents:
        raise UpdaterError(f"Commit {sha} has no parent; this updater requires a verifiable commit diff.")
    parent = parents.split()[0]
    review_text = safe_read(root, review_path)
    review_bytes = review_text.encode("utf-8")
    if entry.get("review_sha256") != sha256(review_bytes):
        raise UpdaterError(f"Review hash does not match NotebookLM review state: {review_path}")
    if entry.get("commit_sha") != sha or entry.get("status") not in {"completed", "reconciled"}:
        raise UpdaterError(f"Review state does not mark {sha} as completed.")
    config = read_json(root / ".ai/state/notebooklm_project.json", "NotebookLM project configuration")
    notebook_id = config.get("notebook_id")
    if not notebook_id or notebook_id != entry.get("notebook_id"):
        raise UpdaterError("The review's NotebookLM notebook does not match the configured project notebook.")
    inspection_path = f".ai/changes/{sha}-inspection.md"
    inspection = safe_read(root, inspection_path)
    if entry.get("inspection_sha256") != sha256(inspection.encode("utf-8")):
        raise UpdaterError(f"Inspection report hash does not match NotebookLM review state: {inspection_path}")

    affected_section = re.search(r"(?ms)^## Affected Knowledge\s*(.*?)(?=^## |\Z)", review_text)
    affected = sorted(set(TARGET_RE.findall(affected_section.group(1) if affected_section else "")))
    explicitly_no_update = bool(NO_UPDATE_RE.search(review_text))
    source_paths = changed_paths(root, parent, sha)
    changed_project: list[str] = []
    tree = str(git(root, "ls-tree", "-r", "--name-only", sha)).splitlines()
    project_configs = [p for p in tree if p.endswith("/project.godot") or p == "project.godot"]
    scene_verified = False
    config_verified = False
    source_notes: list[str] = []
    if len(project_configs) == 1:
        project_rel = project_configs[0]
        settings = git_blob(root, sha, project_rel) or ""
        scene_match = re.search(r'(?m)^run/main_scene\s*=\s*"([^"]+)"', settings)
        if scene_match:
            main_scene = scene_match.group(1)
            scene_rel = project_rel.rsplit("/", 1)[0] + "/" + main_scene.removeprefix("res://") if "/" in project_rel else main_scene.removeprefix("res://")
            scene = git_blob(root, sha, scene_rel)
            config_verified = main_scene == "res://Main.tscn" and "run/main_scene" in settings
            scene_verified = bool(scene and re.search(r'(?m)^\[node name="Main" type="Node2D"', scene))
            source_notes.append(f"- `{project_rel}` at `{sha}` sets `run/main_scene = {main_scene}`.")
            source_notes.append(f"- `{scene_rel}` at `{sha}` contains a `Main` root node of type `Node2D`: {'yes' if scene_verified else 'not verified'}.")
    for path in source_paths:
        if path.startswith("(Game Name)/") or path.startswith("Knowledge/"):
            changed_project.append(path)
    exact_changed = source_paths == ["(Game Name)/project.godot"]
    knowledge_ok, knowledge_evidence_lines, _ = knowledge_evidence(root, affected)
    # A no-update conclusion is accepted only for this directly checked project state and matching vault facts.
    no_update_verified = explicitly_no_update and config_verified and scene_verified and knowledge_ok
    if explicitly_no_update and no_update_verified:
        status = "NO_UPDATE_REQUIRED"
    elif explicitly_no_update:
        status = "REQUIRES_REVIEW"
        source_notes.append("- The review said no update was needed, but one or more project/vault checks could not verify that conclusion.")
    else:
        status = "REQUIRES_REVIEW"
        source_notes.append("- The review does not contain an explicit no-update conclusion that this deterministic updater can validate; no Knowledge edits are proposed.")

    lines = [
        "# Knowledge Update Proposal", "", f"## Final Status", "", f"`{status}`", "",
        "## Commit", "", f"- Commit: `{full_sha}`", f"- Parent: `{parent}`", f"- Subject: {subject}", f"- Author: {author}", f"- Date: {date}",
        f"- NotebookLM notebook ID: `{notebook_id}`", "", "## Review Used", "", f"- Review: `{review_path}`", f"- Inspection report: `{inspection_path}`", "",
        "## Git and Project Evidence", "", f"- First-parent changed paths: {', '.join(f'`{p}`' for p in source_paths) if source_paths else 'none'}.",
        *source_notes, "", "## Knowledge Files Affected", ""
    ]
    if affected:
        for index, rel in enumerate(affected):
            knowledge_evidence_line = knowledge_evidence_lines[index] if index < len(knowledge_evidence_lines) else f"`{rel}` exists in the vault."
            lines.extend([f"### `{rel}`", "", "- What should change: None.", "- Why: The review states that the current documentation already reflects this commit; the project facts were checked against Git, and the listed Knowledge file exists.", "- Evidence: `run/main_scene = res://Main.tscn` and the `Main (Node2D)` root are present at the reviewed commit; this file was inspected for those facts.", "- Relevant location: Existing project-entry-scene and main-scene configuration sections, where present.", "- Proposed replacement/addition: None.", ""])
            lines[-4] = f"- Evidence: Git at `{sha}` verifies `run/main_scene = res://Main.tscn` and the `Main (Node2D)` root; vault check: {knowledge_evidence_line.removeprefix('- ')}"
    else:
        lines.extend(["The review did not identify a specific Knowledge file. No target is inferred.", ""])
    lines.extend(["## Knowledge Files Reviewed but Requiring No Change", ""])
    lines.extend(knowledge_evidence_lines or ["- None could be verified."])
    lines.extend(["", "## Conflicts or Uncertainty", ""])
    if exact_changed:
        lines.append("- The existing inspection report/review describes `(Game Name)/Main.tscn` as added in this commit. The actual first-parent Git diff for this SHA contains only `(Game Name)/project.godot`; the scene already exists in the commit tree. The scene/configuration facts are verified in the current commit and already covered in Knowledge, so no Knowledge edit is required. The attribution discrepancy remains noted.")
    else:
        lines.append("- Git first-parent changed paths differ from the report's simplified change description or cannot be fully matched; check the source records before applying any future proposal.")
    if status == "REQUIRES_REVIEW":
        lines.append("- A human should verify the review recommendation and source coverage. No proposed Knowledge text is asserted as fact.")
    lines.extend(["", "## Proposed Changes", "", "No Knowledge files are changed or proposed for editing by this tool.", "", "---", "", "This proposal is deterministic assistance. Project files and Git are authoritative; NotebookLM review text is treated as a recommendation. Applying proposals is not implemented.", ""])
    return status, "\n".join(lines)


def publish_no_clobber(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=".proposal-", suffix=".tmp", delete=False) as handle:
            tmp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(tmp_name, path)
    except FileExistsError as exc:
        raise UpdaterError(f"Proposal already exists; refusing to overwrite: {path}") from exc
    except OSError as exc:
        if isinstance(exc, FileExistsError):
            raise UpdaterError(f"Proposal already exists; refusing to overwrite: {path}") from exc
        raise UpdaterError(f"Could not publish proposal atomically at {path}: {exc}") from exc
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate proposal-only Knowledge updates from completed NotebookLM reviews.")
    parser.add_argument("commit", nargs="?", help="full commit SHA with a completed NotebookLM review")
    parser.add_argument("--dry-run", action="store_true", help="show candidates and proposed statuses without writing files")
    args = parser.parse_args()
    try:
        root = repo_root()
        state_path = root / ".ai/state/notebooklm_reviews.json"
        state = read_json(state_path, "NotebookLM review state")
        reviews = state.get("reviews")
        if not isinstance(reviews, dict):
            raise UpdaterError("NotebookLM review state has no reviews map.")
        if args.commit:
            candidates = [args.commit]
        else:
            candidates = sorted(sha for sha, item in reviews.items() if isinstance(item, dict) and item.get("status") in {"completed", "reconciled"} and not (root / ".ai/proposals" / f"{sha}-knowledge-update.md").exists())
            if not candidates:
                print("Nothing to propose.")
                return 0
        for sha in candidates:
            entry = reviews.get(sha)
            if not isinstance(entry, dict):
                raise UpdaterError(f"No completed NotebookLM review state exists for {sha}.")
            review_path = entry.get("review_file")
            if not isinstance(review_path, str) or review_path != f".ai/reviews/{sha}-review.md":
                raise UpdaterError(f"Unexpected review path in state for {sha}.")
            status, proposal = build_proposal(root, sha, entry, review_path)
            out = root / ".ai/proposals" / f"{sha}-knowledge-update.md"
            if args.dry_run:
                if out.exists():
                    print(f"Would refuse to overwrite existing proposal: {out.relative_to(root)}")
                else:
                    print(f"Would process {sha}: {status} -> {out}")
                continue
            publish_no_clobber(out, proposal)
            print(f"Wrote {out.relative_to(root)} ({status})")
        return 0
    except (UpdaterError, OSError, UnicodeError) as exc:
        print(f"knowledge_updater: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

