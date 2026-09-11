import argparse
import importlib.util
import json
from pathlib import Path
from unittest import mock

from tests.common import ROOT, LearnTestCase, base_finish


class LifecycleTests(LearnTestCase):
    def load_learnctl(self, name):
        spec = importlib.util.spec_from_file_location(name, ROOT / ".agents/skills/learn/scripts/learnctl.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def lesson(self, lesson_id):
        path = self.vault / "Learning" / "_system" / "lessons" / f"{lesson_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_pause_detaches_and_stops_logging_unrelated_conversation(self):
        started = self.start(session="pause-session")
        self.hook("Stop", "pause-session", "turn-before-pause", "Recorded before pause")
        paused = self.cli("pause", "--session-id", "pause-session")
        self.assertEqual(paused.returncode, 0, paused.stderr)
        before = self.lesson(started["lesson_id"])
        self.assertEqual(before["status"], "paused")
        self.assertEqual(self.active_records(), [])

        self.hook("UserPromptSubmit", "pause-session", "unrelated-user", "ordinary unrelated prompt")
        self.hook("Stop", "pause-session", "unrelated-stop", "unrelated assistant response")
        after = self.lesson(started["lesson_id"])
        self.assertEqual(after["messages"], before["messages"])
        self.assertNotIn("unrelated assistant response", Path(started["note"]).read_text(encoding="utf-8"))

    def test_explicit_resume_rebinds_to_new_conversation_without_stealing(self):
        started = self.start(session="old-conversation")
        paused = self.cli("pause", "--session-id", "old-conversation")
        self.assertEqual(paused.returncode, 0, paused.stderr)
        self.hook("UserPromptSubmit", "new-conversation", "new-turn-1", "$learn resume selected lesson")
        resumed = self.cli("resume", "--lesson-id", started["lesson_id"], "--session-id", "new-conversation")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        lesson = self.lesson(started["lesson_id"])
        self.assertEqual(lesson["status"], "active")
        self.assertEqual(lesson["conversation_binding"]["session_id"], "new-conversation")
        self.assertEqual(lesson["binding_history"][0]["session_id"], "old-conversation")

        self.hook("Stop", "old-conversation", "late-old", "must not cross the rebind")
        self.hook("Stop", "new-conversation", "new-answer", "belongs to resumed lesson")
        markdown = [message["markdown"] for message in self.lesson(started["lesson_id"])["messages"]]
        self.assertNotIn("must not cross the rebind", markdown)
        self.assertIn("belongs to resumed lesson", markdown)

    def test_resume_refuses_to_steal_an_attached_lesson(self):
        started = self.start(session="attached-conversation")
        result = self.cli("resume", "--lesson-id", started["lesson_id"], "--session-id", "other-conversation")
        self.assertEqual(result.returncode, 2)
        self.assertIn("pause", result.stderr)
        lesson = self.lesson(started["lesson_id"])
        self.assertEqual(lesson["conversation_binding"]["session_id"], "attached-conversation")

    def test_only_matching_final_stop_is_captured_and_completes(self):
        started = self.start(session="finish-session")
        self.hook("UserPromptSubmit", "finish-session", "finish-turn", "Final learner evidence")
        finished = self.finish(session="finish-session", payload=base_finish("retrievable"))
        self.assertEqual(finished.returncode, 0, finished.stderr)
        result = json.loads(finished.stdout)
        self.assertEqual(result["expected_final"], {"session_id": "finish-session", "turn_id": "finish-turn"})

        self.hook("Stop", "finish-session", "old-turn", "old stop must be ignored")
        self.hook("Stop", "other-session", "finish-turn", "other conversation must be ignored")
        waiting = self.lesson(started["lesson_id"])
        self.assertEqual(waiting["status"], "finishing")
        self.assertNotIn("old stop must be ignored", [message["markdown"] for message in waiting["messages"]])

        self.hook("Stop", "finish-session", "finish-turn", "Exact final response")
        completed = self.lesson(started["lesson_id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual([m["markdown"] for m in completed["messages"]].count("Exact final response"), 1)
        self.assertEqual(self.active_records(), [])
        self.hook("Stop", "finish-session", "finish-turn", "Exact final response")
        self.assertEqual(
            [m["markdown"] for m in self.lesson(started["lesson_id"])["messages"]].count("Exact final response"),
            1,
        )

    def test_replayed_old_stop_cannot_contaminate_a_newer_lesson(self):
        first = self.start(session="sequential-session", turn="first-turn", title="First Lesson")
        self.assertEqual(self.finish(session="sequential-session").returncode, 0)
        self.hook("Stop", "sequential-session", "first-turn", "First lesson final response")
        self.assertEqual(self.lesson(first["lesson_id"])["status"], "completed")

        second = self.start(session="sequential-session", turn="second-turn", title="Second Lesson")
        self.hook("Stop", "sequential-session", "first-turn", "First lesson final response")
        second_record = self.lesson(second["lesson_id"])
        self.assertEqual(second_record["status"], "active")
        self.assertNotIn(
            "First lesson final response", [message["markdown"] for message in second_record["messages"]]
        )
        self.assertEqual(
            [message["markdown"] for message in self.lesson(first["lesson_id"])["messages"]].count(
                "First lesson final response"
            ),
            1,
        )

    def test_abort_preserves_record_and_stops_future_logging(self):
        started = self.start(session="abort-session")
        aborted = self.cli("abort", "--session-id", "abort-session")
        self.assertEqual(aborted.returncode, 0, aborted.stderr)
        before = self.lesson(started["lesson_id"])
        self.assertEqual(before["status"], "aborted")
        self.assertEqual(self.active_records(), [])

        self.hook("Stop", "abort-session", "late-after-abort", "must not be recorded")
        after = self.lesson(started["lesson_id"])
        self.assertEqual(after["messages"], before["messages"])
        self.assertIn("status: aborted", Path(started["note"]).read_text(encoding="utf-8"))

    def test_repeated_finish_has_one_assessment_and_one_scheduling_effect(self):
        started = self.start(session="retry-finish", title="Retry Finish")
        payload = base_finish("introduced")
        first = self.finish(session="retry-finish", payload=payload)
        self.assertEqual(first.returncode, 0, first.stderr)
        first_lesson = self.lesson(started["lesson_id"])
        first_topic = self.topic("retry-finish")
        second = self.finish(session="retry-finish", payload=payload)
        self.assertEqual(second.returncode, 0, second.stderr)
        second_lesson = self.lesson(started["lesson_id"])
        second_topic = self.topic("retry-finish")
        self.assertEqual(first_lesson["revision"], second_lesson["revision"])
        self.assertEqual(len(second_topic["assessment_refs"]), 1)
        self.assertEqual(len(second_topic["review"]["history"]), len(first_topic["review"]["history"]))

        conflict = dict(payload)
        conflict["final_mental_model"] = "A conflicting replacement assessment."
        rejected = self.finish(session="retry-finish", payload=conflict)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("Conflicting finish", rejected.stderr)
        self.assertEqual(self.lesson(started["lesson_id"]), second_lesson)
        self.assertEqual(self.topic("retry-finish"), second_topic)

    def test_invalid_finish_leaves_authoritative_state_unchanged(self):
        started = self.start(session="invalid-finish")
        lesson_path = self.vault / "Learning" / "_system" / "lessons" / f"{started['lesson_id']}.json"
        topic_path = self.vault / "Learning" / "_system" / "topics" / "test-topic.json"
        note = Path(started["note"])
        before = (lesson_path.read_bytes(), topic_path.read_bytes(), note.read_bytes())
        invalid = base_finish("applicable")
        invalid["demonstrated_abilities"] = []
        result = self.finish(session="invalid-finish", payload=invalid)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(before, (lesson_path.read_bytes(), topic_path.read_bytes(), note.read_bytes()))

    def test_render_failure_after_finish_commit_is_retry_recoverable(self):
        started = self.start(session="render-finish", title="Render Finish")
        module = self.load_learnctl("learnctl_finish_render_failure_test")
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        payload = base_finish("introduced")
        args = argparse.Namespace(
            json=json.dumps(payload), json_file=None, session_id="render-finish", cwd=None
        )
        with mock.patch.object(module, "load_config", return_value=config), mock.patch.dict(
            module.os.environ,
            {"CODEX_THREAD_ID": "render-finish", "CODEX_SESSION_ID": "render-finish"},
            clear=True,
        ), mock.patch.object(module, "render_lesson_note", side_effect=OSError("synthetic render failure")):
            with self.assertRaisesRegex(module.SavedRenderingError, "assessment was saved"):
                module.command_finish(args)
        committed = self.lesson(started["lesson_id"])
        self.assertEqual(committed["status"], "finishing")
        self.assertIsNotNone(committed["final_assessment"])
        self.assertEqual(len(self.topic("render-finish")["review"]["history"]), 1)

        retry = self.finish(session="render-finish", payload=payload)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(len(self.topic("render-finish")["review"]["history"]), 1)

    def test_missing_final_stop_remains_visible_and_is_not_substituted(self):
        started = self.start(session="missing-stop", title="Missing Stop")
        finished = self.finish(session="missing-stop", payload=base_finish("introduced"))
        self.assertEqual(finished.returncode, 0, finished.stderr)
        repair = self.cli("repair", "--lesson-id", started["lesson_id"])
        self.assertEqual(repair.returncode, 0, repair.stderr)
        lesson = self.lesson(started["lesson_id"])
        self.assertEqual(lesson["status"], "finishing")
        self.assertIsNotNone(lesson["final_assessment"])
        status = json.loads(self.cli("status", "--json-output").stdout)
        waiting = next(item for item in status["unfinished_lessons"] if item["lesson_id"] == started["lesson_id"])
        self.assertTrue(waiting["missing_final_response"])
        self.assertEqual(waiting["expected_final"], lesson["expected_finishing"])

        self.hook("UserPromptSubmit", "missing-stop", "later-turn", "$learn recover")
        recovered = self.cli(
            "start",
            "--title",
            "Missing Stop",
            "--goal",
            "Do not replace the missing final response",
            "--mode",
            "learn",
            "--session-id",
            "missing-stop",
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        recovered_payload = json.loads(recovered.stdout)
        self.assertEqual(recovered_payload["lesson_status"], "finishing")
        self.assertEqual(recovered_payload["expected_final"], lesson["expected_finishing"])
        self.assertIn("Do not continue teaching", recovered_payload["instruction"])
