# Codex + Obsidian bug and feedback history

This document records defects and design failures encountered while building and using the Codex + Obsidian implementation. It is a historical input to the forthcoming code review; it is not a refactor plan. “Fixed” means the current working tree contains a correction and regression coverage where practical. The original Pi implementation was not changed to make these fixes.

## Summary

| ID | Problem | Status |
| --- | --- | --- |
| B-01 | Reinstalling after choosing the wrong vault could leave the old writable root | Fixed |
| B-02 | Starting a lesson did not open its Obsidian note | Fixed |
| B-03 | Codex command approval was misreported as a macOS privacy denial | Fixed |
| B-04 | Tiling targeted the wrong application or Terminal window | Fixed |
| B-05 | Mock-only GUI tests failed to reproduce the Codex CLI failure | Fixed in process; real GUI E2E remains manual |
| B-06 | New lesson output did not remain visible or stay in Reading View | Fixed, event-driven |
| B-07 | Diagnostic MCQ answers clustered at option A | Fixed |
| B-08 | The first answer-position fix introduced a predictable balancing cycle | Replaced |
| B-09 | Diagnostic items were stylistically predictable and lacked multi-correct coverage | Mitigated |
| B-10 | The diagnostic was too weakly bounded and lacked learner self-report | Fixed as an adaptive policy |
| B-11 | Diagnostic, lesson, and evaluation boundaries were unclear | Fixed |
| B-12 | A substantial RNN/LSTM lesson omitted its concept diagram | Fixed as a policy |
| B-13 | Generated LaTeX used forms that Obsidian did not render | Fixed in contract and validation |
| B-14 | The first strict math validator treated `$learn` and learner text as broken math | Fixed before release |
| B-15 | Concept diagrams were too horizontal and falsely constrained to DAGs | Fixed in contract and validation |
| B-16 | `source --session-id` was rejected and source records were not attached to lessons | Fixed |
| B-17 | Attached source citekeys could be lost at lesson finish | Fixed before release |
| B-18 | `_system` accumulated disposable files without a lifecycle command | Fixed |
| B-19 | Review dates used UTC rather than the user's local calendar date | Fixed |
| B-20 | Session-identifier documentation overstated the uniqueness of `session_id` | Fixed |

## Detailed records

### B-01 — Wrong-vault reinstall left configuration risk

- **Observed:** The installer was initially run with the wrong directory, and it was unclear whether rerunning it would fully move Learn to another vault.
- **Cause:** Configuration was split between `~/.config/learn-codex/config.json` and the writable-root entry in `~/.codex/config.toml`. Updating only the JSON path could leave a Learn-owned writable root pointing at the previous vault. An earlier recovery path also could not repair the root after the JSON had already changed.
- **Correction:** The installer now reads the previously configured vault before rewriting configuration, replaces only that old Learn-owned root, backs up `config.toml` before a compatible edit, and remains idempotent. It prints instructions instead of editing when it detects a beta permission profile or an incompatible legacy sandbox configuration.
- **Verification:** `test_reinstall_for_new_vault_replaces_prior_learn_writable_root` and `test_reinstall_repairs_stale_learn_owned_root_after_json_was_changed` use temporary homes and Unicode vault paths.
- **Residual constraint:** A beta permission profile still requires the user to add the writable root through the appropriate Codex configuration interface; the installer deliberately does not mix configuration generations.

### B-02 — Lesson note did not open automatically

- **Observed:** `learnctl start` created a session note, but Obsidian did not open it; the note had to be found manually.
- **Causes:** Automatic opening originally defaulted to off. In addition, the opening path treated process launch as success without checking the command's exit status, so an installed but failing Obsidian CLI prevented a useful fallback.
- **Correction:** New configurations default to automatic opening. Learn prefers the official Obsidian CLI, checks its result, and falls back on macOS to a correctly encoded `obsidian://` URI. `start` returns an explicit opening status.
- **Verification:** Installer-default, Unicode URI, CLI-preference, and CLI-to-URI fallback tests cover these paths.
- **Discarded hypothesis:** Whether Obsidian was already running was not the root cause; direct vault writes and the URI workflow work with Obsidian open or closed.

### B-03 — Two permission layers were conflated

- **Observed:** Learn reported that “macOS blocked” opening or tiling even when the actual blocker was Codex asking for permission to launch a GUI command.
- **Cause:** The implementation and skill text did not distinguish the Codex sandbox/command-approval boundary from macOS Accessibility and Apple Events authorization.
- **Correction:** A sandboxed `start` now returns `approval-required` and defers GUI work to a narrowly scoped `learnctl open` retry. Only AppleScript results associated with assistive-access or Apple Events failures return `permission-required`. User-facing guidance names the correct layer.
- **Verification:** Tests cover sandbox deferral and Accessibility error classification; the complete path was also exercised through a real Codex CLI process.
- **Residual constraint:** Users may still need to approve the `learnctl open` command and grant their terminal application Accessibility/Automation permission once.

### B-04 — Tiling targeted the wrong host or Terminal window

- **Observed:** Obsidian reacted, but the Codex terminal was untouched or the windows did not become two exact halves. The preferred “Codex on the right” placement was not reliable.
- **Causes:** The `codex` process is not the macOS GUI window owner; Terminal, iTerm, Ghostty, Warp, WezTerm, VS Code, or the Codex app is. Activating Obsidian also changes the frontmost window, so “front window” is not a stable way to recover the originating terminal. Generic Accessibility geometry and Terminal's native `bounds` representation also differ.
- **Correction:** Learn detects the GUI host bundle, walks the process ancestry to the exact controlling TTY, locates the Terminal tab/window with that TTY, uses Terminal's native bounds where appropriate, selects the display by maximum overlap, and applies exact half-screen geometry with Codex on the configured side. The default side is right.
- **Verification:** Host detection, ancestor-TTY traversal, native Terminal bounds, multi-display selection, odd-width split geometry, and permission reporting have focused tests. A real Terminal-hosted Codex CLI session successfully opened and tiled the windows.
- **Residual constraint:** The desktop split is macOS-specific and depends on window-manager/Accessibility behavior that cannot be fully reproduced in temporary-directory unit tests.

### B-05 — GUI testing gave false confidence

- **Observed:** Tiling was reported fixed more than once, but it still failed when Learn was invoked through the actual Codex CLI.
- **Cause:** Early tests mocked AppleScript return values and verified generated commands, but did not exercise the real process ancestry, TTY, Codex sandbox, Accessibility database, or application focus changes.
- **Correction:** The final debugging pass launched Learn through a real Terminal-hosted `codex` process and checked the observable window result. Unit tests remain for deterministic logic, but successful mocked AppleScript is no longer treated as proof of end-to-end behavior.
- **Verification:** The real CLI E2E produced a tiled layout and identified the originating `/dev/ttys…` terminal. Regression tests cover the logic that can be isolated.
- **Review implication:** GUI behavior needs an explicit manual/E2E checklist in addition to unit tests.

### B-06 — The live note did not follow output or remain in Reading View

- **Observed:** New assistant text was appended on disk, but Obsidian stayed at its previous scroll position; the user had to scroll. The note could also remain in Editing View.
- **Cause:** Filesystem writes do not instruct Obsidian to navigate, and the Stop hook originally only appended Markdown.
- **Correction:** Each newly logged assistant message gets a unique Tutor heading. After the write, the Stop hook reopens the note at that exact heading, selects Obsidian's explicit **Reading View** menu item, and restores focus to the originating Codex host/TTY. Duplicate Stop events do not repeat the action.
- **Verification:** Tests cover heading selection, URI fragment encoding, explicit Reading View selection, exact Terminal focus restoration, and single follow behavior for duplicate Stop events. A real Codex CLI run left the target lesson active in Obsidian with workspace mode `preview`.
- **Residual constraint:** This is event-driven, not a background watcher. If the user changes mode manually, Learn restores Reading View after the next assistant message. Very long responses show the anchored beginning of the newest output, not necessarily its final line.

### B-07 — Correct MCQ answers clustered at A

- **Observed:** All or most diagnostic answers appeared at option A, allowing position recognition to replace subject knowledge.
- **Cause:** The language model generated substantive choices and labels together. LLM output is not a reliable randomness source and showed a strong positional habit.
- **Correction:** The model must draft unlabeled choices first, then request correct positions from `learnctl context --next-mcq` with a stable semantic question ID. The utility uses `secrets.SystemRandom`, outside the model, and stores one immutable issuance per question; retries cannot reroll it.
- **Verification:** Tests patch the entropy source, verify the selected labels, verify one draw per stable question ID, and verify isolation between Codex sessions.
- **Residual constraint:** Operating-system randomness controls labels, not the semantic quality of model-written distractors.

### B-08 — The first MCQ fix leaked a new pattern

- **Observed:** A proposed scheme put A, B, C, and D exactly once in each four-question cycle. Once three answers were known, the fourth became inferable.
- **Cause:** The correction optimized balance rather than unpredictability. Balanced scheduling without replacement is deterministic information leakage.
- **Correction:** The balancing cycle was removed. Every item now samples independently from operating-system randomness; repeats are allowed, and no cycle boundary constrains the next result.
- **Verification:** The issuance tests assert entropy-backed sampling and immutable reuse, not balanced frequencies.

### B-09 — MCQs remained guessable from style and supported only one correct answer

- **Observed:** Even with varied positions, a repeated “kind” of correct option—longer, more qualified, more technical, or more comprehensive—could reveal the answer. Single-correct items also under-sampled concepts with several independently true conditions.
- **Cause:** Position randomization does not remove semantic/style leakage, and the original diagnostic contract assumed single-select questions.
- **Correction:** The skill requires parallel length, grammar, specificity, vocabulary, and confidence across choices; it requires rewriting items that a test-wise learner can solve stylistically. `context --next-mcq` supports multiple correct positions, and diagnostics use select-all items when the concept has unambiguously independent correct claims.
- **Verification:** Utility tests cover multiple unique correct positions and immutable issuance. Semantic quality remains governed by the teaching contract rather than a deterministic parser.
- **Residual constraint:** An LLM cannot guarantee perfectly unbiased distractor semantics. The system can constrain and audit style, but human inspection remains the strongest check.

### B-10 — Diagnostic depth and self-report were underspecified

- **Observed:** A fixed four-question diagnostic could be too shallow for mixed knowledge, while the earlier 2–5 guidance did not state when to stop. It also omitted the learner's own account of goals, confidence, and unclear areas.
- **Cause:** Question count was treated as a default range rather than a decision rule, and recognition items were carrying too much diagnostic responsibility.
- **Correction:** Learn now asks one unscored subjective self-map followed by 4–6 high-information knowledge questions, one at a time. It stops after four only when the last three independent misses or “I don't know” responses span distinct prerequisite levels and converge on one missing foundation. Mixed, boundary, guessing, or calibration evidence extends to five or six. The subjective answer informs route and calibration but never counts as mastery evidence.
- **Verification:** Skill-contract tests check the updated range and subjective prompt. This remains a pedagogical policy rather than a CLI-scored state machine.
- **Residual constraint:** Four questions can select a starting point in a clear case; neither four nor six can characterize the learner's entire knowledge state. Stronger mastery claims still require recall, explanation, application, transfer, and delayed evidence.

### B-11 — Phase transitions were invisible

- **Observed:** During the RNN/LSTM lesson, it was unclear when diagnosis ended, teaching began, or later knowledge evaluation started.
- **Cause:** The initial skill described pedagogical phases internally but did not require visible markers in conversation or the Obsidian log.
- **Correction:** The skill now requires `Diagnostic begins`, numbered diagnostic labels, `Diagnostic complete`, `## Lesson begins`, and separate `Knowledge evaluation begins/complete` markers. Ordinary teaching checks must be labeled as checks rather than silently treated as scored evaluation.
- **Verification:** Skill-contract tests assert the required phase markers.

### B-12 — A substantial lesson omitted its concept map

- **Observed:** The RNN/LSTM session was substantial but contained no Mermaid map.
- **Cause:** “Selective visualization” was interpreted too permissively, and there was no operational threshold requiring a map.
- **Correction:** A lesson with more than one meaningful conceptual relationship or an expected duration over roughly ten minutes must include a small map immediately after `## Lesson begins`. Quick explanations remain exempt.
- **Verification:** The skill contract now makes the threshold and placement explicit.
- **Residual constraint:** Whether a lesson crosses the threshold is still model judgment.

### B-13 — LaTeX did not render in Obsidian

- **Observed:** Diagnostics and bullets contained forms such as `(n_{\text{eff}})`, `(\lambda_0)`, `(\displaystyle …)`, `n{\text{eff}}`, and `\lambda0`, which Obsidian displayed literally or interpreted incorrectly.
- **Causes:** Parentheses were used as if they were math delimiters, LaTeX commands appeared outside dollar delimiters, inline output used `\displaystyle`, and subscripts/command boundaries were malformed. The first correction relied mainly on model instructions and did not mechanically detect regressions.
- **Correction:** The rendering contract now requires dollar delimiters for every mathematical token, one-line unpadded inline math, delimiter-only display blocks, core MathJax commands, explicit braces/subscripts, quoted callout display lines, and per-cell table discipline. `validate` now rejects unsupported `\(…\)`/`\[…\]`, bare LaTeX commands/subscripts, padded or unbalanced inline math, same-line display delimiters, unbalanced braces, `\displaystyle` inline, commands run into digits, and missing subscript/operator forms such as `n{\text{eff}}`.
- **Verification:** Tests cover the reported malformed families and preservation of valid inline/display math, fenced code, callouts, and Mermaid.
- **Residual constraint:** Validation catches common structural failures; it is not a complete TeX parser. Raw assistant Markdown is preserved by design, so the pre-send skill audit remains the first line of defense.

### B-14 — Strict math validation initially rejected valid session records

- **Observed during tests:** The first strict validator treated the literal skill name `$learn` as an unmatched math delimiter. It would also have rejected raw learner text such as an intentionally malformed formula or `$5`.
- **Cause:** Validation scanned the entire lesson transcript without distinguishing command syntax or generated Tutor text from preserved Learner text.
- **Correction:** The exact `$learn` command token is excluded from math parsing, and math/Mermaid rendering checks omit learner transcript blocks while retaining them verbatim in the note. Generated and Tutor content remains strict.
- **Verification:** A regression test validates a session whose initiating learner prompt contains both malformed notation and `$5`.

### B-15 — Concept diagrams were wide and falsely modeled as DAGs

- **Observed:** Mermaid graphs were commonly horizontal and required scrolling in the half-width Obsidian pane. The teaching language also called every map a dependency DAG, even when concepts had cross-links, detours, or feedback.
- **Cause:** The Codex adaptation kept the idea of a dependency graph but did not carry over enough of the upstream visualization skill's “one idea, fewest elements” discipline. DAG language encouraged flattening genuine cycles, while Mermaid direction was not constrained for the split layout.
- **Correction:** Lesson concept maps use `flowchart TD`, a short vertical main spine, approximately 5–7 nodes, short quoted labels, and only meaningful edges. Prerequisite, explanatory, application, cross-link, and feedback edges may differ; real cycles are retained. If cross-links become tangled, the map shows the main chain and explains secondary links in prose rather than falsifying the structure. Validation rejects Tutor-generated `flowchart LR` and `flowchart RL`.
- **Verification:** A regression test accepts a top-to-bottom Mermaid graph containing a feedback cycle and rejects its horizontal equivalent.
- **Residual constraint:** The upstream Pi skill used a dedicated maker that rendered and visually inspected PNG output. The Codex implementation cannot preserve that architecture because this project's constraints prohibit separate visual-maker agents and mandatory Mermaid/Chromium dependencies. `mmdc` rendering remains optional when already installed.

### B-16 — Source command rejected an explicit session and did not attach provenance

- **Observed:** A lesson attempted to call `learnctl source --session-id …`; argparse rejected the flag. The assistant then reported that source logging was automatically tied to the active lesson, which was also false.
- **Cause:** The shared parser added `--session-id` and `--cwd` only for `finish`. `command_source` created or deduplicated a global source record but had no active-session association logic.
- **Correction:** `source` now accepts both selectors. With `--session-id`, it verifies the exact active lesson, creates or deduplicates the source, records the effective citekey in active state, and updates the live lesson's Sources section. Without a selector it remains an explicit global-library operation; association is never implicit.
- **Verification:** Tests execute the exact flag form, reject an unknown session, verify live-note attachment, and verify that a deduplicated source attaches under its existing citekey.
- **Related logging issue:** Stop hooks intentionally log all assistant text. Therefore the assistant's user-visible narration about checking the CLI schema appeared in the lesson note. Fixing the command contract removes that trigger, but the logger cannot selectively discard user-visible operational narration without violating the raw-assistant-text requirement. The skill continues to prohibit internal process commentary in lesson output.

### B-17 — Attached sources could disappear at finish

- **Observed during implementation tests:** Even after attaching a source to active state, a finish payload with an empty `source_citekeys` list would overwrite the session Sources section and omit it from topic state.
- **Cause:** `finish` trusted only the payload and did not merge citekeys already attached by `source`.
- **Correction:** Finish validates the payload, merges active attached citekeys without duplicates, revalidates the effective sources, and then renders session/topic state.
- **Verification:** The source-association regression test deliberately finishes with an empty source list and confirms that the attached verified citekey remains.

### B-18 — `_system` grew without cleanup semantics

- **Observed:** `_system` quickly filled with event hashes, pending records, and stale active records. At one inspection it contained 52 event files, 17 pending records, and nine active records; several active records pointed to missing test/retry notes and caused validation failures.
- **Cause:** The earlier design created one sidecar file per role and turn even though canonical lesson records already owned message identity. Pending records also lacked a defined lifecycle, and interrupted GUI/E2E attempts left orphan active state.
- **Correction:** Event sidecars and their directory were removed. Canonical lesson messages now own deduplication identities exclusively. `learnctl cleanup` remains a dry run by default and requires `--apply`; it handles only disposable pending records, stale bindings, and Finder metadata while preserving durable lesson, review, topic, source, note, and asset data.
- **Verification:** Cleanup tests cover dry-run behavior, apply behavior, active-session protection, stale state, retention, and preservation of durable content. Hook tests verify canonical replay deduplication without sidecars.
- **Implementation correction caught before release:** An early cleanup rule considered deleting pending records once a session was active. `context --next-mcq` still needs the active session's latest pending record to bind answer-position issuance to the current turn. The final rule protects pending records for every live session.

### B-19 — Review scheduling crossed the UTC/local-date boundary

- **Observed during the full test suite:** In India before 05:30, finishing a lesson scheduled the next review one local calendar day too early.
- **Cause:** `today()` returned the UTC date while review dates are user-facing calendar dates and the tests use the machine's local date.
- **Correction:** Review scheduling now uses `datetime.date.today()`; timestamps used for ordering and provenance remain timezone-aware UTC.
- **Verification:** The review-scheduling suite passes across the observed UTC/local date mismatch.

### B-20 — `session_id` was described as a globally unique lesson ID

- **Observed:** Documentation initially called the Codex `session_id` the unique identifier of a learning session without qualification.
- **Cause:** Active-state identity and durable lesson-record identity were conflated. One Codex conversation has one active Learn note at a time, but it can run multiple lessons sequentially with the same Codex `session_id`.
- **Correction:** Documentation now distinguishes the immutable local `lesson_id`, exact runtime `session_id`, and exact runtime `turn_id`. New session notes store lesson and conversation identifiers in frontmatter; the session-note path and short filename suffix replace neither.
- **Verification:** Canonical storage tests confirm UUID lesson identity, exact conversation binding, and exact hook turn isolation.

## Behavior clarifications that were not defects

### C-01 — Pausing a lesson

Learn now has an explicit `paused` state. `pause` preserves the lesson and detaches it so unrelated conversation is not logged. `resume` requires a selected lesson UUID and refuses to steal an attachment from another conversation. `abort` remains abandonment and preserves the record and note.

### C-02 — Obsidian already being open

Obsidian focus is not required for filesystem logging. Automatic navigation uses the CLI or URI whether the application is already running; window automation only needs an accessible window when tiling or selecting Reading View.

### C-03 — Loading updated skill instructions

The symlinked Python utility and hooks use code changes immediately. An already-running model turn may retain the previously loaded teaching instructions, so explicitly invoking `$learn Continue the active lesson using the updated skill` reloads the skill while recovering the same active lesson rather than starting over.

## Known limitations and review inputs

These are current boundaries, not claims of completed fixes:

1. Math validation is deliberately structural, not a complete MathJax/TeX parser.
2. Mermaid is not visually rendered in automated tests unless `mmdc` already exists; the current environment does not provide it.
3. Window tiling and Reading View automation are macOS-specific and still require a manual real-application E2E check after material changes.
4. Auto-follow runs after each newly logged assistant Stop event; it is not continuous background synchronization.
5. Diagnostic stopping remains model-executed policy rather than deterministic state tracked by `learnctl`.
6. Distractor semantics remain model-generated even though answer positions use operating-system randomness.
7. User-visible assistant process narration is logged because the hook must preserve raw assistant messages; prevention belongs in the skill behavior.
8. PyYAML is installed only in the ignored development virtual environment for the bundled skill-package validator. Learn itself remains standard-library-only.

## Current verification baseline

As of 2026-09-05 after the clean canonical reset:

- 98 standard-library `unittest` tests pass; the removed migration suite is preserved in the external migration archive.
- The Python files compile successfully.
- `git diff --check` passes.
- Tests use temporary HOME and vault directories.
- The successful macOS Codex CLI/Obsidian E2E check is manual and is not represented by a permanently automated GUI test.
