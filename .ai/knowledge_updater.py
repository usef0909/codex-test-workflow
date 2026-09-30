from __future__ import annotations

import argparse
import hashlib
import importlib.util
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

# These policies select concise append-only documentation from independently
# verified project facts. NotebookLM prose does not participate in selection.
APPLICATION_TARGETS = {
    "development configuration.md": ("## Godot project settings", "Project input actions and their configured bindings."),
    "project overview.md": ("## Current state", "The player scene, its script, and its placement in the entry scene."),
    "architecture overview.md": ("## Verified relationships", "Scene composition, script attachment, and movement input dependency."),
    "change records.md": ("# Change Records", "Repository paths recorded as changed by this commit."),
    "code inventory.md": ("## Tracked code", "The Player script's inheritance, exported speed, physics method, and movement calls."),
    "project assets and resources.md": ("## Scene resource", "The Player scene, attached script, and displayed icon texture relationship."),
    "scene inventory.md": ("## Main scene", "The Main-to-Player scene instance and the Player scene's local structure."),
    "systems inventory.md": ("## Current status", "The movement script's physics/input calls and configured movement actions."),
}
SYSTEMS_DOCUMENTATION_TARGET = "Knowledge/Systems/Systems Inventory.md"
DEVELOPMENT_CONFIGURATION_TARGET = "Knowledge/00 Project/Development Configuration.md"
SYSTEMS_REPLACEMENT_TARGET = "Knowledge/Systems/Systems Inventory.md"
SYSTEMS_REPLACEMENT_COMMIT = "5472bacbe12818379b9a20771885b63ea4725ea0"
SYSTEMS_REPLACEMENT_ANCHOR = ("# Systems Inventory", "## Current status")
SYSTEMS_REPLACEMENT_OLD_PASSAGE = (
    "No gameplay or technical runtime systems are implemented in the inspected project files. "
    "The project has a single bare entry scene, `Main.tscn`, whose root is a `Node2D` named `Main`; "
    "it has no children or attached script."
)
CODE_INVENTORY_REPLACEMENT_TARGET = "Knowledge/Code/Code Inventory.md"
CODE_INVENTORY_REPLACEMENT_COMMIT = "5472bacbe12818379b9a20771885b63ea4725ea0"
CODE_INVENTORY_REPLACEMENT_ANCHOR = ("# Code Inventory", "## Tracked code")
CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE = (
    "No scripts or source-code files are present in the inspected `(Game Name)/` project tree. "
    "The project does contain `Main.tscn`, a scene resource with no attached script."
)
CODE_INVENTORY_REPLACEMENT_TEXT = (
    "`Player.gd` extends `CharacterBody2D` and exports `speed` with a default value of `300.0`. "
    "It defines `_physics_process`. The Inspector detects calls to `Input.get_vector()` and "
    "`move_and_slide()` in the script."
)
DATA_ASSETS_REPLACEMENT_TARGET = "Knowledge/Data/Project Assets and Resources.md"
DATA_ASSETS_REPLACEMENT_COMMIT = "5472bacbe12818379b9a20771885b63ea4725ea0"
DATA_ASSETS_MAIN_ANCHOR = ("# Project Assets and Resources", "## Scene resource", "### `Main.tscn`")
DATA_ASSETS_MAIN_OLD_PASSAGE = (
    "The project contains a single scene resource. It defines a root node named `Main` of type `Node2D`, "
    "with no child nodes or attached script. `project.godot` references it as the main scene."
)
DATA_ASSETS_MAIN_REPLACEMENT = (
    "`project.godot` selects `res://Main.tscn` as the main scene. `res://Main.tscn` has a `Main` `Node2D` "
    "root and instances `res://Player.tscn` as node `Player`. `res://Player.tscn` has a `Player` "
    "`CharacterBody2D` root."
)
DATA_ASSETS_OTHER_ANCHOR = ("# Project Assets and Resources", "## Other resources and data")
DATA_ASSETS_OTHER_OLD_PASSAGE = (
    "No scripts, custom resource files, data files, or other runtime assets are present in the inspected project tree."
)
DATA_ASSETS_OTHER_REPLACEMENT = (
    "`Player.tscn` attaches `Player.gd` to its `Player` root. Its `Sprite2D` node assigns `res://icon.svg` to `texture`."
)
DEVELOPMENT_INPUT_ROWS = (
    ("move_down", "S"),
    ("move_left", "A"),
    ("move_right", "D"),
    ("move_up", "W"),
)
SYSTEMS_DRAFT_CLAIMS = (
    {
        "key": "scene_structure",
        "text": "`res://Player.tscn` has a `Player` root of type `CharacterBody2D` and attaches `res://Player.gd` to that node.",
        "fact_keys": ("player_scene", "player_scene_root", "player_script_attachment"),
    },
    {
        "key": "script_structure",
        "text": "`res://Player.gd` exists, extends `CharacterBody2D`, exports `speed` with a default of `300.0`, defines `_physics_process`, and calls `move_and_slide()`.",
        "fact_keys": ("player_script", "player_inheritance", "player_speed", "player_physics_function", "player_slide_call"),
    },
)


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


def proposal_fingerprint() -> str:
    """Fingerprint both deterministic implementations used to build a proposal."""
    updater_path = Path(__file__).resolve()
    inspector_path = updater_path.with_name("change_inspector.py")
    digest = hashlib.sha256()
    for label, path in ((b"change_inspector.py", inspector_path), (b"knowledge_updater.py", updater_path)):
        try:
            source = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        except OSError as exc:
            raise UpdaterError(f"Cannot fingerprint proposal implementation {path}: {exc}") from exc
        digest.update(label + b"\0" + source + b"\0")
    return digest.hexdigest()


def proposal_path(root: Path, sha: str, *, versioned: bool = False,
                  replacement_target: str | None = None,
                  replacement_operation_id: str | None = None) -> Path:
    if replacement_target is not None:
        if not versioned:
            raise UpdaterError("Target-specific REPLACE proposals must be versioned.")
        allowed_slugs = {
            SYSTEMS_REPLACEMENT_TARGET: "systems-inventory",
            CODE_INVENTORY_REPLACEMENT_TARGET: "code-inventory",
            DATA_ASSETS_REPLACEMENT_TARGET: "project-assets-and-resources",
        }
        slug = allowed_slugs.get(replacement_target)
        if slug is None:
            raise UpdaterError(f"No target-specific proposal builder is available for {replacement_target}.")
        if replacement_target == DATA_ASSETS_REPLACEMENT_TARGET:
            if not isinstance(replacement_operation_id, str) or not re.fullmatch(r"[0-9a-f]{16}", replacement_operation_id):
                raise UpdaterError("Project Assets versioned proposals require the deterministic operation ID.")
            slug = f"{slug}-{replacement_operation_id}"
        suffix = f"-knowledge-update-{slug}-{proposal_fingerprint()}.md"
    else:
        suffix = f"-knowledge-update-{proposal_fingerprint()}.md" if versioned else "-knowledge-update.md"
    return root / ".ai/proposals" / f"{sha}{suffix}"


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


def safe_read_bytes(root: Path, relative: str) -> bytes:
    candidate = (root / Path(relative)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise UpdaterError(f"Path escapes repository: {relative}") from exc
    try:
        return candidate.read_bytes()
    except OSError as exc:
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


def load_inspector(root: Path):
    sys.dont_write_bytecode = True
    path = root / ".ai/change_inspector.py"
    spec = importlib.util.spec_from_file_location("project_change_inspector", path)
    if spec is None or spec.loader is None:
        raise UpdaterError("Cannot load the Change Inspector implementation.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_fact(facts: list[dict], category: str, statement: str, source: str,
             needles: list[list[str]], anchors: list[str]) -> None:
    facts.append({"category": category, "statement": statement, "source": source,
                  "needles": needles, "anchors": anchors})


def project_facts(root: Path, sha: str, inspector) -> tuple[list[dict], str, str, list[str]]:
    """Extract commit-tree facts using the Inspector's deterministic parsers."""
    tree = str(git(root, "ls-tree", "-r", "--name-only", sha)).splitlines()
    project_configs = [p for p in tree if p.endswith("/project.godot") or p == "project.godot"]
    if len(project_configs) != 1:
        raise UpdaterError("Expected one project.godot in the commit tree; structural evidence is ambiguous.")
    config_path = project_configs[0]
    project_dir = config_path.rpartition("/")[0]
    prefix = project_dir + "/" if project_dir else ""
    project_paths = [p for p in tree if p.startswith(prefix)]
    facts: list[dict] = []
    source_paths: list[str] = []

    parent = str(git(root, "show", "-s", "--format=%P", sha)).strip().split()
    if parent:
        for status, old_path, new_path in inspector.changed_files(parent[0], sha, root):
            path = new_path or old_path
            if path and (path.startswith(prefix) or path == config_path):
                source_paths.append(path)
                add_fact(facts, "change", f"Commit `{sha}` records `{status.lower()}` of `{path}`.", path,
                         [[sha], [path]], [sha, path, Path(path).name])

    for path in project_paths:
        if path == config_path:
            continue
        raw = git_blob(root, sha, path)
        if raw is None:
            continue
        relative_resource = "res://" + path[len(prefix):] if prefix and path.startswith(prefix) else "res://" + path
        basename = Path(path).name
        if path.lower().endswith(".gd"):
            add_fact(facts, "script_file", f"Script `{relative_resource}` exists.", path,
                     [[basename]], [basename, relative_resource])
            source_text = raw
            parsed = inspector.inspect_gd(source_text)
            for base in parsed["extends"]:
                add_fact(facts, "script_inheritance", f"`{relative_resource}` extends `{base}`.", path,
                         [[basename], [base]], [basename, base])
            for declaration in parsed["exports"]:
                var_match = re.search(r"@export(?:_\w+)?\s+var\s+([A-Za-z_]\w*)", declaration)
                name = var_match.group(1) if var_match else declaration
                add_fact(facts, "script_export", f"`{relative_resource}` exports `{name}` using `{declaration}`.", path,
                         [[basename], [name], [declaration]], [basename, name, declaration])
            for signature in parsed["functions"]:
                name_match = re.match(r"func\s+([A-Za-z_]\w*)", signature)
                name = name_match.group(1) if name_match else signature
                add_fact(facts, "script_function", f"`{relative_resource}` defines `{signature}`.", path,
                         [[basename], [name]], [basename, name, signature])
            for call in parsed.get("calls", []):
                if call in {"func"}:
                    continue
                add_fact(facts, "script_call", f"`{relative_resource}` calls `{call}()`.", path,
                         [[basename], [call]], [basename, call])
            for dependency in parsed["resources"]:
                add_fact(facts, "script_resource", f"`{relative_resource}` references `{dependency}`.", path,
                         [[basename], [dependency]], [basename, dependency])
        elif path.lower().endswith(".tscn"):
            add_fact(facts, "scene_file", f"Scene `{relative_resource}` exists.", path,
                     [[basename]], [basename, relative_resource])
            scene = inspector.inspect_scene(raw)
            roots = [node for node in scene["nodes"] if "parent" not in node]
            if roots:
                node = roots[0]
                name, node_type = node.get("name", "?"), node.get("type", "?")
                add_fact(facts, "scene_root", f"`{relative_resource}` has root `{name}` of type `{node_type}`.", path,
                         [[basename], [name], [node_type]], [basename, name, node_type])
            for node in scene["nodes"]:
                if node in roots:
                    continue
                name = node.get("name", "?")
                node_type = inspector.scene_node_type(node) or "type not declared"
                parent_path = node.get("parent", "?")
                relationship = "child" if parent_path == "." else "node"
                add_fact(facts, "scene_node", f"`{relative_resource}` contains {relationship} `{name}` ({node_type}) under `{parent_path}`.", path,
                         [[basename], [name], [node_type]], [basename, name, node_type])
            for node_name, script_path in scene["scripts"]:
                add_fact(facts, "scene_script", f"Node `{node_name}` in `{relative_resource}` attaches `{script_path}`.", path,
                         [[basename], [node_name], [script_path]], [basename, node_name, script_path])
            for node_name, prop, resource_path in scene.get("node_resources", []):
                add_fact(facts, "scene_resource", f"Node `{node_name}` in `{relative_resource}` assigns `{resource_path}` to `{prop}`.", path,
                         [[basename], [node_name], [prop], [resource_path]], [basename, node_name, prop, resource_path])
            for node_name, instance_path in scene["instances"]:
                add_fact(facts, "scene_instance", f"`{relative_resource}` instances `{instance_path}` as node `{node_name}`.", path,
                         [[basename], [node_name], [instance_path]], [basename, node_name, instance_path])
            for resource in scene["external"]:
                resource_path = resource.get("path")
                if resource_path:
                    add_fact(facts, "scene_resource", f"`{relative_resource}` declares external resource `{resource_path}` ({resource.get('type', 'unknown')}).", path,
                             [[basename], [resource_path]], [basename, resource_path, resource.get("type", "")])

    settings_raw = git_blob(root, sha, config_path)
    if settings_raw is None:
        raise UpdaterError("Could not read project.godot from the commit tree.")
    settings = inspector.inspect_project(settings_raw)
    for (section, key), value in sorted(settings.items()):
        if section == "input":
            description = inspector.input_action_description(value)
            action = key
            add_fact(facts, "input_action", f"Input action `{action}` is configured with `{description}`.", config_path,
                     [[action], [description]], ["[input]", action, description])
        elif section in {"application", "autoload", "display", "physics", "rendering"}:
            display_key = f"{section}/{key}"
            add_fact(facts, "project_setting", f"`project.godot` sets `{display_key}` to `{value}`.", config_path,
                     [[display_key], [value.strip('"')]], [display_key, key, value])
    return facts, config_path, project_dir, sorted(set(source_paths))


def target_categories(relative: str) -> set[str]:
    name = Path(relative).name.casefold()
    if name == "development configuration.md":
        return {"project_setting", "input_action"}
    if name == "project overview.md":
        return {"script_file", "scene_file", "scene_root", "scene_instance"}
    if name == "architecture overview.md":
        return {"script_file", "scene_file", "scene_root", "scene_node", "scene_script", "scene_resource", "scene_instance", "input_action"}
    if name == "change records.md":
        return {"change"}
    if name == "code inventory.md":
        return {"script_file", "script_inheritance", "script_export", "script_function", "script_call", "script_resource"}
    if name == "project assets and resources.md":
        return {"script_file", "scene_file", "scene_resource", "scene_script", "scene_instance"}
    if name == "unestablished decisions.md":
        return {"script_inheritance", "script_export", "scene_root", "input_action"}
    if name == "scene inventory.md":
        return {"scene_file", "scene_root", "scene_node", "scene_script", "scene_resource", "scene_instance"}
    if name == "systems inventory.md":
        return {"script_inheritance", "script_function", "script_call", "input_action", "scene_root"}
    return set()


def fact_is_documented(content: str, fact: dict) -> bool:
    normalized = content.casefold()
    return all(any(needle.casefold() in normalized for needle in group if needle)
               for group in fact["needles"])


def recommendation_facts(recommendation: dict, facts: list[dict]) -> tuple[list[dict], bool, list[str]]:
    categories = target_categories(recommendation["path"])
    description = " ".join((recommendation.get("what", ""), recommendation.get("why", ""), recommendation.get("evidence", ""))).casefold()
    all_anchors = [anchor.casefold() for fact in facts for anchor in fact["anchors"] if anchor]
    unknown_literals = []
    for literal in re.findall(r"`([^`]+)`", description):
        token = literal.casefold()
        known = any(token in anchor or anchor in token for anchor in all_anchors)
        if not known and token.endswith("*"):
            known = any(anchor.startswith(token[:-1]) for anchor in all_anchors)
        if not known and literal not in unknown_literals:
            unknown_literals.append(literal)
    selected = []
    for fact in facts:
        if fact["category"] not in categories:
            continue
        if any(anchor and anchor.casefold() in description for anchor in fact["anchors"]):
            selected.append(fact)
    return selected, not selected, unknown_literals


def parse_recommendations(review_text: str) -> list[dict]:
    section_match = re.search(r"(?ms)^## Recommended Knowledge Updates\s*(.*?)(?=^## |\Z)", review_text)
    if not section_match:
        return []
    section = section_match.group(1)
    records = []
    pattern = r"(?ms)^\*\s+\*\*Target Knowledge File:\*\*\s+`([^`]+)`\s*\n(.*?)(?=^\*\s+\*\*Target Knowledge File:|\Z)"
    for match in re.finditer(pattern, section):
        target, body = match.groups()
        if not target.startswith("Knowledge/") or target == "Knowledge/N/A":
            continue
        values = {}
        for key in ("What Should Change", "Why", "Evidence"):
            field = re.search(r"\*\*{}:\*\*\s*(.*)".format(re.escape(key)), body)
            values[key.casefold()] = field.group(1).strip() if field else ""
        records.append({"path": target, "what": values["what should change"],
                        "why": values["why"], "evidence": values["evidence"]})
    return records


def affected_knowledge_paths(review_text: str) -> list[str]:
    section = re.search(r"(?ms)^## Affected Knowledge\s*(.*?)(?=^## |\Z)", review_text)
    return sorted(set(TARGET_RE.findall(section.group(1) if section else "")))


def evaluate_recommendations(root: Path, sha: str, review_text: str, inspector_report: str,
                             inspector) -> tuple[str, list[dict], list[str], list[dict]]:
    facts, config_path, _project_dir, changed = project_facts(root, sha, inspector)
    recommendations = parse_recommendations(review_text)
    explicitly_no_update = bool(NO_UPDATE_RE.search(review_text))
    if not recommendations and explicitly_no_update:
        recommendations = [{"path": path, "what": "Review all project facts relevant to this Knowledge file.",
                            "why": "NotebookLM concluded that the existing note already reflects the inspected changes.",
                            "evidence": "Facts are independently checked against commit and Inspector output."}
                           for path in affected_knowledge_paths(review_text)]
    results = []
    unresolved = []
    for recommendation in recommendations:
        relative = recommendation["path"]
        if not relative.startswith("Knowledge/") or ".." in Path(relative).parts:
            unresolved.append({"path": relative, "reason": "target path is unsafe or outside Knowledge/"})
            continue
        try:
            knowledge_bytes = safe_read_bytes(root, relative)
            content = knowledge_bytes.decode("utf-8")
        except UnicodeError as exc:
            unresolved.append({"path": relative, "reason": f"target Knowledge file is not valid UTF-8: {exc}"})
            results.append({"recommendation": recommendation, "facts": [], "missing": [], "documented": []})
            continue
        except UpdaterError as exc:
            unresolved.append({"path": relative, "reason": str(exc)})
            results.append({"recommendation": recommendation, "facts": [], "missing": [], "documented": []})
            continue
        selected, no_match, unknown_literals = recommendation_facts(recommendation, facts)
        if no_match:
            unresolved.append({"path": relative, "reason": "the recommendation could not be matched to a verified structural fact in the commit and Inspector output"})
        if unknown_literals:
            unresolved.append({"path": relative, "reason": "the recommendation contains technical claims not found in project/Inspector evidence: {}".format(", ".join(f"`{literal}`" for literal in unknown_literals))})
        documented, missing = [], []
        for fact in selected:
            (documented if fact_is_documented(content, fact) else missing).append(fact)
        results.append({"recommendation": recommendation, "facts": selected,
                        "missing": missing, "documented": documented,
                        "verified_facts": facts, "knowledge_content": content,
                        "knowledge_sha256": sha256(knowledge_bytes)})

    missing_count = sum(len(item["missing"]) for item in results)
    if unresolved:
        status = "REQUIRES_REVIEW"
    elif missing_count:
        status = "UPDATE_PROPOSED"
    elif recommendations:
        status = "NO_UPDATE_REQUIRED"
    else:
        status = "REQUIRES_REVIEW"
        unresolved.append({"path": "(review)", "reason": "no actionable Knowledge recommendation or explicit no-update finding was parsed"})
    fingerprint = inspector.embedded_fingerprint(inspector_report)
    notes = [f"- Current Inspector fingerprint used for independent fact extraction: `sha256:{fingerprint or 'unavailable'}`.",
             f"- Git project configuration checked: `{config_path}`.",
             f"- Project paths changed by this commit: {', '.join(f'`{p}`' for p in changed) if changed else 'none' }."]
    return status, results, notes, unresolved


def application_fact_selected(target: str, fact: dict) -> bool:
    """Return whether a verified fact belongs in this target's concise addition."""
    name = Path(target).name.casefold()
    category = fact.get("category")
    statement = fact.get("statement", "")
    if name == "development configuration.md":
        return category == "input_action" and re.search(r"Input action `move_(up|down|left|right)`", statement) is not None
    if name == "project overview.md":
        return (
            category == "scene_instance" and "res://Main.tscn" in statement and "res://Player.tscn" in statement
            or category == "script_file" and "res://Player.gd" in statement
            or category == "scene_file" and "res://Player.tscn" in statement
            or category == "scene_root" and "res://Player.tscn" in statement
        )
    if name == "architecture overview.md":
        return (
            category == "scene_instance" and "res://Main.tscn" in statement and "res://Player.tscn" in statement
            or category == "scene_script" and "res://Player.tscn" in statement and "res://Player.gd" in statement
            or category == "input_action" and "Input action `move_" in statement
            or category == "script_call" and "res://Player.gd" in statement and "Input.get_vector" in statement
        )
    if name == "change records.md":
        return category == "change"
    if name == "code inventory.md":
        return (
            category == "script_file" and "res://Player.gd" in statement
            or category == "script_inheritance" and "res://Player.gd" in statement
            or category == "script_export" and "res://Player.gd" in statement and "`speed`" in statement
            or category == "script_function" and "res://Player.gd" in statement and "_physics_process" in statement
            or category == "script_call" and "res://Player.gd" in statement and ("Input.get_vector" in statement or "move_and_slide" in statement)
        )
    if name == "project assets and resources.md":
        return (
            category == "scene_file" and "res://Player.tscn" in statement
            or category == "scene_script" and "res://Player.tscn" in statement and "res://Player.gd" in statement
            or category == "scene_resource" and "res://Player.tscn" in statement and "res://icon.svg" in statement and "texture" in statement
        )
    if name == "scene inventory.md":
        return (
            category == "scene_instance" and "res://Main.tscn" in statement and "res://Player.tscn" in statement
            or category == "scene_root" and "res://Player.tscn" in statement
            or category == "scene_node" and "res://Player.tscn" in statement and "`Sprite2D`" in statement
            or category == "scene_script" and "res://Player.tscn" in statement and "res://Player.gd" in statement
        )
    if name == "systems inventory.md":
        return (
            category == "script_inheritance" and "res://Player.gd" in statement
            or category == "script_function" and "res://Player.gd" in statement and "_physics_process" in statement
            or category == "script_call" and "res://Player.gd" in statement and ("Input.get_vector" in statement or "move_and_slide" in statement)
            or category == "input_action" and "Input action `move_" in statement
        )
    return False


def build_application_plan(root: Path, sha: str, results: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Build Applier-compatible plan entries from exact verified/missing facts."""
    plan = []
    supplemental: dict[str, list[dict]] = {}
    for item in results:
        recommendation = item.get("recommendation", {})
        target = recommendation.get("path", "")
        if target == DEVELOPMENT_CONFIGURATION_TARGET:
            # This table is a target-specific ADD operation. Its structured table
            # remains meaningful even when the same facts already appear as prose
            # or bullets, so generic missing-fact filtering must not drop it.
            verified = item.get("verified_facts", [])
            draft = render_development_input_table(target, verified)
            content = item.get("knowledge_content", "")
            anchor, context = APPLICATION_TARGETS[Path(target).name.casefold()]
            if content.splitlines().count(anchor) != 1:
                raise UpdaterError(f"Application Plan needs one exact existing heading `{anchor}` in {target}.")
            baseline = item.get("knowledge_sha256")
            if not isinstance(baseline, str) or not re.fullmatch(r"[0-9a-f]{64}", baseline):
                raise UpdaterError(f"Application Plan has no exact Knowledge baseline hash for {target}.")
            plan.append({
                "path": target, "baseline_sha256": baseline, "anchor": anchor,
                "context": context,
                "facts": [fact["statement"] for fact in draft["supporting_facts"]],
                "operation": "ADD", "renderer": "development_input_table",
                "rendered_markdown": draft["table"],
                "rendered_markdown_sha256": sha256(draft["table"].encode("utf-8")),
                "selected_verified_facts": draft["supporting_facts"],
                "claim_to_fact_mapping": draft["claim_to_fact_mapping"],
            })
            continue
        policy = APPLICATION_TARGETS.get(Path(target).name.casefold())
        if not policy:
            continue
        verified = item.get("verified_facts", [])
        # Membership by exact statement and source prevents recommendation data
        # or caller-supplied text from being promoted into the plan.
        verified_keys = {(fact.get("statement"), fact.get("source")) for fact in verified}
        content = item.get("knowledge_content", "")
        eligible = []
        for fact in verified:
            key = (fact.get("statement"), fact.get("source"))
            if (application_fact_selected(target, fact) and key in verified_keys
                    and not fact_is_documented(content, fact)):
                eligible.append(fact)
        eligible.sort(key=lambda fact: (fact["statement"], fact["source"], fact["category"]))
        if not eligible:
            continue
        anchor, context = policy
        if content.splitlines().count(anchor) != 1:
            raise UpdaterError(f"Application Plan needs one exact existing heading `{anchor}` in {target}.")
        baseline = item.get("knowledge_sha256")
        if not isinstance(baseline, str) or not re.fullmatch(r"[0-9a-f]{64}", baseline):
            raise UpdaterError(f"Application Plan has no exact Knowledge baseline hash for {target}.")
        recommended_missing = {
            (fact.get("statement"), fact.get("source")) for fact in item.get("missing", [])
        }
        extra = [fact for fact in eligible if (fact.get("statement"), fact.get("source")) not in recommended_missing]
        if extra:
            supplemental[target] = extra
        plan.append({"path": target, "baseline_sha256": baseline,
                     "anchor": anchor, "context": context,
                     "facts": [fact["statement"] for fact in eligible]})
    return plan, supplemental


def render_application_plan(plan: list[dict], replacements: list[dict] | None = None) -> list[str]:
    replacements = replacements or []
    if replacements:
        lines = ["## Application Plan", "",
                 "This deterministic plan separates existing ADD operations from proposal-only REPLACE operations. Both use project facts independently verified by the Updater; NotebookLM prose is not plan data.", ""]
    else:
        # Preserve the existing ADD-only output and semantics byte-for-byte.
        lines = ["## Application Plan", "",
                 "This deterministic append-only plan contains only project facts independently verified by the Updater. NotebookLM prose is not used as plan data.", ""]
    for entry in plan:
        lines.extend([f"### `{entry['path']}`", "",
                      f"- Base SHA-256: `{entry['baseline_sha256']}`",
                      f"- Insert after: `{entry['anchor']}`",
                      f"- Context: {entry['context']}"])
        lines.extend(f"- Verified fact: {fact}" for fact in entry["facts"])
        if entry.get("renderer") == "development_input_table":
            metadata = {key: entry[key] for key in (
                "operation", "renderer", "rendered_markdown", "rendered_markdown_sha256",
                "selected_verified_facts", "claim_to_fact_mapping",
            )}
            lines.extend([
                "- Operation: ADD", "- Renderer: `development_input_table`", "- Renderer data:",
                "```json", json.dumps(metadata, ensure_ascii=False, sort_keys=True), "```",
            ])
        lines.append("")
    if not plan and not replacements:
        lines.extend(["No facts were selected for safe append-only application.", ""])
    for operation in replacements:
        serialized = json.dumps(operation, ensure_ascii=False, indent=2, sort_keys=True)
        lines.extend([f"### REPLACE operation `{operation['operation_id']}`", "",
                      "```json", serialized, "```", ""])
    return lines


def parse_replacement_operations(text: str) -> list[dict]:
    """Parse proposal-only REPLACE JSON blocks; ADD parsing remains in the Applier."""
    section = re.search(r"(?ms)^## Application Plan\s*\n(.*?)(?=^## |\Z)", text)
    if not section:
        return []
    body = section.group(1)
    pattern = re.compile(
        r"(?ms)^### REPLACE operation `([0-9a-f]{16})`\s*\n\s*```json\s*\n(.*?)\n```\s*$"
    )
    operations = []
    for match in pattern.finditer(body):
        try:
            operation = json.loads(match.group(2))
        except json.JSONDecodeError as exc:
            raise UpdaterError(f"Invalid REPLACE operation JSON: {exc}") from exc
        if not isinstance(operation, dict) or operation.get("operation_id") != match.group(1):
            raise UpdaterError("REPLACE operation heading does not match its serialized operation ID.")
        operations.append(operation)
    return operations


def documentation_fact_id(fact: dict) -> str:
    """Stable identifier for a fact's category, source, and exact statement."""
    material = "\0".join((fact.get("category", ""), fact.get("source", ""), fact.get("statement", "")))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def select_systems_documentation_facts(verified_facts: list[dict]) -> dict[str, dict]:
    """Select the fixed small fact set required by the first Systems renderer."""
    specs = (
        ("player_scene", "scene_file", "(Game Name)/Player.tscn", lambda f: f.get("statement") == "Scene `res://Player.tscn` exists."),
        ("player_scene_root", "scene_root", "(Game Name)/Player.tscn", lambda f: f.get("statement") == "`res://Player.tscn` has root `Player` of type `CharacterBody2D`."),
        ("player_script_attachment", "scene_script", "(Game Name)/Player.tscn", lambda f: f.get("statement") == "Node `Player` in `res://Player.tscn` attaches `res://Player.gd`."),
        ("player_script", "script_file", "(Game Name)/Player.gd", lambda f: f.get("statement") == "Script `res://Player.gd` exists."),
        ("player_inheritance", "script_inheritance", "(Game Name)/Player.gd", lambda f: f.get("statement") == "`res://Player.gd` extends `CharacterBody2D`."),
        ("player_speed", "script_export", "(Game Name)/Player.gd", lambda f: f.get("statement") == "`res://Player.gd` exports `speed` using `@export var speed: float = 300.0`."),
        ("player_physics_function", "script_function", "(Game Name)/Player.gd", lambda f: f.get("statement") == "`res://Player.gd` defines `func _physics_process(_delta: float) -> void:`."),
        ("player_slide_call", "script_call", "(Game Name)/Player.gd", lambda f: f.get("statement") == "`res://Player.gd` calls `move_and_slide()`."),
    )
    selected = {}
    for key, category, source, predicate in specs:
        matches = [fact for fact in verified_facts
                   if fact.get("category") == category and fact.get("source") == source and predicate(fact)]
        if len(matches) != 1:
            raise UpdaterError(
                f"Cannot generate the Systems documentation draft: expected exactly one verified `{key}` fact, found {len(matches)}."
            )
        selected[key] = matches[0]
    return selected


def validate_systems_documentation_draft(draft: dict, verified_facts: list[dict]) -> None:
    """Reject unselected facts or any wording outside the fixed verified templates."""
    if draft.get("target") != SYSTEMS_DOCUMENTATION_TARGET:
        raise UpdaterError("The deterministic documentation renderer only supports the Systems Inventory target.")
    selected = select_systems_documentation_facts(verified_facts)
    selected_ids = {key: documentation_fact_id(fact) for key, fact in selected.items()}
    expected_facts = [
        {"id": selected_ids[key], "category": selected[key]["category"],
         "source": selected[key]["source"], "statement": selected[key]["statement"]}
        for key in selected
    ]
    if draft.get("supporting_facts") != expected_facts:
        raise UpdaterError("Systems draft supporting facts do not exactly match the selected verified facts.")
    claims = draft.get("claims")
    if not isinstance(claims, list) or len(claims) != len(SYSTEMS_DRAFT_CLAIMS):
        raise UpdaterError("Systems draft must contain exactly the approved factual claims.")
    for actual, template in zip(claims, SYSTEMS_DRAFT_CLAIMS):
        expected_ids = [selected_ids[key] for key in template["fact_keys"]]
        if actual != {"key": template["key"], "text": template["text"], "verified_fact_ids": expected_ids}:
            raise UpdaterError(f"Systems draft claim `{template['key']}` is not supported by its selected verified facts.")
    expected_paragraph = " ".join(template["text"] for template in SYSTEMS_DRAFT_CLAIMS)
    if draft.get("paragraph") != expected_paragraph:
        raise UpdaterError("Systems documentation draft contains wording outside the deterministic verified template.")


def render_systems_documentation_draft(target: str, verified_facts: list[dict]) -> dict:
    """Render a proposal-only paragraph for Systems Inventory from eight verified facts."""
    if target != SYSTEMS_DOCUMENTATION_TARGET:
        raise UpdaterError("The deterministic documentation renderer only supports Knowledge/Systems/Systems Inventory.md.")
    selected = select_systems_documentation_facts(verified_facts)
    ids = {key: documentation_fact_id(fact) for key, fact in selected.items()}
    claims = [
        {"key": template["key"], "text": template["text"],
         "verified_fact_ids": [ids[key] for key in template["fact_keys"]]}
        for template in SYSTEMS_DRAFT_CLAIMS
    ]
    draft = {
        "target": target,
        "paragraph": " ".join(claim["text"] for claim in claims),
        "claims": claims,
        "supporting_facts": [
            {"id": ids[key], "category": fact["category"],
             "source": fact["source"], "statement": fact["statement"]}
            for key, fact in selected.items()
        ],
    }
    validate_systems_documentation_draft(draft, verified_facts)
    return draft


def select_development_input_facts(verified_facts: list[dict]) -> dict[str, dict]:
    """Select one unambiguous physical-key fact for each configured action."""
    selected = {}
    pattern = re.compile(
        r"^Input action `(?P<action>[^`]+)` is configured with `(?P<binding>[^`]+)`\.$"
    )
    binding_pattern = re.compile(r"^physical key (?P<key>[A-Za-z]) \((?P<code>[0-9]+)\)$")
    for action, expected_key in DEVELOPMENT_INPUT_ROWS:
        matches = []
        for fact in verified_facts:
            if fact.get("category") != "input_action" or fact.get("source") != "(Game Name)/project.godot":
                continue
            match = pattern.fullmatch(fact.get("statement", ""))
            if not match or match.group("action") != action:
                continue
            binding = binding_pattern.fullmatch(match.group("binding"))
            if not binding:
                raise UpdaterError(
                    f"Cannot generate the Development Configuration table: `{action}` has an absent, ambiguous, or non-physical-key binding."
                )
            key = binding.group("key").upper()
            if int(binding.group("code")) != ord(key):
                raise UpdaterError(
                    f"Cannot generate the Development Configuration table: `{action}` has an inconsistent physical key value."
                )
            matches.append((fact, key))
        if len(matches) != 1:
            raise UpdaterError(
                f"Cannot generate the Development Configuration table: expected exactly one verified `{action}` binding, found {len(matches)}."
            )
        fact, key = matches[0]
        if key != expected_key:
            raise UpdaterError(
                f"Cannot generate the Development Configuration table: `{action}` is bound to `{key}`, expected `{expected_key}`."
            )
        selected[action] = fact
    return selected


def validate_development_input_table(draft: dict, verified_facts: list[dict]) -> None:
    """Require every exact table row to map to its single selected verified fact."""
    if draft.get("target") != DEVELOPMENT_CONFIGURATION_TARGET:
        raise UpdaterError("The input-action renderer only supports Knowledge/00 Project/Development Configuration.md.")
    selected = select_development_input_facts(verified_facts)
    expected_rows = []
    expected_support = []
    expected_mapping = []
    for action, key in DEVELOPMENT_INPUT_ROWS:
        fact = selected[action]
        fact_id = documentation_fact_id(fact)
        expected_rows.append({"action": action, "key": key, "verified_fact_ids": [fact_id]})
        row_text = f"| {action} | physical key {key} ({ord(key)}) |"
        expected_mapping.append({"action": action, "text": row_text, "verified_fact_ids": [fact_id]})
        expected_support.append({
            "id": fact_id, "category": fact["category"],
            "source": fact["source"], "statement": fact["statement"],
        })
    expected_table = "\n".join([
        "| Action | Configured physical key |",
        "|---|---|",
        *(f"| {row['action']} | physical key {row['key']} ({ord(row['key'])}) |" for row in expected_rows),
    ])
    if draft.get("rows") != expected_rows:
        raise UpdaterError("Development Configuration table contains a row that is unsupported or mapped to the wrong fact.")
    if draft.get("supporting_facts") != expected_support:
        raise UpdaterError("Development Configuration table supporting facts do not match the selected verified facts.")
    if draft.get("claim_to_fact_mapping") != expected_mapping:
        raise UpdaterError("Development Configuration table rows do not map to their selected verified facts.")
    if draft.get("table") != expected_table:
        raise UpdaterError("Development Configuration table text contains an unsupported row or value.")


def render_development_input_table(target: str, verified_facts: list[dict]) -> dict:
    """Render the fixed action/key table from exact Inspector-verified facts."""
    if target != DEVELOPMENT_CONFIGURATION_TARGET:
        raise UpdaterError("The input-action renderer only supports Knowledge/00 Project/Development Configuration.md.")
    selected = select_development_input_facts(verified_facts)
    rows = []
    supporting_facts = []
    claim_to_fact_mapping = []
    for action, key in DEVELOPMENT_INPUT_ROWS:
        fact = selected[action]
        fact_id = documentation_fact_id(fact)
        rows.append({"action": action, "key": key, "verified_fact_ids": [fact_id]})
        claim_to_fact_mapping.append({
            "action": action,
            "text": f"| {action} | physical key {key} ({ord(key)}) |",
            "verified_fact_ids": [fact_id],
        })
        supporting_facts.append({
            "id": fact_id, "category": fact["category"],
            "source": fact["source"], "statement": fact["statement"],
        })
    draft = {
        "target": target,
        "rows": rows,
        "claim_to_fact_mapping": claim_to_fact_mapping,
        "table": "\n".join([
            "| Action | Configured physical key |",
            "|---|---|",
            *(f"| {row['action']} | physical key {row['key']} ({ord(row['key'])}) |" for row in rows),
        ]),
        "supporting_facts": supporting_facts,
    }
    validate_development_input_table(draft, verified_facts)
    return draft


def _anchored_passage_count(content: str, anchor_path: tuple[str, ...], passage: str) -> int:
    """Count exact passage occurrences inside a unique nested Markdown heading scope."""
    lines = content.splitlines(keepends=True)
    offsets = []
    cursor = 0
    for line in lines:
        offsets.append((cursor, cursor + len(line), line.rstrip("\r\n")))
        cursor += len(line)
    if cursor < len(content):
        offsets.append((cursor, len(content), content[cursor:]))

    scope_start = 0
    scope_end = len(content)
    expected_parent_level = 0
    for heading in anchor_path:
        level_match = re.match(r"^(#{1,6})\s+", heading)
        if not level_match:
            raise UpdaterError(f"Invalid replacement anchor heading: {heading}")
        level = len(level_match.group(1))
        if level <= expected_parent_level:
            raise UpdaterError("Replacement anchor headings must descend through the Markdown hierarchy.")
        matches = [(start, end) for start, end, text in offsets
                   if text == heading and scope_start <= start < scope_end]
        if len(matches) != 1:
            raise UpdaterError(f"Replacement anchor is missing or non-unique: {heading}")
        start, end = matches[0]
        scope_start = end
        scope_end = len(content)
        for next_start, _next_end, text in offsets:
            next_heading = re.match(r"^(#{1,6})\s+", text)
            if next_start >= end and next_heading and len(next_heading.group(1)) <= level:
                scope_end = next_start
                break
        expected_parent_level = level

    section = content[scope_start:scope_end]
    return section.count(passage)


def build_systems_replacement_operation(root: Path, commit_sha: str, verified_facts: list[dict]) -> dict:
    """Build the single fixed 5472 REPLACE proposal from the existing Systems renderer."""
    if commit_sha != SYSTEMS_REPLACEMENT_COMMIT:
        raise UpdaterError("This proposal-only REPLACE template is restricted to commit 5472bacbe12818379b9a20771885b63ea4725ea0.")
    target_path = root / SYSTEMS_REPLACEMENT_TARGET
    try:
        original = target_path.read_bytes()
        content = original.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise UpdaterError(f"Cannot read replacement target {SYSTEMS_REPLACEMENT_TARGET}: {exc}") from exc
    if content.count(SYSTEMS_REPLACEMENT_OLD_PASSAGE) != 1 or _anchored_passage_count(
            content, SYSTEMS_REPLACEMENT_ANCHOR, SYSTEMS_REPLACEMENT_OLD_PASSAGE) != 1:
        raise UpdaterError("The exact Systems Inventory stale passage is absent or non-unique at its recorded anchor.")

    draft = render_systems_documentation_draft(SYSTEMS_DOCUMENTATION_TARGET, verified_facts)
    old_hash = sha256(SYSTEMS_REPLACEMENT_OLD_PASSAGE.encode("utf-8"))
    replacement_hash = sha256(draft["paragraph"].encode("utf-8"))
    operation_id = sha256("\0".join((commit_sha, SYSTEMS_REPLACEMENT_TARGET, old_hash, replacement_hash)).encode("utf-8"))[:16]
    operation = {
        "operation": "REPLACE",
        "operation_id": operation_id,
        "commit_sha": commit_sha,
        "path": SYSTEMS_REPLACEMENT_TARGET,
        "file_baseline_sha256": sha256(original),
        "exact_old_passage": SYSTEMS_REPLACEMENT_OLD_PASSAGE,
        "old_passage_sha256": old_hash,
        "anchor": {"heading_path": list(SYSTEMS_REPLACEMENT_ANCHOR), "occurrence": 1},
        "replacement_markdown": draft["paragraph"],
        "replacement_sha256": replacement_hash,
        "selected_verified_facts": draft["supporting_facts"],
        "claim_to_fact_mapping": draft["claims"],
    }
    validate_systems_replacement_operation(operation, original, verified_facts, commit_sha)
    return operation


def validate_systems_replacement_operation(operation: dict, file_bytes: bytes,
                                            verified_facts: list[dict], commit_sha: str) -> None:
    """Validate the exact 5472 replacement metadata without applying it."""
    if operation.get("operation") != "REPLACE":
        raise UpdaterError("An ADD-only plan cannot be used as a REPLACE operation.")
    required = {
        "operation", "operation_id", "commit_sha", "path", "file_baseline_sha256",
        "exact_old_passage", "old_passage_sha256", "anchor", "replacement_markdown",
        "replacement_sha256", "selected_verified_facts", "claim_to_fact_mapping",
    }
    missing = sorted(required - operation.keys())
    if missing:
        raise UpdaterError("REPLACE operation is missing required fields: {}".format(", ".join(missing)))
    if operation.get("commit_sha") != commit_sha or commit_sha != SYSTEMS_REPLACEMENT_COMMIT:
        raise UpdaterError("REPLACE operation commit does not match the supported 5472 commit.")
    if operation.get("path") != SYSTEMS_REPLACEMENT_TARGET:
        raise UpdaterError("REPLACE operation targets an unsupported Knowledge path.")
    if not re.fullmatch(r"[0-9a-f]{64}", operation.get("file_baseline_sha256", "")):
        raise UpdaterError("REPLACE operation has an invalid whole-file baseline SHA-256.")
    if sha256(file_bytes) != operation["file_baseline_sha256"]:
        raise UpdaterError("REPLACE target file does not match its whole-file baseline SHA-256.")
    try:
        content = file_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpdaterError("REPLACE target file is not valid UTF-8.") from exc

    passage = operation.get("exact_old_passage")
    if passage != SYSTEMS_REPLACEMENT_OLD_PASSAGE:
        raise UpdaterError("REPLACE operation old passage is altered from the exact approved stale passage.")
    if operation.get("old_passage_sha256") != sha256(passage.encode("utf-8")):
        raise UpdaterError("REPLACE operation old-passage SHA-256 is incorrect.")
    if content.count(passage) != 1 or _anchored_passage_count(
            content, SYSTEMS_REPLACEMENT_ANCHOR, passage) != 1:
        raise UpdaterError("REPLACE target is changed, absent, or non-unique at the recorded anchor.")
    anchor = operation.get("anchor")
    if anchor != {"heading_path": list(SYSTEMS_REPLACEMENT_ANCHOR), "occurrence": 1}:
        raise UpdaterError("REPLACE operation has an incorrect exact anchor/location.")

    if not re.fullmatch(r"[0-9a-f]{64}", operation.get("replacement_sha256", "")):
        raise UpdaterError("REPLACE operation has an invalid replacement SHA-256.")
    replacement = operation.get("replacement_markdown")
    if not isinstance(replacement, str) or sha256(replacement.encode("utf-8")) != operation["replacement_sha256"]:
        raise UpdaterError("REPLACE operation replacement SHA-256 is incorrect.")
    # Reuse the existing sentence-level fact validator; no alternate prose or
    # additional factual claims are permitted in this replacement.
    draft = render_systems_documentation_draft(SYSTEMS_DOCUMENTATION_TARGET, verified_facts)
    if replacement != draft["paragraph"]:
        raise UpdaterError("REPLACE text contains claims outside the validated Systems renderer.")
    if operation.get("selected_verified_facts") != draft["supporting_facts"]:
        raise UpdaterError("REPLACE operation references missing or unselected supporting facts.")
    if operation.get("claim_to_fact_mapping") != draft["claims"]:
        raise UpdaterError("REPLACE claim-to-fact mapping is missing or does not match the validated renderer.")


def render_code_inventory_replacement_draft(verified_facts: list[dict]) -> dict:
    """Build the fixed Code Inventory statement only from independently parsed Player.gd facts."""
    specs = (
        ("player_script", "script_file", "(Game Name)/Player.gd", "Script `res://Player.gd` exists."),
        ("player_inheritance", "script_inheritance", "(Game Name)/Player.gd", "`res://Player.gd` extends `CharacterBody2D`."),
        ("player_speed", "script_export", "(Game Name)/Player.gd",
         "`res://Player.gd` exports `speed` using `@export var speed: float = 300.0`."),
        ("player_physics_function", "script_function", "(Game Name)/Player.gd",
         "`res://Player.gd` defines `func _physics_process(_delta: float) -> void:`."),
        ("player_input_call", "script_call", "(Game Name)/Player.gd", "`res://Player.gd` calls `Input.get_vector()`."),
        ("player_slide_call", "script_call", "(Game Name)/Player.gd", "`res://Player.gd` calls `move_and_slide()`."),
    )
    selected: dict[str, dict] = {}
    for key, category, source, statement in specs:
        matches = [fact for fact in verified_facts
                   if fact.get("category") == category and fact.get("source") == source
                   and fact.get("statement") == statement]
        if len(matches) != 1:
            raise UpdaterError(
                f"Cannot generate the Code Inventory replacement: expected exactly one verified `{key}` fact, found {len(matches)}."
            )
        selected[key] = matches[0]

    claims = [
        {"key": "script_inheritance_and_speed",
         "text": "`Player.gd` extends `CharacterBody2D` and exports `speed` with a default value of `300.0`.",
         "verified_fact_ids": [documentation_fact_id(selected[key])
                               for key in ("player_script", "player_inheritance", "player_speed")]},
        {"key": "physics_function", "text": "It defines `_physics_process`.",
         "verified_fact_ids": [documentation_fact_id(selected["player_physics_function"])]},
        {"key": "detected_script_calls",
         "text": "The Inspector detects calls to `Input.get_vector()` and `move_and_slide()` in the script.",
         "verified_fact_ids": [documentation_fact_id(selected[key])
                               for key in ("player_input_call", "player_slide_call")]},
    ]
    supporting_facts = [
        {"id": documentation_fact_id(selected[key]), "category": selected[key]["category"],
         "source": selected[key]["source"], "statement": selected[key]["statement"]}
        for key, *_ in specs
    ]
    return {"target": CODE_INVENTORY_REPLACEMENT_TARGET,
            "paragraph": CODE_INVENTORY_REPLACEMENT_TEXT,
            "supporting_facts": supporting_facts, "claims": claims}


def validate_code_inventory_replacement_operation(operation: dict, file_bytes: bytes,
                                                   verified_facts: list[dict], commit_sha: str) -> None:
    """Validate Code Inventory's narrow current-state replacement against Git-derived facts."""
    required = {
        "operation", "operation_id", "commit_sha", "path", "file_baseline_sha256",
        "exact_old_passage", "old_passage_sha256", "anchor", "replacement_markdown",
        "replacement_sha256", "selected_verified_facts", "claim_to_fact_mapping",
    }
    missing = sorted(required - operation.keys())
    if operation.get("operation") != "REPLACE":
        raise UpdaterError("An ADD-only plan cannot be used as a REPLACE operation.")
    if missing:
        raise UpdaterError("Code Inventory REPLACE is missing required fields: " + ", ".join(missing))
    if commit_sha != CODE_INVENTORY_REPLACEMENT_COMMIT or operation.get("commit_sha") != commit_sha:
        raise UpdaterError("Code Inventory REPLACE is restricted to the verified Player commit.")
    if operation.get("path") != CODE_INVENTORY_REPLACEMENT_TARGET:
        raise UpdaterError("Code Inventory REPLACE targets an unsupported Knowledge path.")
    if not re.fullmatch(r"[0-9a-f]{64}", operation.get("file_baseline_sha256", "")):
        raise UpdaterError("Code Inventory REPLACE has an invalid whole-file baseline SHA-256.")
    if sha256(file_bytes) != operation["file_baseline_sha256"]:
        raise UpdaterError("Code Inventory target does not match its whole-file baseline SHA-256.")
    try:
        content = file_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpdaterError("Code Inventory target is not valid UTF-8.") from exc
    passage = operation.get("exact_old_passage")
    if passage != CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE:
        raise UpdaterError("Code Inventory old passage does not match the exact approved stale passage.")
    if operation.get("old_passage_sha256") != sha256(passage.encode("utf-8")):
        raise UpdaterError("Code Inventory old-passage SHA-256 is incorrect.")
    if content.count(passage) != 1 or _anchored_passage_count(
            content, CODE_INVENTORY_REPLACEMENT_ANCHOR, passage) != 1:
        raise UpdaterError("Code Inventory target is changed, absent, or non-unique at its recorded anchor.")
    if operation.get("anchor") != {
            "heading_path": list(CODE_INVENTORY_REPLACEMENT_ANCHOR), "occurrence": 1}:
        raise UpdaterError("Code Inventory REPLACE anchor/location is invalid or ambiguous.")
    replacement = operation.get("replacement_markdown")
    if (not re.fullmatch(r"[0-9a-f]{64}", operation.get("replacement_sha256", ""))
            or not isinstance(replacement, str)
            or sha256(replacement.encode("utf-8")) != operation["replacement_sha256"]):
        raise UpdaterError("Code Inventory replacement SHA-256 is incorrect.")
    draft = render_code_inventory_replacement_draft(verified_facts)
    if replacement != draft["paragraph"]:
        raise UpdaterError("Code Inventory replacement contains unsupported or unselected claims.")
    if operation.get("selected_verified_facts") != draft["supporting_facts"]:
        raise UpdaterError("Code Inventory REPLACE selected facts do not match independently verified facts.")
    if operation.get("claim_to_fact_mapping") != draft["claims"]:
        raise UpdaterError("Code Inventory claim-to-fact mapping is invalid or unsupported.")
    expected_id = sha256("\0".join((commit_sha, CODE_INVENTORY_REPLACEMENT_TARGET,
                                     operation["old_passage_sha256"], operation["replacement_sha256"])).encode("utf-8"))[:16]
    if operation.get("operation_id") != expected_id:
        raise UpdaterError("Code Inventory REPLACE operation ID does not match its content hashes.")


def data_assets_replacement_draft(verified_facts: list[dict], variant: str) -> dict:
    variants = {
        "main_scene": {
            "replacement": DATA_ASSETS_MAIN_REPLACEMENT,
            "specs": (
                ("main_scene", "project_setting", "(Game Name)/project.godot",
                 '`project.godot` sets `application/run/main_scene` to `"res://Main.tscn"`.'),
                ("main_root", "scene_root", "(Game Name)/Main.tscn",
                 "`res://Main.tscn` has root `Main` of type `Node2D`."),
                ("main_player_instance", "scene_instance", "(Game Name)/Main.tscn",
                 "`res://Main.tscn` instances `res://Player.tscn` as node `Player`."),
                ("player_root", "scene_root", "(Game Name)/Player.tscn",
                 "`res://Player.tscn` has root `Player` of type `CharacterBody2D`."),
            ),
            "claims": (
                ("main_scene_configuration", "`project.godot` selects `res://Main.tscn` as the main scene.", ("main_scene",)),
                ("main_scene_composition", "`res://Main.tscn` has a `Main` `Node2D` root and instances `res://Player.tscn` as node `Player`.", ("main_root", "main_player_instance")),
                ("player_scene_root", "`res://Player.tscn` has a `Player` `CharacterBody2D` root.", ("player_root",)),
            ),
        },
        "resources": {
            "replacement": DATA_ASSETS_OTHER_REPLACEMENT,
            "specs": (
                ("player_script_attachment", "scene_script", "(Game Name)/Player.tscn",
                 "Node `Player` in `res://Player.tscn` attaches `res://Player.gd`."),
                ("sprite_texture_assignment", "scene_resource", "(Game Name)/Player.tscn",
                 "Node `Sprite2D` in `res://Player.tscn` assigns `res://icon.svg` to `texture`."),
            ),
            "claims": (
                ("player_script_attachment", "`Player.tscn` attaches `Player.gd` to its `Player` root.", ("player_script_attachment",)),
                ("sprite_texture_assignment", "Its `Sprite2D` node assigns `res://icon.svg` to `texture`.", ("sprite_texture_assignment",)),
            ),
        },
    }
    if variant not in variants:
        raise UpdaterError(f"Unknown Project Assets replacement variant: {variant}")
    schema = variants[variant]
    selected = {}
    for key, category, source, statement in schema["specs"]:
        matches = [fact for fact in verified_facts
                   if fact.get("category") == category and fact.get("source") == source
                   and fact.get("statement") == statement]
        if len(matches) != 1:
            raise UpdaterError(f"Project Assets replacement requires exactly one verified `{key}` fact; found {len(matches)}.")
        selected[key] = matches[0]
    claims = [{"key": key, "text": text,
               "verified_fact_ids": [documentation_fact_id(selected[fact_key]) for fact_key in fact_keys]}
              for key, text, fact_keys in schema["claims"]]
    supporting_facts = [{"id": documentation_fact_id(selected[key]), "category": selected[key]["category"],
                         "source": selected[key]["source"], "statement": selected[key]["statement"]}
                        for key, *_ in schema["specs"]]
    return {"paragraph": schema["replacement"], "supporting_facts": supporting_facts, "claims": claims}


def validate_data_assets_replacement_operation(operation: dict, file_bytes: bytes,
                                               verified_facts: list[dict], commit_sha: str,
                                               variant: str) -> None:
    if operation.get("operation") != "REPLACE":
        raise UpdaterError("An ADD-only plan cannot be used as a REPLACE operation.")
    if commit_sha != DATA_ASSETS_REPLACEMENT_COMMIT or operation.get("commit_sha") != commit_sha:
        raise UpdaterError("Project Assets replacement is restricted to the verified Player commit.")
    if operation.get("path") != DATA_ASSETS_REPLACEMENT_TARGET:
        raise UpdaterError("Project Assets replacement targets an unsupported Knowledge path.")
    variants = {
        "main_scene": (DATA_ASSETS_MAIN_OLD_PASSAGE, DATA_ASSETS_MAIN_ANCHOR),
        "resources": (DATA_ASSETS_OTHER_OLD_PASSAGE, DATA_ASSETS_OTHER_ANCHOR),
    }
    passage, anchor_path = variants[variant]
    if sha256(file_bytes) != operation.get("file_baseline_sha256"):
        raise UpdaterError("Project Assets target does not match its whole-file baseline SHA-256.")
    try:
        content = file_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpdaterError("Project Assets target is not valid UTF-8.") from exc
    if operation.get("exact_old_passage") != passage or operation.get("old_passage_sha256") != sha256(passage.encode("utf-8")):
        raise UpdaterError("Project Assets old passage or hash does not match its approved exact passage.")
    if content.count(passage) != 1 or _anchored_passage_count(content, anchor_path, passage) != 1:
        raise UpdaterError("Project Assets old passage is absent, changed, or non-unique at its anchor.")
    if operation.get("anchor") != {"heading_path": list(anchor_path), "occurrence": 1}:
        raise UpdaterError("Project Assets replacement anchor/location is invalid or ambiguous.")
    draft = data_assets_replacement_draft(verified_facts, variant)
    replacement = operation.get("replacement_markdown")
    if (replacement != draft["paragraph"]
            or operation.get("replacement_sha256") != sha256(draft["paragraph"].encode("utf-8"))):
        raise UpdaterError("Project Assets replacement contains unsupported claims or an incorrect hash.")
    if operation.get("selected_verified_facts") != draft["supporting_facts"]:
        raise UpdaterError("Project Assets replacement selected facts do not match the verified facts.")
    if operation.get("claim_to_fact_mapping") != draft["claims"]:
        raise UpdaterError("Project Assets replacement claim-to-fact mapping is invalid.")
    expected_id = sha256("\0".join((commit_sha, DATA_ASSETS_REPLACEMENT_TARGET,
                                     operation["old_passage_sha256"], operation["replacement_sha256"])).encode("utf-8"))[:16]
    if operation.get("operation_id") != expected_id:
        raise UpdaterError("Project Assets REPLACE operation ID does not match its content hashes.")


def build_data_assets_replacement_operation(root: Path, commit_sha: str,
                                             verified_facts: list[dict], variant: str) -> dict:
    if commit_sha != DATA_ASSETS_REPLACEMENT_COMMIT:
        raise UpdaterError("Project Assets replacement is restricted to commit 5472bacbe12818379b9a20771885b63ea4725ea0.")
    passage, anchor = {
        "main_scene": (DATA_ASSETS_MAIN_OLD_PASSAGE, DATA_ASSETS_MAIN_ANCHOR),
        "resources": (DATA_ASSETS_OTHER_OLD_PASSAGE, DATA_ASSETS_OTHER_ANCHOR),
    }[variant]
    path = root / DATA_ASSETS_REPLACEMENT_TARGET
    try:
        original = path.read_bytes()
    except OSError as exc:
        raise UpdaterError(f"Cannot read replacement target {DATA_ASSETS_REPLACEMENT_TARGET}: {exc}") from exc
    draft = data_assets_replacement_draft(verified_facts, variant)
    old_hash = sha256(passage.encode("utf-8"))
    replacement_hash = sha256(draft["paragraph"].encode("utf-8"))
    operation = {
        "operation": "REPLACE",
        "operation_id": sha256("\0".join((commit_sha, DATA_ASSETS_REPLACEMENT_TARGET, old_hash, replacement_hash)).encode("utf-8"))[:16],
        "commit_sha": commit_sha, "path": DATA_ASSETS_REPLACEMENT_TARGET,
        "file_baseline_sha256": sha256(original), "exact_old_passage": passage,
        "old_passage_sha256": old_hash, "anchor": {"heading_path": list(anchor), "occurrence": 1},
        "replacement_markdown": draft["paragraph"], "replacement_sha256": replacement_hash,
        "selected_verified_facts": draft["supporting_facts"], "claim_to_fact_mapping": draft["claims"],
    }
    validate_data_assets_replacement_operation(operation, original, verified_facts, commit_sha, variant)
    return operation


def build_code_inventory_replacement_operation(root: Path, commit_sha: str,
                                               verified_facts: list[dict]) -> dict:
    """Create one proposal-only Code Inventory REPLACE operation for the verified Player commit."""
    if commit_sha != CODE_INVENTORY_REPLACEMENT_COMMIT:
        raise UpdaterError("Code Inventory replacement is restricted to commit 5472bacbe12818379b9a20771885b63ea4725ea0.")
    path = root / CODE_INVENTORY_REPLACEMENT_TARGET
    try:
        original = path.read_bytes()
    except OSError as exc:
        raise UpdaterError(f"Cannot read replacement target {CODE_INVENTORY_REPLACEMENT_TARGET}: {exc}") from exc
    content = original.decode("utf-8")
    if content.count(CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE) != 1 or _anchored_passage_count(
            content, CODE_INVENTORY_REPLACEMENT_ANCHOR, CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE) != 1:
        raise UpdaterError("The exact Code Inventory stale passage is absent or non-unique at its recorded anchor.")
    draft = render_code_inventory_replacement_draft(verified_facts)
    old_hash = sha256(CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE.encode("utf-8"))
    replacement_hash = sha256(draft["paragraph"].encode("utf-8"))
    operation = {
        "operation": "REPLACE",
        "operation_id": sha256("\0".join((commit_sha, CODE_INVENTORY_REPLACEMENT_TARGET,
                                          old_hash, replacement_hash)).encode("utf-8"))[:16],
        "commit_sha": commit_sha,
        "path": CODE_INVENTORY_REPLACEMENT_TARGET,
        "file_baseline_sha256": sha256(original),
        "exact_old_passage": CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE,
        "old_passage_sha256": old_hash,
        "anchor": {"heading_path": list(CODE_INVENTORY_REPLACEMENT_ANCHOR), "occurrence": 1},
        "replacement_markdown": draft["paragraph"],
        "replacement_sha256": replacement_hash,
        "selected_verified_facts": draft["supporting_facts"],
        "claim_to_fact_mapping": draft["claims"],
    }
    validate_code_inventory_replacement_operation(operation, original, verified_facts, commit_sha)
    return operation


def replacement_passage_state(root: Path, target: str, old_passage: str,
                             replacement: str, anchor: tuple[str, ...]) -> tuple[str, str | None]:
    """Distinguish a uniquely stale passage from an already-applied or ambiguous target."""
    path = root / target
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return "requires_review", f"Cannot read {target}: {exc}"
    old_count = text.count(old_passage)
    replacement_count = text.count(replacement)
    try:
        old_at_anchor = _anchored_passage_count(text, anchor, old_passage)
        replacement_at_anchor = _anchored_passage_count(text, anchor, replacement)
    except UpdaterError as exc:
        return "requires_review", str(exc)
    if old_count == 1 and old_at_anchor == 1 and replacement_count == 0:
        return "pending", None
    if old_count == 0 and replacement_count == 1 and replacement_at_anchor == 1:
        return "already_applied", None
    return "requires_review", (
        f"{target} is neither an exact uniquely anchored stale passage nor an exact uniquely anchored applied replacement "
        f"(old occurrences: {old_count}, old at anchor: {old_at_anchor}, "
        f"replacement occurrences: {replacement_count}, replacement at anchor: {replacement_at_anchor})."
    )


def select_pending_replacement_operations(root: Path, commit_sha: str, verified_facts: list[dict],
                                          requested_target: str | None = None
                                          ) -> tuple[list[dict], list[str], list[str]]:
    """Skip exact prior replacements and build at most one outstanding REPLACE operation."""
    if commit_sha != SYSTEMS_REPLACEMENT_COMMIT:
        return [], [], []
    candidates = [requested_target] if requested_target else [
        SYSTEMS_REPLACEMENT_TARGET, CODE_INVENTORY_REPLACEMENT_TARGET, DATA_ASSETS_REPLACEMENT_TARGET,
    ]
    operations: list[dict] = []
    skipped: list[str] = []
    conflicts: list[str] = []
    for target in candidates:
        if target == SYSTEMS_REPLACEMENT_TARGET:
            draft = render_systems_documentation_draft(SYSTEMS_DOCUMENTATION_TARGET, verified_facts)
            state, reason = replacement_passage_state(
                root, target, SYSTEMS_REPLACEMENT_OLD_PASSAGE, draft["paragraph"],
                SYSTEMS_REPLACEMENT_ANCHOR,
            )
            builder = build_systems_replacement_operation
        elif target == CODE_INVENTORY_REPLACEMENT_TARGET:
            state, reason = replacement_passage_state(
                root, target, CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE,
                CODE_INVENTORY_REPLACEMENT_TEXT, CODE_INVENTORY_REPLACEMENT_ANCHOR,
            )
            builder = build_code_inventory_replacement_operation
        elif target == DATA_ASSETS_REPLACEMENT_TARGET:
            variants = (
                ("main_scene", DATA_ASSETS_MAIN_OLD_PASSAGE, DATA_ASSETS_MAIN_REPLACEMENT, DATA_ASSETS_MAIN_ANCHOR),
                ("resources", DATA_ASSETS_OTHER_OLD_PASSAGE, DATA_ASSETS_OTHER_REPLACEMENT, DATA_ASSETS_OTHER_ANCHOR),
            )
            for variant, old_passage, replacement, anchor in variants:
                state, reason = replacement_passage_state(root, target, old_passage, replacement, anchor)
                if state == "already_applied":
                    skipped.append(f"{target}#{variant}")
                    continue
                if state == "requires_review":
                    conflicts.append(reason or f"{target} needs manual review.")
                    return [], skipped, conflicts
                return [build_data_assets_replacement_operation(root, commit_sha, verified_facts, variant)], skipped, []
            continue
        else:
            return [], skipped, [f"No target-specific REPLACE proposal builder is available for {target}."]
        if state == "already_applied":
            skipped.append(target)
            continue
        if state == "requires_review":
            conflicts.append(reason or f"{target} needs manual review.")
            break
        operations.append(builder(root, commit_sha, verified_facts))
        # The Applier accepts one REPLACE operation per proposal. Leave later
        # stale targets pending for a separate versioned proposal.
        break
    return operations, skipped, conflicts


def render_documentation_drafts(results: list[dict], commit_sha: str) -> list[str]:
    """Render proposal-only drafts; this does not create or apply Knowledge edits."""
    target_results = [item for item in results
                      if item.get("recommendation", {}).get("path") == SYSTEMS_DOCUMENTATION_TARGET]
    config_results = [item for item in results
                      if item.get("recommendation", {}).get("path") == DEVELOPMENT_CONFIGURATION_TARGET]
    if not target_results and not config_results:
        return []
    if len(target_results) > 1:
        raise UpdaterError("Cannot generate the Systems documentation draft from duplicate target assessments.")
    if len(config_results) > 1:
        raise UpdaterError("Cannot generate the Development Configuration table from duplicate target assessments.")
    lines = ["## Proposed Documentation Drafts (Proposal Only)", ""]
    if target_results:
        draft = render_systems_documentation_draft(
            SYSTEMS_DOCUMENTATION_TARGET, target_results[0].get("verified_facts", []))
        lines.extend([f"### `{draft['target']}`", "", "**Draft paragraph:**", "",
                      f"> {draft['paragraph']}", "", "**Claim-to-fact support:**", ""])
        for claim in draft["claims"]:
            lines.extend([f"- Claim (`{claim['key']}`): {claim['text']}"])
            supports = set(claim["verified_fact_ids"])
            for fact in draft["supporting_facts"]:
                if fact["id"] in supports:
                    lines.append(f"  - Verified fact `{fact['id']}`: {fact['statement']} Source: `{fact['source']}` at `{commit_sha}`.")
        lines.append("")
    if config_results:
        draft = render_development_input_table(
            DEVELOPMENT_CONFIGURATION_TARGET, config_results[0].get("verified_facts", []))
        lines.extend([f"### `{draft['target']}`", "", "**Draft table:**", "", draft["table"],
                      "", "**Row-to-fact support:**", ""])
        for row, fact in zip(draft["rows"], draft["supporting_facts"]):
            lines.append(
                f"- `{row['action']}` / `{row['key']}`; Verified fact `{fact['id']}`: "
                f"{fact['statement']} Source: `{fact['source']}` at `{commit_sha}`."
            )
        lines.append("")
    return lines


def current_inspector_evidence(root: Path, sha: str, parent: str, inspector) -> tuple[str, str]:
    """Prefer a current fingerprinted report, then current canonical, never a stale report."""
    fingerprint = inspector.implementation_fingerprint()
    candidates = (
        root / ".ai/changes" / f"{sha}-inspection-{fingerprint}.md",
        root / ".ai/changes" / f"{sha}-inspection.md",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            report = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if inspector.embedded_fingerprint(report) == fingerprint:
            return report, path.relative_to(root).as_posix()
    return inspector.inspection_record(sha, parent, root), "(freshly generated in memory)"


def build_proposal(root: Path, sha: str, entry: dict, review_path: str,
                   replacement_target: str | None = None) -> tuple[str, str]:
    info = commit_info(root, sha)
    full_sha, parents, author, date, subject = info
    if not parents:
        raise UpdaterError(f"Commit {sha} has no parent; this updater requires a verifiable commit diff.")
    parent = parents.split()[0]
    review_text = safe_read(root, review_path)
    if entry.get("review_sha256") != sha256(review_text.encode("utf-8")):
        raise UpdaterError(f"Review hash does not match NotebookLM review state: {review_path}")
    if entry.get("commit_sha") != sha or entry.get("status") not in {"completed", "reconciled"}:
        raise UpdaterError(f"Review state does not mark {sha} as completed.")
    config = read_json(root / ".ai/state/notebooklm_project.json", "NotebookLM project configuration")
    notebook_id = config.get("notebook_id")
    if not notebook_id or notebook_id != entry.get("notebook_id"):
        raise UpdaterError("The review's NotebookLM notebook does not match the configured project notebook.")
    recorded_path = str(entry.get("inspection_file", f".ai/changes/{sha}-inspection.md")).replace("\\", "/")
    inspection_path = Path(recorded_path)
    if inspection_path.is_absolute() or ".." in inspection_path.parts or not recorded_path.startswith(".ai/changes/"):
        raise UpdaterError("NotebookLM review state has an unsafe inspection path.")
    stored_inspection = safe_read(root, recorded_path)
    if entry.get("inspection_sha256") != sha256(stored_inspection.encode("utf-8")):
        raise UpdaterError(f"Inspection report hash does not match NotebookLM review state: {recorded_path}")

    inspector = load_inspector(root)
    fresh_report, evidence_report_path = current_inspector_evidence(root, sha, parent, inspector)
    status, results, source_notes, unresolved = evaluate_recommendations(root, sha, review_text, fresh_report, inspector)
    application_plan, supplemental_facts = build_application_plan(root, sha, results)
    replacement_operations = []
    skipped_replacements: list[str] = []
    if replacement_target is not None or sha == SYSTEMS_REPLACEMENT_COMMIT:
        replacement_facts, _config_path, _project_dir, _source_paths = project_facts(root, sha, inspector)
        replacement_operations, skipped_replacements, replacement_conflicts = select_pending_replacement_operations(
            root, sha, replacement_facts, requested_target=replacement_target)
        if replacement_conflicts:
            status = "REQUIRES_REVIEW"
            unresolved.extend({"path": replacement_target or "REPLACE targets", "reason": reason}
                              for reason in replacement_conflicts)
        elif replacement_operations:
            status = "UPDATE_PROPOSED"
        elif replacement_target is not None and skipped_replacements:
            status = "NO_UPDATE_REQUIRED"
        # The Applier deliberately accepts exactly one REPLACE operation per proposal
        # and refuses mixed ADD/REPLACE plans. Target-specific proposals are isolated.
        if replacement_target is not None or replacement_operations or replacement_conflicts:
            application_plan = []
        # A validated replacement of an exact stale passage is an update even when
        # its underlying implementation facts already appear in an applied block.
    source_report_fingerprint = inspector.embedded_fingerprint(stored_inspection)
    current_fingerprint = inspector.implementation_fingerprint()
    stale_review_input = source_report_fingerprint != current_fingerprint
    source_notes.append(f"- Current Inspector report used to validate project facts: `{evidence_report_path}`.")
    if stale_review_input:
        source_notes.append("- NotebookLM reviewed a stale/legacy inspection report; recommendations below were cross-checked independently using current Git blobs and a fresh Inspector report generated in memory. The old report, review, and proposal were not replaced.")

    lines = ["# Knowledge Update Proposal", "", "## Final Status", "", f"`{status}`", "",
             "## Commit", "", f"- Commit: `{full_sha}`", f"- Parent: `{parent}`", f"- Subject: {subject}", f"- Author: {author}", f"- Date: {date}",
             f"- NotebookLM notebook ID: `{notebook_id}`", "", "## Review Used", "",
             f"- Review: `{review_path}`", f"- Inspection report reviewed by NotebookLM: `{recorded_path}`",
             f"- Current Inspector fingerprint: `sha256:{current_fingerprint}`",
             f"- Current Inspector evidence source: `{evidence_report_path}`", "",
             "## Verified Git and Inspector Evidence", "", *source_notes, "", "## Knowledge Files Affected", ""]
    reviewed_without_change = []
    for item in results:
        recommendation = item["recommendation"]
        target = recommendation["path"]
        lines.extend([f"### `{target}`", "", f"- NotebookLM recommendation: {recommendation['what'] or '(not specified)' }",
                      f"- Why: {recommendation['why'] or '(not specified)' }", "- Evidence:"])
        if item["facts"]:
            for fact in item["facts"]:
                state = "missing from this Knowledge file" if fact in item["missing"] else "already documented in this Knowledge file"
                lines.append(f"  - {fact['statement']} Source: `{fact['source']}` at `{sha}`; {state}.")
        else:
            lines.append("  - No corresponding structural fact could be verified from the commit tree and Inspector output.")
        if target in supplemental_facts:
            lines.append("- Additional verified facts selected for the Application Plan (not NotebookLM recommendations):")
            for fact in supplemental_facts[target]:
                lines.append(f"  - {fact['statement']} Source: `{fact['source']}` at `{sha}`; missing from this Knowledge file.")
        lines.extend(["", "- Relevant section/location: Determined from the recommendation's target file; exact subsection is not inferred."])
        if item["missing"]:
            lines.extend(["- Proposed additions:"])
            lines.extend(f"  - {fact['statement']}" for fact in item["missing"])
        elif item["facts"]:
            lines.append("- Proposed replacement/addition: None; the matched facts are already documented.")
            reviewed_without_change.append(target)
        else:
            lines.append("- Proposed replacement/addition: None; verification is insufficient.")
        lines.append("")
    if not results:
        lines.extend(["No Knowledge targets were available for assessment.", ""])
    lines.extend(render_application_plan(application_plan, replacements=replacement_operations))
    lines.extend(render_documentation_drafts(results, sha))
    lines.extend(["## Knowledge Files Reviewed but Requiring No Change", ""])
    lines.extend([f"- `{path}` — all recommendation-matched facts were already documented and verified." for path in sorted(set(reviewed_without_change))] or ["- None."])
    lines.extend(["", "## Conflicts or Uncertainty", ""])
    if stale_review_input:
        lines.append("- The NotebookLM review was based on an inspection report without the current Inspector fingerprint. The recommendations were not treated as facts; each proposed item is independently verified against the commit tree and current Inspector output.")
    lines.extend(f"- `{item}` was skipped because its exact replacement is already present at the approved anchor." for item in skipped_replacements)
    lines.extend(f"- `{item['path']}`: {item['reason']}." for item in unresolved)
    if not stale_review_input and not unresolved:
        lines.append("- No unsupported recommendation or stale review input was detected.")
    lines.extend(["", "## Proposed Changes", ""])
    if status == "UPDATE_PROPOSED":
        lines.append("This file proposes additions only. It does not modify Knowledge.")
    elif status == "NO_UPDATE_REQUIRED":
        lines.append("No Knowledge changes are proposed; every checked fact is already documented.")
    else:
        lines.append("No unsupported Knowledge text is proposed. Human review is required for the unresolved items above.")
    lines.extend(["", "---", "", "Git/project files are authoritative. NotebookLM text is a recommendation. Applying proposals is not implemented.", ""])
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


def publish_proposal(path: Path, content: str, *, replace_existing: bool = False) -> Path | None:
    """Publish without clobbering; replacement preserves the prior proposal in a fixed backup."""
    if not path.exists():
        publish_no_clobber(path, content)
        return None
    if not replace_existing:
        raise UpdaterError(f"Proposal already exists; refusing to overwrite: {path}")

    backup = path.with_name(f"{path.stem}.previous{path.suffix}")
    if backup.exists():
        raise UpdaterError(f"Proposal backup already exists; refusing to overwrite: {backup}")

    try:
        os.replace(path, backup)
    except OSError as exc:
        raise UpdaterError(f"Could not preserve existing proposal at {backup}: {exc}") from exc

    try:
        publish_no_clobber(path, content)
    except Exception:
        # Restore the previous canonical proposal only if no other file appeared there.
        # Never replace a concurrent writer's file during rollback.
        if not path.exists() and backup.exists():
            try:
                os.replace(backup, path)
            except OSError:
                pass
        raise
    return backup


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate proposal-only Knowledge updates from completed NotebookLM reviews.")
    parser.add_argument("commit", nargs="?", help="full commit SHA with a completed NotebookLM review")
    parser.add_argument("--dry-run", action="store_true", help="show candidates and proposed statuses without writing files")
    parser.add_argument("--replace-target", choices=(SYSTEMS_REPLACEMENT_TARGET, CODE_INVENTORY_REPLACEMENT_TARGET,
                                                        DATA_ASSETS_REPLACEMENT_TARGET),
                        help="build one isolated target-specific REPLACE proposal (requires --versioned and an explicit commit)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replace", action="store_true", help="replace an existing proposal after preserving it as .previous.md")
    mode.add_argument("--versioned", action="store_true", help="write a new proposal named with the current Inspector/Updater fingerprint")
    args = parser.parse_args(argv)
    if args.replace and not args.commit:
        parser.error("--replace requires an explicit commit SHA")
    if args.versioned and not args.commit:
        parser.error("--versioned requires an explicit commit SHA")
    if args.replace_target and (not args.versioned or not args.commit or args.replace):
        parser.error("--replace-target requires an explicit commit and --versioned, and cannot be combined with --replace")
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
            status, proposal = build_proposal(root, sha, entry, review_path,
                                              replacement_target=args.replace_target)
            replacement_operation_id = None
            if args.replace_target == DATA_ASSETS_REPLACEMENT_TARGET:
                operations = parse_replacement_operations(proposal)
                if len(operations) != 1:
                    raise UpdaterError("Project Assets replacement proposal must contain exactly one operation.")
                replacement_operation_id = operations[0]["operation_id"]
            out = proposal_path(root, sha, versioned=args.versioned,
                                replacement_target=args.replace_target,
                                replacement_operation_id=replacement_operation_id)
            if args.dry_run:
                print(f"Assessment for {sha}: {status}")
                if out.exists():
                    if args.replace:
                        backup = out.with_name(f"{out.stem}.previous{out.suffix}")
                        if backup.exists():
                            print(f"Would refuse replacement because backup already exists: {backup.relative_to(root)}")
                        else:
                            print(f"Would preserve existing proposal as {backup.relative_to(root)} and publish replacement")
                    else:
                        print(f"Would refuse to overwrite existing proposal: {out.relative_to(root)}")
                else:
                    print(f"Would process {sha}: {status} -> {out}")
                continue
            backup = publish_proposal(out, proposal, replace_existing=args.replace and not args.versioned)
            if backup:
                print(f"Preserved previous proposal at {backup.relative_to(root)}")
            print(f"Wrote {out.relative_to(root)} ({status})")
        return 0
    except (UpdaterError, OSError, UnicodeError) as exc:
        print(f"knowledge_updater: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

