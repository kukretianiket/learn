# Instruction maintenance

Read when updating `AGENTS.md` or its linked guidance.

- Keep the root to the project description, any repository-wide package-manager or non-standard build/typecheck commands, instructions applicable to every task, and links with explicit reading triggers. Put task-specific rules in the corresponding file under `docs/agents/`.
- Add a rule when a recurring mistake or costly non-obvious constraint warrants it. State the action it changes and link to evidence when available.
- Put incident details in [BUG_HISTORY.md](../../BUG_HISTORY.md), product contracts in their [existing references](product-contracts.md), and enforceable behavior in tests. Do not copy test counts, full schemas, or the bug archive into agent guidance.
- Update or remove stale rules when the design changes instead of continually appending exceptions.
- When moving guidance, preserve its scope and exceptions, check relative links from each file's directory, and ask the user to resolve conflicting instructions before choosing one.
