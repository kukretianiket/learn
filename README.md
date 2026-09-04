# learn

[![video](assets/thumbnail.png)](https://www.youtube.com/watch?v=kzcI5F4tGiU)

Amos Blomqvist's original AI learning system from [How I Use AI to Learn Things](https://www.youtube.com/watch?v=kzcI5F4tGiU), plus a separate Codex + Obsidian implementation. The upstream Pi files and attribution are preserved; the Codex implementation lives beside them.

The Codex implementation's reported defects, root causes, corrections, and remaining limitations are recorded in [BUG_HISTORY.md](BUG_HISTORY.md).

## Original Pi usage

The original repository is a `.pi` directory. From a learning project's root:

```bash
git clone https://github.com/amosblomqvist/learn .pi
```

Open Pi in that directory. The upstream pieces remain unchanged:

- `skills/teach/` — teaching philosophy and process
- `skills/visualize/` — selective visual generation
- `extensions/ask-user-question.ts` — interactive questions
- `extensions/quiz.ts` — graded diagnostic questions
- `extensions/md-log.ts` — Markdown session mirroring
- `extensions/visual-tools/` and `agents/` — research and visual subagents

Pi requires [pi](https://github.com/earendil-works/pi), a compatible subagent implementation such as [pi-interactive-subagents](https://github.com/amosblomqvist/pi-interactive-subagents), and the bundled `ask-user-question` implementation. It can run without subagents, with research verification and generated visuals omitted.

## Codex + Obsidian usage

The Codex version is deliberately smaller. Keep Codex and Obsidian side by side and invoke `$learn` only when you want a stateful lesson. Codex teaches in the normal conversation; hooks mirror only user and assistant text into a live Markdown note. There is no Obsidian plugin, Codex panel, MCP server, database, custom quiz UI, embeddings, Anki integration, or specialist agent.

### Architecture

```text
Codex conversation
  -> explicit $learn skill
  -> .agents/skills/learn/scripts/learnctl.py
  -> UserPromptSubmit + Stop hooks
  -> Learning/Sessions/*.md in the Obsidian vault
  -> Learning/_system topic, source, and review JSON
```

`learnctl.py` is a Python 3 standard-library CLI. The hook never reads Codex transcript files. Per-session active state permits concurrent Codex conversations while allowing only one live lesson note in each conversation.

### Installation

Python 3 and an existing writable Obsidian vault directory are required. From this repository:

```bash
python3 codex/install.py --vault "/absolute/path/to/your/Obsidian vault"
```

Session notes open automatically and use a 50/50 desktop split by default on macOS, with Codex on the right. Useful optional flags are `--no-open-notes`, `--window-layout none`, `--codex-side left`, `--vault-name "My Vault"`, `--learning-folder Learning`, and `--replace` when you intentionally want to move aside an existing `~/.agents/skills/learn`.

The installer:

- adds only missing folders and starter files under `Learning/`;
- symlinks the repository skill into `~/.agents/skills/learn`;
- backs up and merges `~/.codex/hooks.json` instead of overwriting it;
- writes `~/.config/learn-codex/config.json`;
- safely adds a simple legacy workspace-write root or prints instructions for an existing/beta permission configuration;
- runs `learnctl doctor`.

It is safe to rerun. Existing Learning notes and templates are not overwritten.

### One-time Codex configuration

After installation:

1. Read the installer's sandbox message. If it printed instructions, add the vault as a writable root in the active Codex permission profile. Do not combine a beta permission profile with legacy `[sandbox_workspace_write]` keys.
2. Restart Codex.
3. Open Codex Settings → Hooks, inspect both commands, and trust them only after confirming they are exactly `python3 ~/.agents/skills/learn/scripts/learnctl.py hook`.
4. Keep the vault open in Obsidian. Automatic opening prefers the official `obsidian` CLI when available and falls back on macOS to a correctly encoded `obsidian://` URI.
5. For automatic desktop tiling, enable the application that hosts Codex in System Settings → Privacy & Security → Accessibility. For Codex CLI, this is the terminal application you launch it from (for example Terminal, iTerm, Ghostty, Warp, or WezTerm), not the `codex` executable. For other interfaces it may be Visual Studio Code or the Codex desktop app. Approve macOS Automation prompts for System Events or Obsidian when they appear.

The installer never edits an existing `config.toml` unless it first backs it up and recognizes a simple compatible legacy configuration. `codex/config.example.toml` shows the legacy form.

### Using `$learn`

Invocation is always explicit. A good first test prompt is:

```text
$learn Explain why the derivative becomes a gradient for vector inputs. Start from what I know, ask one question at a time, and aim for a 20-minute lesson.
```

The skill checks saved topic state before diagnosing, begins with one unscored subjective self-map, then asks 4–6 high-information knowledge questions and treats “I don't know” distinctly. It stops at four only when the last three answers independently converge on the same missing foundation; mixed or boundary evidence continues to five or six. `learnctl` samples single- or multi-correct answer positions from operating-system randomness and retains one immutable draw per user turn; the language model does not choose or reroll its answer labels. Repeats are possible, so there is no balancing cycle to exploit. Distractors must also be parallel in style so the correct answer cannot be inferred from length, qualification, or tone. MCQs locate misconceptions but never count as sufficient mastery evidence. Free recall, explanation, application, transfer, and delayed reviews drive the durable state.

Useful local commands:

```bash
python3 ~/.agents/skills/learn/scripts/learnctl.py status
python3 ~/.agents/skills/learn/scripts/learnctl.py due
python3 ~/.agents/skills/learn/scripts/learnctl.py review --topic gradient --score 2
python3 ~/.agents/skills/learn/scripts/learnctl.py cleanup --json-output
python3 ~/.agents/skills/learn/scripts/learnctl.py cleanup --apply
python3 ~/.agents/skills/learn/scripts/learnctl.py validate
python3 ~/.agents/skills/learn/scripts/learnctl.py doctor
```

`Review Queue.base` is a convenience view. `due` and `review` remain fully usable when Obsidian Bases is disabled.

The full Codex `session_id` is the unique operational identifier for the active learning session. New lesson notes include it in frontmatter; `status` prints it for active sessions. If the same Codex conversation starts another lesson later, it reuses that `session_id`, while the session-note path uniquely identifies the durable lesson record. The filename's short hash is not the full session identifier.

`cleanup` previews disposable `_system` records by default. After reviewing the output, add `--apply` to remove them. It keeps active-session deduplication records and all lesson notes, topic/source state, rendered notes, and assets. Inactive pending prompts are retained for seven days unless `--pending-days` is supplied.

### Example modes

- Learn: `$learn Teach me backpropagation from the chain rule. Use guided discovery and check transfer.`
- Explain: `$learn In explain mode, give me a quick motivated explanation of eigenvectors without forcing a formal lesson plan.`
- Solve: `$learn In solve mode, help me solve this recurrence. Do not reveal the full solution until I attempt it or use the hint ladder.`
- Review: `$learn Review my gradient topic using closed-note recall, one prompt at a time.`
- Research: `$learn Research and teach me the current WebGPU execution model. Verify every factual source you use.`

Diagnostics, lesson starts, and later knowledge evaluations have explicit visible boundaries. Substantial lessons include a compact vertical Mermaid concept map immediately after `## Lesson begins`; the main chain is top-to-bottom, while true cross-links, detours, and feedback loops are retained instead of forcing a DAG. Quick explanations avoid unnecessary ceremony. Math follows a strict Obsidian contract: every mathematical token is dollar-delimited, inline math stays on one line, display delimiters occupy their own lines, and common malformed LaTeX is rejected by validation.

The live lesson is opened in Obsidian Reading View. After each assistant response is logged, Learn navigates to that Tutor heading so the newest output remains visible, then restores focus to the originating Codex window. This uses the existing Stop hook and requires no Obsidian plugin.

### Source guarantees

Each source actually used gets one note in `Learning/Sources` plus machine state in `Learning/_system/sources`. During a lesson, `learnctl source --session-id ID ...` explicitly attaches the citekey to the live note; source logging is not implicitly associated with whichever lesson happens to be active. Records include a citekey, author or organization, date, URL/DOI/local path, type, verification status, retrieval date, precise locator, and claims supported.

Only `verified` and `user-provided` sources may support factual claims. Search-result snippets and metadata-only records do not count as verification. URL, DOI, and local-path normalization prevents duplicate source records, and `learnctl validate` rejects missing source notes, duplicate citekeys, incomplete metadata, and unsupported citation placeholders.

### Privacy and local files

Configuration is stored in `~/.config/learn-codex/config.json`. Learning state and notes remain inside the configured `Learning` folder. The hooks log only user text and final assistant text—never tool calls, shell output, search traces, internal reasoning, system/developer messages, or transcript files.

The utility does not modify existing notes outside `Learning`. Topic notes have a managed region between `<!-- LEARN:BEGIN -->` and `<!-- LEARN:END -->`; everything outside it, especially `## My notes`, is preserved.

### Troubleshooting

- `No pending Codex prompt found`: restart Codex after installing/trusting hooks, then invoke `$learn` again.
- Note not opening: enable it with `learnctl configure --vault "/absolute/path/to/vault" --open-notes` (add `--vault-name "My Vault"` if desired), then run `learnctl doctor`. The official Obsidian CLI is tried first; on macOS without that CLI, a sandboxed start reports `approval-required` and the skill asks Codex to approve one narrowly scoped `learnctl open` command. This is a Codex sandbox gate, not a macOS privacy denial. Outside the sandbox, a failed CLI launch falls back to an encoded `obsidian://` URI. Whether Obsidian is already open does not affect vault writes or this URI workflow.
- Windows not tiling: confirm `window_layout` is `desktop-split`, then enable the current Codex host in System Settings → Privacy & Security → Accessibility. When using Codex CLI, grant access to its terminal application rather than the `codex` executable. A successful `learnctl open` reports `layout.status` as `tiled`; note opening remains usable if tiling fails.
- Vault write failure: add the absolute vault path to the active Codex writable roots, without mixing permission configuration generations.
- Interrupted lesson: invoke `$learn` again in the same Codex session; `start` recovers its active note.
- Stale lesson you intentionally abandoned: run `learnctl abort --session-id <id>`. This retains the note.
- Validation failure: run `learnctl validate`; it reports the exact Markdown, state, asset, source, or link invariant that failed.

### Uninstalling

From this repository:

```bash
python3 codex/uninstall.py
```

The uninstaller removes only the installed symlink, the two exact hook commands, and `~/.config/learn-codex/config.json`. It never deletes Learning notes, topic/source state, source libraries, hook backups, or Codex sandbox configuration.
