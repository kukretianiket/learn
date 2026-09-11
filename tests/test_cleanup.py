import json

from tests.common import LearnTestCase


class CleanupTests(LearnTestCase):
    def records_for(self, folder, session_id):
        paths = []
        for path in (self.vault / "Learning" / "_system" / folder).glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("session_id") == session_id:
                paths.append(path)
        return paths

    def test_cleanup_dry_run_then_apply_preserves_live_and_durable_state(self):
        self.start(session="live-session", title="Live Topic")
        live_note = self.note_for("live-session")
        self.hook("Stop", "live-session", "live-answer", "Keep this active answer")
        live_events = self.records_for("events", "live-session")

        self.start(session="closed-session", title="Closed Topic")
        closed_note = self.note_for("closed-session")
        self.hook("Stop", "closed-session", "closed-answer", "Closed answer")
        closed_events = self.records_for("events", "closed-session")
        aborted = self.cli("abort", "--session-id", "closed-session")
        self.assertEqual(aborted.returncode, 0, aborted.stderr)

        self.hook("UserPromptSubmit", "fresh-pending", "fresh-turn", "$learn A recent prompt")
        fresh_pending = self.records_for("pending", "fresh-pending")[0]
        self.hook("UserPromptSubmit", "old-pending", "old-turn", "$learn An abandoned prompt")
        old_pending = self.records_for("pending", "old-pending")[0]
        old_record = json.loads(old_pending.read_text(encoding="utf-8"))
        old_record["created_at"] = "2000-01-01T00:00:00+00:00"
        old_pending.write_text(json.dumps(old_record), encoding="utf-8")

        self.start(session="orphan-session", title="Orphan Topic")
        orphan_note = self.note_for("orphan-session")
        orphan_note.unlink()
        orphan_active = self.records_for("active", "orphan-session")[0]

        asset = self.vault / "Learning" / "Assets" / "keep.txt"
        asset.write_text("durable", encoding="utf-8")
        topics_before = set((self.vault / "Learning" / "_system" / "topics").glob("*.json"))

        preview = self.cli("cleanup", "--json-output")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        preview_payload = json.loads(preview.stdout)
        self.assertEqual(preview_payload["status"], "dry-run")
        self.assertIn("live-session", preview_payload["protected_active_session_ids"])
        self.assertTrue(old_pending.exists())
        self.assertTrue(orphan_active.exists())
        self.assertTrue(all(path.exists() for path in closed_events))

        applied = self.cli("cleanup", "--apply", "--json-output")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        payload = json.loads(applied.stdout)
        self.assertEqual(payload["status"], "cleaned")
        self.assertEqual(payload["session_identifier"], "Codex session_id")
        self.assertTrue(live_note.exists())
        self.assertTrue(all(path.exists() for path in live_events))
        self.assertTrue(self.records_for("active", "live-session"))
        self.assertTrue(self.records_for("pending", "live-session"))
        self.assertFalse(old_pending.exists())
        self.assertTrue(fresh_pending.exists())
        self.assertTrue(orphan_active.exists())
        self.assertTrue(any("needs rendering repair" in warning for warning in payload["warnings"]))
        self.assertTrue(all(not path.exists() for path in closed_events))
        self.assertTrue(closed_note.exists())
        self.assertEqual(set((self.vault / "Learning" / "_system" / "topics").glob("*.json")), topics_before)
        self.assertEqual(asset.read_text(encoding="utf-8"), "durable")

    def test_cleanup_rejects_negative_retention(self):
        result = self.cli("cleanup", "--pending-days", "-1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("zero or greater", result.stderr)
