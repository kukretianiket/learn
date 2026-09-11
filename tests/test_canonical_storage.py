import importlib.util
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import uuid
from unittest import mock

from tests.common import LEARNCTL, ROOT, LearnTestCase


class CanonicalStorageTests(LearnTestCase):
    def load_learnctl(self, name):
        spec = importlib.util.spec_from_file_location(name, ROOT / ".agents/skills/learn/scripts/learnctl.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def canonical(self, lesson_id=None):
        paths = list((self.vault / "Learning" / "_system" / "lessons").glob("*.json"))
        if lesson_id:
            paths = [path for path in paths if path.stem == lesson_id]
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text(encoding="utf-8"))

    def test_start_creates_uuid_lesson_and_small_exact_binding(self):
        started = self.start(session="conversation-a", prompt="$learn canonical storage")
        self.assertEqual(str(uuid.UUID(started["lesson_id"])), started["lesson_id"])
        lesson = self.canonical(started["lesson_id"])
        self.assertEqual(lesson["conversation_binding"]["session_id"], "conversation-a")
        self.assertEqual(lesson["messages"][0]["markdown"], "$learn canonical storage")
        self.assertEqual(lesson["messages"][0]["event_identity"]["turn_id"], "turn-1")
        binding_path = next((self.vault / "Learning" / "_system" / "active").glob("*.json"))
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        self.assertEqual(set(binding), {"schema_version", "lesson_id", "session_id", "bound_at"})
        self.assertEqual(binding["lesson_id"], lesson["lesson_id"])

    def test_model_command_uses_verified_runtime_conversation_id_from_unrelated_directory(self):
        session_id = "3ad5f65e-0f71-47d7-a363-8fe4d2760a56"
        self.hook("UserPromptSubmit", session_id, "turn-1", "$learn exact runtime identity")
        unrelated = self.cwd.parent / "unrelated"
        unrelated.mkdir()
        env = self.env()
        env["CODEX_THREAD_ID"] = session_id
        env["CODEX_SESSION_ID"] = session_id
        result = subprocess.run(
            [sys.executable, str(LEARNCTL), "start", "--title", "Runtime ID", "--goal", "Exact binding"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(unrelated),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["session_id"], session_id)

    def test_duplicate_is_noop_conflicting_replay_preserves_original(self):
        started = self.start()
        self.hook("UserPromptSubmit", "session-a", "turn-2", "original answer")
        self.hook("UserPromptSubmit", "session-a", "turn-2", "original answer")
        self.hook("UserPromptSubmit", "session-a", "turn-2", "conflicting answer")
        lesson = self.canonical(started["lesson_id"])
        matching = [message for message in lesson["messages"] if message["event_identity"]["turn_id"] == "turn-2"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["markdown"], "original answer")
        health = json.loads(
            (self.vault / "Learning" / "_system" / "operational.json").read_text(encoding="utf-8")
        )
        self.assertEqual(health["last_error"]["error_type"], "EventConflictError")
        self.assertEqual(health["pending_rendering_repairs"], [])

    def test_duplicate_replay_repairs_stale_markdown_and_preserves_personal_content(self):
        started = self.start()
        marker_text = (
            "Answer with quoted controls:\n<!-- LEARN:TRANSCRIPT:END -->\n"
            "<!-- LEARN:SYNTHESIS:BEGIN -->\n<!-- LEARN:SOURCES:END -->\n"
            f"<!-- LEARN:TRANSCRIPT:{started['lesson_id']}:BEGIN -->\n"
            f"<!-- LEARN:TRANSCRIPT:{started['lesson_id']}:END -->"
        )
        self.hook("Stop", "session-a", "turn-2", marker_text)
        note = Path(started["note"])
        personalized = note.read_text(encoding="utf-8").replace(marker_text, "STALE DERIVED OUTPUT")
        personalized += "\nPersonal prose outside managed regions.\n"
        note.write_text(personalized, encoding="utf-8")
        self.hook("Stop", "session-a", "turn-2", marker_text)
        repaired = note.read_text(encoding="utf-8")
        self.assertEqual(repaired.count(marker_text), 1)
        self.assertNotIn("STALE DERIVED OUTPUT", repaired)
        self.assertIn("Personal prose outside managed regions.", repaired)
        validation = self.cli("validate", "--session", str(note))
        self.assertEqual(validation.returncode, 0, validation.stderr)

    def test_render_failure_after_commit_is_recovered_by_replay(self):
        started = self.start()
        module = self.load_learnctl("learnctl_render_failure_test")
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        data = {
            "hook_event_name": "Stop",
            "session_id": "session-a",
            "turn_id": "committed-turn",
            "cwd": str(self.cwd),
            "last_assistant_message": "Durable before rendering",
        }
        with mock.patch.object(module, "load_config", return_value=config), mock.patch.object(
            module, "render_lesson_note", side_effect=OSError("synthetic renderer failure")
        ):
            with self.assertRaises(OSError):
                module.run_hook(data)
        lesson = self.canonical(started["lesson_id"])
        self.assertEqual([m["markdown"] for m in lesson["messages"]].count("Durable before rendering"), 1)
        with mock.patch.object(module, "load_config", return_value=config):
            module.run_hook(data)
        note = Path(started["note"])
        self.assertEqual(note.read_text(encoding="utf-8").count("Durable before rendering"), 1)

    def test_interruption_before_canonical_save_leaves_no_partial_message(self):
        started = self.start()
        module = self.load_learnctl("learnctl_precommit_failure_test")
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        data = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-a",
            "turn_id": "precommit-turn",
            "cwd": str(self.cwd),
            "prompt": "Persist me atomically",
        }
        with mock.patch.object(module, "load_config", return_value=config), mock.patch.object(
            module, "save_lesson", side_effect=OSError("synthetic precommit failure")
        ):
            with self.assertRaises(OSError):
                module.run_hook(data)
        self.assertNotIn("Persist me atomically", [m["markdown"] for m in self.canonical(started["lesson_id"])["messages"]])
        with mock.patch.object(module, "load_config", return_value=config):
            module.run_hook(data)
        self.assertEqual(
            [m["markdown"] for m in self.canonical(started["lesson_id"])["messages"]].count(
                "Persist me atomically"
            ),
            1,
        )

    def test_repair_recreates_missing_output_from_canonical_record(self):
        started = self.start()
        self.hook("Stop", "session-a", "turn-2", "Recoverable response")
        note = Path(started["note"])
        note.unlink()
        result = self.cli("repair", "--lesson-id", started["lesson_id"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Recoverable response", note.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(result.stdout)["status"], "repaired")

    def test_explicit_repair_preserves_personal_regions(self):
        started = self.start()
        self.hook("Stop", "session-a", "turn-2", "Canonical response")
        note = Path(started["note"])
        personalized = note.read_text(encoding="utf-8").replace("Canonical response", "STALE")
        personalized = personalized.replace("status: active", "status: active\npersonal_frontmatter: retained")
        personalized += "\nPersonal appendix retained.\n"
        note.write_text(personalized, encoding="utf-8")
        result = self.cli("repair", "--lesson-id", started["lesson_id"])
        self.assertEqual(result.returncode, 0, result.stderr)
        repaired = note.read_text(encoding="utf-8")
        self.assertIn("Canonical response", repaired)
        self.assertNotIn("STALE", repaired)
        self.assertIn("personal_frontmatter: retained", repaired)
        self.assertIn("Personal appendix retained.", repaired)

    def test_multiple_runtime_message_ids_in_one_turn_remain_distinct(self):
        started = self.start()
        module = self.load_learnctl("learnctl_runtime_message_id_test")
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        base = {
            "hook_event_name": "Stop",
            "session_id": "session-a",
            "turn_id": "shared-turn",
            "cwd": str(self.cwd),
        }
        with mock.patch.object(module, "load_config", return_value=config):
            module.run_hook({**base, "message_id": "message-1", "last_assistant_message": "First event"})
            module.run_hook({**base, "message_id": "message-2", "last_assistant_message": "Second event"})
        lesson = self.canonical(started["lesson_id"])
        shared = [m for m in lesson["messages"] if m["event_identity"]["turn_id"] == "shared-turn"]
        self.assertEqual([m["event_identity"]["runtime_event_id"] for m in shared], ["message-1", "message-2"])

    def test_concurrent_conversations_and_updates_do_not_cross_or_lose_messages(self):
        first = self.start(session="conversation-a", title="Concurrent A")
        second = self.start(session="conversation-b", title="Concurrent B")

        def capture(item):
            session_id, index = item
            return self.hook(
                "Stop",
                session_id,
                f"turn-{index}",
                f"message {session_id} {index}",
            ).returncode

        work = [(session_id, index) for session_id in ("conversation-a", "conversation-b") for index in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(capture, work)), [0] * len(work))

        lesson_a = self.canonical(first["lesson_id"])
        lesson_b = self.canonical(second["lesson_id"])
        text_a = [message["markdown"] for message in lesson_a["messages"]]
        text_b = [message["markdown"] for message in lesson_b["messages"]]
        self.assertEqual(sum(message.startswith("message conversation-a") for message in text_a), 8)
        self.assertEqual(sum(message.startswith("message conversation-b") for message in text_b), 8)
        self.assertFalse(any("conversation-b" in message for message in text_a))
        self.assertFalse(any("conversation-a" in message for message in text_b))
        rendered_a = Path(first["note"]).read_text(encoding="utf-8")
        rendered_b = Path(second["note"]).read_text(encoding="utf-8")
        for index in range(8):
            self.assertEqual(rendered_a.count(f"message conversation-a {index}"), 1)
            self.assertEqual(rendered_b.count(f"message conversation-b {index}"), 1)

    def test_late_other_conversation_event_is_ignored_and_arrival_order_is_preserved(self):
        started = self.start(session="current-conversation")
        self.hook("Stop", "older-conversation", "old-turn", "late unrelated output")
        self.hook("Stop", "current-conversation", "turn-3", "assistant arrived first")
        self.hook("UserPromptSubmit", "current-conversation", "turn-2", "learner arrived later")
        lesson = self.canonical(started["lesson_id"])
        markdown = [message["markdown"] for message in lesson["messages"]]
        self.assertNotIn("late unrelated output", markdown)
        self.assertEqual(markdown[-2:], ["assistant arrived first", "learner arrived later"])

    def test_lock_unavailable_fails_clearly(self):
        module = self.load_learnctl("learnctl_lock_failure_test")
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        with mock.patch.object(module, "fcntl", None):
            with self.assertRaisesRegex(module.LearnError, "locking is unavailable"):
                with module.state_lock(config):
                    pass

    def test_retired_migration_and_event_sidecars_are_absent(self):
        events = self.vault / "Learning" / "_system" / "events"
        self.assertFalse(events.exists())
        retired = self.cli("migrate")
        self.assertEqual(retired.returncode, 2)
        self.assertIn("invalid choice", retired.stderr)

        self.start()
        self.hook("Stop", "session-a", "turn-2", "Canonical deduplication only")
        self.assertFalse(events.exists())
