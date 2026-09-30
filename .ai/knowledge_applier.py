#!/usr/bin/env python3
"""Safely apply explicitly planned, project-verified Knowledge additions."""

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


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
FINGERPRINT_RE = re.compile(r"(?m)^- Current Inspector fingerprint: `sha256:([0-9a-f]{64})`$")
TARGET_HEADING_RE = re.compile(r"(?m)^### `(Knowledge/[^`]+\.md)`\s*$")
PLAN_FACT_RE = re.compile(r"(?m)^- Verified fact: (.+?)\s*$")
EVIDENCE_RE = re.compile(
    r"(?m)^\s{2}- (.+?) Source: `([^`]+)` at `([0-9a-f]{40})`; (missing from|already documented in) this Knowledge file\.\s*$"
)
BASELINE_RE = re.compile(r"(?m)^- Base SHA-256: `([0-9a-f]{64})`\s*$")
ANCHOR_RE = re.compile(r"(?m)^- Insert after: `([^`]+)`\s*$")
CONTEXT_RE = re.compile(r"(?m)^- Context: (.+?)\s*$")
REPLACE_HEADING_RE = re.compile(r"(?m)^### REPLACE operation `([0-9a-f]{16})`\s*$")

PROJECT_OVERVIEW_TARGET = "Knowledge/00 Project/Project Overview.md"
PROJECT_OVERVIEW_OLD_PASSAGE = (
    "The project has one scene, `Main.tscn`, configured as its main scene. It contains a single `Node2D` root named `Main` and no child nodes or attached script."
)
PROJECT_OVERVIEW_REPLACEMENT = (
    "`Main.tscn` remains configured as the main scene. It has a `Main` `Node2D` root and instances `Player.tscn` as node `Player`."
)
PROJECT_OVERVIEW_ANCHOR = ("# Project Overview", "## Current state")

ARCHITECTURE_OVERVIEW_TARGET = "Knowledge/Architecture/Architecture Overview.md"
ARCHITECTURE_OVERVIEW_OLD_PASSAGE = (
    "The inspected Godot project has a minimal runtime entry point: `Main.tscn` is configured as the main scene, and its entire node tree is a single `Node2D` root named `Main`. It has no child nodes, attached script, or signal connections."
)
ARCHITECTURE_OVERVIEW_REPLACEMENT = (
    "`Main.tscn` remains configured as the main scene and has a `Main` `Node2D` root. It instances `Player.tscn` as node `Player`; `Player.tscn` has a `Player` `CharacterBody2D` root with `Player.gd` attached to node `Player`."
)
ARCHITECTURE_OVERVIEW_ANCHOR = ("# Architecture Overview", "## Current shape")

DEVELOPMENT_CONFIGURATION_ADD_TARGET = "Knowledge/00 Project/Development Configuration.md"
DEVELOPMENT_CONFIGURATION_ADD_ANCHOR = "## Godot project settings"
DEVELOPMENT_CONFIGURATION_ADD_RENDERER = "development_input_table"
DEVELOPMENT_CONFIGURATION_INPUT_ROWS = (
    ("move_down", "S", 83),
    ("move_left", "A", 65),
    ("move_right", "D", 68),
    ("move_up", "W", 87),
)

CODE_INVENTORY_TARGET = "Knowledge/Code/Code Inventory.md"
CODE_INVENTORY_OLD_PASSAGE = (
    "No scripts or source-code files are present in the inspected `(Game Name)/` project tree. "
    "The project does contain `Main.tscn`, a scene resource with no attached script."
)
CODE_INVENTORY_REPLACEMENT = (
    "`Player.gd` extends `CharacterBody2D` and exports `speed` with a default value of `300.0`. "
    "It defines `_physics_process`. The Inspector detects calls to `Input.get_vector()` and "
    "`move_and_slide()` in the script."
)
CODE_INVENTORY_ANCHOR = ("# Code Inventory", "## Tracked code")

DATA_ASSETS_TARGET = "Knowledge/Data/Project Assets and Resources.md"
DATA_ASSETS_MAIN_OLD_PASSAGE = (
    "The project contains a single scene resource. It defines a root node named `Main` of type `Node2D`, "
    "with no child nodes or attached script. `project.godot` references it as the main scene."
)
DATA_ASSETS_MAIN_REPLACEMENT = (
    "`project.godot` selects `res://Main.tscn` as the main scene. `res://Main.tscn` has a `Main` `Node2D` "
    "root and instances `res://Player.tscn` as node `Player`. `res://Player.tscn` has a `Player` "
    "`CharacterBody2D` root."
)
DATA_ASSETS_MAIN_ANCHOR = ("# Project Assets and Resources", "## Scene resource", "### `Main.tscn`")
DATA_ASSETS_OTHER_OLD_PASSAGE = (
    "No scripts, custom resource files, data files, or other runtime assets are present in the inspected project tree."
)
DATA_ASSETS_OTHER_REPLACEMENT = (
    "`Player.tscn` attaches `Player.gd` to its `Player` root. Its `Sprite2D` node assigns `res://icon.svg` to `texture`."
)
DATA_ASSETS_OTHER_ANCHOR = ("# Project Assets and Resources", "## Other resources and data")


class ReplaceSchema:
    def __init__(self, target, commit, old_passage, anchor, replacement=None,
                 fact_specs=(), claims=(), validator_kind="template", variants=None):
        self.target = target
        self.commit = commit
        self.old_passage = old_passage
        self.anchor = anchor
        self.replacement = replacement
        self.fact_specs = fact_specs
        self.claims = claims
        self.validator_kind = validator_kind
        self.variants = variants or {}


class ApplierError(Exception):
    """Safe, user-facing refusal or filesystem/Git error."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str, check: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError as exc:
        raise ApplierError(f"Could not run Git: {exc}") from exc
    if check and result.returncode:
        raise ApplierError(result.stderr.strip() or f"Git command failed: {' '.join(args)}")
    return result.stdout.strip()


def repository_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    result = subprocess.run(
        ["git", "-C", str(script_dir), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode:
        raise ApplierError("The Knowledge Applier is not inside a Git repository.")
    return Path(result.stdout.strip()).resolve()


def load_updater(root: Path):
    sys.dont_write_bytecode = True
    path = root / ".ai/knowledge_updater.py"
    spec = importlib.util.spec_from_file_location("knowledge_updater_for_applier", path)
    if spec is None or spec.loader is None:
        raise ApplierError("Cannot load Knowledge Updater for deterministic fact validation.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def proposal_path(root: Path, argument: str) -> Path:
    candidate = Path(argument)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    proposals = (root / ".ai/proposals").resolve()
    if candidate.parent != proposals or not candidate.is_file():
        raise ApplierError("Proposal must be an existing file directly under .ai/proposals/.")
    return candidate


def proposal_metadata(text: str) -> tuple[str, str, str]:
    status_section = re.search(r"(?ms)^## Final Status\s*\n\s*`([^`]+)`", text)
    commit = re.search(r"(?m)^- Commit: `([0-9a-f]{40})`\s*$", text)
    inspector_fp = FINGERPRINT_RE.search(text)
    if not status_section or not commit or not inspector_fp:
        raise ApplierError("Proposal is missing its status, full commit SHA, or Inspector fingerprint metadata.")
    return status_section.group(1), commit.group(1), inspector_fp.group(1)


def parse_evidence(text: str, commit_sha: str) -> dict[str, list[tuple[str, str, str]]]:
    section_match = re.search(
        r"(?ms)^## Knowledge Files Affected\s*\n(.*?)(?=^## Application Plan|^## Knowledge Files Reviewed|^## Conflicts|^## Proposed Changes|\Z)", text
    )
    if not section_match:
        raise ApplierError("Proposal has no Knowledge Files Affected evidence section.")
    section = section_match.group(1)
    headings = list(TARGET_HEADING_RE.finditer(section))
    evidence: dict[str, list[tuple[str, str, str]]] = {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(section)
        body = section[heading.end():end]
        target = heading.group(1)
        records = [(match.group(1).strip(), match.group(2), match.group(4))
                   for match in EVIDENCE_RE.finditer(body)]
        for _statement, _source, evidence_sha, _state in EVIDENCE_RE.findall(body):
            if evidence_sha != commit_sha:
                raise ApplierError(f"Evidence for {target} refers to a different commit.")
        evidence[target] = records
    return evidence


def parse_application_plan(text: str) -> list[dict]:
    match = re.search(r"(?ms)^## Application Plan\s*\n(.*?)(?=^## |\Z)", text)
    if not match:
        raise ApplierError(
            "Proposal has no `## Application Plan`; safe application needs target baselines, section anchors, and selected verified facts."
        )
    body = match.group(1)
    headings = list(TARGET_HEADING_RE.finditer(body))
    if not headings:
        raise ApplierError("Application Plan has no Knowledge target entries.")
    plans = []
    seen = set()
    for index, heading in enumerate(headings):
        target = heading.group(1)
        if target in seen:
            raise ApplierError(f"Application Plan repeats target {target}.")
        seen.add(target)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        entry = body[heading.end():end]
        baseline = BASELINE_RE.search(entry)
        anchor = ANCHOR_RE.search(entry)
        context = CONTEXT_RE.search(entry)
        facts = [value.strip() for value in PLAN_FACT_RE.findall(entry)]
        if not baseline or not anchor or not context or not facts:
            raise ApplierError(f"Application Plan for {target} needs a base SHA-256, one insertion heading, context, and verified facts.")
        if len(facts) != len(set(facts)):
            raise ApplierError(f"Application Plan for {target} repeats a verified fact.")
        plan = {"path": target, "baseline_sha256": baseline.group(1),
                "anchor": anchor.group(1), "context": context.group(1), "facts": facts}
        renderer = re.search(r"(?m)^- Renderer: `([^`]+)`\s*$", entry)
        renderer_marker = re.search(r"(?m)^- Renderer data:\s*$", entry)
        operation = re.search(r"(?m)^- Operation: ([A-Z]+)\s*$", entry)
        if renderer or renderer_marker:
            if (not renderer or not renderer_marker or not operation
                    or operation.group(1) != "ADD"):
                raise ApplierError(f"Application Plan renderer metadata is incomplete or not an ADD for {target}.")
            data_block = re.search(
                r"(?ms)^- Renderer data:\s*\n\s*```json\s*\n(.*?)\n```\s*$", entry
            )
            if not data_block:
                raise ApplierError(f"Application Plan renderer data is malformed for {target}.")
            try:
                metadata = json.loads(data_block.group(1))
            except json.JSONDecodeError as exc:
                raise ApplierError(f"Application Plan renderer JSON is invalid for {target}: {exc}") from exc
            expected_metadata_keys = {
                "operation", "renderer", "rendered_markdown", "rendered_markdown_sha256",
                "selected_verified_facts", "claim_to_fact_mapping",
            }
            if (not isinstance(metadata, dict)
                    or set(metadata) != expected_metadata_keys
                    or metadata.get("operation") != "ADD"
                    or metadata.get("renderer") != renderer.group(1)):
                raise ApplierError(f"Application Plan renderer metadata does not match for {target}.")
            plan.update(metadata)
        plans.append(plan)
    return plans


def safe_knowledge_file(root: Path, relative: str) -> Path:
    posix = Path(relative)
    if not relative.startswith("Knowledge/") or posix.is_absolute() or ".." in posix.parts or posix.suffix.lower() != ".md":
        raise ApplierError(f"Unsafe Knowledge target: {relative}")
    path = (root / posix).resolve()
    knowledge_root = (root / "Knowledge").resolve()
    try:
        path.relative_to(knowledge_root)
    except ValueError as exc:
        raise ApplierError(f"Knowledge target resolves outside Knowledge/: {relative}") from exc
    if not path.is_file():
        raise ApplierError(f"Target Knowledge file does not exist: {relative}")
    return path


def validate_facts(root: Path, commit_sha: str, plans: list[dict], evidence: dict, inspector, updater) -> None:
    commit_facts, *_ = updater.project_facts(root, commit_sha, inspector)
    head_sha = git(root, "rev-parse", "HEAD")
    current_facts, _config, project_dir, _changed = updater.project_facts(root, head_sha, inspector)
    if project_dir:
        dirty = git(root, "status", "--porcelain", "--untracked-files=all", "--", project_dir)
        if dirty:
            raise ApplierError("Godot project working tree has uncommitted or untracked files; current project facts cannot be validated safely.")
    commit_index = {(fact["statement"], fact["source"]): fact for fact in commit_facts}
    current_index = {(fact["statement"], fact["source"]) for fact in current_facts}
    evidence_index: dict[str, set[tuple[str, str]]] = {}
    for target, records in evidence.items():
        for statement, source, state in records:
            key = (statement, source)
            if key not in commit_index:
                raise ApplierError(f"Proposal evidence is not present in commit {commit_sha}: {statement}")
            if state not in {"missing from", "already documented in"}:
                raise ApplierError(f"Proposal has an unknown Knowledge evidence state for {target}: {statement}")
            if state == "missing from":
                evidence_index.setdefault(target, set()).add(key)
    for plan in plans:
        target = plan["path"]
        if "renderer" in plan:
            validate_development_configuration_table_plan(
                plan, commit_sha, commit_facts, current_facts, evidence, updater
            )
            continue
        known = evidence_index.get(target, set())
        for statement in plan["facts"]:
            matches = [key for key in known if key[0] == statement]
            if len(matches) != 1:
                raise ApplierError(f"Planned fact is not uniquely marked as missing in proposal evidence for {target}: {statement}")
            key = matches[0]
            fact = commit_index[key]
            if fact["category"] != "change" and key not in current_index:
                raise ApplierError(f"Project fact no longer matches current HEAD: {statement}")


def development_configuration_table(facts: list[dict], updater) -> dict:
    """Derive the only supported input-action table from exact Inspector facts."""
    selected = []
    rows = []
    claims = []
    lines = ["| Action | Configured physical key |", "|---|---|"]
    for action, key, code in DEVELOPMENT_CONFIGURATION_INPUT_ROWS:
        expected_statement = f"Input action `{action}` is configured with `physical key {key} ({code})`."
        matches = [fact for fact in facts
                   if fact.get("category") == "input_action"
                   and fact.get("source") == "(Game Name)/project.godot"
                   and fact.get("statement") == expected_statement]
        if len(matches) != 1:
            raise ApplierError(
                f"Development Configuration table requires exactly one verified `{action}` physical-key fact; found {len(matches)}."
            )
        fact = matches[0]
        fact_id = updater.documentation_fact_id(fact)
        selected.append({"id": fact_id, "category": fact["category"],
                         "source": fact["source"], "statement": fact["statement"]})
        row = f"| {action} | physical key {key} ({code}) |"
        lines.append(row)
        rows.append({"action": action, "claim": row, "verified_fact_ids": [fact_id]})
        claims.append({"action": action, "text": row, "verified_fact_ids": [fact_id]})
    return {"markdown": "\n".join(lines), "selected_verified_facts": selected,
            "rows": rows, "claim_to_fact_mapping": claims}


def validate_development_configuration_table_plan(plan: dict, commit_sha: str,
                                                  commit_facts: list[dict], current_facts: list[dict],
                                                  evidence: dict, updater) -> None:
    target = DEVELOPMENT_CONFIGURATION_ADD_TARGET
    if plan.get("path") != target:
        raise ApplierError(f"The {DEVELOPMENT_CONFIGURATION_ADD_RENDERER} renderer only supports {target}.")
    if (plan.get("operation") != "ADD"
            or plan.get("renderer") != DEVELOPMENT_CONFIGURATION_ADD_RENDERER
            or plan.get("anchor") != DEVELOPMENT_CONFIGURATION_ADD_ANCHOR):
        raise ApplierError("Development Configuration table plan has an unsupported operation, renderer, or anchor.")
    expected = development_configuration_table(commit_facts, updater)
    if plan.get("rendered_markdown") != expected["markdown"]:
        raise ApplierError("Development Configuration table contains altered or unsupported claims.")
    if plan.get("rendered_markdown_sha256") != sha256(expected["markdown"].encode("utf-8")):
        raise ApplierError("Development Configuration table Markdown hash is incorrect.")
    if plan.get("selected_verified_facts") != expected["selected_verified_facts"]:
        raise ApplierError("Development Configuration table selected facts do not match independently verified facts.")
    if plan.get("facts") != [fact["statement"] for fact in expected["selected_verified_facts"]]:
        raise ApplierError("Development Configuration plan facts do not exactly match its selected table facts.")
    if plan.get("claim_to_fact_mapping") != expected["claim_to_fact_mapping"]:
        raise ApplierError("Development Configuration table row-to-fact mapping is invalid.")

    evidence_rows = evidence.get(target, [])
    for selected in expected["selected_verified_facts"]:
        matching = [item for item in evidence_rows
                    if item[0] == selected["statement"] and item[1] == selected["source"]]
        if len(matching) != 1:
            raise ApplierError(f"Development Configuration row lacks unique proposal evidence: {selected['statement']}")
    current_index = {(fact.get("category"), fact.get("source"), fact.get("statement"))
                     for fact in current_facts}
    for selected in expected["selected_verified_facts"]:
        material = (selected["category"], selected["source"], selected["statement"])
        if material not in current_index:
            raise ApplierError(f"Input-action fact no longer matches current HEAD: {selected['statement']}")


def parse_replace_operations(text: str, updater) -> list[dict]:
    """Parse explicit REPLACE blocks; malformed REPLACE must never fall through to ADD."""
    section = re.search(r"(?ms)^## Application Plan\s*\n(.*?)(?=^## |\Z)", text)
    if not section:
        return []
    headings = list(REPLACE_HEADING_RE.finditer(section.group(1)))
    if not headings:
        if re.search(r'(?m)^\s*"operation"\s*:\s*"REPLACE"\s*,?\s*$', section.group(1)):
            raise ApplierError("Application Plan contains REPLACE execution data without an explicit REPLACE operation heading.")
        return []
    try:
        operations = updater.parse_replacement_operations(text)
    except Exception as exc:
        raise ApplierError(f"Malformed REPLACE operation: {exc}") from exc
    if len(operations) != len(headings):
        raise ApplierError("Application Plan contains a malformed or unrecognized REPLACE operation block.")
    return operations


def replacement_schema_registry(updater) -> dict[str, ReplaceSchema]:
    """Explicit allowlist and evidence contract for each supported REPLACE target."""
    commit = updater.SYSTEMS_REPLACEMENT_COMMIT
    return {
        updater.SYSTEMS_REPLACEMENT_TARGET: ReplaceSchema(
            target=updater.SYSTEMS_REPLACEMENT_TARGET, commit=commit,
            old_passage=updater.SYSTEMS_REPLACEMENT_OLD_PASSAGE,
            anchor=tuple(updater.SYSTEMS_REPLACEMENT_ANCHOR), validator_kind="systems",
        ),
        PROJECT_OVERVIEW_TARGET: ReplaceSchema(
            target=PROJECT_OVERVIEW_TARGET, commit=commit,
            old_passage=PROJECT_OVERVIEW_OLD_PASSAGE,
            anchor=PROJECT_OVERVIEW_ANCHOR,
            replacement=PROJECT_OVERVIEW_REPLACEMENT,
            fact_specs=(
                ("main_scene", "project_setting", "(Game Name)/project.godot",
                 '`project.godot` sets `application/run/main_scene` to `"res://Main.tscn"`.'),
                ("main_root", "scene_root", "(Game Name)/Main.tscn",
                 "`res://Main.tscn` has root `Main` of type `Node2D`."),
                ("main_player_instance", "scene_instance", "(Game Name)/Main.tscn",
                 "`res://Main.tscn` instances `res://Player.tscn` as node `Player`."),
            ),
            claims=(
                ("main_scene", "`Main.tscn` remains configured as the main scene.", ("main_scene",)),
                ("main_scene_shape", "It has a `Main` `Node2D` root and instances `Player.tscn` as node `Player`.",
                 ("main_root", "main_player_instance")),
            ),
        ),
        ARCHITECTURE_OVERVIEW_TARGET: ReplaceSchema(
            target=ARCHITECTURE_OVERVIEW_TARGET, commit=commit,
            old_passage=ARCHITECTURE_OVERVIEW_OLD_PASSAGE,
            anchor=ARCHITECTURE_OVERVIEW_ANCHOR,
            replacement=ARCHITECTURE_OVERVIEW_REPLACEMENT,
            fact_specs=(
                ("main_scene", "project_setting", "(Game Name)/project.godot",
                 '`project.godot` sets `application/run/main_scene` to `"res://Main.tscn"`.'),
                ("main_root", "scene_root", "(Game Name)/Main.tscn",
                 "`res://Main.tscn` has root `Main` of type `Node2D`."),
                ("main_player_instance", "scene_instance", "(Game Name)/Main.tscn",
                 "`res://Main.tscn` instances `res://Player.tscn` as node `Player`."),
                ("player_root", "scene_root", "(Game Name)/Player.tscn",
                 "`res://Player.tscn` has root `Player` of type `CharacterBody2D`."),
                ("player_script_attachment", "scene_script", "(Game Name)/Player.tscn",
                 "Node `Player` in `res://Player.tscn` attaches `res://Player.gd`."),
            ),
            claims=(
                ("main_entry_shape", "`Main.tscn` remains configured as the main scene and has a `Main` `Node2D` root.",
                 ("main_scene", "main_root")),
                ("player_scene_composition",
                 "It instances `Player.tscn` as node `Player`; `Player.tscn` has a `Player` `CharacterBody2D` root with `Player.gd` attached to node `Player`.",
                 ("main_player_instance", "player_root", "player_script_attachment")),
            ),
        ),
        CODE_INVENTORY_TARGET: ReplaceSchema(
            target=CODE_INVENTORY_TARGET, commit=commit,
            old_passage=CODE_INVENTORY_OLD_PASSAGE,
            anchor=CODE_INVENTORY_ANCHOR,
            replacement=CODE_INVENTORY_REPLACEMENT,
            fact_specs=(
                ("player_script", "script_file", "(Game Name)/Player.gd",
                 "Script `res://Player.gd` exists."),
                ("player_inheritance", "script_inheritance", "(Game Name)/Player.gd",
                 "`res://Player.gd` extends `CharacterBody2D`."),
                ("player_speed", "script_export", "(Game Name)/Player.gd",
                 "`res://Player.gd` exports `speed` using `@export var speed: float = 300.0`."),
                ("player_physics_function", "script_function", "(Game Name)/Player.gd",
                 "`res://Player.gd` defines `func _physics_process(_delta: float) -> void:`."),
                ("player_input_call", "script_call", "(Game Name)/Player.gd",
                 "`res://Player.gd` calls `Input.get_vector()`."),
                ("player_slide_call", "script_call", "(Game Name)/Player.gd",
                 "`res://Player.gd` calls `move_and_slide()`."),
            ),
            claims=(
                ("script_inheritance_and_speed",
                 "`Player.gd` extends `CharacterBody2D` and exports `speed` with a default value of `300.0`.",
                 ("player_script", "player_inheritance", "player_speed")),
                ("physics_function", "It defines `_physics_process`.", ("player_physics_function",)),
                ("detected_script_calls",
                 "The Inspector detects calls to `Input.get_vector()` and `move_and_slide()` in the script.",
                 ("player_input_call", "player_slide_call")),
            ),
        ),
        DATA_ASSETS_TARGET: ReplaceSchema(
            target=DATA_ASSETS_TARGET, commit=commit,
            old_passage=DATA_ASSETS_MAIN_OLD_PASSAGE,
            anchor=DATA_ASSETS_MAIN_ANCHOR,
            replacement=DATA_ASSETS_MAIN_REPLACEMENT,
            fact_specs=(
                ("main_scene", "project_setting", "(Game Name)/project.godot",
                 '`project.godot` sets `application/run/main_scene` to `"res://Main.tscn"`.'),
                ("main_root", "scene_root", "(Game Name)/Main.tscn",
                 "`res://Main.tscn` has root `Main` of type `Node2D`."),
                ("main_player_instance", "scene_instance", "(Game Name)/Main.tscn",
                 "`res://Main.tscn` instances `res://Player.tscn` as node `Player`."),
                ("player_root", "scene_root", "(Game Name)/Player.tscn",
                 "`res://Player.tscn` has root `Player` of type `CharacterBody2D`."),
            ),
            claims=(
                ("main_scene_configuration", "`project.godot` selects `res://Main.tscn` as the main scene.", ("main_scene",)),
                ("main_scene_composition", "`res://Main.tscn` has a `Main` `Node2D` root and instances `res://Player.tscn` as node `Player`.", ("main_root", "main_player_instance")),
                ("player_scene_root", "`res://Player.tscn` has a `Player` `CharacterBody2D` root.", ("player_root",)),
            ),
            variants={
                DATA_ASSETS_OTHER_OLD_PASSAGE: {
                    "anchor": DATA_ASSETS_OTHER_ANCHOR,
                    "replacement": DATA_ASSETS_OTHER_REPLACEMENT,
                    "fact_specs": (
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
            },
        ),
    }


def resolve_replace_schema_variant(schema: ReplaceSchema, old_passage: str) -> ReplaceSchema:
    if old_passage == schema.old_passage:
        return schema
    variant = schema.variants.get(old_passage)
    if not variant:
        raise ApplierError(f"REPLACE old passage is not supported for target {schema.target}.")
    return ReplaceSchema(
        target=schema.target, commit=schema.commit, old_passage=old_passage,
        anchor=variant["anchor"], replacement=variant["replacement"],
        fact_specs=variant["fact_specs"], claims=variant["claims"],
    )


def schema_expected_draft(schema: ReplaceSchema, facts: list[dict], updater) -> dict:
    selected: dict[str, dict] = {}
    for key, category, source, statement in schema.fact_specs:
        matches = [fact for fact in facts
                   if fact.get("category") == category and fact.get("source") == source
                   and fact.get("statement") == statement]
        if len(matches) != 1:
            raise ApplierError(
                f"REPLACE target {schema.target} requires exactly one verified `{key}` fact; found {len(matches)}."
            )
        selected[key] = matches[0]
    selected_facts = [
        {"id": updater.documentation_fact_id(fact), "category": fact["category"],
         "source": fact["source"], "statement": fact["statement"]}
        for key, _category, _source, _statement in schema.fact_specs
        for fact in [selected[key]]
    ]
    claims = [
        {"key": key, "text": text,
         "verified_fact_ids": [updater.documentation_fact_id(selected[fact_key]) for fact_key in fact_keys]}
        for key, text, fact_keys in schema.claims
    ]
    if schema.replacement is None:
        raise ApplierError(f"REPLACE target has no deterministic replacement template: {schema.target}")
    return {"paragraph": schema.replacement, "supporting_facts": selected_facts, "claims": claims}


def replacement_payload_facts(operation: dict, commit_sha: str, commit_facts: list[dict],
                               current_facts: list[dict], updater) -> None:
    """Dispatch to one allowlisted target schema and recheck its verified claims."""
    required = {
        "operation", "operation_id", "commit_sha", "path", "file_baseline_sha256",
        "exact_old_passage", "old_passage_sha256", "anchor", "replacement_markdown",
        "replacement_sha256", "selected_verified_facts", "claim_to_fact_mapping",
    }
    missing = sorted(required - operation.keys())
    if missing:
        raise ApplierError("REPLACE operation is missing required fields: " + ", ".join(missing))
    if operation.get("operation") != "REPLACE":
        raise ApplierError("Application operation is not explicitly REPLACE.")
    if operation.get("commit_sha") != commit_sha:
        raise ApplierError("REPLACE operation commit does not match proposal commit.")
    expected_path = operation.get("path")
    schemas = replacement_schema_registry(updater)
    schema = schemas.get(expected_path)
    if schema is None:
        raise ApplierError(f"Unsupported REPLACE target: {expected_path}")
    schema = resolve_replace_schema_variant(schema, operation.get("exact_old_passage", ""))
    for field in ("file_baseline_sha256", "old_passage_sha256", "replacement_sha256"):
        if not isinstance(operation.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", operation[field]):
            raise ApplierError(f"REPLACE operation has an invalid {field}.")
    if not isinstance(operation.get("operation_id"), str) or not re.fullmatch(r"[0-9a-f]{16}", operation["operation_id"]):
        raise ApplierError("REPLACE operation has an invalid operation ID.")

    passage = operation.get("exact_old_passage")
    replacement = operation.get("replacement_markdown")
    if not isinstance(passage, str) or not passage or not isinstance(replacement, str) or not replacement:
        raise ApplierError("REPLACE operation has an empty or invalid passage.")
    if sha256(passage.encode("utf-8")) != operation["old_passage_sha256"]:
        raise ApplierError("REPLACE old-passage SHA-256 is incorrect.")
    if sha256(replacement.encode("utf-8")) != operation["replacement_sha256"]:
        raise ApplierError("REPLACE replacement SHA-256 is incorrect.")
    expected_anchor = {"heading_path": list(schema.anchor), "occurrence": 1}
    if operation.get("anchor") != expected_anchor:
        raise ApplierError("REPLACE anchor/location is invalid or ambiguous.")

    if commit_sha != schema.commit or passage != schema.old_passage:
        raise ApplierError("REPLACE operation does not match the explicitly supported stale passage.")
    if schema.validator_kind == "systems":
        draft = updater.render_systems_documentation_draft(expected_path, commit_facts)
    else:
        draft = schema_expected_draft(schema, commit_facts, updater)
    if replacement != draft["paragraph"]:
        raise ApplierError("REPLACE text contains unsupported or unselected claims.")
    if operation.get("selected_verified_facts") != draft["supporting_facts"]:
        raise ApplierError("REPLACE selected facts do not match facts independently verified from the commit.")
    if operation.get("claim_to_fact_mapping") != draft["claims"]:
        raise ApplierError("REPLACE claim-to-fact mapping is invalid or unsupported.")
    expected_id = sha256("\0".join((commit_sha, expected_path, operation["old_passage_sha256"],
                                      operation["replacement_sha256"])).encode("utf-8"))[:16]
    if operation["operation_id"] != expected_id:
        raise ApplierError("REPLACE operation ID does not match its target and content hashes.")

    current_index = {(fact.get("category"), fact.get("source"), fact.get("statement")) for fact in current_facts}
    for selected in operation["selected_verified_facts"]:
        if not isinstance(selected, dict):
            raise ApplierError("REPLACE selected fact is malformed.")
        material = (selected.get("category"), selected.get("source"), selected.get("statement"))
        if material not in {(fact.get("category"), fact.get("source"), fact.get("statement")) for fact in commit_facts}:
            raise ApplierError("REPLACE selected fact is not present in the proposal commit.")
        if selected.get("category") != "change" and material not in current_index:
            raise ApplierError(f"REPLACE selected project fact no longer matches current HEAD: {selected.get('statement')}")


def validate_replace_target(operation: dict, file_bytes: bytes, commit_facts: list[dict],
                            commit_sha: str, updater) -> None:
    """Validate file baseline, passage uniqueness, and anchor using the target's schema."""
    schema = replacement_schema_registry(updater).get(operation.get("path"))
    if schema is None:
        raise ApplierError(f"Unsupported REPLACE target: {operation.get('path')}")
    schema = resolve_replace_schema_variant(schema, operation.get("exact_old_passage", ""))
    if schema.validator_kind == "systems":
        # Preserve the established Systems replacement validator unchanged.
        updater.validate_systems_replacement_operation(operation, file_bytes, commit_facts, commit_sha)
        return
    if sha256(file_bytes) != operation["file_baseline_sha256"]:
        raise ApplierError("REPLACE target file does not match its whole-file baseline SHA-256.")
    try:
        content = file_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApplierError("REPLACE target file is not valid UTF-8.") from exc
    passage = schema.old_passage
    if content.count(passage) != 1:
        raise ApplierError("Exact old passage must occur exactly once; refusing to replace.")
    try:
        scoped_count = updater._anchored_passage_count(content, schema.anchor, passage)
    except Exception as exc:
        raise ApplierError(f"REPLACE anchor is missing, ambiguous, or invalid: {exc}") from exc
    if scoped_count != 1:
        raise ApplierError("REPLACE old passage is absent or non-unique at its exact anchor.")
    if operation["exact_old_passage"] != schema.old_passage:
        raise ApplierError("REPLACE old passage does not match the target-specific stale passage.")
    # Recheck target-specific claims at the final file-validation boundary too.
    draft = schema_expected_draft(schema, commit_facts, updater)
    if (operation["replacement_markdown"] != draft["paragraph"]
            or operation["selected_verified_facts"] != draft["supporting_facts"]
            or operation["claim_to_fact_mapping"] != draft["claims"]):
        raise ApplierError("REPLACE claims or selected facts failed target-specific validation.")


def validate_replace_facts(root: Path, commit_sha: str, operation: dict, inspector, updater):
    commit_facts, *_ = updater.project_facts(root, commit_sha, inspector)
    head_sha = git(root, "rev-parse", "HEAD")
    current_facts, _config, project_dir, _changed = updater.project_facts(root, head_sha, inspector)
    if project_dir:
        dirty = git(root, "status", "--porcelain", "--untracked-files=all", "--", project_dir)
        if dirty:
            raise ApplierError("Godot project working tree has uncommitted or untracked files; current project facts cannot be validated safely.")
    replacement_payload_facts(operation, commit_sha, commit_facts, current_facts, updater)
    return commit_facts, current_facts


def replacement_present_at_anchor(text: str, operation: dict, updater) -> bool:
    anchor = operation["anchor"]["heading_path"]
    return (text.count(operation["replacement_markdown"]) == 1
            and updater._anchored_passage_count(text, tuple(anchor), operation["replacement_markdown"]) == 1)


def load_application_state(state_path: Path) -> dict:
    state = {"schema_version": 1, "applications": {}}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApplierError(f"Cannot read application state: {exc}") from exc
        if not isinstance(state, dict) or not isinstance(state.get("applications"), dict):
            raise ApplierError("Application state must contain an applications object.")
    return state


def apply_replace_proposal(root: Path, proposal: Path, proposal_digest: str, commit_sha: str,
                           actual_fp: str, operations: list[dict], dry_run: bool,
                           inspector, updater) -> dict:
    if len(operations) != 1:
        raise ApplierError("This Applier version executes exactly one REPLACE operation per proposal.")
    # A mixed plan would make the operation boundary unclear; ADD plans continue through their unchanged path.
    section = re.search(r"(?ms)^## Application Plan\s*\n(.*?)(?=^## |\Z)", proposal.read_text(encoding="utf-8"))
    if section and TARGET_HEADING_RE.search(section.group(1)):
        raise ApplierError("A proposal cannot mix REPLACE and ADD operations in this Applier version.")
    operation = operations[0]
    if operation.get("operation") != "REPLACE":
        raise ApplierError("Application operation is not explicitly REPLACE.")
    if operation.get("commit_sha") != commit_sha:
        raise ApplierError("REPLACE operation commit does not match proposal commit.")
    # Verify commit and current facts before examining application state or changing anything.
    commit_facts, current_facts = validate_replace_facts(root, commit_sha, operation, inspector, updater)
    relative = operation.get("path")
    target = safe_knowledge_file(root, relative)
    state_path = root / ".ai/state/knowledge_applications.json"
    record_path = root / ".ai/applications" / (proposal.stem + "-application.json")
    state = load_application_state(state_path)
    current = target.read_bytes()
    prior = state["applications"].get(proposal_digest)

    if prior or record_path.exists():
        record = prior
        if record_path.exists():
            try:
                disk_record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ApplierError(f"Cannot validate existing application record: {exc}") from exc
            if record is not None and record != disk_record:
                raise ApplierError("Application state and application record disagree; refusing to reconcile.")
            record = disk_record
        elif prior is not None:
            raise ApplierError("Replacement state exists without its application record; refusing to assume prior application.")
        if not isinstance(record, dict):
            raise ApplierError("Application record exists without matching replacement state; refusing to guess.")
        if (record.get("proposal_sha256") != proposal_digest
                or record.get("operation_type") != "REPLACE"
                or record.get("operation_id") != operation.get("operation_id")
                or record.get("commit_sha") != commit_sha
                or record.get("target_path") != relative
                or record.get("status") not in {"applied", "reconciled"}):
            raise ApplierError("Existing application state does not prove this exact REPLACE operation was applied.")
        record_changes = record.get("changes")
        change = record_changes[0] if isinstance(record_changes, list) and len(record_changes) == 1 and isinstance(record_changes[0], dict) else {}
        if (change.get("operation_type") != "REPLACE"
                or change.get("before_sha256") != operation["file_baseline_sha256"]
                or change.get("old_passage_sha256") != operation["old_passage_sha256"]
                or change.get("replacement_sha256") != operation["replacement_sha256"]
                or sha256(current) != change.get("after_sha256")):
            raise ApplierError("Current Knowledge file does not match the recorded result of this REPLACE operation.")
        try:
            current_text = current.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ApplierError("Replacement target is no longer valid UTF-8.") from exc
        if not replacement_present_at_anchor(current_text, operation, updater):
            raise ApplierError("Recorded replacement is not uniquely present at its intended anchor.")
        if not state.get("applications", {}).get(proposal_digest):
            if dry_run:
                return result("ALREADY_APPLIED", proposal, commit_sha, changes=record["changes"], writes=False,
                              state=state_path.relative_to(root).as_posix(),
                              application_record=record_path.relative_to(root).as_posix())
            state["applications"][proposal_digest] = record
            atomic_json(state_path, state)
        return result("ALREADY_APPLIED", proposal, commit_sha, changes=record["changes"], writes=False,
                      state=state_path.relative_to(root).as_posix(),
                      application_record=record_path.relative_to(root).as_posix())

    original = current
    if sha256(original) != operation["file_baseline_sha256"]:
        raise ApplierError("Knowledge target changed since the REPLACE proposal baseline.")
    try:
        original_text = original.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApplierError("Replacement target is not valid UTF-8.") from exc
    try:
        validate_replace_target(operation, original, commit_facts, commit_sha, updater)
    except Exception as exc:
        raise ApplierError(f"REPLACE precondition failed: {exc}") from exc
    if original_text.count(operation["exact_old_passage"]) != 1:
        raise ApplierError("Exact old passage must occur exactly once; refusing to replace.")
    updated_text = original_text.replace(operation["exact_old_passage"], operation["replacement_markdown"], 1)
    updated = updated_text.encode("utf-8")
    changes = [{
        "operation_type": "REPLACE", "operation_id": operation["operation_id"],
        "path": relative, "status": "planned", "before_sha256": sha256(original),
        "after_sha256": sha256(updated), "old_passage_sha256": operation["old_passage_sha256"],
        "replacement_sha256": operation["replacement_sha256"],
        "anchor": operation["anchor"],
        "reason": "Replaced only the exact uniquely anchored passage with the renderer-validated Markdown.",
    }]
    if dry_run:
        return result("READY", proposal, commit_sha, inspector_fingerprint=actual_fp,
                      changes=changes, writes=False)

    if record_path.exists():
        raise ApplierError(f"Application record already exists; refusing to overwrite: {record_path.relative_to(root).as_posix()}")
    recorded_changes = [dict(change, status="applied") for change in changes]
    application = {
        "proposal": proposal.relative_to(root).as_posix(), "proposal_sha256": proposal_digest,
        "commit_sha": commit_sha, "inspector_fingerprint": actual_fp, "operation_type": "REPLACE",
        "operation_id": operation["operation_id"], "target_path": relative,
        "status": "applied", "changes": recorded_changes,
    }
    if sha256(target.read_bytes()) != sha256(original):
        raise ApplierError("Knowledge target changed during REPLACE preparation; refusing to overwrite.")
    written = False
    try:
        atomic_replace(target, updated)
        written = True
        publish_record_no_clobber(record_path, application)
    except Exception:
        if written:
            try:
                if sha256(target.read_bytes()) == sha256(updated):
                    atomic_replace(target, original)
            except (OSError, ApplierError):
                pass
        raise
    state["schema_version"] = 1
    state["applications"][proposal_digest] = application
    try:
        atomic_json(state_path, state)
    except Exception as exc:
        raise ApplierError("Knowledge was updated and the application record was saved, but state finalization failed; rerun this proposal to reconcile state. {}".format(exc)) from exc
    return result("APPLIED", proposal, commit_sha, inspector_fingerprint=actual_fp,
                  changes=recorded_changes, state=state_path.relative_to(root).as_posix(),
                  application_record=record_path.relative_to(root).as_posix(), writes=True)


def marker_for(proposal_digest: str) -> tuple[str, str]:
    token = f"knowledge-applier:{proposal_digest}"
    return f"<!-- {token} -->", f"<!-- /{token} -->"


def section_insert(text: str, anchor: str, addition: str, begin: str, end: str) -> str:
    lines = text.splitlines(keepends=True)
    headings = [(index, len(match.group(1))) for index, line in enumerate(lines)
                if (match := re.match(r"^(#{1,6})\s+.*?\s*\r?\n?$", line)) and line.strip() == anchor]
    if len(headings) != 1:
        raise ApplierError(f"Insertion heading must occur exactly once: {anchor}")
    start, level = headings[0]
    stop = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[index])
        if match and len(match.group(1)) <= level:
            stop = index
            break
    newline = "\r\n" if "\r\n" in text else "\n"
    nested_level = min(level + 1, 6)
    block = [begin, "#" * nested_level + f" Verified additions from commit `{addition['commit']}`"]
    block.extend(f"- {fact}" for fact in addition["facts"])
    block.append(end)
    rendered = newline.join(block)
    prefix = "" if stop == 0 or not lines[stop - 1].strip() else newline
    suffix = newline if stop < len(lines) else ("" if text.endswith(("\n", "\r")) else newline)
    lines[stop:stop] = [prefix + rendered + suffix]
    return "".join(lines)


def section_insert_rendered_markdown(text: str, anchor: str, markdown: str,
                                    begin: str, end: str) -> str:
    """Insert an exact allowlisted renderer output under one unique heading."""
    lines = text.splitlines(keepends=True)
    headings = [(index, len(match.group(1))) for index, line in enumerate(lines)
                if (match := re.match(r"^(#{1,6})\s+.*?\s*\r?\n?$", line)) and line.strip() == anchor]
    if len(headings) != 1:
        raise ApplierError(f"Insertion heading must occur exactly once: {anchor}")
    start, level = headings[0]
    stop = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[index])
        if match and len(match.group(1)) <= level:
            stop = index
            break
    newline = "\r\n" if "\r\n" in text else "\n"
    block = [begin, *markdown.split("\n"), end]
    rendered = newline.join(block)
    prefix = "" if stop == 0 or not lines[stop - 1].strip() else newline
    suffix = newline if stop < len(lines) else ("" if text.endswith(("\n", "\r")) else newline)
    lines[stop:stop] = [prefix + rendered + suffix]
    return "".join(lines)


def prepare_targets(root: Path, plans: list[dict], proposal_digest: str, commit_sha: str) -> list[dict]:
    begin, end = marker_for(proposal_digest)
    prepared = []
    for plan in plans:
        path = safe_knowledge_file(root, plan["path"])
        original = path.read_bytes()
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ApplierError(f"Target is not UTF-8; refusing to rewrite: {plan['path']}") from exc
        if begin in text or end in text:
            if text.count(begin) != 1 or text.count(end) != 1:
                raise ApplierError(f"Application marker is incomplete or duplicated in {plan['path']}.")
            marker_start, marker_end = text.index(begin), text.index(end) + len(end)
            block = text[marker_start:marker_end]
            newline = "\r\n" if "\r\n" in text else "\n"
            level_match = re.match(r"^(#{1,6})\s+", plan["anchor"])
            if not level_match:
                raise ApplierError(f"Invalid Markdown heading anchor for {plan['path']}.")
            nested_level = min(len(level_match.group(1)) + 1, 6)
            expected_block = newline.join(
                ([begin, *plan["rendered_markdown"].splitlines(), end]
                 if plan.get("renderer") == DEVELOPMENT_CONFIGURATION_ADD_RENDERER else
                 [begin, "#" * nested_level + f" Verified additions from commit `{commit_sha}`"]
                 + [f"- {fact}" for fact in plan["facts"]] + [end])
            )
            if block != expected_block:
                raise ApplierError(f"Existing application marker does not match this proposal in {plan['path']}.")
            prepared.append({"path": plan["path"], "file": path, "before": original,
                             "after": original, "status": "already_applied", "facts": plan["facts"]})
            continue
        current_hash = sha256(original)
        if current_hash != plan["baseline_sha256"]:
            raise ApplierError(
                f"Knowledge target changed since proposal baseline ({plan['path']}); expected {plan['baseline_sha256']}, found {current_hash}."
            )
        if plan.get("renderer") == DEVELOPMENT_CONFIGURATION_ADD_RENDERER:
            updated_text = section_insert_rendered_markdown(
                text, plan["anchor"], plan["rendered_markdown"], begin, end
            )
        else:
            addition = {"commit": commit_sha, "facts": plan["facts"]}
            updated_text = section_insert(text, plan["anchor"], addition, begin, end)
        updated = updated_text.encode("utf-8")
        prepared.append({"path": plan["path"], "file": path, "before": original,
                         "after": updated, "status": "planned", "facts": plan["facts"]})
    return prepared


def atomic_replace(path: Path, content: bytes) -> None:
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".knowledge-apply-", suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except OSError as exc:
        raise ApplierError(f"Could not atomically update {path}: {exc}") from exc
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_replace(path, data)


def publish_record_no_clobber(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=".application-record-", suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp_name, path)
    except FileExistsError as exc:
        raise ApplierError(f"Application record already exists; refusing to overwrite: {path}") from exc
    except OSError as exc:
        raise ApplierError(f"Could not publish application record {path}: {exc}") from exc
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.unlink(temp_name)
            except OSError:
                pass


def result(status: str, proposal: Path, commit: str | None = None, **extra) -> dict:
    payload = {"status": status, "proposal": proposal.as_posix()}
    if commit:
        payload["commit"] = commit
    payload.update(extra)
    return payload


def apply_proposal(root: Path, proposal: Path, *, dry_run: bool = False) -> dict:
    proposal_bytes = proposal.read_bytes()
    proposal_text = proposal_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    proposal_digest = sha256(proposal_bytes)
    status, commit_sha, expected_fp = proposal_metadata(proposal_text)
    if status != "UPDATE_PROPOSED":
        raise ApplierError(f"Proposal status is {status}; only UPDATE_PROPOSED proposals can be applied.")
    if not SHA_RE.fullmatch(commit_sha):
        raise ApplierError("Proposal commit must be a full 40-character SHA.")
    resolved = git(root, "rev-parse", "--verify", f"{commit_sha}^{{commit}}")
    if resolved != commit_sha:
        raise ApplierError("Proposal commit did not resolve to its full SHA.")
    updater = load_updater(root)
    inspector = updater.load_inspector(root)
    actual_fp = inspector.implementation_fingerprint()
    if expected_fp != actual_fp:
        raise ApplierError(f"Proposal Inspector fingerprint is stale: expected {expected_fp}, current {actual_fp}.")

    evidence = parse_evidence(proposal_text, commit_sha)
    replacements = parse_replace_operations(proposal_text, updater)
    if replacements:
        return apply_replace_proposal(root, proposal, proposal_digest, commit_sha, actual_fp,
                                      replacements, dry_run, inspector, updater)
    plans = parse_application_plan(proposal_text)
    planned_targets = {item["path"] for item in plans}
    if not planned_targets.issubset(set(evidence)):
        raise ApplierError("Application Plan targets must be present in the proposal's affected Knowledge files.")
    validate_facts(root, commit_sha, plans, evidence, inspector, updater)
    prepared = prepare_targets(root, plans, proposal_digest, commit_sha)

    changes = [{"path": item["path"], "status": item["status"],
                "facts": item["facts"], "before_sha256": sha256(item["before"]),
                "after_sha256": sha256(item["after"]),
                "reason": "Added selected verified facts marked missing in the proposal under the specified existing heading; existing Knowledge prose was preserved."}
               for item in prepared]
    all_already_applied = all(item["status"] == "already_applied" for item in prepared)
    if all_already_applied and dry_run:
        return result("ALREADY_APPLIED", proposal, commit_sha, changes=changes, writes=False)
    if dry_run:
        return result("READY", proposal, commit_sha, inspector_fingerprint=actual_fp,
                      changes=changes, writes=False)

    state_path = root / ".ai/state/knowledge_applications.json"
    record_dir = root / ".ai/applications"
    record_path = record_dir / (proposal.stem + "-application.json")
    state = {"schema_version": 1, "applications": {}}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApplierError(f"Cannot read application state: {exc}") from exc
        if not isinstance(state, dict) or not isinstance(state.get("applications"), dict):
            raise ApplierError("Application state must contain an applications object.")
    prior = state["applications"].get(proposal_digest)
    if prior:
        if isinstance(prior, dict) and prior.get("status") in {"applied", "reconciled"} and all_already_applied:
            return result("ALREADY_APPLIED", proposal, commit_sha, changes=changes, writes=False)
        raise ApplierError("Application state already contains this proposal but does not match the Knowledge files; refusing to overwrite.")
    if record_path.exists():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ApplierError(f"Cannot validate existing application record: {exc}") from exc
        if (isinstance(record, dict) and record.get("proposal_sha256") == proposal_digest
                and record.get("status") in {"applied", "reconciled"} and all_already_applied):
            state["applications"][proposal_digest] = record
            atomic_json(state_path, state)
            return result("ALREADY_APPLIED", proposal, commit_sha, changes=changes, writes=False,
                          state=state_path.relative_to(root).as_posix(),
                          application_record=record_path.relative_to(root).as_posix())
        raise ApplierError(f"Application record already exists and does not match current Knowledge; refusing to overwrite: {record_path.relative_to(root).as_posix()}")

    written = []
    application = {
        "proposal": proposal.relative_to(root).as_posix(),
        "proposal_sha256": proposal_digest,
        "commit_sha": commit_sha,
        "inspector_fingerprint": actual_fp,
        "status": "reconciled" if all_already_applied else "applied",
        "changes": changes,
    }
    try:
        for item in prepared:
            if item["status"] == "already_applied":
                continue
            if sha256(item["file"].read_bytes()) != sha256(item["before"]):
                raise ApplierError(f"Knowledge target changed during application; refusing to overwrite: {item['path']}")
            atomic_replace(item["file"], item["after"])
            written.append(item)
        publish_record_no_clobber(record_path, application)
    except Exception:
        for item in reversed(written):
            try:
                if sha256(item["file"].read_bytes()) == sha256(item["after"]):
                    atomic_replace(item["file"], item["before"])
            except (OSError, ApplierError):
                pass
        raise
    state["schema_version"] = 1
    state["applications"][proposal_digest] = application
    try:
        atomic_json(state_path, state)
    except Exception as exc:
        raise ApplierError(
            "Knowledge was updated and the application record was saved, but state finalization failed; "
            "rerun this proposal to reconcile state without duplicating the edit. {}".format(exc)
        ) from exc
    return result("ALREADY_APPLIED" if all_already_applied else "APPLIED", proposal, commit_sha, changes=changes,
                  state=state_path.relative_to(root).as_posix(),
                  application_record=record_path.relative_to(root).as_posix(), writes=not all_already_applied)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Validate and apply a Knowledge Update Proposal without contacting NotebookLM.")
    parser.add_argument("proposal", help="proposal Markdown path directly under .ai/proposals/")
    parser.add_argument("--dry-run", action="store_true", help="validate and report changes without writing files")
    args = parser.parse_args(argv)
    try:
        root = repository_root()
        proposal = proposal_path(root, args.proposal)
        output = apply_proposal(root, proposal, dry_run=args.dry_run)
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (ApplierError, OSError, UnicodeError) as exc:
        output = {"status": "REFUSED", "proposal": args.proposal, "reason": str(exc), "writes": False}
        print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
