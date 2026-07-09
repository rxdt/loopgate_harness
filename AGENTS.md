# FUNCTIONALITY

## Repository boundary

- Work only inside this repository; use `scratchpad/` for temporary files.
- Do not read, write, or search outside the repo unless the user explicitly asks.
- Do not edit forbidden paths: `AGENTS.md`, `harness/`, `tests/harness/`, `.githooks/`, `.github/`, `pyproject.toml`. The gate enforces this.
- Do not make assumptions. Check the code before moving forward.

## Quality

- The quality minimum is code in `harness/gate.py` (forbidden to agents). Leave it unchallenged.
- Strengthen tests and coverage. Pass lint, type, and gate checks.
- Avoid lint suppressions, type-ignores, skipped/xfail tests, or broad exception swallowing.
- Never run destructive git commands (`rm -rf`, `git reset --hard`, `git branch -D`) unless the user explicitly asks; verify each risky step.
- Never bypass or reconfigure git hooks.

## Commit and gate

- ralph harness preflight runs on commit => fast lint + format check + plus loop containment.
- Run `harness preflight` often, run `harness gate` before pushing
- Done means: no forbidden path touched; ralph harness gate is green; your chosen spec and `docs/PROJECT_STATUS.md` reflect what was built.

## Documentation

- Every agent-maintained `.md` (`docs/PROJECT_STATUS`, `docs/specs/`) stays under 100 lines — distill for the next agent.

## Session handoff

- At <=40% of your context window, stop expanding scope.
  - Update `docs/PROJECT_STATUS.md` with new state, checks, commit/branch, blockers, and next steps.
  - Update your spec.
  - Leave the repo resumable.
  - Commit through the gate, push, then merge when safe.

# RULES

## 1. Think First

**Verify assumptions. Surface confusion. Note tradeoffs.**

Before implementing:

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Request clarity.
- Review source code and tests.

## 2. Simplicity

**Only the minimum code that solves the problem**

- Strict scope compliance.
- Readable and reusable code.
- Prune code written for unlikely error paths.
- Be clear, not clever.
- Avoid sprawl. 200 lines could be 25: rewrite.

Ask: "Would a human say this is over-engineered?" Then simplify.

## 3. Surgical Edits

**Touch minimal surfaces. Clean up only your own mess.**

- Don't "improve" adjacent code.
- Don't refactor code that wasn't assigned.
- If you notice unrelated dead code, mention it - don't delete it.
- Remove imports/variables/functions that YOUR changes orphaned.

Acceptance criteria: Each changed line traces directly to the user's request.

## 4. Python

- Write simple, readable Python.
- Prefer module-level functions. Reserve classes for changed state on data grouped with behavior.
- Avoid AI-bloat, like:
  - wrapping literals in their constructors (`"x"`, not `str("x")`; `[]`, not `list([])`)
  - repeated string normalization (`.strip()` later followed by`.replace()`)
  - overly defensive checks not at boundaries
