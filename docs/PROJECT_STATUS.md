# Project Status

> Current truth of the repo. Keep it short and current. Human-agent interface point.

## Now

- Branch `claude-wires-cli-clean` has staged release work for the Ralph harness:
  tuple-based agent launch commands, positional run verbosity, live JSONL output,
  full gate coverage flags, prompt/doc/test cleanup, and CI alignment.
- The active spec's PRIORITY 1 items are satisfied: `specs/base.md` is
  active/concrete and `tests/test_specs.py` guards against template regression.

## Checks

- `uv run harness preflight` — passed.
- `uv run harness gate` — passed.
- `uv run pytest -q` — passed, 89 tests.
- `uv run pytest --cov --cov-report=term-missing --cov-fail-under=100 -q` —
  passed, 100% coverage.

## Next

- Human should review and commit the staged release work.
- Add downstream product code only after `docs/plan.md` names a concrete project.

## Blockers

- Untracked `zsh` file (appears to be an accidental empty file) was left in place,
  not committed or deleted.
- A test Claude run created commit `6ca3347` with stale status text; this file now
  corrects that state in the working tree.
