<div align="center">
<img src="banner.svg" alt="Blue infinity loop" width="360">

<h1>L∞PS: A Python Ralph Harness</h1>
<p>No task list, no orchestrator. Just a reusable PROMPT with guards.
Loosely opinionated scaffold, easy to opt out of features, for a gated autonomous agent loop ("Ralph"). A dumb Ralph tells an agent "Go!" and hands it a PROMPT. The agent iterates on tasks from specs. Each iteration the worker commits under the pre-commit gate and updates specs; `PROMPT.md` also instructs it to push to GitHub (the harness itself does not push).</p>

![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![Status](https://img.shields.io/badge/github-repo-blue?logo=github)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat)](https://makeapullrequest.com)
![GitHub activity](https://img.shields.io/github/commit-activity/m/rxdt/py_ralph_frame)
![GitHub Release](https://img.shields.io/github/v/release/rxdt/py_ralph_frame?color=pink)
![GitHub Repo Size](https://img.shields.io/github/repo-size/rxdt/py_ralph_frame)
![X (formerly Twitter) Follow](https://img.shields.io/twitter/follow/roxdtvc)
[![](https://img.shields.io/badge/code%20style-mine-999)](https://github.com/sebmestrallet/absurd-badges)
[![](https://img.shields.io/badge/created%20an%20AGI%20by%20mistake-no-3C1)](https://github.com/sebmestrallet/absurd-badges)
![Claude](https://img.shields.io/badge/Claude-D97757?style=for-the-badge&logo=claude&logoColor=white)

</div>

---

## TLDR; Getting Started.

1. Get the scaffold: clone this repo (or use it as a GitHub template) into your project directory.
2. Set it up from inside the checkout: `uv run harness install <your-project-name>` — renames the project, syncs dependencies, installs the git hook.
3. Write what you want to build in [docs/plan.md](docs/plan.md).
   Include statements that you _"want specs created at [specs](specs) and [docs](docs) to be updated but do NOT touch `plan.md`"_. Be specific.
4. Run the loop: `harness/ralph.sh [max_iterations] [max_minutes] <worker-agent>`
5. If agents don't get you exactly what you want, trash it, start over, and refine the plan.

---

## Details

Agents update their spec and `PROJECT_STATUS` at the end of each iteration. How things get built: the agent's `PROMPT` tells it to pick a `spec`. The `specs/` say *what* to build. The agent in the loop decides *what next*. Humans update the **MASTER** `PLAN` that refreshes `specs/`. Ideas from [ghuntley](https://github.com/ghuntley), How to Ralph Wiggum.

## Start a new project

Use with new Python projects or drop `harness/` and dependencies into an existing project.

1. From inside the checkout, run `uv run harness install <your-project-name>`. Names the project, installs dependencies, and sets up the git hook.
2. Write your grand vision into `docs/plan.md`.
3. Optionally add the first spec in `specs/`, or have an agent draft the first specs.
4. Put product code under `src/` and list new source directories in `pyproject.toml [tool.coverage.run]`.
5. Strict Ruff rules, type checking, semgrep, and test coverage are set in `pyproject.toml`.
6. Your coding quirks go in `harness/preferences.py`.
7. Run a loop:

```sh
harness/ralph.sh [max_iterations] [max_minutes] <worker-agent>  # prompt-injected
```

![L∞PS Architecture Engine Flow](.loops.svg)

## A l∞p

The repo is the only memory between iterations. Each iteration is a fresh-context agent.

- `specs/` say WHAT to build
- constant `PROMPT.md` tells the agent: read `specs/`, review `src/`, build the most important unfinished thing
- agent builds
- agent commits
- every git commit passes the fast preflight (lint, format, plus loop containment for the agent)
- every git push runs the full gate: lint, types, semgrep, tests, 100% coverage
- the loop stops at `max_iterations`, a nonzero worker exit, or a timeout
- Unspecified iterations/minutes → default to 2 iterations × 20 minutes each
- **The harness is worker-agnostic.** Any agent CLI that reads a prompt from stdin and can edit/commit works.

![L∞PS Agents](.loops_agents.svg)

- There is NO worktree/branch creation by design. Agent duties can be contained to a part of the repo. e.g. Codex-1-frontend uses `specs/frontend.md`, Claude-2-researcher `specs/backend`...
- Intentional:
  1. For simplicity and maintainability of the framework.
  2. Because a fresh iteration can't see unmerged work in another worktree, so agents miss context and scramble to merge while conflicts pile up.
  3. Change this behavior if you're comfortable with granting agents machine access, feeding context to agents, and managing rapidly moving git history.
  4. You can also create branches/trees and run a loop in each, then merge.
- If you don't like _ANYTHING_ in this framework, remove it.

## Safety

`harness/ralph.sh` launches an autonomous LLM worker with the permissions you grant it (e.g.
`--permission-mode acceptEdits`). The gate bounds what any **commit** may touch, but the worker itself is **not** sandboxed to this repo — under a permissive mode it can run arbitrary shell. You are authorizing real changes. Choose the worker and permission mode deliberately. Use `git log --oneline <branch>..HEAD` to show what's unpushed.

#### The Gate: Tiered Checks

 `harness/gate.py` holds `FORBIDDEN_FILES`, `FORBIDDEN_DIRS` and `FORBIDDEN_PATTERNS`. `harness/preferences.py` holds human's style checks other tools can't catch. Containment runs when `RALPH_LOOP=1`, which `ralph.sh` sets on each run. `pyproject.toml` holds many rules. Humans own them (`harness/preferences.py` is part of `harness/`).

⚡ `harness preflight` (pre-commit) → fast checks.
Ruff lint + check format for everyone, _plus_ **containment** for the agents.

✅ `harness gate` (CI/PR pre-push) → ruff lint + check format, pyright, pylint, semgrep, pytest @ 100% cov.

Humans ONLY can bypass triggered gates and commit by adding flag `--no-verify`.

## Layout
```
harness/        the gate, loop (ralph.sh), CLI, custom user checks   (🤖 forbidden)
tests/harness/  the harness's own tests                              (🤖 forbidden)
.githooks/      pre-commit / pre-push gate hooks                     (🤖 forbidden)
.github/        CI that re-runs the gate                             (🤖 forbidden)
pyproject.toml  project + tooling config                             (🤖 forbidden)
AGENTS.md       rules for agents working in the repo                 (🤖 forbidden)
PROMPT.md       the standing per-iteration instruction               (human maintained)
specs/          WHAT to build, one PRIORITY-bannered file per track
src/            your product/source code (add to coverage source)
docs/           PLAN; PROJECT_STATUS                                 (human maintained plan.md)
scratchpad/     scratch dir agents can use for temp files            (For 🤖)
```

## ⚠️ Warnings. Read this before a first run.

1. **This harness does not sandbox agents.** It *tries* to harness bad code in loops via gates. Sandboxing agents will, e.g. prevent them from maintaining git, running Playwright, being seen as trustworthy by semgrep leading to cyclical failures, etc.

2. **The gate is a guardrail, not a jail.** Agents are crafty — like people. They will find a way to complete a task at all costs. **Trust nothing and no one.**

3. **Mind your usage limits.** `ralph.sh` works agents to the cap set. You can easily burn through your tokens, context windows, and provider usage limits. **Workers keep working as long as there is work to do.**

4. **`PROMPT.md` tells the worker to push every iteration** Protect `main` and run the loop on its own branch.

5. **100% coverage does not mean good tests.** That is quantity, not quality. (Upcoming feature: mutation testing)

## Commands

```sh
harness install <your-project-name>  # rewrite [project] name, uv sync, set core.hooksPath to .githooks
harness preflight  # fast checks: ruff lint + format (plus loop containment)
harness gate  # full pass: ruff, format, pyright, pylint, semgrep, pytest @ 100% cov
harness run <agent> [max_iterations] [max_minutes] [verbose] # claude/codex/agy/copilot, defaults: 2 20 True

# Underlying tools
ruff check . && ruff format
pyright
pylint harness src
semgrep scan --config auto --config p/secrets --error --quiet .
pytest  # Note: Pydantic is included. Use it.

# Underlying agent calls
harness/ralph.sh 10 20 claude -p --permission-mode acceptEdits --output-format stream-json --verbose

harness/ralph.sh 2 20 codex exec -m gpt-5.5 --json --sandbox workspace-write -

harness/ralph.sh 3 10 agy --log-file agy.log --print

harness/ralph.sh 2 20 sh -c 'copilot --output-format json --stream on --allow-all-tools -p "$(cat)"'
```

## For agents

· Rules: `AGENTS.md` · What to build: `specs/` · Standing instruction: `PROMPT.md` ·

Use your best judgment · Leave the code how you would like to find it ·

Human and agent-owned status interface: `docs/PROJECT_STATUS.md`.
