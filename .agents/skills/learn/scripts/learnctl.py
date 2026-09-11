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
import uuid
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
LESSON_SCHEMA_VERSION = 1
REVIEW_SCHEMA_VERSION = 1
DEFAULT_CONTEXT_MESSAGES = 8
MAX_CONTEXT_MESSAGES = 50
MAX_CHECKPOINT_BYTES = 16 * 1024
LESSON_STATES = {"active", "paused", "finishing", "completed", "aborted"}
OPTIONAL_FINISH_KEYS = {"reassessment", "contrary_evidence_refs"}
SEMANTIC_REVIEW_KEYS = {
    "evidence_state",
    "demonstrated_abilities",
    "transfer_task_result",
    "misconceptions",
    "unresolved_gaps",
    "reassessment",
    "contrary_evidence_refs",
}
TOPIC_EVIDENCE_FIELDS = (
    "mental_model",
    "dependencies",
    "demonstrated_abilities",
    "hints_required",
    "misconceptions",
    "unresolved_gaps",
    "retrieval_prompts",
    "source_citekeys",
)
CHECKPOINT_KEYS = {
    "phase",
    "goal_and_route",
    "relevant_concepts",
    "demonstrated_understanding",
    "misconceptions",
    "unknowns",
    "evidence_refs",
    "hints_and_independence",
    "outstanding_question",
    "next_teaching_step",
    "covered_through",
}
MARKER_PAIRS = {
    "TRANSCRIPT": ("<!-- LEARN:TRANSCRIPT:BEGIN -->", "<!-- LEARN:TRANSCRIPT:END -->"),
    "SYNTHESIS": ("<!-- LEARN:SYNTHESIS:BEGIN -->", "<!-- LEARN:SYNTHESIS:END -->"),
    "SOURCES": ("<!-- LEARN:SOURCES:BEGIN -->", "<!-- LEARN:SOURCES:END -->"),
    "TOPIC": ("<!-- LEARN:BEGIN -->", "<!-- LEARN:END -->"),
    "SOURCE_RECORD": ("<!-- LEARN:SOURCE:BEGIN -->", "<!-- LEARN:SOURCE:END -->"),
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


class EventConflictError(LearnError):
    pass


class SavedRenderingError(LearnError):
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
        "_system/lessons",
        "_system/reviews",
        "_system/topics",
        "_system/sources",
    ):
        target = root / relative
        resolved = target.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise LearnError(f"Learning layout path resolves outside its root: {target}") from exc
        target.mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def state_lock(config: dict):
    if fcntl is None:
        raise LearnError("Process locking is unavailable on this platform; refusing to mutate learning state")
    ensure_layout(config)
    lock_path = system_root(config) / ".learnctl.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
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


def topic_json_path(config: dict, slug: str) -> Path:
    return system_root(config) / "topics" / f"{slug}.json"


def source_json_path(config: dict, citekey: str) -> Path:
    return system_root(config) / "sources" / f"{citekey}.json"


def canonical_lesson_id(value: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, AttributeError) as exc:
        raise LearnError(f"Invalid lesson_id: {value!r}") from exc
    canonical = str(parsed)
    if str(value) != canonical:
        raise LearnError(f"lesson_id must use canonical UUID form: {value!r}")
    return canonical


def lesson_json_path(config: dict, lesson_id: str) -> Path:
    return system_root(config) / "lessons" / f"{canonical_lesson_id(lesson_id)}.json"


def review_json_path(config: dict, assessment_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", str(assessment_id)):
        raise LearnError("Review assessment_id must be a lowercase SHA-256 identifier")
    return system_root(config) / "reviews" / f"{assessment_id}.json"


def operational_path(config: dict) -> Path:
    return system_root(config) / "operational.json"


def lesson_note_path(config: dict, lesson: dict) -> Path:
    relative = Path(str(lesson.get("note_relative") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise LearnError("Lesson note_relative must be a safe vault-relative path")
    target = (Path(config["vault_path"]) / relative).resolve(strict=False)
    root = learning_root(config).resolve()
    sessions = (root / "Sessions").resolve()
    try:
        sessions.relative_to(root)
    except ValueError as exc:
        raise LearnError("Learning/Sessions resolves outside the Learning directory") from exc
    try:
        target.relative_to(sessions)
    except ValueError as exc:
        raise LearnError(f"Lesson note resolves outside the Learning/Sessions directory: {relative}") from exc
    return target


def validate_lesson_record(lesson: dict, expected_id: str | None = None) -> dict:
    if not isinstance(lesson, dict):
        raise LearnError("Lesson record must be a JSON object")
    if lesson.get("schema_version") != LESSON_SCHEMA_VERSION:
        raise LearnError(f"Unsupported lesson schema_version: {lesson.get('schema_version')!r}")
    lesson_id = canonical_lesson_id(str(lesson.get("lesson_id") or ""))
    if expected_id and lesson_id != expected_id:
        raise LearnError("Lesson identity does not match its filename")
    if lesson.get("status") not in LESSON_STATES:
        raise LearnError(f"Invalid lesson status: {lesson.get('status')!r}")
    binding = lesson.get("conversation_binding")
    if not isinstance(binding, dict) or not str(binding.get("session_id") or "").strip():
        raise LearnError("Lesson conversation binding requires an exact session_id")
    allowed_sessions = {str(binding["session_id"])}
    binding_history = lesson.get("binding_history", [])
    if not isinstance(binding_history, list):
        raise LearnError("Lesson binding_history must be a list")
    for index, prior in enumerate(binding_history):
        if not isinstance(prior, dict) or not str(prior.get("session_id") or "").strip():
            raise LearnError(f"Lesson binding history entry {index} is malformed")
        allowed_sessions.add(str(prior["session_id"]))
    status = lesson["status"]
    if status in {"paused", "completed", "aborted"} and not binding.get("detached_at"):
        raise LearnError(f"A {status} lesson must record when its conversation was detached")
    if status in {"finishing", "completed"}:
        assessment = lesson.get("final_assessment")
        if (
            not isinstance(assessment, dict)
            or not str(assessment.get("assessment_id") or "").strip()
            or not isinstance(assessment.get("payload"), dict)
        ):
            raise LearnError(f"A {status} lesson requires one stored final assessment")
        expected = lesson.get("expected_finishing")
        if (
            not isinstance(expected, dict)
            or expected.get("session_id") != binding["session_id"]
            or not str(expected.get("turn_id") or "").strip()
        ):
            raise LearnError(f"A {status} lesson requires an exact expected final conversation and turn")
    for key in ("topic_id", "title", "goal", "mode", "note_relative"):
        if not isinstance(lesson.get(key), str) or not lesson[key].strip():
            raise LearnError(f"Lesson {key} must be a non-empty string")
    messages = lesson.get("messages")
    if not isinstance(messages, list):
        raise LearnError("Lesson messages must be an ordered list")
    identities = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or not isinstance(message.get("markdown"), str):
            raise LearnError(f"Lesson message {index} is malformed")
        identity = message.get("event_identity")
        if not isinstance(identity, dict):
            raise LearnError(f"Lesson message {index} has no event identity")
        if identity.get("session_id") not in allowed_sessions:
            raise LearnError(f"Lesson message {index} belongs to an unrecorded conversation binding")
        if not identity.get("turn_id") or identity.get("role") not in {"user", "assistant"}:
            raise LearnError(f"Lesson message {index} has an invalid turn or role")
        key = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if key in identities:
            raise LearnError(f"Lesson contains duplicate event identity at message {index}")
        identities.add(key)
    if lesson.get("latest_checkpoint") is not None:
        validate_stored_checkpoint(lesson)
    return lesson


def load_lesson(config: dict, lesson_id: str) -> dict:
    canonical = canonical_lesson_id(lesson_id)
    lesson = read_json(lesson_json_path(config, canonical), None)
    if lesson is None:
        raise LearnError(f"Lesson record does not exist: {canonical}")
    validate_lesson_record(lesson, canonical)
    lesson_note_path(config, lesson)
    return lesson


def save_lesson(config: dict, lesson: dict) -> None:
    validate_lesson_record(lesson)
    lesson_note_path(config, lesson)
    lesson["revision"] = int(lesson.get("revision", 0)) + 1
    lesson["updated_at"] = iso_now()
    write_json(lesson_json_path(config, lesson["lesson_id"]), lesson)


def binding_record(config: dict, session_id: str) -> dict | None:
    record = read_json(active_path(config, session_id), None)
    if not record:
        return None
    if str(record.get("session_id") or "") != session_id:
        raise LearnError("Conversation binding does not match its active-state filename")
    return record


def read_active(config: dict, session_id: str) -> dict | None:
    record = binding_record(config, session_id)
    if not record:
        return None
    lesson_id = record.get("lesson_id")
    if not lesson_id:
        raise LearnError("Active binding is missing its canonical lesson_id")
    lesson = load_lesson(config, str(lesson_id))
    if lesson["conversation_binding"]["session_id"] != session_id:
        raise LearnError("Conversation binding and canonical lesson disagree")
    return lesson


def bind_lesson(config: dict, lesson: dict) -> None:
    session_id = lesson["conversation_binding"]["session_id"]
    existing = binding_record(config, session_id)
    if existing and existing.get("lesson_id") != lesson["lesson_id"]:
        raise LearnError(f"Conversation {session_id} is already attached to another lesson")
    for path in (system_root(config) / "active").glob("*.json"):
        record = read_json(path, None)
        if record and record.get("lesson_id") == lesson["lesson_id"] and record.get("session_id") != session_id:
            raise LearnError(f"Lesson {lesson['lesson_id']} is already attached to another conversation")
    write_json(
        active_path(config, session_id),
        {
            "schema_version": 1,
            "lesson_id": lesson["lesson_id"],
            "session_id": session_id,
            "bound_at": lesson["conversation_binding"]["attached_at"],
        },
    )


def recover_exact_binding(config: dict, session_id: str) -> dict | None:
    matches = []
    for path in (system_root(config) / "lessons").glob("*.json"):
        lesson = load_lesson(config, path.stem)
        if (
            lesson["conversation_binding"]["session_id"] == session_id
            and lesson["status"] in {"active", "finishing"}
        ):
            matches.append(lesson)
    if len(matches) > 1:
        raise LearnError(f"Multiple unfinished lessons claim conversation {session_id}; repair manually")
    if not matches:
        return None
    bind_lesson(config, matches[0])
    return matches[0]


def active_session_id(active: dict) -> str:
    return str(active["conversation_binding"]["session_id"])


def active_topic_id(active: dict) -> str:
    return str(active.get("topic_id") or "")


def exact_runtime_session_id(explicit: str | None = None) -> str:
    thread = str(os.environ.get("CODEX_THREAD_ID") or "").strip()
    session = str(os.environ.get("CODEX_SESSION_ID") or "").strip()
    if thread and session and thread != session:
        raise LearnError("Codex runtime supplied conflicting conversation identifiers")
    runtime = thread or session
    if explicit:
        wanted = str(explicit).strip()
        if runtime and wanted != runtime:
            raise LearnError("--session-id does not match the current Codex conversation")
        return wanted
    if runtime:
        return runtime
    raise LearnError("Exact Codex conversation identity is unavailable; pass --session-id")


def replace_managed(text: str, begin: str, end: str, body: str) -> str:
    first = text.find(begin)
    last = text.find(end)
    if first < 0 or last < 0 or last < first:
        raise LearnError(f"Missing or invalid generated-state markers: {begin} / {end}")
    content_start = first + len(begin)
    return text[:content_start] + "\n" + body.rstrip() + "\n" + text[last:]


def update_frontmatter_fields(text: str, updates: dict[str, str], label: str) -> str:
    """Update simple Learn-owned scalar keys without replacing user-owned YAML."""
    opening = re.match(r"\A---[ \t]*(?:\r?\n)", text)
    if not opening:
        raise LearnError(f"{label} frontmatter is missing")
    closing = re.search(r"(?m)^---[ \t]*\r?$", text[opening.end() :])
    if not closing:
        raise LearnError(f"{label} frontmatter is unbalanced")
    start = opening.end()
    end = opening.end() + closing.start()
    frontmatter = text[start:end]
    for key, value in updates.items():
        pattern = re.compile(rf"(?m)^{re.escape(key)}[ \t]*:[^\r\n]*(?:\r?$)")
        matches = list(pattern.finditer(frontmatter))
        if len(matches) > 1:
            raise LearnError(f"{label} frontmatter has duplicate {key!r} properties")
        replacement = f"{key}: {value}"
        if matches:
            match = matches[0]
            frontmatter = frontmatter[: match.start()] + replacement + frontmatter[match.end() :]
        else:
            if frontmatter and not frontmatter.endswith(("\n", "\r")):
                frontmatter += "\n"
            frontmatter += replacement + "\n"
    return text[:start] + frontmatter + text[end:]


def default_operational_state() -> dict:
    return {
        "schema_version": 1,
        "last_successful_capture": None,
        "pending_rendering_repairs": [],
        "last_error": None,
    }


def read_operational_state(config: dict) -> dict:
    state = read_json(operational_path(config), None)
    if not isinstance(state, dict):
        return default_operational_state()
    result = default_operational_state()
    result.update(state)
    if not isinstance(result.get("pending_rendering_repairs"), list):
        result["pending_rendering_repairs"] = []
    return result


def record_capture_health(
    config: dict, event: str, session_id: str, turn_id: str, note_relative: str | None = None
) -> None:
    state = read_operational_state(config)
    state["last_successful_capture"] = {
        "at": iso_now(),
        "event": event,
        "session_id": session_id,
        "turn_id": turn_id,
    }
    if note_relative:
        state["pending_rendering_repairs"] = [
            item for item in state["pending_rendering_repairs"] if item != note_relative
        ]
    write_json(operational_path(config), state)


def record_operational_error(
    config: dict, data: dict, operation: str, exc: Exception, needs_rendering_repair: bool = True
) -> None:
    """Persist actionable metadata only; hook transcript text is deliberately excluded."""
    with state_lock(config):
        state = read_operational_state(config)
        session_id = hook_session_id(data)
        try:
            active = read_active(config, session_id) if session_id else None
        except LearnError:
            active = None
        note_relative = str(active.get("note_relative") or "") if active else ""
        if needs_rendering_repair and note_relative and note_relative not in state["pending_rendering_repairs"]:
            state["pending_rendering_repairs"].append(note_relative)
        state["last_error"] = {
            "at": iso_now(),
            "operation": operation,
            "error_type": type(exc).__name__,
            "event": hook_event_name(data),
            "session_id": session_id or None,
            "turn_id": hook_turn_id(data) or None,
        }
        write_json(operational_path(config), state)


def mark_rendering_repair_locked(
    config: dict,
    lesson: dict,
    operation: str,
    exc: Exception,
    turn_id: str | None = None,
) -> None:
    state = read_operational_state(config)
    relative = str(lesson.get("note_relative") or "")
    if relative and relative not in state["pending_rendering_repairs"]:
        state["pending_rendering_repairs"].append(relative)
    state["last_error"] = {
        "at": iso_now(),
        "operation": operation,
        "error_type": type(exc).__name__,
        "event": None,
        "session_id": active_session_id(lesson),
        "turn_id": turn_id,
    }
    write_json(operational_path(config), state)


def mark_topic_rendering_repair_locked(config: dict, topic: dict, operation: str, exc: Exception) -> None:
    state = read_operational_state(config)
    relative = (Path(config["learning_folder"]) / "Topics" / f"{topic['slug']}.md").as_posix()
    if relative not in state["pending_rendering_repairs"]:
        state["pending_rendering_repairs"].append(relative)
    state["last_error"] = {
        "at": iso_now(),
        "operation": operation,
        "error_type": type(exc).__name__,
        "event": None,
        "session_id": None,
        "turn_id": None,
    }
    write_json(operational_path(config), state)


def lesson_transcript_markers(lesson_id: str) -> tuple[str, str]:
    canonical = canonical_lesson_id(lesson_id)
    return (
        f"<!-- LEARN:TRANSCRIPT:{canonical}:BEGIN -->",
        f"<!-- LEARN:TRANSCRIPT:{canonical}:END -->",
    )


def event_identity(data: dict, role: str) -> dict:
    identity = {
        "session_id": hook_session_id(data),
        "turn_id": hook_turn_id(data),
        "role": role,
    }
    # Current Codex hook schemas expose one relevant message per role/turn and
    # no message ID. Preserve a runtime ID if a future compatible payload adds one.
    runtime_id = data.get("message_id") or data.get("event_id")
    if runtime_id is not None and str(runtime_id).strip():
        identity["runtime_event_id"] = str(runtime_id).strip()
    return identity


def message_id_for(identity: dict) -> str:
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def render_transcript(lesson: dict) -> str:
    blocks = []
    for message in lesson["messages"]:
        label = "Learner" if message["role"] == "user" else "Tutor"
        icon = "🧑" if message["role"] == "user" else "🤖"
        heading = f"{icon} {label} · {message['captured_at']}"
        blocks.append(f"---\n\n### {heading}\n\n{message['markdown'].rstrip()}\n")
    return "\n" + "\n".join(blocks).rstrip() + "\n"


def ensure_lesson_transcript_markers(text: str, lesson_id: str) -> str:
    expected_begin, expected_end = lesson_transcript_markers(lesson_id)
    expected_first = text.find(expected_begin)
    expected_last = text.rfind(expected_end)
    if expected_first >= 0 or expected_last >= 0:
        if expected_first < 0 or expected_last < expected_first:
            raise LearnError("Session note has invalid lesson-specific transcript markers")
        return text
    raise LearnError("Session note has no lesson-specific transcript markers")


def replace_lesson_transcript(text: str, lesson: dict) -> str:
    begin, end = lesson_transcript_markers(lesson["lesson_id"])
    first = text.find(begin)
    last = text.rfind(end)
    if first < 0 or last < first:
        raise LearnError("Session note has invalid lesson-specific transcript markers")
    content_start = first + len(begin)
    return text[:content_start] + render_transcript(lesson) + text[last:]


def replace_managed_after(text: str, begin: str, end: str, body: str, offset: int) -> str:
    first = text.find(begin, offset)
    last = text.find(end, first + len(begin)) if first >= 0 else -1
    if first < 0 or last < first:
        raise LearnError(f"Missing or invalid generated-state markers: {begin} / {end}")
    content_start = first + len(begin)
    return text[:content_start] + "\n" + body.rstrip() + "\n" + text[last:]


def render_lesson_note(config: dict, lesson: dict) -> tuple[Path, str | None]:
    note = lesson_note_path(config, lesson)
    if note.exists():
        text = ensure_lesson_transcript_markers(
            note.read_text(encoding="utf-8"), lesson["lesson_id"]
        )
    else:
        text = render_session_template(
            lesson["title"],
            lesson["goal"],
            lesson["mode"],
            lesson["topic_id"],
            lesson.get("starting_point") or "Resume from the canonical lesson record.",
            active_session_id(lesson),
            lesson["lesson_id"],
        )
    text = update_frontmatter_fields(
        text,
        {
            "learn_type": "session",
            "lesson_id": yaml_string(lesson["lesson_id"]),
            "session_id": yaml_string(active_session_id(lesson)),
            "title": yaml_string(lesson["title"]),
            "mode": yaml_string(lesson["mode"]),
            "goal": yaml_string(lesson["goal"]),
            "status": str(lesson["status"]),
            "topic": yaml_string(f"[[Topics/{lesson['topic_id']}]]"),
        },
        f"Session note {note}",
    )
    text = replace_lesson_transcript(text, lesson)
    transcript_end = text.rfind(lesson_transcript_markers(lesson["lesson_id"])[1])
    assessment = lesson.get("final_assessment")
    assessment_payload = assessment.get("payload") if isinstance(assessment, dict) else None
    if isinstance(assessment_payload, dict):
        synthesis = synthesis_markdown(assessment_payload)
    else:
        synthesis = "Session still in progress."
    text = replace_managed_after(text, *MARKER_PAIRS["SYNTHESIS"], synthesis, transcript_end)
    synthesis_end = text.find(MARKER_PAIRS["SYNTHESIS"][1], transcript_end)
    source_lines = "\n".join(f"- [[Sources/{key}]]" for key in lesson.get("source_citekeys", []))
    text = replace_managed_after(
        text, *MARKER_PAIRS["SOURCES"], source_lines or "No sources recorded yet.", synthesis_end
    )
    atomic_write(note, text)
    tutor = [message for message in lesson["messages"] if message["role"] == "assistant"]
    heading = None
    if tutor:
        heading = f"🤖 Tutor · {tutor[-1]['captured_at']}"
    return note, heading


def capture_lesson_message(
    config: dict, data: dict, role: str, markdown: str
) -> tuple[dict, bool | None, Path | None, str | None]:
    session_id = hook_session_id(data)
    lesson = read_active(config, session_id)
    if not lesson or not lesson.get("lesson_id"):
        raise LearnError(f"No canonical lesson is attached to conversation {session_id}")
    identity = event_identity(data, role)
    if cross_lesson_event_replay(config, lesson["lesson_id"], identity, markdown):
        return lesson, None, None, None
    key = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    for existing in lesson["messages"]:
        existing_key = json.dumps(
            existing["event_identity"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        if existing_key != key:
            continue
        if existing["markdown"] != markdown:
            raise EventConflictError("Conflicting replay for an existing hook event identity")
        note, heading = render_lesson_note(config, lesson)
        return lesson, False, note, heading
    captured_at = iso_now()
    lesson["messages"].append(
        {
            "message_id": message_id_for(identity),
            "event_identity": identity,
            "role": role,
            "markdown": markdown,
            "captured_at": captured_at,
        }
    )
    save_lesson(config, lesson)
    note, heading = render_lesson_note(config, lesson)
    return lesson, True, note, heading


def cross_lesson_event_replay(config: dict, lesson_id: str, identity: dict, markdown: str) -> bool:
    """Reject conflicts and identify harmless replays already owned by another lesson."""
    key = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    for path in (system_root(config) / "lessons").glob("*.json"):
        if path.stem == lesson_id:
            continue
        other = load_lesson(config, path.stem)
        for message in other["messages"]:
            other_key = json.dumps(
                message["event_identity"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            if other_key != key:
                continue
            if message["markdown"] != markdown:
                raise EventConflictError(
                    "Hook event identity is already owned by another lesson with different content"
                )
            return True
    return False


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


def is_explicit_learn_activation(prompt: str) -> bool:
    """Accept a leading CLI token or IDE link to the local Learn skill."""
    if re.match(r"^\s*\$learn(?:\s|$)", prompt):
        return True
    # The IDE serializes a selected skill as [$learn](.../learn/SKILL.md).
    # Recognize that narrow form without rewriting the captured prompt,
    # reading the linked file, or matching mentions elsewhere in prose/code.
    link = re.match(
        r"^\s*\[\$learn\]\((?P<target><[^<>\r\n]+>|[^<>\r\n]*[/\\]learn[/\\]SKILL\.md)\)(?=\s|$)",
        prompt,
    )
    if not link:
        return False
    target = link.group("target").removeprefix("<").removesuffix(">")
    return "://" not in target and re.search(r"(?:^|[/\\])learn[/\\]SKILL\.md$", target) is not None


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
            active = read_active(config, session_id)
            canonical_logging = bool(active and active.get("status") == "active")
            if not canonical_logging and not is_explicit_learn_activation(prompt):
                return
            record = {
                "session_id": session_id,
                "turn_id": turn_id,
                "cwd": str(data.get("cwd") or ""),
                "prompt": prompt,
                "created_at": iso_now(),
                "created_at_ns": int(now_utc().timestamp() * 1_000_000_000),
            }
            runtime_event_id = data.get("message_id") or data.get("event_id")
            if runtime_event_id is not None and str(runtime_event_id).strip():
                record["runtime_event_id"] = str(runtime_event_id).strip()
            rendered_note = None
            if canonical_logging:
                active, stored, note, _ = capture_lesson_message(config, data, "user", prompt)
                if stored is None:
                    return
                rendered_note = str(active["note_relative"])
            else:
                write_json(pending_path(config, session_id), record)
            if canonical_logging:
                write_json(pending_path(config, session_id), record)
            record_capture_health(
                config,
                event,
                session_id,
                turn_id,
                rendered_note,
            )
            return

        active = read_active(config, session_id)
        if not active:
            return
        message = data.get("last_assistant_message")
        if not isinstance(message, str) or not message:
            return
        rendered_note = None
        if active.get("status") == "finishing":
            expected = active.get("expected_finishing") or {}
            if expected.get("session_id") != session_id or expected.get("turn_id") != turn_id:
                return
        elif active.get("status") != "active":
            return
        active, stored, note, heading = capture_lesson_message(config, data, "assistant", message)
        if stored is None:
            return
        if stored:
            follow_request = (note, dict(active), heading)
        rendered_note = str(active["note_relative"])
        if active.get("status") == "finishing":
            active["status"] = "completed"
            active["completed_at"] = iso_now()
            active["conversation_binding"]["detached_at"] = active["completed_at"]
            save_lesson(config, active)
            render_error = None
            try:
                note, _ = render_lesson_note(config, active)
            except (LearnError, OSError) as exc:
                mark_rendering_repair_locked(config, active, "complete-render", exc, turn_id)
                render_error = exc
            active_path(config, session_id).unlink(missing_ok=True)
            if render_error:
                raise SavedRenderingError("Final response was saved and lesson completed; rendering needs repair") from render_error
        record_capture_health(config, event, session_id, turn_id, rendered_note)
    if follow_request and config.get("open_notes_automatically"):
        note, active, heading = follow_request
        try:
            follow_lesson_output(config, note, active, heading)
        except (LearnError, OSError, subprocess.SubprocessError) as exc:
            record_operational_error(config, data, "gui-follow", exc, needs_rendering_repair=False)


def pending_for_session(config: dict, session_id: str | None = None) -> dict:
    exact = exact_runtime_session_id(session_id)
    record = read_json(pending_path(config, exact), None)
    if not record:
        raise LearnError(f"No pending prompt for Codex session {exact}")
    if record.get("session_id") != exact:
        raise LearnError("Pending prompt identity does not match its storage key")
    return record


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
        body = update_frontmatter_fields(
            body,
            {
                "learn_type": "topic",
                "topic_id": yaml_string(topic["slug"]),
                "title": yaml_string(topic["title"]),
                "state": yaml_string(topic["state"]),
                "last_review": yaml_string(review.get("last_review") or ""),
                "next_review": yaml_string(review.get("next_review") or ""),
            },
            f"Topic note {path}",
        )
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
    title: str,
    goal: str,
    mode: str,
    slug: str,
    starting_point: str,
    session_id: str,
    lesson_id: str,
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
    begin, end = lesson_transcript_markers(lesson_id)
    text = text.replace(MARKER_PAIRS["TRANSCRIPT"][0], begin).replace(MARKER_PAIRS["TRANSCRIPT"][1], end)
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
    session_id = exact_runtime_session_id(args.session_id)
    with state_lock(config):
        pending = pending_for_session(config, session_id=session_id)
        session_id = pending["session_id"]
        existing = read_active(config, session_id)
        if not existing:
            existing = recover_exact_binding(config, session_id)
        if existing:
            note, _ = render_lesson_note(config, existing)
            current_active = existing
            response = {
                "status": "resumed",
                "session_id": session_id,
                "lesson_id": existing["lesson_id"],
                "note": str(note),
                "recovery": recovery_context(config, existing),
                "lesson_status": existing["status"],
                "expected_final": existing.get("expected_finishing"),
            }
            if existing["status"] == "finishing":
                response["instruction"] = (
                    "Do not continue teaching; this lesson still awaits its exact final Stop."
                )
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
            lesson_id = str(uuid.uuid4())
            name = f"{stamp}--{slug}--{lesson_id[:8]}.md"
            note = learning_root(config) / "Sessions" / name
            relative = note.relative_to(Path(config["vault_path"])).as_posix()
            attached_at = iso_now()
            identity = {
                "session_id": session_id,
                "turn_id": pending["turn_id"],
                "role": "user",
            }
            if pending.get("runtime_event_id"):
                identity["runtime_event_id"] = pending["runtime_event_id"]
            active = {
                "schema_version": LESSON_SCHEMA_VERSION,
                "lesson_id": lesson_id,
                "topic_id": slug,
                "title": args.title,
                "goal": args.goal,
                "mode": args.mode,
                "time_budget": args.time_budget,
                "status": "active",
                "conversation_binding": {"session_id": session_id, "attached_at": attached_at},
                "cwd": pending.get("cwd", ""),
                "initiating_turn_id": pending["turn_id"],
                "note_relative": relative,
                "created_at": attached_at,
                "updated_at": attached_at,
                "revision": 0,
                "starting_point": starting,
                "messages": [
                    {
                        "message_id": message_id_for(identity),
                        "event_identity": identity,
                        "role": "user",
                        "markdown": pending["prompt"],
                        "captured_at": pending.get("created_at") or attached_at,
                    }
                ],
                "latest_checkpoint": None,
                "final_assessment": None,
                "expected_finishing": None,
                "source_citekeys": [],
                "source_usages": [],
                "codex_host_bundle_id": detect_codex_host_bundle_id(),
                "mcq_position_state": {"issuances": {}, "history": []},
            }
            save_lesson(config, active)
            bind_lesson(config, active)
            try:
                note, _ = render_lesson_note(config, active)
            except (LearnError, OSError) as exc:
                mark_rendering_repair_locked(config, active, "start-render", exc, pending["turn_id"])
                raise SavedRenderingError("Lesson was saved; rendering needs repair") from exc
            current_active = active
            response = {
                "status": "started",
                "session_id": session_id,
                "lesson_id": lesson_id,
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
    del cwd
    exact = exact_runtime_session_id(session_id)
    active = read_active(config, exact)
    if not active:
        raise LearnError(f"No active lesson for session {exact}")
    return active


def resolve_message_reference(
    lesson: dict,
    reference: str,
    current_turn: str | None = None,
    required_role: str | None = None,
) -> str:
    messages = lesson["messages"]
    by_id = {str(message.get("message_id") or ""): message for message in messages}
    if reference in by_id:
        candidates = [by_id[reference]]
    else:
        turn_id = None
        if reference == "current":
            if not current_turn:
                raise LearnError("The current message selector requires an exact current turn")
            turn_id = current_turn
        elif reference.startswith("turn:"):
            turn_id = reference[5:]
        if turn_id is None:
            raise LearnError(f"Message reference does not resolve to a recorded message: {reference}")
        candidates = [
            message
            for message in messages
            if str(message.get("event_identity", {}).get("turn_id") or "") == turn_id
        ]
    if required_role:
        candidates = [message for message in candidates if message.get("role") == required_role]
    if len(candidates) != 1:
        raise LearnError(f"Message selector must resolve to exactly one recorded message: {reference}")
    return str(candidates[0]["message_id"])


def validate_checkpoint_shape(payload: dict) -> None:
    missing = sorted(CHECKPOINT_KEYS - set(payload))
    extra = sorted(set(payload) - CHECKPOINT_KEYS)
    if missing:
        raise LearnError("Checkpoint is missing: " + ", ".join(missing))
    if extra:
        raise LearnError("Checkpoint has unsupported keys: " + ", ".join(extra))
    for key in ("phase", "goal_and_route", "next_teaching_step", "covered_through"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise LearnError(f"Checkpoint {key} must be a non-empty string")
    outstanding = payload["outstanding_question"]
    if outstanding is not None and (not isinstance(outstanding, str) or not outstanding.strip()):
        raise LearnError("Checkpoint outstanding_question must be null or a non-empty string")
    for key in (
        "relevant_concepts",
        "demonstrated_understanding",
        "misconceptions",
        "unknowns",
        "evidence_refs",
        "hints_and_independence",
    ):
        values = payload[key]
        if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
            raise LearnError(f"Checkpoint {key} must be a list of non-empty strings")
        if len(values) > 12:
            raise LearnError(f"Checkpoint {key} must contain at most 12 items")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_CHECKPOINT_BYTES:
        raise LearnError(f"Checkpoint must be at most {MAX_CHECKPOINT_BYTES} UTF-8 bytes")


def prepare_checkpoint_record(
    lesson: dict, payload: dict, current_turn: str | None = None
) -> dict:
    validate_checkpoint_shape(payload)
    normalized = json_copy(payload)
    normalized["evidence_refs"] = [
        resolve_message_reference(lesson, reference, current_turn, required_role="user")
        for reference in payload["evidence_refs"]
    ]
    covered = resolve_message_reference(lesson, payload["covered_through"], current_turn)
    normalized.pop("covered_through")
    normalized["covered_through_message_id"] = covered
    return normalized


def checkpoint_fingerprint(lesson: dict, checkpoint: dict) -> str:
    encoded = json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        f"{lesson['lesson_id']}\0checkpoint\0{encoded}".encode("utf-8")
    ).hexdigest()


def validate_stored_checkpoint(lesson: dict) -> None:
    checkpoint = lesson.get("latest_checkpoint")
    if not isinstance(checkpoint, dict):
        raise LearnError("Lesson latest_checkpoint must be an object or null")
    semantic_keys = (CHECKPOINT_KEYS - {"covered_through"}) | {"covered_through_message_id"}
    if set(checkpoint) != semantic_keys:
        raise LearnError("Stored checkpoint has an invalid schema")
    shape = dict(checkpoint)
    shape["covered_through"] = shape.pop("covered_through_message_id")
    validate_checkpoint_shape(shape)
    covered = resolve_message_reference(lesson, checkpoint["covered_through_message_id"])
    if covered != checkpoint["covered_through_message_id"]:
        raise LearnError("Stored checkpoint covered-through reference is not canonical")
    for reference in checkpoint["evidence_refs"]:
        resolved = resolve_message_reference(lesson, reference, required_role="user")
        if resolved != reference:
            raise LearnError("Stored checkpoint evidence reference is not canonical")


def checkpoint_message_index(lesson: dict, message_id: str) -> int:
    for index, message in enumerate(lesson["messages"]):
        if message.get("message_id") == message_id:
            return index
    raise LearnError(f"Checkpoint references a missing lesson message: {message_id}")


def apply_checkpoint_to_lesson(lesson: dict, checkpoint: dict) -> bool:
    existing = lesson.get("latest_checkpoint")
    new_index = checkpoint_message_index(lesson, checkpoint["covered_through_message_id"])
    if isinstance(existing, dict):
        old_index = checkpoint_message_index(lesson, str(existing.get("covered_through_message_id") or ""))
        if new_index < old_index:
            raise LearnError("Checkpoint covered-through message cannot move backward")
        if existing == checkpoint:
            return False
    lesson["latest_checkpoint"] = checkpoint
    return True


def compact_topic_summary(topic: dict | None, limit: int = 8) -> dict | None:
    if not topic:
        return None
    result = {
        "slug": topic["slug"],
        "title": topic["title"],
        "state": topic["state"],
        "mental_model": topic.get("mental_model"),
        "next_review": topic.get("review", {}).get("next_review"),
    }
    omitted = {}
    for field in (
        "demonstrated_abilities",
        "misconceptions",
        "unresolved_gaps",
        "retrieval_prompts",
    ):
        values = list(topic.get(field, []))
        result[field] = values[-limit:]
        if len(values) > limit:
            omitted[field] = len(values) - limit
    result["omitted_item_counts"] = omitted
    return result


def recovery_context(
    config: dict,
    lesson: dict,
    limit: int = DEFAULT_CONTEXT_MESSAGES,
    message_id: str | None = None,
    from_message: str | None = None,
    to_message: str | None = None,
) -> dict:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_CONTEXT_MESSAGES:
        raise LearnError(f"Context limit must be between 1 and {MAX_CONTEXT_MESSAGES}")
    messages = lesson["messages"]
    indexes = {str(message["message_id"]): index for index, message in enumerate(messages)}
    if message_id:
        if message_id not in indexes:
            raise LearnError(f"Unknown lesson message_id: {message_id}")
        start = indexes[message_id]
        end = start + 1
    else:
        checkpoint = lesson.get("latest_checkpoint")
        start = 0
        if checkpoint:
            start = checkpoint_message_index(
                lesson, str(checkpoint.get("covered_through_message_id") or "")
            ) + 1
        if from_message:
            if from_message not in indexes:
                raise LearnError(f"Unknown lesson message_id: {from_message}")
            start = indexes[from_message]
        end = len(messages)
        if to_message:
            if to_message not in indexes:
                raise LearnError(f"Unknown lesson message_id: {to_message}")
            end = indexes[to_message] + 1
        if start > end:
            raise LearnError("Context message range is reversed")
    selected = messages[start:end]
    omitted_before = max(0, len(selected) - limit)
    selected = selected[-limit:]
    public_messages = [
        {
            "message_id": message["message_id"],
            "role": message["role"],
            "markdown": message["markdown"],
            "captured_at": message["captured_at"],
        }
        for message in selected
    ]
    return {
        "session": {
            "session_id": active_session_id(lesson),
            "lesson_id": lesson.get("lesson_id"),
            "title": lesson["title"],
            "goal": lesson["goal"],
            "mode": lesson["mode"],
            "status": lesson.get("status", "active"),
            "expected_final": lesson.get("expected_finishing"),
            "note": str(Path(config["vault_path"]) / lesson["note_relative"]),
        },
        "checkpoint": json_copy(lesson.get("latest_checkpoint")),
        "topic_summary": compact_topic_summary(topic_record(config, active_topic_id(lesson))),
        "history": {
            "messages": public_messages,
            "returned_count": len(public_messages),
            "available_count": end - start,
            "more_history_exists": omitted_before > 0,
            "omitted_before_count": omitted_before,
            "retrieval_hint": (
                "Use context --from-message/--to-message or --message-id for targeted retrieval."
                if omitted_before
                else None
            ),
        },
    }


def command_checkpoint(args) -> dict:
    config = load_config()
    payload = load_payload(args)
    with state_lock(config):
        lesson = find_current_active(config, args.session_id, args.cwd)
        if lesson["status"] != "active":
            raise LearnError(f"Cannot checkpoint a lesson in {lesson['status']} state")
        pending = pending_for_session(config, active_session_id(lesson))
        checkpoint = prepare_checkpoint_record(lesson, payload, pending["turn_id"])
        changed = apply_checkpoint_to_lesson(lesson, checkpoint)
        if changed:
            save_lesson(config, lesson)
    return {
        "status": "saved" if changed else "unchanged",
        "lesson_id": lesson["lesson_id"],
        "checkpoint_fingerprint": checkpoint_fingerprint(lesson, checkpoint),
        "covered_through_message_id": checkpoint["covered_through_message_id"],
    }


def command_context(args) -> dict:
    config = load_config()
    if args.next_mcq:
        if args.message_id or args.from_message or args.to_message:
            raise LearnError("MCQ issuance cannot be combined with message retrieval selectors")
        with state_lock(config):
            active = find_current_active(config, args.session_id, args.cwd)
            if args.correct_count > args.choices:
                raise LearnError("--correct-count cannot exceed --choices")
            pending = pending_for_session(config, session_id=active_session_id(active))
            if not args.question_id:
                raise LearnError("--question-id is required with --next-mcq")
            before_issuance = json.dumps(
                active.get("mcq_position_state"), ensure_ascii=False, sort_keys=True
            )
            positions = issue_mcq_positions(
                active, args.choices, args.correct_count, args.question_id, pending["turn_id"]
            )
            after_issuance = json.dumps(
                active.get("mcq_position_state"), ensure_ascii=False, sort_keys=True
            )
            if after_issuance != before_issuance:
                save_lesson(config, active)
        labels = [chr(ord("A") + position) for position in positions]
        return {
            "question_id": args.question_id,
            "correct_option_labels": labels,
            "answer_choices": args.choices,
            "selection": "single-select" if len(labels) == 1 else "multi-select",
            "instruction": (
                "Put the correct answer content at exactly these labels; do not reroll. "
                "Append 'I don't know' after the substantive choices."
            ),
        }
    if args.question_id:
        raise LearnError("--question-id is used only with --next-mcq")
    if args.message_id and args.to_message:
        raise LearnError("--message-id cannot be combined with --to-message")
    active = find_current_active(config, args.session_id, args.cwd)
    return recovery_context(
        config,
        active,
        args.limit,
        message_id=args.message_id,
        from_message=args.from_message,
        to_message=args.to_message,
    )


def issue_mcq_positions(
    active: dict, choices: int, correct_count: int, question_id: str, turn_id: str
) -> list[int]:
    """Sample correct positions once for a stable semantic question identity."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", question_id):
        raise LearnError("question_id must be 1–80 stable identifier characters")
    state = active.get("mcq_position_state")
    if not isinstance(state, dict) or "issuances" not in state:
        state = {"issuances": {}, "history": []}
        active["mcq_position_state"] = state
    issued = state["issuances"].get(question_id)
    if issued:
        if (
            issued["choices"] != choices
            or issued["correct_count"] != correct_count
            or issued.get("question_id") != question_id
        ):
            raise LearnError(
                "An MCQ layout was already issued for this question_id; use its original choices and correct count"
            )
        return list(issued["positions"])
    for prior in state.get("history", []):
        if prior.get("turn_id") == turn_id and prior.get("question_id") != question_id:
            raise LearnError("A different MCQ question_id was already issued for this learner turn")
    positions = sorted(secrets.SystemRandom().sample(range(choices), correct_count))
    record = {
        "question_id": question_id,
        "turn_id": turn_id,
        "choices": choices,
        "correct_count": correct_count,
        "positions": positions,
        "issued_at": iso_now(),
    }
    state["issuances"][question_id] = record
    state.setdefault("history", []).append(record)
    return positions


def command_open(args) -> dict:
    config = load_config()
    active = find_current_active(config, args.session_id, args.cwd)
    note = lesson_note_path(config, active)
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
        "session_id": active_session_id(active),
        "lesson_id": active.get("lesson_id"),
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


def source_observation(source: dict) -> dict:
    return {
        "verification_status": source["verification_status"],
        "retrieval_date": source["retrieval_date"],
        "precise_locator": source["precise_locator"],
        "claims_supported": list(source.get("claims_supported", [])),
    }


def merge_source_record(existing: dict | None, incoming: dict) -> dict:
    merged = dict(existing or {})
    citekey = str(existing["citekey"] if existing else incoming["citekey"])
    for key in REQUIRED_SOURCE_KEYS | {"url", "doi", "local_path"}:
        if key == "citekey":
            continue
        if key in {"url", "doi", "local_path"} and not incoming.get(key) and existing:
            merged[key] = existing.get(key, "")
        else:
            merged[key] = incoming.get(key, "")
    merged["citekey"] = citekey
    merged["normalized_identities"] = sorted(
        set(existing.get("normalized_identities", []) if existing else [])
        | set(incoming.get("normalized_identities", []))
    )
    locators = list(existing.get("precise_locators", []) if existing else [])
    if existing and existing.get("precise_locator") and existing["precise_locator"] not in locators:
        locators.append(existing["precise_locator"])
    if incoming["precise_locator"] not in locators:
        locators.append(incoming["precise_locator"])
    merged["precise_locators"] = locators
    history = list(existing.get("verification_history", []) if existing else [])
    if existing and not history:
        history.append(source_observation(existing))
    observation = source_observation(incoming)
    if observation not in history:
        history.append(observation)
    merged["verification_history"] = history
    merged["updated_at"] = iso_now()
    validate_source_payload(merged)
    return merged


def render_source_note(config: dict, source: dict) -> Path:
    path = learning_root(config) / "Sources" / f"{source['citekey']}.md"
    claims = list_items(source.get("claims_supported", []))
    reference = source.get("url") or source.get("doi") or source.get("local_path")
    if source.get("doi") and not source.get("url"):
        reference = "https://doi.org/" + normalize_doi(source["doi"])
    frontmatter = f"""---
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
---"""
    managed = f"""# {source['title']}

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
    if path.exists():
        text = update_frontmatter_fields(
            path.read_text(encoding="utf-8"),
            {
                "learn_type": "source",
                "citekey": yaml_string(source["citekey"]),
                "title": yaml_string(source["title"]),
                "author_or_organization": yaml_string(source["author_or_organization"]),
                "year_or_date": yaml_string(source["year_or_date"]),
                "source_type": yaml_string(source["source_type"]),
                "verification_status": yaml_string(source["verification_status"]),
                "retrieval_date": yaml_string(source["retrieval_date"]),
                "precise_locator": yaml_string(source["precise_locator"]),
                "url": yaml_string(source.get("url") or ""),
                "doi": yaml_string(source.get("doi") or ""),
                "local_path": yaml_string(source.get("local_path") or ""),
            },
            f"Source note {path}",
        )
        begin, end = MARKER_PAIRS["SOURCE_RECORD"]
        if begin in text or end in text:
            text = replace_managed(text, begin, end, managed)
    else:
        begin, end = MARKER_PAIRS["SOURCE_RECORD"]
        text = (
            f"{frontmatter}\n\n{begin}\n{managed.rstrip()}\n{end}\n\n"
            "## My notes\n\nWrite anything here. This section is never replaced by learnctl.\n"
        )
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
    with state_lock(config):
        active = None
        if selected_active:
            active = read_active(config, active_session_id(selected_active))
            if not active:
                raise LearnError("Learning session closed while source logging was running")
        source_status = "created"
        citekey = payload["citekey"]
        existing = None
        existing_path = None
        for path in (system_root(config) / "sources").glob("*.json"):
            candidate = read_json(path)
            if set(candidate.get("normalized_identities", [])) & set(payload["normalized_identities"]):
                source_status = "deduplicated"
                existing = candidate
                existing_path = path
                citekey = candidate["citekey"]
                break
        if existing is None:
            target = source_json_path(config, citekey)
            if target.exists():
                existing = read_json(target)
                if set(existing.get("normalized_identities", [])) != set(payload["normalized_identities"]):
                    raise LearnError(f"citekey already belongs to another source: {citekey}")
                source_status = "deduplicated"
                existing_path = target
        stored = merge_source_record(existing, payload)
        write_json(existing_path or source_json_path(config, citekey), stored)
        note = render_source_note(config, stored)
        if active:
            citekeys = active.setdefault("source_citekeys", [])
            if citekey not in citekeys:
                citekeys.append(citekey)
            usage = {
                "citekey": citekey,
                "verification_status": payload["verification_status"],
                "retrieval_date": payload["retrieval_date"],
                "precise_locator": payload["precise_locator"],
                "claims_supported": list(payload["claims_supported"]),
            }
            usages = active.setdefault("source_usages", [])
            if usage not in usages:
                usages.append(usage)
            save_lesson(config, active)
            session_note, _ = render_lesson_note(config, active)
    result = {"status": source_status, "citekey": citekey, "note": str(note)}
    if active:
        result["attached_session_id"] = active_session_id(active)
        result["lesson_id"] = active.get("lesson_id")
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


def validate_reference_list(value, label: str, required: bool = False) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise LearnError(f"{label} must be a list of non-empty message references")
    if required and not value:
        raise LearnError(f"{label} requires at least one learner-message reference")


def validate_semantic_evidence_shape(payload: dict) -> None:
    state = payload.get("evidence_state")
    if state not in TOPIC_STATES:
        raise LearnError("Invalid evidence_state")
    if state == "unseen":
        raise LearnError("An assessment cannot return a topic to unseen")
    abilities = payload.get("demonstrated_abilities")
    if not isinstance(abilities, list):
        raise LearnError("demonstrated_abilities must be a list")
    for item in abilities:
        if isinstance(item, dict):
            if not isinstance(item.get("ability"), str) or not item["ability"].strip():
                raise LearnError("Every structured demonstrated ability needs an ability string")
            if item.get("evidence_type") not in EVIDENCE_TYPES:
                raise LearnError("Invalid demonstrated ability evidence_type")
            if not isinstance(item.get("independent"), bool) or not isinstance(item.get("delayed"), bool):
                raise LearnError("Structured abilities require boolean independent and delayed")
            validate_reference_list(item.get("evidence_refs"), "ability evidence_refs", required=True)
        elif not isinstance(item, str) or not item.strip():
            raise LearnError("demonstrated_abilities entries must be non-empty strings or evidence objects")
    transfer = payload.get("transfer_task_result")
    transfer_keys = {"attempted", "successful", "independent", "delayed", "summary", "evidence_refs"}
    if not isinstance(transfer, dict) or not transfer_keys.issubset(transfer):
        raise LearnError(
            "transfer_task_result requires attempted, successful, independent, delayed, summary, and evidence_refs"
        )
    if any(not isinstance(transfer[key], bool) for key in ("attempted", "successful", "independent", "delayed")):
        raise LearnError("transfer_task_result flags must be booleans")
    if not isinstance(transfer["summary"], str):
        raise LearnError("transfer_task_result summary must be a string")
    validate_reference_list(
        transfer["evidence_refs"], "transfer_task_result evidence_refs", required=transfer["attempted"]
    )
    if transfer["successful"] and not transfer["attempted"]:
        raise LearnError("A successful transfer task must be marked attempted")
    if transfer["independent"] and not transfer["successful"]:
        raise LearnError("An independent transfer result must be successful")
    if transfer["delayed"] and not transfer["attempted"]:
        raise LearnError("A delayed transfer task must be marked attempted")
    for key in ("misconceptions", "unresolved_gaps"):
        if not isinstance(payload.get(key), list) or any(
            not isinstance(item, str) or not item.strip() for item in payload[key]
        ):
            raise LearnError(f"{key} must be a list of non-empty strings")
    if not isinstance(payload.get("reassessment"), bool):
        raise LearnError("reassessment must be a boolean")
    validate_reference_list(payload.get("contrary_evidence_refs"), "contrary_evidence_refs")
    if payload["reassessment"] and not payload["contrary_evidence_refs"]:
        raise LearnError("An explicit reassessment requires contrary_evidence_refs")
    if payload["contrary_evidence_refs"] and not payload["reassessment"]:
        raise LearnError("contrary_evidence_refs require reassessment=true")


def parse_evidence_timestamp(value: object, label: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise LearnError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def semantic_evidence_refs(payload: dict) -> list[str]:
    refs = []
    for item in payload.get("demonstrated_abilities", []):
        if isinstance(item, dict):
            refs.extend(item.get("evidence_refs", []))
    transfer = payload.get("transfer_task_result")
    if isinstance(transfer, dict):
        refs.extend(transfer.get("evidence_refs", []))
    refs.extend(payload.get("contrary_evidence_refs", []))
    return refs


def validate_evidence_assessment(payload: dict, messages: list[dict], assessed_at: str) -> None:
    """Mechanically validate semantic evidence for both lesson and review assessments."""
    validate_semantic_evidence_shape(payload)
    assessed = parse_evidence_timestamp(assessed_at, "assessment timestamp")
    learners = {}
    for message in messages:
        if message.get("role") != "user":
            continue
        message_id = str(message.get("message_id") or "")
        if not message_id:
            raise LearnError("Referenced lesson contains a learner message without message_id")
        captured = parse_evidence_timestamp(message.get("captured_at"), f"learner message {message_id} timestamp")
        learners[message_id] = captured
    for reference in semantic_evidence_refs(payload):
        if reference not in learners:
            raise LearnError(f"Evidence reference does not resolve to a recorded learner response: {reference}")
        if learners[reference] > assessed:
            raise LearnError(f"Evidence reference is later than the assessment timestamp: {reference}")

    rank = TOPIC_STATES.index(payload["evidence_state"])
    recall = ability_evidence(payload, {"recall", "explanation"})
    delayed_recall = ability_evidence(payload, {"recall", "explanation"}, delayed=True)
    ability_application = ability_evidence(payload, {"application", "transfer"})
    delayed_ability_transfer = ability_evidence(payload, {"transfer"}, delayed=True)
    transfer = payload["transfer_task_result"]
    applied = ability_application or (transfer["successful"] and transfer["independent"])
    delayed_transfer = delayed_ability_transfer or (
        transfer["successful"] and transfer["independent"] and transfer["delayed"]
    )
    if rank >= TOPIC_STATES.index("retrievable") and not recall:
        raise LearnError("retrievable or higher requires recorded independent recall or explanation evidence")
    if rank >= TOPIC_STATES.index("applicable") and not applied:
        raise LearnError("applicable or higher requires recorded successful independent application or transfer")
    if payload["evidence_state"] == "robust" and not (delayed_recall and delayed_transfer):
        raise LearnError("robust requires recorded delayed independent retrieval and transfer")


def resolve_evidence_references(payload: dict, lesson: dict, current_turn: str | None = None) -> dict:
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    for item in normalized.get("demonstrated_abilities", []):
        if isinstance(item, dict):
            item["evidence_refs"] = [
                resolve_message_reference(lesson, reference, current_turn, required_role="user")
                for reference in item.get("evidence_refs", [])
            ]
    transfer = normalized.get("transfer_task_result")
    if isinstance(transfer, dict):
        transfer["evidence_refs"] = [
            resolve_message_reference(lesson, reference, current_turn, required_role="user")
            for reference in transfer.get("evidence_refs", [])
        ]
    normalized["contrary_evidence_refs"] = [
        resolve_message_reference(lesson, reference, current_turn, required_role="user")
        for reference in normalized.get("contrary_evidence_refs", [])
    ]
    return normalized


def validate_finish_payload(
    payload: dict,
    config: dict,
    messages: list[dict] | None = None,
    assessed_at: str | None = None,
    validate_sources: bool = True,
) -> None:
    missing = sorted(REQUIRED_FINISH_KEYS - set(payload))
    extra = sorted(set(payload) - REQUIRED_FINISH_KEYS - OPTIONAL_FINISH_KEYS)
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
    validate_semantic_evidence_shape(payload)
    score = payload["suggested_review_score"]
    if not isinstance(score, int) or isinstance(score, bool) or score not in range(4):
        raise LearnError("suggested_review_score must be 0, 1, 2, or 3")

    if messages is not None:
        validate_evidence_assessment(payload, messages, assessed_at or iso_now())

    if validate_sources:
        for citekey in payload["source_citekeys"]:
            if not isinstance(citekey, str):
                raise LearnError("source_citekeys must contain strings")
            source = read_json(source_json_path(config, citekey), None)
            if not source:
                raise LearnError(f"Unknown source citekey: {citekey}")
            if source.get("verification_status") not in SUPPORTING_STATUSES:
                raise LearnError(f"Source {citekey} is not verified or user-provided")


def add_semantic_defaults(payload: dict) -> dict:
    normalized = json_copy(payload)
    normalized.setdefault("reassessment", False)
    normalized.setdefault("contrary_evidence_refs", [])
    transfer = normalized.get("transfer_task_result")
    if isinstance(transfer, dict):
        transfer.setdefault("evidence_refs", [])
    return normalized


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
    return update_frontmatter_fields(text, {"status": status}, "Session note")


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


def transcript_content_bounds(text: str) -> tuple[int, int] | None:
    dynamic = re.search(
        r"(?m)^<!-- LEARN:TRANSCRIPT:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):BEGIN -->[ \t]*$",
        text,
    )
    if dynamic:
        end_marker = lesson_transcript_markers(dynamic.group(1))[1]
        end = text.rfind(end_marker)
        if end >= dynamic.end():
            return dynamic.end(), end
        return None
    return None


def without_transcript_content(text: str) -> str:
    bounds = transcript_content_bounds(text)
    if not bounds:
        return text
    start, end = bounds
    return text[:start] + "\n" * text[start:end].count("\n") + text[end:]


def without_learner_transcript(text: str) -> str:
    bounds = transcript_content_bounds(text)
    if not bounds:
        return text
    start, end = bounds
    lines = []
    learner_block = False
    for line in text[start:end].splitlines():
        if re.match(r"^### 🧑 Learner · ", line):
            learner_block = True
        elif re.match(r"^### 🤖 Tutor · ", line):
            learner_block = False
        lines.append("" if learner_block else line)
    return text[:start] + "\n".join(lines) + text[end:]


def finish_assessment_id(lesson_id: str, payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{lesson_id}\0{encoded}".encode("utf-8")).hexdigest()


def ensure_session_structure_before_finish(config: dict, lesson: dict) -> None:
    note = lesson_note_path(config, lesson)
    if not note.exists():
        return
    text = note.read_text(encoding="utf-8")
    update_frontmatter_fields(text, {}, f"Session note {note}")
    transcript = transcript_content_bounds(text)
    if not transcript:
        raise LearnError("Session note has unusable transcript control structure")
    _, transcript_end = transcript
    synthesis = text.find(MARKER_PAIRS["SYNTHESIS"][0], transcript_end)
    sources = text.find(MARKER_PAIRS["SOURCES"][0], transcript_end)
    if synthesis < 0 or sources < synthesis:
        raise LearnError("Session note has unusable synthesis or source control structure")


def json_copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def topic_baseline(topic: dict) -> dict:
    return {
        "state": topic.get("state", "unseen"),
        **{field: json_copy(topic.get(field, [] if field != "mental_model" else "Not established yet.")) for field in TOPIC_EVIDENCE_FIELDS},
        "review": json_copy(
            topic.get("review", {"interval_index": 0, "last_review": None, "next_review": None, "history": []})
        ),
        "included_assessment_ids": list(topic.get("applied_assessment_ids", [])),
    }


def ensure_topic_derivation(topic: dict) -> None:
    if not isinstance(topic.get("historical_baseline"), dict):
        topic["historical_baseline"] = topic_baseline(topic)
    if not isinstance(topic.get("assessment_refs"), list):
        topic["assessment_refs"] = []
    for sequence, reference in enumerate(topic["assessment_refs"]):
        if isinstance(reference, dict):
            reference.setdefault("sequence", sequence)


def append_assessment_ref(topic: dict, reference: dict) -> None:
    for existing in topic["assessment_refs"]:
        if (
            isinstance(existing, dict)
            and existing.get("kind") == reference.get("kind")
            and existing.get("assessment_id") == reference.get("assessment_id")
        ):
            return
    stored = dict(reference)
    stored["sequence"] = len(topic["assessment_refs"])
    topic["assessment_refs"].append(stored)


def merge_unique(existing: list, incoming: list) -> list:
    merged = json_copy(existing)
    keys = {json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for item in merged}
    for item in incoming:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if key not in keys:
            merged.append(json_copy(item))
            keys.add(key)
    return merged


def apply_semantic_summary(derived: dict, payload: dict) -> None:
    prior_rank = TOPIC_STATES.index(derived["state"])
    proposed_rank = TOPIC_STATES.index(payload["evidence_state"])
    if proposed_rank > prior_rank or (proposed_rank < prior_rank and payload.get("reassessment") is True):
        derived["state"] = payload["evidence_state"]
    if "final_mental_model" in payload and (
        proposed_rank >= prior_rank or payload.get("reassessment") is True
    ):
        derived["mental_model"] = payload["final_mental_model"]
    for field in (
        "dependencies",
        "demonstrated_abilities",
        "hints_required",
        "misconceptions",
        "unresolved_gaps",
        "retrieval_prompts",
        "source_citekeys",
    ):
        if field in payload:
            derived[field] = merge_unique(derived.get(field, []), payload[field])


def lesson_assessment_effect(config: dict, reference: dict) -> dict:
    lesson = load_lesson(config, str(reference.get("lesson_id") or ""))
    assessment = lesson.get("final_assessment") or {}
    if assessment.get("assessment_id") != reference.get("assessment_id"):
        raise LearnError("Topic lesson assessment reference does not match its canonical lesson")
    recorded_at = str(assessment.get("recorded_at") or "")
    parse_evidence_timestamp(recorded_at, "lesson assessment timestamp")
    validate_finish_payload(
        assessment["payload"], config, lesson["messages"], recorded_at, validate_sources=False
    )
    return {
        "kind": "lesson",
        "assessment_id": assessment["assessment_id"],
        "lesson_id": lesson["lesson_id"],
        "effective_at": recorded_at,
        "assessed_on": parse_evidence_timestamp(recorded_at, "lesson assessment timestamp").date().isoformat(),
        "score": assessment["payload"]["suggested_review_score"],
        "semantic": assessment["payload"],
    }


def review_assessment_effect(config: dict, reference: dict) -> dict:
    assessment_id = str(reference.get("assessment_id") or "")
    record = read_json(review_json_path(config, assessment_id), None)
    if not record or record.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise LearnError(f"Missing or unsupported durable review assessment: {assessment_id}")
    if record.get("assessment_id") != assessment_id or record.get("topic_id") != reference.get("topic_id"):
        raise LearnError("Topic review assessment reference is inconsistent")
    stable_content = {
        key: record.get(key)
        for key in (
            "topic_id",
            "kind",
            "assessed_on",
            "score",
            "notes",
            "semantic_assessment",
            "evidence_lesson_id",
        )
    }
    if review_assessment_id(stable_content) != assessment_id:
        raise LearnError("Durable review assessment content does not match its identity")
    parse_evidence_timestamp(record.get("recorded_at"), "review recorded_at")
    assessed_on = dt.date.fromisoformat(str(record.get("assessed_on")))
    if record.get("kind") not in {"score-only", "semantic-review"}:
        raise LearnError("Durable review assessment has an invalid kind")
    if not isinstance(record.get("score"), int) or isinstance(record.get("score"), bool) or record["score"] not in range(4):
        raise LearnError("Durable review assessment has an invalid score")
    semantic = record.get("semantic_assessment")
    if record["kind"] == "score-only":
        if semantic is not None or record.get("evidence_lesson_id") is not None:
            raise LearnError("Score-only review record must not claim semantic evidence")
    else:
        if not isinstance(semantic, dict):
            raise LearnError("Semantic review record has no semantic assessment")
        lesson = load_lesson(config, str(record.get("evidence_lesson_id") or ""))
        if active_topic_id(lesson) != record["topic_id"]:
            raise LearnError("Durable review evidence lesson belongs to a different topic")
        validate_evidence_assessment(
            semantic, lesson["messages"], f"{assessed_on.isoformat()}T23:59:59+00:00"
        )
    return {
        "kind": "review",
        "assessment_id": assessment_id,
        "effective_at": f"{assessed_on.isoformat()}T23:59:59+00:00",
        "assessed_on": assessed_on.isoformat(),
        "score": record["score"],
        "notes": record.get("notes", ""),
        "semantic": semantic,
        "review_kind": record["kind"],
    }


def topic_assessment_effects(config: dict, topic: dict) -> list[dict]:
    effects = []
    identities = set()
    for reference in topic.get("assessment_refs", []):
        if not isinstance(reference, dict):
            raise LearnError("Topic assessment_refs contains a malformed reference")
        if reference.get("kind") == "lesson":
            effect = lesson_assessment_effect(config, reference)
        elif reference.get("kind") == "review":
            effect = review_assessment_effect(config, reference)
        else:
            raise LearnError("Topic assessment reference has an unknown kind")
        if effect["assessment_id"] != reference.get("assessment_id"):
            raise LearnError("Topic assessment reference identity mismatch")
        identity = (effect["kind"], effect["assessment_id"])
        if identity in identities:
            raise LearnError("Topic contains a duplicate assessment reference")
        identities.add(identity)
        effect["sequence"] = int(reference.get("sequence", 0))
        effects.append(effect)
    effects.sort(key=lambda item: (item["effective_at"], item["sequence"], item["assessment_id"]))
    return effects


def apply_review_schedule(review: dict, score: int, assessed_on: str, intervals: list[int]) -> None:
    current = int(review.get("interval_index", 0))
    if score == 0:
        next_index = 0
    elif score == 1:
        next_index = max(0, current - 1)
    else:
        next_index = min(len(intervals) - 1, current + 1)
    review_date = dt.date.fromisoformat(assessed_on)
    review["interval_index"] = next_index
    review["last_review"] = assessed_on
    review["next_review"] = (review_date + dt.timedelta(days=intervals[next_index])).isoformat()


def rebuild_topic_state(config: dict, topic: dict) -> dict:
    ensure_topic_derivation(topic)
    baseline = json_copy(topic["historical_baseline"])
    derived = {
        "state": baseline.get("state", "unseen"),
        **{
            field: json_copy(baseline.get(field, [] if field != "mental_model" else "Not established yet."))
            for field in TOPIC_EVIDENCE_FIELDS
        },
    }
    review = json_copy(
        baseline.get("review", {"interval_index": 0, "last_review": None, "next_review": None, "history": []})
    )
    review.setdefault("history", [])
    sessions = list(topic.get("sessions", []))
    for effect in topic_assessment_effects(config, topic):
        semantic = effect.get("semantic")
        if isinstance(semantic, dict):
            apply_semantic_summary(derived, semantic)
        if effect["kind"] == "lesson":
            lesson = load_lesson(config, effect["lesson_id"])
            session_link = str(Path(lesson["note_relative"]).relative_to(config["learning_folder"]))
            if session_link not in sessions:
                sessions.append(session_link)
            schedule_changed = not review.get("next_review")
            if schedule_changed:
                first_date = dt.date.fromisoformat(effect["assessed_on"])
                review["interval_index"] = 0
                review["last_review"] = effect["assessed_on"]
                review["next_review"] = (
                    first_date + dt.timedelta(days=config["review_intervals"][0])
                ).isoformat()
            review["history"].append(
                {
                    "date": effect["assessed_on"],
                    "score": effect["score"],
                    "kind": "lesson-finish",
                    "assessment_id": effect["assessment_id"],
                    "lesson_id": effect["lesson_id"],
                    "schedule_changed": schedule_changed,
                }
            )
        else:
            apply_review_schedule(review, effect["score"], effect["assessed_on"], config["review_intervals"])
            review["history"].append(
                {
                    "date": effect["assessed_on"],
                    "score": effect["score"],
                    "kind": effect["review_kind"],
                    "assessment_id": effect["assessment_id"],
                    "notes": effect["notes"],
                }
            )
    topic.update(derived)
    topic["review"] = review
    topic["sessions"] = sessions
    topic["updated_at"] = iso_now()
    return topic


def recover_topic_assessment_refs(config: dict, topic: dict) -> None:
    ensure_topic_derivation(topic)
    included = set(topic["historical_baseline"].get("included_assessment_ids", []))
    for path in sorted((system_root(config) / "lessons").glob("*.json")):
        lesson = load_lesson(config, path.stem)
        assessment = lesson.get("final_assessment")
        if active_topic_id(lesson) != topic["slug"] or not isinstance(assessment, dict):
            continue
        if assessment.get("assessment_id") in included:
            continue
        reference = {
            "kind": "lesson",
            "assessment_id": assessment.get("assessment_id"),
            "lesson_id": lesson["lesson_id"],
            "topic_id": topic["slug"],
        }
        lesson_assessment_effect(config, reference)
        append_assessment_ref(topic, reference)
    for path in sorted((system_root(config) / "reviews").glob("*.json")):
        record = read_json(path, None)
        if not record or record.get("topic_id") != topic["slug"]:
            continue
        reference = {
            "kind": "review",
            "assessment_id": record.get("assessment_id"),
            "topic_id": topic["slug"],
        }
        review_assessment_effect(config, reference)
        append_assessment_ref(topic, reference)


def apply_finish_to_topic(config: dict, lesson: dict, payload: dict, assessment_id: str) -> dict:
    topic_id = active_topic_id(lesson)
    topic = topic_record(config, topic_id) or new_topic(lesson["title"], topic_id)
    ensure_topic_derivation(topic)
    reference = {
        "kind": "lesson",
        "assessment_id": assessment_id,
        "lesson_id": lesson["lesson_id"],
        "topic_id": topic_id,
    }
    append_assessment_ref(topic, reference)
    topic["title"] = lesson["title"]
    rebuild_topic_state(config, topic)
    write_json(topic_json_path(config, topic["slug"]), topic)
    return topic


def finish_canonical_locked(
    config: dict, lesson: dict, payload: dict, checkpoint: dict | None = None
) -> dict:
    ensure_session_structure_before_finish(config, lesson)
    session_id = active_session_id(lesson)
    changed = False
    if lesson["status"] == "finishing":
        if lesson["final_assessment"]["payload"] != payload:
            raise LearnError("Conflicting finish: this lesson already has a different final assessment")
        stored_checkpoint = lesson["final_assessment"].get("checkpoint_fingerprint")
        incoming_checkpoint = checkpoint_fingerprint(lesson, checkpoint) if checkpoint else None
        if stored_checkpoint != incoming_checkpoint:
            raise LearnError("Conflicting finish: this lesson already has a different completion checkpoint")
        expected = lesson["expected_finishing"]
    elif lesson["status"] == "active":
        pending = pending_for_session(config, session_id=session_id)
        expected = {"session_id": session_id, "turn_id": pending["turn_id"]}
        assessment_id = finish_assessment_id(lesson["lesson_id"], payload)
        recorded_at = iso_now()
        lesson["final_assessment"] = {
            "assessment_id": assessment_id,
            "recorded_at": recorded_at,
            "payload": payload,
            "checkpoint_fingerprint": checkpoint_fingerprint(lesson, checkpoint) if checkpoint else None,
        }
        lesson["expected_finishing"] = expected
        lesson["status"] = "finishing"
        lesson["finishing_at"] = recorded_at
        lesson["source_citekeys"] = list(payload["source_citekeys"])
        changed = True
    else:
        raise LearnError(f"Cannot finish a lesson in {lesson['status']} state")
    if checkpoint and apply_checkpoint_to_lesson(lesson, checkpoint):
        changed = True
    if changed:
        save_lesson(config, lesson)
    assessment_id = finish_assessment_id(lesson["lesson_id"], payload)
    topic = apply_finish_to_topic(config, lesson, payload, assessment_id)
    try:
        note, _ = render_lesson_note(config, lesson)
        topic_note = render_topic_note(config, topic)
    except (LearnError, OSError) as exc:
        mark_rendering_repair_locked(config, lesson, "finish-render", exc, expected["turn_id"])
        raise SavedRenderingError("Final assessment was saved; rendering needs repair") from exc
    warnings = validate_artifacts(config, session=note, topic_slug=topic["slug"])
    return {
        "status": "ready-to-close",
        "lesson_id": lesson["lesson_id"],
        "topic": topic["slug"],
        "evidence_state": topic["state"],
        "next_review": topic["review"]["next_review"],
        "expected_final": expected,
        "checkpoint_fingerprint": lesson["final_assessment"].get("checkpoint_fingerprint"),
        "session_note": str(note),
        "topic_note": str(topic_note),
        "warnings": warnings,
        "instruction": "Send one concise final message; only its matching Stop will close the lesson.",
    }


def command_finish(args) -> dict:
    config = load_config()
    raw_payload = dict(load_payload(args))
    checkpoint_payload = raw_payload.pop("checkpoint", None)
    if checkpoint_payload is not None and not isinstance(checkpoint_payload, dict):
        raise LearnError("finish checkpoint must be a JSON object")
    payload = add_semantic_defaults(raw_payload)
    validate_finish_payload(payload, config)
    active = find_current_active(config, args.session_id, args.cwd)
    with state_lock(config):
        active = read_active(config, active_session_id(active))
        if not active:
            raise LearnError("Learning session closed while finish was running")
        merged_citekeys = list(
            dict.fromkeys(list(active.get("source_citekeys", [])) + payload["source_citekeys"])
        )
        if merged_citekeys != payload["source_citekeys"]:
            payload["source_citekeys"] = merged_citekeys
            validate_finish_payload(payload, config)
        if active["status"] == "finishing":
            current_turn = active["expected_finishing"]["turn_id"]
            assessed_at = active["final_assessment"]["recorded_at"]
        else:
            current_turn = pending_for_session(config, active_session_id(active))["turn_id"]
            assessed_at = iso_now()
        payload = resolve_evidence_references(payload, active, current_turn)
        validate_finish_payload(payload, config, active["messages"], assessed_at)
        checkpoint = (
            prepare_checkpoint_record(active, checkpoint_payload, current_turn)
            if checkpoint_payload is not None
            else None
        )
        return finish_canonical_locked(config, active, payload, checkpoint)


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
    control_text = without_transcript_content(text)
    for found in re.findall(r"<!--\s*LEARN:([^>]+)\s*-->", control_text):
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
            "SOURCE:BEGIN",
            "SOURCE:END",
        }
        dynamic_transcript = re.fullmatch(
            r"TRANSCRIPT:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:(?:BEGIN|END)",
            token,
        )
        if token not in supported and not dynamic_transcript:
            errors.append(f"{label}: unsupported generated-state marker LEARN:{token}")
    for begin, end in MARKER_PAIRS.values():
        if begin in control_text or end in control_text:
            if (
                control_text.count(begin) != 1
                or control_text.count(end) != 1
                or control_text.find(begin) > control_text.find(end)
            ):
                errors.append(f"{label}: invalid generated-state markers {begin} / {end}")
    dynamic_begin = re.findall(
        r"<!-- LEARN:TRANSCRIPT:([0-9a-f-]{36}):BEGIN -->", control_text
    )
    dynamic_end = re.findall(r"<!-- LEARN:TRANSCRIPT:([0-9a-f-]{36}):END -->", control_text)
    if dynamic_begin or dynamic_end:
        if len(dynamic_begin) != 1 or dynamic_begin != dynamic_end:
            errors.append(f"{label}: invalid lesson-specific transcript markers")
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
        binding = read_json(path, None)
        if not binding:
            continue
        session_id = str(binding.get("session_id") or "")
        try:
            active = read_active(config, session_id)
        except LearnError as exc:
            errors.append(f"{path}: {exc}")
            continue
        if not active:
            errors.append(f"{path}: binding has no active lesson")
            continue
        note = lesson_note_path(config, active)
        if not note.is_file():
            errors.append(f"{path}: active session note is missing")
        if path != active_path(config, session_id):
            errors.append(f"{path}: active state key does not match session_id")

    for path in (system_root(config) / "lessons").glob("*.json"):
        try:
            lesson = load_lesson(config, path.stem)
        except LearnError as exc:
            errors.append(f"{path}: {exc}")
            continue
        if lesson["lesson_id"] != path.stem:
            errors.append(f"{path}: lesson_id does not match its state filename")
        note = lesson_note_path(config, lesson)
        if not note.is_file():
            errors.append(f"{path}: rendered lesson note is missing; run repair --lesson-id {lesson['lesson_id']}")

    for path in (system_root(config) / "reviews").glob("*.json"):
        record = read_json(path, None)
        if not record:
            continue
        try:
            review_assessment_effect(
                config,
                {
                    "kind": "review",
                    "assessment_id": path.stem,
                    "topic_id": record.get("topic_id"),
                },
            )
            review_topic = topic_record(config, str(record.get("topic_id") or ""))
            applied = any(
                isinstance(reference, dict)
                and reference.get("kind") == "review"
                and reference.get("assessment_id") == path.stem
                for reference in (review_topic or {}).get("assessment_refs", [])
            )
            if not applied:
                errors.append(
                    f"{path}: durable review is not applied; run repair --topic {record.get('topic_id')}"
                )
        except (LearnError, ValueError) as exc:
            errors.append(f"{path}: {exc}")

    for path in (system_root(config) / "topics").glob("*.json"):
        topic = read_json(path, None)
        if not topic:
            continue
        if topic.get("state") not in TOPIC_STATES:
            errors.append(f"{path}: invalid topic state {topic.get('state')}")
        if topic.get("slug") != path.stem:
            errors.append(f"{path}: topic slug does not match its state filename")
        try:
            rebuilt = rebuild_topic_state(config, json_copy(topic))
            compared_fields = ("state", *TOPIC_EVIDENCE_FIELDS, "review", "sessions")
            if any(rebuilt.get(field) != topic.get(field) for field in compared_fields):
                errors.append(f"{path}: derived topic state is stale; run repair --topic {path.stem}")
        except (LearnError, ValueError) as exc:
            errors.append(f"{path}: {exc}")
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


def review_assessment_id(content: dict) -> str:
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"review\0{encoded}".encode("utf-8")).hexdigest()


def prepare_review_semantic_payload(args, config: dict, topic: dict) -> tuple[dict | None, str | None]:
    if not args.json and not args.json_file:
        return None, None
    payload = add_semantic_defaults(dict(load_payload(args)))
    missing = sorted(SEMANTIC_REVIEW_KEYS - set(payload))
    extra = sorted(set(payload) - SEMANTIC_REVIEW_KEYS)
    if missing:
        raise LearnError("Review assessment is missing: " + ", ".join(missing))
    if extra:
        raise LearnError("Review assessment has unsupported keys: " + ", ".join(extra))
    validate_semantic_evidence_shape(payload)
    if args.lesson_id:
        lesson = load_lesson(config, args.lesson_id)
    elif args.session_id:
        lesson = find_current_active(config, args.session_id)
        if not lesson.get("lesson_id"):
            raise LearnError("Semantic review evidence requires a canonical lesson")
    else:
        raise LearnError("Semantic review evidence requires --lesson-id or --session-id")
    if active_topic_id(lesson) != topic["slug"]:
        raise LearnError("Review evidence lesson belongs to a different topic")
    current_turn = None
    if args.session_id and lesson["status"] == "active":
        current_turn = pending_for_session(config, active_session_id(lesson))["turn_id"]
    payload = resolve_evidence_references(payload, lesson, current_turn)
    assessed_at = f"{args.on_date or today().isoformat()}T23:59:59+00:00"
    validate_evidence_assessment(payload, lesson["messages"], assessed_at)
    return payload, lesson["lesson_id"]


def command_review(args) -> dict:
    config = load_config()
    review_date = dt.date.fromisoformat(args.on_date) if args.on_date else today()
    with state_lock(config):
        topic = topic_record(config, args.topic)
        if not topic:
            raise LearnError(f"Unknown topic: {args.topic}")
        ensure_topic_derivation(topic)
        baseline_last = topic["historical_baseline"].get("review", {}).get("last_review")
        if baseline_last and review_date < dt.date.fromisoformat(str(baseline_last)):
            raise LearnError("Review date predates the preserved historical review baseline")
        semantic, lesson_id = prepare_review_semantic_payload(args, config, topic)
        stable_content = {
            "topic_id": topic["slug"],
            "kind": "semantic-review" if semantic else "score-only",
            "assessed_on": review_date.isoformat(),
            "score": args.score,
            "notes": args.notes or "",
            "semantic_assessment": semantic,
            "evidence_lesson_id": lesson_id,
        }
        assessment_id = review_assessment_id(stable_content)
        record = {
            "schema_version": REVIEW_SCHEMA_VERSION,
            "assessment_id": assessment_id,
            **stable_content,
            "recorded_at": iso_now(),
        }
        path = review_json_path(config, assessment_id)
        existing = read_json(path, None)
        replayed = existing is not None
        if existing:
            comparable = {key: existing.get(key) for key in stable_content}
            if comparable != stable_content:
                raise LearnError("Conflicting review assessment identity; preserved the existing record")
        else:
            write_json(path, record)  # Durable evidence precedes all derived topic updates.

        reference = {
            "kind": "review",
            "assessment_id": assessment_id,
            "topic_id": topic["slug"],
        }
        append_assessment_ref(topic, reference)
        rebuild_topic_state(config, topic)
        write_json(topic_json_path(config, topic["slug"]), topic)
        try:
            note = render_topic_note(config, topic)
        except (LearnError, OSError) as exc:
            mark_topic_rendering_repair_locked(config, topic, "review-render", exc)
            raise SavedRenderingError("Review assessment was saved; rendering needs repair") from exc
    return {
        "status": "replayed" if replayed else "recorded",
        "assessment_id": assessment_id,
        "assessment_kind": stable_content["kind"],
        "topic": topic["slug"],
        "state": topic["state"],
        "score": args.score,
        "next_review": topic["review"]["next_review"],
        "note": str(note),
    }


def command_status(args) -> dict:
    config = load_config()
    active = []
    for path in sorted((system_root(config) / "active").glob("*.json")):
        binding = read_json(path)
        active.append(read_active(config, str(binding["session_id"])))
    topics = list((system_root(config) / "topics").glob("*.json"))
    sources = list((system_root(config) / "sources").glob("*.json"))
    reviews = list((system_root(config) / "reviews").glob("*.json"))
    lesson_paths = list((system_root(config) / "lessons").glob("*.json"))
    lessons = [load_lesson(config, path.stem) for path in sorted(lesson_paths)]
    return {
        "vault": config["vault_path"],
        "learning_folder": config["learning_folder"],
        "active_sessions": [
            {
                "session_id": active_session_id(item),
                "lesson_id": item["lesson_id"],
                "title": item["title"],
                "mode": item["mode"],
                "note": item["note_relative"],
            }
            for item in active
        ],
        "topic_count": len(topics),
        "source_count": len(sources),
        "review_assessment_count": len(reviews),
        "lesson_count": len(lessons),
        "unfinished_lessons": [
            {
                "lesson_id": lesson["lesson_id"],
                "status": lesson["status"],
                "session_id": active_session_id(lesson),
                "expected_final": lesson.get("expected_finishing"),
                "missing_final_response": lesson["status"] == "finishing",
                "note": lesson["note_relative"],
            }
            for lesson in lessons
            if lesson["status"] in {"active", "paused", "finishing"}
        ],
        "due_count": len(command_due(argparse.Namespace(on_date=None))),
        "operational_health": read_operational_state(config),
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
    retained = {"active": 0, "pending": 0}

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
            try:
                active = read_active(config, session_id)
            except LearnError as exc:
                warnings.append(str(exc) + "; preserved for manual inspection")
                retained["active"] += 1
                continue
            if not active:
                warnings.append(f"{relative(path)} has no resolvable lesson; preserved for manual inspection")
                retained["active"] += 1
                continue
            note = lesson_note_path(config, active)
            if not note.is_file():
                warnings.append(f"{relative(path)} needs rendering repair; canonical lesson preserved")
                live_session_ids.add(session_id)
                retained["active"] += 1
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
        "durable_state_preserved": [
            "lessons",
            "topics",
            "sources",
            "review assessments",
            "session notes",
            "topic notes",
            "source notes",
            "assets",
        ],
    }


def command_abort(args) -> dict:
    config = load_config()
    active = find_current_active(config, args.session_id, args.cwd)
    with state_lock(config):
        session_id = active_session_id(active)
        active = read_active(config, session_id)
        if not active:
            raise LearnError("Learning session closed while abort was running")
        active["status"] = "aborted"
        active["aborted_at"] = iso_now()
        active["conversation_binding"]["detached_at"] = active["aborted_at"]
        save_lesson(config, active)
        active_path(config, session_id).unlink(missing_ok=True)
        try:
            note, _ = render_lesson_note(config, active)
        except (LearnError, OSError) as exc:
            mark_rendering_repair_locked(config, active, "abort-render", exc)
            raise SavedRenderingError("Lesson was aborted; rendering needs repair") from exc
    return {"status": "aborted", "session_id": session_id, "lesson_id": active["lesson_id"], "note": str(note)}


def command_pause(args) -> dict:
    config = load_config()
    active = find_current_active(config, args.session_id, args.cwd)
    with state_lock(config):
        session_id = active_session_id(active)
        lesson = read_active(config, session_id)
        if not lesson:
            raise LearnError("Learning session closed while pause was running")
        if lesson["status"] != "active":
            raise LearnError(f"Cannot pause a lesson in {lesson['status']} state")
        paused_at = iso_now()
        lesson["status"] = "paused"
        lesson["paused_at"] = paused_at
        lesson["conversation_binding"]["detached_at"] = paused_at
        save_lesson(config, lesson)
        active_path(config, session_id).unlink(missing_ok=True)
        try:
            note, _ = render_lesson_note(config, lesson)
        except (LearnError, OSError) as exc:
            mark_rendering_repair_locked(config, lesson, "pause-render", exc)
            raise SavedRenderingError("Lesson was paused; rendering needs repair") from exc
    return {"status": "paused", "lesson_id": lesson["lesson_id"], "session_id": session_id, "note": str(note)}


def attached_session_for_lesson(config: dict, lesson_id: str) -> str | None:
    attached = []
    for path in (system_root(config) / "active").glob("*.json"):
        record = read_json(path, None)
        if record and record.get("lesson_id") == lesson_id:
            attached.append(str(record.get("session_id") or ""))
    if len(attached) > 1:
        raise LearnError(f"Lesson {lesson_id} has multiple conversation bindings")
    return attached[0] if attached else None


def command_resume(args) -> dict:
    config = load_config()
    target_session = exact_runtime_session_id(args.session_id)
    with state_lock(config):
        lesson = load_lesson(config, args.lesson_id)
        if lesson["status"] in {"completed", "aborted"}:
            raise LearnError(f"Cannot resume a {lesson['status']} lesson")
        if lesson["status"] == "finishing":
            expected = lesson.get("expected_finishing") or {}
            raise LearnError(
                "Finishing lesson still awaits its original final Stop "
                f"for session {expected.get('session_id')} turn {expected.get('turn_id')}"
            )
        target_binding = binding_record(config, target_session)
        if target_binding and target_binding.get("lesson_id") != lesson["lesson_id"]:
            raise LearnError(f"Conversation {target_session} is already attached to another lesson")
        attached = attached_session_for_lesson(config, lesson["lesson_id"])
        if attached and attached != target_session:
            raise LearnError(
                f"Lesson {lesson['lesson_id']} is still attached to conversation {attached}; pause it before rebinding"
            )
        current_session = active_session_id(lesson)
        if lesson["status"] == "active" and current_session != target_session:
            raise LearnError("An active lesson cannot be rebound without an explicit pause")
        if lesson["status"] == "paused":
            prior = dict(lesson["conversation_binding"])
            lesson.setdefault("binding_history", []).append(prior)
            attached_at = iso_now()
            lesson["conversation_binding"] = {"session_id": target_session, "attached_at": attached_at}
            lesson["status"] = "active"
            lesson["resumed_at"] = attached_at
            save_lesson(config, lesson)
        bind_lesson(config, lesson)
        try:
            note, _ = render_lesson_note(config, lesson)
        except (LearnError, OSError) as exc:
            mark_rendering_repair_locked(config, lesson, "resume-render", exc)
            raise SavedRenderingError("Lesson was resumed; rendering needs repair") from exc
    return {
        "status": "resumed",
        "lesson_id": lesson["lesson_id"],
        "session_id": target_session,
        "note": str(note),
        "recovery": recovery_context(config, lesson),
    }


def command_repair(args) -> dict:
    config = load_config()
    with state_lock(config):
        if args.topic:
            lessons = []
            topic_ids = {args.topic}
        elif args.lesson_id:
            lessons = [load_lesson(config, args.lesson_id)]
            topic_ids = {active_topic_id(lessons[0])}
        elif args.all:
            lessons = [load_lesson(config, path.stem) for path in sorted((system_root(config) / "lessons").glob("*.json"))]
            topic_ids = {path.stem for path in (system_root(config) / "topics").glob("*.json")}
        else:
            session_id = exact_runtime_session_id(args.session_id)
            active = read_active(config, session_id)
            if not active:
                raise LearnError(f"No canonical lesson is attached to conversation {session_id}")
            lessons = [active]
            topic_ids = {active_topic_id(active)}
        repaired_lessons = []
        repaired_topics = []
        failures = []
        health = read_operational_state(config)
        for lesson in lessons:
            try:
                if lesson["status"] in {"active", "finishing"}:
                    bind_lesson(config, lesson)
                note, _ = render_lesson_note(config, lesson)
                relative = lesson["note_relative"]
                health["pending_rendering_repairs"] = [
                    item for item in health["pending_rendering_repairs"] if item != relative
                ]
                repaired_lessons.append(
                    {
                        "lesson_id": lesson["lesson_id"],
                        "note": str(note),
                        "revision": lesson["revision"],
                    }
                )
            except (LearnError, OSError) as exc:
                relative = str(lesson.get("note_relative") or "")
                if relative and relative not in health["pending_rendering_repairs"]:
                    health["pending_rendering_repairs"].append(relative)
                failures.append(
                    {"kind": "lesson", "lesson_id": lesson["lesson_id"], "error_type": type(exc).__name__}
                )
        for topic_id in sorted(topic_ids):
            relative = (Path(config["learning_folder"]) / "Topics" / f"{topic_id}.md").as_posix()
            try:
                topic = topic_record(config, topic_id)
                if not topic:
                    raise LearnError(f"Unknown topic: {topic_id}")
                recover_topic_assessment_refs(config, topic)
                rebuild_topic_state(config, topic)
                write_json(topic_json_path(config, topic["slug"]), topic)
                note = render_topic_note(config, topic)
                health["pending_rendering_repairs"] = [
                    item for item in health["pending_rendering_repairs"] if item != relative
                ]
                repaired_topics.append({"topic": topic_id, "note": str(note)})
            except (LearnError, OSError) as exc:
                if relative not in health["pending_rendering_repairs"]:
                    health["pending_rendering_repairs"].append(relative)
                failures.append({"kind": "topic", "topic": topic_id, "error_type": type(exc).__name__})
        if failures:
            health["last_error"] = {
                "at": iso_now(),
                "operation": "repair-render",
                "error_type": failures[0]["error_type"],
                "event": None,
                "session_id": None,
                "turn_id": None,
            }
        write_json(operational_path(config), health)
    return {
        "status": "repaired" if not failures else "partial",
        "repaired": repaired_lessons,
        "repaired_topics": repaired_topics,
        "failures": failures,
    }


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
        try:
            health = read_operational_state(config)
            health_ok = health.get("schema_version") == 1
            health_detail = (
                f"{len(health.get('pending_rendering_repairs', []))} pending rendering repair(s)"
            )
        except LearnError as exc:
            health_ok = False
            health_detail = str(exc)
        checks.append({"name": "operational-health", "ok": health_ok, "detail": health_detail})
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
    context.add_argument("--question-id")
    context.add_argument("--choices", type=int, choices=range(2, 7), default=4)
    context.add_argument("--correct-count", type=int, choices=range(1, 7), default=1)
    context.add_argument("--limit", type=int, default=DEFAULT_CONTEXT_MESSAGES)
    context_start = context.add_mutually_exclusive_group()
    context_start.add_argument("--message-id")
    context_start.add_argument("--from-message")
    context.add_argument("--to-message")

    checkpoint = sub.add_parser("checkpoint", help="save a compact semantic recovery checkpoint")
    checkpoint_payload = checkpoint.add_mutually_exclusive_group(required=True)
    checkpoint_payload.add_argument("--json")
    checkpoint_payload.add_argument("--json-file")
    checkpoint.add_argument("--cwd")
    checkpoint.add_argument("--session-id")

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
    review_evidence = review.add_mutually_exclusive_group()
    review_evidence.add_argument("--json", help="optional semantic review assessment")
    review_evidence.add_argument("--json-file", help="optional semantic review assessment file")
    review_binding = review.add_mutually_exclusive_group()
    review_binding.add_argument("--lesson-id")
    review_binding.add_argument("--session-id")

    status = sub.add_parser("status", help="show active and durable state")
    status.add_argument("--json-output", action="store_true")

    cleanup = sub.add_parser("cleanup", help="prune disposable hook state without deleting learning notes")
    cleanup.add_argument("--apply", action="store_true", help="remove candidates; the default is a dry run")
    cleanup.add_argument("--pending-days", type=int, default=DEFAULT_PENDING_RETENTION_DAYS)
    cleanup.add_argument("--json-output", action="store_true", help="show the complete candidate list as JSON")

    abort = sub.add_parser("abort", help="abandon one active lesson without deleting notes")
    abort.add_argument("--cwd")
    abort.add_argument("--session-id")

    pause = sub.add_parser("pause", help="pause and detach one active canonical lesson")
    pause.add_argument("--cwd")
    pause.add_argument("--session-id")

    resume = sub.add_parser("resume", help="attach an explicitly selected unfinished lesson")
    resume.add_argument("--lesson-id", required=True)
    resume.add_argument("--session-id")

    repair = sub.add_parser("repair", help="regenerate lesson Markdown from canonical lesson records")
    repair_selector = repair.add_mutually_exclusive_group(required=True)
    repair_selector.add_argument("--lesson-id")
    repair_selector.add_argument("--session-id")
    repair_selector.add_argument("--topic")
    repair_selector.add_argument("--all", action="store_true")
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
        data = {}
        try:
            raw = sys.stdin.read()
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict):
                run_hook(data)
        except Exception as exc:
            try:
                config = load_config(required=False)
                if config and isinstance(data, dict):
                    record_operational_error(
                        config,
                        data,
                        "hook-capture",
                        exc,
                        needs_rendering_repair=not isinstance(exc, EventConflictError),
                    )
            except Exception:
                pass
        return 0
    commands = {
        "configure": command_configure,
        "doctor": command_doctor,
        "start": command_start,
        "context": command_context,
        "checkpoint": command_checkpoint,
        "open": command_open,
        "source": command_source,
        "validate": command_validate,
        "finish": command_finish,
        "due": command_due,
        "review": command_review,
        "status": command_status,
        "cleanup": command_cleanup,
        "abort": command_abort,
        "pause": command_pause,
        "resume": command_resume,
        "repair": command_repair,
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
