"""Behavioral tests with synthetic projects. Never touch real Codex history."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import re
import unittest
import uuid
from unittest.mock import patch

SKILLS = Path(__file__).resolve().parents[1] / "skills"
SPEC = importlib.util.spec_from_file_location("dtc", SKILLS / "dtc1/scripts/dtc.py")
dtc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dtc)


def write_log(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) if isinstance(row, dict) else row for row in rows) + "\n", encoding="utf-8")


def meta(cwd, tid="fixture"):
    return {"type": "session_meta", "payload": {"id": tid, "cwd": str(cwd)}}


def message(text, role="user", channel=None):
    payload = {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}
    if channel:
        payload["channel"] = channel
    return {"type": "response_item", "timestamp": "2026-10-17T08:00:00Z", "payload": payload}


class DtcTest(unittest.TestCase):
    def setUp(self):
        self.run_root = Path(__file__).resolve().parent / "_runs"
        self.base = self.run_root / uuid.uuid4().hex
        self.base.mkdir(parents=True)
        self.root = self.base / "项目 with spaces"
        self.root.mkdir()
        self.home = self.base / "codex"
        self.log = self.home / "sessions" / "fixture.jsonl"
        write_log(self.log, [meta(self.root), message("Preserve 0.010 A; timeout 12 ms. Constraint: no deployment.")])

    def tearDown(self):
        if self.base.resolve().parent != self.run_root.resolve():
            raise RuntimeError("Unexpected test cleanup target")
        shutil.rmtree(self.base)

    def capture(self, ids=None):
        args = argparse.Namespace(project=str(self.root), codex_home=str(self.home), alias=[], command="collect", thread_id=ids or ["fixture"], label=None)
        return Path(dtc.collect(args)["snapshot"])

    def author(self, snap):
        manifest = dtc.read_json(snap / "manifest.json")
        sources = [s["file"] for s in manifest["sources"]]
        ref = [{"file": sources[0], "anchor": "L2"}]
        ledger = {"schema_version": 1, "project_root": str(self.root),
                  "items": [{"id": "V1", "kind": "value", "text": "Current 0.010 A", "value": "0.010", "unit": "A", "as_of": "source not dated", "value_type": "original", "state": "active", "basis": "user_confirmed", "refs": ref}],
                  "tasks": [{"id": "T1", "text": "Check timeout implementation", "status": "in_progress", "next_action": "Inspect timeout constant", "refs": ref}],
                  "next_task_id": "T1", "read_next": ["MEMORY.md"],
                  "recall_checks": [{"question": "What is current?", "answer": "0.010 A", "item_ids": ["V1"]}]}
        review = {"schema_version": 1, "read_files": sources, "checked_item_ids": ["V1", "T1"], "coverage": "partial", "gaps": ["Tool evidence not included"], "source_inventory_notes": "Synthetic local sessions", "semantic_review": "Fixture checked"}
        dtc.write_json(snap / "ledger.json", ledger)
        dtc.write_json(snap / "review.json", review)
        (snap / "START_HERE.md").write_text("Resume T1; current 0.010 A; no deployment", encoding="utf-8")
        (snap / "MEMORY.md").write_text("12 ms timeout; inspect constant. Source L2", encoding="utf-8")
        return ledger, review

    def test_selected_chat_archive_and_directory_moves_preserve_full_history(self):
        other = self.base / "unrelated"
        write_log(self.home / "sessions" / "foreign.jsonl", [meta(other, "unselected"), message("FOREIGN_SECRET")])
        write_log(self.home / "archived_sessions" / "old.jsonl", [meta(self.root), message("ARCHIVED_TASK")])
        write_log(self.home / "sessions" / "moved.jsonl", [meta(other), message("BEFORE_MOVE"),
                  {"type": "turn_context", "payload": {"cwd": str(self.root)}}, message("PROJECT_SEGMENT"),
                  {"type": "turn_context", "payload": {"cwd": str(other)}}, message("AFTER_MOVE")])
        snap = self.capture()
        texts = "\n".join(p.read_text(encoding="utf-8") for p in (snap / "sources").glob("*.md"))
        self.assertIn("0.010 A", texts)
        self.assertIn("12 ms", texts)
        self.assertIn("ARCHIVED_TASK", texts)
        self.assertIn("PROJECT_SEGMENT", texts)
        self.assertIn("BEFORE_MOVE", texts)
        self.assertIn("AFTER_MOVE", texts)
        self.assertNotIn("FOREIGN_", texts)
        self.assertFalse((dtc.thread_home(self.root, "fixture") / "CURRENT.json").exists())

    def test_secret_redaction_and_reasoning_tool_exclusion(self):
        write_log(self.log, [meta(self.root), message("sk-" + "a" * 30), message("PRIVATE_REASONING", "assistant", "analysis"),
                  {"type": "response_item", "payload": {"type": "function_call_output", "output": "TOOL_SECRET"}},
                  message("PUBLIC_MESSAGE", "assistant")])
        snap = self.capture()
        text = next((snap / "sources").glob("*.md")).read_text(encoding="utf-8")
        self.assertNotIn("a" * 30, text)
        self.assertNotIn("PRIVATE_REASONING", text)
        self.assertNotIn("TOOL_SECRET", text)
        self.assertIn("PUBLIC_MESSAGE", text)
        self.assertIn("REDACTED_CREDENTIAL", text)

    def test_event_fallback_preserves_repeated_user_prompts(self):
        event = {"type": "event_msg", "payload": {"type": "user_message", "message": "repeat"}}
        write_log(self.log, [meta(self.root), event, message("repeat"), event])
        snap = self.capture()
        text = next((snap / "sources").glob("*.md")).read_text(encoding="utf-8")
        self.assertEqual(text.count("\nrepeat\n"), 2)

    def test_malformed_line_is_a_gap_but_does_not_truncate_selected_chat(self):
        write_log(self.log, [meta(self.root), message("SAFE"), "{broken", message("RECOVERABLE_SEGMENT"),
                  {"type": "turn_context", "payload": {"cwd": str(self.root)}}, message("SAFE_AGAIN")])
        snap = self.capture()
        text = next((snap / "sources").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("RECOVERABLE_SEGMENT", text)
        self.assertIn("SAFE_AGAIN", text)
        self.assertTrue(dtc.read_json(snap / "manifest.json")["collection_gaps"])

    def test_publish_verify_and_tamper_detection(self):
        snap = self.capture()
        self.author(snap)
        dtc.publish(snap)
        folder, pointer = dtc.locate(self.root, None)
        self.assertEqual(snap, folder)
        self.assertIsNotNone(pointer)
        dtc.validate(folder, sealed=True)
        (folder / "MEMORY.md").write_text("Tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            dtc.validate(folder, sealed=True)

    def test_incomplete_source_review_or_values_block_publish(self):
        snap = self.capture()
        ledger, review = self.author(snap)
        review["read_files"] = []
        dtc.write_json(snap / "review.json", review)
        with self.assertRaisesRegex(ValueError, "not marked fully read"):
            dtc.publish(snap)
        self.assertIsNone(dtc.current(self.root))
        ledger, review = self.author(snap)
        ledger["items"][0]["value"] = 0.010
        dtc.write_json(snap / "ledger.json", ledger)
        with self.assertRaisesRegex(ValueError, "exact string"):
            dtc.publish(snap)

    def test_bad_refs_and_false_done_block_publish(self):
        snap = self.capture()
        ledger, _ = self.author(snap)
        ledger["items"][0]["refs"][0]["file"] = "../../../../outside.md"
        dtc.write_json(snap / "ledger.json", ledger)
        with self.assertRaisesRegex(ValueError, "leaves package"):
            dtc.validate(snap)
        ledger, _ = self.author(snap)
        ledger["tasks"][0]["status"] = "done"
        ledger["next_task_id"] = None
        dtc.write_json(snap / "ledger.json", ledger)
        with self.assertRaisesRegex(ValueError, "missing verification"):
            dtc.publish(snap)

    def test_ids_cannot_disappear_in_next_generation(self):
        old = self.capture()
        self.author(old)
        dtc.publish(old)
        new = self.capture()
        ledger, review = self.author(new)
        ledger["tasks"] = []
        ledger["next_task_id"] = None
        dtc.write_json(new / "ledger.json", ledger)
        with self.assertRaisesRegex(ValueError, "silently dropped"):
            dtc.publish(new)
        self.assertEqual(dtc.locate(self.root, None)[0], old)

    def test_concurrent_publish_keeps_first_pointer(self):
        one, two = self.capture(), self.capture()
        self.author(one)
        self.author(two)
        dtc.publish(one)
        with self.assertRaisesRegex(ValueError, "CURRENT advanced"):
            dtc.publish(two)
        self.assertEqual(dtc.locate(self.root, None)[0], one)
        self.assertFalse((dtc.thread_home(self.root, "fixture") / "PUBLISH.lock").exists())

    def test_lock_collision_and_project_drift(self):
        snap = self.capture()
        self.author(snap)
        lock = dtc.thread_home(self.root, "fixture") / "PUBLISH.lock"
        lock.write_text("Other live process", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "lock exists"):
            dtc.publish(snap)
        self.assertEqual(lock.read_text(), "Other live process")

    @unittest.skipUnless(shutil.which("git"), "Git not installed")
    def test_dirty_git_content_drift_even_when_status_same(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        target = self.root / "source.txt"
        target.write_text("initial", encoding="utf-8")
        old = dtc.git_state(self.root)
        snap = self.capture()
        self.author(snap)
        target.write_text("changed without changing filename/status", encoding="utf-8")
        self.assertNotEqual(old, dtc.git_state(self.root))
        with self.assertRaisesRegex(ValueError, "Project changed"):
            dtc.publish(snap)
        self.assertIsNone(dtc.current(self.root))

    def test_dtc2_runs_standalone_and_detects_extra_file(self):
        snap = self.capture()
        self.author(snap)
        dtc.publish(snap)
        result = subprocess.run([os.sys.executable, str(SKILLS / "dtc2/scripts/dtc.py"), "verify", "--project", str(self.root)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["next_task_id"], "T1")
        (snap / "unsealed-file.md").write_text("unexpected", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "file set changed"):
            dtc.validate(snap, sealed=True)

    def test_incomplete_attachment_is_reported(self):
        attachment = message("Image defines the pinout")
        attachment["payload"]["content"].append({"type": "input_image", "image_url": "fixture-only"})
        write_log(self.log, [meta(self.root), attachment])
        snap = self.capture()
        self.author(snap)
        review = dtc.read_json(snap / "review.json")
        review["coverage"] = "accessible_selected_sources"
        dtc.write_json(snap / "review.json", review)
        with self.assertRaisesRegex(ValueError, "Collector gaps"):
            dtc.publish(snap)

    def test_manifest_pointer_tampering_is_detected(self):
        snap = self.capture()
        self.author(snap)
        dtc.publish(snap)
        manifest = dtc.read_json(snap / "manifest.json")
        manifest["created_at"] = "modified"
        dtc.write_json(snap / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "CURRENT manifest"):
            dtc.locate(self.root, None)

    def test_large_messages_are_not_truncated_and_root_prefix_is_not_scope(self):
        payload = "A" * 65000 + "END_SENTINEL"
        write_log(self.log, [meta(self.root), message(payload)])
        lookalike = Path(str(self.root) + "-other")
        write_log(self.home / "sessions/lookalike.jsonl", [meta(lookalike, "lookalike"), message("OUTSIDE_SCOPE")])
        snap = self.capture()
        files = sorted((snap / "sources").glob("*.md"))
        text = "\n".join(p.read_text(encoding="utf-8") for p in files)
        self.assertGreater(len(files), 1)
        self.assertIn("END_SENTINEL", text)
        self.assertNotIn("OUTSIDE_SCOPE", text)

    def test_credentials_redaction_is_not_a_numeric_value_change(self):
        text, count = dtc.redact("limit=0.010 A; timeout=12 ms; password=supersecret123")
        self.assertEqual(count, 1)
        self.assertIn("0.010 A", text)
        self.assertNotIn("supersecret123", text)

    def test_exact_source_line_anchor_does_not_match_longer_number(self):
        snap = self.capture()
        ledger, _ = self.author(snap)
        source = snap / ledger["items"][0]["refs"][0]["file"]
        source.write_text(source.read_text(encoding="utf-8").replace("## L2 ", "## L20 "), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "anchor not found"):
            dtc.validate(snap)

    def test_default_current_chat_excludes_other_chats_in_same_project(self):
        write_log(self.home / "sessions/neighbor.jsonl", [meta(self.root, "neighbor"), message("SAME_PROJECT_NEIGHBOR")])
        args = argparse.Namespace(project=str(self.root), codex_home=str(self.home), alias=[], command="collect", thread_id=None)
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "fixture"}):
            snap = Path(dtc.collect(args)["snapshot"])
        manifest = dtc.read_json(snap / "manifest.json")
        self.assertEqual(manifest["scope"]["thread_ids"], ["fixture"])
        texts = "\n".join(p.read_text(encoding="utf-8") for p in (snap / "sources").glob("*.md"))
        self.assertNotIn("SAME_PROJECT_NEIGHBOR", texts)

    def test_unknown_current_chat_id_never_falls_back_to_whole_project(self):
        args = argparse.Namespace(project=str(self.root), codex_home=str(self.home), alias=[], command="collect", thread_id=None)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Select at least one"):
                dtc.collect(args)
        self.assertFalse((self.root / ".dtc").exists())

    def test_explicit_multi_chat_merge_excludes_third_chat(self):
        write_log(self.home / "sessions/b.jsonl", [meta(self.root, "chat-b"), message("B_EARLY"), message("B_LATE")])
        write_log(self.home / "sessions/c.jsonl", [meta(self.root, "chat-c"), message("UNSELECTED_C")])
        snap = self.capture(["fixture", "chat-b"])
        self.author(snap)
        dtc.publish(snap)
        texts = "\n".join(p.read_text(encoding="utf-8") for p in (snap / "sources").glob("*.md"))
        self.assertIn("0.010 A", texts)
        self.assertIn("B_EARLY", texts)
        self.assertIn("B_LATE", texts)
        self.assertNotIn("UNSELECTED_C", texts)
        self.assertEqual(dtc.selection_key(["fixture", "chat-b"]), dtc.selection_key(["chat-b", "fixture", "fixture"]))
        folder, _ = dtc.locate(self.root, None, ["chat-b", "fixture"])
        self.assertEqual(folder, snap)

    def test_single_and_multi_bundles_coexist_without_guessing_or_overwrite(self):
        write_log(self.home / "sessions/b.jsonl", [meta(self.root, "chat-b"), message("B")])
        single = self.capture()
        self.author(single)
        dtc.publish(single)
        group = self.capture(["fixture", "chat-b"])
        self.author(group)
        dtc.publish(group)
        self.assertEqual(dtc.locate(self.root, None, ["fixture"])[0], single)
        self.assertEqual(dtc.locate(self.root, None, ["chat-b", "fixture"])[0], group)
        with self.assertRaisesRegex(ValueError, "cannot choose"):
            dtc.locate(self.root, None)
        self.assertEqual(len(dtc.list_handoffs(self.root)["handoffs"]), 2)

    def test_publish_rejects_source_from_unselected_chat(self):
        snap = self.capture()
        self.author(snap)
        manifest = dtc.read_json(snap / "manifest.json")
        manifest["sources"][0]["thread_id"] = "neighbor"
        dtc.write_json(snap / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "unselected"):
            dtc.publish(snap)

    def test_missing_selected_chat_cannot_be_claimed_fully_read(self):
        snap = self.capture(["fixture", "not-available"])
        self.author(snap)
        manifest = dtc.read_json(snap / "manifest.json")
        self.assertTrue(any(g.get("thread_id") == "not-available" for g in manifest["collection_gaps"]))
        review = dtc.read_json(snap / "review.json")
        review["coverage"] = "accessible_selected_sources"
        dtc.write_json(snap / "review.json", review)
        with self.assertRaisesRegex(ValueError, "Collector gaps"):
            dtc.publish(snap)


class PackagingTest(unittest.TestCase):
    def test_standalone_engines_are_identical(self):
        self.assertEqual((SKILLS / "dtc1/scripts/dtc.py").read_bytes(), (SKILLS / "dtc2/scripts/dtc.py").read_bytes())

    def test_manifest_yaml_subset_and_relative_references(self):
        # These files intentionally use plain scalar frontmatter and JSON-quoted YAML UI values.
        for name in ("dtc1", "dtc2"):
            root = SKILLS / name
            text = (root / "SKILL.md").read_text(encoding="utf-8")
            match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
            self.assertIsNotNone(match)
            fields = dict(line.split(": ", 1) for line in match[1].splitlines())
            self.assertEqual(set(fields), {"name", "description"})
            self.assertEqual(fields["name"], name)
            self.assertLessEqual(len(fields["description"]), 1024)
            self.assertNotRegex(fields["description"], r"[<>\n]")
            for ref in re.findall(r"\]\((references/[^)]+)\)", text):
                self.assertTrue((root / ref).is_file(), ref)
            ui = (root / "agents/openai.yaml").read_text(encoding="utf-8")
            values = {}
            for line in ui.splitlines():
                if line.startswith("  "):
                    key, value = line.strip().split(": ", 1)
                    values[key] = json.loads(value)
            self.assertIn("$" + name, values["default_prompt"])
            self.assertTrue(25 <= len(values["short_description"]) <= 64)
            self.assertIs(values["allow_implicit_invocation"], True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
