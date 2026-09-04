import json

from tests.common import LearnTestCase, base_finish, source_payload


class SourceTests(LearnTestCase):
    def test_source_accepts_session_id_and_attaches_to_live_lesson(self):
        self.start(session="research-session", title="Research Topic", mode="research")
        created = self.cli(
            "source",
            "--session-id",
            "research-session",
            "--json",
            json.dumps(source_payload()),
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        result = json.loads(created.stdout)
        self.assertEqual(result["attached_session_id"], "research-session")
        self.assertIn("[[Sources/org2026guide]]", self.note_for("research-session").read_text(encoding="utf-8"))
        active = self.active_records()[0]
        self.assertEqual(active["source_citekeys"], ["org2026guide"])

        duplicate = source_payload(citekey="another-key", url="https://EXAMPLE.org/Guide")
        deduplicated = self.cli(
            "source",
            "--session-id",
            "research-session",
            "--json",
            json.dumps(duplicate),
        )
        self.assertEqual(deduplicated.returncode, 0, deduplicated.stderr)
        self.assertEqual(json.loads(deduplicated.stdout)["citekey"], "org2026guide")
        self.assertEqual(self.note_for("research-session").read_text(encoding="utf-8").count("[[Sources/org2026guide]]"), 1)

        finish = base_finish()
        finish["source_citekeys"] = []
        finished = self.finish(session="research-session", payload=finish)
        self.assertEqual(finished.returncode, 0, finished.stderr)
        topic = self.topic("research-topic")
        self.assertEqual(topic["source_citekeys"], ["org2026guide"])

    def test_source_rejects_unknown_explicit_session(self):
        result = self.cli(
            "source",
            "--session-id",
            "missing-session",
            "--json",
            json.dumps(source_payload()),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("No active lesson for session missing-session", result.stderr)

    def test_source_deduplicates_normalized_url(self):
        first = self.cli("source", "--json", json.dumps(source_payload()))
        self.assertEqual(first.returncode, 0, first.stderr)
        duplicate = source_payload(citekey="different-key", url="https://EXAMPLE.org/Guide")
        second = self.cli("source", "--json", json.dumps(duplicate))
        self.assertEqual(second.returncode, 0, second.stderr)
        result = json.loads(second.stdout)
        self.assertEqual(result["status"], "deduplicated")
        self.assertEqual(result["citekey"], "org2026guide")
        self.assertEqual(len(list((self.vault / "Learning" / "Sources").glob("*.md"))), 1)

    def test_unverified_source_cannot_support_claims(self):
        payload = source_payload(status="unverified")
        result = self.cli("source", "--json", json.dumps(payload))
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot support factual claims", result.stderr)

    def test_finish_rejects_non_supporting_source(self):
        payload = source_payload(status="metadata-only", claims=[])
        created = self.cli("source", "--json", json.dumps(payload))
        self.assertEqual(created.returncode, 0, created.stderr)
        self.start()
        finish = base_finish()
        finish["source_citekeys"] = ["org2026guide"]
        result = self.finish(payload=finish)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not verified or user-provided", result.stderr)

    def test_verified_source_note_has_required_metadata(self):
        result = self.cli("source", "--json", json.dumps(source_payload()))
        self.assertEqual(result.returncode, 0, result.stderr)
        note = (self.vault / "Learning" / "Sources" / "org2026guide.md").read_text(encoding="utf-8")
        for field in (
            "citekey:",
            "author_or_organization:",
            "year_or_date:",
            "source_type:",
            "verification_status:",
            "retrieval_date:",
            "precise_locator:",
            "## Claims supported",
        ):
            self.assertIn(field, note)
        validate = self.cli("validate")
        self.assertEqual(validate.returncode, 0, validate.stderr)
