#!/usr/bin/env python3
"""Remove only the Codex integration installed by learn-codex."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_SOURCE = REPO_ROOT / ".agents" / "skills" / "learn"
HOOK_COMMAND = "python3 ~/.agents/skills/learn/scripts/learnctl.py hook"
HOOK_EVENTS = ("UserPromptSubmit", "Stop")


def atomic_write(path: Path, text: str) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temp), str(path))
    finally:
        if temp.exists():
            temp.unlink()


def remove_hooks(path: Path) -> int:
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in HOOK_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                kept_groups.append(group)
                continue
            kept_handlers = []
            for handler in group["hooks"]:
                if (
                    isinstance(handler, dict)
                    and handler.get("type") == "command"
                    and handler.get("command") == HOOK_COMMAND
                ):
                    removed += 1
                else:
                    kept_handlers.append(handler)
            if kept_handlers:
                updated = dict(group)
                updated["hooks"] = kept_handlers
                kept_groups.append(updated)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    if removed:
        atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return removed


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    skill = Path.home() / ".agents" / "skills" / "learn"
    skill_removed = False
    if skill.is_symlink() and skill.resolve() == SKILL_SOURCE.resolve():
        skill.unlink()
        skill_removed = True
    hooks = Path.home() / ".codex" / "hooks.json"
    try:
        removed_hooks = remove_hooks(hooks)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"uninstall: could not safely update {hooks}: {exc}")
        return 2
    config = Path.home() / ".config" / "learn-codex" / "config.json"
    config_removed = False
    if config.is_file():
        config.unlink()
        config_removed = True
    print(f"Learn skill symlink removed: {skill_removed}")
    print(f"Exact hook commands removed: {removed_hooks}")
    print(f"Learn configuration removed: {config_removed}")
    print("Learning notes, source notes, state files, hook backups, and Codex sandbox configuration were preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
