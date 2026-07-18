<div align="center">
<img src=".banner.svg" alt="Blue infinity loop" width="360">

<h1>L∞pGate</h1>
<p>A coding-agent loop harness for Claude, Codex, Copilot, or any CLI agent.  A dumb Ralph loop runner tells an agent to "Go!" and hands it a PROMPT. Agents can edit. Gates decide what lands. You set the plan in motion. The loops eat the prompt, and each agent iteration must update specs and commit through guardrails.</p>

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Status](https://img.shields.io/badge/github-repo-blue?logo=github)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat)](https://makeapullrequest.com)
[![first-timers-only](https://img.shields.io/badge/first--timers--only-friendly-blue.svg?style=flat-square)](https://www.firsttimersonly.com/)
![GitHub activity](https://img.shields.io/github/commit-activity/m/rxdt/loopgate-harness)
![GitHub Release](https://img.shields.io/github/v/release/rxdt/loopgate-harness?color=pink)
![GitHub Repo Size](https://img.shields.io/github/repo-size/rxdt/loopgate-harness)
![X (formerly Twitter) Follow](https://img.shields.io/twitter/follow/roxdtvc)
[![](https://img.shields.io/badge/code%20style-mine-999)](https://github.com/sebmestrallet/absurd-badges)
[![](https://img.shields.io/badge/created%20an%20AGI%20by%20mistake-no-3C1)](https://github.com/sebmestrallet/absurd-badges)
![Claude](https://img.shields.io/badge/Claude-D97757?style=for-the-badge&logo=claude&logoColor=white)
[![gate](https://github.com/rxdt/loopgate_harness/actions/workflows/ci.yml/badge.svg)](https://github.com/rxdt/loopgate_harness/actions/workflows/ci.yml)

</div>

---

## TL;DR: Getting Started.

1. `gh repo create my-app --template <your-gh-username>/loopgate_harness --private --clone` **or** ['Use This Template'](https://github.com/new?template_name=loopgate_harness&template_owner=rxdt)
2. `uv run harness install <your-project-name>`
3. Write your project goal in [docs/plan.md](docs/plan.md)
4. `harness run <agent=claude|codex|agy|copilot> [max_iterations] [max_minutes]`
5. Not what you wanted? Refine [`docs/plan.md`](docs/plan.md) / [`docs/PROMPT.md`](docs/PROMPT.md) and re-run

---

## Features

- **Quality-first**: Fight the AI slop with standards and style 💯
- **Worker-agnostic**: Claude, Codex, Copilot, Agy, or any prompt-reading CLI
- **No lazy**: Agents work, _only if they pass the quality gates you set_ ✅
- **Repo-as-memory workflow**: specs/status/prompt are durable but code is king, leaving you free 😎
- **Built-in stack**: Ruff, Pyright, Pylint, Semgrep, Complexipy, Hypothesis, 100% coverage ☑☑☑
- **Progressive**: Preflight vs full gate split 🆗
- **Forbidden-path containment**: Don't touch that!-configurable 🛑
- **Installable project template**: `harness install loopgate` gets the repo ready ▶️
- **No-rot**: Fresh-context agent iterations to reduce context rot 🔄
- **Simple**: One command setup gets you git hooks and everything else
- **No-waste**: Timeouts and time-limits for all loops ⏸
- **Agent containment prioritized**: Stop the madness (and [Semgrep](https://semgrep.dev/) for safety) 🔓

---

## Details

> [!IMPORTANT]
> Default configurations In [`pyproject.toml`](pyproject.toml) Update tool settings, add agent calls, remove or include checks... or leave as it.

`docs/PROMPT.md` tells each agent to pick a `spec` and build. `docs/specs/` say _what_ to build. The agent decides _what next_. You keep `docs/plan.md` current, and specs get rewritten from it (agent is told in `docs/PROMPT.md` to update the specs). Each iteration the agent updates its spec and `PROJECT_STATUS`. Ideas from [ghuntley](https://github.com/ghuntley), How to Ralph Wiggum.

> [!TIP]
> If you don't like _ANYTHING_ in this framework, remove it.

## Start a project

1. From inside the checkout, run `harness install <your-project-name>` to name the project, installs dependencies, and set up the 3 git hook.
2. Write your grand vision into `docs/plan.md`.
3. Optionally add the first spec in `docs/specs/`, or have an agent draft the first specs.
4. Put product code under `src/` and list new source directories in `pyproject.toml [tool.coverage.run]`.
5. Strict Ruff rules, type checking, pyright, complexipy, and pytest coverage are set in `pyproject.toml`.
6. Your coding quirks go in `harness/preferences.py`.
7. Run a loop:

```sh
harness run <agent> [max_iterations] [max_minutes]  # agent: claude/codex/agy/copilot. ralph loop runner adds prompt
```

![L∞P architecture engine flow](.loops.svg)

## A L∞PS Loop

The repo is the only memory. Each iteration is a fresh-context agent.

- `docs/specs/` say WHAT to build
- constant `docs/PROMPT.md` tells the agent: read `docs/specs/`, review `src/`, build the most important unfinished thing
- agent builds
- agent commits
- every git commit passes the fast preflight (lint, format, plus loop containment for the agent)
- every git push runs the full gate: lint, types, semgrep, tests, 100% coverage
- the loop stops at `max_iterations`, a nonzero worker exit, or a timeout
- Unspecified iterations/minutes → default to 2 iterations × 20 minutes each
- **The harness is worker-agnostic.** Any agent CLI that reads a prompt from stdin and can edit/commit works.

![L∞PS Agents](.loops_agents.svg)

## Safety

`harness run` launches an autonomous LLM worker with the configured permissions, e.g.
`--permission-mode acceptEdits` or `--sandbox danger-full-access`.

The gate bounds what any **commit** may touch, but the worker itself is **not** sandboxed to this repo unless you set that config. Consider the balance: without access it cannot do much. With machine access it can wreak havoc. Under a permissive mode it can run arbitrary shell. You are authorizing real changes. Choose the worker and permission mode deliberately.

#### The Gate: Tiered Checks

⚡ `harness preflight` (pre-commit) → fast checks.
Ruff lint + check format for everyone, _plus_ **containment** for the agents. Self-heals by un-staging forbidden files.

✅ `harness gate` (CI/PR pre-push). Local checks mirror CI → ruff lint + format report-only, pyright, pylint, semgrep, complexipy, hypothesis, pytest @ 100% cov.

Only humans can bypass triggered gates and commit by adding flag `--no-verify`.

<details>
  <summary>

## Directory Layout

</summary>

```
harness/        the gate, loop runner, CLI, custom user checks       (🤖 forbidden)
  preferences.py  user-defined preferences not covered by tools      (🤖 forbidden)
  gate.py         mirror the CI locally + preferences.py honored     (🤖 forbidden)
  tests/          the harness's own tests                            (🤖 forbidden)
    test_properties.py  hypothesis tests                             (🤖 forbidden)
.githooks/      pre-commit / pre-push gate hooks                     (🤖 forbidden)
.github/        CI that re-runs the gate                             (🤖 forbidden)
pyproject.toml  project + tooling config                             (🤖 forbidden)
AGENTS.md       rules for agents working in the repo                 (🤖 forbidden)
docs/PROMPT.md  the standing per-iteration instruction               (human maintained)
docs/           PLAN, PROJECT_STATUS, PROMPT                          (human maintained plan.md)
scratchpad/     scratch dir agents can use for temp files            (For the 🤖 to play)
docs/specs/     WHAT to build, one PRIORITY-bannered file per track
src/            your product/source code (add to coverage source)
```

If an agent edits a forbidden file, the file will be unstaged (not allowed to commit). A forbidden pattern by an agent (e.g. `# noqa` will also prevent their commit and force them to fix it.)

</details>

[`pyproject.toml`](pyproject.toml) is the single source of harness configuration. Humans own all of it (`pyproject.toml` is agent-forbidden; `harness/preferences.py` is part of `harness/`).

A minimal `[tool.harness.gate]` snippet could look like:

```toml
[tool.harness.forbidden]
dirs = ["harness/"]       # agents may not commit changes here
iles = ["pyproject.toml"]
patterns = ["# noqa"]     # banned in agent-authored diffs

[tool.harness.gate]
pytest = "uv sync pytest"  # one check command, run by the local gate AND CI
```

## ⚠️ Warnings. Read this before a first run.

1. **This harness does not sandbox agents.** It _tries_ to harness bad code in loops via gates. Sandboxing agents will, e.g. prevent them from maintaining git, running Playwright, being seen as trustworthy by semgrep leading to cyclical failures, etc.

2. **The gate is a guardrail, not a jail.** Agents are crafty, like people. They will find a way to complete a task at all costs. **Trust nothing and no one.**

3. **Mind your usage limits.** `harness run` works agents to the cap set. You can easily burn through your tokens, context windows, and provider usage limits. **Workers continue running as long as there is work to do.**

4. **`docs/PROMPT.md` tells the worker to push every iteration**. Protect `main` and run the loop on its own branch.

5. **100% coverage does not mean good tests.** That is quantity, not quality. (Upcoming feature: mutation testing)

6. **Note**: `semgrep --config auto` needs network for semgrep registry rules.

## Commands

Tool commands are defined once, in `[tool.harness.gate.checks]` in [pyproject.toml](pyproject.toml). The local gate and CI both derive them from there.

```sh
harness install <your-project-name>  # rewrite [project] name, uv sync, set core.hooksPath to .githooks
harness preflight  # fast checks: preferences, ruff lint + format (plus loop containment)
harness gate  # full pass: preferences, ruff, format, pyright, pylint, complexipy, semgrep, pytest @ 100% cov, hypothesis
RALPH_LOOP=1 harness gate  # to run as if you are the agent in the loop
harness run <agent> [max_iterations] [max_minutes] [verbose] # claude/codex/agy/copilot, defaults: 2 20 True

# AGENT CALLS
harness run claude 10 20
harness run codex 2 20
harness run agy 3 10
harness run copilot 2 20
```

<details>
  <summary>

## Expanding your harness </summary>

- Edit rules at [pyproject.toml](pyproject.toml) for [ruff](https://docs.astral.sh/ruff/), [pylint](https://pypi.org/project/pylint/), [pydoclint](https://pypi.org/project/pydoclint/0.9.1/), [pyright](https://github.com/microsoft/pyright), [pytest](https://docs.pytest.org/en/stable/), [hypothesis](https://hypothesis.readthedocs.io/), [complexipy](https://github.com/rohaquinlop/complexipy)
- Add forbidden files, directories, or patterns in `[tool.harness.gate]` at [pyproject.toml](pyproject.toml)
- Add Hypothesis tests in any test directory, examples at [test_properties.py](harness/tests/test_properties.py)
- [semgrep](https://docs.semgrep.dev/semgrep-ci/sample-ci-configs) has no repo config here. It uses registry configs plus Semgrep's built-in defaults which ignore tests.
- Edit checks in `[tool.harness.gate.checks]` at [pyproject.toml](pyproject.toml) — [ci.yml](.github/workflows/ci.yml) runs the same `harness gate`, so there is nothing to keep in sync
- Removing existing preferences or add your own preferences at [preferences.py](harness/preferences.py). Current preferences:

```py
function_argument_assignment_has_star  # agents use non-specific `def fun(*)`
function_argument_assignment_underscore_lead  # agents love over-using underscore names `def _fun()`
hidden_signature_star_args  # Complain when a function uses *args or **kwargs (it hides function signatures)
dynamic_star_call  # Calls to def fun(*items) breaks when you can't tell how many arguments f is getting
pointless_class  # ensure classes are added for good reasons (carry state, values, methods)
lazy_assert  # enforce real assertions, stronger tests
objects_injected_into_runtime_memory  # finds calls that manipulate global state (dangerous, tricky)
lambda_found  # abolish lambdas for agents to keep their code simpler
lazy_any_type_hints  # abolish type `Any` used to bypass strict type-checking
chaotic_continue_statements  # abolish unecessary nested continue statements, clean code
complex_comprehension  # no needlessly dense list/set/dict comprehensions, prefer linear code
```

</details>

<details>
  <summary>

### FAQ </summary>

**What is the difference between a gate and a sandbox?**

A **gate** is a workflow checkpoint that evaluates code and decides whether it is allowed to land in your commits. A **sandbox** is an isolated OS-level environment designed to prevent code from modifying your underlying machine. LoopGate uses gates to control your git history, but it does _not_ provide a secure OS sandbox.

</details>

<details>
  <summary>

## Coordination </summary>

- Use `git log --oneline <branch>..HEAD` to show what's unpushed.
- There is NO worktree/branch creation by design. You can create branches/trees and run a loop in each, then merge _(if you really feel like managing that)_
- Agent duties can be contained to a part of the repo. e.g. Codex-1-frontend uses `docs/specs/frontend.md`, Claude-2-researcher `docs/specs/backend`...

### If you must be a ringleader

**Recommendations for running several agents at once on one branch (no worktrees):**

- **You (human):** seed each spec once with this exact line near the top:

  ```
  Spec claimed by agent: <unclaimed>
  ```

- **The agents:** paste this exact block into [PROMPT.md line 3](docs/PROMPT.md#L3):

  ```
  Other agents are working this repo. Before touching code, pick a spec whose claim line is
  <unclaimed>, replace it with your name, and commit that claim first. Own that spec's file and its
  tests. Set the line back to <unclaimed> on your last commit.
  ```

- What fails when agents do not claim specs/work: agents all pick the top-priority spec, duplicate work, and leave a half-staged git index.
- What fails with too little time i.e. MAX_MINUTES too low: a worker dies mid-`gate` before it can commit. Give each iteration enough minutes to finish (the gate itself takes a while). One successful iteration needs ~2-3 min of pure overhead aside from 'real' work.
  - A worker killed too soon leaves its spec claim STUCK: spec stays locked to its name. No other agent will take it until a human resets the line to `<unclaimed>`.
  - preflight on git commit: ~ a few seconds
  - full gate on git push: ~20-48s
  - push + cleanup: ~ few seconds -
- Do not rely on agent names for coordination: agents self-name inconsistently and can collide (e.g. two both call themselves the same thing). Names are for human blame/log-matching only; the claim line + committed code are what actually coordinate.

- Which doc does what:
  - **specs** = the product work
  - **`docs/PROMPT.md`** = how to operate headlessly
  - **repo + green gate** = the source of truth
  - `docs/PROJECT_STATUS.md` is a human-readable record, not authoritative

- No branch/worktree creation in this harness was intentional:
  1. For simplicity and maintainability of the framework.
  2. Because a fresh iteration can't see the unmerged work in another worktree, so agents miss context and scramble to merge while conflicts pile up.
  3. Change this behavior if you're comfortable with granting agents machine access, feeding context to agents, and managing rapidly moving git history.

</details>

![diagram](.diagram.png)
