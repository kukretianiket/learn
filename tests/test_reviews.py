import datetime as dt
import json

from tests.common import LearnTestCase, base_finish


class ReviewTests(LearnTestCase):
    def test_finish_schedules_first_review(self):
        self.start(title="Review Timing")
        result = self.finish(payload=base_finish("introduced"))
        self.assertEqual(result.returncode, 0, result.stderr)
        topic = self.topic("review-timing")
        expected = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        self.assertEqual(topic["review"]["next_review"], expected)

    def test_due_and_review_work_without_obsidian_bases(self):
        self.start(title="CLI Review")
        result = self.finish(payload=base_finish("introduced"))
        self.assertEqual(result.returncode, 0, result.stderr)
        due = self.cli("due", "--on-date", "2999-01-01", "--json-output")
        self.assertEqual(due.returncode, 0, due.stderr)
        rows = json.loads(due.stdout)
        self.assertEqual(rows[0]["slug"], "cli-review")
        review = self.cli("review", "--topic", "cli-review", "--score", "2", "--on-date", "2030-01-10")
        self.assertEqual(review.returncode, 0, review.stderr)
        topic = self.topic("cli-review")
        self.assertEqual(topic["state"], "retrievable")
        self.assertEqual(topic["review"]["next_review"], "2030-01-13")

    def test_score_three_marks_delayed_transfer_robust(self):
        self.start(title="Transfer Review")
        self.assertEqual(self.finish(payload=base_finish("applicable")).returncode, 0)
        review = self.cli("review", "--topic", "transfer-review", "--score", "3", "--on-date", "2030-02-01")
        self.assertEqual(review.returncode, 0, review.stderr)
        self.assertEqual(self.topic("transfer-review")["state"], "robust")
