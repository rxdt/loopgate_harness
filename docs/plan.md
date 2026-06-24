# Plan

> The human vision: what this project is and why. The loop reads this for direction; `specs/` carries out concrete, prioritized build plans.

## Goal

This project is a reusable Python Ralph harness for fresh-context autonomous coding loops. It gives agents a standing prompt, prioritized specs, docs handoff points, and gated commit/push checks so each iteration can make one bounded change and leave the repo resumable.

## Approach

- Deterministic, well-typed packages under `src/`.
- Built by the gated Ralph loop, one small change per iteration, 100% covered from day one.
- Keep human-owned guardrail files stable unless a human explicitly assigns that work.

## Milestones

1. Replace placeholder planning/spec docs with concrete Ralph harness direction.
2. Add non-harness tests that protect agent-maintained loop docs from template regressions.
3. Add downstream product code only after the plan names a concrete project.
