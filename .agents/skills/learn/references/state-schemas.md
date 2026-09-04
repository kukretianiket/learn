# State and CLI schemas

All commands use the single `learnctl.py` utility. JSON can be passed with `--json`; use `--json-file` for payloads that are awkward to quote. Hooks alone read JSON from stdin.

## Finish payload

All keys are required:

```json
{
  "final_mental_model": "Concise connected model",
  "dependencies": ["foundation", "derived idea"],
  "demonstrated_abilities": [
    {"ability": "...", "evidence_type": "recall", "independent": true, "delayed": false}
  ],
  "hints_required": ["structural hint on ..."],
  "misconceptions": ["old model → repaired model"],
  "unresolved_gaps": ["..."],
  "retrieval_prompts": ["Explain ... without notes"],
  "evidence_state": "retrievable",
  "source_citekeys": ["author2024topic"],
  "transfer_task_result": {
    "attempted": false,
    "successful": false,
    "independent": false,
    "delayed": false,
    "summary": "Not attempted"
  },
  "suggested_review_score": 2
}
```

## Source payload

During a lesson, attach the source explicitly:

```bash
learnctl.py source --session-id SESSION_ID --json '<payload>'
```

With `--session-id`, the command validates that the lesson is active, adds the source wikilink to the live session note, and records the citekey in active state. A deduplicated source is attached under its existing citekey. `finish` preserves explicitly attached citekeys even if the supplied finish payload omits one. Without `--session-id` or `--cwd`, `source` only adds to the global source library.

```json
{
  "citekey": "org2026guide",
  "title": "Guide title",
  "author_or_organization": "Organization",
  "year_or_date": "2026",
  "url": "https://example.org/guide",
  "doi": "",
  "local_path": "",
  "source_type": "official documentation",
  "verification_status": "verified",
  "retrieval_date": "2026-09-03",
  "precise_locator": "Section 2, paragraph 3",
  "claims_supported": ["Exact claim used in the lesson"]
}
```

## Useful commands

```bash
learnctl.py context [--session-id ID]
learnctl.py due [--json-output]
learnctl.py review --topic SLUG --score 0..3 [--notes TEXT]
learnctl.py status [--json-output]
learnctl.py cleanup [--pending-days DAYS] [--apply]
learnctl.py validate [--session PATH] [--topic SLUG]
learnctl.py abort [--session-id ID]
```

## Session identity and cleanup

The exact Codex `session_id` from hook input is the unique key used for an active learning session and for commands such as `context`, `finish`, and `abort`. The active-state filename is a filesystem-safe token derived from it, and the short hash in a session-note filename is only a convenience suffix; neither replaces the full `session_id`. New session notes store the full identifier in frontmatter. If one Codex conversation runs multiple lessons sequentially, those lesson records share a `session_id`; their session-note paths distinguish the durable records.

`cleanup` defaults to a dry run. It protects active sessions and their pending and event-deduplication records. With `--apply`, it removes inactive event records, inactive pending records older than seven days by default, pending records associated with orphaned active state, active records whose note is missing or already completed/aborted, invalid disposable hook records, and Finder metadata. `--pending-days` changes only the retention period for inactive pending prompts. Add `--json-output` to inspect every candidate path. Cleanup never removes session notes, rendered notes, assets, or `_system/topics` and `_system/sources`.

## Window layout configuration

`window_layout` is `desktop-split` or `none`; `codex_side` is `left` or `right`. On macOS, `learnctl open` opens the active note and applies the configured 50/50 desktop layout. The application hosting Codex must have macOS Accessibility permission. Layout failure never prevents direct lesson-note writes.

State JSON lives under `Learning/_system`; rendered topic/source/session Markdown lives under their corresponding Learning folders. Do not hand-edit generated JSON. Personal prose outside the managed topic markers, especially `## My notes`, is preserved.
