# Preserved Pi configuration

This directory contains the original Pi learning system from amosblomqvist's [How I Use AI to Learn Things](https://www.youtube.com/watch?v=kzcI5F4tGiU). Its agent, extension, and skill files were moved here without content changes. The separate Codex + Obsidian learner is documented in the [repository README](../README.md).

## Contents

| Path | Purpose |
| --- | --- |
| [skills/teach/](skills/teach/SKILL.md) | Teaching philosophy and process |
| [skills/visualize/](skills/visualize/SKILL.md) | Diagram selection and maker workflow |
| [agents/](agents/) | Researcher, SVG maker, and Mermaid maker definitions |
| [extensions/ask-user-question.ts](extensions/ask-user-question.ts) | Question popups |
| [extensions/quiz.ts](extensions/quiz.ts) | Graded questions and feedback |
| [extensions/md-log.ts](extensions/md-log.ts) | Session-to-Markdown logging |
| [extensions/visual-tools/](extensions/visual-tools/) | Tools used by visualization makers |

## Use as a Pi project configuration

Use this directory as the learning project's `.pi` directory. For a project that does not already have `.pi`, create a symlink using absolute paths:

```bash
ln -s "/absolute/path/to/learn/pi" "/absolute/path/to/learning-project/.pi"
```

Run Pi from the learning project's root. If the project already has a `.pi` configuration, merge only the desired files into its matching `agents/`, `extensions/`, and `skills/` directories instead of replacing the whole configuration.

The repository root is no longer itself a Pi configuration. Update any existing `.pi` symlink that points at the repository root to point at its `pi/` subdirectory. If the repository was cloned directly into a project's `.pi` directory, keep the checkout separately and point the project's `.pi` at the checkout's `pi/` directory. Individually configured extension and skill paths likewise gain the `pi/` prefix.

References to `.pi/agents/` inside the preserved skills describe the installed project layout and remain correct. Visual outputs still go to `viz/` under Pi's working directory; run from the intended learning project inside the Obsidian vault.

## Existing runtime requirements

The preserved configuration expects Pi, a compatible subagent extension for the researcher and visual makers, and the bundled question-popup implementation. The visual tools integrate with `interactive-subagents`; their Mermaid renderer uses extension-local `node_modules` and installed Chrome, while SVG rendering uses `rsvg-convert` or ImageMagick.

The [visual-tools package manifest](extensions/visual-tools/package.json) and lockfile retain the upstream author's absolute local Pi development dependency. Adapt that dependency to the Pi installation being used before installing this package's dependencies. Relocating the files does not make that upstream environment-specific setup portable.

Codex does not load or install these dependencies. Its skill stays in `.agents/skills/learn/` at the repository root, and its installer and hooks retain their existing paths.
