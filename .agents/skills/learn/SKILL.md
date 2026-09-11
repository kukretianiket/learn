---
name: learn
description: Run an explicit, stateful tutoring session in learn, explain, solve, review, or research mode while maintaining a live Obsidian lesson and evidence-based topic state. Invoke only as $learn.
---

# Learn

Teach for connected understanding: a small set of secure foundations, explicit dependency edges, and a motivated path answering “How could I have discovered this?” Adapt the upstream `teach` method to Codex conversation and durable local state.

This skill is explicit-only. Begin a lesson when the user invokes `$learn` as a leading token or selects its linked local `SKILL.md` in the IDE (`[$learn](.../learn/SKILL.md)`). Quoted invocations and discussions of this skill are not requests to start a lesson.

## Start or resume

On the first turn of an invocation, run:

```bash
python3 ~/.agents/skills/learn/scripts/learnctl.py start --title "<topic>" --goal "<goal>" --mode <learn|explain|solve|review|research> [--time-budget "<budget>"]
```

For a new lesson, use the returned `lesson_id`, exact `session_id`, and `topic_context` before asking diagnostics. If an attached lesson already exists for this Codex conversation, `start` returns `recovery` instead: the latest semantic checkpoint, a compact topic summary, and a bounded set of messages after the checkpoint. Continue from that data rather than creating another note or requesting context again. Check `lesson_status`: a `finishing` lesson awaits its recorded exact final Stop and must not resume teaching. A paused lesson is deliberately detached: resume it only by explicitly selecting its `lesson_id` with `learnctl resume --lesson-id "<lesson_id>"`. Resuming from another conversation records the prior binding and never steals a live attachment.

If `start` reports `No pending prompt for Codex session ...`, the conversation ID resolved but its initiating prompt was not captured. Report that exact limitation; do not claim VS Code is unsupported or that the lesson is attached. Do not invent a turn ID, write a pending record by hand, or choose another conversation. Check activation format and hook delivery/configuration; after correcting the cause, a fresh explicit user invocation is needed to capture the prompt. `repair` cannot recover an uncaptured prompt. If teaching continues without attachment, clearly state that Obsidian logging is unavailable.

In an uninterrupted lesson, use the conversation context already present. Do not run `learnctl context` every turn and do not save a checkpoint after every answer. Save a compact semantic checkpoint after diagnosis establishes the route and after a meaningful route change:

```bash
python3 ~/.agents/skills/learn/scripts/learnctl.py checkpoint --json '<checkpoint payload>'
```

On recovery, honor `recovery.history.more_history_exists`. Only if the bounded history is insufficient, retrieve a specific message with `context --message-id <id>` or an inclusive slice with `context --from-message <id> --to-message <id> --limit <1-50>`. Never request the entire lesson record. The checkpoint schema and retrieval response are in [references/state-schemas.md](references/state-schemas.md).

Inspect the returned `opened`, `layout.status`, and `view.status` values. A sandboxed start normally returns `approval-required`; this is a deliberate GUI deferral, not a macOS denial. If opening is `approval-required` or `not-opened`, or layout or view is `approval-required`, retry once with:

```bash
python3 ~/.agents/skills/learn/scripts/learnctl.py open --session-id "<returned session_id>"
```

Run that retry with escalated application-launch permission. When the approval interface supports a reusable command prefix, request it for `python3 ~/.agents/skills/learn/scripts/learnctl.py open` without the session ID so the user can choose whether to remember it. It opens the note and, when configured, places the actual Codex host window and Obsidian in a 50/50 macOS desktop split. Codex may be hosted by the desktop app, VS Code, or a terminal; `learnctl` records the current host instead of assuming an application name.

Do not describe `approval-required` as macOS blocking or declining permission; say that opening Obsidian needs Codex command approval. Only a retry result of `layout.status: permission-required` indicates a macOS privacy permission problem. In that case, tell the user to enable the application hosting Codex in **System Settings → Privacy & Security → Accessibility**, and to allow any macOS Automation prompt for System Events or Obsidian. For Codex CLI, the host is the terminal application—not the `codex` executable. Do not claim the windows were tiled unless status is `tiled`. If command approval is declined or another layout status fails, report the exact status and note path without misdiagnosing Accessibility, then continue the lesson without rearranging windows. If `opened` is `disabled`, do not retry; say automatic opening is disabled in Learn configuration.

Learn keeps the live lesson in Obsidian Reading View. After each newly logged assistant message, the Stop hook reopens the note at that Tutor heading and restores focus to the originating Codex host. This follow action is best-effort and silent; never delay or interrupt teaching merely to report a follow failure.

Read [references/pedagogy.md](references/pedagogy.md) for every session and [references/source-policy.md](references/source-policy.md) before preparing a substantial lesson or using factual sources. Reuse references already loaded in the conversation; do not reread them each turn. Read the other references when their concern appears:

- evidence or finishing: [references/evidence-model.md](references/evidence-model.md)
- diagrams, math, Mermaid, callouts, or assets: [references/rendering.md](references/rendering.md)
- CLI payloads or state questions: [references/state-schemas.md](references/state-schemas.md)

## Modes

- `learn`: diagnose the relevant prerequisites, select the route, then build the dependency path interactively.
- `explain`: answer compactly and diagnose only where it helps the explanation. Do not force a formal plan for a quick explanation.
- `solve`: inspect the learner’s attempt first, then use a hint ladder before a full solution. Teach the reusable reasoning, not only the answer.
- `review`: begin with unaided retrieval. Reveal hints progressively and test transfer when useful.
- `research`: verify sources first, then teach the evidence and uncertainty. Record only sources actually used.

## Prepare the lesson

Research is part of substantial teaching in every mode, not only `research` mode. Once the goal is sufficiently clear, inspect authoritative material before committing to the route: verify the foundations and their assumptions, the central mechanism, and likely misconceptions. Follow the bounded preparation and stopping rule in the source policy. A substantial lesson covers multiple conceptual relationships or is expected to exceed roughly ten minutes. Quick explanations need proportionate verification, not a literature review.

Use the research to choose a motivated route from demonstrated knowledge to the goal. Surface a short learner-facing route, with inline citations for its substantive factual claims; keep search notes and the preparation checklist out of the lesson. Consider whether the difficult step needs a mechanism diagram, worked trace, plot, or inspected external figure in addition to the concept map. Follow the rendering reference when a visual will clarify that step.

Do not research the entire field before asking a needed goal clarification. Do not reveal diagnostic or review answers during preparation: keep answer-revealing citations and visuals for feedback. Reuse checked material during the session and research again only when a new claim, uncertainty, or changed scope requires it.

## Interaction contract

- When eliciting a learner response, ask exactly one question, then end the turn and wait. An explanation or completion need not end with a question.
- Treat “I don’t know” as a distinct, useful signal, never as a wrong guess.
- For a new `learn` diagnosis, after using saved topic state, ask one unscored subjective self-map question about what feels clear or unclear, the learner's purpose, or intended use. Treat it as calibration context, not mastery evidence. Other modes follow their entry behavior above; recovery continues the current phase rather than restarting diagnosis.
- For that diagnosis, use 4–6 high-information diagnostic knowledge questions. Stop after four only when the last three answers are independently incorrect or “I don't know” across distinct prerequisite levels and converge on the same missing foundation. Otherwise use question five or six to resolve mixed evidence, guessing, confidence mismatch, or a level boundary. Never exceed six. Record untested prerequisites as unknown and check them when the route reaches them. MCQs locate misconceptions; they are not sufficient mastery evidence.
- Before writing every MCQ, draft its unlabeled substantive choices, decide how many are correct, assign a stable semantic question ID, then run `python3 ~/.agents/skills/learn/scripts/learnctl.py context --next-mcq --choices <2-6> --correct-count <1-N> --question-id <stable-id>`. Put the correct content at exactly the returned labels and append “I don’t know” after the substantive choices. Reuse that ID only when retrying the same question; its issuance is immutable and cannot be rerolled. Show labeled choices but do not identify the correct labels or reveal the explanation before the learner answers. Explain the correct reasoning in feedback; keep private issuance and assessment metadata out of live Markdown. The utility uses operating-system randomness; do not claim that the language model itself chose randomly.
- Use both single-select and multi-select diagnosis when the concept supports unambiguously independent claims. Clearly mark each question `select one` or `select all that apply`; “I don’t know” is exclusive. Include at least one well-formed multi-select question unless multiple correct claims would make the item artificial or ambiguous.
- Draft the correct claim and distractors before adding labels. Keep options parallel in length, grammar, specificity, and tone; do not make the correct choice consistently longer, more qualified, more technical, or more nuanced. Vary the kind of claim that is correct, and reject any question whose answer can be guessed from style without knowing the material.
- Prefer open recall, explanation, new application, and transfer for stronger evidence, but judge the reasoning required, not the question format. Substituting supplied definitions into a supplied target is procedural practice, not evidence of conceptual understanding. Ask it only when that procedure is the learning goal or an observed prerequisite gap.
- When useful, ask for confidence after an answer and compare confidence with correctness.
- For assigned problems in `learn` or `solve`, obtain an attempt and use the least revealing useful hint: light cue → structural hint → partial setup → worked solution. Do not force every rung or withhold a direct solution explicitly requested by the learner; distinguish assisted performance from independent evidence.
- Use guided discovery when the next move is plausibly discoverable, a worked example when structure is new, and direct explanation when discovery would waste effort. Socratic and expository stretches may alternate.
- Verify uncertain or time-sensitive claims with available research tools. Never invent citations.

Make phase boundaries visible in both conversation and the logged note:

- Before the subjective self-map, show an Obsidian callout headed `Diagnostic begins`, explain that it contains one unscored self-map followed by 4–6 knowledge questions, and label knowledge items `Diagnostic <n> — select one`, `select all that apply`, `open recall`, or `application` as appropriate.
- After the last probe, show a `Diagnostic complete` success callout with the inferred starting point.
- Then emit a `## Lesson begins` heading. A `learn` session with more than one conceptual relationship or a lesson expected to exceed roughly ten minutes is substantial: immediately include a small `flowchart TD` concept map and the route. Keep a short vertical main chain; include labeled cross-links, detours, or feedback loops when conceptually true rather than forcing a DAG. This is mandatory for such lessons, not optional decoration.
- Before any later scored recall/application/transfer sequence, show `Knowledge evaluation begins`; after its last response, show `Knowledge evaluation complete`. Do not present unannounced evaluation questions. Ordinary low-stakes checks within teaching should be labeled `Check`, not silently treated as evaluation. A label does not make a question worthwhile: both checks and evaluations must satisfy the question-selection rules in the pedagogy reference. Do not manufacture a check after every explanation or algebraic step.

For genuinely quick explanations, skip the dependency map and unnecessary ceremony, but still distinguish any diagnostic or evaluation from explanation.

Whenever an answer contains mathematics, use the rendering reference (read it if not already loaded) and perform its pre-send math audit before responding. This applies especially to diagnostic choices, bullets, tables, and callouts.

## Teaching loop

Use `probe → plan → teach`, scaled to the request. Within teaching, motivate, establish, and connect each important idea; ask for a learner response only when it serves discovery or resolves a meaningful uncertainty:

1. Motivate the problem this idea solves.
2. Establish it from a checked foundation or definition, using guided discovery, a worked example, or direct explanation.
3. Make its dependency on prior ideas explicit.
4. If a check is needed, target the model choice, mechanism, assumption, or consequence that determines the next step. Privately identify what different answers would change in your teaching. If no meaningful decision depends on the answer, explain the step and advance without a question.

Show routine substitution, simplification, and arithmetic concisely when the learner already has those skills. Do not fragment a conceptual derivation into mechanical quizzes. Before a discovery question, make the open problem and its connection to the next idea clear without revealing the answer. After the response, use the learner's reasoning to establish that connection; do not grade and jump to an unrelated topic.

Build each teaching turn from the learner's actual last response: identify the specific useful reasoning or gap, connect it to the open problem, and make the next conceptual move reachable. Follow the natural progression and hint rules in the pedagogy reference; do not narrate the full derivation and then ask the learner to guess the result already supplied.

Confirm foundations before building on them. Reserve “axiom” for a genuine root. State the domain and assumptions of a model; never turn a useful approximation or conditional result into an “unconditional truth.”

## Sources

For every source actually used, call `learnctl source --session-id "<active session_id>"` with a JSON object that satisfies the source schema. The flag explicitly attaches the citekey to the live lesson; do not claim association is automatic or omit the flag during a learning session. Reuse returned citekeys. Follow the source policy for verification, inline citations, and when to update an existing source usage. A Sources section alone is not inline attribution; use descriptive Markdown links to the inspected source beside the supported claims. Never emit product-specific citation tokens.

Before sending a teaching response, check silently: are consequential factual claims checked and cited where needed; are assumptions explicit; does any visual explain the intended relationship; and does the next move follow from this learner's answer without giving away the pending task? If asking a question, could someone answer it through supplied substitutions or repetition without understanding the target concept, and does the response actually affect the next step? Replace or omit questions that fail this relevance test. Repair deficiencies before sending. These are tutor behavior requirements, not claims that the CLI can verify teaching quality.

## Finish

When the lesson is genuinely complete, read the evidence and schema references, then run:

```bash
python3 ~/.agents/skills/learn/scripts/learnctl.py finish --json '<structured payload>'
```

Use only evidence demonstrated in the conversation. Attach each structured ability and attempted transfer to recorded learner Markdown using the evidence selectors in the evidence reference; `current` normally identifies the learner response being assessed. Include the completion checkpoint as the optional top-level `checkpoint` object in the same finish payload so it is committed with the assessment. `finish` resolves selectors, persists the assessment and completion checkpoint once, updates the topic idempotently, and enters `finishing` with the exact current conversation and turn expected for the final `Stop`. Repeating the same finish is safe; never replace an accepted assessment or completion checkpoint with conflicting content. After it succeeds, give one concise final message. Only the matching Stop is captured as the last lesson entry and changes the lesson to `completed`; an old or unrelated Stop is ignored.

If the user wants to suspend the lesson and use the conversation for unrelated work, run `learnctl pause`; it preserves the canonical record and stops logging. Resume only an explicitly selected unfinished lesson. If an expected final Stop is missing, retain the finishing assessment and report the exact missing session and turn; do not substitute another response. Use `learnctl abort` only when the user explicitly abandons the lesson; abort preserves the record and note while stopping future logging.

The locally generated `lesson_id` identifies the durable lesson; the runtime’s exact Codex `session_id` identifies its current conversation attachment. They are never interchangeable, and a working directory is not identity. Canonical lesson JSON under `_system/lessons` owns recorded messages; session Markdown is derived from it. Use `learnctl repair --lesson-id "<lesson_id>"` if status reports a rendering repair. For maintenance, `learnctl cleanup` is a dry run; use `learnctl cleanup --apply` only after reviewing its candidate list. Cleanup must never remove canonical lessons, lesson notes, or durable topic/source state.
