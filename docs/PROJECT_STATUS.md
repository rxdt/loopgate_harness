# Project Status

> Current truth of the repo. Keep it short and current < 100 lines. Human-agent interface document.

## Current Focus

- Active spec + milestone: context-rot health protocol (branch `feat/context-rot-health`)

## Current State

- `harness/contextrot.py`: scores finished run logs for context rot (committed).
- `harness/health.py`: `write_health()` maps a `RotScore` → `ok`/`wrap_up` and publishes `.harness/context-health.json` atomically; `health_status()` picks the zone. Unit-tested in `harness/tests/test_health.py`.
- `docs/PROMPT.md`: standing wrap-up instruction so agents honor the health file.
- `docs/plan.md` and `docs/specs/base.md` are still placeholder templates — no concrete milestones defined yet.

## Checks

- `harness gate` — not run this iteration (wrap_up triggered before new work).

## Next

1. Wire `write_health()` into the run loop (`harness/cli.py`) so a live run publishes `.harness/context-health.json`; nothing calls it today.
2. Add an end-to-end test: drive the harness run entry point and assert the health file is written with the expected `status` and a 0 exit code (rule 6).
3. Human: fill `docs/plan.md` and `docs/specs/base.md` with concrete milestones so later iterations can pick the next unfinished item.

## Changelog

- 0001-claude iter 1/1: read `.harness/context-health.json` → `status: wrap_up` (pressure_risk 88, red). Per protocol, started no new work. Working tree was clean (prior iteration committed the health writer + tests at 06b4ac4). Wrote this handoff and stopped.

## Blockers

- Specs/plan are templates; agents cannot derive milestones until a human fills them (or continues the context-health wiring above).
