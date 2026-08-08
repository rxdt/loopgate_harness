You are a fresh-context iteration in a loop. The repo `src/` and `docs/` are your memory. Specs say what to build.
You decide what is the next most useful change.

1. Read `docs/specs/*.md` and `docs/plan.md` and identify the most important unfinished items.
2. If a spec is wrong or missing, add or update the spec using `plan.md` as a guide instead of guessing.
3. Inspect the relevant code and tests before editing.
4. Implement the scoped change that advances the specs.
5. If you are blocked, report it in `docs/PROJECT_STATUS.md` and exit: do not waste your turn and tokens pretending to work.
6. Verify existing 'blockers' before trusting them. Try to remove blockers.
7. Add or update tests that prove behavior and challenge the source; use durable, behavior-focused names and docstrings.
8. A milestone is not DONE until a test executes the entry point end-to-end and asserts observable output and exit code. Unit-testing an internal function is not sufficient. Prefer `hypothesis` property tests when possible.
9. Periodically run `mutmut run` and kill mutants.
10. Run `harness gate`. If `harness` is not on PATH, run `.venv/bin/harness gate`.
11. Update the relevant spec and `docs/PROJECT_STATUS.md` to match what changed. Keep `docs/PROJECT_STATUS.md` uncluttered: persist only actionable items.
12. Commit on the current branch.

Rules:

- Do not batch unrelated work.
- Keep history linear on the current branch: no branches or worktrees unless the human explicitly asked for them. Commit only relevant current-branch work.
- If forbidden paths block a commit, run `git restore --staged <path>` and leave those working-tree edits for human review.
- Never delete tests or assertions to make checks pass.
- Fix failures without weakening tests, coverage, typing, security checks, or the gate.
- Do not edit forbidden paths: `AGENTS.md`, `harness/`, `.githooks/`, `.github/`, `pyproject.toml`, `PROMPT.md`.
- Use tests for code output and contracts. Do not test for `.md` contents.
  Commit message:

```
One sentence summary

- concrete detail
- concrete detail
...

<prefix><your-agent-id>-<spec-you-worked>-<RALPH_ITERATION> # e.g. `codex-0006-frontend_ui-6/7`
```

Use the agent id the harness gave you (e.g. `0002-codex`); append the spec you worked and the
`RALPH_ITERATION` value. This makes commits traceable to their run log (`scratchpad/runs/<id>.jsonl`).
