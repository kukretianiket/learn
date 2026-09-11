import importlib.util
import json
from pathlib import Path
from unittest import mock

from tests.common import ROOT, LearnTestCase, base_finish


def checkpoint_payload(covered_through="current", evidence_refs=None, phase="teaching"):
    return {
        "phase": phase,
        "goal_and_route": "Build the foundation, then apply it to a changed case.",
        "relevant_concepts": ["foundation", "dependency"],
        "demonstrated_understanding": ["Identified the foundation"],
        "misconceptions": [],
        "unknowns": ["Whether transfer is independent"],
        "evidence_refs": ["current"] if evidence_refs is None else evidence_refs,
        "hints_and_independence": ["One structural hint was used"],
        "outstanding_question": "Can the learner transfer the model?",
        "next_teaching_step": "Ask for a changed-case application.",
        "covered_through": covered_through,
    }


class RecoveryTests(LearnTestCase):
    def canonical(self, lesson_id):
        path = self.vault / "Learning" / "_system" / "lessons" / f"{lesson_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def checkpoint(self, payload, session="session-a"):
        return self.cli(
            "checkpoint", "--session-id", session, "--json", json.dumps(payload)
        )

    def test_checkpoint_is_compact_resolved_and_retry_safe(self):
        started = self.start(prompt="$learn checkpoint recovery")
        before = self.canonical(started["lesson_id"])
        result = self.checkpoint(checkpoint_payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(result.stdout)
        self.assertEqual(saved["status"], "saved")

        lesson = self.canonical(started["lesson_id"])
        checkpoint = lesson["latest_checkpoint"]
        first_message_id = lesson["messages"][0]["message_id"]
        self.assertEqual(checkpoint["covered_through_message_id"], first_message_id)
        self.assertEqual(checkpoint["evidence_refs"], [first_message_id])
        self.assertEqual(
            set(checkpoint),
            {
                "phase",
                "goal_and_route",
                "relevant_concepts",
                "demonstrated_understanding",
                "misconceptions",
                "unknowns",
                "evidence_refs",
                "hints_and_independence",
                "outstanding_question",
                "next_teaching_step",
                "covered_through_message_id",
            },
        )
        self.assertGreater(lesson["revision"], before["revision"])

        revision = lesson["revision"]
        repeat = self.checkpoint(checkpoint_payload())
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(json.loads(repeat.stdout)["status"], "unchanged")
        self.assertEqual(self.canonical(started["lesson_id"])["revision"], revision)

    def test_checkpoint_rejection_and_backward_coverage_do_not_mutate_lesson(self):
        started = self.start()
        initial_id = self.canonical(started["lesson_id"])["messages"][0]["message_id"]
        self.hook("Stop", "session-a", "turn-1", "First explanation")
        self.hook("UserPromptSubmit", "session-a", "turn-2", "A later learner answer")
        current = self.checkpoint(checkpoint_payload())
        self.assertEqual(current.returncode, 0, current.stderr)
        before = self.canonical(started["lesson_id"])

        backward_payload = checkpoint_payload(initial_id, [initial_id])
        backward = self.checkpoint(backward_payload)
        self.assertEqual(backward.returncode, 2)
        self.assertIn("cannot move backward", backward.stderr)
        self.assertEqual(self.canonical(started["lesson_id"]), before)

        malformed = checkpoint_payload()
        malformed["private_notes"] = "must not enter the schema"
        rejected = self.checkpoint(malformed)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("unsupported keys", rejected.stderr)
        self.assertEqual(self.canonical(started["lesson_id"]), before)

        oversized = checkpoint_payload()
        oversized["goal_and_route"] = "route " * 4000
        rejected = self.checkpoint(oversized)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("UTF-8 bytes", rejected.stderr)
        self.assertEqual(self.canonical(started["lesson_id"]), before)

    def test_resume_returns_checkpoint_compact_topic_and_bounded_messages(self):
        started = self.start(prompt="$learn bounded recovery")
        first_checkpoint = self.checkpoint(checkpoint_payload())
        self.assertEqual(first_checkpoint.returncode, 0, first_checkpoint.stderr)
        for index in range(1, 7):
            self.hook("Stop", "session-a", f"assistant-{index}", f"Assistant message {index}")
            self.hook("UserPromptSubmit", "session-a", f"user-{index}", f"Learner message {index}")

        pause = self.cli("pause", "--session-id", "session-a")
        self.assertEqual(pause.returncode, 0, pause.stderr)
        resume = self.cli(
            "resume", "--lesson-id", started["lesson_id"], "--session-id", "session-b"
        )
        self.assertEqual(resume.returncode, 0, resume.stderr)
        recovery = json.loads(resume.stdout)["recovery"]
        self.assertEqual(recovery["session"]["session_id"], "session-b")
        self.assertEqual(recovery["checkpoint"]["phase"], "teaching")
        self.assertEqual(recovery["history"]["returned_count"], 8)
        self.assertEqual(recovery["history"]["available_count"], 12)
        self.assertTrue(recovery["history"]["more_history_exists"])
        self.assertEqual(recovery["history"]["omitted_before_count"], 4)
        self.assertIn("targeted retrieval", recovery["history"]["retrieval_hint"])
        self.assertLessEqual(len(recovery["topic_summary"]["demonstrated_abilities"]), 8)
        serialized = json.dumps(recovery)
        self.assertNotIn("mcq_position_state", serialized)
        self.assertNotIn("final_assessment", serialized)
        self.assertNotIn("source_usages", serialized)

    def test_targeted_context_retrieves_exact_message_or_inclusive_range(self):
        started = self.start()
        self.hook("Stop", "session-a", "assistant-1", "First tutor reply")
        self.hook("UserPromptSubmit", "session-a", "user-2", "Second learner reply")
        self.hook("Stop", "session-a", "assistant-2", "Second tutor reply")
        messages = self.canonical(started["lesson_id"])["messages"]

        exact = self.cli(
            "context", "--session-id", "session-a", "--message-id", messages[1]["message_id"]
        )
        self.assertEqual(exact.returncode, 0, exact.stderr)
        exact_history = json.loads(exact.stdout)["history"]
        self.assertEqual([item["markdown"] for item in exact_history["messages"]], ["First tutor reply"])
        self.assertFalse(exact_history["more_history_exists"])

        ranged = self.cli(
            "context", "--session-id", "session-a",
            "--from-message", messages[1]["message_id"],
            "--to-message", messages[3]["message_id"], "--limit", "50"
        )
        self.assertEqual(ranged.returncode, 0, ranged.stderr)
        self.assertEqual(
            [item["message_id"] for item in json.loads(ranged.stdout)["history"]["messages"]],
            [item["message_id"] for item in messages[1:4]],
        )

        missing = self.cli(
            "context", "--session-id", "session-a", "--message-id", "missing-message"
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("Unknown lesson message_id", missing.stderr)

    def test_routine_hook_capture_never_invokes_recovery_or_checkpoint_commands(self):
        started = self.start()
        spec = importlib.util.spec_from_file_location(
            "learnctl_phase5_hook_test", ROOT / ".agents/skills/learn/scripts/learnctl.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        event = {
            "hook_event_name": "Stop",
            "session_id": "session-a",
            "turn_id": "routine-turn",
            "cwd": str(self.cwd),
            "last_assistant_message": "Captured without a recovery read",
        }
        with mock.patch.object(module, "load_config", return_value=config), mock.patch.object(
            module, "command_context", side_effect=AssertionError("unexpected context command")
        ), mock.patch.object(
            module, "command_checkpoint", side_effect=AssertionError("unexpected checkpoint command")
        ):
            module.run_hook(event)
        lesson = self.canonical(started["lesson_id"])
        self.assertIn("Captured without a recovery read", [item["markdown"] for item in lesson["messages"]])
        self.assertIsNone(lesson["latest_checkpoint"])

    def test_finish_attaches_one_retry_safe_completion_checkpoint(self):
        started = self.start(prompt="$learn finish with checkpoint")
        payload = base_finish("introduced")
        payload["checkpoint"] = checkpoint_payload(phase="completion")
        first = self.finish(payload=payload)
        self.assertEqual(first.returncode, 0, first.stderr)
        lesson = self.canonical(started["lesson_id"])
        self.assertEqual(lesson["latest_checkpoint"]["phase"], "completion")
        self.assertNotIn("checkpoint", lesson["final_assessment"]["payload"])
        self.assertEqual(
            lesson["final_assessment"]["checkpoint_fingerprint"],
            json.loads(first.stdout).get("checkpoint_fingerprint"),
        )

        revision = lesson["revision"]
        repeat = self.finish(payload=payload)
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(self.canonical(started["lesson_id"])["revision"], revision)

        conflict_payload = base_finish("introduced")
        conflict_payload["checkpoint"] = checkpoint_payload(phase="completion")
        conflict_payload["checkpoint"]["next_teaching_step"] = "A conflicting next step."
        conflict = self.finish(payload=conflict_payload)
        self.assertEqual(conflict.returncode, 2)
        self.assertIn("different completion checkpoint", conflict.stderr)
        self.assertEqual(self.canonical(started["lesson_id"])["revision"], revision)

    def test_mcq_question_identity_is_stable_and_private(self):
        started = self.start()
        first = self.cli(
            "context", "--session-id", "session-a", "--next-mcq",
            "--question-id", "private-diagnostic-1", "--choices", "5", "--correct-count", "2"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.hook("UserPromptSubmit", "session-a", "turn-2", "My diagnostic response")
        revision_before_retry = self.canonical(started["lesson_id"])["revision"]
        retry = self.cli(
            "context", "--session-id", "session-a", "--next-mcq",
            "--question-id", "private-diagnostic-1", "--choices", "5", "--correct-count", "2"
        )
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(json.loads(first.stdout), json.loads(retry.stdout))
        self.assertEqual(self.canonical(started["lesson_id"])["revision"], revision_before_retry)

        conflict = self.cli(
            "context", "--session-id", "session-a", "--next-mcq",
            "--question-id", "private-diagnostic-1", "--choices", "4", "--correct-count", "1"
        )
        self.assertEqual(conflict.returncode, 2)
        self.assertIn("already issued", conflict.stderr)

        note = Path(started["note"]).read_text(encoding="utf-8")
        context = self.cli("context", "--session-id", "session-a")
        self.assertEqual(context.returncode, 0, context.stderr)
        public = context.stdout
        for text in (note, public):
            self.assertNotIn("private-diagnostic-1", text)
            self.assertNotIn("correct_option_labels", text)
            self.assertNotIn("mcq_position_state", text)
