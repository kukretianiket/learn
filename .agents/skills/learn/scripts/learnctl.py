#!/usr/bin/env python3
"""Local state and Markdown utility for the Codex + Obsidian learning skill."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from urllib.parse import quote, urlsplit, urlunsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


CONFIG_RELATIVE = Path(".config/learn-codex/config.json")
DEFAULT_INTERVALS = [1, 3, 7, 14, 30, 60, 120]
DEFAULT_PENDING_RETENTION_DAYS = 7
TOPIC_STATES = ["unseen", "introduced", "retrievable", "applicable", "robust"]
SOURCE_STATUSES = {"verified", "metadata-only", "user-provided", "unverified", "rejected"}
SUPPORTING_STATUSES = {"verified", "user-provided"}
EVIDENCE_TYPES = {"recall", "explanation", "application", "transfer", "mcq"}
WINDOW_LAYOUTS = {"none", "desktop-split"}
WINDOW_SIDES = {"left", "right"}
MARKER_PAIRS = {
    "TRANSCRIPT": ("<!-- LEARN:TRANSCRIPT:BEGIN -->", "<!-- LEARN:TRANSCRIPT:END -->"),
    "SYNTHESIS": ("<!-- LEARN:SYNTHESIS:BEGIN -->", "<!-- LEARN:SYNTHESIS:END -->"),
    "SOURCES": ("<!-- LEARN:SOURCES:BEGIN -->", "<!-- LEARN:SOURCES:END -->"),
    "TOPIC": ("<!-- LEARN:BEGIN -->", "<!-- LEARN:END -->"),
}
REQUIRED_FINISH_KEYS = {
    "final_mental_model",
    "dependencies",
    "demonstrated_abilities",
    "hints_required",
    "misconceptions",
    "unresolved_gaps",
    "retrieval_prompts",
    "evidence_state",
    "source_citekeys",
    "transfer_task_result",
    "suggested_review_score",
}
REQUIRED_SOURCE_KEYS = {
    "citekey",
    "title",
    "author_or_organization",
    "year_or_date",
    "source_type",
    "verification_status",
    "retrieval_date",
    "precise_locator",
    "claims_supported",
}


class LearnError(RuntimeError):
    pass


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_now() -> str:
    return now_utc().replace(microsecond=0).isoformat()


def today() -> dt.date:
    return dt.date.today()


def config_path() -> Path:
    return Path.home() / CONFIG_RELATIVE


def atomic_write(path: Path, text: str) -> None:
    """Write UTF-8 text by fsyncing a sibling temporary file then replacing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp), str(path))
    finally:
        if temp.exists():
            temp.unlink()


def write_json(path: Path, value: object) -> None:
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LearnError(f"Invalid JSON in {path}: {exc}") from exc


def default_config(vault_path: Path, learning_folder: str = "Learning") -> dict:
    return {
        "vault_path": str(vault_path.resolve()),
        "learning_folder": learning_folder,
        "open_notes_automatically": True,
        "obsidian_vault_name": None,
        "review_intervals": list(DEFAULT_INTERVALS),
        "window_layout": "desktop-split",
        "codex_side": "right",
    }


def validate_config(config: dict) -> dict:
    required = {"vault_path", "learning_folder", "open_notes_automatically", "review_intervals"}
    missing = sorted(required - set(config))
    if missing:
        raise LearnError("Configuration is missing: " + ", ".join(missing))
    vault = Path(str(config["vault_path"])).expanduser()
    if not vault.is_absolute():
        raise LearnError("vault_path must be absolute")
    folder = str(config["learning_folder"]).strip().strip("/\\")
    if not folder or folder in {".", ".."} or ".." in Path(folder).parts:
        raise LearnError("learning_folder must be a safe path inside the vault")
    intervals = config["review_intervals"]
    if not isinstance(intervals, list) or not intervals or any(
        not isinstance(day, int) or isinstance(day, bool) or day <= 0 for day in intervals
    ):
        raise LearnError("review_intervals must be a non-empty list of positive integers")
    if intervals != sorted(set(intervals)):
        raise LearnError("review_intervals must be unique and increasing")
    config = dict(config)
    config["vault_path"] = str(vault.resolve())
    config["learning_folder"] = folder
    config["open_notes_automatically"] = bool(config["open_notes_automatically"])
    config["obsidian_vault_name"] = config.get("obsidian_vault_name") or None
    config["window_layout"] = config.get("window_layout", "desktop-split")
    config["codex_side"] = config.get("codex_side", "right")
    if config["window_layout"] not in WINDOW_LAYOUTS:
        raise LearnError("window_layout must be 'none' or 'desktop-split'")
    if config["codex_side"] not in WINDOW_SIDES:
        raise LearnError("codex_side must be 'left' or 'right'")
    return config


def load_config(required: bool = True) -> dict | None:
    path = config_path()
    if not path.exists():
        if required:
            raise LearnError(f"Not configured. Run learnctl configure or codex/install.py ({path})")
        return None
    return validate_config(read_json(path))


def learning_root(config: dict) -> Path:
    vault = Path(config["vault_path"])
    root = (vault / config["learning_folder"]).resolve()
    try:
        root.relative_to(vault.resolve())
    except ValueError as exc:
        raise LearnError("Learning folder resolves outside the vault") from exc
    return root


def system_root(config: dict) -> Path:
    return learning_root(config) / "_system"


def ensure_layout(config: dict) -> None:
    root = learning_root(config)
    for relative in (
        "Sessions",
        "Topics",
        "Sources",
        "Assets",
        "_system",
        "_system/pending",
        "_system/active",
        "_system/events",
        "_system/topics",
        "_system/sources",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def state_lock(config: dict):
    ensure_layout(config)
    lock_path = system_root(config) / ".learnctl.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def id_token(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")[:48] or "session"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{clean}-{digest}"


def slugify(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    ascii_text = folded.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.casefold()).strip("-")
    if not slug:
        slug = "topic-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return slug[:80]


def normalized_topic(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"\w+", folded, flags=re.UNICODE))


def yaml_string(value: object) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def pending_path(config: dict, session_id: str) -> Path:
    return system_root(config) / "pending" / f"{id_token(session_id)}.json"


def active_path(config: dict, session_id: str) -> Path:
    return system_root(config) / "active" / f"{id_token(session_id)}.json"


def event_path(config: dict, session_id: str, turn_id: str, role: str) -> Path:
    key = f"{session_id}\0{turn_id}\0{role}"
    return system_root(config) / "events" / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.json"


def topic_json_path(config: dict, slug: str) -> Path:
    return system_root(config) / "topics" / f"{slug}.json"


def source_json_path(config: dict, citekey: str) -> Path:
    return system_root(config) / "sources" / f"{citekey}.json"


def read_active(config: dict, session_id: str) -> dict | None:
    return read_json(active_path(config, session_id), None)


def replace_managed(text: str, begin: str, end: str, body: str) -> str:
    first = text.find(begin)
    last = text.find(end)
    if first < 0 or last < 0 or last < first:
        raise LearnError(f"Missing or invalid generated-state markers: {begin} / {end}")
    content_start = first + len(begin)
    return text[:content_start] + "\n" + body.rstrip() + "\n" + text[last:]


def append_transcript(note: Path, role: str, text: str, timestamp: str | None = None) -> str | None:
    if not text:
        return None
    current = note.read_text(encoding="utf-8")
    begin, end = MARKER_PAIRS["TRANSCRIPT"]
    first = current.find(begin)
    last = current.find(end)
    if first < 0 or last < first:
        raise LearnError(f"Session note has invalid transcript markers: {note}")
    label = "Learner" if role == "user" else "Tutor"
    icon = "🧑" if role == "user" else "🤖"
    heading = f"{icon} {label} · {timestamp or iso_now()}"
    block = f"---\n\n### {heading}\n\n{text.rstrip()}\n\n"
    updated = current[:last] + block + current[last:]
    atomic_write(note, updated)
    return heading


def mark_event(config: dict, session_id: str, turn_id: str, role: str) -> None:
    write_json(
        event_path(config, session_id, turn_id, role),
        {"session_id": session_id, "turn_id": turn_id, "role": role, "logged_at": iso_now()},
    )


def hook_event_name(data: dict) -> str | None:
    raw = data.get("hook_event_name") or data.get("event_name") or data.get("hookEventName")
    if raw:
        normalized = re.sub(r"[^a-z]", "", str(raw).casefold())
        if normalized == "userpromptsubmit":
            return "UserPromptSubmit"
        if normalized == "stop":
            return "Stop"
    if "last_assistant_message" in data:
        return "Stop"
    if "prompt" in data:
        return "UserPromptSubmit"
    return None


def hook_session_id(data: dict) -> str:
    value = data.get("session_id") or data.get("sessionId") or data.get("thread_id") or data.get("threadId")
    return str(value or "").strip()


def hook_turn_id(data: dict) -> str:
    value = data.get("turn_id") or data.get("turnId")
    return str(value or "").strip()


def run_hook(data: dict) -> None:
    """Handle a Codex hook. Caller intentionally suppresses all output/errors."""
    config = load_config(required=False)
    if not config:
        return
    ensure_layout(config)
    event = hook_event_name(data)
    session_id = hook_session_id(data)
    turn_id = hook_turn_id(data)
    if not event or not session_id or not turn_id:
        return

    follow_request = None
    with state_lock(config):
        if event == "UserPromptSubmit":
            prompt = data.get("prompt")
            if not isinstance(prompt, str):
                return
            record = {
                "session_id": session_id,
                "turn_id": turn_id,
                "cwd": str(data.get("cwd") or ""),
                "prompt": prompt,
                "created_at": iso_now(),
                "created_at_ns": int(now_utc().timestamp() * 1_000_000_000),
            }
            write_json(pending_path(config, session_id), record)
            active = read_active(config, session_id)
            marker = event_path(config, session_id, turn_id, "user")
            if active and not marker.exists():
                note = Path(config["vault_path"]) / active["note_relative"]
                append_transcript(note, "user", prompt)
                mark_event(config, session_id, turn_id, "user")
            return

        active = read_active(config, session_id)
        if not active:
            return
        message = data.get("last_assistant_message")
        if not isinstance(message, str) or not message:
            return
        marker = event_path(config, session_id, turn_id, "assistant")
        if not marker.exists():
            note = Path(config["vault_path"]) / active["note_relative"]
            heading = append_transcript(note, "assistant", message)
            mark_event(config, session_id, turn_id, "assistant")
            follow_request = (note, dict(active), heading)
        if active.get("close_after_stop"):
            note = Path(config["vault_path"]) / active["note_relative"]
            atomic_write(note, update_session_status(note.read_text(encoding="utf-8"), "completed"))
            path = active_path(config, session_id)
            if path.exists():
                path.unlink()
    if follow_request and config.get("open_notes_automatically"):
        note, active, heading = follow_request
        try:
            follow_lesson_output(config, note, active, heading)
        except (LearnError, OSError, subprocess.SubprocessError):
            pass


def newest_pending(config: dict, cwd: str | None = None, session_id: str | None = None) -> dict:
    if session_id:
        record = read_json(pending_path(config, session_id), None)
        if not record:
            raise LearnError(f"No pending prompt for Codex session {session_id}")
        return record
    records = []
    for path in (system_root(config) / "pending").glob("*.json"):
        record = read_json(path, None)
        if record:
            records.append(record)
    if not records:
        raise LearnError("No pending Codex prompt found. Invoke $learn from a hooked Codex session first.")
    wanted = str(Path(cwd or os.getcwd()).resolve())
    matching = []
    for record in records:
        raw_cwd = record.get("cwd")
        try:
            record_cwd = str(Path(raw_cwd).resolve()) if raw_cwd else ""
        except OSError:
            record_cwd = str(raw_cwd or "")
        if record_cwd == wanted:
            matching.append(record)
    choices = matching or records
    return max(choices, key=lambda item: (item.get("created_at_ns", 0), item.get("created_at", "")))


def topic_record(config: dict, slug: str) -> dict | None:
    return read_json(topic_json_path(config, slug), None)


def find_related_topic(config: dict, title: str) -> dict | None:
    needle = normalized_topic(title)
    for path in (system_root(config) / "topics").glob("*.json"):
        record = read_json(path, None)
        if not record:
            continue
        names = [record.get("title", ""), record.get("slug", "")] + list(record.get("aliases", []))
        if needle and needle in {normalized_topic(str(name)) for name in names}:
            return record
    return None


def new_topic(title: str, slug: str) -> dict:
    return {
        "schema_version": 1,
        "slug": slug,
        "title": title,
        "aliases": [],
        "state": "unseen",
        "mental_model": "Not established yet.",
        "dependencies": [],
        "demonstrated_abilities": [],
        "hints_required": [],
        "misconceptions": [],
        "unresolved_gaps": [],
        "retrieval_prompts": [],
        "source_citekeys": [],
        "sessions": [],
        "review": {"interval_index": 0, "last_review": None, "next_review": None, "history": []},
        "updated_at": iso_now(),
    }


def list_items(items) -> str:
    if not items:
        return "- None recorded"
    lines = []
    for item in items:
        if isinstance(item, dict):
            text = item.get("ability") or item.get("summary") or json.dumps(item, ensure_ascii=False, sort_keys=True)
        else:
            text = str(item)
        lines.append(f"- {text}")
    return "\n".join(lines)


def render_topic_note(config: dict, topic: dict) -> Path:
    root = learning_root(config)
    path = root / "Topics" / f"{topic['slug']}.md"
    review = topic.get("review", {})
    frontmatter = "\n".join(
        [
            "---",
            "learn_type: topic",
            f"topic_id: {yaml_string(topic['slug'])}",
            f"title: {yaml_string(topic['title'])}",
            f"state: {yaml_string(topic['state'])}",
            f"last_review: {yaml_string(review.get('last_review') or '')}",
            f"next_review: {yaml_string(review.get('next_review') or '')}",
            "---",
        ]
    )
    sessions = [f"[[{Path(item).with_suffix('')}]]" for item in topic.get("sessions", [])]
    sources = [f"[[Sources/{key}]]" for key in topic.get("source_citekeys", [])]
    unaided = [
        item
        for item in topic.get("demonstrated_abilities", [])
        if isinstance(item, dict) and item.get("independent") is True and item.get("evidence_type") != "mcq"
    ]
    managed = f"""## Current mental model

{topic.get('mental_model') or 'Not established yet.'}

## Dependency path

{list_items(topic.get('dependencies'))}

## Demonstrated unaided abilities

{list_items(unaided)}

## Misconceptions repaired

{list_items(topic.get('misconceptions'))}

## Remaining gaps

{list_items(topic.get('unresolved_gaps'))}

## Retrieval prompts

{list_items(topic.get('retrieval_prompts'))}

## Evidence and sessions

{list_items(sessions)}

## Sources

{list_items(sources)}

## Review state

- Last review: {review.get('last_review') or 'Not reviewed'}
- Next review: {review.get('next_review') or 'Not scheduled'}
"""
    if path.exists():
        old = path.read_text(encoding="utf-8")
        begin, end = MARKER_PAIRS["TOPIC"]
        body = replace_managed(old, begin, end, managed)
        second = body.find("---", 3) if body.startswith("---") else -1
        if second >= 0:
            body = frontmatter + body[second + 3 :]
        else:
            raise LearnError(f"Topic note has invalid frontmatter: {path}")
    else:
        begin, end = MARKER_PAIRS["TOPIC"]
        body = f"{frontmatter}\n\n# {topic['title']}\n\n{begin}\n{managed.rstrip()}\n{end}\n\n## My notes\n\nWrite anything here. This section is never replaced by learnctl.\n"
    atomic_write(path, body)
    return path


def session_template() -> str:
    path = Path(__file__).resolve().parent.parent / "assets" / "session-template.md"
    if not path.exists():
        raise LearnError(f"Missing session template: {path}")
    return path.read_text(encoding="utf-8")


def render_session_template(
    title: str, goal: str, mode: str, slug: str, starting_point: str, session_id: str
) -> str:
    values = {
        "title_yaml": yaml_string(title),
        "goal_yaml": yaml_string(goal),
        "mode_yaml": yaml_string(mode),
        "started_yaml": yaml_string(iso_now()),
        "session_id_yaml": yaml_string(session_id),
        "topic_link_yaml": yaml_string(f"[[Topics/{slug}]]"),
        "title": title,
        "goal": goal,
        "starting_point": starting_point,
    }
    text = session_template()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def obsidian_uri(config: dict, note_path: Path, heading: str | None = None) -> str:
    vault = config.get("obsidian_vault_name")
    suffix = f"#{heading}" if heading else ""
    if vault:
        relative = note_path.resolve().relative_to(Path(config["vault_path"]).resolve()).as_posix()
        return "obsidian://open?vault=" + quote(str(vault), safe="") + "&file=" + quote(relative + suffix, safe="")
    return "obsidian://open?path=" + quote(str(note_path.resolve()) + suffix, safe="")


def open_note(config: dict, note_path: Path, heading: str | None = None) -> str:
    cli = shutil.which("obsidian")
    if cli:
        relative = note_path.resolve().relative_to(Path(config["vault_path"]).resolve()).as_posix()
        if heading:
            relative += f"#{heading}"
        command = [cli]
        if config.get("obsidian_vault_name"):
            command.append(f"vault={config['obsidian_vault_name']}")
        command.extend(["open", f"path={relative}"])
        try:
            result = subprocess.run(
                command,
                cwd=str(Path(config["vault_path"]).resolve()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                return "obsidian-cli"
        except (OSError, subprocess.TimeoutExpired):
            pass
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["open", obsidian_uri(config, note_path, heading)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                return "obsidian-uri"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return "not-opened"


def latest_tutor_heading(note: Path) -> str | None:
    headings = re.findall(r"(?m)^### (🤖 Tutor · .+)$", note.read_text(encoding="utf-8"))
    return headings[-1] if headings else None


def force_obsidian_reading_view(host_bundle_id: str | None, terminal_tty: str | None = None) -> dict:
    if sys.platform != "darwin":
        return {"status": "unsupported"}
    if host_bundle_id and not re.fullmatch(r"[A-Za-z0-9._-]+", host_bundle_id):
        host_bundle_id = None
    host_literal = json.dumps(host_bundle_id) if host_bundle_id else None
    restore = ""
    if host_bundle_id == "com.apple.Terminal":
        restore = r'''
tell application id "com.apple.Terminal"
  if (count of windows) > 0 then
    set targetTTY to item 1 of argv
    set hostWindow to front window
    if targetTTY is not "" then
      set foundWindow to false
      repeat with candidateWindow in windows
        repeat with candidateTab in tabs of candidateWindow
          if tty of candidateTab is targetTTY then
            set hostWindow to contents of candidateWindow
            set foundWindow to true
            exit repeat
          end if
        end repeat
        if foundWindow then exit repeat
      end repeat
    end if
    try
      set index of hostWindow to 1
    end try
    activate
  end if
end tell'''
    elif host_literal:
        restore = f"tell application id {host_literal} to activate"
    script = r'''on run argv
tell application id "md.obsidian" to activate
delay 0.35
tell application "System Events"
  try
    set obsidianProcess to first application process whose bundle identifier is "md.obsidian"
    tell menu 1 of menu bar item "View" of menu bar 1 of obsidianProcess
      if exists menu item "Reading View" then click menu item "Reading View"
    end tell
  on error errorMessage
    return "LEARN_READING_VIEW_FAILED|" & errorMessage
  end try
end tell
delay 0.15
__RESTORE_HOST__
return "OK"
end run'''.replace("__RESTORE_HOST__", restore)
    try:
        result = run_osascript(script, [terminal_tty or ""])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "message": str(exc)}
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0 or output != "OK":
        return {"status": "failed", "message": output or "Could not select Obsidian Reading View"}
    return {"status": "reading", "focus_restored": bool(host_bundle_id)}


def follow_lesson_output(config: dict, note: Path, active: dict, heading: str | None = None) -> dict:
    opened = open_note(config, note, heading or latest_tutor_heading(note))
    if opened == "not-opened":
        return {"status": "not-opened"}
    host_bundle_id = active.get("codex_host_bundle_id") or detect_codex_host_bundle_id()
    terminal_tty = detect_ancestor_tty() if host_bundle_id == "com.apple.Terminal" else None
    view = force_obsidian_reading_view(host_bundle_id, terminal_tty)
    return {"status": "followed", "opened": opened, "view": view}


def opening_needs_escalation(config: dict | None = None) -> bool:
    """Return true when macOS GUI launch is known to be outside the active Codex sandbox."""
    if sys.platform != "darwin" or not os.environ.get("CODEX_SANDBOX"):
        return False
    layout_needs_accessibility = bool(config and config.get("window_layout") == "desktop-split")
    return layout_needs_accessibility or not shutil.which("obsidian")


def detect_codex_host_bundle_id(environ: dict | None = None) -> str | None:
    values = os.environ if environ is None else environ
    inherited_bundle = str(values.get("__CFBundleIdentifier", "")).strip()
    if re.fullmatch(r"[A-Za-z0-9._-]+", inherited_bundle) and inherited_bundle not in {
        "com.apple.osascript",
        "org.python.python",
    }:
        return inherited_bundle
    origin = str(values.get("CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "")).casefold()
    if "vscode" in origin or str(values.get("TERM_PROGRAM", "")).casefold() == "vscode":
        return "com.microsoft.VSCode"
    if origin in {"codex_app", "chatgpt", "codex-desktop"} or "desktop" in origin:
        return "com.openai.codex"
    terminal = str(values.get("TERM_PROGRAM", "")).casefold()
    if terminal == "apple_terminal":
        return "com.apple.Terminal"
    if terminal in {"iterm.app", "iterm2"}:
        return "com.googlecode.iterm2"
    terminal_bundles = {
        "warpterminal": "dev.warp.Warp-Stable",
        "warp": "dev.warp.Warp-Stable",
        "ghostty": "com.mitchellh.ghostty",
        "wezterm": "com.github.wez.wezterm",
        "apple_terminal": "com.apple.Terminal",
    }
    if terminal in terminal_bundles:
        return terminal_bundles[terminal]
    return None


def detect_ancestor_tty() -> str | None:
    """Find the controlling TTY of the Codex CLI ancestor, if one exists."""
    for descriptor in (0, 1, 2):
        try:
            return os.ttyname(descriptor)
        except OSError:
            pass
    pid = os.getpid()
    for _ in range(12):
        try:
            result = subprocess.run(
                ["/bin/ps", "-o", "ppid=,tty=", "-p", str(pid)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        fields = result.stdout.split()
        if result.returncode != 0 or len(fields) < 2:
            return None
        try:
            parent_pid = int(fields[0])
        except ValueError:
            return None
        tty = fields[1]
        if tty not in {"?", "??", "-"}:
            return tty if tty.startswith("/dev/") else f"/dev/{tty}"
        if parent_pid <= 1 or parent_pid == pid:
            return None
        pid = parent_pid
    return None


def run_osascript(script: str, arguments: list[str] | None = None, language: str | None = None):
    command = ["/usr/bin/osascript"]
    if language:
        command.extend(["-l", language])
    command.extend(["-e", script])
    command.extend(arguments or [])
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15, check=False)


def macos_screen_frames() -> list[dict]:
    script = r'''ObjC.import("AppKit");
const screens = $.NSScreen.screens;
const count = Number(screens.count);
if (count === 0) throw new Error("no displays available");
const mainHeight = Number($.NSScreen.mainScreen.frame.size.height);
const output = [];
for (let index = 0; index < count; index++) {
  const frame = screens.objectAtIndex(index).visibleFrame;
  output.push({
    x: Math.round(Number(frame.origin.x)),
    y: Math.round(mainHeight - Number(frame.origin.y) - Number(frame.size.height)),
    width: Math.round(Number(frame.size.width)),
    height: Math.round(Number(frame.size.height))
  });
}
JSON.stringify(output);'''
    try:
        result = run_osascript(script, language="JavaScript")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LearnError(f"Could not read macOS display geometry: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-400:]
        raise LearnError(f"Could not read macOS display geometry: {detail or 'osascript failed'}")
    try:
        frames = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LearnError("macOS returned invalid display geometry") from exc
    if not isinstance(frames, list) or not frames:
        raise LearnError("macOS reported no displays")
    return frames


def choose_screen(frames: list[dict], window_bounds: tuple[int, int, int, int]) -> dict:
    wx, wy, width, height = window_bounds
    best = frames[0]
    best_overlap = -1
    for frame in frames:
        left = max(wx, int(frame["x"]))
        top = max(wy, int(frame["y"]))
        right = min(wx + width, int(frame["x"]) + int(frame["width"]))
        bottom = min(wy + height, int(frame["y"]) + int(frame["height"]))
        overlap = max(0, right - left) * max(0, bottom - top)
        if overlap > best_overlap:
            best = frame
            best_overlap = overlap
    return best


def split_window_bounds(frame: dict, codex_side: str) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    x = int(frame["x"])
    y = int(frame["y"])
    width = int(frame["width"])
    height = int(frame["height"])
    left_width = width // 2
    left = (x, y, left_width, height)
    right = (x + left_width, y, width - left_width, height)
    return (left, right) if codex_side == "left" else (right, left)


def automation_failure_status(detail: str) -> str:
    lowered = detail.casefold()
    if any(
        marker in lowered
        for marker in (
            "learn_accessibility_required",
            "not allowed assistive access",
            "not authorized to send apple events",
            "-1743",
        )
    ):
        return "permission-required"
    if "learn_host_not_found" in lowered:
        return "host-not-found"
    if "learn_obsidian_not_found" in lowered:
        return "obsidian-window-not-found"
    return "failed"


def automation_failure_result(detail: str, host_bundle_id: str) -> dict:
    status = automation_failure_status(detail)
    host_name = {
        "com.microsoft.VSCode": "Visual Studio Code",
        "com.openai.codex": "Codex",
        "com.apple.Terminal": "Terminal",
        "com.googlecode.iterm2": "iTerm",
        "dev.warp.Warp-Stable": "Warp",
        "com.mitchellh.ghostty": "Ghostty",
        "com.github.wez.wezterm": "WezTerm",
    }.get(host_bundle_id, host_bundle_id)
    messages = {
        "permission-required": (
            f"Enable {host_name} in System Settings → Privacy & Security → Accessibility, "
            "allow any Automation prompt for System Events or Obsidian, then retry the Learn session"
        ),
        "host-not-found": f"No accessible window was found for {host_name}",
        "obsidian-window-not-found": "Obsidian opened, but no accessible Obsidian window was found",
    }
    return {"status": status, "message": messages.get(status, detail or "Window automation failed")}


def tile_macos_windows(host_bundle_id: str, codex_side: str, terminal_tty: str | None = None) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", host_bundle_id):
        return {"status": "host-not-found", "message": "The Codex host bundle identifier is invalid"}
    host_literal = json.dumps(host_bundle_id)
    query = r'''on run argv
if __HOST_BUNDLE__ is "com.apple.Terminal" then
  set targetTTY to item 1 of argv
  tell application id "com.apple.Terminal"
    if (count of windows) is 0 then return "LEARN_HOST_NOT_FOUND"
    set hostWindow to front window
    if targetTTY is not "" then
      set foundWindow to false
      repeat with candidateWindow in windows
        repeat with candidateTab in tabs of candidateWindow
          if tty of candidateTab is targetTTY then
            set hostWindow to contents of candidateWindow
            set foundWindow to true
            exit repeat
          end if
        end repeat
        if foundWindow then exit repeat
      end repeat
    end if
    set hostBounds to bounds of hostWindow
  end tell
  set hostWidth to (item 3 of hostBounds) - (item 1 of hostBounds)
  set hostHeight to (item 4 of hostBounds) - (item 2 of hostBounds)
  return "OK|" & (item 1 of hostBounds as text) & "|" & (item 2 of hostBounds as text) & "|" & (hostWidth as text) & "|" & (hostHeight as text)
end if
tell application id __HOST_BUNDLE__ to activate
delay 0.4
tell application "System Events"
  if UI elements enabled is false then return "LEARN_ACCESSIBILITY_REQUIRED"
  try
    set hostProcess to first application process whose bundle identifier is __HOST_BUNDLE__
  on error
    return "LEARN_HOST_NOT_FOUND"
  end try
  if (count of windows of hostProcess) is 0 then return "LEARN_HOST_NOT_FOUND"
  set hostWindow to window 1 of hostProcess
  set largestArea to 0
  repeat with candidateWindow in windows of hostProcess
    try
      set candidateSize to size of candidateWindow
      set candidateArea to (item 1 of candidateSize) * (item 2 of candidateSize)
      if candidateArea > largestArea then
        set largestArea to candidateArea
        set hostWindow to contents of candidateWindow
      end if
    end try
  end repeat
  set hostPosition to position of hostWindow
  set hostSize to size of hostWindow
  return "OK|" & (item 1 of hostPosition as text) & "|" & (item 2 of hostPosition as text) & "|" & (item 1 of hostSize as text) & "|" & (item 2 of hostSize as text)
end tell
end run'''.replace("__HOST_BUNDLE__", host_literal)
    try:
        query_result = run_osascript(query, [terminal_tty or ""])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "message": f"Could not inspect application windows: {exc}"}
    query_output = (query_result.stdout or query_result.stderr).strip()
    if query_result.returncode != 0 or not query_output.startswith("OK|"):
        return automation_failure_result(query_output or "Could not inspect application windows", host_bundle_id)
    try:
        values = tuple(int(value) for value in query_output.split("|")[1:])
        if len(values) != 4:
            raise ValueError
        frames = macos_screen_frames()
    except (LearnError, ValueError) as exc:
        return {"status": "failed", "message": str(exc) or "Invalid host window geometry"}
    frame = choose_screen(frames, values)
    codex_bounds, obsidian_bounds = split_window_bounds(frame, codex_side)
    arrange = r'''on run argv
set codexX to (item 1 of argv) as integer
set codexY to (item 2 of argv) as integer
set codexWidth to (item 3 of argv) as integer
set codexHeight to (item 4 of argv) as integer
set obsidianX to (item 5 of argv) as integer
set obsidianY to (item 6 of argv) as integer
set obsidianWidth to (item 7 of argv) as integer
set obsidianHeight to (item 8 of argv) as integer
tell application id "md.obsidian" to activate
delay 0.4
tell application "System Events"
  if UI elements enabled is false then return "LEARN_ACCESSIBILITY_REQUIRED"
  set obsidianProcess to missing value
  repeat 30 times
    try
      set obsidianProcess to first application process whose bundle identifier is "md.obsidian"
    end try
    if obsidianProcess is not missing value then
      if (count of windows of obsidianProcess) > 0 then exit repeat
    end if
    delay 0.1
  end repeat
  if obsidianProcess is missing value then return "LEARN_OBSIDIAN_NOT_FOUND"
  if (count of windows of obsidianProcess) is 0 then return "LEARN_OBSIDIAN_NOT_FOUND"
  set obsidianWindow to window 1 of obsidianProcess
  set largestArea to 0
  repeat with candidateWindow in windows of obsidianProcess
    try
      set candidateSize to size of candidateWindow
      set candidateArea to (item 1 of candidateSize) * (item 2 of candidateSize)
      if candidateArea > largestArea then
        set largestArea to candidateArea
        set obsidianWindow to contents of candidateWindow
      end if
    end try
  end repeat
  try
    if value of attribute "AXFullScreen" of obsidianWindow is true then
      set value of attribute "AXFullScreen" of obsidianWindow to false
      delay 0.4
    end if
  end try
  try
    set value of attribute "AXMinimized" of obsidianWindow to false
  end try
  set size of obsidianWindow to {obsidianWidth, obsidianHeight}
  set position of obsidianWindow to {obsidianX, obsidianY}
end tell
if __HOST_BUNDLE__ is "com.apple.Terminal" then
  try
    tell application id "com.apple.Terminal"
      set targetTTY to item 9 of argv
      set hostWindow to front window
      if targetTTY is not "" then
        set foundWindow to false
        repeat with candidateWindow in windows
          repeat with candidateTab in tabs of candidateWindow
            if tty of candidateTab is targetTTY then
              set hostWindow to contents of candidateWindow
              set foundWindow to true
              exit repeat
            end if
          end repeat
          if foundWindow then exit repeat
        end repeat
      end if
      set bounds of hostWindow to {codexX, codexY, codexX + codexWidth, codexY + codexHeight}
      activate
    end tell
  on error
    return "LEARN_HOST_NOT_FOUND"
  end try
else
  tell application id __HOST_BUNDLE__ to activate
  delay 0.4
  tell application "System Events"
    try
      set hostProcess to first application process whose bundle identifier is __HOST_BUNDLE__
    on error
      return "LEARN_HOST_NOT_FOUND"
    end try
    if (count of windows of hostProcess) is 0 then return "LEARN_HOST_NOT_FOUND"
    set hostWindow to window 1 of hostProcess
    set largestArea to 0
    repeat with candidateWindow in windows of hostProcess
      try
        set candidateSize to size of candidateWindow
        set candidateArea to (item 1 of candidateSize) * (item 2 of candidateSize)
        if candidateArea > largestArea then
          set largestArea to candidateArea
          set hostWindow to contents of candidateWindow
        end if
      end try
    end repeat
    try
      if value of attribute "AXFullScreen" of hostWindow is true then
        set value of attribute "AXFullScreen" of hostWindow to false
        delay 0.4
      end if
    end try
    try
      set value of attribute "AXMinimized" of hostWindow to false
    end try
    set size of hostWindow to {codexWidth, codexHeight}
    set position of hostWindow to {codexX, codexY}
    set frontmost of hostProcess to true
  end tell
end if
return "OK"
end run'''.replace("__HOST_BUNDLE__", host_literal)
    arguments = [str(value) for value in (*codex_bounds, *obsidian_bounds)] + [terminal_tty or ""]
    try:
        arrange_result = run_osascript(arrange, arguments)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "message": f"Could not arrange application windows: {exc}"}
    arrange_output = (arrange_result.stdout or arrange_result.stderr).strip()
    if arrange_result.returncode != 0 or arrange_output != "OK":
        return automation_failure_result(arrange_output or "Could not arrange application windows", host_bundle_id)
    response = {
        "status": "tiled",
        "host_bundle_id": host_bundle_id,
        "codex_side": codex_side,
        "screen": frame,
    }
    if host_bundle_id == "com.apple.Terminal" and terminal_tty:
        response["terminal_tty"] = terminal_tty
    return response


def apply_window_layout(config: dict, active: dict) -> dict:
    if config.get("window_layout") == "none":
        return {"status": "disabled"}
    if sys.platform != "darwin":
        return {"status": "unsupported", "message": "desktop-split is currently supported only on macOS"}
    host_bundle_id = active.get("codex_host_bundle_id") or detect_codex_host_bundle_id()
    if not host_bundle_id:
        return {
            "status": "host-not-found",
            "message": "Could not identify the Codex host application for this session",
        }
    terminal_tty = detect_ancestor_tty() if host_bundle_id == "com.apple.Terminal" else None
    return tile_macos_windows(host_bundle_id, config.get("codex_side", "right"), terminal_tty)


def command_configure(args) -> dict:
    vault = Path(args.vault).expanduser()
    if not vault.is_absolute() or not vault.is_dir():
        raise LearnError("--vault must be an existing absolute directory")
    intervals = [int(item.strip()) for item in args.review_intervals.split(",") if item.strip()]
    config = default_config(vault, args.learning_folder)
    config.update(
        {
            "open_notes_automatically": args.open_notes,
            "obsidian_vault_name": args.vault_name or None,
            "review_intervals": intervals,
            "window_layout": args.window_layout,
            "codex_side": args.codex_side,
        }
    )
    config = validate_config(config)
    ensure_layout(config)
    write_json(config_path(), config)
    return config


def command_start(args) -> dict:
    config = load_config()
    ensure_layout(config)
    pending = newest_pending(config, args.cwd, args.session_id)
    session_id = pending["session_id"]
    with state_lock(config):
        existing = read_active(config, session_id)
        if existing:
            note = Path(config["vault_path"]) / existing["note_relative"]
            current_active = existing
            response = {
                "status": "resumed",
                "session_id": session_id,
                "note": str(note),
                "topic": topic_record(config, existing["topic_slug"]),
            }
        else:
            related = find_related_topic(config, args.title)
            if related:
                topic = related
                slug = topic["slug"]
                starting = f"Existing state: {topic['state']}. Use saved abilities, gaps, and review history before probing."
            else:
                slug = slugify(args.title)
                candidate = slug
                suffix = 2
                while topic_json_path(config, candidate).exists():
                    candidate = f"{slug}-{suffix}"
                    suffix += 1
                slug = candidate
                topic = new_topic(args.title, slug)
                write_json(topic_json_path(config, slug), topic)
                render_topic_note(config, topic)
                starting = "No matching topic state was found; begin with a short high-information diagnostic."
            stamp = now_utc().strftime("%Y%m%d-%H%M%S")
            name = f"{stamp}--{slug}--{hashlib.sha256(session_id.encode()).hexdigest()[:6]}.md"
            note = learning_root(config) / "Sessions" / name
            atomic_write(note, render_session_template(args.title, args.goal, args.mode, slug, starting, session_id))
            append_transcript(note, "user", pending["prompt"], pending.get("created_at"))
            mark_event(config, session_id, pending["turn_id"], "user")
            relative = note.relative_to(Path(config["vault_path"])).as_posix()
            active = {
                "schema_version": 1,
                "session_id": session_id,
                "title": args.title,
                "goal": args.goal,
                "mode": args.mode,
                "time_budget": args.time_budget,
                "cwd": pending.get("cwd", ""),
                "initiating_turn_id": pending["turn_id"],
                "topic_slug": slug,
                "note_relative": relative,
                "started_at": iso_now(),
                "close_after_stop": False,
                "codex_host_bundle_id": detect_codex_host_bundle_id(),
                "mcq_position_state": {"issuances": {}, "history": []},
            }
            write_json(active_path(config, session_id), active)
            current_active = active
            response = {
                "status": "started",
                "session_id": session_id,
                "note": str(note),
                "topic_context": {
                    "slug": topic["slug"],
                    "state": topic["state"],
                    "mental_model": topic.get("mental_model"),
                    "demonstrated_abilities": topic.get("demonstrated_abilities", []),
                    "misconceptions": topic.get("misconceptions", []),
                    "unresolved_gaps": topic.get("unresolved_gaps", []),
                    "retrieval_prompts": topic.get("retrieval_prompts", []),
                    "next_review": topic.get("review", {}).get("next_review"),
                },
            }
    opened = "disabled"
    layout = {"status": "disabled"}
    view = {"status": "disabled"}
    if config["open_notes_automatically"]:
        if opening_needs_escalation(config):
            opened = "approval-required"
            if config.get("window_layout") == "desktop-split":
                layout = {"status": "approval-required", "reason": "codex-sandbox"}
            view = {"status": "approval-required", "reason": "codex-sandbox"}
        else:
            opened = open_note(config, note, latest_tutor_heading(note))
            if opened != "not-opened":
                host_bundle_id = current_active.get("codex_host_bundle_id") or detect_codex_host_bundle_id()
                terminal_tty = detect_ancestor_tty() if host_bundle_id == "com.apple.Terminal" else None
                view = force_obsidian_reading_view(host_bundle_id, terminal_tty)
                layout = apply_window_layout(config, current_active)
    response["opened"] = opened
    response["layout"] = layout
    response["view"] = view
    return response


def find_current_active(config: dict, session_id: str | None = None, cwd: str | None = None) -> dict:
    if session_id:
        active = read_active(config, session_id)
        if not active:
            raise LearnError(f"No active lesson for session {session_id}")
        return active
    try:
        pending = newest_pending(config, cwd)
        active = read_active(config, pending["session_id"])
        if active:
            return active
    except LearnError:
        pass
    active_records = [read_json(path) for path in (system_root(config) / "active").glob("*.json")]
    if len(active_records) == 1:
        return active_records[0]
    if not active_records:
        raise LearnError("No active learning session")
    raise LearnError("Multiple lessons are active; pass --session-id")


def command_context(args) -> dict:
    config = load_config()
    if args.next_mcq:
        with state_lock(config):
            active = find_current_active(config, args.session_id, args.cwd)
            if args.correct_count > args.choices:
                raise LearnError("--correct-count cannot exceed --choices")
            pending = newest_pending(config, session_id=active["session_id"])
            positions = issue_mcq_positions(active, args.choices, args.correct_count, pending["turn_id"])
            write_json(active_path(config, active["session_id"]), active)
        labels = [chr(ord("A") + position) for position in positions]
        return {
            "correct_option_labels": labels,
            "answer_choices": args.choices,
            "selection": "single-select" if len(labels) == 1 else "multi-select",
            "instruction": (
                "Put the correct answer content at exactly these labels; do not reroll. "
                "Append 'I don't know' after the substantive choices."
            ),
        }
    active = find_current_active(config, args.session_id, args.cwd)
    topic = topic_record(config, active["topic_slug"])
    return {
        "session": {
            "session_id": active["session_id"],
            "title": active["title"],
            "goal": active["goal"],
            "mode": active["mode"],
            "note": str(Path(config["vault_path"]) / active["note_relative"]),
        },
        "topic": topic,
    }


def issue_mcq_positions(active: dict, choices: int, correct_count: int, turn_id: str) -> list[int]:
    """Sample correct positions from OS entropy and make one issuance immutable per user turn."""
    state = active.get("mcq_position_state")
    if not isinstance(state, dict) or "issuances" not in state:
        state = {"issuances": {}, "history": []}
        active["mcq_position_state"] = state
    issued = state["issuances"].get(turn_id)
    if issued:
        if issued["choices"] != choices or issued["correct_count"] != correct_count:
            raise LearnError(
                "An MCQ layout was already issued for this user turn; use its original choices and correct count"
            )
        return list(issued["positions"])
    positions = sorted(secrets.SystemRandom().sample(range(choices), correct_count))
    record = {
        "turn_id": turn_id,
        "choices": choices,
        "correct_count": correct_count,
        "positions": positions,
        "issued_at": iso_now(),
    }
    state["issuances"][turn_id] = record
    state.setdefault("history", []).append(record)
    return positions


def command_open(args) -> dict:
    config = load_config()
    active = find_current_active(config, args.session_id, args.cwd)
    note = Path(config["vault_path"]) / active["note_relative"]
    if not note.is_file():
        raise LearnError(f"Active lesson note does not exist: {note}")
    opened = open_note(config, note, latest_tutor_heading(note))
    if opened == "not-opened":
        raise LearnError(f"Obsidian could not open the lesson note: {note}")
    host_bundle_id = active.get("codex_host_bundle_id") or detect_codex_host_bundle_id()
    terminal_tty = detect_ancestor_tty() if host_bundle_id == "com.apple.Terminal" else None
    view = force_obsidian_reading_view(host_bundle_id, terminal_tty)
    layout = apply_window_layout(config, active)
    return {
        "opened": opened,
        "layout": layout,
        "view": view,
        "note": str(note),
        "session_id": active["session_id"],
    }


def load_payload(args) -> dict:
    if getattr(args, "json_file", None):
        raw = Path(args.json_file).read_text(encoding="utf-8")
    elif getattr(args, "json", None) is not None:
        raw = args.json
    else:
        raise LearnError("Pass --json or --json-file")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LearnError(f"Invalid payload JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise LearnError("Payload must be a JSON object")
    return value


def normalize_url(value: str) -> str:
    if not value:
        return ""
    parts = urlsplit(value.strip())
    scheme = parts.scheme.casefold()
    host = (parts.hostname or "").casefold()
    port = f":{parts.port}" if parts.port else ""
    netloc = host + port
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def normalize_doi(value: str) -> str:
    text = value.strip().casefold()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text)
    return text.rstrip("/")


def normalize_local_path(value: str) -> str:
    return str(Path(value).expanduser().resolve()) if value else ""


def validate_source_payload(payload: dict) -> None:
    missing = sorted(REQUIRED_SOURCE_KEYS - set(payload))
    if missing:
        raise LearnError("Source payload is missing: " + ", ".join(missing))
    citekey = str(payload["citekey"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", citekey):
        raise LearnError("citekey must be 1–80 safe filename characters")
    for key in ("title", "author_or_organization", "year_or_date", "source_type", "precise_locator"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise LearnError(f"{key} must be a non-empty string")
    if not any(str(payload.get(key, "")).strip() for key in ("url", "doi", "local_path")):
        raise LearnError("Source requires a URL, DOI, or local path")
    status = payload["verification_status"]
    if status not in SOURCE_STATUSES:
        raise LearnError("Invalid verification_status")
    try:
        dt.date.fromisoformat(str(payload["retrieval_date"]))
    except ValueError as exc:
        raise LearnError("retrieval_date must be YYYY-MM-DD") from exc
    claims = payload["claims_supported"]
    if not isinstance(claims, list) or any(not isinstance(item, str) or not item.strip() for item in claims):
        raise LearnError("claims_supported must be a list of non-empty strings")
    if claims and status not in SUPPORTING_STATUSES:
        raise LearnError(f"{status} sources cannot support factual claims")


def source_identity(payload: dict) -> set[str]:
    identities = set()
    if payload.get("url"):
        normalized = normalize_url(str(payload["url"]))
        identities.add("url:" + normalized)
        parts = urlsplit(normalized)
        if (parts.hostname or "").casefold() in {"doi.org", "dx.doi.org"}:
            identities.add("doi:" + normalize_doi(parts.path.lstrip("/")))
    if payload.get("doi"):
        identities.add("doi:" + normalize_doi(str(payload["doi"])))
    if payload.get("local_path"):
        identities.add("path:" + normalize_local_path(str(payload["local_path"])))
    return identities


def render_source_note(config: dict, source: dict) -> Path:
    path = learning_root(config) / "Sources" / f"{source['citekey']}.md"
    claims = list_items(source.get("claims_supported", []))
    reference = source.get("url") or source.get("doi") or source.get("local_path")
    if source.get("doi") and not source.get("url"):
        reference = "https://doi.org/" + normalize_doi(source["doi"])
    text = f"""---
learn_type: source
citekey: {yaml_string(source['citekey'])}
title: {yaml_string(source['title'])}
author_or_organization: {yaml_string(source['author_or_organization'])}
year_or_date: {yaml_string(source['year_or_date'])}
source_type: {yaml_string(source['source_type'])}
verification_status: {yaml_string(source['verification_status'])}
retrieval_date: {yaml_string(source['retrieval_date'])}
precise_locator: {yaml_string(source['precise_locator'])}
url: {yaml_string(source.get('url') or '')}
doi: {yaml_string(source.get('doi') or '')}
local_path: {yaml_string(source.get('local_path') or '')}
---

# {source['title']}

- **Author or organization:** {source['author_or_organization']}
- **Date:** {source['year_or_date']}
- **Reference:** [{reference}]({reference})
- **Type:** {source['source_type']}
- **Verification:** {source['verification_status']}
- **Retrieved:** {source['retrieval_date']}
- **Precise locator:** {source['precise_locator']}

## Claims supported

{claims}
"""
    atomic_write(path, text)
    return path


def command_source(args) -> dict:
    config = load_config()
    ensure_layout(config)
    payload = load_payload(args)
    validate_source_payload(payload)
    selected_active = None
    if args.session_id or args.cwd:
        selected_active = find_current_active(config, args.session_id, args.cwd)
    payload = dict(payload)
    payload.setdefault("url", "")
    payload.setdefault("doi", "")
    payload.setdefault("local_path", "")
    payload["normalized_identities"] = sorted(source_identity(payload))
    payload["updated_at"] = iso_now()
    with state_lock(config):
        active = None
        if selected_active:
            active = read_active(config, selected_active["session_id"])
            if not active:
                raise LearnError("Learning session closed while source logging was running")
        source_status = "created"
        citekey = payload["citekey"]
        note = None
        for path in (system_root(config) / "sources").glob("*.json"):
            existing = read_json(path)
            if set(existing.get("normalized_identities", [])) & set(payload["normalized_identities"]):
                source_status = "deduplicated"
                citekey = existing["citekey"]
                note = learning_root(config) / "Sources" / f"{citekey}.md"
                break
        if note is None:
            target = source_json_path(config, citekey)
            if target.exists():
                existing = read_json(target)
                if set(existing.get("normalized_identities", [])) != set(payload["normalized_identities"]):
                    raise LearnError(f"citekey already belongs to another source: {citekey}")
            write_json(target, payload)
            note = render_source_note(config, payload)
        if active:
            citekeys = active.setdefault("source_citekeys", [])
            if citekey not in citekeys:
                citekeys.append(citekey)
            session_note = Path(config["vault_path"]) / active["note_relative"]
            session_text = session_note.read_text(encoding="utf-8")
            source_lines = "\n".join(f"- [[Sources/{key}]]" for key in citekeys)
            session_text = replace_managed(session_text, *MARKER_PAIRS["SOURCES"], source_lines)
            atomic_write(session_note, session_text)
            write_json(active_path(config, active["session_id"]), active)
    result = {"status": source_status, "citekey": citekey, "note": str(note)}
    if active:
        result["attached_session_id"] = active["session_id"]
        result["session_note"] = str(session_note)
    return result


def ability_evidence(payload: dict, kinds: set[str], delayed: bool | None = None) -> bool:
    for item in payload.get("demonstrated_abilities", []):
        if not isinstance(item, dict):
            continue
        if item.get("evidence_type") not in kinds or item.get("independent") is not True:
            continue
        if delayed is None or item.get("delayed") is delayed:
            return True
    return False


def validate_finish_payload(payload: dict, config: dict) -> None:
    missing = sorted(REQUIRED_FINISH_KEYS - set(payload))
    extra = sorted(set(payload) - REQUIRED_FINISH_KEYS)
    if missing:
        raise LearnError("Finish payload is missing: " + ", ".join(missing))
    if extra:
        raise LearnError("Finish payload has unsupported keys: " + ", ".join(extra))
    for key in ("final_mental_model",):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise LearnError(f"{key} must be a non-empty string")
    for key in (
        "dependencies",
        "demonstrated_abilities",
        "hints_required",
        "misconceptions",
        "unresolved_gaps",
        "retrieval_prompts",
        "source_citekeys",
    ):
        if not isinstance(payload[key], list):
            raise LearnError(f"{key} must be a list")
    for key in (
        "dependencies",
        "hints_required",
        "misconceptions",
        "unresolved_gaps",
        "retrieval_prompts",
        "source_citekeys",
    ):
        if any(not isinstance(item, str) or not item.strip() for item in payload[key]):
            raise LearnError(f"{key} must contain only non-empty strings")
    for item in payload["demonstrated_abilities"]:
        if isinstance(item, dict):
            if not isinstance(item.get("ability"), str) or not item["ability"].strip():
                raise LearnError("Every structured demonstrated ability needs an ability string")
            if item.get("evidence_type") not in EVIDENCE_TYPES:
                raise LearnError("Invalid demonstrated ability evidence_type")
            if not isinstance(item.get("independent"), bool) or not isinstance(item.get("delayed"), bool):
                raise LearnError("Structured abilities require boolean independent and delayed")
        elif not isinstance(item, str):
            raise LearnError("demonstrated_abilities entries must be strings or evidence objects")
    state = payload["evidence_state"]
    if state not in TOPIC_STATES:
        raise LearnError("Invalid evidence_state")
    if state == "unseen":
        raise LearnError("A completed lesson with an explanation must be at least introduced")
    transfer = payload["transfer_task_result"]
    transfer_keys = {"attempted", "successful", "independent", "delayed", "summary"}
    if not isinstance(transfer, dict) or not transfer_keys.issubset(transfer):
        raise LearnError("transfer_task_result requires attempted, successful, independent, delayed, and summary")
    if any(not isinstance(transfer[key], bool) for key in ("attempted", "successful", "independent", "delayed")):
        raise LearnError("transfer_task_result flags must be booleans")
    if not isinstance(transfer["summary"], str):
        raise LearnError("transfer_task_result summary must be a string")
    if transfer["successful"] and not transfer["attempted"]:
        raise LearnError("A successful transfer task must be marked attempted")
    if transfer["independent"] and not transfer["successful"]:
        raise LearnError("An independent transfer result must be successful")
    if transfer["delayed"] and not transfer["attempted"]:
        raise LearnError("A delayed transfer task must be marked attempted")
    score = payload["suggested_review_score"]
    if not isinstance(score, int) or isinstance(score, bool) or score not in range(4):
        raise LearnError("suggested_review_score must be 0, 1, 2, or 3")

    rank = TOPIC_STATES.index(state)
    recall = ability_evidence(payload, {"recall", "explanation"})
    delayed_recall = ability_evidence(payload, {"recall", "explanation"}, delayed=True)
    applied = transfer.get("successful") is True and transfer.get("independent") is True
    delayed_transfer = applied and transfer.get("delayed") is True
    if rank >= TOPIC_STATES.index("retrievable") and not recall:
        raise LearnError("retrievable or higher requires independent recall or explanation evidence")
    if rank >= TOPIC_STATES.index("applicable") and not applied:
        raise LearnError("applicable or higher requires a successful independent application or transfer")
    if state == "robust" and not (delayed_recall and delayed_transfer):
        raise LearnError("robust requires delayed independent retrieval and transfer")

    for citekey in payload["source_citekeys"]:
        if not isinstance(citekey, str):
            raise LearnError("source_citekeys must contain strings")
        source = read_json(source_json_path(config, citekey), None)
        if not source:
            raise LearnError(f"Unknown source citekey: {citekey}")
        if source.get("verification_status") not in SUPPORTING_STATUSES:
            raise LearnError(f"Source {citekey} is not verified or user-provided")


def synthesis_markdown(payload: dict) -> str:
    transfer = payload["transfer_task_result"]
    return f"""### Final mental model

{payload['final_mental_model']}

### Dependency path

{list_items(payload['dependencies'])}

### Demonstrated abilities

{list_items(payload['demonstrated_abilities'])}

### Hints required

{list_items(payload['hints_required'])}

### Misconceptions repaired

{list_items(payload['misconceptions'])}

### Unresolved gaps

{list_items(payload['unresolved_gaps'])}

### Retrieval prompts

{list_items(payload['retrieval_prompts'])}

### Transfer task

- Attempted: {str(transfer['attempted']).lower()}
- Successful: {str(transfer['successful']).lower()}
- Independent: {str(transfer['independent']).lower()}
- Delayed: {str(transfer['delayed']).lower()}
- Summary: {transfer['summary'] or 'None'}

### Evidence state

`{payload['evidence_state']}`
"""


def update_session_status(text: str, status: str) -> str:
    if not text.startswith("---\n"):
        raise LearnError("Session note frontmatter is missing")
    end = text.find("\n---", 4)
    if end < 0:
        raise LearnError("Session note frontmatter is unbalanced")
    head = text[:end]
    if re.search(r"(?m)^status:\s*.*$", head):
        head = re.sub(r"(?m)^status:\s*.*$", f"status: {status}", head)
    else:
        head += f"\nstatus: {status}"
    return head + text[end:]


def unescaped_dollar_positions(text: str) -> list[int]:
    positions = []
    for index, character in enumerate(text):
        if character != "$":
            continue
        backslashes = 0
        cursor = index - 1
        while cursor >= 0 and text[cursor] == "\\":
            backslashes += 1
            cursor -= 1
        if backslashes % 2 == 0:
            positions.append(index)
    return positions


def strip_inline_code(text: str) -> str:
    return re.sub(r"(`+)[^`]*?\1", "", text)


def validate_math_fragment(fragment: str, label: str, line_number: int, kind: str, errors: list[str]) -> None:
    location = f"{label}:{line_number}"
    if not fragment.strip():
        errors.append(f"{location}: empty {kind} math expression")
        return
    if fragment != fragment.strip():
        errors.append(f"{location}: whitespace immediately inside {kind} math delimiters")
    if kind == "inline" and r"\displaystyle" in fragment:
        errors.append(f"{location}: \\displaystyle is not supported in inline math; use a display block")
    opening = len(re.findall(r"(?<!\\)\{", fragment))
    closing = len(re.findall(r"(?<!\\)\}", fragment))
    if opening != closing:
        errors.append(f"{location}: unbalanced LaTeX braces in {kind} math")
    if re.search(r"\\[A-Za-z]+\d", fragment):
        errors.append(f"{location}: LaTeX command runs into a digit; use an explicit subscript or separator")
    if re.search(r"(?<![_A-Za-z])[A-Za-z]\{\\text\{", fragment):
        errors.append(f"{location}: text component appears without an explicit subscript or operator")


def validate_math_lines(lines: list[str], label: str, errors: list[str]) -> None:
    in_display = False
    display_fragments = []
    for line_number, raw_line in enumerate(lines, 1):
        line = strip_inline_code(raw_line)
        line = re.sub(r"(?<![\w])\$learn\b", "learn", line)
        unquoted = re.sub(r"^\s*(?:>\s*)*", "", line)
        if "$$" in line:
            if unquoted.strip() != "$$":
                errors.append(f"{label}:{line_number}: display-math delimiter must be alone on its line")
                continue
            if in_display:
                validate_math_fragment("\n".join(display_fragments), label, line_number, "display", errors)
                display_fragments = []
            in_display = not in_display
            continue
        if in_display:
            display_fragments.append(unquoted)
            continue
        if any(delimiter in line for delimiter in (r"\(", r"\)", r"\[", r"\]")):
            errors.append(f"{label}:{line_number}: unsupported LaTeX delimiter; use $ or $$")

        dollars = unescaped_dollar_positions(line)
        if len(dollars) % 2:
            errors.append(f"{label}:{line_number}: unbalanced inline-math dollar delimiters")
            continue
        outside = []
        cursor = 0
        for index in range(0, len(dollars), 2):
            start, end = dollars[index], dollars[index + 1]
            outside.append(line[cursor:start])
            validate_math_fragment(line[start + 1 : end], label, line_number, "inline", errors)
            cursor = end + 1
        outside.append(line[cursor:])
        prose = "".join(outside).replace(r"\$", "")
        if re.search(r"\\[A-Za-z]+", prose):
            errors.append(f"{label}:{line_number}: LaTeX command appears outside math delimiters")
        if re.search(r"(?<![\w])(?:[A-Za-z]|[A-Za-z]\d?)_(?:\{|[A-Za-z0-9])", prose):
            errors.append(f"{label}:{line_number}: subscript appears outside math delimiters")
        if re.search(r"(?<![\w])(?:[A-Za-z]|[A-Za-z]\d?)\^(?:\{|[A-Za-z0-9])", prose):
            errors.append(f"{label}:{line_number}: superscript appears outside math delimiters")
    if in_display:
        errors.append(f"{label}: unbalanced display-math delimiters")


def without_learner_transcript(text: str) -> str:
    lines = []
    in_transcript = False
    learner_block = False
    for line in text.splitlines():
        if line.strip() == MARKER_PAIRS["TRANSCRIPT"][0]:
            in_transcript = True
            learner_block = False
        elif line.strip() == MARKER_PAIRS["TRANSCRIPT"][1]:
            in_transcript = False
            learner_block = False
        elif in_transcript and re.match(r"^### 🧑 Learner · ", line):
            learner_block = True
        elif in_transcript and re.match(r"^### 🤖 Tutor · ", line):
            learner_block = False
        lines.append("" if in_transcript and learner_block else line)
    return "\n".join(lines)


def command_finish(args) -> dict:
    config = load_config()
    payload = load_payload(args)
    active = find_current_active(config, args.session_id, args.cwd)
    with state_lock(config):
        active = read_active(config, active["session_id"])
        if not active:
            raise LearnError("Learning session closed while finish was running")
        payload = dict(payload)
        validate_finish_payload(payload, config)
        merged_citekeys = list(
            dict.fromkeys(list(active.get("source_citekeys", [])) + payload["source_citekeys"])
        )
        if merged_citekeys != payload["source_citekeys"]:
            payload["source_citekeys"] = merged_citekeys
            validate_finish_payload(payload, config)
        note = Path(config["vault_path"]) / active["note_relative"]
        text = note.read_text(encoding="utf-8")
        text = replace_managed(text, *MARKER_PAIRS["SYNTHESIS"], synthesis_markdown(payload))
        source_lines = [f"- [[Sources/{key}]]" for key in payload["source_citekeys"]]
        text = replace_managed(text, *MARKER_PAIRS["SOURCES"], "\n".join(source_lines) or "No sources used.")
        text = update_session_status(text, "finishing")
        atomic_write(note, text)

        topic = topic_record(config, active["topic_slug"]) or new_topic(active["title"], active["topic_slug"])
        topic.update(
            {
                "title": active["title"],
                "state": payload["evidence_state"],
                "mental_model": payload["final_mental_model"],
                "dependencies": payload["dependencies"],
                "demonstrated_abilities": payload["demonstrated_abilities"],
                "hints_required": payload["hints_required"],
                "misconceptions": payload["misconceptions"],
                "unresolved_gaps": payload["unresolved_gaps"],
                "retrieval_prompts": payload["retrieval_prompts"],
                "source_citekeys": payload["source_citekeys"],
                "updated_at": iso_now(),
            }
        )
        session_link = str(Path(active["note_relative"]).relative_to(config["learning_folder"]))
        if session_link not in topic["sessions"]:
            topic["sessions"].append(session_link)
        review = topic.setdefault("review", {"interval_index": 0, "history": []})
        review["interval_index"] = 0
        review["last_review"] = today().isoformat()
        review["next_review"] = (today() + dt.timedelta(days=config["review_intervals"][0])).isoformat()
        review.setdefault("history", []).append(
            {"date": today().isoformat(), "score": payload["suggested_review_score"], "kind": "lesson-finish"}
        )
        write_json(topic_json_path(config, topic["slug"]), topic)
        topic_note = render_topic_note(config, topic)

        errors = validate_artifacts(config, session=note, topic_slug=topic["slug"])
        if errors:
            raise LearnError("Validation failed:\n- " + "\n- ".join(errors))
        active["close_after_stop"] = True
        active["finished_at"] = iso_now()
        write_json(active_path(config, active["session_id"]), active)
    return {
        "status": "ready-to-close",
        "topic": topic["slug"],
        "evidence_state": topic["state"],
        "next_review": topic["review"]["next_review"],
        "session_note": str(note),
        "topic_note": str(topic_note),
        "instruction": "Send one concise final message; Stop will log it and close the active state.",
    }


def validate_markdown(path: Path, config: dict, errors: list[str]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"{path}: unreadable ({exc})")
        return
    label = str(path)
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        errors.append(f"{label}: missing opening frontmatter delimiter")
    elif not any(line.strip() == "---" for line in lines[1:]):
        errors.append(f"{label}: unbalanced frontmatter delimiters")
    open_fence = None
    for line in lines:
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)[0]
            if open_fence is None:
                open_fence = marker
            elif marker == open_fence:
                open_fence = None
            continue
    if open_fence is not None:
        errors.append(f"{label}: unbalanced fenced code blocks")
    math_text = []
    open_math_fence = None
    renderable_text = without_learner_transcript(text)
    for line in renderable_text.splitlines():
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)[0]
            if open_math_fence is None:
                open_math_fence = marker
            elif marker == open_math_fence:
                open_math_fence = None
            continue
        if open_math_fence is None:
            math_text.append(line)
    if math_text and math_text[0].strip() == "---":
        for index, line in enumerate(math_text[1:], 1):
            if line.strip() == "---":
                math_text = math_text[index + 1 :]
                break
    validate_math_lines(math_text, label, errors)
    for index, block in enumerate(re.findall(r"```mermaid\s*\n(.*?)```", renderable_text, re.DOTALL), 1):
        header = next((line.strip() for line in block.splitlines() if line.strip()), "")
        direction = re.match(r"(?:flowchart|graph)\s+([A-Za-z]{2})\b", header)
        if direction and direction.group(1).upper() in {"LR", "RL"}:
            errors.append(f"{label}: Mermaid block {index} uses horizontal flow; use flowchart TD")
    for found in re.findall(r"<!--\s*LEARN:([^>]+)\s*-->", text):
        token = found.strip()
        supported = {
            "BEGIN",
            "END",
            "TRANSCRIPT:BEGIN",
            "TRANSCRIPT:END",
            "SYNTHESIS:BEGIN",
            "SYNTHESIS:END",
            "SOURCES:BEGIN",
            "SOURCES:END",
        }
        if token not in supported:
            errors.append(f"{label}: unsupported generated-state marker LEARN:{token}")
    for begin, end in MARKER_PAIRS.values():
        if begin in text or end in text:
            if text.count(begin) != 1 or text.count(end) != 1 or text.find(begin) > text.find(end):
                errors.append(f"{label}: invalid generated-state markers {begin} / {end}")
    for raw in re.findall(r"!\[\[([^\]]+\.(?:png|svg)(?:\|[^\]]+)?)\]\]", text, re.IGNORECASE):
        target = raw.split("|", 1)[0]
        candidates = [learning_root(config) / target, Path(config["vault_path"]) / target]
        if not any(candidate.exists() for candidate in candidates):
            errors.append(f"{label}: embedded local asset does not exist: {target}")
    for citekey in re.findall(r"\[\[Sources/([^\]|#]+)", text):
        if not (learning_root(config) / "Sources" / f"{citekey}.md").exists():
            errors.append(f"{label}: linked source note does not exist: {citekey}")
    if re.search(r"TODO\s*:?[\s_-]*(?:citation|cite)|citation needed|\[cite\]", text, re.IGNORECASE):
        errors.append(f"{label}: unsupported citation placeholder")


def validate_artifacts(config: dict, session: Path | None = None, topic_slug: str | None = None) -> list[str]:
    errors: list[str] = []
    root = learning_root(config)
    markdown_paths: set[Path] = set()
    if session:
        markdown_paths.add(session)
    else:
        for folder in ("Sessions", "Topics", "Sources"):
            markdown_paths.update((root / folder).glob("*.md"))
    if topic_slug:
        markdown_paths.add(root / "Topics" / f"{topic_slug}.md")
    for path in sorted(markdown_paths):
        validate_markdown(path, config, errors)
        if path.parent.name == "Sessions" and path.exists():
            text = path.read_text(encoding="utf-8")
            match = re.search(r"(?m)^topic:\s*[\"']?\[\[Topics/([^\]|#]+)", text)
            if not match:
                errors.append(f"{path}: session topic link is missing")
            else:
                slug = match.group(1)
                if not (root / "Topics" / f"{slug}.md").exists() or not topic_json_path(config, slug).exists():
                    errors.append(f"{path}: linked topic does not exist: {slug}")

    citekeys = set()
    for path in (system_root(config) / "sources").glob("*.json"):
        source = read_json(path, None)
        if not source:
            continue
        missing = REQUIRED_SOURCE_KEYS - set(source)
        if missing:
            errors.append(f"{path}: missing source metadata {', '.join(sorted(missing))}")
        key = source.get("citekey")
        if key in citekeys:
            errors.append(f"{path}: duplicate source citekey {key}")
        citekeys.add(key)
        if key != path.stem:
            errors.append(f"{path}: citekey does not match its state filename")
        if source.get("claims_supported") and source.get("verification_status") not in SUPPORTING_STATUSES:
            errors.append(f"{path}: unverified source supports factual claims")

    for path in (system_root(config) / "active").glob("*.json"):
        active = read_json(path, None)
        if not active:
            continue
        note = Path(config["vault_path"]) / active.get("note_relative", "")
        if not note.is_file():
            errors.append(f"{path}: active session note is missing")
        if path != active_path(config, str(active.get("session_id", ""))):
            errors.append(f"{path}: active state key does not match session_id")

    for path in (system_root(config) / "topics").glob("*.json"):
        topic = read_json(path, None)
        if not topic:
            continue
        if topic.get("state") not in TOPIC_STATES:
            errors.append(f"{path}: invalid topic state {topic.get('state')}")
        if topic.get("slug") != path.stem:
            errors.append(f"{path}: topic slug does not match its state filename")
        note = root / "Topics" / f"{topic.get('slug')}.md"
        if not note.exists():
            errors.append(f"{path}: rendered topic note is missing")
        for link in topic.get("sessions", []):
            if not (root / link).exists():
                errors.append(f"{path}: linked session does not exist: {link}")
    return errors


def command_validate(args) -> dict:
    config = load_config()
    session = Path(args.session).expanduser().resolve() if args.session else None
    errors = validate_artifacts(config, session=session, topic_slug=args.topic)
    if args.render_mermaid and shutil.which("mmdc"):
        targets = [session] if session else list((learning_root(config) / "Sessions").glob("*.md"))
        for target in filter(None, targets):
            text = target.read_text(encoding="utf-8")
            for index, block in enumerate(re.findall(r"```mermaid\s*\n(.*?)```", text, re.DOTALL), 1):
                with tempfile.TemporaryDirectory(prefix="learn-mermaid-") as temp_dir:
                    src = Path(temp_dir) / "diagram.mmd"
                    out = Path(temp_dir) / "diagram.svg"
                    atomic_write(src, block)
                    result = subprocess.run(
                        ["mmdc", "-i", str(src), "-o", str(out)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    if result.returncode != 0:
                        errors.append(f"{target}: Mermaid block {index} did not render")
    if errors:
        raise LearnError("Validation failed:\n- " + "\n- ".join(errors))
    return {"status": "ok", "checked": "configured Learning artifacts"}


def command_due(args):
    config = load_config()
    cutoff = dt.date.fromisoformat(args.on_date) if args.on_date else today()
    due = []
    for path in (system_root(config) / "topics").glob("*.json"):
        topic = read_json(path, None)
        next_review = (topic or {}).get("review", {}).get("next_review")
        if next_review and dt.date.fromisoformat(next_review) <= cutoff:
            due.append(
                {
                    "slug": topic["slug"],
                    "title": topic["title"],
                    "state": topic["state"],
                    "next_review": next_review,
                    "retrieval_prompts": topic.get("retrieval_prompts", []),
                }
            )
    due.sort(key=lambda item: (item["next_review"], item["title"].casefold()))
    return due


def command_review(args) -> dict:
    config = load_config()
    topic = topic_record(config, args.topic)
    if not topic:
        raise LearnError(f"Unknown topic: {args.topic}")
    review_date = dt.date.fromisoformat(args.on_date) if args.on_date else today()
    intervals = config["review_intervals"]
    review = topic.setdefault("review", {"interval_index": 0, "history": []})
    current = int(review.get("interval_index", 0))
    prior_review = review.get("last_review")
    delayed = not prior_review or review_date > dt.date.fromisoformat(prior_review)
    if args.score == 0:
        next_index = 0
        if topic["state"] != "unseen":
            topic["state"] = "introduced"
    elif args.score == 1:
        next_index = max(0, current - 1)
        if TOPIC_STATES.index(topic["state"]) > TOPIC_STATES.index("introduced"):
            topic["state"] = "introduced"
    else:
        next_index = min(len(intervals) - 1, current + 1)
        if args.score == 2 and TOPIC_STATES.index(topic["state"]) < TOPIC_STATES.index("retrievable"):
            topic["state"] = "retrievable"
        if args.score == 3:
            topic["state"] = "robust" if delayed else "applicable"
    review["interval_index"] = next_index
    review["last_review"] = review_date.isoformat()
    review["next_review"] = (review_date + dt.timedelta(days=intervals[next_index])).isoformat()
    review.setdefault("history", []).append(
        {"date": review_date.isoformat(), "score": args.score, "kind": "review", "notes": args.notes or ""}
    )
    topic["updated_at"] = iso_now()
    with state_lock(config):
        write_json(topic_json_path(config, topic["slug"]), topic)
        note = render_topic_note(config, topic)
    return {
        "topic": topic["slug"],
        "state": topic["state"],
        "score": args.score,
        "next_review": review["next_review"],
        "note": str(note),
    }


def command_status(args) -> dict:
    config = load_config()
    active = [read_json(path) for path in sorted((system_root(config) / "active").glob("*.json"))]
    topics = list((system_root(config) / "topics").glob("*.json"))
    sources = list((system_root(config) / "sources").glob("*.json"))
    return {
        "vault": config["vault_path"],
        "learning_folder": config["learning_folder"],
        "active_sessions": [
            {"session_id": item["session_id"], "title": item["title"], "mode": item["mode"], "note": item["note_relative"]}
            for item in active
        ],
        "topic_count": len(topics),
        "source_count": len(sources),
        "due_count": len(command_due(argparse.Namespace(on_date=None))),
    }


def record_time(record: dict, path: Path, key: str) -> dt.datetime:
    raw = record.get(key)
    if raw:
        try:
            parsed = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc)
        except ValueError:
            pass
    return dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)


def session_note_status(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    frontmatter = re.match(r"\A---\s*\n(.*?)\n---(?:\s*\n|\Z)", text, re.DOTALL)
    if not frontmatter:
        return None
    match = re.search(r"(?m)^status:\s*['\"]?([^'\"\s]+)", frontmatter.group(1))
    return match.group(1).casefold() if match else None


def command_cleanup(args) -> dict:
    """Remove disposable hook state while preserving live sessions and durable learning state."""
    if args.pending_days < 0:
        raise LearnError("--pending-days must be zero or greater")
    config = load_config()
    root = system_root(config)
    cutoff = now_utc() - dt.timedelta(days=args.pending_days)
    candidates = []
    warnings = []
    retained = {"active": 0, "pending": 0, "events": 0}

    def relative(path: Path) -> str:
        return path.relative_to(root).as_posix()

    def candidate(path: Path, kind: str, reason: str, session_id: str | None = None) -> None:
        item = {"kind": kind, "path": relative(path), "reason": reason}
        if session_id:
            item["session_id"] = session_id
        candidates.append((path, item))

    with state_lock(config):
        live_session_ids = set()
        stale_session_ids = set()
        for path in sorted((root / "active").glob("*.json")):
            try:
                record = read_json(path)
            except LearnError as exc:
                warnings.append(str(exc) + "; preserved for manual inspection")
                retained["active"] += 1
                continue
            session_id = str(record.get("session_id") or "").strip()
            if not session_id:
                warnings.append(f"{relative(path)} has no session_id; preserved for manual inspection")
                retained["active"] += 1
                continue
            note = Path(config["vault_path"]) / str(record.get("note_relative") or "")
            if not note.is_file():
                candidate(path, "active", "session note is missing", session_id)
                stale_session_ids.add(session_id)
                continue
            status = session_note_status(note)
            if status in {"completed", "aborted"}:
                candidate(path, "active", f"session note is already {status}", session_id)
                stale_session_ids.add(session_id)
                continue
            live_session_ids.add(session_id)
            retained["active"] += 1

        for path in sorted((root / "pending").glob("*.json")):
            try:
                record = read_json(path)
            except LearnError:
                candidate(path, "pending", "invalid pending JSON")
                continue
            session_id = str(record.get("session_id") or "").strip()
            if session_id in live_session_ids:
                retained["pending"] += 1
            elif session_id in stale_session_ids:
                candidate(path, "pending", "associated active state is stale", session_id)
            elif record_time(record, path, "created_at") <= cutoff:
                candidate(path, "pending", f"older than {args.pending_days} days", session_id or None)
            else:
                retained["pending"] += 1

        for path in sorted((root / "events").glob("*.json")):
            try:
                record = read_json(path)
            except LearnError:
                candidate(path, "event", "invalid event JSON")
                continue
            session_id = str(record.get("session_id") or "").strip()
            if session_id in live_session_ids:
                retained["events"] += 1
            else:
                candidate(path, "event", "session is no longer active", session_id or None)

        metadata = root / ".DS_Store"
        if metadata.is_file():
            candidate(metadata, "metadata", "Finder metadata is not Learn state")

        if args.apply:
            for path, _ in candidates:
                path.unlink(missing_ok=True)

    items = [item for _, item in candidates]
    counts = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {
        "status": "cleaned" if args.apply else "dry-run",
        "session_identifier": "Codex session_id",
        "protected_active_session_ids": sorted(live_session_ids),
        "pending_retention_days": args.pending_days,
        "counts": counts,
        "removed" if args.apply else "would_remove": items,
        "retained": retained,
        "warnings": warnings,
        "durable_state_preserved": ["topics", "sources", "session notes", "topic notes", "source notes", "assets"],
    }


def command_abort(args) -> dict:
    config = load_config()
    active = find_current_active(config, args.session_id, args.cwd)
    with state_lock(config):
        note = Path(config["vault_path"]) / active["note_relative"]
        if note.exists():
            text = update_session_status(note.read_text(encoding="utf-8"), "aborted")
            atomic_write(note, text)
        path = active_path(config, active["session_id"])
        if path.exists():
            path.unlink()
    return {"status": "aborted", "session_id": active["session_id"], "note": str(note)}


def command_doctor(args) -> dict:
    checks = []
    config = load_config(required=False)
    checks.append({"name": "configuration", "ok": config is not None, "detail": str(config_path())})
    if config:
        vault = Path(config["vault_path"])
        root = learning_root(config)
        checks.append({"name": "vault", "ok": vault.is_dir(), "detail": str(vault)})
        checks.append({"name": "learning-folder", "ok": root.is_dir(), "detail": str(root)})
        checks.append({"name": "learning-folder-writable", "ok": os.access(root, os.W_OK), "detail": str(root)})
        errors = validate_artifacts(config)
        checks.append({"name": "artifact-validation", "ok": not errors, "detail": "; ".join(errors[:3]) or "ok"})
    skill = Path.home() / ".agents/skills/learn"
    checks.append({"name": "skill", "ok": (skill / "SKILL.md").is_file(), "detail": str(skill)})
    hooks = Path.home() / ".codex/hooks.json"
    hook_ok = False
    if hooks.exists():
        try:
            data = read_json(hooks)
            blob = json.dumps(data)
            hook_ok = "learnctl.py hook" in blob and "UserPromptSubmit" in blob and "Stop" in blob
        except LearnError:
            hook_ok = False
    checks.append({"name": "hooks", "ok": hook_ok, "detail": str(hooks)})
    result = {"ok": all(check["ok"] for check in checks), "checks": checks}
    if not result["ok"]:
        raise LearnError(json.dumps(result, ensure_ascii=False))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="learnctl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    configure = sub.add_parser("configure", help="write local configuration")
    configure.add_argument("--vault", required=True)
    configure.add_argument("--learning-folder", default="Learning")
    opening = configure.add_mutually_exclusive_group()
    opening.add_argument("--open-notes", dest="open_notes", action="store_true")
    opening.add_argument("--no-open-notes", dest="open_notes", action="store_false")
    configure.set_defaults(open_notes=True)
    configure.add_argument("--vault-name")
    configure.add_argument("--review-intervals", default=",".join(map(str, DEFAULT_INTERVALS)))
    configure.add_argument("--window-layout", choices=sorted(WINDOW_LAYOUTS), default="desktop-split")
    configure.add_argument("--codex-side", choices=sorted(WINDOW_SIDES), default="right")

    sub.add_parser("doctor", help="check installation and state")
    sub.add_parser("hook", help="process one Codex hook JSON object from stdin")

    start = sub.add_parser("start", help="start or recover a learning session")
    start.add_argument("--title", required=True)
    start.add_argument("--goal", required=True)
    start.add_argument("--mode", choices=["learn", "explain", "solve", "review", "research"], default="learn")
    start.add_argument("--time-budget")
    start.add_argument("--cwd")
    start.add_argument("--session-id")

    context = sub.add_parser("context", help="show compact active-session context")
    context.add_argument("--cwd")
    context.add_argument("--session-id")
    context.add_argument("--next-mcq", action="store_true", help="sample correct-answer positions from OS entropy")
    context.add_argument("--choices", type=int, choices=range(2, 7), default=4)
    context.add_argument("--correct-count", type=int, choices=range(1, 7), default=1)

    open_command = sub.add_parser("open", help="open the active lesson note in Obsidian")
    open_command.add_argument("--cwd")
    open_command.add_argument("--session-id")

    for name in ("source", "finish"):
        item = sub.add_parser(name, help=f"process a structured {name} payload")
        payload = item.add_mutually_exclusive_group(required=True)
        payload.add_argument("--json")
        payload.add_argument("--json-file")
        item.add_argument("--cwd")
        item.add_argument("--session-id")

    validate = sub.add_parser("validate", help="validate Learning artifacts")
    validate.add_argument("--session")
    validate.add_argument("--topic")
    validate.add_argument("--render-mermaid", action="store_true")

    due = sub.add_parser("due", help="list topics due for review")
    due.add_argument("--on-date")
    due.add_argument("--json-output", action="store_true")

    review = sub.add_parser("review", help="record a delayed review score")
    review.add_argument("--topic", required=True)
    review.add_argument("--score", required=True, type=int, choices=range(4))
    review.add_argument("--notes")
    review.add_argument("--on-date")

    status = sub.add_parser("status", help="show active and durable state")
    status.add_argument("--json-output", action="store_true")

    cleanup = sub.add_parser("cleanup", help="prune disposable hook state without deleting learning notes")
    cleanup.add_argument("--apply", action="store_true", help="remove candidates; the default is a dry run")
    cleanup.add_argument("--pending-days", type=int, default=DEFAULT_PENDING_RETENTION_DAYS)
    cleanup.add_argument("--json-output", action="store_true", help="show the complete candidate list as JSON")

    abort = sub.add_parser("abort", help="abandon one active lesson without deleting notes")
    abort.add_argument("--cwd")
    abort.add_argument("--session-id")
    return parser


def print_human(command: str, result) -> None:
    if command == "due":
        if not result:
            print("No reviews due.")
            return
        for item in result:
            print(f"{item['next_review']}  {item['slug']}  [{item['state']}]  {item['title']}")
        return
    if command == "cleanup":
        verb = "Removed" if result["status"] == "cleaned" else "Would remove"
        total = sum(result["counts"].values())
        details = ", ".join(f"{count} {kind}" for kind, count in sorted(result["counts"].items())) or "nothing"
        print(f"{verb} {total} disposable records ({details}).")
        protected = result["protected_active_session_ids"]
        print(f"Protected {len(protected)} active learning session(s).")
        for warning in result["warnings"]:
            print(f"Warning: {warning}")
        if result["status"] == "dry-run":
            print("Run again with --json-output to inspect every path, then add --apply to remove them.")
        return
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "hook":
        try:
            raw = sys.stdin.read()
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict):
                run_hook(data)
        except Exception:
            pass
        return 0
    commands = {
        "configure": command_configure,
        "doctor": command_doctor,
        "start": command_start,
        "context": command_context,
        "open": command_open,
        "source": command_source,
        "validate": command_validate,
        "finish": command_finish,
        "due": command_due,
        "review": command_review,
        "status": command_status,
        "cleanup": command_cleanup,
        "abort": command_abort,
    }
    try:
        result = commands[args.command](args)
        if getattr(args, "json_output", False):
            print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
        else:
            print_human(args.command, result)
        return 0
    except (LearnError, OSError, ValueError) as exc:
        print(f"learnctl: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
