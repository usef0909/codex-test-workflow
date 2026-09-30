import copy
import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "knowledge_updater.py"
SPEC = importlib.util.spec_from_file_location("knowledge_updater_under_test", SCRIPT)
assert SPEC and SPEC.loader
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
APPLIER_SPEC = importlib.util.spec_from_file_location(
    "knowledge_applier_for_updater_tests", SCRIPT.with_name("knowledge_applier.py"))
assert APPLIER_SPEC and APPLIER_SPEC.loader
applier = importlib.util.module_from_spec(APPLIER_SPEC)
APPLIER_SPEC.loader.exec_module(applier)


class ProposalPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "commit-knowledge-update.md"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_refuses_existing_proposal(self):
        self.path.write_text("old proposal", encoding="utf-8")
        with self.assertRaises(updater.UpdaterError):
            updater.publish_proposal(self.path, "replacement")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "old proposal")
        self.assertFalse(self.path.with_name("commit-knowledge-update.previous.md").exists())


class ApplicationPlanTests(unittest.TestCase):
    commit = "a" * 40

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.target = "Knowledge/00 Project/Development Configuration.md"
        self.content = "# Development Configuration\n\n## Godot project settings\n\nCurrent settings.\n"
        path = self.root / self.target
        path.parent.mkdir(parents=True)
        path.write_bytes(self.content.encode("utf-8"))
        self.facts = [
            {"category": "input_action", "statement": f"Input action `move_{direction}` is configured with `{binding}`.", "source": "(Game Name)/project.godot",
             "needles": [[f"move_{direction}"], [binding]]}
            for direction, binding in (("up", "physical key W (87)"), ("down", "physical key S (83)"),
                                       ("left", "physical key A (65)"), ("right", "physical key D (68)"))
        ]
        self.unrelated = {"category": "scene_resource", "statement": "Scene assigns icon to texture.", "source": "(Game Name)/Player.tscn", "needles": [["icon"], ["texture"]]}
        self.unverified = {"category": "input_action", "statement": "Input action `move_fake` is configured with `physical key Q (81)`.", "source": "(Game Name)/project.godot", "needles": [["move_fake"], ["Q"]]}
        self.item = {
            "recommendation": {"path": self.target}, "facts": [], "missing": [*self.facts, self.unverified],
            "verified_facts": [*self.facts, self.unrelated], "knowledge_content": self.content,
            "knowledge_sha256": updater.sha256(self.content.encode("utf-8")),
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_plan_is_deterministic_hashed_anchored_selected_and_applier_compatible(self):
        first, _ = updater.build_application_plan(self.root, self.commit, [self.item])
        second, _ = updater.build_application_plan(self.root, self.commit, [self.item])
        self.assertEqual(first, second)
        self.assertEqual(first[0]["baseline_sha256"], updater.sha256((self.root / self.target).read_bytes()))
        self.assertEqual(first[0]["anchor"], "## Godot project settings")
        self.assertIn("input actions", first[0]["context"])
        self.assertEqual(len(first[0]["facts"]), 4)
        rendered = "\n".join(updater.render_application_plan(first))
        parsed = applier.parse_application_plan(rendered)
        self.assertEqual(parsed[0]["facts"], first[0]["facts"])
        self.assertEqual(parsed[0]["baseline_sha256"], first[0]["baseline_sha256"])
        self.assertEqual(parsed[0]["anchor"], first[0]["anchor"])
        self.assertEqual(parsed[0]["context"], first[0]["context"])
        self.assertEqual(parsed[0]["renderer"], "development_input_table")
        self.assertEqual(parsed[0]["rendered_markdown"], first[0]["rendered_markdown"])
        self.assertEqual(parsed[0]["selected_verified_facts"], first[0]["selected_verified_facts"])
        self.assertEqual(parsed[0]["claim_to_fact_mapping"], first[0]["claim_to_fact_mapping"])
        self.assertEqual(len(first[0]["claim_to_fact_mapping"]), 4)
        self.assertNotIn(self.unverified["statement"], rendered)
        self.assertNotIn(self.unrelated["statement"], rendered)

    def test_generated_table_plan_passes_applier_dry_run(self):
        plan, _ = updater.build_application_plan(self.root, self.commit, [self.item])
        inspector = updater.load_inspector(SCRIPT.parent.parent)
        proposal_dir = self.root / ".ai/proposals"
        proposal_dir.mkdir(parents=True)
        evidence = [f"### `{self.target}`", "", "- Evidence:"]
        evidence.extend(
            f"  - {fact['statement']} Source: `{fact['source']}` at `{self.commit}`; missing from this Knowledge file."
            for fact in self.facts
        )
        proposal = proposal_dir / "generated-table.md"
        proposal.write_text("\n".join([
            "# Knowledge Update Proposal", "", "## Final Status", "", "`UPDATE_PROPOSED`", "",
            "## Commit", "", f"- Commit: `{self.commit}`",
            f"- Current Inspector fingerprint: `sha256:{inspector.implementation_fingerprint()}`", "",
            "## Knowledge Files Affected", "", *evidence, "",
            *updater.render_application_plan(plan), "",
        ]), encoding="utf-8", newline="\n")

        def fake_git(root, *args, check=True):
            if args[:2] in (("rev-parse", "--verify"), ("rev-parse", "HEAD")):
                return self.commit
            if args and args[0] == "status":
                return ""
            raise AssertionError(f"Unexpected Git invocation: {args}")

        with patch.object(applier, "load_updater", return_value=updater), \
                patch.object(updater, "load_inspector", return_value=inspector), \
                patch.object(updater, "project_facts", return_value=(self.facts, "", "", [])), \
                patch.object(applier, "git", side_effect=fake_git):
            result = applier.apply_proposal(self.root, proposal, dry_run=True)
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["writes"])
        self.assertEqual((self.root / self.target).read_text(encoding="utf-8"), self.content)

    def test_incomplete_application_plan_is_still_refused(self):
        inspector = updater.load_inspector(SCRIPT.parent.parent)
        proposal_dir = self.root / ".ai/proposals"
        proposal_dir.mkdir(parents=True, exist_ok=True)
        proposal = proposal_dir / "incomplete.md"
        proposal.write_text("\n".join([
            "# Knowledge Update Proposal", "", "## Final Status", "", "`UPDATE_PROPOSED`", "",
            "## Commit", "", f"- Commit: `{self.commit}`",
            f"- Current Inspector fingerprint: `sha256:{inspector.implementation_fingerprint()}`", "",
            "## Knowledge Files Affected", "", "## Application Plan", "",
            "No facts were selected for safe append-only application.", "",
        ]), encoding="utf-8", newline="\n")

        def fake_git(root, *args, check=True):
            if args[:2] == ("rev-parse", "--verify"):
                return self.commit
            raise AssertionError(f"Unexpected Git invocation: {args}")

        with patch.object(applier, "load_updater", return_value=updater), \
                patch.object(updater, "load_inspector", return_value=inspector), \
                patch.object(applier, "git", side_effect=fake_git):
            with self.assertRaisesRegex(applier.ApplierError, "no Knowledge target entries"):
                applier.apply_proposal(self.root, proposal, dry_run=True)

    def test_non_decision_targets_without_applicable_verified_facts_are_omitted(self):
        decision = {
            "recommendation": {"path": "Knowledge/Decisions/Unestablished Decisions.md"},
            "verified_facts": [self.unrelated], "knowledge_content": "# Decisions\n",
            "knowledge_sha256": "0" * 64, "missing": [self.unrelated],
        }
        plan, _ = updater.build_application_plan(self.root, self.commit, [decision])
        self.assertEqual(plan, [])


class SystemsDocumentationDraftTests(unittest.TestCase):
    target = updater.SYSTEMS_DOCUMENTATION_TARGET

    def setUp(self):
        self.facts = [
            {"category": "scene_file", "statement": "Scene `res://Player.tscn` exists.", "source": "(Game Name)/Player.tscn"},
            {"category": "scene_root", "statement": "`res://Player.tscn` has root `Player` of type `CharacterBody2D`.", "source": "(Game Name)/Player.tscn"},
            {"category": "scene_script", "statement": "Node `Player` in `res://Player.tscn` attaches `res://Player.gd`.", "source": "(Game Name)/Player.tscn"},
            {"category": "script_file", "statement": "Script `res://Player.gd` exists.", "source": "(Game Name)/Player.gd"},
            {"category": "script_inheritance", "statement": "`res://Player.gd` extends `CharacterBody2D`.", "source": "(Game Name)/Player.gd"},
            {"category": "script_export", "statement": "`res://Player.gd` exports `speed` using `@export var speed: float = 300.0`.", "source": "(Game Name)/Player.gd"},
            {"category": "script_function", "statement": "`res://Player.gd` defines `func _physics_process(_delta: float) -> void:`.", "source": "(Game Name)/Player.gd"},
            {"category": "script_call", "statement": '`res://Player.gd` calls `move_and_slide()`.'.rstrip(), "source": "(Game Name)/Player.gd"},
        ]

    def test_render_is_deterministic_and_every_claim_maps_to_selected_facts(self):
        first = updater.render_systems_documentation_draft(self.target, self.facts)
        second = updater.render_systems_documentation_draft(self.target, self.facts)

        self.assertEqual(first, second)
        self.assertEqual(len(first["supporting_facts"]), 8)
        selected_ids = {fact["id"] for fact in first["supporting_facts"]}
        self.assertTrue(first["claims"])
        for claim in first["claims"]:
            self.assertTrue(claim["verified_fact_ids"])
            self.assertTrue(set(claim["verified_fact_ids"]).issubset(selected_ids))
        self.assertIn("`move_and_slide()`", first["paragraph"])
        self.assertNotIn("Input.get_vector", first["paragraph"])

    def test_current_valid_two_sentence_draft_passes_claim_validation(self):
        draft = updater.render_systems_documentation_draft(self.target, self.facts)

        self.assertEqual(len(draft["paragraph"].split(". ")), 2)
        self.assertIsNone(updater.validate_systems_documentation_draft(draft, self.facts))

    def test_attachment_claim_cannot_be_supported_by_script_existence_alone(self):
        draft = updater.render_systems_documentation_draft(self.target, self.facts)
        script_exists_id = next(fact["id"] for fact in draft["supporting_facts"]
                                if fact["category"] == "script_file")
        draft["claims"][0]["verified_fact_ids"] = [script_exists_id]

        with self.assertRaisesRegex(updater.UpdaterError, "scene_structure.*not supported"):
            updater.validate_systems_documentation_draft(draft, self.facts)

    def test_script_inheritance_claim_cannot_be_supported_by_scene_root_type(self):
        draft = updater.render_systems_documentation_draft(self.target, self.facts)
        scene_root_id = next(fact["id"] for fact in draft["supporting_facts"]
                             if fact["category"] == "scene_root")
        draft["claims"][1]["verified_fact_ids"] = [scene_root_id]

        with self.assertRaisesRegex(updater.UpdaterError, "script_structure.*not supported"):
            updater.validate_systems_documentation_draft(draft, self.facts)

    def test_speed_default_claim_refuses_different_export_value(self):
        draft = updater.render_systems_documentation_draft(self.target, self.facts)
        altered_facts = copy.deepcopy(self.facts)
        speed_fact = next(fact for fact in altered_facts if fact["category"] == "script_export")
        speed_fact["statement"] = "`res://Player.gd` exports `speed` using `@export var speed: float = 250.0`."

        with self.assertRaisesRegex(updater.UpdaterError, "player_speed.*found 0"):
            updater.validate_systems_documentation_draft(draft, altered_facts)

    def test_function_call_relationship_is_rejected_when_facts_only_show_both(self):
        draft = updater.render_systems_documentation_draft(self.target, self.facts)
        unsupported = "`res://Player.gd` defines `_physics_process`, which calls `move_and_slide()`."
        draft["claims"][1]["text"] = unsupported
        draft["paragraph"] = draft["claims"][0]["text"] + " " + unsupported

        with self.assertRaisesRegex(updater.UpdaterError, "script_structure.*not supported"):
            updater.validate_systems_documentation_draft(draft, self.facts)

    def test_claim_with_unverified_project_path_is_rejected(self):
        draft = updater.render_systems_documentation_draft(self.target, self.facts)
        unsupported = draft["claims"][0]["text"].replace("res://Player.tscn", "res://OtherPlayer.tscn")
        draft["claims"][0]["text"] = unsupported
        draft["paragraph"] = unsupported + " " + draft["claims"][1]["text"]

        with self.assertRaisesRegex(updater.UpdaterError, "scene_structure.*not supported"):
            updater.validate_systems_documentation_draft(draft, self.facts)

    def test_unmapped_factual_sentence_is_rejected(self):
        draft = updater.render_systems_documentation_draft(self.target, self.facts)
        draft["paragraph"] += " Player movement uses WASD."

        with self.assertRaisesRegex(updater.UpdaterError, "outside the deterministic verified template"):
            updater.validate_systems_documentation_draft(draft, self.facts)

    def test_draft_cannot_reference_an_unselected_fact_or_add_unmapped_claim(self):
        draft = updater.render_systems_documentation_draft(self.target, self.facts)
        draft["claims"][0]["verified_fact_ids"].append("f" * 64)
        with self.assertRaisesRegex(updater.UpdaterError, "not supported"):
            updater.validate_systems_documentation_draft(draft, self.facts)

        draft = updater.render_systems_documentation_draft(self.target, self.facts)
        draft["paragraph"] += " Input.get_vector() uses the configured movement actions."
        with self.assertRaisesRegex(updater.UpdaterError, "outside the deterministic verified template"):
            updater.validate_systems_documentation_draft(draft, self.facts)

    def test_missing_or_unverified_required_fact_refuses_draft(self):
        unavailable = [fact for fact in self.facts if fact["category"] != "scene_script"]
        unavailable.append({"category": "scene_script", "statement": "Node `Player` in `res://Player.tscn` attaches `res://Player.gd`.", "source": "NotebookLM"})

        with self.assertRaisesRegex(updater.UpdaterError, "player_script_attachment"):
            updater.render_systems_documentation_draft(self.target, unavailable)

    def test_renderer_is_limited_to_systems_inventory(self):
        with self.assertRaisesRegex(updater.UpdaterError, "only supports"):
            updater.render_systems_documentation_draft("Knowledge/Code/Code Inventory.md", self.facts)

    def test_proposal_section_records_each_claim_and_verified_support(self):
        rendered = updater.render_documentation_drafts(
            [{"recommendation": {"path": self.target}, "verified_facts": self.facts}], "a" * 40)
        text = "\n".join(rendered)
        self.assertIn("## Proposed Documentation Drafts (Proposal Only)", text)
        for fact in self.facts:
            self.assertIn(fact["statement"], text)
        self.assertIn("Claim (`scene_structure`)", text)
        self.assertIn("Claim (`script_structure`)", text)

    def test_unrelated_target_produces_no_documentation_draft(self):
        self.assertEqual(updater.render_documentation_drafts(
            [{"recommendation": {"path": "Knowledge/Code/Code Inventory.md"}, "verified_facts": self.facts}], "a" * 40), [])


class SystemsReplacementProposalTests(unittest.TestCase):
    commit = updater.SYSTEMS_REPLACEMENT_COMMIT

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.path = self.root / updater.SYSTEMS_REPLACEMENT_TARGET
        self.path.parent.mkdir(parents=True)
        self.content = (
            "# Systems Inventory\n\n## Current status\n\n"
            + updater.SYSTEMS_REPLACEMENT_OLD_PASSAGE
            + "\n\nThis inventory describes the current tree.\n"
        ).encode("utf-8")
        self.path.write_bytes(self.content)
        self.facts = [
            {"category": "scene_file", "statement": "Scene `res://Player.tscn` exists.", "source": "(Game Name)/Player.tscn"},
            {"category": "scene_root", "statement": "`res://Player.tscn` has root `Player` of type `CharacterBody2D`.", "source": "(Game Name)/Player.tscn"},
            {"category": "scene_script", "statement": "Node `Player` in `res://Player.tscn` attaches `res://Player.gd`.", "source": "(Game Name)/Player.tscn"},
            {"category": "script_file", "statement": "Script `res://Player.gd` exists.", "source": "(Game Name)/Player.gd"},
            {"category": "script_inheritance", "statement": "`res://Player.gd` extends `CharacterBody2D`.", "source": "(Game Name)/Player.gd"},
            {"category": "script_export", "statement": "`res://Player.gd` exports `speed` using `@export var speed: float = 300.0`.", "source": "(Game Name)/Player.gd"},
            {"category": "script_function", "statement": "`res://Player.gd` defines `func _physics_process(_delta: float) -> void:`.", "source": "(Game Name)/Player.gd"},
            {"category": "script_call", "statement": "`res://Player.gd` calls `move_and_slide()`." , "source": "(Game Name)/Player.gd"},
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def build(self):
        return updater.build_systems_replacement_operation(self.root, self.commit, self.facts)

    def test_valid_replace_records_passage_replacement_and_file_hashes(self):
        operation = self.build()

        self.assertEqual(operation["operation"], "REPLACE")
        self.assertEqual(operation["path"], updater.SYSTEMS_REPLACEMENT_TARGET)
        self.assertEqual(operation["exact_old_passage"], updater.SYSTEMS_REPLACEMENT_OLD_PASSAGE)
        self.assertEqual(operation["old_passage_sha256"], updater.sha256(
            updater.SYSTEMS_REPLACEMENT_OLD_PASSAGE.encode("utf-8")))
        self.assertEqual(operation["replacement_markdown"], updater.render_systems_documentation_draft(
            updater.SYSTEMS_DOCUMENTATION_TARGET, self.facts)["paragraph"])
        self.assertEqual(operation["replacement_sha256"], updater.sha256(
            operation["replacement_markdown"].encode("utf-8")))
        self.assertEqual(operation["file_baseline_sha256"], updater.sha256(self.content))
        self.assertEqual(operation["anchor"], {
            "heading_path": list(updater.SYSTEMS_REPLACEMENT_ANCHOR), "occurrence": 1,
        })

    def test_replacement_application_plan_is_deterministic_and_round_trips(self):
        first = self.build()
        second = self.build()
        first_text = "\n".join(updater.render_application_plan([], replacements=[first]))
        second_text = "\n".join(updater.render_application_plan([], replacements=[second]))

        self.assertEqual(first, second)
        self.assertEqual(first_text, second_text)
        self.assertEqual(updater.parse_replacement_operations(first_text), [first])
        parsed = updater.parse_replacement_operations(first_text)[0]
        updater.validate_systems_replacement_operation(parsed, self.path.read_bytes(), self.facts, self.commit)

    def test_anchor_requires_a_unique_target_passage(self):
        duplicate = self.content.replace(
            updater.SYSTEMS_REPLACEMENT_OLD_PASSAGE.encode("utf-8"),
            (updater.SYSTEMS_REPLACEMENT_OLD_PASSAGE + "\n\n" + updater.SYSTEMS_REPLACEMENT_OLD_PASSAGE).encode("utf-8"),
        )
        self.path.write_bytes(duplicate)
        with self.assertRaisesRegex(updater.UpdaterError, "absent or non-unique"):
            self.build()

    def test_changed_passage_is_rejected(self):
        operation = self.build()
        changed = self.content.replace(
            updater.SYSTEMS_REPLACEMENT_OLD_PASSAGE.encode("utf-8"), b"The stale passage changed."
        )
        operation["file_baseline_sha256"] = updater.sha256(changed)
        with self.assertRaisesRegex(updater.UpdaterError, "changed, absent, or non-unique"):
            updater.validate_systems_replacement_operation(operation, changed, self.facts, self.commit)

    def test_altered_old_passage_and_incorrect_hash_are_rejected(self):
        operation = self.build()
        operation["exact_old_passage"] += " Altered."
        operation["old_passage_sha256"] = updater.sha256(operation["exact_old_passage"].encode("utf-8"))
        with self.assertRaisesRegex(updater.UpdaterError, "old passage is altered"):
            updater.validate_systems_replacement_operation(operation, self.content, self.facts, self.commit)

        operation = self.build()
        operation["old_passage_sha256"] = "0" * 64
        with self.assertRaisesRegex(updater.UpdaterError, "old-passage SHA-256 is incorrect"):
            updater.validate_systems_replacement_operation(operation, self.content, self.facts, self.commit)

    def test_incorrect_whole_file_baseline_is_rejected(self):
        operation = self.build()
        operation["file_baseline_sha256"] = "0" * 64
        with self.assertRaisesRegex(updater.UpdaterError, "whole-file baseline SHA-256"):
            updater.validate_systems_replacement_operation(operation, self.content, self.facts, self.commit)

    def test_unsupported_replacement_claim_is_rejected(self):
        operation = self.build()
        operation["replacement_markdown"] += " Player controls use WASD."
        operation["replacement_sha256"] = updater.sha256(operation["replacement_markdown"].encode("utf-8"))
        with self.assertRaisesRegex(updater.UpdaterError, "outside the validated Systems renderer"):
            updater.validate_systems_replacement_operation(operation, self.content, self.facts, self.commit)

    def test_missing_or_unselected_supporting_fact_is_rejected(self):
        operation = self.build()
        operation["selected_verified_facts"].pop()
        with self.assertRaisesRegex(updater.UpdaterError, "missing or unselected supporting facts"):
            updater.validate_systems_replacement_operation(operation, self.content, self.facts, self.commit)

        operation = self.build()
        operation["claim_to_fact_mapping"][0]["verified_fact_ids"] = ["f" * 64]
        with self.assertRaisesRegex(updater.UpdaterError, "claim-to-fact mapping"):
            updater.validate_systems_replacement_operation(operation, self.content, self.facts, self.commit)

    def test_missing_fields_and_add_only_plan_cannot_be_used_as_replace(self):
        with self.assertRaisesRegex(updater.UpdaterError, "ADD-only plan"):
            updater.validate_systems_replacement_operation(
                {"operation": "ADD", "path": updater.SYSTEMS_REPLACEMENT_TARGET},
                self.content, self.facts, self.commit,
            )
        operation = self.build()
        del operation["anchor"]
        with self.assertRaisesRegex(updater.UpdaterError, "missing required fields.*anchor"):
            updater.validate_systems_replacement_operation(operation, self.content, self.facts, self.commit)

    def test_legacy_add_plan_still_parses_and_replace_is_not_treated_as_add(self):
        add_plan = [{
            "path": "Knowledge/00 Project/Development Configuration.md",
            "baseline_sha256": "a" * 64,
            "anchor": "## Godot project settings",
            "context": "Input actions.",
            "facts": ["Input action `move_up` is configured with `physical key W (87)`."],
        }]
        replacement_text = "\n".join(updater.render_application_plan([], replacements=[self.build()]))
        legacy_text = "\n".join(updater.render_application_plan(add_plan))

        self.assertEqual(applier.parse_application_plan(legacy_text)[0]["facts"], add_plan[0]["facts"])
        with self.assertRaisesRegex(applier.ApplierError, "no Knowledge target entries"):
            applier.parse_application_plan(replacement_text)

    def test_building_replace_proposal_does_not_modify_knowledge(self):
        before = self.path.read_bytes()
        operation = self.build()
        rendered = updater.render_application_plan([], replacements=[operation])

        self.assertTrue(rendered)
        self.assertEqual(self.path.read_bytes(), before)


class CodeInventoryReplacementTests(unittest.TestCase):
    commit = updater.CODE_INVENTORY_REPLACEMENT_COMMIT

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.path = self.root / updater.CODE_INVENTORY_REPLACEMENT_TARGET
        self.path.parent.mkdir(parents=True)
        self.content = (
            "# Code Inventory\n\n## Tracked code\n\n"
            + updater.CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE
            + "\n\nNeighboring prose remains intact.\n"
        ).encode("utf-8")
        self.path.write_bytes(self.content)
        self.facts = [
            {"category": "script_file", "statement": "Script `res://Player.gd` exists.", "source": "(Game Name)/Player.gd"},
            {"category": "script_inheritance", "statement": "`res://Player.gd` extends `CharacterBody2D`.", "source": "(Game Name)/Player.gd"},
            {"category": "script_export", "statement": "`res://Player.gd` exports `speed` using `@export var speed: float = 300.0`.", "source": "(Game Name)/Player.gd"},
            {"category": "script_function", "statement": "`res://Player.gd` defines `func _physics_process(_delta: float) -> void:`.", "source": "(Game Name)/Player.gd"},
            {"category": "script_call", "statement": "`res://Player.gd` calls `Input.get_vector()`.", "source": "(Game Name)/Player.gd"},
            {"category": "script_call", "statement": "`res://Player.gd` calls `move_and_slide()`.", "source": "(Game Name)/Player.gd"},
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def build(self):
        return updater.build_code_inventory_replacement_operation(self.root, self.commit, self.facts)

    def test_valid_replace_uses_exact_draft_and_all_six_verified_facts(self):
        operation = self.build()
        self.assertEqual(operation["operation"], "REPLACE")
        self.assertEqual(operation["path"], updater.CODE_INVENTORY_REPLACEMENT_TARGET)
        self.assertEqual(operation["exact_old_passage"], updater.CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE)
        self.assertEqual(operation["replacement_markdown"], updater.CODE_INVENTORY_REPLACEMENT_TEXT)
        self.assertEqual(operation["file_baseline_sha256"], updater.sha256(self.content))
        self.assertEqual(operation["old_passage_sha256"], updater.sha256(
            updater.CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE.encode("utf-8")))
        self.assertEqual(operation["replacement_sha256"], updater.sha256(
            updater.CODE_INVENTORY_REPLACEMENT_TEXT.encode("utf-8")))
        self.assertEqual(operation["anchor"], {
            "heading_path": list(updater.CODE_INVENTORY_REPLACEMENT_ANCHOR), "occurrence": 1,
        })
        self.assertEqual(len(operation["selected_verified_facts"]), 6)

    def test_unsupported_claim_is_refused(self):
        operation = self.build()
        operation["replacement_markdown"] += " Player uses WASD."
        operation["replacement_sha256"] = updater.sha256(operation["replacement_markdown"].encode("utf-8"))
        with self.assertRaisesRegex(updater.UpdaterError, "unsupported or unselected claims"):
            updater.validate_code_inventory_replacement_operation(operation, self.content, self.facts, self.commit)

    def test_stale_baseline_is_refused(self):
        operation = self.build()
        with self.assertRaisesRegex(updater.UpdaterError, "whole-file baseline"):
            updater.validate_code_inventory_replacement_operation(operation, self.content + b"\n", self.facts, self.commit)

    def test_wrong_old_passage_is_refused(self):
        operation = self.build()
        operation["exact_old_passage"] += " Altered."
        operation["old_passage_sha256"] = updater.sha256(operation["exact_old_passage"].encode("utf-8"))
        with self.assertRaisesRegex(updater.UpdaterError, "old passage does not match"):
            updater.validate_code_inventory_replacement_operation(operation, self.content, self.facts, self.commit)

    def test_draft_does_not_modify_knowledge(self):
        before = self.path.read_bytes()
        self.build()
        self.assertEqual(self.path.read_bytes(), before)


class PendingReplacementSelectionTests(unittest.TestCase):
    commit = updater.SYSTEMS_REPLACEMENT_COMMIT

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        repo_root = SCRIPT.parent.parent
        self.inspector = updater.load_inspector(repo_root)
        self.facts, *_ = updater.project_facts(repo_root, self.commit, self.inspector)
        self.systems_path = self.root / updater.SYSTEMS_REPLACEMENT_TARGET
        self.code_path = self.root / updater.CODE_INVENTORY_REPLACEMENT_TARGET
        self.systems_path.parent.mkdir(parents=True)
        self.code_path.parent.mkdir(parents=True)
        self.systems_replacement = updater.render_systems_documentation_draft(
            updater.SYSTEMS_DOCUMENTATION_TARGET, self.facts)["paragraph"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_target(self, path, anchor, passage):
        path.write_text("\n\n".join((*anchor, "", passage, "")), encoding="utf-8")

    def test_applied_systems_replacement_is_skipped_and_code_proposal_can_be_built(self):
        self.write_target(self.systems_path, updater.SYSTEMS_REPLACEMENT_ANCHOR,
                          self.systems_replacement)
        self.write_target(self.code_path, updater.CODE_INVENTORY_REPLACEMENT_ANCHOR,
                          updater.CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE)
        operations, skipped, conflicts = updater.select_pending_replacement_operations(
            self.root, self.commit, self.facts)
        self.assertEqual(conflicts, [])
        self.assertEqual(skipped, [updater.SYSTEMS_REPLACEMENT_TARGET])
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]["path"], updater.CODE_INVENTORY_REPLACEMENT_TARGET)
        updater.validate_code_inventory_replacement_operation(
            operations[0], self.code_path.read_bytes(), self.facts, self.commit)

    def test_already_updated_targets_are_skipped(self):
        self.write_target(self.systems_path, updater.SYSTEMS_REPLACEMENT_ANCHOR,
                          self.systems_replacement)
        self.write_target(self.code_path, updater.CODE_INVENTORY_REPLACEMENT_ANCHOR,
                          updater.CODE_INVENTORY_REPLACEMENT_TEXT)
        data_path = self.root / updater.DATA_ASSETS_REPLACEMENT_TARGET
        data_path.parent.mkdir(parents=True, exist_ok=True)
        data_path.write_text("\n\n".join([
            *updater.DATA_ASSETS_MAIN_ANCHOR, "", updater.DATA_ASSETS_MAIN_REPLACEMENT,
            updater.DATA_ASSETS_OTHER_ANCHOR[-1], "", updater.DATA_ASSETS_OTHER_REPLACEMENT,
        ]), encoding="utf-8")
        operations, skipped, conflicts = updater.select_pending_replacement_operations(
            self.root, self.commit, self.facts)
        self.assertEqual(operations, [])
        self.assertEqual(skipped, [updater.SYSTEMS_REPLACEMENT_TARGET,
                                   updater.CODE_INVENTORY_REPLACEMENT_TARGET,
                                   f"{updater.DATA_ASSETS_REPLACEMENT_TARGET}#main_scene",
                                   f"{updater.DATA_ASSETS_REPLACEMENT_TARGET}#resources"])
        self.assertEqual(conflicts, [])

    def test_missing_or_nonunique_stale_passage_requires_review(self):
        self.write_target(self.systems_path, updater.SYSTEMS_REPLACEMENT_ANCHOR,
                          "An unrelated Systems status passage.")
        self.write_target(self.code_path, updater.CODE_INVENTORY_REPLACEMENT_ANCHOR,
                          updater.CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE)
        operations, skipped, conflicts = updater.select_pending_replacement_operations(
            self.root, self.commit, self.facts)
        self.assertEqual(operations, [])
        self.assertEqual(skipped, [])
        self.assertTrue(conflicts)
        self.assertIn("neither an exact uniquely anchored stale passage", conflicts[0])

        self.write_target(self.systems_path, updater.SYSTEMS_REPLACEMENT_ANCHOR,
                          self.systems_replacement)
        duplicated = updater.CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE + "\n\n" + updater.CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE
        self.write_target(self.code_path, updater.CODE_INVENTORY_REPLACEMENT_ANCHOR, duplicated)
        operations, _skipped, conflicts = updater.select_pending_replacement_operations(
            self.root, self.commit, self.facts, requested_target=updater.CODE_INVENTORY_REPLACEMENT_TARGET)
        self.assertEqual(operations, [])
        self.assertTrue(conflicts)

    def test_selection_and_operation_generation_do_not_modify_knowledge(self):
        self.write_target(self.systems_path, updater.SYSTEMS_REPLACEMENT_ANCHOR,
                          self.systems_replacement)
        self.write_target(self.code_path, updater.CODE_INVENTORY_REPLACEMENT_ANCHOR,
                          updater.CODE_INVENTORY_REPLACEMENT_OLD_PASSAGE)
        before = {path: path.read_bytes() for path in (self.systems_path, self.code_path)}
        operations, _skipped, conflicts = updater.select_pending_replacement_operations(
            self.root, self.commit, self.facts)
        self.assertFalse(conflicts)
        self.assertEqual(len(operations), 1)
        self.assertEqual({path: path.read_bytes() for path in before}, before)


class DevelopmentConfigurationTableTests(unittest.TestCase):
    target = updater.DEVELOPMENT_CONFIGURATION_TARGET
    commit = "b" * 40

    def setUp(self):
        self.facts = [
            {
                "category": "input_action",
                "statement": f"Input action `{action}` is configured with `physical key {key} ({ord(key)})`.",
                "source": "(Game Name)/project.godot",
            }
            for action, key in updater.DEVELOPMENT_INPUT_ROWS
        ]

    def test_four_rows_are_deterministic_and_exact(self):
        first = updater.render_development_input_table(self.target, self.facts)
        second = updater.render_development_input_table(self.target, self.facts)

        self.assertEqual(first, second)
        self.assertEqual(first["table"], "\n".join([
            "| Action | Configured physical key |",
            "|---|---|",
            "| move_down | physical key S (83) |",
            "| move_left | physical key A (65) |",
            "| move_right | physical key D (68) |",
            "| move_up | physical key W (87) |",
        ]))
        self.assertEqual(
            [(row["action"], row["key"]) for row in first["rows"]],
            list(updater.DEVELOPMENT_INPUT_ROWS),
        )

    def test_every_row_maps_to_its_exact_verified_fact_id(self):
        draft = updater.render_development_input_table(self.target, self.facts)
        facts_by_id = {fact["id"]: fact for fact in draft["supporting_facts"]}

        self.assertEqual(len(draft["rows"]), 4)
        for row, (action, key), fact in zip(draft["rows"], updater.DEVELOPMENT_INPUT_ROWS, self.facts):
            expected_id = updater.documentation_fact_id(fact)
            self.assertEqual(row, {"action": action, "key": key, "verified_fact_ids": [expected_id]})
            self.assertEqual(facts_by_id[expected_id]["statement"], fact["statement"])
            self.assertEqual(facts_by_id[expected_id]["source"], "(Game Name)/project.godot")
        self.assertEqual(len(draft["claim_to_fact_mapping"]), 4)

    def test_missing_action_binding_is_rejected(self):
        with self.assertRaisesRegex(updater.UpdaterError, "move_right.*found 0"):
            updater.render_development_input_table(self.target, self.facts[:2] + self.facts[3:])

    def test_changed_key_is_rejected(self):
        altered = copy.deepcopy(self.facts)
        altered[0]["statement"] = "Input action `move_down` is configured with `physical key Q (81)`."
        with self.assertRaisesRegex(updater.UpdaterError, "move_down.*expected `S`"):
            updater.render_development_input_table(self.target, altered)

    def test_wrong_action_is_rejected(self):
        altered = copy.deepcopy(self.facts)
        altered[0]["statement"] = "Input action `move_forward` is configured with `physical key W (87)`."
        with self.assertRaisesRegex(updater.UpdaterError, "move_down.*found 0"):
            updater.render_development_input_table(self.target, altered)

    def test_wrong_source_path_is_rejected(self):
        altered = copy.deepcopy(self.facts)
        altered[0]["source"] = "Knowledge/project.godot"
        with self.assertRaisesRegex(updater.UpdaterError, "move_down.*found 0"):
            updater.render_development_input_table(self.target, altered)

    def test_ambiguous_multiple_bindings_are_rejected(self):
        altered = copy.deepcopy(self.facts)
        altered[0]["statement"] = (
            "Input action `move_down` is configured with `physical key S (83); physical key Down (4194320)`."
        )
        with self.assertRaisesRegex(updater.UpdaterError, "move_down.*ambiguous"):
            updater.render_development_input_table(self.target, altered)

    def test_unsupported_or_unmapped_row_is_rejected(self):
        draft = updater.render_development_input_table(self.target, self.facts)
        draft["rows"][0]["action"] = "move_forward"
        with self.assertRaisesRegex(updater.UpdaterError, "unsupported or mapped"):
            updater.validate_development_input_table(draft, self.facts)

        draft = updater.render_development_input_table(self.target, self.facts)
        draft["table"] += "\n| `jump` | Space |"
        with self.assertRaisesRegex(updater.UpdaterError, "unsupported row or value"):
            updater.validate_development_input_table(draft, self.facts)

    def test_proposal_integration_emits_table_and_row_evidence(self):
        rendered = updater.render_documentation_drafts(
            [{"recommendation": {"path": self.target}, "verified_facts": self.facts}], self.commit
        )
        text = "\n".join(rendered)

        self.assertIn("## Proposed Documentation Drafts (Proposal Only)", text)
        self.assertIn("| Action | Configured physical key |", text)
        self.assertIn("| move_up | physical key W (87) |", text)
        for fact in self.facts:
            self.assertIn(f"Verified fact `{updater.documentation_fact_id(fact)}`", text)
        self.assertNotIn("Player controls", text)
        self.assertNotIn("movement controls", text)


class DataAssetsReplacementProposalTests(unittest.TestCase):
    commit = updater.DATA_ASSETS_REPLACEMENT_COMMIT

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.path = self.root / updater.DATA_ASSETS_REPLACEMENT_TARGET
        self.path.parent.mkdir(parents=True)
        self.content = "\n\n".join([
            "# Project Assets and Resources", "## Scene resource", "### `Main.tscn`",
            updater.DATA_ASSETS_MAIN_OLD_PASSAGE, "## Tracked asset", "### `icon.svg`",
            "Existing icon description.", "## Other resources and data",
            updater.DATA_ASSETS_OTHER_OLD_PASSAGE,
        ]) + "\n"
        self.path.write_text(self.content, encoding="utf-8")
        inspector = updater.load_inspector(SCRIPT.parent.parent)
        self.facts, *_ = updater.project_facts(SCRIPT.parent.parent, self.commit, inspector)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_each_exact_replacement_is_verified_and_versioned_sequentially(self):
        operations, skipped, conflicts = updater.select_pending_replacement_operations(
            self.root, self.commit, self.facts, requested_target=updater.DATA_ASSETS_REPLACEMENT_TARGET)
        self.assertFalse(skipped)
        self.assertFalse(conflicts)
        self.assertEqual(len(operations), 1)
        first = operations[0]
        self.assertEqual(first["exact_old_passage"], updater.DATA_ASSETS_MAIN_OLD_PASSAGE)
        self.assertEqual(len(first["selected_verified_facts"]), 4)
        self.assertEqual(first["file_baseline_sha256"], updater.sha256(self.path.read_bytes()))
        self.assertEqual(len(first["claim_to_fact_mapping"]), 3)
        self.assertIn("instances `res://Player.tscn` as node `Player`", first["replacement_markdown"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.content)

        updated = self.content.replace(first["exact_old_passage"], first["replacement_markdown"], 1)
        self.path.write_text(updated, encoding="utf-8")
        next_operations, next_skipped, conflicts = updater.select_pending_replacement_operations(
            self.root, self.commit, self.facts, requested_target=updater.DATA_ASSETS_REPLACEMENT_TARGET)
        self.assertFalse(conflicts)
        self.assertEqual(next_skipped, [f"{updater.DATA_ASSETS_REPLACEMENT_TARGET}#main_scene"])
        self.assertEqual(next_operations[0]["exact_old_passage"], updater.DATA_ASSETS_OTHER_OLD_PASSAGE)
        self.assertEqual(next_operations[0]["file_baseline_sha256"], updater.sha256(self.path.read_bytes()))
        self.assertEqual(len(next_operations[0]["selected_verified_facts"]), 2)

    def test_data_replacement_refuses_missing_facts_and_changed_passage(self):
        with self.assertRaisesRegex(updater.UpdaterError, "player_root"):
            updater.build_data_assets_replacement_operation(
                self.root, self.commit,
                [fact for fact in self.facts if fact["statement"] != "`res://Player.tscn` has root `Player` of type `CharacterBody2D`."],
                "main_scene")
        self.path.write_text(self.content.replace("no child nodes", "with child nodes"), encoding="utf-8")
        operations, _skipped, conflicts = updater.select_pending_replacement_operations(
            self.root, self.commit, self.facts, requested_target=updater.DATA_ASSETS_REPLACEMENT_TARGET)
        self.assertFalse(operations)
        self.assertTrue(conflicts)


class ProposalBehaviorTests(unittest.TestCase):
    commit = "a" * 40

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.proposals = self.root / ".ai/proposals"
        self.path = Path(self.temp_dir.name) / "commit-knowledge-update.md"
        self.canonical = self.proposals / f"{self.commit}-knowledge-update.md"
        self.backup = self.proposals / f"{self.commit}-knowledge-update.previous.md"
        self.entry = {"review_file": f".ai/reviews/{self.commit}-review.md"}

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_versioned(self, *extra_args):
        state = {"reviews": {self.commit: self.entry}}
        output = io.StringIO()
        errors = io.StringIO()
        with patch.object(updater, "repo_root", return_value=self.root), \
             patch.object(updater, "read_json", return_value=state), \
             patch.object(updater, "build_proposal", return_value=("UPDATE_PROPOSED", "fresh proposal")), \
             redirect_stdout(output), redirect_stderr(errors):
            result = updater.main(["--versioned", self.commit, *extra_args])
        return result, output.getvalue(), errors.getvalue()

    def test_versioned_cli_creates_fingerprint_named_proposal(self):
        self.canonical.parent.mkdir(parents=True)
        self.canonical.write_text("canonical stays", encoding="utf-8")
        self.backup.write_text("backup stays", encoding="utf-8")
        expected = self.proposals / f"{self.commit}-knowledge-update-{updater.proposal_fingerprint()}.md"

        result, output, _ = self.run_versioned()

        self.assertEqual(result, 0, output)
        self.assertEqual(expected.read_text(encoding="utf-8"), "fresh proposal")
        self.assertRegex(expected.stem, r"^" + self.commit + r"-knowledge-update-[0-9a-f]{64}$")
        self.assertEqual(self.canonical.read_text(encoding="utf-8"), "canonical stays")
        self.assertEqual(self.backup.read_text(encoding="utf-8"), "backup stays")

    def test_versioned_cli_refuses_existing_versioned_proposal(self):
        expected = self.proposals / f"{self.commit}-knowledge-update-{updater.proposal_fingerprint()}.md"
        expected.parent.mkdir(parents=True)
        expected.write_text("existing version", encoding="utf-8")

        result, output, errors = self.run_versioned()

        self.assertEqual(result, 1)
        self.assertIn("refusing to overwrite", errors)
        self.assertEqual(expected.read_text(encoding="utf-8"), "existing version")

    def test_versioned_dry_run_does_not_write_or_change_proposals(self):
        self.canonical.parent.mkdir(parents=True)
        self.canonical.write_text("canonical stays", encoding="utf-8")
        self.backup.write_text("backup stays", encoding="utf-8")
        expected = self.proposals / f"{self.commit}-knowledge-update-{updater.proposal_fingerprint()}.md"

        result, output, _ = self.run_versioned("--dry-run")

        self.assertEqual(result, 0, output)
        self.assertIn(str(expected), output)
        self.assertFalse(expected.exists())
        self.assertEqual(self.canonical.read_text(encoding="utf-8"), "canonical stays")
        self.assertEqual(self.backup.read_text(encoding="utf-8"), "backup stays")

    def test_current_fingerprinted_inspection_is_preferred_to_stale_canonical(self):
        inspector_spec = importlib.util.spec_from_file_location(
            "inspector_for_versioned_evidence", SCRIPT.with_name("change_inspector.py"))
        inspector = importlib.util.module_from_spec(inspector_spec)
        inspector_spec.loader.exec_module(inspector)
        fingerprint = inspector.implementation_fingerprint()
        current_text = f"# Fresh report\n- Inspector fingerprint: `sha256:{fingerprint}`\n"
        stale_text = "# Old report\n- Inspector fingerprint: `sha256:" + "0" * 64 + "`\n"
        versioned = self.root / f".ai/changes/{self.commit}-inspection-{fingerprint}.md"
        canonical = self.root / f".ai/changes/{self.commit}-inspection.md"
        versioned.parent.mkdir(parents=True)
        versioned.write_text(current_text, encoding="utf-8")
        canonical.write_text(stale_text, encoding="utf-8")

        report, source = updater.current_inspector_evidence(self.root, self.commit, "b" * 40, inspector)

        self.assertEqual(report, current_text)
        self.assertEqual(source, versioned.relative_to(self.root).as_posix())

    def test_replace_preserves_previous_and_publishes_new(self):
        self.path.write_text("old proposal", encoding="utf-8")
        backup = updater.publish_proposal(self.path, "new proposal", replace_existing=True)
        self.assertEqual(backup, self.path.with_name("commit-knowledge-update.previous.md"))
        self.assertEqual(backup.read_text(encoding="utf-8"), "old proposal")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "new proposal")

    def test_existing_backup_blocks_replacement_without_changes(self):
        self.path.write_text("old proposal", encoding="utf-8")
        backup = self.path.with_name("commit-knowledge-update.previous.md")
        backup.write_text("older backup", encoding="utf-8")
        with self.assertRaises(updater.UpdaterError):
            updater.publish_proposal(self.path, "replacement", replace_existing=True)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "old proposal")
        self.assertEqual(backup.read_text(encoding="utf-8"), "older backup")

    def test_new_proposal_does_not_create_backup(self):
        backup = updater.publish_proposal(self.path, "new proposal", replace_existing=True)
        self.assertIsNone(backup)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "new proposal")

    def test_failed_publish_restores_original(self):
        self.path.write_text("old proposal", encoding="utf-8")
        with patch.object(updater, "publish_no_clobber", side_effect=updater.UpdaterError("simulated failure")):
            with self.assertRaises(updater.UpdaterError):
                updater.publish_proposal(self.path, "replacement", replace_existing=True)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "old proposal")
        self.assertFalse(self.path.with_name("commit-knowledge-update.previous.md").exists())


if __name__ == "__main__":
    unittest.main()
