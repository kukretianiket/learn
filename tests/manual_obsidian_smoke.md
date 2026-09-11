# Real Obsidian smoke test

Run this checklist after material adapter changes. Unit tests do not substitute for it.

## Prerequisites

- Use Obsidian 1.12.7 or later and enable **Settings → General → Command line interface**.
- Run `learnctl doctor`; the `obsidian-cli` check must report `available` with `open`, `eval`, and `vault` capabilities.
- Start an explicit Learn lesson in the host being tested and produce at least one Tutor response containing a long section, one image or Mermaid diagram, and a Unicode character.

## VS Code host

1. Put the lesson pane in Live Preview, then run `learnctl open --session-id <current conversation id>` from the same Codex conversation.
2. Require `launch.status` to confirm CLI launch/readiness and `note_open.status` to be `verified`.
3. Confirm the exact configured vault and lesson note are active, Reading View is selected, the start of the newest Tutor response is visible after the delayed asset renders, and focus returns to VS Code.
4. Repeat from Source mode and after creating two Tutor messages within the same clock second.

## Codex CLI host

1. Repeat the VS Code procedure from a real terminal-hosted Codex CLI conversation.
2. Confirm focus returns to the originating terminal window and exact TTY, not another terminal window.
3. Trigger one synthetic navigation failure, then replay the identical Stop event or submit the next learner prompt. Confirm operational health retains the failure until the retry verifies the newest message and that the transcript has no duplicate.
4. With two active lessons, trigger overlapping Stop events and confirm each session retains its own pending request while final navigation never moves a pane to an older message.

Record the Obsidian version, CLI version, Codex host, result JSON, and observed pane/focus state. A URI-only result is `unverified` and does not pass this smoke test.
