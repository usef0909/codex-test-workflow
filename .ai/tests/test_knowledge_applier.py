import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


AI_DIR = Path(__file__).resolve().parents[1]
UPDATER_SPEC = importlib.util.spec_from_file_location("knowledge_updater_for_applier_tests", AI_DIR / "knowledge_updater.py")
assert UPDATER_SPEC and UPDATER_SPEC.loader
updater = importlib.util.module_from_spec(UPDATER_SPEC)
UPDATER_SPEC.loader.exec_module(updater)
SPEC = importlib.util.spec_from_file_location("knowledge_applier_under_test", AI_DIR / "knowledge_applier.py")
assert SPEC and SPEC.loader
applier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(applier)


class KnowledgeApplierTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / ".ai").mkdir()
        (self.root / "Knowledge/Code").mkdir(parents=True)
        (self.root / "(Game Name)").mkdir()
        shutil.copy2(AI_DIR / "change_inspector.py", self.root / ".ai/change_inspector.py")
        shutil.copy2(AI_DIR / "knowledge_updater.py", self.root / ".ai/knowledge_updater.py")
        self.knowledge_path = self.root / "Knowledge/Code/Code Inventory.md"
        self.knowledge_bytes = b"# Code Inventory\n\n## Tracked code\n\nNo script details listed yet.\n"
        self.knowledge_path.write_bytes(self.knowledge_bytes)
        (self.root / "(Game Name)/project.godot").write_text(
            '[application]\nconfig/name="Applier fixture"\n', encoding="utf-8")
        (self.root / "(Game Name)/Player.gd").write_text("extends Node2D\n", encoding="utf-8")

        self.git("init", "--quiet")
        self.git("config", "user.name", "Knowledge Applier Test")
        self.git("config", "user.email", "applier-test@example.invalid")
        self.git("add", "(Game Name)", "Knowledge", ".ai")
        self.git("commit", "--quiet", "-m", "fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.inspector = applier.load_updater(self.root).load_inspector(self.root)
        self.facts, *_ = applier.load_updater(self.root).project_facts(self.root, self.commit, self.inspector)
        self.fact = next(fact for fact in self.facts if fact["category"] == "script_file")
        self.proposal = self.root / f".ai/proposals/{self.commit}-knowledge-update-fixture.md"
        self.proposal.parent.mkdir(parents=True)
        self.proposal.write_text(self.proposal_text(), encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def git(self, *args):
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = "Knowledge Applier Test"
        env["GIT_AUTHOR_EMAIL"] = "applier-test@example.invalid"
        env["GIT_COMMITTER_NAME"] = "Knowledge Applier Test"
        env["GIT_COMMITTER_EMAIL"] = "applier-test@example.invalid"
        result = subprocess.run(["git", *args], cwd=self.root, env=env,
                                capture_output=True, text=True, check=False)
        if result.returncode:
            raise AssertionError(result.stderr)
        return result.stdout

    def proposal_text(self):
        fingerprint = self.inspector.implementation_fingerprint()
        source = self.fact["source"]
        statement = self.fact["statement"]
        baseline = hashlib.sha256(self.knowledge_bytes).hexdigest()
        return f'''# Knowledge Update Proposal

## Final Status

`UPDATE_PROPOSED`

## Commit

- Commit: `{self.commit}`
- Current Inspector fingerprint: `sha256:{fingerprint}`

## Knowledge Files Affected

### `Knowledge/Code/Code Inventory.md`

- NotebookLM recommendation: list the verified script.
- Evidence:
  - {statement} Source: `{source}` at `{self.commit}`; missing from this Knowledge file.

## Application Plan

### `Knowledge/Code/Code Inventory.md`
- Base SHA-256: `{baseline}`
- Insert after: `## Tracked code`
- Context: The tracked code section for concise script inventory facts.
- Verified fact: {statement}
'''

    def test_dry_run_validates_without_writing(self):
        before = self.knowledge_path.read_bytes()

        outcome = applier.apply_proposal(self.root, self.proposal, dry_run=True)

        self.assertEqual(outcome["status"], "READY")
        self.assertFalse(outcome["writes"])
        self.assertEqual(self.knowledge_path.read_bytes(), before)
        self.assertFalse((self.root / ".ai/state/knowledge_applications.json").exists())
        self.assertFalse((self.root / ".ai/applications").exists())

    def test_apply_is_atomic_recorded_and_idempotent(self):
        first = applier.apply_proposal(self.root, self.proposal)
        content = self.knowledge_path.read_text(encoding="utf-8")
        second = applier.apply_proposal(self.root, self.proposal)

        self.assertEqual(first["status"], "APPLIED")
        self.assertEqual(second["status"], "ALREADY_APPLIED")
        self.assertEqual(content.count("<!-- knowledge-applier:"), 1)
        self.assertEqual(content.count(self.fact["statement"]), 1)
        self.assertTrue((self.root / ".ai/state/knowledge_applications.json").is_file())
        self.assertTrue((self.root / first["application_record"]).is_file())

    def test_target_change_after_proposal_is_refused(self):
        self.knowledge_path.write_bytes(self.knowledge_bytes + b"\nManual edit.\n")

        with self.assertRaisesRegex(applier.ApplierError, "changed since proposal baseline"):
            applier.apply_proposal(self.root, self.proposal, dry_run=True)

    def test_project_change_after_commit_is_refused(self):
        (self.root / "(Game Name)/Player.gd").write_text("extends CharacterBody2D\n", encoding="utf-8")

        with self.assertRaisesRegex(applier.ApplierError, "project working tree"):
            applier.apply_proposal(self.root, self.proposal, dry_run=True)

    def test_legacy_proposal_without_application_plan_is_refused(self):
        self.proposal.write_text(self.proposal_text().split("## Application Plan")[0], encoding="utf-8")

        with self.assertRaisesRegex(applier.ApplierError, "no `## Application Plan`"):
            applier.apply_proposal(self.root, self.proposal, dry_run=True)

    def test_plan_may_omit_reviewed_target_without_safe_additions(self):
        text = self.proposal_text()
        extra_evidence = (
            "### `Knowledge/Systems/Systems Inventory.md`\n\n"
            "- NotebookLM recommendation: review the system inventory.\n"
            "- Evidence:\n"
            f"  - {self.fact['statement']} Source: `{self.fact['source']}` at `{self.commit}`; missing from this Knowledge file.\n\n"
        )
        text = text.replace("## Application Plan", extra_evidence + "## Application Plan", 1)
        self.proposal.write_text(text, encoding="utf-8")

        outcome = applier.apply_proposal(self.root, self.proposal, dry_run=True)

        self.assertEqual(outcome["status"], "READY")
        self.assertEqual([change["path"] for change in outcome["changes"]], ["Knowledge/Code/Code Inventory.md"])

    def test_application_plan_requires_context_metadata(self):
        self.proposal.write_text(self.proposal_text().replace(
            "- Context: The tracked code section for concise script inventory facts.\n", ""), encoding="utf-8")

        with self.assertRaisesRegex(applier.ApplierError, "context"):
            applier.apply_proposal(self.root, self.proposal, dry_run=True)


class KnowledgeApplierReplaceTests(unittest.TestCase):
    commit = "5472bacbe12818379b9a20771885b63ea4725ea0"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / ".ai/proposals").mkdir(parents=True)
        (self.root / ".ai/state").mkdir()
        (self.root / ".ai/applications").mkdir()
        (self.root / "Knowledge/Systems").mkdir(parents=True)
        (self.root / "(Game Name)").mkdir()
        shutil.copy2(AI_DIR / "change_inspector.py", self.root / ".ai/change_inspector.py")
        shutil.copy2(AI_DIR / "knowledge_updater.py", self.root / ".ai/knowledge_updater.py")
        self.knowledge_path = self.root / updater.SYSTEMS_REPLACEMENT_TARGET
        self.original = (
            "# Systems Inventory\n\n## Current status\n\n"
            + updater.SYSTEMS_REPLACEMENT_OLD_PASSAGE
            + "\n\n## Not established\n\nNo further system facts are recorded.\n"
        ).encode("utf-8")
        self.knowledge_path.write_bytes(self.original)
        self.inspector = updater.load_inspector(AI_DIR.parent)
        self.facts, *_ = updater.project_facts(AI_DIR.parent, self.commit, self.inspector)
        self.operation = updater.build_systems_replacement_operation(self.root, self.commit, self.facts)
        self.proposal = self.root / ".ai/proposals/5472-replace-test.md"
        self.write_proposal()
        self.patches = [
            patch.object(applier, "load_updater", return_value=updater),
            patch.object(updater, "project_facts", side_effect=self.fake_project_facts),
            patch.object(applier, "git", side_effect=self.fake_git),
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def fake_project_facts(self, root, sha, inspector_obj):
        return self.facts, "", "", []

    def fake_git(self, root, *args, check=True):
        if args[:2] == ("rev-parse", "--verify"):
            return self.commit
        if args[:2] == ("rev-parse", "HEAD"):
            return self.commit
        if args and args[0] == "status":
            return ""
        raise AssertionError(f"Unexpected Git invocation: {args}")

    def write_proposal(self, operation=None, *, headings=True):
        operation = operation or self.operation
        support = operation.get("selected_verified_facts", [])
        evidence = [
            "### `Knowledge/Systems/Systems Inventory.md`", "", "- NotebookLM recommendation: update stale status.",
            "- Evidence:",
        ]
        evidence.extend(
            f"  - {fact['statement']} Source: `{fact['source']}` at `{self.commit}`; missing from this Knowledge file."
            for fact in support
        )
        plan = updater.render_application_plan([], [operation])
        if not headings:
            plan = [line for line in plan if not line.startswith("### REPLACE operation")]
        text = "\n".join([
            "# Knowledge Update Proposal", "", "## Final Status", "", "`UPDATE_PROPOSED`", "",
            "## Commit", "", f"- Commit: `{self.commit}`",
            f"- Current Inspector fingerprint: `sha256:{self.inspector.implementation_fingerprint()}`", "",
            "## Knowledge Files Affected", "", *evidence, "", *plan, "",
        ])
        self.proposal.write_text(text, encoding="utf-8", newline="\n")

    def run_apply(self, *, dry_run=False):
        with self.patches[0], self.patches[1], self.patches[2]:
            return applier.apply_proposal(self.root, self.proposal, dry_run=dry_run)

    def rehash_operation(self, operation):
        operation["old_passage_sha256"] = updater.sha256(operation["exact_old_passage"].encode("utf-8"))
        operation["replacement_sha256"] = updater.sha256(operation["replacement_markdown"].encode("utf-8"))
        operation["operation_id"] = updater.sha256("\0".join((
            self.commit, operation["path"], operation["old_passage_sha256"],
            operation["replacement_sha256"],
        )).encode("utf-8"))[:16]

    def assert_refused_unchanged(self):
        before = self.knowledge_path.read_bytes()
        with self.assertRaises(applier.ApplierError):
            self.run_apply()
        self.assertEqual(self.knowledge_path.read_bytes(), before)
        self.assertFalse((self.root / ".ai/state/knowledge_applications.json").exists())

    def test_replace_dry_run_is_ready_and_non_mutating(self):
        result = self.run_apply(dry_run=True)
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["writes"])
        self.assertEqual(self.knowledge_path.read_bytes(), self.original)
        self.assertFalse((self.root / ".ai/state/knowledge_applications.json").exists())

    def test_replace_apply_is_narrowly_recorded_and_second_apply_is_idempotent(self):
        result = self.run_apply()
        after = self.knowledge_path.read_bytes()
        second = self.run_apply()
        expected = self.original.replace(
            self.operation["exact_old_passage"].encode("utf-8"),
            self.operation["replacement_markdown"].encode("utf-8"), 1)
        self.assertEqual(result["status"], "APPLIED")
        self.assertTrue(result["writes"])
        self.assertEqual(after, expected)
        self.assertEqual(second["status"], "ALREADY_APPLIED")
        self.assertFalse(second["writes"])
        state = json.loads((self.root / ".ai/state/knowledge_applications.json").read_text(encoding="utf-8"))
        application = next(iter(state["applications"].values()))
        self.assertEqual(application["operation_type"], "REPLACE")
        self.assertEqual(application["operation_id"], self.operation["operation_id"])
        self.assertEqual(application["changes"][0]["after_sha256"], updater.sha256(after))

    def test_changed_whole_file_baseline_is_refused(self):
        self.operation["file_baseline_sha256"] = "0" * 64
        self.write_proposal()
        self.assert_refused_unchanged()

    def test_changed_old_passage_is_refused(self):
        self.operation["exact_old_passage"] += " Extra."
        self.rehash_operation(self.operation)
        self.write_proposal()
        self.assert_refused_unchanged()

    def test_duplicate_old_passage_is_refused(self):
        self.knowledge_path.write_bytes(self.original + b"\n" + updater.SYSTEMS_REPLACEMENT_OLD_PASSAGE.encode())
        self.operation["file_baseline_sha256"] = updater.sha256(self.knowledge_path.read_bytes())
        self.write_proposal()
        self.assert_refused_unchanged()

    def test_invalid_old_passage_hash_is_refused(self):
        self.operation["old_passage_sha256"] = "0" * 64
        self.write_proposal()
        self.assert_refused_unchanged()

    def test_invalid_replacement_hash_is_refused(self):
        self.operation["replacement_sha256"] = "0" * 64
        self.write_proposal()
        self.assert_refused_unchanged()

    def test_unsupported_replacement_claim_is_refused(self):
        self.operation["replacement_markdown"] += " The player uses WASD."
        self.rehash_operation(self.operation)
        self.write_proposal()
        self.assert_refused_unchanged()

    def test_missing_or_unverified_fact_is_refused(self):
        self.operation["selected_verified_facts"].pop()
        self.write_proposal()
        self.assert_refused_unchanged()

    def test_missing_replace_field_is_refused(self):
        del self.operation["anchor"]
        self.write_proposal()
        self.assert_refused_unchanged()

    def test_unheaded_replace_data_cannot_fall_through_to_add(self):
        self.write_proposal(headings=False)
        before = self.knowledge_path.read_bytes()
        with self.assertRaisesRegex(applier.ApplierError, "without an explicit REPLACE"):
            self.run_apply()
        self.assertEqual(self.knowledge_path.read_bytes(), before)

    def test_add_plan_with_embedded_replace_data_is_refused_not_applied_as_add(self):
        plan = updater.render_application_plan([{
            "path": updater.SYSTEMS_REPLACEMENT_TARGET,
            "baseline_sha256": self.operation["file_baseline_sha256"],
            "anchor": "## Current status", "context": "test", "facts": ["unrelated ADD fact"],
        }], [self.operation])
        self.proposal.write_text("\n".join([
            "# Knowledge Update Proposal", "", "## Final Status", "", "`UPDATE_PROPOSED`", "",
            "## Commit", "", f"- Commit: `{self.commit}`",
            f"- Current Inspector fingerprint: `sha256:{self.inspector.implementation_fingerprint()}`", "",
            "## Knowledge Files Affected", "", "### `Knowledge/Systems/Systems Inventory.md`", "",
            "- Evidence:", *[f"  - {fact['statement']} Source: `{fact['source']}` at `{self.commit}`; missing from this Knowledge file."
                             for fact in self.operation["selected_verified_facts"]], "", *plan, "",
        ]), encoding="utf-8")
        before = self.knowledge_path.read_bytes()
        with self.assertRaisesRegex(applier.ApplierError, "cannot mix REPLACE and ADD"):
            self.run_apply()
        self.assertEqual(self.knowledge_path.read_bytes(), before)

    def test_replacement_requires_unique_anchor(self):
        self.knowledge_path.write_bytes(self.original.replace(
            b"## Not established", b"## Current status\n\n## Not established"))
        self.operation["file_baseline_sha256"] = updater.sha256(self.knowledge_path.read_bytes())
        self.write_proposal()
        self.assert_refused_unchanged()

    def test_failed_validation_has_no_partial_application_record(self):
        self.operation["replacement_sha256"] = "f" * 64
        self.write_proposal()
        self.assert_refused_unchanged()
        self.assertEqual(list((self.root / ".ai/applications").iterdir()), [])


class KnowledgeApplierTargetSchemaTests(unittest.TestCase):
    commit = "5472bacbe12818379b9a20771885b63ea4725ea0"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / ".ai/proposals").mkdir(parents=True)
        (self.root / ".ai/state").mkdir()
        (self.root / ".ai/applications").mkdir()
        (self.root / "(Game Name)").mkdir()
        shutil.copy2(AI_DIR / "change_inspector.py", self.root / ".ai/change_inspector.py")
        shutil.copy2(AI_DIR / "knowledge_updater.py", self.root / ".ai/knowledge_updater.py")
        self.inspector = updater.load_inspector(AI_DIR.parent)
        self.facts, *_ = updater.project_facts(AI_DIR.parent, self.commit, self.inspector)
        self.patches = [
            patch.object(applier, "load_updater", return_value=updater),
            patch.object(updater, "project_facts", side_effect=self.fake_project_facts),
            patch.object(applier, "git", side_effect=self.fake_git),
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def fake_project_facts(self, root, sha, inspector_obj):
        return self.facts, "", "", []

    def fake_git(self, root, *args, check=True):
        if args[:2] == ("rev-parse", "--verify"):
            return self.commit
        if args[:2] == ("rev-parse", "HEAD"):
            return self.commit
        if args and args[0] == "status":
            return ""
        raise AssertionError(f"Unexpected Git invocation: {args}")

    def make_case(self, target, variant=None):
        schema = applier.replacement_schema_registry(updater)[target]
        if variant == "resources":
            schema = applier.resolve_replace_schema_variant(schema, applier.DATA_ASSETS_OTHER_OLD_PASSAGE)
        anchor_text = "\n\n".join((*schema.anchor, "", schema.old_passage, "", "This neighboring prose must remain unchanged.")) + "\n"
        knowledge = self.root / target
        knowledge.parent.mkdir(parents=True, exist_ok=True)
        original = anchor_text.encode("utf-8")
        knowledge.write_bytes(original)
        draft = applier.schema_expected_draft(schema, self.facts, updater)
        old_hash = updater.sha256(schema.old_passage.encode("utf-8"))
        replacement_hash = updater.sha256(draft["paragraph"].encode("utf-8"))
        operation = {
            "operation": "REPLACE", "commit_sha": self.commit, "path": target,
            "operation_id": updater.sha256("\0".join((self.commit, target, old_hash, replacement_hash)).encode("utf-8"))[:16],
            "file_baseline_sha256": updater.sha256(original),
            "exact_old_passage": schema.old_passage, "old_passage_sha256": old_hash,
            "anchor": {"heading_path": list(schema.anchor), "occurrence": 1},
            "replacement_markdown": draft["paragraph"], "replacement_sha256": replacement_hash,
            "selected_verified_facts": draft["supporting_facts"],
            "claim_to_fact_mapping": draft["claims"],
        }
        proposal = self.root / ".ai/proposals" / f"{target.split('/')[-1].replace('.md','')}-replace.md"
        evidence = [f"### `{target}`", "", "- Evidence:"]
        evidence.extend(
            f"  - {fact['statement']} Source: `{fact['source']}` at `{self.commit}`; missing from this Knowledge file."
            for fact in draft["supporting_facts"]
        )
        plan = updater.render_application_plan([], [operation])
        text = "\n".join([
            "# Knowledge Update Proposal", "", "## Final Status", "", "`UPDATE_PROPOSED`", "",
            "## Commit", "", f"- Commit: `{self.commit}`",
            f"- Current Inspector fingerprint: `sha256:{self.inspector.implementation_fingerprint()}`", "",
            "## Knowledge Files Affected", "", *evidence, "", *plan, "",
        ])
        proposal.write_text(text, encoding="utf-8", newline="\n")
        return schema, operation, knowledge, original, proposal

    def run_apply(self, proposal, *, dry_run=True):
        with self.patches[0], self.patches[1], self.patches[2]:
            return applier.apply_proposal(self.root, proposal, dry_run=dry_run)

    def rehash(self, operation):
        operation["old_passage_sha256"] = updater.sha256(operation["exact_old_passage"].encode("utf-8"))
        operation["replacement_sha256"] = updater.sha256(operation["replacement_markdown"].encode("utf-8"))
        operation["operation_id"] = updater.sha256("\0".join((
            self.commit, operation["path"], operation["old_passage_sha256"], operation["replacement_sha256"],
        )).encode("utf-8"))[:16]

    def refuse_without_writes(self, operation, knowledge, original, proposal):
        # Rewrite only the isolated fixture proposal after tampering with its parsed operation.
        plan = updater.render_application_plan([], [operation])
        text = proposal.read_text(encoding="utf-8")
        prefix = text.split("## Application Plan", 1)[0]
        proposal.write_text(prefix + "\n" + "\n".join(plan) + "\n", encoding="utf-8")
        with self.assertRaises(applier.ApplierError):
            self.run_apply(proposal)
        self.assertEqual(knowledge.read_bytes(), original)
        self.assertFalse((self.root / ".ai/state/knowledge_applications.json").exists())

    def test_project_overview_valid_replace_dry_run_is_ready(self):
        _schema, _operation, knowledge, original, proposal = self.make_case(applier.PROJECT_OVERVIEW_TARGET)
        result = self.run_apply(proposal)
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["writes"])
        self.assertEqual(knowledge.read_bytes(), original)
        self.assertEqual(len(result["changes"]), 1)

    def test_architecture_valid_replace_dry_run_is_ready(self):
        _schema, _operation, knowledge, original, proposal = self.make_case(applier.ARCHITECTURE_OVERVIEW_TARGET)
        result = self.run_apply(proposal)
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["writes"])
        self.assertEqual(knowledge.read_bytes(), original)

    def test_code_inventory_valid_replace_dry_run_is_ready(self):
        _schema, _operation, knowledge, original, proposal = self.make_case(applier.CODE_INVENTORY_TARGET)
        result = self.run_apply(proposal)
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["writes"])
        self.assertEqual(knowledge.read_bytes(), original)

    def test_code_inventory_unsupported_claim_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.CODE_INVENTORY_TARGET)
        operation["replacement_markdown"] += " Player uses WASD."
        self.rehash(operation)
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_code_inventory_stale_baseline_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.CODE_INVENTORY_TARGET)
        operation["file_baseline_sha256"] = "0" * 64
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_code_inventory_wrong_old_passage_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.CODE_INVENTORY_TARGET)
        operation["exact_old_passage"] += " Altered."
        self.rehash(operation)
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_code_inventory_second_application_is_idempotent(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.CODE_INVENTORY_TARGET)
        first = self.run_apply(proposal, dry_run=False)
        expected = original.replace(operation["exact_old_passage"].encode("utf-8"),
                                    operation["replacement_markdown"].encode("utf-8"), 1)
        self.assertEqual(first["status"], "APPLIED")
        self.assertTrue(first["writes"])
        self.assertEqual(knowledge.read_bytes(), expected)
        second = self.run_apply(proposal, dry_run=False)
        self.assertEqual(second["status"], "ALREADY_APPLIED")
        self.assertFalse(second["writes"])
        self.assertEqual(knowledge.read_bytes(), expected)

    def test_data_assets_main_scene_replace_validates(self):
        _schema, _operation, knowledge, original, proposal = self.make_case(applier.DATA_ASSETS_TARGET)
        result = self.run_apply(proposal)
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["writes"])
        self.assertEqual(knowledge.read_bytes(), original)

    def test_data_assets_resource_replace_is_idempotent(self):
        _schema, operation, knowledge, original, proposal = self.make_case(
            applier.DATA_ASSETS_TARGET, variant="resources")
        first = self.run_apply(proposal, dry_run=False)
        self.assertEqual(first["status"], "APPLIED")
        self.assertTrue(first["writes"])
        result_bytes = original.replace(operation["exact_old_passage"].encode(),
                                        operation["replacement_markdown"].encode(), 1)
        self.assertEqual(knowledge.read_bytes(), result_bytes)
        second = self.run_apply(proposal, dry_run=False)
        self.assertEqual(second["status"], "ALREADY_APPLIED")
        self.assertFalse(second["writes"])
        self.assertEqual(knowledge.read_bytes(), result_bytes)

    def test_data_assets_unsupported_claim_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(
            applier.DATA_ASSETS_TARGET, variant="resources")
        operation["replacement_markdown"] += " The camera follows the player."
        self.rehash(operation)
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_data_assets_missing_selected_fact_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(
            applier.DATA_ASSETS_TARGET, variant="resources")
        operation["selected_verified_facts"].pop()
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_data_assets_stale_baseline_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.DATA_ASSETS_TARGET)
        operation["file_baseline_sha256"] = "0" * 64
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_data_assets_altered_passage_or_anchor_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.DATA_ASSETS_TARGET)
        operation["exact_old_passage"] += " changed"
        self.rehash(operation)
        self.refuse_without_writes(operation, knowledge, original, proposal)

        _schema, operation, knowledge, original, proposal = self.make_case(applier.DATA_ASSETS_TARGET)
        operation["anchor"]["heading_path"][-1] = "### Wrong heading"
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_unknown_replace_target_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.PROJECT_OVERVIEW_TARGET)
        operation["path"] = "Knowledge/Other/Unknown.md"
        self.rehash(operation)
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_project_overview_unsupported_claim_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.PROJECT_OVERVIEW_TARGET)
        operation["replacement_markdown"] += " It uses WASD."
        self.rehash(operation)
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_project_overview_missing_selected_fact_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.PROJECT_OVERVIEW_TARGET)
        operation["selected_verified_facts"].pop()
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_architecture_unsupported_claim_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.ARCHITECTURE_OVERVIEW_TARGET)
        operation["replacement_markdown"] += " The camera follows the player."
        self.rehash(operation)
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_architecture_missing_selected_fact_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.ARCHITECTURE_OVERVIEW_TARGET)
        operation["selected_verified_facts"].pop()
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_incorrect_replacement_hash_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.PROJECT_OVERVIEW_TARGET)
        operation["replacement_sha256"] = "0" * 64
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_incorrect_baseline_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.ARCHITECTURE_OVERVIEW_TARGET)
        operation["file_baseline_sha256"] = "0" * 64
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_duplicate_old_passage_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.PROJECT_OVERVIEW_TARGET)
        duplicate = original + b"\n" + operation["exact_old_passage"].encode("utf-8") + b"\n"
        knowledge.write_bytes(duplicate)
        operation["file_baseline_sha256"] = updater.sha256(duplicate)
        self.refuse_without_writes(operation, knowledge, duplicate, proposal)

    def test_changed_old_passage_is_refused(self):
        _schema, operation, knowledge, original, proposal = self.make_case(applier.ARCHITECTURE_OVERVIEW_TARGET)
        operation["exact_old_passage"] += " Changed."
        self.rehash(operation)
        self.refuse_without_writes(operation, knowledge, original, proposal)

    def test_target_dry_run_does_not_create_application_records(self):
        for target in (applier.PROJECT_OVERVIEW_TARGET, applier.ARCHITECTURE_OVERVIEW_TARGET):
            with self.subTest(target=target):
                _schema, _operation, _knowledge, _original, proposal = self.make_case(target)
                result = self.run_apply(proposal)
                self.assertEqual(result["status"], "READY")
                self.assertFalse((self.root / ".ai/state/knowledge_applications.json").exists())
                self.assertEqual(list((self.root / ".ai/applications").iterdir()), [])


class DevelopmentConfigurationTableApplierTests(unittest.TestCase):
    commit = "5472bacbe12818379b9a20771885b63ea4725ea0"
    target = applier.DEVELOPMENT_CONFIGURATION_ADD_TARGET

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / ".ai/proposals").mkdir(parents=True)
        (self.root / "(Game Name)").mkdir()
        shutil.copy2(AI_DIR / "change_inspector.py", self.root / ".ai/change_inspector.py")
        shutil.copy2(AI_DIR / "knowledge_updater.py", self.root / ".ai/knowledge_updater.py")
        self.inspector = updater.load_inspector(AI_DIR.parent)
        self.facts, *_ = updater.project_facts(AI_DIR.parent, self.commit, self.inspector)
        self.original = b"# Development Configuration\n\n## Godot project settings\n\nSettings are listed here.\n\n## Input actions\n\nActions are listed here.\n"
        self.knowledge = self.root / self.target
        self.knowledge.parent.mkdir(parents=True, exist_ok=True)
        self.knowledge.write_bytes(self.original)
        self.proposal = self.root / ".ai/proposals/input-table.md"
        self.patches = [
            patch.object(applier, "load_updater", return_value=updater),
            patch.object(updater, "project_facts", side_effect=self.fake_project_facts),
            patch.object(applier, "git", side_effect=self.fake_git),
        ]
        self.write_proposal()

    def tearDown(self):
        self.temp_dir.cleanup()

    def fake_project_facts(self, root, sha, inspector_obj):
        return self.facts, "", "", []

    def fake_git(self, root, *args, check=True):
        if args[:2] in (("rev-parse", "--verify"), ("rev-parse", "HEAD")):
            return self.commit
        if args and args[0] == "status":
            return ""
        raise AssertionError(f"Unexpected Git invocation: {args}")

    def make_plan_data(self, markdown=None, selected=None, mapping=None):
        expected = applier.development_configuration_table(self.facts, updater)
        markdown = expected["markdown"] if markdown is None else markdown
        selected = expected["selected_verified_facts"] if selected is None else selected
        mapping = expected["claim_to_fact_mapping"] if mapping is None else mapping
        return {
            "operation": "ADD", "renderer": applier.DEVELOPMENT_CONFIGURATION_ADD_RENDERER,
            "rendered_markdown": markdown,
            "rendered_markdown_sha256": updater.sha256(markdown.encode("utf-8")),
            "selected_verified_facts": selected, "claim_to_fact_mapping": mapping,
        }

    def write_proposal(self, *, metadata=None, baseline=None, table_facts=None):
        expected = applier.development_configuration_table(self.facts, updater)
        facts = table_facts or expected["selected_verified_facts"]
        metadata = metadata or self.make_plan_data()
        baseline = baseline or updater.sha256(self.original)
        evidence = [f"### `{self.target}`", "", "- Evidence:"]
        evidence.extend(
            f"  - {fact['statement']} Source: `{fact['source']}` at `{self.commit}`; missing from this Knowledge file."
            for fact in facts
        )
        plan = [
            "## Application Plan", "", f"### `{self.target}`",
            f"- Base SHA-256: `{baseline}`",
            f"- Insert after: `{applier.DEVELOPMENT_CONFIGURATION_ADD_ANCHOR}`",
            "- Context: Input-action configuration table.",
            *[f"- Verified fact: {fact['statement']}" for fact in facts],
            f"- Operation: {metadata['operation']}",
            f"- Renderer: `{metadata['renderer']}`", "- Renderer data:",
            "```json", json.dumps(metadata, ensure_ascii=False, sort_keys=True), "```",
        ]
        self.proposal.write_text("\n".join([
            "# Knowledge Update Proposal", "", "## Final Status", "", "`UPDATE_PROPOSED`", "",
            "## Commit", "", f"- Commit: `{self.commit}`",
            f"- Current Inspector fingerprint: `sha256:{self.inspector.implementation_fingerprint()}`", "",
            "## Knowledge Files Affected", "", *evidence, "", *plan, "",
        ]), encoding="utf-8", newline="\n")

    def run_apply(self, *, dry_run=False):
        with self.patches[0], self.patches[1], self.patches[2]:
            return applier.apply_proposal(self.root, self.proposal, dry_run=dry_run)

    def test_valid_table_application_is_atomic_and_idempotent(self):
        ready = self.run_apply(dry_run=True)
        self.assertEqual(ready["status"], "READY")
        self.assertFalse(ready["writes"])
        self.assertEqual(self.knowledge.read_bytes(), self.original)
        self.assertFalse((self.root / ".ai/state/knowledge_applications.json").exists())

        applied = self.run_apply()
        self.assertEqual(applied["status"], "APPLIED")
        self.assertTrue(applied["writes"])
        expected = self.original.decode("utf-8")
        begin, end = applier.marker_for(applier.sha256(self.proposal.read_bytes()))
        expected = applier.section_insert_rendered_markdown(
            expected, applier.DEVELOPMENT_CONFIGURATION_ADD_ANCHOR,
            applier.development_configuration_table(self.facts, updater)["markdown"], begin, end,
        )
        self.assertEqual(self.knowledge.read_text(encoding="utf-8"), expected)

        second = self.run_apply()
        self.assertEqual(second["status"], "ALREADY_APPLIED")
        self.assertFalse(second["writes"])
        self.assertEqual(self.knowledge.read_text(encoding="utf-8"), expected)

    def test_altered_table_claim_is_refused(self):
        expected = applier.development_configuration_table(self.facts, updater)
        altered = expected["markdown"].replace("physical key S (83)", "physical key A (65)")
        self.write_proposal(metadata=self.make_plan_data(markdown=altered))
        before = self.knowledge.read_bytes()
        with self.assertRaisesRegex(applier.ApplierError, "altered or unsupported"):
            self.run_apply(dry_run=True)
        self.assertEqual(self.knowledge.read_bytes(), before)

    def test_unsupported_claim_is_refused(self):
        expected = applier.development_configuration_table(self.facts, updater)
        altered = expected["markdown"] + "\n| player_controls | WASD |"
        self.write_proposal(metadata=self.make_plan_data(markdown=altered))
        with self.assertRaisesRegex(applier.ApplierError, "altered or unsupported"):
            self.run_apply(dry_run=True)

    def test_stale_baseline_is_refused(self):
        self.write_proposal(baseline="0" * 64)
        with self.assertRaisesRegex(applier.ApplierError, "baseline"):
            self.run_apply(dry_run=True)

    def test_row_without_selected_fact_mapping_is_refused(self):
        expected = applier.development_configuration_table(self.facts, updater)
        mapping = expected["claim_to_fact_mapping"][:-1]
        self.write_proposal(metadata=self.make_plan_data(mapping=mapping))
        with self.assertRaisesRegex(applier.ApplierError, "row-to-fact mapping"):
            self.run_apply(dry_run=True)


if __name__ == "__main__":
    unittest.main()
