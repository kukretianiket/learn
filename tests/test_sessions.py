import json

from tests.common import LearnTestCase, base_finish


class SessionTests(LearnTestCase):
    def test_mcq_positions_are_randomized_once_per_turn_and_support_multi_select(self):
        self.start(session="session-a", title="Random Positions")
        first = self.cli(
            "context", "--session-id", "session-a", "--next-mcq", "--choices", "4", "--correct-count", "1"
        )
        repeat = self.cli(
            "context", "--session-id", "session-a", "--next-mcq", "--choices", "4", "--correct-count", "1"
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(json.loads(first.stdout), json.loads(repeat.stdout))

        changed = self.cli(
            "context", "--session-id", "session-a", "--next-mcq", "--choices", "4", "--correct-count", "2"
        )
        self.assertEqual(changed.returncode, 2)
        self.assertIn("already issued", changed.stderr)

        self.hook("UserPromptSubmit", "session-a", "turn-2", "My first diagnostic answer")
        multi = self.cli(
            "context", "--session-id", "session-a", "--next-mcq", "--choices", "5", "--correct-count", "2"
        )
        self.assertEqual(multi.returncode, 0, multi.stderr)
        payload = json.loads(multi.stdout)
        self.assertEqual(payload["selection"], "multi-select")
        self.assertEqual(len(payload["correct_option_labels"]), 2)
        self.assertEqual(len(set(payload["correct_option_labels"])), 2)
        self.assertTrue(set(payload["correct_option_labels"]) <= {"A", "B", "C", "D", "E"})

        self.start(session="session-b", title="Separate Positions")
        other = self.cli(
            "context", "--session-id", "session-b", "--next-mcq", "--choices", "4", "--correct-count", "1"
        )
        self.assertEqual(other.returncode, 0, other.stderr)
        records = {item["session_id"]: item for item in self.active_records()}
        self.assertEqual(len(records["session-a"]["mcq_position_state"]["history"]), 2)
        self.assertEqual(len(records["session-b"]["mcq_position_state"]["history"]), 1)

    def test_first_prompt_is_captured_once(self):
        self.start(session="unique-session-id", prompt="$learn Explain gradients from first principles")
        note = self.note_for("unique-session-id").read_text(encoding="utf-8")
        self.assertEqual(note.count("$learn Explain gradients from first principles"), 1)
        self.assertIn('session_id: "unique-session-id"', note)

    def test_interrupted_session_recovery(self):
        first = self.start(prompt="begin this lesson")
        result = self.cli(
            "start",
            "--title",
            "Ignored replacement title",
            "--goal",
            "resume",
            "--session-id",
            "session-a",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        resumed = json.loads(result.stdout)
        self.assertEqual(resumed["status"], "resumed")
        self.assertEqual(resumed["note"], first["note"])
        self.assertEqual(len(list((self.vault / "Learning" / "Sessions").glob("*.md"))), 1)

    def test_close_after_final_stop(self):
        self.start()
        result = self.finish(payload=base_finish("introduced"))
        self.assertEqual(result.returncode, 0, result.stderr)
        note = self.note_for()
        self.assertTrue(self.active_records()[0]["close_after_stop"])
        self.close_after_finish("session-a", text="Your next review is scheduled.")
        self.assertEqual(self.active_records(), [])
        text = note.read_text(encoding="utf-8")
        self.assertIn("Your next review is scheduled.", text)
        self.assertIn("status: completed", text)

    def test_markdown_math_and_callouts_are_preserved(self):
        self.start()
        message = "> [!definition] Gradient\n> Local linear change\n\nInline $f(x)$ and display:\n\n$$\n\\nabla f = (1,2)\n$$"
        self.hook("Stop", "session-a", "math-turn", message)
        text = self.note_for().read_text(encoding="utf-8")
        self.assertIn(message, text)
        self.assertEqual(text.count("$$"), 2)

    def test_fenced_code_and_mermaid_are_preserved(self):
        self.start()
        message = "```python\nprint('edge')\n```\n\n```mermaid\nflowchart TD\n  A[\"Root\"] --> B[\"Result\"]\n```"
        self.hook("Stop", "session-a", "diagram-turn", message)
        text = self.note_for().read_text(encoding="utf-8")
        self.assertIn(message, text)
        self.assertIn('A["Root"] --> B["Result"]', text)
        validation = self.cli("validate", "--session", str(self.note_for()))
        self.assertEqual(validation.returncode, 0, validation.stderr)
