# learn

[![video](assets/thumbnail.png)](https://www.youtube.com/watch?v=kzcI5F4tGiU)

amosblomqvist's AI learning system from [How I Use AI to Learn Things](https://www.youtube.com/watch?v=kzcI5F4tGiU), along with a separate Codex + Obsidian implementation. The original Pi files are preserved under [pi/](pi/README.md).

See [BUG_HISTORY.md](BUG_HISTORY.md) for known issues, fixes, and remaining limitations.

## Repository layout

```text
pi/                    Original Pi agents, extensions, and skills
.agents/skills/learn/  Codex learner skill, references, and Python controller
codex/                 Codex installer, uninstaller, and example configuration
vault-template/        Codex learner's Obsidian starter files
tests/                 Codex learner tests and manual GUI checklist
docs/agents/           Task-specific repository development guidance
assets/                Shared README assets
```

The former root `agents/`, `extensions/`, and `skills/` directories now live under `pi/`. For Pi setup and existing `.pi` links, see the [Pi configuration guide](pi/README.md). Codex installation commands, the installed `~/.agents/skills/learn` link, hooks, and vault paths are unchanged; this relocation does not require a Codex reinstall.

## Codex + Obsidian version

This version provides stateful lessons in Codex and mirrors the conversation into Markdown notes in Obsidian. It uses a skill, two Codex hooks, Obsidian's official CLI, and a Python standard-library controller—no custom Obsidian plugin, database, or MCP server.

```text
Codex conversation
  -> $learn skill
  -> UserPromptSubmit + Stop hooks
  -> Learning/_system/lessons/<lesson_id>.json
  -> deterministic Learning/Sessions/*.md rendering
  -> optional Obsidian navigation after persistence
```

The canonical JSON record owns lesson identity, exact conversation binding, message Markdown, lifecycle, checkpoints, source usage, and assessment data. Session Markdown is a recoverable view of that record. Topic Markdown is likewise derived from durable topic baselines plus lesson/review assessments. Routine hooks make no model calls and return no transcript to model context.

### Install

Requirements: Python 3 and an existing writable Obsidian vault. Verified opening and follow require Obsidian 1.12.7+ with **Settings → General → Command line interface** enabled. A URI can request launch when the CLI is unavailable, but that result remains explicitly unverified.

```bash
python3 codex/install.py --vault "/absolute/path/to/your/Obsidian vault"
```

The installer creates starter files under `Learning/`, links the skill into `~/.agents/skills/learn`, merges the Codex hooks, writes local configuration, and runs a health check. It is safe to rerun and does not overwrite existing notes or templates.

By default, notes open automatically. A configured 50/50 desktop split is available as a separate optional action on macOS. Common options:

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
5. Run `learnctl doctor` and inspect its Obsidian CLI capability result.
6. For optional window tiling on macOS, grant Accessibility permission to the app hosting Codex, such as your terminal, VS Code, or the Codex desktop app.

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
python3 ~/.agents/skills/learn/scripts/learnctl.py pause --session-id <conversation-id>
python3 ~/.agents/skills/learn/scripts/learnctl.py resume --lesson-id <lesson-uuid>
python3 ~/.agents/skills/learn/scripts/learnctl.py resume --topic <topic-title-or-slug>
python3 ~/.agents/skills/learn/scripts/learnctl.py open --session-id <conversation-id>
python3 ~/.agents/skills/learn/scripts/learnctl.py layout --session-id <conversation-id>
python3 ~/.agents/skills/learn/scripts/learnctl.py repair --lesson-id <lesson-uuid>
python3 ~/.agents/skills/learn/scripts/learnctl.py validate
python3 ~/.agents/skills/learn/scripts/learnctl.py doctor
```

`cleanup` previews disposable pending and stale-binding state; add `--apply` only after reviewing it. It does not delete lesson notes or durable learning state.

After GUI integration changes, run the host-specific [real Obsidian smoke checklist](tests/manual_obsidian_smoke.md) from both VS Code and a terminal-hosted Codex CLI session. Mocked CLI or AppleScript results are not an end-to-end pass.

### Notes, sources, and privacy

Session notes, canonical lessons, topic state, durable review assessments, and source records stay inside the configured `Learning` folder. `lesson_id` identifies one immutable lesson, `session_id` is the exact Codex conversation attachment, and `turn_id` is the runtime turn identity. Each source used in a lesson is recorded under `Learning/Sources`; only verified or user-provided sources may support factual claims.

Hooks log only user and final assistant text. They do not record tool calls, shell output, search traces, internal reasoning, or system messages. Existing notes outside `Learning` are untouched, and content outside managed regions in topic notes is preserved.

### Troubleshooting

- **No pending Codex prompt:** The conversation ID resolved, but Learn has no captured initiating prompt. New lessons accept a leading `$learn` token or the IDE's linked form `[$learn](.../learn/SKILL.md)`; quoted examples and mentions later in prose do not activate logging. Versions before the IDE-link fix silently rejected linked invocations. After updating the hook script, submit a fresh explicit invocation; rerunning `start` alone cannot recreate an uncaptured prompt. If it still fails, verify that `UserPromptSubmit` and `Stop` are enabled and trusted in the failing Codex runtime and use the intended configuration. `doctor` checks configured files, not actual hook delivery.
- **Note does not open:** Read the returned launch, readiness, exact-vault, and note-verification stages. Enable the official CLI if unavailable, then run `learnctl open --session-id ...` to retry.
- **Windows do not tile:** Opening does not tile. Run `learnctl layout --session-id ...`; confirm `window_layout` is `desktop-split` and grant macOS Accessibility permission to the app hosting Codex.
- **Vault write failure:** Add the vault's absolute path to the active Codex writable roots.
- **Interrupted lesson:** Invoke `$learn` again in the same Codex session.
- **Paused lesson:** Use `learnctl resume --topic <title-or-slug>` when exactly one paused lesson matches, or select it with `--lesson-id <lesson-uuid>` when multiple lessons match.
- **Stale generated Markdown:** Run `learnctl repair --lesson-id <lesson-uuid>`; canonical messages remain durable.
- **Abandoned lesson:** Run `learnctl abort --session-id <id>`; the note is retained.
- **Validation failure:** Run `learnctl validate` for the exact failing file or invariant.

### Uninstall

```bash
python3 codex/uninstall.py
```

This removes the installed skill link, hook commands, and local Learn configuration. It does not delete notes, learning state, backups, or Codex sandbox configuration.
