<div align="center">
<img src=".banner.svg" alt="Blue infinity loop" width="360">

<h1>L∞pGate</h1>
<h4>Run coding agents strictly and only accept changes that pass your quality gates.</h4>
<p>A loop harness for Claude, Codex, Copilot, or any CLI agent. A loop runner hands each agent a prompt. Agents can edit. Gates decide what lands. You set the plan. Each agent iteration must update specs and commit through quality guardrails.</p>

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
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
[![mutation](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Frxdt%2Floopgate_harness%2Fmain%2Fmutation-score.json)](https://github.com/rxdt/loopgate_harness/actions/workflows/mutation.yml)


</div>

---

## TL;DR

1. `gh repo create <your-github-username>/<your-new-app-name> --template rxdt/loopgate_harness --private --clone && cd <your-new-app-name> && uv run harness install <your-new-app-name> && source .venv/bin/activate && git add . && git commit --amend --no-edit`
2. `harness run codex`

**Requirements**: `pip`, `uv`, or `poetry`. Python 3.10. Linux or MacOS (Windows is experimental.)

---

## Features

Each run starts fresh, has clear limits, saves its logs, protects key files, and must pass checks you choose.

- **Quality-first**: Fight the AI slop with standards and style 💯
- **Worker-agnostic**: Claude, Codex, Copilot, Agy, or any prompt-reading CLI
- **No lazy**: Agents work, _only if they pass the quality gates you set_ ✅
- **Repo-as-memory workflow**: specs/status/prompt are durable but code is king, leaving you free 😎
- **Built-in stack**: Linting, Format Check, Type-Checks, Dependancy Audit, Property-testing, Mutation-testing, 100% test coverage, Semgrep Security ☑☑☑
- **Progressive**: Preflight vs full gate split 🆗
- **Forbidden-file containment**: Don't touch that!-configurable 🛑
- **Installable project template**: `harness install <your-app-name>` gets the repo ready ▶️
- **No-rot**: Fresh-context agent iterations to reduce context rot 🔄
- **Simple**: One-command setup gets you going
- **Hooks and CI ready-to-go:** Pre-commit, pre-push, and commit-message hooks + [`CI`](.github/workflows/ci.yml) are defined and already work with the checks
- **No-waste**: Timeouts and time-limits for all loops ⏰
- **Diff size guardrails**: Agent changes warn at 300 lines and block at 400 📖
- **No empty work**: Agents blocked from empty commits
- **Agent containment prioritized**: Stop the madness
- **Industry-grade Security** Enabled with [Semgrep](https://semgrep.dev/) 🔓

---

## Details

> [!IMPORTANT]
> Default configurations In [`pyproject.toml`](pyproject.toml) Update tool settings, add agent calls, remove or include checks... or leave as is.

[docs/plan.md](`docs/plan.md`) is where you define what you want the end product to be. You must be _very_ clear on what the finished product should and should **not** contain. Do **not** let agents guess.

`docs/PROMPT.md` tells each agent to pick a `spec` and build. `docs/specs/` say _what_ to build. The agent decides _what next_. You keep `docs/plan.md` current, and specs get rewritten from it (agent is told in `docs/PROMPT.md` to update the specs). Each iteration the agent updates its spec and `PROJECT_STATUS`.

> [!TIP]
> If you don't like _ANYTHING_ in this framework, [update it](#expanding-your-harness).

### Start a project

1. `gh repo create <your-github-username>/<your-new-app-name> --template rxdt/loopgate_harness --private --clone` **or**
   ['Use This Template'](https://github.com/new?template_name=loopgate_harness&template_owner=rxdt)
2. Source your environment (if applicable)
3. From the root, run `harness install <your-project-name>` to name the project, install dependencies, set up the git hooks, and delete excess files. Install dependencies e.g. `uv sync && source .venv/bin/activate && harness install <your-project-name>`.
4. `git commit` (the `install` command updates the repo)
5. Write your grand vision in [docs/plan.md](docs/plan.md)
6. Optionally add the first spec in `docs/specs/` (or leave it to the agents to draft the first specs based on your `plan.md`)
7. Product code goes in [`src/`](src/). _(List new source directories in [`pyproject.toml [tool.coverage.run]`](pyproject.toml#toolcoveragerun).)_
8. Run some loops!
   `harness run <agent=claude|codex|agy|copilot> [max_iterations] [max_minutes]`
9. Not what you wanted? Refine [`docs/plan.md`](docs/plan.md) / [`docs/PROMPT.md`](docs/PROMPT.md) and re-run
10. Strict Ruff rules, type-checking Pyright, Complexipy, and Pytest coverage are set in [`pyproject.toml`](pyproject.toml).
11. Your coding quirks go in [`preferences/preferences.py`](preferences/preferences.py).

### Works with `uv`, `poetry`, or `pip`

```sh
uv sync
source .venv/bin/activate
harness install <your-project-name>
git add . && git commit
harness gate
harness run <agent>

poetry install
poetry run harness install <your-project-name>
git add . && git commit
poetry run harness gate
poetry run harness run <agent>

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -e .
harness install <your-project-name>
git add . && git commit
harness gate
harness run <agent>
```

![L∞P architecture engine flow](.loops.svg)

## A L∞Pgate Loop

The repo is the only memory. Each iteration is a fresh-context agent.

- `docs/specs/` say WHAT to build
- constant `docs/PROMPT.md` tells the agent: read `docs/specs/`, review `src/`, build the most important unfinished thing
- agent builds
- agent commits
- every git commit passes the fast preflight (lint, format, plus loop containment for the agent)
- every git push runs the full gate: lint, types, semgrep, tests, 100% coverage
- the loop stops at `max_iterations`, a nonzero worker exit, or a timeout
- Unspecified iterations/minutes → default to 2 iterations × 20 minutes each
- Each run streams agent 'thought' output live and is saved in a local scratchpad log
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
harness/        the gate, loop runner, CLI                           (🤖 forbidden directory)
  tests/          the harness's own tests
  gate.py         mirror the CI locally + preferences.py honored
  cli.py          command-line entry point
  js-scaffold   javascript example to build upon
preferences/    user-defined preferences not covered by tools        (🤖 forbidden directory)
tests/
  preferences/  (🤖 tests/preferences is forbidden directory)
.githooks/      pre-commit / pre-push gate hooks                     (🤖 forbidden directory)
.github/        CI that re-runs the gate                             (🤖 forbidden directory)
pyproject.toml  project + tooling config                             (🤖 forbidden)
docs/           PROMPT, specs/, your plan                            (agent and human maintained)
scratchpad/     scratch dir agents can use for temp files            (For the 🤖 to play)
src/            your product/source code (add to coverage source)
```

[`pyproject.toml`](pyproject.toml) is the single source of harness configuration. Humans own it and [`preferences/`](preferences/); both are agent-protected.

If an agent edits a forbidden file, the file will be unstaged (not allowed to commit). A forbidden pattern by an agent (e.g. `# noqa` or `nosemgrep` will also prevent their commit and force them to fix it.)

</details>

## Commands

Tool commands are defined once, in `[tool.harness]` in [pyproject.toml](pyproject.toml). The local gate and CI both derive them from there.

```sh
harness install <your-project-name>  # rewrite [project] name, uv sync, set core.hooksPath to .githooks
harness preflight  # fast checks: preferences, ruff lint + format (plus loop containment)
harness gate  # full pass: preferences, ruff, format, pyright, pylint, complexipy, semgrep, pip-audit, pytest @ 100% cov, hypothesis
harness info  # show configured agents, checks, and protected paths
harness status  # count run logs and show the newest log
RALPH_LOOP=1 harness gate  # to run as if you are the agent in the loop
harness run <agent> [max_iterations] [max_minutes] [verbose] # claude/codex/agy/copilot, defaults: 2 20 True

# AGENT CALLS
harness run claude 10 20
harness run codex 2 20
harness run agy 3 10
harness run copilot 2 20
```

### Running with claude

To run LoopGate with Claude :

```sh
harness run claude 2 20
```

Note: The worker must be installed and authenticated separately.

<details>
  <summary>

## Expanding your harness </summary>

- Edit rules at [pyproject.toml](pyproject.toml) for [ruff](https://docs.astral.sh/ruff/), [pylint](https://pypi.org/project/pylint/), [pydoclint](https://pypi.org/project/pydoclint/0.9.1/), [pyright](https://github.com/microsoft/pyright), [pytest](https://docs.pytest.org/en/stable/), [hypothesis](https://hypothesis.readthedocs.io/), [complexipy](https://github.com/rohaquinlop/complexipy), [mutmut](https://mutmut.readthedocs.io/)
- Add forbidden files, directories, or patterns in `[tool.harness]` at [pyproject.toml](pyproject.toml)
- Add [Hypothesis](https://hypothesis.readthedocs.io/) tests in any test directory, examples at [test_properties.py](tests/preferences/test_properties.py).
- Run [mutmut](https://mutmut.readthedocs.io/) by hand with `uv run mutmut run`, then `uv run mutmut browse`. A surviving mutant is a covered line no assertion checks. It is not a gate check: `mutmut run` exits 0 even with survivors and it should be run ~1x/week. Its mutants are cached in a JSON and should be used to identify weak tests. Example at [check_mutmut.py](https://github.com/rxdt/loopgate_harness/blob/main/mutation/check_mutmut.py).
- [semgrep](https://docs.semgrep.dev/semgrep-ci/sample-ci-configs) has no repo config here. It uses registry configs / Semgrep's built-in defaults which ignore tests.
- Update `[tool.harness.gate.checks]` in [pyproject.toml](pyproject.toml). [ci.yml](.github/workflows/ci.yml) runs those **same exact** `harness gate` checks.
- Add or remove coding preferences [preferences.py](preferences/preferences.py) that only agents in loops **must** respect. Current preferences:

```py
function_argument_assignment_has_star  # agents use non-specific `def fun(*)`
function_argument_assignment_underscore_lead  # agents love over-using underscore names `def _fun()`
hidden_signature_star_args  # Complain when a function uses *args or **kwargs (it hides function signatures)
dynamic_star_call  # Calls to def fun(*items) breaks when you can't tell how many arguments f is getting
pointless_class  # ensure classes are added for good reasons (carry state, values, methods)
lazy_assert  # enforce real assertions, stronger tests
objects_injected_into_runtime_memory  # finds calls that manipulate global state (dangerous, tricky)
lambda_found  # abolish lambdas, make agents keep their code simple
lazy_any_type_hints  # abolish type `Any` used to bypass strict type-checking
chaotic_continue_statements  # abolish unecessary nested continue statements, clean code
complex_comprehension  # no needlessly dense list/set/dict comprehensions, prefer linear code
```

</details>

<details>
  <summary>

### FAQ </summary>

- **`harness run <agent>` exits immediately / can't find the worker?**

LoopGate does not install or log in agent CLIs. Install and authenticate the worker you selected (`claude`, `codex`, `copilot`, or `agy`), confirm it is on your `PATH` (e.g. `which codex`), then retry. If `which` finds the binary but the run still fails, finish that tool's login/auth flow and retry `harness run`.

- **What is the difference between a gate and a sandbox?**

A **gate** is a workflow checkpoint that evaluates code and decides whether it is allowed to land in your commits. A **sandbox** is an isolated OS-level environment designed to prevent code from modifying your underlying machine. LoopGate uses gates to control your git history, but it does _not_ provide a secure OS sandbox.

- **What if I don't want to build an app in Python?**

You don’t have to. The loop runner, Ralph, and the CLI take a prompt, launch agents pointed at markdown files. LoopGate is language-agnostic at the agent-loop level, but the template is configured to be Python-specific at [pyproject.toml](pyproject.toml). Add your language and commands for your checks to run there.

- **Javascript?**

The included [`harness/js-scaffold`](harness/js-scaffold/package.json) is a simple JavaScript **example** to expand on. Go to [pyproject.toml line 75](pyproject.toml#L75). Update checks. Put `js` into list `[tool.harness].languages`. Remove `py` if unused.

```
npm run --prefix harness/js-scaffold gate
npm run --prefix harness/js-scaffold preflight
```

- **Why not just a shell loop?**

A shell loop only reruns an agent. LoopGate ensures fresh context, durable repo state, time and iteration limits, protected paths, and quality gates that stop bad changes _before_ they land.

</details>

<details>
  <summary>

## Coordination </summary>

- Use `git log --oneline <branch>..HEAD` to show what's unpushed.
- There is NO worktree/branch creation by design. You can create branches/trees and run a loop in each, then merge _(if you feel like managing that)_
- Agent duties can be contained to a part of the repo. e.g. Codex-1-frontend uses `docs/specs/frontend.md`, Claude-2-researcher `docs/specs/backend`...

### If you want to run a graph

**Recommendations for running several agents at once on one branch (no worktrees):**

- **You (human):** seed each spec once with this exact line near the top:

  ```
  Spec claimed by agent: <unclaimed>
  ```

- **The agents:** paste this exact block into [PROMPT.md line 3](docs/PROMPT.md#L3):

  ```
  Other agents are working this repo. Before touching code, pick a spec whose claim line is
  <unclaimed>, replace it with your exact name `<your-agent-id>-<spec-you-worked>-<RALPH_ITERATION>/<MAX_ITERATIONS>`, e.g. `claude-0003-backend-3/3`, and commit that claim first. Own that spec's file and its tests. Set the line back to <unclaimed> on your last commit.
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
  3. Change this behavior as you like.

</details>

![diagram](.diagram.png)

## Read this before a first run.

1. **This harness does not sandbox agents.** It _tries_ to harness bad code in loops via gates. Sandboxing agents will, e.g. prevent them from maintaining git, running Playwright, being seen as trustworthy by semgrep leading to cyclical failures, etc.

2. **The gate is a guardrail, not a jail.** Agents are crafty, like people. They will find a way to complete a task at all costs. **Trust nothing and no one.**

3. **Mind your usage limits.** `harness run` works agents to the cap set. You can easily burn through your tokens, context windows, and provider usage limits. **Workers continue running as long as there is work to do.**

4. **`docs/PROMPT.md` tells the worker to push or not**.

5. Protect `main` and run the loop on its own branch.

6. **100% coverage does not mean good tests.** That is quantity, not quality. Run `uv run mutmut run` to find covered lines that no assertion actually checks.

7. **Note**: `semgrep --config auto` needs network for semgrep registry rules.
