import importlib.util
import json
from unittest import mock

from tests.common import ROOT, LearnTestCase


class HookTests(LearnTestCase):
    def test_pending_prompt_requires_exact_session_even_with_unrelated_cwd(self):
        other = self.cwd.parent / "other"
        other.mkdir()
        self.hook("UserPromptSubmit", "wrong-session", "t1", "$learn wrong prompt", other)
        self.hook("UserPromptSubmit", "right-session", "t2", "$learn right prompt", self.cwd)
        result = self.cli(
            "start",
            "--title",
            "Association",
            "--goal",
            "Test association",
            "--cwd",
            str(other),
            "--session-id",
            "right-session",
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
            module, "opening_needs_escalation", return_value=False
        ), mock.patch.object(module.ObsidianAdapter, "validate_mathjax", return_value={"status": "verified"}), mock.patch.object(
            module, "follow_lesson_output", return_value={"status": "verified", "note_open": {"status": "verified"}}
        ) as follow:
            module.run_hook(data)
            module.run_hook(data)
        follow.assert_called_once()
        self.assertRegex(follow.call_args.args[3], r"^[0-9a-f]{64}$")

    def test_failed_navigation_is_observable_and_duplicate_hook_retries_it(self):
        self.start()
        module = importlib.util.module_from_spec(
            importlib.util.spec_from_file_location(
                "learnctl_hook_retry_test", ROOT / ".agents/skills/learn/scripts/learnctl.py"
            )
        )
        module.__spec__.loader.exec_module(module)
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        config["open_notes_automatically"] = True
        data = {
            "hook_event_name": "Stop",
            "session_id": "session-a",
            "turn_id": "retry-turn",
            "cwd": str(self.cwd),
            "last_assistant_message": "Raw response remains canonical.",
        }
        failed = {
            "status": "failed",
            "note_open": {"status": "failed", "stage": "verify-note", "diagnostic": "anchor not visible"},
        }
        succeeded = {"status": "verified", "note_open": {"status": "verified"}}
        with mock.patch.object(module, "load_config", return_value=config), mock.patch.object(
            module, "opening_needs_escalation", return_value=False
        ), mock.patch.object(module.ObsidianAdapter, "validate_mathjax", return_value={"status": "verified"}), mock.patch.object(
            module, "follow_lesson_output", side_effect=[failed, succeeded]
        ) as follow:
            module.run_hook(data)
            health = module.read_operational_state(config)
            self.assertEqual(health["failed_stage"], "verify-note")
            self.assertEqual(health["diagnostic"], "anchor not visible")
            self.assertIsNotNone(health["pending_display"])
            module.run_hook(data)
        self.assertEqual(follow.call_count, 2)
        health = module.read_operational_state(config)
        self.assertIsNone(health["pending_display"])
        self.assertIsNone(health["failed_stage"])
        self.assertEqual(
            health["latest_requested_message"]["message_id"],
            health["last_successfully_displayed_message"]["message_id"],
        )

    def test_overlapping_sessions_keep_independent_pending_display_requests(self):
        self.start("session-a", title="Topic A")
        self.start("session-b", title="Topic B")
        module = importlib.util.module_from_spec(
            importlib.util.spec_from_file_location(
                "learnctl_overlap_test", ROOT / ".agents/skills/learn/scripts/learnctl.py"
            )
        )
        module.__spec__.loader.exec_module(module)
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        with module.state_lock(config):
            lesson_a = module.read_active(config, "session-a")
            lesson_b = module.read_active(config, "session-b")
            module.record_display_requested_locked(config, lesson_a, "a" * 64)
            module.record_display_requested_locked(config, lesson_b, "b" * 64)
        health = module.read_operational_state(config)
        self.assertEqual(set(health["pending_displays"]), {"session-a", "session-b"})
        self.assertEqual(health["pending_displays"]["session-a"]["message_id"], "a" * 64)
        self.assertEqual(health["pending_displays"]["session-b"]["message_id"], "b" * 64)

    def test_inactive_hook_captures_only_explicit_activation(self):
        self.hook("UserPromptSubmit", "unrelated", "t0", "ordinary unrelated work")
        self.assertEqual(list((self.vault / "Learning" / "_system" / "pending").glob("*.json")), [])
        self.hook("UserPromptSubmit", "inactive", "t1", "$learn pending only")
        self.hook("Stop", "inactive", "t1", "must not be logged")
        self.assertFalse(list((self.vault / "Learning" / "Sessions").glob("*.md")))
        pending = list((self.vault / "Learning" / "_system" / "pending").glob("*.json"))
        self.assertEqual(len(pending), 1)

    def test_vscode_skill_link_captures_prompt_and_starts_exact_lesson(self):
        session = "01a08010-28c4-7022-a8e6-748ef82e0eb9"
        prompt = (
            "[$learn](/Users/kukretianiket/Documents/stuff/learn/.agents/skills/learn/SKILL.md) "
            "Refresh Python OOP and inheritance for understanding PyTorch nn.Module."
        )
        # Do not use self.start(): it prefixes nonliteral prompts with $learn,
        # which would hide the IDE activation bug.
        for _ in range(2):
            self.hook("UserPromptSubmit", session, "ide-turn", prompt)
        result = self.cli(
            "start", "--session-id", session,
            "--title", "Python OOP", "--goal", "Understand nn.Module inheritance",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        started = json.loads(result.stdout)
        self.assertEqual(started["session_id"], session)
        lesson_path = self.vault / "Learning/_system/lessons" / f"{started['lesson_id']}.json"
        lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
        self.assertEqual(len(lesson["messages"]), 1)
        self.assertEqual(lesson["messages"][0]["markdown"], prompt)
        self.assertEqual(lesson["messages"][0]["event_identity"]["turn_id"], "ide-turn")
        self.hook("Stop", session, "ide-turn", "The attached lesson is ready.")
        self.assertIn("The attached lesson is ready.", self.note_for(session).read_text(encoding="utf-8"))

    def test_activation_formats_preserve_explicit_only_boundary(self):
        spec = importlib.util.spec_from_file_location(
            "learnctl_activation_test", ROOT / ".agents/skills/learn/scripts/learnctl.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        accepted = [
            "$learn", "  $learn Teach me", "\n$learn\nTeach me",
            "[$learn](/project/.agents/skills/learn/SKILL.md) Teach me",
            "[$learn](</project with spaces/Ω/learn/SKILL.md>) Teach me",
            "[$learn](/project (copy)/learn/SKILL.md) Teach me",
            "[$learn](.agents/skills/learn/SKILL.md)",
        ]
        rejected = [
            "ordinary unrelated work", "$learning Teach me", "$learn-other Teach me",
            "Explain how $learn works", "Use [$learn](/project/learn/SKILL.md)",
            "> [$learn](/project/learn/SKILL.md) quoted report",
            "`$learn`", "```\n$learn\n```",
            "[$other](/project/learn/SKILL.md) Teach me",
            "[$learn](/project/other/SKILL.md) Teach me",
            "[$learn](https://example.org/learn/SKILL.md) Teach me",
            "[$learn](/project/learn/SKILL.md", "[$learn]()",
        ]
        for prompt in accepted:
            with self.subTest(prompt=prompt):
                self.assertTrue(module.is_explicit_learn_activation(prompt))
        for index, prompt in enumerate(rejected):
            with self.subTest(prompt=prompt):
                self.assertFalse(module.is_explicit_learn_activation(prompt))
                self.hook("UserPromptSubmit", f"unrelated-{index}", "t1", prompt)
        self.assertEqual(list((self.vault / "Learning/_system/pending").glob("*.json")), [])

    def test_hook_errors_are_recorded_without_transcript_text(self):
        self.start()
        note = self.note_for()
        relative = next(item["note_relative"] for item in self.active_records() if item["session_id"] == "session-a")
        note.write_text("---\ninvalid: note\n---\n", encoding="utf-8")
        secret = "PRIVATE ASSISTANT TRANSCRIPT MUST NOT ENTER HEALTH"
        result = self.hook("Stop", "session-a", "broken-turn", secret)
        self.assertEqual(result.returncode, 0, result.stderr)
        health_path = self.vault / "Learning" / "_system" / "operational.json"
        health_text = health_path.read_text(encoding="utf-8")
        health = json.loads(health_text)
        self.assertNotIn(secret, health_text)
        self.assertEqual(health["last_error"]["operation"], "hook-capture")
        self.assertEqual(health["last_error"]["turn_id"], "broken-turn")
        self.assertEqual(health["pending_rendering_repairs"], [relative])
        status = self.cli("status", "--json-output")
        self.assertEqual(json.loads(status.stdout)["operational_health"], health)

    def test_structural_diagnostics_preserve_raw_canonical_assistant_message(self):
        started = self.start()
        raw = "## What Flynn…\n\nThe force force is written as $\\r_angled$."
        self.hook("Stop", "session-a", "quality-turn", raw)
        lesson_path = self.vault / "Learning/_system/lessons" / f"{started['lesson_id']}.json"
        lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
        self.assertEqual(lesson["messages"][-1]["markdown"], raw)
        health = json.loads(
            (self.vault / "Learning/_system/operational.json").read_text(encoding="utf-8")
        )
        validation = health["last_response_validation"]
        self.assertEqual(validation["status"], "failed")
        self.assertEqual(validation["message_id"], lesson["messages"][-1]["message_id"])
        self.assertTrue(any(item["kind"] == "language" for item in validation["diagnostics"]))
        self.hook("UserPromptSubmit", "session-a", "recover-turn", "$learn recover with diagnostics")
        recovered = self.cli(
            "start", "--session-id", "session-a", "--title", "Test Topic",
            "--goal", "Build a connected working model", "--mode", "learn",
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertTrue(json.loads(recovered.stdout)["response_diagnostics"])
