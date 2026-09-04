# learn

[![video](assets/thumbnail.png)](https://www.youtube.com/watch?v=kzcI5F4tGiU)

Amos Blomqvist's AI learning system from [How I Use AI to Learn Things](https://www.youtube.com/watch?v=kzcI5F4tGiU), along with a separate Codex + Obsidian implementation. The original Pi files are preserved; the Codex version lives beside them.

See [BUG_HISTORY.md](BUG_HISTORY.md) for known issues, fixes, and remaining limitations.

## Original Pi version

Clone this repository as `.pi` in the root of a learning project:

```bash
git clone https://github.com/amosblomqvist/learn .pi
```

Then open Pi from that directory. The original implementation includes:

- `skills/teach/` — teaching process
- `skills/visualize/` — visual generation
- `extensions/` — questions, quizzes, session logging, and visual tools
- `agents/` — research and visual subagents

It requires [Pi](https://github.com/earendil-works/pi), the bundled question extension, and optionally a compatible subagent implementation such as [pi-interactive-subagents](https://github.com/amosblomqvist/pi-interactive-subagents). Without subagents, research verification and generated visuals are unavailable.

## Codex + Obsidian version

This version provides stateful lessons in Codex and mirrors the conversation into Markdown notes in Obsidian. It uses a skill, two Codex hooks, and a Python standard-library CLI—no Obsidian plugin, database, or MCP server.

```text
Codex conversation
  -> $learn skill
  -> UserPromptSubmit + Stop hooks
  -> Learning/Sessions/*.md
  -> Learning/_system state
```

### Install

Requirements: Python 3 and an existing writable Obsidian vault.

```bash
python3 codex/install.py --vault "/absolute/path/to/your/Obsidian vault"
```

The installer creates starter files under `Learning/`, links the skill into `~/.agents/skills/learn`, merges the Codex hooks, writes local configuration, and runs a health check. It is safe to rerun and does not overwrite existing notes or templates.

By default, notes open automatically and Codex and Obsidian use a 50/50 desktop split on macOS. Common options:

```bash
--no-open-notes
--window-layout none
--codex-side left
--vault-name "My Vault"
--learning-folder Learning
--replace
```

After installation:

1. Follow any sandbox instructions printed by the installer so Codex can write to the vault.
2. Restart Codex.
3. In Codex Settings → Hooks, verify and trust both commands: `python3 ~/.agents/skills/learn/scripts/learnctl.py hook`.
4. Keep the vault open in Obsidian.
5. For automatic window tiling on macOS, grant Accessibility permission to the app hosting Codex, such as your terminal, VS Code, or the Codex desktop app.

The installer backs up compatible configuration before editing it. See `codex/config.example.toml` for the legacy sandbox configuration format.

### Use

Invoke the skill explicitly:

```text
$learn Explain why the derivative becomes a gradient for vector inputs. Start from what I know, ask one question at a time, and aim for a 20-minute lesson.
```

Available modes include:

- Learn: `$learn Teach me backpropagation from the chain rule.`
- Explain: `$learn In explain mode, give me a quick explanation of eigenvectors.`
- Solve: `$learn In solve mode, help me solve this recurrence using hints.`
- Review: `$learn Review my gradient topic using closed-note recall.`
- Research: `$learn Research and teach me the current WebGPU execution model.`

Lessons adapt to saved knowledge, diagnose gaps, and favor recall, explanation, application, and delayed review over multiple-choice performance. Substantial lessons include a compact concept map; quick explanations skip unnecessary ceremony.

Useful commands:

```bash
python3 ~/.agents/skills/learn/scripts/learnctl.py status
python3 ~/.agents/skills/learn/scripts/learnctl.py due
python3 ~/.agents/skills/learn/scripts/learnctl.py review --topic gradient --score 2
python3 ~/.agents/skills/learn/scripts/learnctl.py validate
python3 ~/.agents/skills/learn/scripts/learnctl.py doctor
```

`cleanup` previews disposable hook state; add `--apply` only after reviewing it. It does not delete lesson notes or durable learning state.

### Notes, sources, and privacy

Session notes, topic state, review history, and source records stay inside the configured `Learning` folder. Each source used in a lesson is recorded under `Learning/Sources`; only verified or user-provided sources may support factual claims.

Hooks log only user and final assistant text. They do not record tool calls, shell output, search traces, internal reasoning, or system messages. Existing notes outside `Learning` are untouched, and content outside managed regions in topic notes is preserved.

### Troubleshooting

- **No pending Codex prompt:** Restart Codex after installing and trusting the hooks, then invoke `$learn` again.
- **Note does not open:** Run `learnctl doctor`. If needed, re-enable opening with `learnctl configure --vault "/path/to/vault" --open-notes`.
- **Windows do not tile:** Confirm `window_layout` is `desktop-split` and grant macOS Accessibility permission to the app hosting Codex.
- **Vault write failure:** Add the vault's absolute path to the active Codex writable roots.
- **Interrupted lesson:** Invoke `$learn` again in the same Codex session.
- **Abandoned lesson:** Run `learnctl abort --session-id <id>`; the note is retained.
- **Validation failure:** Run `learnctl validate` for the exact failing file or invariant.

### Uninstall

```bash
python3 codex/uninstall.py
```

This removes the installed skill link, hook commands, and local Learn configuration. It does not delete notes, learning state, backups, or Codex sandbox configuration.
