import importlib.util
import json
from unittest import mock

from tests.common import ROOT, LearnTestCase


class HookTests(LearnTestCase):
    def test_pending_prompt_associates_by_cwd(self):
        other = self.cwd.parent / "other"
        other.mkdir()
        self.hook("UserPromptSubmit", "wrong-session", "t1", "wrong prompt", other)
        self.hook("UserPromptSubmit", "right-session", "t2", "right prompt", self.cwd)
        result = self.cli(
            "start",
            "--title",
            "Association",
            "--goal",
            "Test association",
            "--cwd",
            str(self.cwd),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["session_id"], "right-session")

    def test_concurrent_sessions_are_isolated(self):
        self.start("session-a", prompt="first A", title="Topic A")
        self.start("session-b", prompt="first B", title="Topic B")
        self.hook("UserPromptSubmit", "session-a", "a2", "only A")
        self.hook("Stop", "session-a", "a2", "answer A")
        self.hook("UserPromptSubmit", "session-b", "b2", "only B")
        self.hook("Stop", "session-b", "b2", "answer B")
        note_a = self.note_for("session-a").read_text(encoding="utf-8")
        note_b = self.note_for("session-b").read_text(encoding="utf-8")
        self.assertIn("only A", note_a)
        self.assertIn("answer A", note_a)
        self.assertNotIn("only B", note_a)
        self.assertIn("only B", note_b)
        self.assertNotIn("only A", note_b)

    def test_user_and_assistant_hook_logging(self):
        self.start()
        self.hook("UserPromptSubmit", "session-a", "turn-2", "My attempted explanation")
        self.hook("Stop", "session-a", "turn-2", "Let us connect that to the foundation.")
        note = self.note_for().read_text(encoding="utf-8")
        self.assertIn("### 🧑 Learner", note)
        self.assertIn("My attempted explanation", note)
        self.assertIn("### 🤖 Tutor", note)
        self.assertIn("Let us connect", note)

    def test_duplicate_hook_events_are_ignored(self):
        self.start()
        for _ in range(2):
            self.hook("UserPromptSubmit", "session-a", "turn-2", "same learner event")
            self.hook("Stop", "session-a", "turn-2", "same tutor event")
        note = self.note_for().read_text(encoding="utf-8")
        self.assertEqual(note.count("same learner event"), 1)
        self.assertEqual(note.count("same tutor event"), 1)

    def test_new_assistant_output_triggers_one_best_effort_follow(self):
        self.start()
        spec = importlib.util.spec_from_file_location(
            "learnctl_hook_follow_test", ROOT / ".agents/skills/learn/scripts/learnctl.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        config["open_notes_automatically"] = True
        data = {
            "hook_event_name": "Stop",
            "session_id": "session-a",
            "turn_id": "follow-turn",
            "cwd": str(self.cwd),
            "last_assistant_message": "Newest visible answer",
        }
        with mock.patch.object(module, "load_config", return_value=config), mock.patch.object(
            module, "follow_lesson_output"
        ) as follow:
            module.run_hook(data)
            module.run_hook(data)
        follow.assert_called_once()
        self.assertTrue(follow.call_args.args[3].startswith("🤖 Tutor · "))

    def test_inactive_hook_only_leaves_pending_record(self):
        self.hook("UserPromptSubmit", "inactive", "t1", "pending only")
        self.hook("Stop", "inactive", "t1", "must not be logged")
        self.assertFalse(list((self.vault / "Learning" / "Sessions").glob("*.md")))
        pending = list((self.vault / "Learning" / "_system" / "pending").glob("*.json"))
        self.assertEqual(len(pending), 1)
