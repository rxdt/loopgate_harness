# Run-Log Summaries

> **PRIORITY 1.** Run logs already contain everything a person needs to answer "what did my runs cost, and why did that one stop early?" — but only `status`'s file count ever read them back. This spec makes `harness status` answer those questions from the repo's own `scratchpad/runs/` logs.

## Scope

- New module `harness/runs.py`: read-only parsing of `scratchpad/runs/<YYYYMMDD>/<agent>/<NNNN>.jsonl`.
- `harness status` in `harness/cli.py` renders a newest-first summary table; `--verbose` adds detail.
- Sample logs (claude 0003, codex 0003) live in `harness/tests/logs/` as test fixtures; `run_worker` is untouched.

## Priorities

1. Milestone: one shared row shape per run
  - Description: Claude and Codex report the same facts under different names; find lines by `type`, never position.
  - Sub-tasks:
    - Map claude `result` (`total_cost_usd`, `usage.cache_*_input_tokens`, `stop_reason`, `duration_ms`) and codex `turn.completed` (`usage.cached/cache_write_input_tokens`) onto shared columns.
    - Take iterations and elapsed minutes from ralph's `type: "ralph"` framing lines (present for every agent).
    - Last message: claude `result` text, else codex `item.completed` with `item.type == "agent_message"`.
  - Files: `harness/runs.py`, `harness/tests/test_runs.py`
  - Definition of done: `pytest harness/tests/test_runs.py` maps both committed sample logs onto expected facts.
2. Milestone: status surface
  - Description: default output a person can read; expandable on request, not by default.
  - Sub-tasks:
    - Table: DATE, AGENT, RUN, ITERS, MIN, TOKENS IN/OUT, CACHE R/W, COST, MS, STOP; newest (mtime) first.
    - `--verbose`: last message and log path per run; empty runs dir keeps the old one-line receipt.
  - Files: `harness/cli.py`, `harness/tests/test_cli.py`
  - Definition of done: `harness status` and `harness status --verbose` exit 0 on sample logs and an empty repo.

## Guardrails

- Facts an agent never reported render as `-`, never `0`; a wrong number is worse than a blank.
- Parse defensively: skip unparseable lines, non-JSON-object lines, and unreadable files; one weird log cannot crash `status`.
- Do not change `run_worker` or any write path; this feature is read-only.
- No new dependencies; `rich.Table` style follows `check()`.

## Acceptance Criteria

- `pytest -p no:cacheprovider -n auto --cov --cov-fail-under=100` covers `harness/runs.py` at 100%.
- ruff format + ruff check are clean on `harness/runs.py` and the changed files.
- Hypothesis property tests prove arbitrary text/numbers/timestamps render without crashing and never fabricate values.

## Out of Scope

- Reading agent-native session logs outside the repo (Option B): the repo boundary forbids it, and claude's `--no-session-persistence` means those logs often do not exist; `scratchpad/runs/` is the source.
- agy/copilot parsing until real sample logs exist to design against.
- Writing, pruning, or aggregating logs; JSON status output; per-run token breakdowns.

## Blockers

- None. (agy/copilot shapes remain unknown until someone runs them and shares one log in the issue.)

## Changelog

_Keep brief and to the latest items to keep spec < 100 lines_
- 2026-09-12: implemented Milestones 1–2 on `feat/run-log-summaries` — `harness/runs.py`, status table + `--verbose`, fixture logs, 17 unit/property tests, two `test_cli.py` status tests updated. Hypothesis caught a real bug en route: unicode-digit junk parses as `float`, so totals now require finite numbers.
