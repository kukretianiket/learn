# Testing and verification

Read when verifying a change. Run commands from the repository root. Codex tests use standard-library `unittest`.

## Python behavior changes

Run affected tests while iterating; this is an example focused module:

```bash
python3 -m unittest tests.test_hooks -v
```

Run the full suite before handing off Python behavior changes:

```bash
python3 -m unittest discover -s tests -t . -v
```

- Use [tests/common.py](../../tests/common.py) fixtures with temporary homes and vaults, including paths with spaces and Unicode. Do not exercise installation or cleanup against personal configuration or notes as a test.
- Add regression coverage for a demonstrated failure or changed behavior. Test observable state, CLI results, replay, isolation, and recovery as appropriate; avoid tests that merely repeat implementation details.

## Documentation changes

Review links and documented commands, then check the diff. Documentation-only edits do not need new behavioral tests.

## GUI and rendering changes

For material GUI changes, verify the real Codex host → hook/CLI → Obsidian flow: correct note, originating window, layout, Reading View, follow, and restored focus. Mocked AppleScript success is not an end-to-end pass; see B-05 in [BUG_HISTORY.md](../../BUG_HISTORY.md). If the real flow cannot be exercised, report GUI behavior as unverified. Claim rendered visual verification only after inspecting a render.

## Before handing off

```bash
git diff --check
```

Review the final diff for unnecessary branches, duplicate state, swallowed errors, and obsolete code introduced or left behind by the change. Report the behavior changed, checks actually run, and material remaining limitations.
