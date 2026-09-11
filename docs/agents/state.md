# State invariants

Read before changing persistence, hooks, identity, lifecycle, or cleanup. Consult [State and CLI schemas](../../.agents/skills/learn/references/state-schemas.md) for payloads and detailed behavior.

## Ownership and identity

- Canonical lesson JSON owns messages and lesson state. Session Markdown is a rebuildable view; topic Markdown is derived from durable baselines and lesson/review assessments. Do not introduce another authoritative copy or per-event deduplication files.
- Keep `lesson_id` (immutable lesson UUID), `session_id` (exact conversation attachment), and `turn_id` (runtime turn) distinct. Never infer identity from a working directory, filename suffix, or another active conversation.

## Persistence and lifecycle

- Use the existing locking and atomic-write paths. Commit canonical data before rendering or GUI work. A failed render must leave durable data repairable; an identical replay must not duplicate a message, assessment, scheduling effect, or GUI action. Conflicting event content must not overwrite the original.
- Preserve lifecycle boundaries: paused lessons are detached; resume selects an explicit lesson and cannot steal an attachment; finishing accepts only the recorded final conversation/turn. Read [lifecycle tests](../../tests/test_lifecycle.py) and [recovery tests](../../tests/test_recovery.py) before modifying these paths.

## Transcript and user data

- Preserve raw user and final assistant Markdown. Hooks make no model calls and return no transcript to model context. Recovery returns bounded context; tool traces and internal messages do not enter the lesson log.
- Preserve personal prose outside managed note regions and unknown frontmatter properties. Cleanup defaults to preview and removes only disposable state, protecting live pending records and durable learning data. A missing derived note requires repair, not deletion of the lesson or binding.
