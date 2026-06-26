# Ralph loop prompt

You are one fresh-context iteration of the loop. The repo is your memory.
Specs say what to build. You decide what is the next most useful change.

1. Read `specs/` and identify the single most important unfinished item.
2. Inspect the relevant code and tests before editing.
3. Implement one tightly scoped change that advances that item.
4. Add or update tests that prove behavior and challenge the source; use durable, behavior-focused names and docstrings.
5. Run `harness gate`. If `harness` is not on PATH, run `.venv/bin/harness gate`.
6. Fix failures without weakening tests, coverage, typing, security checks, or the gate.
7. Update the relevant spec and `docs/PROJECT_STATUS.md` to match what changed.
8. Commit on the current branch through the normal git hooks.
9. Push the current branch so the iteration is saved remotely.

Rules:
- Do not create a branch or worktree unless the human explicitly asked for one.
- Keep the change small enough to finish in this iteration.
- Do not batch unrelated work.
- Keep history linear on the current branch: no branches, worktrees, merges, or rebases; commit only relevant current-branch work.
- If forbidden paths block a commit, run `git restore --staged <path>` and leave those working-tree edits for human review.
- If a spec is wrong or missing, update the spec instead of guessing.
- Never delete tests or assertions to make checks pass.
- Do not edit forbidden paths: `AGENTS.md`, `harness/`, `tests/harness/`, `.githooks/`, `.github/`, or `pyproject.toml`, `PROMPT.md`.
- Pass `harness gate` and `harness preflight`
- Use tests for code behavior and API contracts. Do not test for `.md` contents.

Commit message:
```
One sentence summary

- concrete detail
- concrete detail

<agent-name>-<spec>-<RALPH_ITERATION_COUNT/TOTAL_ITERATIONS>
```
