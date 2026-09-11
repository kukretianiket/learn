import json
from pathlib import Path

from tests.common import LearnTestCase, base_finish, source_payload
from tests.test_recovery import checkpoint_payload


class IntegrationTests(LearnTestCase):
    def test_canonical_lesson_checkpoint_source_finish_review_and_repair(self):
        started = self.start(
            session="integration-session",
            prompt="$learn integrated path",
            title="Integrated Topic",
        )
        self.hook("Stop", "integration-session", "turn-1", "Initial connected explanation")
        self.hook(
            "UserPromptSubmit",
            "integration-session",
            "turn-2",
            "I can reconstruct the model and apply it independently.",
        )
        checkpoint = self.cli(
            "checkpoint",
            "--session-id",
            "integration-session",
            "--json",
            json.dumps(checkpoint_payload()),
        )
        self.assertEqual(checkpoint.returncode, 0, checkpoint.stderr)

        source = self.cli(
            "source",
            "--session-id",
            "integration-session",
            "--json",
            json.dumps(source_payload(citekey="integration-source")),
        )
        self.assertEqual(source.returncode, 0, source.stderr)

        finish_payload = base_finish("applicable")
        finish_payload["checkpoint"] = checkpoint_payload(phase="completion")
        finished = self.finish("integration-session", finish_payload)
        self.assertEqual(finished.returncode, 0, finished.stderr)
        expected = json.loads(finished.stdout)["expected_final"]
        self.assertEqual(expected, {"session_id": "integration-session", "turn_id": "turn-2"})
        self.hook("Stop", "integration-session", "turn-2", "Concise final lesson response")

        first_review = self.cli(
            "review", "--topic", "integrated-topic", "--score", "2", "--on-date", "2030-01-10"
        )
        replayed_review = self.cli(
            "review", "--topic", "integrated-topic", "--score", "2", "--on-date", "2030-01-10"
        )
        self.assertEqual(first_review.returncode, 0, first_review.stderr)
        self.assertEqual(replayed_review.returncode, 0, replayed_review.stderr)
        self.assertEqual(
            json.loads(first_review.stdout)["assessment_id"],
            json.loads(replayed_review.stdout)["assessment_id"],
        )

        note = Path(started["note"])
        note.write_text(note.read_text(encoding="utf-8") + "\nPersonal integration notes.\n", encoding="utf-8")
        topic_note = self.vault / "Learning" / "Topics" / "integrated-topic.md"
        topic_note.unlink()
        repair = self.cli("repair", "--all")
        self.assertEqual(repair.returncode, 0, repair.stderr)
        self.assertTrue(topic_note.exists())
        rendered = note.read_text(encoding="utf-8")
        self.assertIn("Initial connected explanation", rendered)
        self.assertIn("Concise final lesson response", rendered)
        self.assertIn("Personal integration notes.", rendered)
        self.assertIn("[[Sources/integration-source]]", rendered)

        validation = self.cli("validate")
        self.assertEqual(validation.returncode, 0, validation.stderr)
        status = self.cli("status", "--json-output")
        self.assertEqual(status.returncode, 0, status.stderr)
        state = json.loads(status.stdout)
        self.assertEqual(state["active_sessions"], [])
        self.assertEqual(state["lesson_count"], 1)
        self.assertEqual(state["review_assessment_count"], 1)

