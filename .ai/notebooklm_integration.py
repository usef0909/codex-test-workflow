#!/usr/bin/env python3
"""Sync project Markdown and request NotebookLM reviews of inspection reports."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class IntegrationError(Exception):
    """An actionable local or NotebookLM CLI error."""


CLI = [sys.executable, "-m", "notebooklm"]
REVIEW_HEADINGS = [
    "# NotebookLM Change Review",
    "## Change",
    "## Affected Knowledge",
    "## Potentially Stale Knowledge",
    "## New Knowledge",
    "## Conflicts",
    "## Recommended Knowledge Updates",
    "## Uncertainty",
]


def run(args, cwd=None, input_bytes=None, check=True):
    try:
        result = subprocess.run(
            list(args), cwd=str(cwd) if cwd else None, input=input_bytes,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
    except OSError as exc:
        raise IntegrationError("Could not start {}: {}".format(args[0], exc))
    if check and result.returncode:
        detail = (result.stderr or result.stdout).decode("utf-8", "replace").strip()
        lowered = detail.lower()
        if any(word in lowered for word in ("auth", "cookie", "login", "unauthorized", "unauthenticated")):
            detail = "NotebookLM authentication failed. Run `python -m notebooklm login` and retry. " + detail
        raise IntegrationError(detail or "Command failed with exit code {}.".format(result.returncode))
    return result


def repository_root():
    script_dir = Path(__file__).resolve().parent
    result = run(["git", "rev-parse", "--show-toplevel"], cwd=script_dir)
    return Path(os.fsdecode(result.stdout.strip())).resolve()


def repo_name(root):
    result = run(["git", "config", "--get", "remote.origin.url"], cwd=root, check=False)
    raw = result.stdout.decode("utf-8", "replace").strip() if result.returncode == 0 else ""
    if raw:
        return raw.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1].removesuffix(".git")
    return root.name


def project_name(root):
    settings = root / "(Game Name)" / "project.godot"
    if not settings.is_file():
        return root.name
    match = re.search(r'^\s*config/name\s*=\s*"((?:[^"\\]|\\.)*)"', settings.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
    return match.group(1) if match else root.name


def state_paths(root):
    state = root / ".ai" / "state"
    return {
        "project": state / "notebooklm_project.json",
        "sources": state / "notebooklm_sources.json",
        "reviews": state / "notebooklm_reviews.json",
    }


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temp_name = stream.name
            json.dump(data, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
        raise IntegrationError("Could not write state {}: {}".format(path.name, exc))


def read_json(path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrationError("Cannot read state file {}: {}".format(path.name, exc))
    if not isinstance(value, dict):
        raise IntegrationError("State file {} must contain a JSON object.".format(path.name))
    return value


def defaults(root):
    return {
        "project": {
            "schema_version": 1, "repository_name": repo_name(root),
            "project_name": project_name(root), "notebook_id": None,
            "notebook_title": None, "configured_at": None,
        },
        "sources": {"schema_version": 1, "notebook_id": None, "sources": {}},
        "reviews": {"schema_version": 1, "notebook_id": None, "reviews": {}},
    }


def ensure_state_files(root):
    paths = state_paths(root)
    default = defaults(root)
    current = {}
    for key, path in paths.items():
        current[key] = read_json(path, default[key])
        if not path.exists():
            atomic_json(path, current[key])
    return paths, current


def save_state(path, state):
    atomic_json(path, state)


def cli_json(args):
    result = run(CLI + list(args))
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrationError("NotebookLM CLI did not return valid JSON: {}".format(exc))


def notebooklm_version():
    try:
        version = importlib.metadata.version("notebooklm-py")
    except importlib.metadata.PackageNotFoundError:
        raise IntegrationError("notebooklm-py is not installed for this Python. No packages were installed.")
    result = run(CLI + ["--version"], check=False)
    if result.returncode:
        raise IntegrationError("notebooklm-py is installed, but `python -m notebooklm` is unavailable: {}".format(result.stderr.decode("utf-8", "replace").strip()))
    return version, result.stdout.decode("utf-8", "replace").strip()


def check_authentication():
    payload = cli_json(["doctor", "--json"])
    auth = payload.get("checks", {}).get("auth", {})
    if auth.get("status") != "pass":
        detail = auth.get("detail", "authentication status could not be verified")
        raise IntegrationError("NotebookLM is not authenticated: {}. Run `python -m notebooklm login`.".format(detail))
    return payload


def configured_notebook(paths, states):
    project = states["project"]
    notebook_id = project.get("notebook_id")
    if not notebook_id:
        raise IntegrationError(
            "No project NotebookLM notebook is configured. Use `python .ai/notebooklm_integration.py list-notebooks` then `configure <notebook_id>`, or explicitly `create <title>`."
        )
    if states["sources"].get("notebook_id") not in (None, notebook_id):
        raise IntegrationError("Source state belongs to a different notebook; refusing to reuse or discard it.")
    if states["reviews"].get("notebook_id") not in (None, notebook_id):
        raise IntegrationError("Review state belongs to a different notebook; refusing to reuse or discard it.")
    return notebook_id


def find_objects(payload, key):
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        for child in payload.values():
            found = find_objects(child, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for child in payload:
            found = find_objects(child, key)
            if found is not None:
                return found
    return None


def list_notebooks():
    data = cli_json(["list", "--json"])
    notebooks = find_objects(data, "notebooks")
    if notebooks is None:
        raise IntegrationError("NotebookLM list output had no `notebooks` array.")
    return notebooks


def notebook_id_from(data):
    if isinstance(data, dict):
        for key in ("notebook_id", "id"):
            if isinstance(data.get(key), str) and data[key]:
                return data[key]
        for child in data.values():
            value = notebook_id_from(child)
            if value:
                return value
    elif isinstance(data, list):
        for child in data:
            value = notebook_id_from(child)
            if value:
                return value
    return None


def save_notebook_config(root, notebook_id, title):
    paths, states = ensure_state_files(root)
    for key in ("sources", "reviews"):
        existing = states[key].get("notebook_id")
        if existing not in (None, notebook_id):
            raise IntegrationError("{} state belongs to notebook {}; it was not discarded.".format(key.title(), existing))
    project = states["project"]
    project.update({"notebook_id": notebook_id, "notebook_title": title,
                    "configured_at": datetime.now(timezone.utc).isoformat()})
    states["sources"]["notebook_id"] = notebook_id
    states["reviews"]["notebook_id"] = notebook_id
    save_state(paths["sources"], states["sources"])
    save_state(paths["reviews"], states["reviews"])
    save_state(paths["project"], project)


def check_command(root, paths, states):
    version, cli_version = notebooklm_version()
    doctor = check_authentication()
    print("NotebookLM CLI available: yes ({})".format(cli_version))
    print("Installed notebooklm-py version: {}".format(version))
    print("Authentication: authenticated (default profile)")
    notebook_id = states["project"].get("notebook_id")
    print("Project notebook configured: {}".format("yes ({})".format(notebook_id) if notebook_id else "no"))
    return doctor


def remote_sources(notebook_id):
    payload = cli_json(["source", "list", "--json", "--notebook", notebook_id])
    sources = find_objects(payload, "sources")
    if sources is None:
        raise IntegrationError("NotebookLM source list output had no `sources` array.")
    normalized = []
    for item in sources:
        if isinstance(item, dict):
            source_id = item.get("id") or item.get("source_id")
            title = item.get("title")
            if source_id:
                normalized.append({"id": str(source_id), "title": str(title or "")})
    return normalized


def local_knowledge_files(root):
    base = root / "Knowledge"
    if not base.is_dir():
        raise IntegrationError("Knowledge directory is missing: {}".format(base))
    result = []
    for path in sorted(base.rglob("*.md")):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("Knowledge/.obsidian/") or "/.obsidian/" in relative:
            continue
        if path.is_file():
            result.append((relative, path))
    return result


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_title(relative_path, digest):
    return "{} [sha256:{}]".format(relative_path, digest)


def parse_source_add(payload):
    source = payload.get("source") if isinstance(payload, dict) else None
    source_id = source.get("id") if isinstance(source, dict) else None
    if not source_id:
        raise IntegrationError("NotebookLM reported source add success without a source ID; state was not advanced.")
    return str(source_id)


def add_file_source(notebook_id, relative_path, path, digest, remote):
    title = source_title(relative_path, digest)
    collisions = [item for item in remote if item["title"] == title]
    if collisions:
        raise IntegrationError(
            "A NotebookLM source titled {!r} already exists but is not tracked in local state. Refusing to create a duplicate; reconcile it explicitly first.".format(title)
        )
    payload = cli_json([
        "source", "add", "--type", "file", "--title", title,
        "--notebook", notebook_id, "--json", str(path.resolve()),
    ])
    source_id = parse_source_add(payload)
    remote.append({"id": source_id, "title": title})
    return source_id


def delete_tracked_source(notebook_id, source_id, remote):
    if any(item["id"] == source_id for item in remote):
        cli_json(["source", "delete", source_id, "--notebook", notebook_id, "--yes", "--json"])
        remote[:] = [item for item in remote if item["id"] != source_id]


def sync_files(root, paths, states, files, dry_run=False):
    notebook_id = states["project"].get("notebook_id") if dry_run else configured_notebook(paths, states)
    sources_path = paths["sources"]
    sources_state = states["sources"]
    if sources_state.get("notebook_id") is None:
        sources_state["notebook_id"] = notebook_id
    mapping = sources_state.setdefault("sources", {})
    if not isinstance(mapping, dict):
        raise IntegrationError("notebooklm_sources.json `sources` must be an object.")
    if dry_run:
        for relative, path in files:
            digest = sha256(path)
            entry = mapping.get(relative, {})
            action = "unchanged" if entry.get("sha256") == digest else ("update" if entry else "add")
            print("{}: {} (sha256 {})".format(action, relative, digest))
        present = {relative for relative, _ in files}
        for relative in sorted(k for k in mapping if k.startswith("Knowledge/") and k not in present):
            print("delete remote source: {}".format(relative))
        return

    remote = remote_sources(notebook_id)
    current_paths = {relative: path for relative, path in files}
    # Remove sources only when the local state explicitly tracks their IDs.
    for relative in sorted(k for k in mapping if k.startswith("Knowledge/") and k not in current_paths):
        entry = mapping[relative]
        source_id = entry.get("source_id") if isinstance(entry, dict) else None
        if source_id:
            delete_tracked_source(notebook_id, source_id, remote)
        del mapping[relative]
        save_state(sources_path, sources_state)

    for relative, path in files:
        digest = sha256(path)
        old = mapping.get(relative)
        if isinstance(old, dict) and old.get("sha256") == digest and any(
            item["id"] == old.get("source_id") for item in remote
        ):
            continue
        # Replacement is delete-then-add: the CLI supports refresh only for URL/
        # Drive sources, while append would corrupt a whole-file Markdown source.
        if isinstance(old, dict) and old.get("source_id"):
            delete_tracked_source(notebook_id, old["source_id"], remote)
            mapping.pop(relative, None)
            save_state(sources_path, sources_state)
        source_id = add_file_source(notebook_id, relative, path, digest, remote)
        mapping[relative] = {"source_id": source_id, "sha256": digest}
        save_state(sources_path, sources_state)
        print("Synchronized {}".format(relative))


def all_inspection_reports(root):
    output = []
    for path in sorted((root / ".ai" / "changes").glob("*-inspection.md")):
        match = re.fullmatch(r"([0-9a-f]{40}|[0-9a-f]{64})-inspection\.md", path.name)
        if match and path.is_file():
            output.append((match.group(1), path))
    return output


def review_prompt(commit_sha, report_text, knowledge_paths):
    knowledge_listing = "\n".join("- " + p for p in knowledge_paths) or "(No Knowledge Markdown sources were synchronized.)"
    return """You are reviewing one deterministic change inspection for the project repository.

Evidence and authority rules:
- Actual Godot source files, scenes, project settings, resources, and other actual project files are authoritative.
- Knowledge/ is derived project knowledge and may be stale or incomplete.
- The Change Inspector report is a deterministic description of Git changes, not an interpretation of behavior.
- Your analysis is an interpretation, not a project fact. Clearly separate evidence from interpretation.
- Use only the supplied sources. Do not invent missing information or infer runtime behavior without direct evidence.
- This is a standalone review of commit {commit}; do not rely on earlier chat turns.
- Produce exactly the headings below, in this order, with no extra headings. For every recommendation state target Knowledge file, what should change, why, and evidence. If nothing is established, say so explicitly.

Required output:
# NotebookLM Change Review

## Change

## Affected Knowledge

## Potentially Stale Knowledge

## New Knowledge

## Conflicts

## Recommended Knowledge Updates

## Uncertainty

Knowledge Markdown paths expected among the supplied sources:
{knowledge}

Change Inspector report for commit {commit}:
{report}
""".format(commit=commit_sha, knowledge=knowledge_listing, report=report_text)


def extract_answer(payload):
    if isinstance(payload, dict):
        if isinstance(payload.get("answer"), str):
            return payload["answer"]
        for value in payload.values():
            found = extract_answer(value)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = extract_answer(value)
            if found is not None:
                return found
    return None


def validate_review(answer):
    headings = re.findall(r"^#{1,6} .+$", answer, re.MULTILINE)
    if headings != REVIEW_HEADINGS:
        raise IntegrationError("NotebookLM did not return exactly the required review headings; no review file or processed-state entry was written.")


def write_review(path, text, overwrite=False):
    if not overwrite:
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                if not text.endswith("\n"):
                    stream.write("\n")
        except FileExistsError:
            raise IntegrationError("Review already exists; refusing to overwrite: {}".format(path.name))
        except OSError as exc:
            raise IntegrationError("Could not write review {}: {}".format(path.name, exc))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_text(path, text)


def review_matches_state(entry, report_path, review_path):
    return (
        isinstance(entry, dict)
        and review_path.is_file()
        and entry.get("inspection_sha256") == sha256(report_path)
        and entry.get("review_sha256") == sha256(review_path)
        and entry.get("status", "completed") in ("completed", "reconciled")
    )


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temp_name = stream.name
            stream.write(text)
            if not text.endswith("\n"):
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
        raise IntegrationError("Could not write {}: {}".format(path.name, exc))


def review_bytes(text):
    return (text if text.endswith("\n") else text + "\n").encode("utf-8")


def stage_review(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=path.name + ".",
            suffix=".pending", delete=False,
        ) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return Path(stream.name)
    except OSError as exc:
        raise IntegrationError("Could not stage review {}: {}".format(path.name, exc))


def publish_staged_review(temp_path, review_path, overwrite):
    try:
        if overwrite:
            os.replace(temp_path, review_path)
        else:
            # Hard-link publication is atomic and fails if the destination exists.
            # Both paths are in .ai/reviews, so they are on the same filesystem.
            os.link(temp_path, review_path)
            temp_path.unlink()
    except FileExistsError:
        raise IntegrationError("Review appeared during publication; refusing to overwrite: {}".format(review_path.name))
    except OSError as exc:
        raise IntegrationError("Could not publish staged review {}: {}".format(review_path.name, exc))


def inspection_source_id(root, states, commit_sha, inspection_hash):
    relative = ".ai/changes/{}-inspection.md".format(commit_sha)
    entry = states["sources"].get("sources", {}).get(relative, {})
    if isinstance(entry, dict) and entry.get("sha256") == inspection_hash:
        return entry.get("source_id")
    return None


def finish_review_state(root, states, commit_sha, report_path, review_path,
                        notebook_id, source_id, status="completed", extra=None):
    entry = {
        "commit_sha": commit_sha,
        "review_file": review_path.relative_to(root).as_posix(),
        "notebook_id": notebook_id,
        "inspection_source_id": source_id,
        "inspection_sha256": sha256(report_path),
        "review_sha256": sha256(review_path),
        "status": status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        entry.update(extra)
    states["reviews"].setdefault("reviews", {})[commit_sha] = entry
    states["reviews"]["notebook_id"] = notebook_id
    return entry


def review_one(root, paths, states, commit_sha, overwrite=False, dry_run=False):
    report_path = root / ".ai" / "changes" / (commit_sha + "-inspection.md")
    if not report_path.is_file():
        raise IntegrationError("No Change Inspector report exists for commit {}.".format(commit_sha))
    review_path = root / ".ai" / "reviews" / (commit_sha + "-review.md")
    reviews_state = states["reviews"]
    processed = reviews_state.setdefault("reviews", {})
    old = processed.get(commit_sha)
    report_hash = sha256(report_path)
    if old and not overwrite:
        if review_matches_state(old, report_path, review_path):
            print("Already reviewed: {}".format(review_path.relative_to(root).as_posix()))
            return
        raise IntegrationError("Review state or output does not match the current files; refusing to repeat or replace it: {}".format(review_path.relative_to(root)))
    if review_path.exists() and not overwrite:
        raise IntegrationError("Review file already exists without matching processed state; refusing to overwrite: {}".format(review_path.relative_to(root)))
    if dry_run:
        print("Would review {} using report {} and {} Knowledge file(s).".format(
            commit_sha, report_path.relative_to(root).as_posix(),
            len(local_knowledge_files(root))))
        return

    notebook_id = configured_notebook(paths, states)

    knowledge_files = local_knowledge_files(root)
    sync_files(root, paths, states, knowledge_files, dry_run=False)
    # Refresh in-memory state after sync writes it atomically.
    states["sources"] = read_json(paths["sources"], states["sources"])
    source_state = states["sources"].get("sources", {})
    remote = remote_sources(notebook_id)
    report_relative = report_path.relative_to(root).as_posix()
    report_entry = source_state.get(report_relative)
    if not isinstance(report_entry, dict) or not report_entry.get("source_id") or report_entry.get("sha256") != report_hash:
        source_id = add_file_source(notebook_id, report_relative, report_path, report_hash, remote)
        source_state[report_relative] = {"source_id": source_id, "sha256": report_hash}
        save_state(paths["sources"], states["sources"])
        print("Added inspection report source {}".format(report_relative))
    selected_sources = []
    for key, entry in sorted(source_state.items()):
        if key.startswith("Knowledge/") or key == report_relative:
            if isinstance(entry, dict) and entry.get("source_id"):
                selected_sources.append(entry["source_id"])
    if not selected_sources:
        raise IntegrationError("No synchronized Knowledge or inspection sources are available for the review.")
    prompt = review_prompt(commit_sha, report_path.read_text(encoding="utf-8"),
                           [relative for relative, _ in knowledge_files])
    command = CLI + ["ask", "--json", "--notebook", notebook_id]
    for source_id in selected_sources:
        command.extend(["--source", source_id])
    command.extend(["--prompt-file", "-"])
    result = run(command, input_bytes=prompt.encode("utf-8"))
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrationError("NotebookLM ask returned invalid JSON: {}".format(exc))
    answer = extract_answer(payload)
    if answer is None:
        raise IntegrationError("NotebookLM ask returned no answer; no review was recorded.")
    validate_review(answer)
    content = review_bytes(answer)
    staged = stage_review(review_path, content)
    entry = {
        "commit_sha": commit_sha,
        "review_file": review_path.relative_to(root).as_posix(),
        "notebook_id": notebook_id,
        "inspection_source_id": inspection_source_id(root, states, commit_sha, report_hash),
        "inspection_sha256": report_hash,
        "review_sha256": hashlib.sha256(content).hexdigest(),
        "status": "prepared",
        "overwrite_authorized": overwrite,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "temporary_review_file": staged.relative_to(root).as_posix(),
    }
    processed[commit_sha] = entry
    reviews_state["notebook_id"] = notebook_id
    try:
        # Journal the intended output before atomically publishing it. A later
        # failure can be repaired locally without asking NotebookLM again.
        save_state(paths["reviews"], reviews_state)
    except IntegrationError:
        staged.unlink(missing_ok=True)
        raise
    publish_staged_review(staged, review_path, overwrite=overwrite)
    entry.pop("temporary_review_file", None)
    entry.pop("prepared_at", None)
    entry.pop("overwrite_authorized", None)
    entry["status"] = "completed"
    entry["completed_at"] = datetime.now(timezone.utc).isoformat()
    entry["reviewed_at"] = entry["completed_at"]
    save_state(paths["reviews"], reviews_state)
    print("Wrote {}".format(review_path.relative_to(root).as_posix()))


def reconcile_review(root, paths, states, commit_sha):
    notebook_id = configured_notebook(paths, states)
    report_path = root / ".ai" / "changes" / (commit_sha + "-inspection.md")
    review_path = root / ".ai" / "reviews" / (commit_sha + "-review.md")
    if not report_path.is_file():
        raise IntegrationError("No Change Inspector report exists for commit {}.".format(commit_sha))
    processed = states["reviews"].setdefault("reviews", {})
    entry = processed.get(commit_sha)
    report_hash = sha256(report_path)

    if isinstance(entry, dict) and entry.get("status") == "prepared":
        temp_relative = entry.get("temporary_review_file")
        if not temp_relative:
            raise IntegrationError("Prepared review state has no staged-file path; no files were changed.")
        temp_path = (root / Path(temp_relative)).resolve()
        review_directory = (root / ".ai" / "reviews").resolve()
        if temp_path.parent != review_directory or not temp_path.name.startswith(review_path.name + "."):
            raise IntegrationError("Prepared review state points outside its expected staging file; no files were changed.")
        if entry.get("inspection_sha256") != report_hash or entry.get("notebook_id") != notebook_id:
            raise IntegrationError("Prepared review no longer matches the current inspection or notebook; no files were changed.")
        if review_path.is_file() and sha256(review_path) == entry.get("review_sha256"):
            # Handles interruption after publication but before the final state
            # update (including os.replace, which consumes the staged file).
            temp_path.unlink(missing_ok=True)
        else:
            if not temp_path.is_file() or sha256(temp_path) != entry.get("review_sha256"):
                raise IntegrationError("Prepared review staging file is missing or does not match state; no files were changed.")
            if review_path.exists():
                if entry.get("overwrite_authorized"):
                    publish_staged_review(temp_path, review_path, overwrite=True)
                else:
                    raise IntegrationError("A different review file exists beside the prepared journal; refusing to overwrite it.")
            else:
                publish_staged_review(temp_path, review_path, overwrite=False)

    if not review_path.is_file():
        raise IntegrationError("No existing review file is available to reconcile for commit {}.".format(commit_sha))
    answer = review_path.read_text(encoding="utf-8")
    validate_review(answer)
    current_hash = sha256(review_path)
    if isinstance(entry, dict):
        if entry.get("inspection_sha256") not in (None, report_hash):
            raise IntegrationError("Stored review references a different inspection report; state was not changed.")
        if entry.get("notebook_id") not in (None, notebook_id):
            raise IntegrationError("Stored review references a different NotebookLM notebook; state was not changed.")
        if entry.get("status") == "prepared" and entry.get("review_sha256") != current_hash:
            raise IntegrationError("Published review hash does not match prepared state; state was not changed.")
        if entry.get("status") in ("completed", "reconciled") and entry.get("review_sha256") == current_hash and entry.get("inspection_sha256") == report_hash:
            print("Review state already matches {}.".format(review_path.relative_to(root).as_posix()))
            return

    source_id = inspection_source_id(root, states, commit_sha, report_hash)
    extra = {}
    if isinstance(entry, dict) and entry.get("reviewed_at"):
        extra["reviewed_at"] = entry["reviewed_at"]
    if isinstance(entry, dict) and entry.get("status") == "prepared":
        # The review was already returned by NotebookLM; finish the journaled
        # transaction without contacting the service again.
        status = "completed"
    else:
        status = "reconciled"
        extra["reconciled_at"] = datetime.now(timezone.utc).isoformat()
    finish_review_state(root, states, commit_sha, report_path, review_path,
                        notebook_id, source_id, status=status, extra=extra)
    save_state(paths["reviews"], states["reviews"])
    print("Reconciled {} without contacting NotebookLM.".format(review_path.relative_to(root).as_posix()))


def cmd_check(root, paths, states, _args):
    check_command(root, paths, states)


def cmd_list(root, _paths, _states, _args):
    check_authentication()
    notebooks = list_notebooks()
    if not notebooks:
        print("No NotebookLM notebooks are visible to the authenticated account.")
    for item in notebooks:
        if isinstance(item, dict):
            print("{}\t{}".format(item.get("id", "?"), item.get("title", "(untitled)")))


def cmd_configure(root, _paths, _states, args):
    check_authentication()
    notebooks = list_notebooks()
    exact = [item for item in notebooks if isinstance(item, dict) and item.get("id") == args.notebook_id]
    if not exact:
        raise IntegrationError("Notebook ID was not found in the authenticated account's notebook list; configuration was not changed.")
    save_notebook_config(root, args.notebook_id, exact[0].get("title"))
    print("Configured project notebook {} ({})".format(exact[0].get("title", "(untitled)"), args.notebook_id))


def cmd_create(root, _paths, _states, args):
    check_authentication()
    payload = cli_json(["create", args.title, "--json"])
    notebook_id = notebook_id_from(payload)
    if not notebook_id:
        raise IntegrationError("Notebook creation returned no notebook ID; local configuration was not changed.")
    data = find_objects(payload, "notebook")
    title = args.title
    if isinstance(data, dict):
        title = data.get("title") or title
    save_notebook_config(root, notebook_id, title)
    print("Created and configured project notebook {} ({})".format(title, notebook_id))


def cmd_sync(root, paths, states, args):
    if not args.dry_run:
        check_authentication()
    sync_files(root, paths, states, local_knowledge_files(root), dry_run=args.dry_run)
    if args.dry_run:
        print("Dry run only; no NotebookLM or local state changes made.")


def cmd_review(root, paths, states, args):
    if not args.dry_run:
        check_authentication()
    sha = resolve_inspection_sha(root, args.commit)
    review_one(root, paths, states, sha, overwrite=args.overwrite, dry_run=args.dry_run)


def resolve_inspection_sha(root, value):
    matches = [sha for sha, _ in all_inspection_reports(root) if sha.startswith(value.lower())]
    if len(matches) != 1:
        raise IntegrationError("Commit {!r} matched {} inspection reports; provide a unique full or partial SHA.".format(value, len(matches)))
    return matches[0]


def cmd_review_pending(root, paths, states, args):
    pending = []
    known = states["reviews"].get("reviews", {})
    for sha, report in all_inspection_reports(root):
        target = root / ".ai" / "reviews" / (sha + "-review.md")
        entry = known.get(sha)
        if isinstance(entry, dict) and entry.get("status") == "prepared":
            raise IntegrationError("Review {} has a prepared recovery journal; run `reconcile {}` before reviewing again.".format(target.relative_to(root), sha))
        if review_matches_state(entry, report, target):
            continue
        if target.exists() and not args.overwrite:
            raise IntegrationError("Review exists without matching state: {}; refusing to overwrite.".format(target.relative_to(root)))
        pending.append(sha)
    if args.dry_run:
        print("Pending inspection reports: {}".format(len(pending)))
        for sha in pending:
            print("Would review {}".format(sha))
        return
    check_authentication()
    # Sync once before any NotebookLM review; review_one rechecks and syncs as a
    # safety boundary for direct/manual invocation.
    sync_files(root, paths, states, local_knowledge_files(root), dry_run=False)
    states["sources"] = read_json(paths["sources"], states["sources"])
    for sha in pending:
        review_one(root, paths, states, sha, overwrite=args.overwrite, dry_run=False)
        states["reviews"] = read_json(paths["reviews"], states["reviews"])


def build_parser():
    parser = argparse.ArgumentParser(description="Synchronize project Knowledge and review Change Inspector reports with NotebookLM.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="check CLI availability and authentication")
    sub.add_parser("list-notebooks", help="list authenticated NotebookLM notebooks")
    configure = sub.add_parser("configure", help="select an existing project notebook")
    configure.add_argument("notebook_id")
    create = sub.add_parser("create", help="create and configure a project notebook")
    create.add_argument("title")
    sync = sub.add_parser("sync", help="synchronize Knowledge Markdown")
    sync.add_argument("--dry-run", action="store_true")
    review = sub.add_parser("review", help="review one Change Inspector report")
    review.add_argument("commit")
    review.add_argument("--overwrite", action="store_true", help="explicitly replace an existing review")
    review.add_argument("--dry-run", action="store_true")
    reconcile = sub.add_parser("reconcile", help="repair state for an existing review without contacting NotebookLM")
    reconcile.add_argument("commit")
    pending = sub.add_parser("review-pending", help="review every pending inspection report")
    pending.add_argument("--overwrite", action="store_true", help="explicitly replace existing reviews")
    pending.add_argument("--dry-run", action="store_true")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        root = repository_root()
        paths, states = ensure_state_files(root)
        if args.command is None:
            check_authentication()
            configured_notebook(paths, states)
            sync_files(root, paths, states, local_knowledge_files(root), dry_run=False)
            states["sources"] = read_json(paths["sources"], states["sources"])
            pending = argparse.Namespace(overwrite=False, dry_run=False)
            cmd_review_pending(root, paths, states, pending)
            return 0
        handlers = {
            "check": cmd_check, "list-notebooks": cmd_list,
            "configure": cmd_configure, "create": cmd_create,
            "sync": cmd_sync, "review": cmd_review,
            "review-pending": cmd_review_pending,
        }
        if args.command == "reconcile":
            sha = resolve_inspection_sha(root, args.commit)
            reconcile_review(root, paths, states, sha)
            return 0
        handlers[args.command](root, paths, states, args)
        return 0
    except IntegrationError as exc:
        print("notebooklm_integration: {}".format(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print("notebooklm_integration: filesystem error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
