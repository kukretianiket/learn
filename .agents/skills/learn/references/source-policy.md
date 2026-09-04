# Source policy

Create one source record only for a source actually used. Search-result snippets are discovery aids, not verification.

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

Statuses are `verified`, `metadata-only`, `user-provided`, `unverified`, and `rejected`. Only `verified` and `user-provided` sources may have non-empty `claims_supported` or be cited as factual support at finish. `metadata-only` can identify a work but cannot support its claims.

Verification means the relevant primary text or authoritative page was opened and checked at the precise locator. Record uncertainty and disagreements rather than blending them away. Prefer primary sources, official documentation, standards, and high-quality reviews appropriate to the claim.

The CLI deduplicates normalized DOI, URL, and local path. Reuse its returned citekey. Do not cite a result snippet, a source you did not inspect, or a source note merely copied from another lesson without verifying its status and locator.

Within an active lesson, always pass its exact Codex `session_id` to `learnctl source --session-id ...`. This explicitly attaches the resulting citekey to that lesson and updates its Sources section. Source creation without a session selector is allowed only for maintaining the global source library; it is not automatically associated with a lesson.
