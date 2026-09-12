# Project Status

> Current truth of the repo. Keep it short and current < 100 lines. Human-agent interface document.

## Current Focus

- Active spec + milestone: docs/specs/base.md → Run-Log Summaries (Milestones 1–2 implemented)

## Current State

- `harness/runs.py` parses `scratchpad/runs/` JSONL: ralph framing lines (iterations, span), claude `result` and codex `turn.completed` mapped to one row shape; unknown facts render as `-`, corrupt lines and unreadable files are skipped.
- `harness status` renders a newest-first run table (DATE, AGENT, RUN, ITERS, MIN, TOKENS IN/OUT, CACHE R/W, COST, MS, STOP); `--verbose` adds last message + log path; empty repo keeps the old one-line receipt.
- Sample logs `harness/tests/logs/{claude,codex}/0003.jsonl` committed as fixtures; `test_runs.py` (unit + hypothesis property) and two updated `test_cli.py` status tests cover the feature.
- `run_worker` untouched; feature is read-only.

## Checks

- `pytest -p no:cacheprovider -n auto` green (282 passed; the template-install test needs this commit present, since it clones HEAD and imports `harness.runs` from the clone)
- `harness/runs.py` + changed files: ruff format & ruff check clean; coverage 100% on `harness/runs.py`
- Full-repo 100% coverage gate verified except the pre-commit-only template test above

## Next

1. Run the full gate (`harness gate`) with the package env — mutmut, semgrep, pyright, pylint all run from the gate, not preflight.
2. Wire mutmut against `harness/runs.py` (`mutmut run`) and kill survivors in `test_runs.py`.
3. When someone runs `agy` or `copilot`, add one real sample log to `harness/tests/logs/` and extend the parsers in `runs.py`.

## Changelog

- Implemented run-log summaries per docs/specs/base.md on branch `feat/run-log-summaries`: new `harness/runs.py`, upgraded `status`, fixture logs, 17 tests in `test_runs.py`, updated 2 `test_cli.py` tests + README.
- Hypothesis caught a real bug: unicode-digit junk strings parse as `float`, so `_number` now requires finite values (`math.isfinite`) — dash instead of `$nan`.

## Blockers

- None known.
