import json

from tests.common import LearnTestCase, base_finish


class TopicTests(LearnTestCase):
    def finish_and_close(self, session, state, title="State Machine"):
        self.start(session, title=title, prompt=f"learn {state}")
        result = self.finish(session, base_finish(state))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.close_after_finish(session)

    def test_topic_state_transitions_are_evidence_based(self):
        self.finish_and_close("s1", "introduced")
        self.finish_and_close("s2", "retrievable")
        self.finish_and_close("s3", "applicable")
        self.finish_and_close("s4", "robust")
        topic = self.topic("state-machine")
        self.assertEqual(topic["state"], "robust")
        self.assertEqual(len(topic["sessions"]), 4)

    def test_mcq_alone_cannot_establish_application(self):
        self.start(title="MCQ Boundary")
        payload = base_finish("applicable")
        payload["demonstrated_abilities"] = [
            {
                "ability": "Selected the right option",
                "evidence_type": "mcq",
                "independent": True,
                "delayed": False,
                "evidence_refs": ["current"],
            }
        ]
        result = self.finish(payload=payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires recorded independent recall", result.stderr)

    def test_render_preserves_my_notes(self):
        self.start(title="Preservation")
        topic_note = self.vault / "Learning" / "Topics" / "preservation.md"
        original = topic_note.read_text(encoding="utf-8")
        personalized = original.replace(
            "learn_type: topic",
            "learn_type: topic\npersonal_alias: My own label\npersonal_nested:\n  title: Nested title must survive",
        )
        topic_note.write_text(personalized + "\nA personal derivation that must survive.\n", encoding="utf-8")
        result = self.finish(payload=base_finish("retrievable"))
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = topic_note.read_text(encoding="utf-8")
        self.assertIn("## My notes", rendered)
        self.assertIn("A personal derivation that must survive.", rendered)
        self.assertIn("personal_alias: My own label", rendered)
        self.assertIn("  title: Nested title must survive", rendered)
        self.assertIn("Foundations generate", rendered)

    def test_score_only_failure_does_not_claim_a_mastery_downgrade(self):
        self.finish_and_close("s1", "applicable", title="Failure Reduction")
        result = self.cli("review", "--topic", "failure-reduction", "--score", "0", "--on-date", "2030-01-01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.topic("failure-reduction")["state"], "applicable")
