You are a fresh-context iteration in a loop. The repo is your memory.
Specs say what to build. You decide what is the next most useful change.

1. Read `docs/specs/*.md` and `docs/plan.md` and identify the most important unfinished items.
2. Inspect the relevant code and tests before editing.
3. Implement the scoped change that advances the specs.
4. Add or update tests that prove behavior and challenge the source; use durable, behavior-focused names and docstrings.
5. A milestone is not DONE until a test executes the entry point end-to-end and asserts observable output and exit code. Unit-testing an internal function is not sufficient.
6. Run `harness gate`. If `harness` is not on PATH, run `.venv/bin/harness gate`.
7. Fix failures without weakening tests, coverage, typing, security checks, or the gate.
8. Update the relevant spec and `docs/PROJECT_STATUS.md` to match what changed.
9. Commit on the current branch.
10. Push the current branch so the iteration is saved remotely.

Rules:

- Do not batch unrelated work.
- Keep history linear on the current branch: no branches, worktrees, merges, or rebases unless the human explicitly asked for one; commit only relevant current-branch work.
- If forbidden paths block a commit, run `git restore --staged <path>` and leave those working-tree edits for human review.
- If a spec is wrong or missing, add or update the spec using `plan.md` instead of guessing.
- Never delete tests or assertions to make checks pass.
- Do not edit forbidden paths: `AGENTS.md`, `harness/`, `.githooks/`, `.github/`, `pyproject.toml`, `PROMPT.md`.
- Use tests for code behavior and API contracts. Do not test for `.md` contents.

Commit message:

```
One sentence summary

- concrete detail
- concrete detail

<agent-name>-<spec>-<RALPH_ITERATION_COUNT/TOTAL_ITERATIONS>
```
