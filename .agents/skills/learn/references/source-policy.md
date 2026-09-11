# Source policy

Create one source record only for a source actually used. Search-result snippets are discovery aids, not verification.

## Research before the route

For a substantial lesson, prepare from inspected material even when the topic feels familiar. Once the learner's goal is clear enough, use a focused search and open the relevant passages, not just snippets or abstracts:

1. Establish the standard definitions, prerequisites, mechanism, and assumptions from an authoritative foundation: a textbook, university treatment, primary paper, official documentation, or an appropriate evidence review. For software behavior, prefer documentation for the relevant version.
2. Cross-check the central nontrivial claim or derivation against an independent authoritative treatment. Normally this means two complementary inspected sources for a substantial lesson, not two pages repeating the same account. One definitive source can suffice for a narrow specification or result; do not collect redundant citations to meet a quota.
3. Resolve the misconception or limiting case most likely to affect this learner's route. Select a small worked example and, if helpful, a figure that makes the mechanism visible. Check algebra or executable examples with available tools when that resolves uncertainty; source inspection alone does not check a newly invented example.

Stop preparing when the foundations and central mechanism are supported, consequential assumptions or disagreements are understood, and there is a usable route and example from the learner's starting point. More links without better coverage are not more preparation. Do not browse every prerequisite in the field or front-load a long literature survey.

For a quick explanation, check the claims that need verification rather than doing a full preparation pass. For review, do preparatory checking without revealing answers before retrieval. During diagnosis, verify an uncertain answer key before asking the item. If the goal remains unclear, ask the one needed clarification before broad research.

Reuse checked sources and concise findings in the current conversation. Search again when the route introduces an unsupported claim, the learner challenges a claim, sources disagree, or current/version-specific behavior matters. On recovery, inspect only source records or passages relevant to the next step; do not reload a full research transcript. Do not add research-only checkpoint calls.

If browsing or the required text is unavailable, say briefly what could not be checked. Narrow the explanation to supported material or label a tentative account; do not invent a source, claim verification, or teach an unresolved factual assertion as a secure foundation. Honor an explicit no-browsing request with the same honest scope. Links discovered but not inspected are optional further reading, never evidence.

## Inline citations

Place a descriptive Markdown link immediately after the sentence or short paragraph it supports: `[Author, section or figure](actual-inspected-url)`. Link to the relevant section, figure, or page when a real locator is available; otherwise include the locator in the link label. Never guess anchors or URLs. For local supplied material, use an accessible note/file link and a precise locator.

Cite definitions or mechanisms drawn from a source when first established, non-obvious sourced results, empirical or numerical claims, historical attribution, disputed claims, and current/version-specific behavior. Re-cite when a later claim has different support or attribution would otherwise be ambiguous. Routine algebra derived openly from cited premises, learner feedback, and clearly labeled original examples do not need a citation on every line. Mark your own inference as an inference and identify the supporting premises.

The linked passage must support the actual claim and its qualifications. An authoritative homepage or a bibliography at lesson end is not a substitute for inline support. If sources disagree, state the disagreement and relevant assumptions instead of presenting a blended consensus. Keep citations out of answer options; delay answer-revealing citations and figures until feedback for closed-note diagnostics or review. A source-analysis exercise may include its source, but must not be scored as unaided recall.

Keep the explanation self-contained. External reading and diagrams supplement the lesson; they must not become mandatory homework to follow the next step.

## Source records

Required metadata:

- `citekey`
- `title`
- `author_or_organization`
- `year_or_date`
- at least one of `url`, `doi`, or `local_path`
- `source_type`
- `verification_status`
- `retrieval_date`
- `precise_locator`
- `claims_supported`

Statuses are `verified`, `metadata-only`, `user-provided`, `unverified`, and `rejected`. The CLI permits only `verified` and `user-provided` sources to have non-empty `claims_supported` or support a finish payload. That schema permission is not proof of truth: inspect the relevant passage, distinguish what supplied material claims from established fact, and corroborate questionable claims before adopting them as foundations. `metadata-only` can identify a work but cannot support its claims.

Verification means the relevant primary text or authoritative page was opened and checked at the precise locator. Record uncertainty and disagreements rather than blending them away. Prefer primary sources, official documentation, standards, and high-quality reviews appropriate to the claim.

The CLI deduplicates normalized DOI, URL, and local path. Reuse its returned citekey. Do not cite a result snippet, a source you did not inspect, or a source note merely copied from another lesson without verifying its status and locator.

Within an active lesson, always pass its exact Codex `session_id` to `learnctl source --session-id ...`. This explicitly attaches the resulting citekey to that lesson and updates its Sources section. Source creation without a session selector is allowed only for maintaining the global source library; it is not automatically associated with a lesson.

Register a used source before first citing it in the lesson. Reuse the returned citekey without another call for the same recorded usage. Submit an update when a new supported claim, locator, or verification observation is actually needed; group claims from the same inspected passage. Record sources that materially inform the route as well as the explanation. Do not register every search result. Keep extracted findings concise in `claims_supported`; do not copy full passages or add schema fields. Use direct inline source links in teaching prose and the recorded citekeys in lesson/topic provenance.
