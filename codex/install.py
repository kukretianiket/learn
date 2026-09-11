#!/usr/bin/env python3
"""Install the Codex + Obsidian learning system without altering upstream Pi files."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_SOURCE = REPO_ROOT / ".agents" / "skills" / "learn"
VAULT_TEMPLATE = REPO_ROOT / "vault-template" / "Learning"
HOOK_COMMAND = "python3 ~/.agents/skills/learn/scripts/learnctl.py hook"
HOOK_EVENTS = ("UserPromptSubmit", "Stop")


class InstallError(RuntimeError):
    pass


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = path.with_name(f"{path.name}.learn-codex-backup-{stamp}")
    shutil.copy2(str(path), str(target))
    return target


def validate_vault(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise InstallError("--vault must be an absolute path")
    if not path.exists() or not path.is_dir():
        raise InstallError(f"Vault path is not an existing directory: {path}")
    if not os.access(path, os.W_OK):
        raise InstallError(f"Vault path is not writable: {path}")
    return path.resolve()


def validate_learning_folder(raw: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise InstallError("--learning-folder must be a non-empty relative path")
    value = raw.strip()
    path = Path(value)
    if (
        path.is_absolute()
        or "\\" in value
        or any(ord(character) < 32 for character in value)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise InstallError("--learning-folder must be a safe relative path inside the vault")
    return path.as_posix()


def validate_review_intervals(raw: str) -> list[int]:
    try:
        values = [int(item.strip()) for item in raw.split(",")]
    except (AttributeError, ValueError) as exc:
        raise InstallError("--review-intervals must be comma-separated positive integers") from exc
    if not values or any(value <= 0 for value in values) or values != sorted(set(values)):
        raise InstallError("--review-intervals must be unique positive integers in increasing order")
    return values


def require_inside(root: Path, target: Path, label: str) -> Path:
    resolved_root = root.resolve()
    resolved_target = target.resolve(strict=False)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise InstallError(f"{label} resolves outside {resolved_root}: {target}") from exc
    return resolved_target


def preflight_vault_template(vault: Path, learning_folder: str) -> Path:
    if not VAULT_TEMPLATE.is_dir():
        raise InstallError(f"Missing vault template: {VAULT_TEMPLATE}")
    destination = vault / learning_folder
    require_inside(vault, destination, "Learning destination")
    relatives = (
        "Sessions",
        "Topics",
        "Sources",
        "Assets",
        "_system",
        "_system/pending",
        "_system/active",
        "_system/lessons",
        "_system/reviews",
        "_system/topics",
        "_system/sources",
    )
    for relative in relatives:
        require_inside(destination, destination / relative, f"Template directory {relative}")
    for source in VAULT_TEMPLATE.iterdir():
        if source.is_file() and not source.name.startswith("."):
            require_inside(destination, destination / source.name, f"Template file {source.name}")
    return destination


def preflight_home_destinations() -> None:
    home = Path.home().resolve()
    for label, target in (
        ("Skill destination directory", home / ".agents" / "skills"),
        ("Hook configuration directory", home / ".codex"),
        ("Codex configuration directory", home / ".codex"),
        ("Learn configuration directory", home / ".config" / "learn-codex"),
    ):
        require_inside(home, target, label)


def install_vault_template(vault: Path, learning_folder: str) -> list[Path]:
    destination = vault / learning_folder
    created = []
    for relative in (
        "Sessions",
        "Topics",
        "Sources",
        "Assets",
        "_system",
        "_system/pending",
        "_system/active",
        "_system/lessons",
        "_system/reviews",
        "_system/topics",
        "_system/sources",
    ):
        target = destination / relative
        require_inside(destination, target, f"Template directory {relative}")
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            created.append(target)
    for source in VAULT_TEMPLATE.iterdir():
        if not source.is_file() or source.name.startswith("."):
            continue
        target = destination / source.name
        require_inside(destination, target, f"Template file {source.name}")
        if not target.exists():
            if source.name == "Review Queue.base" and learning_folder != "Learning":
                content = source.read_text(encoding="utf-8").replace("Learning/Topics", f"{learning_folder}/Topics")
                atomic_write(target, content)
            else:
                shutil.copy2(str(source), str(target))
            created.append(target)
    return created


def preflight_skill(replace: bool) -> None:
    destination = Path.home() / ".agents" / "skills" / "learn"
    if destination.is_symlink() and destination.resolve() == SKILL_SOURCE.resolve():
        return
    if (destination.exists() or destination.is_symlink()) and not replace:
        raise InstallError(
            f"Skill conflict at {destination}. Preserve it or rerun with --replace; nothing was replaced."
        )


def preflight_hooks(path: Path) -> None:
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"Cannot merge invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InstallError(f"Hook configuration must be a JSON object: {path}")
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError(f"The 'hooks' key in {path} must be an object")
    for event in HOOK_EVENTS:
        if event in hooks and not isinstance(hooks[event], list):
            raise InstallError(f"hooks.{event} must be an array")


def install_skill(replace: bool) -> tuple[Path, Path | None]:
    destination = Path.home() / ".agents" / "skills" / "learn"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() and destination.resolve() == SKILL_SOURCE.resolve():
        return destination, None
    replaced_backup = None
    if destination.exists() or destination.is_symlink():
        if not replace:
            raise InstallError(
                f"Skill conflict at {destination}. Preserve it or rerun with --replace; nothing was replaced."
            )
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        replaced_backup = destination.with_name(f"learn.before-learn-codex-{stamp}")
        destination.rename(replaced_backup)
    destination.symlink_to(SKILL_SOURCE, target_is_directory=True)
    return destination, replaced_backup


def contains_hook(group: object) -> bool:
    if not isinstance(group, dict):
        return False
    handlers = group.get("hooks")
    if not isinstance(handlers, list):
        return False
    return any(
        isinstance(handler, dict)
        and handler.get("type") == "command"
        and handler.get("command") == HOOK_COMMAND
        for handler in handlers
    )


def merge_hooks(path: Path) -> tuple[bool, Path | None]:
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InstallError(f"Cannot merge invalid JSON in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise InstallError(f"Hook configuration must be a JSON object: {path}")
    else:
        data = {}
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError(f"The 'hooks' key in {path} must be an object")
    changed = False
    for event in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise InstallError(f"hooks.{event} must be an array")
        if not any(contains_hook(group) for group in groups):
            groups.append(
                {
                    "matcher": "",
                    "hooks": [{"type": "command", "command": HOOK_COMMAND, "async": False}],
                }
            )
            changed = True
    if not changed:
        return False, None
    saved = backup(path)
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return True, saved


def toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def configured_learn_vault() -> Path | None:
    path = Path.home() / ".config" / "learn-codex" / "config.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = Path(data["vault_path"]).expanduser()
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return value.resolve() if value.is_absolute() else None


def configure_writable_root(vault: Path, previous_vault: Path | None = None) -> dict:
    """Edit only a simple compatible legacy config; otherwise return guidance."""
    path = Path.home() / ".codex" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    result = {"status": "unchanged", "backup": None, "message": ""}
    beta = bool(
        re.search(r"(?m)^\s*(?:permission_profile|sandbox_permissions)\s*=", text)
        or re.search(r"(?m)^\s*\[(?:permission|permissions|profiles\.)", text)
    )
    workspace_section = re.search(r"(?m)^\s*\[sandbox_workspace_write\]\s*$", text)
    sandbox_match = re.search(r'(?m)^\s*sandbox_mode\s*=\s*["\']([^"\']+)', text)
    sandbox_mode = sandbox_match.group(1) if sandbox_match else None
    snippet = f"[sandbox_workspace_write]\nwritable_roots = [{toml_quote(str(vault))}]"

    if beta:
        result["status"] = "printed"
        result["message"] = (
            "A beta permission profile is configured. No legacy sandbox keys were added. "
            f"Add {vault} as a writable root in that Codex permission profile."
        )
        return result
    if workspace_section:
        section_start = workspace_section.start()
        next_section = re.search(r"(?m)^\s*\[", text[workspace_section.end() :])
        section_end = workspace_section.end() + next_section.start() if next_section else len(text)
        section = text[section_start:section_end]
        roots_match = re.search(
            r"(?m)^(\s*writable_roots\s*=\s*)(\[[^\r\n]*\])(\s*(?:#.*)?)$",
            section,
        )
        try:
            roots = json.loads(roots_match.group(2)) if roots_match else None
        except json.JSONDecodeError:
            roots = None
        if not isinstance(roots, list) or not all(isinstance(item, str) for item in roots):
            result["status"] = "printed"
            result["message"] = (
                "The existing [sandbox_workspace_write] writable_roots array could not be merged safely. "
                f"Add {toml_quote(str(vault))} to it manually."
            )
            return result

        wanted = str(vault)
        previous = str(previous_vault) if previous_vault else None
        learn_owned_table = "# Added by learn-codex" in text[max(0, section_start - 120) : section_start]
        if previous == wanted and wanted not in roots and len(roots) == 1 and learn_owned_table:
            # Recover installations made before reconfiguration could update the
            # Learn-owned root after the JSON config had already changed.
            roots_to_merge = [wanted]
        else:
            roots_to_merge = roots
        updated_roots = []
        for item in roots_to_merge:
            if previous and previous != wanted and item == previous:
                if wanted not in updated_roots:
                    updated_roots.append(wanted)
            elif item not in updated_roots:
                updated_roots.append(item)
        if wanted not in updated_roots:
            updated_roots.append(wanted)
        if updated_roots == roots:
            result["message"] = f"Codex config already contains the vault writable root: {vault}"
            return result

        value_start = section_start + roots_match.start(2)
        value_end = section_start + roots_match.end(2)
        updated = text[:value_start] + json.dumps(updated_roots, ensure_ascii=False) + text[value_end:]
        saved = backup(path)
        atomic_write(path, updated)
        result.update(
            {
                "status": "updated",
                "backup": str(saved) if saved else None,
                "message": f"Updated the compatible Codex writable-root list for {vault}",
            }
        )
        return result
    if sandbox_mode and sandbox_mode != "workspace-write":
        result["status"] = "printed"
        result["message"] = (
            f"sandbox_mode is {sandbox_mode!r}; config was not changed. When appropriate, use workspace-write "
            f"and add:\n{snippet}"
        )
        return result

    saved = backup(path)
    updated = text
    if updated and not updated.endswith("\n"):
        updated += "\n"
    if updated:
        updated += "\n"
    updated += "# Added by learn-codex; remove this table manually if no longer needed.\n" + snippet + "\n"
    atomic_write(path, updated)
    result.update(
        {
            "status": "added",
            "backup": str(saved) if saved else None,
            "message": f"Added vault writable root to {path}",
        }
    )
    return result


def run_configure(vault: Path, args) -> None:
    command = [
        sys.executable,
        str(SKILL_SOURCE / "scripts" / "learnctl.py"),
        "configure",
        "--vault",
        str(vault),
        "--learning-folder",
        args.learning_folder,
        "--review-intervals",
        args.review_intervals,
    ]
    command.append("--open-notes" if args.open_notes else "--no-open-notes")
    command.extend(["--window-layout", args.window_layout, "--codex-side", args.codex_side])
    if args.vault_name:
        command.extend(["--vault-name", args.vault_name])
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise InstallError(result.stderr.strip() or "learnctl configure failed")


def run_doctor() -> str:
    result = subprocess.run(
        [sys.executable, str(SKILL_SOURCE / "scripts" / "learnctl.py"), "doctor"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise InstallError(result.stderr.strip() or "learnctl doctor failed")
    return result.stdout.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, help="absolute path to an existing Obsidian vault")
    parser.add_argument("--learning-folder", default="Learning")
    parser.add_argument("--replace", action="store_true", help="move aside an existing learn skill and install this one")
    opening = parser.add_mutually_exclusive_group()
    opening.add_argument("--open-notes", dest="open_notes", action="store_true")
    opening.add_argument("--no-open-notes", dest="open_notes", action="store_false")
    parser.set_defaults(open_notes=True)
    parser.add_argument("--vault-name")
    parser.add_argument("--review-intervals", default="1,3,7,14,30,60,120")
    parser.add_argument("--window-layout", choices=["none", "desktop-split"], default="desktop-split")
    parser.add_argument("--codex-side", choices=["left", "right"], default="right")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        vault = validate_vault(args.vault)
        args.learning_folder = validate_learning_folder(args.learning_folder)
        validate_review_intervals(args.review_intervals)
        preflight_vault_template(vault, args.learning_folder)
        preflight_home_destinations()
        if args.vault_name is not None and (
            not args.vault_name.strip() or any(ord(character) < 32 for character in args.vault_name)
        ):
            raise InstallError("--vault-name must be a non-empty single-line value")
        hooks_path = Path.home() / ".codex" / "hooks.json"
        preflight_skill(args.replace)
        preflight_hooks(hooks_path)
        previous_vault = configured_learn_vault()
        created = install_vault_template(vault, args.learning_folder)
        skill, replaced = install_skill(args.replace)
        hooks_changed, hooks_backup = merge_hooks(hooks_path)
        run_configure(vault, args)
        sandbox = configure_writable_root(vault, previous_vault)
        doctor = run_doctor()
    except (InstallError, OSError, ValueError) as exc:
        print(f"install: {exc}", file=sys.stderr)
        return 2

    print(f"Installed Learn skill: {skill}")
    if replaced:
        print(f"Previous skill moved to: {replaced}")
    print(f"Learning template: {vault / args.learning_folder} ({len(created)} missing items created)")
    print(f"Hooks: {'updated' if hooks_changed else 'already installed'} ({hooks_path})")
    if hooks_backup:
        print(f"Hooks backup: {hooks_backup}")
    print(sandbox["message"])
    if sandbox.get("backup"):
        print(f"Codex config backup: {sandbox['backup']}")
    print("\nDoctor:\n" + doctor)
    print(
        "\nRestart Codex, review the two command hooks in Codex Settings → Hooks, and trust them only "
        "after confirming they run the installed learnctl.py. Then invoke $learn explicitly."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
