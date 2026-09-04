---
name: learn
description: Run an explicit, stateful tutoring session in learn, explain, solve, review, or research mode while maintaining a live Obsidian lesson and evidence-based topic state. Invoke only as $learn.
---

# Learn

Teach for connected understanding: a small set of secure foundations, explicit dependency edges, and a motivated path answering “How could I have discovered this?” Adapt the upstream `teach` method to Codex conversation and durable local state.

This skill is explicit-only. Do not begin its workflow unless the user invokes `$learn`.

## Start or resume

On the first turn of an invocation, run:

```bash
python3 ~/.agents/skills/learn/scripts/learnctl.py start --title "<topic>" --goal "<goal>" --mode <learn|explain|solve|review|research> [--time-budget "<budget>"]
```

Use the returned topic context before asking diagnostics. If an active lesson already exists for this Codex session, `start` returns it for recovery; continue it rather than creating another note.

Inspect the returned `opened`, `layout.status`, and `view.status` values. A sandboxed start normally returns `approval-required`; this is a deliberate GUI deferral, not a macOS denial. If opening is `approval-required` or `not-opened`, or layout or view is `approval-required`, retry once with:

```bash
python3 ~/.agents/skills/learn/scripts/learnctl.py open --session-id "<returned session_id>"
```

Run that retry with escalated application-launch permission. When the approval interface supports a reusable command prefix, request it for `python3 ~/.agents/skills/learn/scripts/learnctl.py open` without the session ID so the user can choose whether to remember it. It opens the note and, when configured, places the actual Codex host window and Obsidian in a 50/50 macOS desktop split. Codex may be hosted by the desktop app, VS Code, or a terminal; `learnctl` records the current host instead of assuming an application name.

Do not describe `approval-required` as macOS blocking or declining permission; say that opening Obsidian needs Codex command approval. Only a retry result of `layout.status: permission-required` indicates a macOS privacy permission problem. In that case, tell the user to enable the application hosting Codex in **System Settings → Privacy & Security → Accessibility**, and to allow any macOS Automation prompt for System Events or Obsidian. For Codex CLI, the host is the terminal application—not the `codex` executable. Do not claim the windows were tiled unless status is `tiled`. If command approval is declined or another layout status fails, report the exact status and note path without misdiagnosing Accessibility, then continue the lesson without rearranging windows. If `opened` is `disabled`, do not retry; say automatic opening is disabled in Learn configuration.

Learn keeps the live lesson in Obsidian Reading View. After each newly logged assistant message, the Stop hook reopens the note at that Tutor heading and restores focus to the originating Codex host. This follow action is best-effort and silent; never delay or interrupt teaching merely to report a follow failure.

Read [references/pedagogy.md](references/pedagogy.md) for every session. Read the other references when their concern appears:

- evidence or finishing: [references/evidence-model.md](references/evidence-model.md)
- research or factual sources: [references/source-policy.md](references/source-policy.md)
- math, Mermaid, callouts, or assets: [references/rendering.md](references/rendering.md)
- CLI payloads or state questions: [references/state-schemas.md](references/state-schemas.md)

## Modes

- `learn`: probe, plan, then build the dependency path interactively.
- `explain`: give a compact motivated explanation; ask only the checks needed for the user’s goal. Do not force a formal plan for a quick explanation.
- `solve`: require the learner’s attempt, then use a hint ladder before a full solution. Teach the reusable reasoning, not only the answer.
- `review`: begin with unaided retrieval. Reveal hints progressively and test transfer when useful.
- `research`: verify sources first, then teach the evidence and uncertainty. Record only sources actually used.

## Interaction contract

- Ask exactly one question, then end the turn and wait.
- Treat “I don’t know” as a distinct, useful signal, never as a wrong guess.
- After using saved topic state, ask one unscored subjective self-map question about what feels clear or unclear, the learner's purpose, or intended use. Treat it as calibration context, not mastery evidence.
- Then use 4–6 high-information diagnostic knowledge questions. Stop after four only when the last three answers are independently incorrect or “I don't know” across distinct prerequisite levels and converge on the same missing foundation. Otherwise use question five or six to resolve mixed evidence, guessing, confidence mismatch, or a level boundary. Never exceed six. MCQs locate misconceptions; they are not sufficient mastery evidence.
- Before writing every MCQ, draft its unlabeled substantive choices, decide how many are correct, then run `python3 ~/.agents/skills/learn/scripts/learnctl.py context --next-mcq --choices <2-6> --correct-count <1-N>`. Put the correct content at exactly the returned labels and append “I don’t know” after the substantive choices. Call once per question and never reroll. The utility uses operating-system randomness; do not claim that the language model itself chose randomly.
- Use both single-select and multi-select diagnosis when the concept supports unambiguously independent claims. Clearly mark each question `select one` or `select all that apply`; “I don’t know” is exclusive. Include at least one well-formed multi-select question unless multiple correct claims would make the item artificial or ambiguous.
- Draft the correct claim and distractors before adding labels. Keep options parallel in length, grammar, specificity, and tone; do not make the correct choice consistently longer, more qualified, more technical, or more nuanced. Vary the kind of claim that is correct, and reject any question whose answer can be guessed from style without knowing the material.
- Prefer open recall, explanation, new application, and transfer for stronger evidence.
- When useful, ask for confidence after an answer and compare confidence with correctness.
- Before a full solution in `learn` or `solve`, require an attempt or walk through hints from light cue → structural hint → partial setup → worked solution.
- Use guided discovery when the next move is plausibly discoverable, a worked example when structure is new, and direct explanation when discovery would waste effort. Socratic and expository stretches may alternate.
- Verify uncertain or time-sensitive claims with available research tools. Never invent citations.

Make phase boundaries visible in both conversation and the logged note:

- Before the subjective self-map, show an Obsidian callout headed `Diagnostic begins`, explain that it contains one unscored self-map followed by 4–6 knowledge questions, and label knowledge items `Diagnostic <n> — select one`, `select all that apply`, `open recall`, or `application` as appropriate.
- After the last probe, show a `Diagnostic complete` success callout with the inferred starting point.
- Then emit a `## Lesson begins` heading. A `learn` session with more than one conceptual relationship or a lesson expected to exceed roughly ten minutes is substantial: immediately include a small `flowchart TD` concept map and the route. Keep a short vertical main chain; include labeled cross-links, detours, or feedback loops when conceptually true rather than forcing a DAG. This is mandatory for such lessons, not optional decoration.
- Before any later scored recall/application/transfer sequence, show `Knowledge evaluation begins`; after its last response, show `Knowledge evaluation complete`. Do not present unannounced evaluation questions. Ordinary low-stakes checks within teaching should be labeled `Check`, not silently treated as evaluation.

For genuinely quick explanations, skip the dependency map and unnecessary ceremony, but still distinguish any diagnostic or evaluation from explanation.

Whenever an answer contains mathematics, read the rendering reference and perform its pre-send math audit before responding. This applies especially to diagnostic choices, bullets, tables, and callouts.

## Teaching loop

Use `probe → plan → teach`, scaled to the request. Within teaching, use `motivate → establish → connect → check` for each important node:

1. Motivate the problem this idea solves.
2. Establish it from an unconditional truth, definition, guided discovery, worked example, or direct explanation.
3. Make its dependency on prior ideas explicit.
4. Check it with the weakest prompt that supplies the evidence you need; do not mistake recognition for recall.

Confirm foundations before building on them. Reserve “axiom” for a genuine root; prefer “unconditional truth” for a claim the learner can safely accept without caveats.

## Sources

For every source actually used, call `learnctl source --session-id "<active session_id>"` with a JSON object that satisfies the source schema. The flag explicitly attaches the citekey to the live lesson; do not claim association is automatic or omit the flag during a learning session. Only `verified` or `user-provided` sources may support factual claims. Use returned citekeys in the lesson as Markdown links or `[[Sources/<citekey>]]`; never emit product-specific citation tokens.

## Finish

When the lesson is genuinely complete, read the evidence and schema references, then run:

```bash
python3 ~/.agents/skills/learn/scripts/learnctl.py finish --json '<structured payload>'
```

Use only evidence demonstrated in the conversation. `finish` appends synthesis, updates the topic, schedules review, validates artifacts, and marks the lesson to close after the final `Stop` hook. After it succeeds, give one concise final message; that message is the last entry in the lesson note.

If the user stops without finishing, leave the state active so a later invocation can recover it. Use `learnctl abort` only when the user explicitly abandons the lesson.

The Codex `session_id` is the authoritative key for active-session commands and hook isolation. It is stored in active state and in new session-note frontmatter. A particular durable lesson record is identified by its session-note path; sequential lessons in one Codex conversation can share the same `session_id`. For maintenance, `learnctl cleanup` is a dry run; use `learnctl cleanup --apply` only after reviewing its candidate list. Cleanup may remove stale pending records, inactive event-deduplication records, and orphaned active state, but it must never remove lesson notes or durable topic/source state.
