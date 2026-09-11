# State and CLI schemas

All commands use the single `learnctl.py` utility. JSON can be passed with `--json`; use `--json-file` for payloads that are awkward to quote. Hooks alone read JSON from stdin.

`start` and `resume` return `instruction_bundle` before teaching. It contains `pedagogy.md`, `rendering.md`, `source-policy.md`, `evidence-model.md`, and `state-schemas.md` in that fixed order, with their complete current contents, per-file SHA-256 hashes, and one bundle fingerprint. Bundle construction fails before lesson mutation if any file is missing or unreadable. The bundle is context-scoped: reuse it during an uninterrupted conversation, and load it again after recovery when the earlier instructions are no longer in context. No durable state claims that a model read it.

## Semantic checkpoint

Save a checkpoint only after diagnosis establishes the route, after a meaningful route change, or at completion. Every field is required; `outstanding_question` may be `null`, each list is limited to 12 items, and the whole checkpoint is limited to 16 KiB:

Use `next_teaching_step` for the specific relationship to establish and why the learner is ready for it. If a question is needed, name the conceptual uncertainty it will resolve; otherwise record the explanation to give next and leave `outstanding_question` as `null`. Do not fill either field with a mechanical exercise merely to keep a question pending. Follow the pedagogy reference when selecting retrieval prompts and the evidence reference when describing demonstrated abilities; the JSON schema cannot determine their conceptual relevance.

```json
{
  "phase": "teaching",
  "goal_and_route": "Establish the foundation, then transfer it.",
  "relevant_concepts": ["foundation", "dependency"],
  "demonstrated_understanding": ["Explained the foundation"],
  "misconceptions": [],
  "unknowns": ["Independent transfer"],
  "evidence_refs": ["current"],
  "hints_and_independence": ["One structural hint used"],
  "outstanding_question": "Can the learner transfer it?",
  "next_teaching_step": "Ask for a changed-case application.",
  "covered_through": "current"
}
```

Run `learnctl.py checkpoint --json '<payload>'`. `current`, `turn:<exact turn_id>`, and exact message IDs follow the evidence-selector rules. Python resolves them to canonical message IDs, refuses backward checkpoint coverage, and stores only the latest semantic checkpoint.

`start` for an attached lesson and explicit `resume` return `recovery` containing session metadata, this checkpoint, a compact topic summary, and at most eight messages after the checkpoint by default. The complete instruction bundle is returned beside, not inside, bounded lesson history. `history.more_history_exists` and `omitted_before_count` disclose truncation. Retrieve more only as needed:

```bash
learnctl.py context --message-id MESSAGE_ID
learnctl.py context --from-message MESSAGE_ID [--to-message MESSAGE_ID] [--limit 1..50]
```

Routine hook capture never returns transcript data. The unbounded lesson JSON and private MCQ issuance state are not context responses.

## Finish payload

All keys are required:

```json
{
  "final_mental_model": "Concise connected model",
  "dependencies": ["foundation", "derived idea"],
  "demonstrated_abilities": [
    {
      "ability": "...",
      "evidence_type": "recall",
      "independent": true,
      "delayed": false,
      "evidence_refs": ["current"]
    }
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
    "summary": "Not attempted",
    "evidence_refs": []
  },
  "suggested_review_score": 2,
  "reassessment": false,
  "contrary_evidence_refs": [],
  "checkpoint": {
    "phase": "completion",
    "goal_and_route": "Completed the selected route.",
    "relevant_concepts": ["foundation", "dependency"],
    "demonstrated_understanding": ["Reconstructed the dependency path"],
    "misconceptions": [],
    "unknowns": [],
    "evidence_refs": ["current"],
    "hints_and_independence": ["Independent final retrieval"],
    "outstanding_question": null,
    "next_teaching_step": "Use the scheduled delayed review.",
    "covered_through": "current"
  }
}
```

The assessment keys are required. `checkpoint` is the only optional top-level key and, when present, is committed atomically with the completion assessment. `evidence_refs` use `current`, `turn:<exact turn_id>`, or an exact canonical learner `message_id`; they are stored as resolved message IDs. Set `reassessment` only for an explicit contrary assessment and provide the learner responses supporting the downgrade.

## Review assessments

The compatible score-only form records scheduling evidence honestly and cannot change mastery:

```bash
learnctl.py review --topic SLUG --score 0..3 [--notes TEXT] [--on-date YYYY-MM-DD]
```

For a semantic review, add `--session-id` or `--lesson-id` and `--json` with exactly these fields:

```json
{
  "evidence_state": "retrievable",
  "demonstrated_abilities": [
    {
      "ability": "Reconstructed the model unaided",
      "evidence_type": "recall",
      "independent": true,
      "delayed": true,
      "evidence_refs": ["current"]
    }
  ],
  "transfer_task_result": {
    "attempted": false,
    "successful": false,
    "independent": false,
    "delayed": false,
    "summary": "Not attempted",
    "evidence_refs": []
  },
  "misconceptions": [],
  "unresolved_gaps": [],
  "reassessment": false,
  "contrary_evidence_refs": []
}
```

Each review is first stored under `_system/reviews/<assessment_id>.json`. Identical replay has the same Python-generated identifier and cannot advance the schedule twice.

## Source payload

During a lesson, attach the source explicitly:

```bash
learnctl.py source --session-id SESSION_ID --json '<payload>'
```

With `--session-id`, the command validates the exact conversation binding, adds the source wikilink to the live session note, and records the usage in the canonical lesson. A deduplicated source is attached under its existing citekey. `finish` preserves explicitly attached citekeys even if the supplied finish payload omits one. Without `--session-id`, `source` only adds to the global source library.

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
learnctl.py context [--session-id ID] [--limit 1..50]
learnctl.py checkpoint --json '<payload>' [--session-id ID]
learnctl.py due [--json-output]
learnctl.py review --topic SLUG --score 0..3 [--notes TEXT]
learnctl.py status [--json-output]
learnctl.py cleanup [--pending-days DAYS] [--apply]
learnctl.py validate [--session PATH] [--topic SLUG]
learnctl.py abort [--session-id ID]
learnctl.py pause [--session-id ID]
learnctl.py resume (--lesson-id UUID | --topic TITLE_OR_SLUG) [--session-id ID]
learnctl.py open [--session-id ID]
learnctl.py layout [--session-id ID]
learnctl.py repair (--lesson-id UUID | --session-id ID | --topic SLUG | --all)
```

## Session identity and cleanup

Each lesson has an immutable, locally generated UUID `lesson_id`. Its authoritative record is `Learning/_system/lessons/<lesson_id>.json`. The exact Codex `session_id` from hook input is a separate conversation identifier used for attachment and for commands such as `context`, `finish`, and `abort`; the runtime-provided `turn_id` identifies a turn. New session notes store both the lesson and conversation identifiers in frontmatter. A small record in `_system/active` maps one exact conversation to one lesson and contains no duplicate lesson state. A working directory is never used as conversation identity.

Messages retain their original Markdown and event identity in the canonical lesson. Hook capture atomically updates that record before regenerating the session Markdown. Replaying identical hook input is a no-op for canonical state but still repairs stale Markdown; conflicting content for the same event identity is retained as an operational error without overwriting the original. `repair` explicitly regenerates lesson Markdown without returning transcript content to the model.

Lifecycle states are `active`, `paused`, `finishing`, `completed`, and `aborted`. `pause` detaches an active canonical lesson. `resume` accepts an explicit unfinished `lesson_id`, or a topic only when exactly one paused lesson matches; ambiguous topic matches require a lesson ID. It refuses to steal another conversation's attachment and records the previous conversation binding. `start` refuses to create a same-topic lesson while any paused lesson for that topic exists. `finish` stores one immutable assessment and the exact expected final `session_id` and `turn_id`; only that Stop can capture the final assistant response and complete the lesson. Status reports finishing lessons whose expected final response is still missing. Completed and aborted records remain durable but detached.

Topic metadata includes a preserved historical baseline and ordered references to canonical lesson and review assessments. Mastery summaries, abilities, gaps, and review scheduling are rebuilt from those durable inputs. A later lesson merges rather than replaces prior evidence, and it initializes a schedule only when none is already established.

`cleanup` defaults to a dry run. It protects active bindings, canonical lessons, and their pending records. A missing derived session note is a repair condition, not grounds to delete its binding. `--pending-days` changes only the retention period for inactive pending prompts. Add `--json-output` to inspect every candidate path. Cleanup never removes canonical lessons, session notes, rendered notes, assets, or topic/source state.

## Obsidian delivery and optional window layout

The official Obsidian CLI is the primary integration. `doctor` reports whether the installed CLI exposes the required `vault`, `open`, and `eval` capabilities. Opening uses bounded readiness checks and the sequence `launch → select exact configured vault → open exact vault-relative path → set and verify Reading View → verify the requested stable message anchor`. CLI `path=` always remains an exact file path; navigation is a separate application-side operation.

Each Tutor message has a stable anchor derived from its canonical `message_id`; the timestamp remains only a display label. “Follow latest” means that anchor at the start of the newest Tutor response. Application-side verification waits for the exact pane, preview mode, the anchor, and settled images or diagrams before confirming visibility. A URI may request launch when the CLI path fails, but remains `unverified` until application state can be read back.

Operational health records `latest_requested_message`, `last_successfully_displayed_message`, `pending_display`, `failed_stage`, `diagnostic`, and the latest structural response diagnostics. Display requests are serialized and an older request cannot supersede a newer queued message. A failed request receives bounded retries inside the adapter and remains pending for the next relevant hook or explicit `open`. Duplicate hook capture repairs Markdown and retries pending delivery without duplicating canonical content.

`window_layout` is `desktop-split` or `none`; `codex_side` is `left` or `right`. Opening never applies layout. `learnctl layout --session-id ...` is the separate optional macOS action and may require Accessibility permission for the application hosting Codex. Layout failure never prevents opening or direct lesson-note writes.

State JSON lives under `Learning/_system`; rendered topic/source/session Markdown lives under their corresponding Learning folders. Do not hand-edit generated JSON. Personal prose outside managed regions and unknown frontmatter properties are preserved.
