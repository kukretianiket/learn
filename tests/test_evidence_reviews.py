import argparse
import importlib.util
import json
from unittest import mock

from tests.common import ROOT, LearnTestCase, base_finish, base_review_assessment


class EvidenceAndReviewTests(LearnTestCase):
    def load_learnctl(self, name):
        spec = importlib.util.spec_from_file_location(name, ROOT / ".agents/skills/learn/scripts/learnctl.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def lesson(self, lesson_id):
        path = self.vault / "Learning" / "_system" / "lessons" / f"{lesson_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_quick_explanation_preserves_stronger_evidence_gaps_and_schedule(self):
        first = self.start(session="strong-session", title="Preserved Knowledge")
        strong = base_finish("applicable")
        strong["demonstrated_abilities"][0]["ability"] = "Recalled the durable foundation"
        strong["unresolved_gaps"] = ["Prior untested edge case"]
        self.assertEqual(self.finish("strong-session", strong).returncode, 0)
        self.close_after_finish("strong-session")
        established = self.topic("preserved-knowledge")
        established_schedule = established["review"]["next_review"]

        self.start(session="quick-session", title="Preserved Knowledge")
        quick = base_finish("introduced")
        quick["final_mental_model"] = "A short supplementary explanation."
        quick["unresolved_gaps"] = ["Newly noticed edge case"]
        self.assertEqual(self.finish("quick-session", quick).returncode, 0)
        after = self.topic("preserved-knowledge")

        self.assertEqual(after["state"], "applicable")
        abilities = [item.get("ability") for item in after["demonstrated_abilities"] if isinstance(item, dict)]
        self.assertIn("Recalled the durable foundation", abilities)
        self.assertEqual(
            after["unresolved_gaps"], ["Prior untested edge case", "Newly noticed edge case"]
        )
        self.assertEqual(after["review"]["next_review"], established_schedule)
        self.assertFalse(after["review"]["history"][-1]["schedule_changed"])

    def test_score_only_review_is_durable_and_replay_does_not_reschedule(self):
        self.start(title="Replayable Review")
        self.assertEqual(self.finish(payload=base_finish("introduced")).returncode, 0)
        command = (
            "review",
            "--topic",
            "replayable-review",
            "--score",
            "3",
            "--notes",
            "Standalone score only",
            "--on-date",
            "2030-04-10",
        )
        first = self.cli(*command)
        self.assertEqual(first.returncode, 0, first.stderr)
        first_result = json.loads(first.stdout)
        first_topic = self.topic("replayable-review")
        second = self.cli(*command)
        self.assertEqual(second.returncode, 0, second.stderr)
        second_result = json.loads(second.stdout)
        second_topic = self.topic("replayable-review")

        self.assertEqual(first_result["assessment_id"], second_result["assessment_id"])
        self.assertEqual(second_result["status"], "replayed")
        self.assertEqual(first_topic["review"], second_topic["review"])
        self.assertEqual(second_topic["state"], "introduced")
        records = list((self.vault / "Learning" / "_system" / "reviews").glob("*.json"))
        self.assertEqual(len(records), 1)
        stored = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(stored["kind"], "score-only")
        self.assertIsNone(stored["semantic_assessment"])
        self.assertIsNone(stored["evidence_lesson_id"])

    def test_semantic_review_uses_same_evidence_rules_and_can_promote(self):
        first = self.start(session="review-foundation", title="Semantic Review")
        self.assertEqual(self.finish("review-foundation", base_finish("introduced")).returncode, 0)
        self.close_after_finish("review-foundation")
        current = self.start(session="review-evidence", title="Semantic Review")
        semantic = base_review_assessment("robust")
        result = self.cli(
            "review",
            "--topic",
            "semantic-review",
            "--score",
            "3",
            "--on-date",
            "2030-05-01",
            "--session-id",
            "review-evidence",
            "--json",
            json.dumps(semantic),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.topic("semantic-review")["state"], "robust")
        record_path = next((self.vault / "Learning" / "_system" / "reviews").glob("*.json"))
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(record["kind"], "semantic-review")
        self.assertEqual(record["evidence_lesson_id"], current["lesson_id"])
        evidence_refs = record["semantic_assessment"]["demonstrated_abilities"][0]["evidence_refs"]
        self.assertNotIn("current", evidence_refs)
        learner_ids = {
            message["message_id"]
            for message in self.lesson(current["lesson_id"])["messages"]
            if message["role"] == "user"
        }
        self.assertTrue(set(evidence_refs) <= learner_ids)

    def test_mcq_review_cannot_claim_robustness_and_is_not_stored(self):
        self.start(session="mcq-base", title="MCQ Review Boundary")
        self.assertEqual(self.finish("mcq-base", base_finish("introduced")).returncode, 0)
        self.close_after_finish("mcq-base")
        self.start(session="mcq-review", title="MCQ Review Boundary")
        semantic = base_review_assessment("robust")
        semantic["demonstrated_abilities"] = [
            {
                "ability": "Recognized the answer",
                "evidence_type": "mcq",
                "independent": True,
                "delayed": True,
                "evidence_refs": ["current"],
            }
        ]
        result = self.cli(
            "review",
            "--topic",
            "mcq-review-boundary",
            "--score",
            "3",
            "--on-date",
            "2030-06-01",
            "--session-id",
            "mcq-review",
            "--json",
            json.dumps(semantic),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires recorded independent recall", result.stderr)
        self.assertEqual(list((self.vault / "Learning" / "_system" / "reviews").glob("*.json")), [])
        self.assertEqual(self.topic("mcq-review-boundary")["state"], "introduced")

    def test_explicit_reassessment_can_downgrade_but_preserves_prior_abilities(self):
        self.start(session="prior-robust", title="Contrary Evidence")
        self.assertEqual(self.finish("prior-robust", base_finish("robust")).returncode, 0)
        self.close_after_finish("prior-robust")
        self.start(session="contrary-review", title="Contrary Evidence")
        reassessment = base_review_assessment("introduced")
        reassessment["reassessment"] = True
        reassessment["contrary_evidence_refs"] = ["current"]
        reassessment["unresolved_gaps"] = ["Could not reconstruct the transfer step"]
        result = self.cli(
            "review",
            "--topic",
            "contrary-evidence",
            "--score",
            "0",
            "--on-date",
            "2030-07-01",
            "--session-id",
            "contrary-review",
            "--json",
            json.dumps(reassessment),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        topic = self.topic("contrary-evidence")
        self.assertEqual(topic["state"], "introduced")
        self.assertTrue(topic["demonstrated_abilities"])
        self.assertIn("Could not reconstruct the transfer step", topic["unresolved_gaps"])

    def test_invalid_evidence_reference_is_rejected_before_finish_mutation(self):
        started = self.start(session="bad-reference", title="Bad Reference")
        payload = base_finish("retrievable")
        payload["demonstrated_abilities"][0]["evidence_refs"] = ["missing-message-id"]
        before = self.lesson(started["lesson_id"])
        result = self.finish("bad-reference", payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not resolve", result.stderr)
        self.assertEqual(self.lesson(started["lesson_id"]), before)

    def test_evidence_timestamp_cannot_postdate_review_assessment(self):
        self.start(session="timestamp-review", title="Timestamp Evidence")
        semantic = base_review_assessment("retrievable")
        result = self.cli(
            "review",
            "--topic",
            "timestamp-evidence",
            "--score",
            "2",
            "--on-date",
            "2000-01-01",
            "--session-id",
            "timestamp-review",
            "--json",
            json.dumps(semantic),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("later than the assessment timestamp", result.stderr)
        self.assertEqual(list((self.vault / "Learning" / "_system" / "reviews").glob("*.json")), [])

    def test_review_record_survives_interruption_before_topic_derivation(self):
        self.start(title="Durable Review")
        self.assertEqual(self.finish(payload=base_finish("introduced")).returncode, 0)
        module = self.load_learnctl("learnctl_review_interruption_test")
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        args = argparse.Namespace(
            topic="durable-review",
            score=2,
            notes="Persist before derive",
            on_date="2030-08-01",
            json=None,
            json_file=None,
            lesson_id=None,
            session_id=None,
        )
        with mock.patch.object(module, "load_config", return_value=config), mock.patch.object(
            module, "rebuild_topic_state", side_effect=RuntimeError("synthetic derivation interruption")
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic derivation interruption"):
                module.command_review(args)
        records = list((self.vault / "Learning" / "_system" / "reviews").glob("*.json"))
        self.assertEqual(len(records), 1)
        self.assertNotEqual(self.topic("durable-review")["review"]["last_review"], "2030-08-01")

        repair = self.cli("repair", "--topic", "durable-review")
        self.assertEqual(repair.returncode, 0, repair.stderr)
        repaired_topic = self.topic("durable-review")
        self.assertEqual(repaired_topic["review"]["last_review"], "2030-08-01")

        retry = self.cli(
            "review",
            "--topic",
            "durable-review",
            "--score",
            "2",
            "--notes",
            "Persist before derive",
            "--on-date",
            "2030-08-01",
        )
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(json.loads(retry.stdout)["status"], "replayed")
        self.assertEqual(len(list((self.vault / "Learning" / "_system" / "reviews").glob("*.json"))), 1)
        matching = [
            item
            for item in self.topic("durable-review")["review"]["history"]
            if item.get("assessment_id") == json.loads(retry.stdout)["assessment_id"]
        ]
        self.assertEqual(len(matching), 1)

    def test_review_render_failure_reports_saved_and_topic_repair_recovers(self):
        self.start(title="Review Render Repair")
        self.assertEqual(self.finish(payload=base_finish("introduced")).returncode, 0)
        module = self.load_learnctl("learnctl_review_render_failure_test")
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        args = argparse.Namespace(
            topic="review-render-repair",
            score=2,
            notes="Committed before rendering",
            on_date="2030-08-15",
            json=None,
            json_file=None,
            lesson_id=None,
            session_id=None,
        )
        with mock.patch.object(module, "load_config", return_value=config), mock.patch.object(
            module, "render_topic_note", side_effect=OSError("synthetic topic render failure")
        ):
            with self.assertRaisesRegex(module.SavedRenderingError, "assessment was saved"):
                module.command_review(args)
        health_path = self.vault / "Learning" / "_system" / "operational.json"
        health = json.loads(health_path.read_text(encoding="utf-8"))
        self.assertIn("Learning/Topics/review-render-repair.md", health["pending_rendering_repairs"])

        repaired = self.cli("repair", "--topic", "review-render-repair")
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        health = json.loads(health_path.read_text(encoding="utf-8"))
        self.assertNotIn("Learning/Topics/review-render-repair.md", health["pending_rendering_repairs"])
        review_entries = [
            item
            for item in self.topic("review-render-repair")["review"]["history"]
            if item.get("kind") == "score-only"
        ]
        self.assertEqual(len(review_entries), 1)

    def test_repair_rebuilds_topic_cache_from_baseline_and_durable_assessments(self):
        started = self.start(session="rebuild-session", title="Rebuildable Topic")
        payload = base_finish("applicable")
        payload["unresolved_gaps"] = ["Preserved gap"]
        self.assertEqual(self.finish("rebuild-session", payload).returncode, 0)
        self.assertEqual(
            self.cli(
                "review",
                "--topic",
                "rebuildable-topic",
                "--score",
                "2",
                "--on-date",
                "2030-09-01",
            ).returncode,
            0,
        )
        expected = self.topic("rebuildable-topic")
        topic_path = self.vault / "Learning" / "_system" / "topics" / "rebuildable-topic.json"
        damaged = dict(expected)
        damaged["state"] = "unseen"
        damaged["demonstrated_abilities"] = []
        damaged["unresolved_gaps"] = []
        damaged["review"] = {"interval_index": 0, "last_review": None, "next_review": None, "history": []}
        damaged["assessment_refs"] = []
        topic_path.write_text(json.dumps(damaged), encoding="utf-8")

        repaired = self.cli("repair", "--lesson-id", started["lesson_id"])
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        after = self.topic("rebuildable-topic")
        for key in ("state", "demonstrated_abilities", "unresolved_gaps", "review"):
            self.assertEqual(after[key], expected[key])
