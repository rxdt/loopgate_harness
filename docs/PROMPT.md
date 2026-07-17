You are a fresh-context iteration in a loop. The repo is your memory.
Specs say what to build. You decide what is the next most useful change.

0. FIRST, before anything else, read `.harness/context-health.json` if it exists. If its
   `status` is `wrap_up`: do NOT start new work. Commit your WIP on the current branch,
   append a short handoff note to `docs/PROJECT_STATUS.md` (what you finished, what is
   mid-flight, the exact next step), and stop immediately. If the file is missing or
   `status` is `ok`, continue.
1. Read `docs/specs/*.md` and `docs/plan.md` and identify the most important unfinished items.
2. If a spec is wrong or missing, add or update the spec using `plan.md` instead of guessing.
3. Inspect the relevant code and tests before editing.
4. Implement the scoped change that advances the specs.
5. Add or update tests that prove behavior and challenge the source; use durable, behavior-focused names and docstrings.
6. A milestone is not DONE until a test executes the entry point end-to-end and asserts observable output and exit code. Unit-testing an internal function is not sufficient.
7. Run `harness gate`. If `harness` is not on PATH, run `.venv/bin/harness gate`.
8. Fix failures without weakening tests, coverage, typing, security checks, or the gate.
9. Update the relevant spec and `docs/PROJECT_STATUS.md` to match what changed.
10. Commit on the current branch.
11. Push the current branch so the iteration is saved remotely.

Rules:

- Do not batch unrelated work.
- Keep history linear on the current branch: no branches, worktrees, merges, or rebases unless the human explicitly asked for one; commit only relevant current-branch work.
- If forbidden paths block a commit, run `git restore --staged <path>` and leave those working-tree edits for human review.
- Never delete tests or assertions to make checks pass.
- Do not edit forbidden paths: `AGENTS.md`, `harness/`, `.githooks/`, `.github/`, `pyproject.toml`, `PROMPT.md`.
- Use tests for code behavior and API contracts. Do not test for `.md` contents.

Commit message:

```
One sentence summary

- concrete detail
- concrete detail

<your-agent-id>-<spec-you-worked>-<RALPH_ITERATION>
```

Use the agent id the harness gave you verbatim (e.g. `0002-codex`); append the spec you worked and the
`RALPH_ITERATION` value. This makes commits traceable to their run log (`scratchpad/runs/<id>.jsonl`).
