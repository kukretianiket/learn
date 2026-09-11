from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
LEARNCTL = ROOT / ".agents" / "skills" / "learn" / "scripts" / "learnctl.py"
INSTALL = ROOT / "codex" / "install.py"
UNINSTALL = ROOT / "codex" / "uninstall.py"


def base_finish(state="introduced"):
    abilities = []
    transfer = {
        "attempted": False,
        "successful": False,
        "independent": False,
        "delayed": False,
        "summary": "Not attempted",
        "evidence_refs": [],
    }
    if state in {"retrievable", "applicable", "robust"}:
        abilities = [
            {
                "ability": "Explained the central model from memory",
                "evidence_type": "recall",
                "independent": True,
                "delayed": state == "robust",
                "evidence_refs": ["current"],
            }
        ]
    if state in {"applicable", "robust"}:
        transfer = {
            "attempted": True,
            "successful": True,
            "independent": True,
            "delayed": state == "robust",
            "summary": "Transferred the model to a changed example",
            "evidence_refs": ["current"],
        }
    return {
        "final_mental_model": "Foundations generate the derived result through explicit dependencies.",
        "dependencies": ["Foundation", "Derived result"],
        "demonstrated_abilities": abilities,
        "hints_required": [],
        "misconceptions": [],
        "unresolved_gaps": [],
        "retrieval_prompts": ["Reconstruct the dependency path without notes."],
        "evidence_state": state,
        "source_citekeys": [],
        "transfer_task_result": transfer,
        "suggested_review_score": 2,
        "reassessment": False,
        "contrary_evidence_refs": [],
    }


def base_review_assessment(state="introduced"):
    finish = base_finish(state)
    return {
        key: finish[key]
        for key in (
            "evidence_state",
            "demonstrated_abilities",
            "transfer_task_result",
            "misconceptions",
            "unresolved_gaps",
            "reassessment",
            "contrary_evidence_refs",
        )
    }


class LearnTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="learn-tests-")
        base = Path(self.temp.name)
        self.home = base / "home"
        self.vault = base / "Vault with spaces Ω"
        self.cwd = base / "project"
        self.home.mkdir()
        self.vault.mkdir()
        self.cwd.mkdir()
        result = self.cli("configure", "--vault", str(self.vault), "--no-open-notes")
        self.assertEqual(result.returncode, 0, result.stderr)

    def tearDown(self):
        self.temp.cleanup()

    def env(self):
        env = os.environ.copy()
        env.pop("CODEX_THREAD_ID", None)
        env.pop("CODEX_SESSION_ID", None)
        env["HOME"] = str(self.home)
        env["PYTHONPYCACHEPREFIX"] = str(self.home / ".pycache")
        return env

    def cli(self, *args, input_text=None):
        return subprocess.run(
            [sys.executable, str(LEARNCTL), *map(str, args)],
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self.env(),
            cwd=str(self.cwd),
        )

    def hook(self, event, session, turn, text, cwd=None):
        payload = {
            "hook_event_name": event,
            "session_id": session,
            "turn_id": turn,
            "cwd": str(cwd or self.cwd),
        }
        if event == "UserPromptSubmit":
            payload["prompt"] = text
        else:
            payload["last_assistant_message"] = text
        return self.cli("hook", input_text=json.dumps(payload))

    def start(self, session="session-a", turn="turn-1", prompt="$learn teach me", title="Test Topic", mode="learn"):
        if not prompt.lstrip().startswith("$learn"):
            prompt = "$learn " + prompt
        self.hook("UserPromptSubmit", session, turn, prompt)
        result = self.cli(
            "start",
            "--title",
            title,
            "--goal",
            "Build a connected working model",
            "--mode",
            mode,
            "--session-id",
            session,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def finish(self, session="session-a", payload=None):
        payload = payload or base_finish()
        return self.cli("finish", "--session-id", session, "--json", json.dumps(payload))

    def active_records(self):
        folder = self.vault / "Learning" / "_system" / "active"
        records = []
        for path in folder.glob("*.json"):
            binding = json.loads(path.read_text(encoding="utf-8"))
            if binding.get("lesson_id"):
                lesson = self.vault / "Learning" / "_system" / "lessons" / f"{binding['lesson_id']}.json"
                record = json.loads(lesson.read_text(encoding="utf-8"))
                record["session_id"] = binding["session_id"]
                records.append(record)
            else:
                records.append(binding)
        return records

    def topic(self, slug="test-topic"):
        path = self.vault / "Learning" / "_system" / "topics" / f"{slug}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def note_for(self, session="session-a"):
        active = next(item for item in self.active_records() if item["session_id"] == session)
        return self.vault / active["note_relative"]

    def close_after_finish(self, session, turn=None, text="Lesson complete."):
        if turn is None:
            record = next(item for item in self.active_records() if item["session_id"] == session)
            turn = record.get("expected_finishing", {}).get("turn_id") or "final-turn"
        result = self.hook("Stop", session, turn, text)
        self.assertEqual(result.returncode, 0, result.stderr)


def install_env(home: Path):
    env = os.environ.copy()
    env.pop("CODEX_THREAD_ID", None)
    env.pop("CODEX_SESSION_ID", None)
    env["HOME"] = str(home)
    env["PYTHONPYCACHEPREFIX"] = str(home / ".pycache")
    return env


def run_install(home: Path, vault: Path, *args):
    return subprocess.run(
        [sys.executable, str(INSTALL), "--vault", str(vault), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=install_env(home),
        cwd=str(ROOT),
    )


def source_payload(citekey="org2026guide", status="verified", claims=None, url="https://example.org/Guide/"):
    return {
        "citekey": citekey,
        "title": "Authoritative Guide",
        "author_or_organization": "Example Organization",
        "year_or_date": "2026",
        "url": url,
        "doi": "",
        "local_path": "",
        "source_type": "official documentation",
        "verification_status": status,
        "retrieval_date": dt.date.today().isoformat(),
        "precise_locator": "Section 2",
        "claims_supported": ["The documented mechanism behaves as described."] if claims is None else claims,
    }
