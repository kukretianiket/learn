from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.common import INSTALL, ROOT, UNINSTALL, LearnTestCase, install_env, run_install


class ValidationTests(LearnTestCase):
    def load_learnctl(self, name):
        spec = importlib.util.spec_from_file_location(name, ROOT / ".agents/skills/learn/scripts/learnctl.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_codex_host_detection_and_split_geometry(self):
        module = self.load_learnctl("learnctl_layout_geometry_test")
        self.assertEqual(
            module.detect_codex_host_bundle_id({"CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "codex_vscode"}),
            "com.microsoft.VSCode",
        )
        self.assertEqual(
            module.detect_codex_host_bundle_id({"CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "codex_app"}),
            "com.openai.codex",
        )
        self.assertEqual(
            module.detect_codex_host_bundle_id(
                {"CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "codex_cli", "__CFBundleIdentifier": "com.apple.Terminal"}
            ),
            "com.apple.Terminal",
        )
        self.assertEqual(
            module.detect_codex_host_bundle_id(
                {"CODEX_INTERNAL_ORIGINATOR_OVERRIDE": "codex_cli", "TERM_PROGRAM": "ghostty"}
            ),
            "com.mitchellh.ghostty",
        )
        frames = [
            {"x": 0, "y": 24, "width": 1920, "height": 1080},
            {"x": 1920, "y": 0, "width": 1280, "height": 1024},
        ]
        selected = module.choose_screen(frames, (2100, 100, 900, 700))
        self.assertEqual(selected, frames[1])
        codex, obsidian = module.split_window_bounds(selected, "left")
        self.assertEqual(codex, (1920, 0, 640, 1024))
        self.assertEqual(obsidian, (2560, 0, 640, 1024))
        self.assertEqual(module.split_window_bounds(selected, "right"), (obsidian, codex))

    def test_ancestor_tty_walks_past_detached_tool_processes(self):
        module = self.load_learnctl("learnctl_ancestor_tty_test")
        detached = subprocess.CompletedProcess([], 0, stdout="200 ??\n", stderr="")
        attached = subprocess.CompletedProcess([], 0, stdout="100 ttys004\n", stderr="")
        with mock.patch.object(module.os, "ttyname", side_effect=OSError), mock.patch.object(
            module.os, "getpid", return_value=300
        ), mock.patch.object(module.subprocess, "run", side_effect=[detached, attached]) as run:
            self.assertEqual(module.detect_ancestor_tty(), "/dev/ttys004")
        self.assertEqual(run.call_count, 2)

    def test_tiling_reports_accessibility_permission_without_moving_windows(self):
        module = self.load_learnctl("learnctl_layout_permission_test")
        denied = subprocess.CompletedProcess([], 0, stdout="LEARN_ACCESSIBILITY_REQUIRED\n", stderr="")
        with mock.patch.object(module, "run_osascript", return_value=denied) as run:
            result = module.tile_macos_windows("com.microsoft.VSCode", "left")
        self.assertEqual(result["status"], "permission-required")
        self.assertIn("Visual Studio Code", result["message"])
        self.assertEqual(run.call_count, 1)

    def test_tiling_uses_current_display_and_exact_halves(self):
        module = self.load_learnctl("learnctl_layout_success_test")
        inspected = subprocess.CompletedProcess([], 0, stdout="OK|2100|100|900|700\n", stderr="")
        arranged = subprocess.CompletedProcess([], 0, stdout="OK\n", stderr="")
        frames = [
            {"x": 0, "y": 24, "width": 1920, "height": 1080},
            {"x": 1920, "y": 0, "width": 1281, "height": 1024},
        ]
        with mock.patch.object(module, "run_osascript", side_effect=[inspected, arranged]) as run, mock.patch.object(
            module, "macos_screen_frames", return_value=frames
        ):
            result = module.tile_macos_windows("com.microsoft.VSCode", "right")
        self.assertEqual(result["status"], "tiled")
        self.assertEqual(result["screen"], frames[1])
        arrange_arguments = run.call_args_list[1].args[1]
        self.assertEqual(arrange_arguments[:4], ["2560", "0", "641", "1024"])
        self.assertEqual(arrange_arguments[4:8], ["1920", "0", "640", "1024"])

    def test_terminal_tiling_uses_native_bounds(self):
        module = self.load_learnctl("learnctl_terminal_layout_test")
        inspected = subprocess.CompletedProcess([], 0, stdout="OK|10|30|1200|800\n", stderr="")
        arranged = subprocess.CompletedProcess([], 0, stdout="OK\n", stderr="")
        frames = [{"x": 0, "y": 38, "width": 1920, "height": 1126}]
        with mock.patch.object(module, "run_osascript", side_effect=[inspected, arranged]) as run, mock.patch.object(
            module, "macos_screen_frames", return_value=frames
        ):
            result = module.tile_macos_windows("com.apple.Terminal", "right")
        self.assertEqual(result["status"], "tiled")
        query_script = run.call_args_list[0].args[0]
        arrange_script = run.call_args_list[1].args[0]
        self.assertIn("set hostBounds to bounds of hostWindow", query_script)
        self.assertIn('tell application id "com.apple.Terminal"', arrange_script)
        self.assertIn("set bounds of hostWindow", arrange_script)
        self.assertIn("tty of candidateTab is targetTTY", arrange_script)
        terminal_branch = arrange_script.split('if "com.apple.Terminal" is "com.apple.Terminal" then', 1)[1]
        self.assertLess(terminal_branch.index("set bounds of hostWindow"), terminal_branch.index("activate"))

    def test_skill_enforces_obsidian_math_delimiters_and_mcq_slot_issuance(self):
        skill = (ROOT / ".agents/skills/learn/SKILL.md").read_text(encoding="utf-8")
        rendering = (ROOT / ".agents/skills/learn/references/rendering.md").read_text(encoding="utf-8")
        self.assertIn("context --next-mcq --choices", skill)
        self.assertIn("--correct-count", skill)
        self.assertIn("learnctl.py open --session-id", skill)
        self.assertIn("## Lesson begins", skill)
        self.assertIn("Knowledge evaluation begins", skill)
        self.assertIn("pre-send math audit", skill)
        self.assertIn("4–6 high-information", skill)
        self.assertIn("subjective self-map", skill)
        self.assertIn("flowchart TD", skill)
        self.assertIn("rather than forcing a DAG", skill)
        self.assertIn(r"A. $\phi = \frac{2\pi n_{\mathrm{eff}}L}{\lambda_0}$", rendering)
        self.assertIn(r"do not use `\(...\)` or `\[...\]`", rendering)

    def test_mcq_issuance_uses_system_randomness_and_cannot_reroll(self):
        spec = importlib.util.spec_from_file_location("learnctl_random_test", ROOT / ".agents/skills/learn/scripts/learnctl.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        active = {}
        random_source = mock.Mock()
        random_source.sample.return_value = [3, 1]
        with mock.patch.object(module.secrets, "SystemRandom", return_value=random_source):
            first = module.issue_mcq_positions(active, 5, 2, "turn-1")
            second = module.issue_mcq_positions(active, 5, 2, "turn-1")
        self.assertEqual(first, [1, 3])
        self.assertEqual(second, [1, 3])
        random_source.sample.assert_called_once_with(range(0, 5), 2)

    def test_note_validation_finds_unbalanced_markdown(self):
        self.start()
        note = self.note_for()
        note.write_text(note.read_text(encoding="utf-8") + "\n```python\nprint('broken')\n", encoding="utf-8")
        result = self.cli("validate", "--session", str(note))
        self.assertEqual(result.returncode, 2)
        self.assertIn("unbalanced fenced code blocks", result.stderr)

    def test_note_validation_rejects_nonrendering_latex_forms(self):
        self.start()
        note = self.note_for()
        bad_math = (
            "Bare (n_{\\mathrm{eff}}) and \\lambda_0.\n\n"
            "Inline $\\displaystyle \\frac{a}{b}$ and padded $ x_i $.\n\n"
            "Legacy \\(x\\), malformed $\\lambda0$, and $n{\\text{eff}}$.\n\n"
            "$$ E = mc^2 $$\n"
        )
        self.hook("Stop", "session-a", "bad-math", bad_math)
        result = self.cli("validate", "--session", str(note))
        self.assertEqual(result.returncode, 2)
        self.assertIn("LaTeX command appears outside math delimiters", result.stderr)
        self.assertIn("\\displaystyle is not supported in inline math", result.stderr)
        self.assertIn("whitespace immediately inside inline math delimiters", result.stderr)
        self.assertIn("display-math delimiter must be alone on its line", result.stderr)
        self.assertIn("unsupported LaTeX delimiter", result.stderr)
        self.assertIn("LaTeX command runs into a digit", result.stderr)
        self.assertIn("text component appears without an explicit subscript", result.stderr)

    def test_math_validation_does_not_reject_raw_learner_text(self):
        self.start(prompt=r"$learn Why did my note show (n_{\mathrm{eff}}) and $5?")
        result = self.cli("validate", "--session", str(self.note_for()))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_vertical_mermaid_allows_cycles_but_horizontal_flow_is_rejected(self):
        self.start()
        note = self.note_for()
        vertical_cycle = (
            '```mermaid\nflowchart TD\n  A["Foundation"] --> B["Mechanism"]\n'
            '  B --> C["Application"]\n  C -. "feedback" .-> B\n```\n'
        )
        self.hook("Stop", "session-a", "vertical-cycle", vertical_cycle)
        valid = self.cli("validate", "--session", str(note))
        self.assertEqual(valid.returncode, 0, valid.stderr)

        note.write_text(note.read_text(encoding="utf-8").replace("flowchart TD", "flowchart LR"), encoding="utf-8")
        invalid = self.cli("validate", "--session", str(note))
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("uses horizontal flow; use flowchart TD", invalid.stderr)

    def test_validation_finds_missing_asset_and_source(self):
        self.start()
        note = self.note_for()
        text = note.read_text(encoding="utf-8").replace(
            "<!-- LEARN:TRANSCRIPT:END -->",
            "![[Assets/missing image.png]]\n\n[[Sources/missing-source]]\n<!-- LEARN:TRANSCRIPT:END -->",
        )
        note.write_text(text, encoding="utf-8")
        result = self.cli("validate", "--session", str(note))
        self.assertEqual(result.returncode, 2)
        self.assertIn("embedded local asset does not exist", result.stderr)
        self.assertIn("linked source note does not exist", result.stderr)

    def test_obsidian_uri_encodes_spaces_and_unicode(self):
        spec = importlib.util.spec_from_file_location("learnctl_for_test", ROOT / ".agents/skills/learn/scripts/learnctl.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        note = self.vault / "Learning" / "Sessions" / "Vectors Ω.md"
        config = {
            "vault_path": str(self.vault),
            "learning_folder": "Learning",
            "obsidian_vault_name": "Vault with spaces Ω",
        }
        uri = module.obsidian_uri(config, note)
        self.assertIn("Vault%20with%20spaces%20%CE%A9", uri)
        self.assertIn("Learning%2FSessions%2FVectors%20%CE%A9.md", uri)
        self.assertNotIn(" ", uri)
        anchored = module.obsidian_uri(config, note, "🤖 Tutor · latest")
        self.assertIn("%23%F0%9F%A4%96%20Tutor%20%C2%B7%20latest", anchored)

    def test_reading_view_is_forced_and_terminal_focus_is_restored(self):
        module = self.load_learnctl("learnctl_reading_view_test")
        completed = subprocess.CompletedProcess([], 0, stdout="OK\n", stderr="")
        with mock.patch.object(module.sys, "platform", "darwin"), mock.patch.object(
            module, "run_osascript", return_value=completed
        ) as run:
            result = module.force_obsidian_reading_view("com.apple.Terminal", "/dev/ttys004")
        self.assertEqual(result, {"status": "reading", "focus_restored": True})
        script, arguments = run.call_args.args
        self.assertIn('click menu item "Reading View"', script)
        self.assertIn("tty of candidateTab is targetTTY", script)
        self.assertEqual(arguments, ["/dev/ttys004"])

    def test_latest_tutor_heading_tracks_newest_output(self):
        module = self.load_learnctl("learnctl_latest_heading_test")
        note = self.vault / "Learning" / "Sessions" / "follow.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            "### 🤖 Tutor · first\n\nOld\n\n### 🧑 Learner · reply\n\nHi\n\n### 🤖 Tutor · latest\n\nNew\n",
            encoding="utf-8",
        )
        self.assertEqual(module.latest_tutor_heading(note), "🤖 Tutor · latest")

    def test_obsidian_cli_is_preferred_and_uses_vault_relative_path(self):
        spec = importlib.util.spec_from_file_location("learnctl_open_test", ROOT / ".agents/skills/learn/scripts/learnctl.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        note = self.vault / "Learning" / "Sessions" / "Vectors Ω.md"
        config = {
            "vault_path": str(self.vault),
            "learning_folder": "Learning",
            "obsidian_vault_name": "Vault with spaces Ω",
        }
        completed = subprocess.CompletedProcess([], 0)
        with mock.patch.object(module.shutil, "which", return_value="/usr/local/bin/obsidian"), mock.patch.object(
            module.subprocess, "run", return_value=completed
        ) as run:
            self.assertEqual(module.open_note(config, note), "obsidian-cli")
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["/usr/local/bin/obsidian", "vault=Vault with spaces Ω", "open"])
        self.assertEqual(command[3], "path=Learning/Sessions/Vectors Ω.md")

    def test_failed_obsidian_cli_falls_back_to_uri_on_macos(self):
        spec = importlib.util.spec_from_file_location("learnctl_fallback_test", ROOT / ".agents/skills/learn/scripts/learnctl.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        note = self.vault / "Learning" / "Sessions" / "Vectors Ω.md"
        config = {
            "vault_path": str(self.vault),
            "learning_folder": "Learning",
            "obsidian_vault_name": "Vault with spaces Ω",
        }
        results = [subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]
        with mock.patch.object(module.shutil, "which", return_value="/usr/local/bin/obsidian"), mock.patch.object(
            module.sys, "platform", "darwin"
        ), mock.patch.object(module.subprocess, "run", side_effect=results) as run:
            self.assertEqual(module.open_note(config, note), "obsidian-uri")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[0][0], "open")
        self.assertIn("obsidian://open?", run.call_args_list[1].args[0][1])

    def test_sandboxed_macos_launch_is_deferred_for_escalated_retry(self):
        spec = importlib.util.spec_from_file_location("learnctl_escalation_test", ROOT / ".agents/skills/learn/scripts/learnctl.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(module.sys, "platform", "darwin"), mock.patch.dict(
            module.os.environ, {"CODEX_SANDBOX": "seatbelt"}
        ), mock.patch.object(module.shutil, "which", return_value=None):
            self.assertTrue(module.opening_needs_escalation())
        with mock.patch.object(module.sys, "platform", "darwin"), mock.patch.dict(
            module.os.environ, {"CODEX_SANDBOX": "seatbelt"}
        ), mock.patch.object(module.shutil, "which", return_value="/usr/local/bin/obsidian"):
            self.assertTrue(module.opening_needs_escalation({"window_layout": "desktop-split"}))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="learn-install-")
        base = Path(self.temp.name)
        self.home = base / "home"
        self.vault = base / "Vault install Ω"
        self.home.mkdir()
        self.vault.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_install_merges_hooks_and_is_idempotent(self):
        codex = self.home / ".codex"
        codex.mkdir()
        original = {
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "special", "hooks": [{"type": "command", "command": "python3 existing.py", "async": False}]}
                ],
                "SessionStart": [{"matcher": "", "hooks": [{"type": "command", "command": "echo existing", "async": False}]}],
            }
        }
        (codex / "hooks.json").write_text(json.dumps(original), encoding="utf-8")
        first = run_install(self.home, self.vault)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = run_install(self.home, self.vault)
        self.assertEqual(second.returncode, 0, second.stderr)
        hooks = json.loads((codex / "hooks.json").read_text(encoding="utf-8"))
        blob = json.dumps(hooks)
        self.assertIn("python3 existing.py", blob)
        self.assertIn("echo existing", blob)
        self.assertEqual(blob.count("learnctl.py hook"), 2)
        self.assertTrue(list(codex.glob("hooks.json.learn-codex-backup-*")))
        self.assertTrue((self.home / ".agents/skills/learn").is_symlink())
        self.assertTrue((self.vault / "Learning/Home.md").is_file())
        self.assertTrue((self.vault / "Learning/Review Queue.base").is_file())
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        self.assertTrue(config["open_notes_automatically"])
        self.assertEqual(config["window_layout"], "desktop-split")
        self.assertEqual(config["codex_side"], "right")

    def test_installer_can_disable_automatic_opening(self):
        installed = run_install(
            self.home, self.vault, "--no-open-notes", "--window-layout", "none", "--codex-side", "right"
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        config = json.loads((self.home / ".config/learn-codex/config.json").read_text(encoding="utf-8"))
        self.assertFalse(config["open_notes_automatically"])
        self.assertEqual(config["window_layout"], "none")
        self.assertEqual(config["codex_side"], "right")

    def test_reinstall_for_new_vault_replaces_prior_learn_writable_root(self):
        first = run_install(self.home, self.vault)
        self.assertEqual(first.returncode, 0, first.stderr)
        other_vault = self.home / "Second Vault Ω"
        other_vault.mkdir()
        second = run_install(self.home, other_vault)
        self.assertEqual(second.returncode, 0, second.stderr)
        codex_config = (self.home / ".codex/config.toml").read_text(encoding="utf-8")
        self.assertIn(str(other_vault), codex_config)
        self.assertNotIn(str(self.vault), codex_config)
        self.assertTrue(list((self.home / ".codex").glob("config.toml.learn-codex-backup-*")))

    def test_reinstall_repairs_stale_learn_owned_root_after_json_was_changed(self):
        first = run_install(self.home, self.vault)
        self.assertEqual(first.returncode, 0, first.stderr)
        stale = self.home / "Old Vault"
        codex_config_path = self.home / ".codex/config.toml"
        codex_config_path.write_text(
            codex_config_path.read_text(encoding="utf-8").replace(str(self.vault), str(stale)),
            encoding="utf-8",
        )
        second = run_install(self.home, self.vault)
        self.assertEqual(second.returncode, 0, second.stderr)
        codex_config = codex_config_path.read_text(encoding="utf-8")
        self.assertIn(str(self.vault), codex_config)
        self.assertNotIn(str(stale), codex_config)

    def test_uninstall_has_no_collateral_deletion(self):
        installed = run_install(self.home, self.vault)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        personal = self.vault / "Learning" / "Topics" / "My irreplaceable note.md"
        personal.write_text("keep me", encoding="utf-8")
        hooks_path = self.home / ".codex/hooks.json"
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        hooks["hooks"]["Stop"].append(
            {"matcher": "", "hooks": [{"type": "command", "command": "python3 other.py", "async": False}]}
        )
        hooks_path.write_text(json.dumps(hooks), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(UNINSTALL)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=install_env(self.home),
            cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(personal.is_file())
        self.assertEqual(personal.read_text(encoding="utf-8"), "keep me")
        self.assertFalse((self.home / ".agents/skills/learn").exists())
        self.assertFalse((self.home / ".config/learn-codex/config.json").exists())
        self.assertIn("python3 other.py", hooks_path.read_text(encoding="utf-8"))
        self.assertNotIn("learnctl.py hook", hooks_path.read_text(encoding="utf-8"))

    def test_beta_permission_profile_is_not_mixed_with_legacy_keys(self):
        codex = self.home / ".codex"
        codex.mkdir()
        config = 'permission_profile = "managed"\n'
        (codex / "config.toml").write_text(config, encoding="utf-8")
        result = run_install(self.home, self.vault)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((codex / "config.toml").read_text(encoding="utf-8"), config)
        self.assertIn("No legacy sandbox keys were added", result.stdout)

    def test_existing_skill_conflict_requires_replace(self):
        skill = self.home / ".agents/skills/learn"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("personal", encoding="utf-8")
        result = run_install(self.home, self.vault)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Skill conflict", result.stderr)
        self.assertEqual((skill / "SKILL.md").read_text(encoding="utf-8"), "personal")
