# Development

Read when changing implementation or reviewing code. For product and installation context, consult [README.md](../../README.md). For a previously encountered failure, read the matching entry in [BUG_HISTORY.md](../../BUG_HISTORY.md); historical fixes are context, not instructions to restore an older design.

## Limit code growth

- Fix the owning code path instead of layering a workaround on its callers. Keep unrelated cleanup out of the diff.
- When replacing an implementation, remove obsolete branches, helpers, and tests that exist only for those branches. Preserve supported behavior and its regression coverage. Do not restore retired migrations, event sidecars, or legacy state formats for hypothetical compatibility.
- Add an abstraction only to simplify a concrete current code path or share an existing responsibility. Name that use in the change explanation; a possible future caller is insufficient.
- Follow conventions in the surrounding Python code. Keep comments concise and focused on non-obvious invariants and tradeoffs.

## Codex architecture

The preserved Pi implementation lives under [pi/](../../pi/README.md): `pi/agents/`, `pi/extensions/`, and `pi/skills/`. Codex uses `.agents/skills/learn/`, `codex/`, and `vault-template/`; the two implementations do not load each other's skills or extensions. Apply the following runtime constraints to Codex changes, not to the preserved Pi dependency stack.

- Keep the Codex runtime Python standard-library-only. Preserve the file-based architecture: no database, Obsidian plugin, MCP server, background watcher, or mandatory rendering stack unless the task explicitly changes that design.
- Put deterministic persistence, validation, identity, and randomness in Python. Put pedagogical judgment in the skill and references; do not add a parser or state machine to claim it can verify teaching quality.
- Validate external inputs and persisted invariants. Do not silently turn malformed state or failed operations into success. Keep best-effort GUI failure separate from persistence failure.

For changes involving stored data or hooks, read [State invariants](state.md). Use [Testing and verification](testing.md) for the relevant checks and final diff review.
