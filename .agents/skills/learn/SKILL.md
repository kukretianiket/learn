---
name: learn
description: Run an explicit, stateful tutoring session in learn, explain, solve, review, or research mode while maintaining a live Obsidian lesson and evidence-based topic state. Invoke only as $learn.
---

# Learn

Teach for connected understanding: establish secure foundations, make dependency edges explicit, and motivate each step by answering “How could I have discovered this?”

This skill is explicit-only. Start only for a leading `$learn` token or an IDE selection of this local `SKILL.md`. Quoted examples and later mentions do not start a lesson.

## Load the complete contract before teaching

On the first turn, route explicit resume intent before starting anything new. If the learner asks to resume or continue a prior lesson, run `resume --lesson-id "<lesson_id>"` when an ID is supplied; otherwise run `resume --topic "<topic>"`. Topic selection succeeds only when exactly one paused lesson matches. For a new lesson, run:

```bash
python3 ~/.agents/skills/learn/scripts/learnctl.py start --title "<topic>" --goal "<goal>" --mode <learn|explain|solve|review|research> [--time-budget "<budget>"]
```

Before the first teaching response, read every file in the returned `instruction_bundle`, in its returned order. The bundle contains all five reference files, filenames, per-file hashes, and one version fingerprint generated from their current bytes. The fingerprint records what was supplied; it does not prove comprehension. Do not begin diagnostics or teaching if the complete bundle was not returned.

Reuse that loaded bundle throughout an uninterrupted conversation. Do not request it after each learner answer and do not store or trust a persistent “already read” flag. If recovery occurs after these instructions have left context, run `start` for the attached conversation or `resume` with an explicit lesson or unambiguous topic selector, then load the newly returned complete bundle before teaching again.

Use the returned exact `lesson_id`, `session_id`, lesson status, topic context, and bounded recovery. Treat the returned `status` as authoritative: `started` means a new lesson and note were created; `resumed` means an existing lesson and note were attached or recovered. Finding `topic_context` is not a resumed lesson. A `finishing` lesson awaits its recorded final Stop and must not resume teaching. A paused lesson resumes only through the explicit `resume` command; a topic may select it only when exactly one paused lesson matches, and an explicit lesson ID is required when matches are ambiguous. Resume cannot steal another live attachment. If `start` reports `No pending prompt for Codex session ...`, report that exact limitation; do not invent identity or state.

The reference bundle owns the detailed contracts:

- `pedagogy.md`: modes, diagnosis, questions, teaching progression, and checkpoints.
- `rendering.md`: Markdown, visuals, MathJax notation, and mathematical review.
- `source-policy.md`: research, factual verification, attribution, and checked examples.
- `evidence-model.md`: evidence strength and permitted mastery transitions.
- `state-schemas.md`: CLI payloads, identity, lifecycle, recovery, delivery health, and repair.

## Mandatory pre-send pass

Before every teaching response, perform three separate checks and repair the draft before sending:

1. **Language quality:** read the ordinary prose once for complete sentences, meaningful headings, spelling, accidental repetition, and consistent terminology. Catch fragments such as “What Flynn…” and duplicates such as “force force.” Preserve valid technical terms rather than “correcting” unfamiliar vocabulary by guesswork.
2. **Mathematical notation:** apply the rendering contract to every math span, then review whether each rendered symbol and expression means what the explanation claims. Renderable syntax is not proof of correct mathematics.
3. **Factual accuracy:** apply the source policy, inspect consequential claims and sources, state assumptions, and check newly derived examples. Good spelling is not evidence of truth; poor spelling prompts review but does not identify which scientific claims are false.

This preventive pass uses the current draft; do not make another model call. Stop-hook diagnostics occur only after text is visible and cannot prevent an already displayed defect. They preserve the raw canonical response and report concise correction diagnostics; never silently rewrite or discard it.

## Operate the lesson

Follow the five loaded references for teaching and state commands. In an uninterrupted lesson, use conversation context; do not run `context` or save a checkpoint every turn. Save a semantic checkpoint only after diagnosis establishes the route, after a meaningful route change, or at completion. Retrieve only bounded recovery slices when needed.

Before every MCQ, draft unlabeled substantive choices and request immutable OS-randomized positions with `context --next-mcq --choices <2-6> --correct-count <1-N> --question-id <stable-id>`. Keep issuance metadata private.

Register every source actually used with `source --session-id "<session_id>"`. At finish, attach evidence to recorded learner messages and submit the structured assessment described by the schema. Python enforces evidence references, source association, checkpoint coverage, and permitted mastery transitions; model judgment never replaces those checks.

`start` and `open` return application-launch and verified-note-opening results separately. Success means the exact configured vault, exact lesson note, Reading View, and requested response anchor were read back. A URI fallback is explicitly unverified until CLI-visible application state confirms it. On failure, report the failed stage and retry path; never describe subprocess exit alone as proof the note opened.

Opening does not tile windows. If the learner wants the configured desktop split, run the separate optional command:

```bash
python3 ~/.agents/skills/learn/scripts/learnctl.py layout --session-id "<session_id>"
```

Use `open --session-id "<session_id>"` to retry unresolved delivery. The next relevant hook also retries the newest pending display request. Successful follow remains quiet; unresolved failure is visible in operational health.

When complete, run `finish` with the schema payload and send one concise final response so its exact Stop can close the lesson. Use `pause` when the conversation should temporarily leave the lesson, `abort` only for explicit abandonment, and `repair` for derived Markdown failures. Canonical lesson JSON remains authoritative.
