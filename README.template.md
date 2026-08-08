## TL;DR: Getting Started.

Now that you have the template locally:

1. `uv sync` OR `poetry install` OR ` pip install -r requirements.txt`, then `harness install <your-project-name> && git add . && git commit`
2. Write your project goal in [docs/plan.md](docs/plan.md)
3. `harness run <agent=claude|codex|agy|copilot> [max_iterations] [max_minutes]`
4. Not what you wanted? Refine [`docs/plan.md`](docs/plan.md) / [`docs/PROMPT.md`](docs/PROMPT.md) and re-run

---

## Details

> [!IMPORTANT]
> Default configurations In [`pyproject.toml`](pyproject.toml) Update tool settings, add agent calls, remove or include checks... or leave as is.

> [!TIP]
> If you don't like _ANYTHING_ in this framework, [update it](#expanding-your-harness).

### Start a project

```sh
uv sync
source .venv/bin/activate
harness install <your-project-name> && git add . && git commit
harness gate
harness run <agent>>

poetry install
poetry run harness install <your-project-name> && git add . && git commit
poetry run harness gate
poetry run harness <agent>

python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -e .
harness install <your-project-name>  && git add . && git commit
harness gate
harness run <agent>
```

1. From the root, run `harness install <your-project-name>` to name the project, install dependencies, set up the three git hooks, delete extraneous files.
2. Write your grand vision into `docs/plan.md`.
   - Specs get rewritten from `docs/plan.md` and state of repo.
   - Agent is told in `docs/PROMPT.md` to update the specs.
   - `docs/specs/` tell an agent _what_ to build.
   - Right now `docs/PROMPT.md` tells each agent to pick a `spec`.
3. Put your product code under `src/` .
4. Configurations plus strict Ruff rules, type checking, pyright, complexipy, and pytest coverage are set in [`pyproject.toml`](pyproject.toml).
5. Coding preferences not caught by tooling go in [`preferences/preferences.py`](preferences/preferences.py).
6. Any agent CLI that reads a prompt from stdin and can edit/commit works if they are set in `AGENTS` in [`pyproject.toml`](pyproject.toml).
7. The repo is the only memory. Each iteration is a fresh-context agent.
8. `harness run` launches an autonomous LLM worker with the configured permissions,
9. Run a loop `harness run <agent> [max_iterations] [max_minutes]`:

- agent builds
- agent commits
- every git commit passes the fast preflight (lint, format, plus loop containment for the agent)
- every git push runs the full gate: lint, types, semgrep, tests, 100% coverage
- the loop stops at `max_iterations`, a nonzero worker exit, or a timeout
- Unspecified iterations/minutes → default to 2 iterations × 20 minutes each

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

Tool commands are defined in `[tool.harness.gate.checks]` in [pyproject.toml](pyproject.toml). The gate and CI both derive them from there.

#### The Gate: Tiered Checks

⚡ `harness preflight` (pre-commit) → Ruff lint + check format for everyone, _plus_ **containment** for the agents. Self-heals by un-staging forbidden files.

✅ `harness gate` Local checks mirror CI → ruff lint + format report-only, pyright, pylint, semgrep, complexipy, hypothesis, pytest @ 100% cov.

🤥 `prepare-commit-msg` ensures an agent is not trying to commit empty -- must do work.

> [!IMPORTANT]
> **Only humans can bypass triggered gates and commit by adding flag `--no-verify`.**

<details>
  <summary>

## Directory Layout

</summary>

```
harness/        the gate, loop runner, CLI                           (🤖 forbidden directory)
  gate.py         mirror the CI locally + preferences.py honored
  cli.py          command-line entry point
  tests/          the harness's own tests
  js-scaffold   javascript example to build upon
preferences/    user-defined preferences not covered by tools        (🤖 forbidden directory)
tests/
  preferences/  (🤖 tests/preferences is forbidden directory)
.githooks/      pre-commit / pre-push gate hooks                     (🤖 forbidden directory)
.github/        CI that re-runs the gate                             (🤖 forbidden directory)
pyproject.toml  project + tooling config                             (🤖 forbidden)
AGENTS.md       rules for agents working in the repo                 (🤖 forbidden)
docs/PROMPT.md  the standing per-iteration instruction               (human maintained)
docs/           PLAN, PROJECT_STATUS, PROMPT                         (human or agent maintained plan.md)
scratchpad/     scratch dir agents can use for temp files            (For the 🤖 to play)
docs/specs/     WHAT to build, one PRIORITY-bannered file per track  (agent maintained)
src/            your product/source code (add to coverage source)
```

[`pyproject.toml`](pyproject.toml) is the single source of harness configuration. Humans own it and [`preferences/`](preferences/); both are agent-protected.

If an agent edits a forbidden file, the file will be unstaged (not allowed to commit). A forbidden pattern by an agent (e.g. `# noqa` will also prevent their commit and force them to fix it.)

Review/edit current "[preferences.py](preferences/preferences.py)":

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

## Read this before a first run.

The gate bounds what any **commit** may touch, but the worker itself is **not** sandboxed to this repo unless you set that config. Consider the balance: without access it cannot do much. With machine access it can wreak havoc. Under a permissive mode it can run arbitrary shell. You are authorizing real changes. Choose the worker and permission mode deliberately.

1. **This harness does not sandbox agents.** It _tries_ to harness bad code in loops via gates. Sandboxing agents will, e.g. prevent them from maintaining git, running Playwright, being seen as trustworthy by semgrep leading to cyclical failures, etc.

2. **The gate is a guardrail, not a jail.** Agents are crafty, like people. They will find a way to complete a task at all costs. **Trust nothing and no one.**

3. **Mind your usage limits.** `harness run` works agents to the cap set. You can easily burn through your tokens, context windows, and provider usage limits. **Workers continue running as long as there is work to do.**

4. **`docs/PROMPT.md` tells the worker to push or not**.

5. Protect `main` and run the loop on its own branch.

6. **100% coverage does not mean good tests.** That is quantity, not quality. (Upcoming feature: mutation testing)

7. **Note**: `semgrep --config auto` needs network for semgrep registry rules.

</details>

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

</details>

<details>
  <summary>

## Coordination </summary>

- Use `git log --oneline <branch>..HEAD` to show what's unpushed.
- There is NO worktree/branch creation by design. You can create branches/trees and run a loop in each, then merge _(if you really feel like managing that)_
- Agent duties can be contained to a part of the repo. e.g. Codex-1-frontend uses `docs/specs/frontend.md`, Claude-2-researcher `docs/specs/backend`...

### If you want to run a graph

**Recommendations for running several agents at once on one branch (no worktrees):**

- **You (human):** seed each spec once with this exact line near the top:

  ```
  Spec claimed by agent: <unclaimed>
  ```

- **This exact block** into [PROMPT.md line 3](docs/PROMPT.md#L3):

  ```
  Other agents are working this repo. Before touching code, pick a spec whose claim line is <unclaimed>, replace it with your exact name `<your-agent-id>-<spec-you-worked>-<RALPH_ITERATION>/<MAX_ITERATIONS>`, and commit that claim first. Own that spec's file and its
  tests. Set the line back to <unclaimed> on your last commit.
  ```

- What fails when agents do not claim specs/work: agents all pick the top-priority spec, duplicate work, and leave a half-staged git index.
- What fails with too little time i.e. MAX_MINUTES too low: a worker dies mid-`gate` before it can commit. Give each iteration enough minutes to finish (the gate itself takes a while). One successful iteration needs ~2-3 min of pure overhead aside from 'real' work.
  - A worker killed too soon leaves its spec claim STUCK: spec stays locked to its name. No other agent will take it until a human resets the line to `<unclaimed>`.
  - preflight on git commit: ~ a few seconds
  - full gate on git push: ~20-48s
  - push + cleanup: ~ few seconds -
- Do not rely on agent names for coordination: agents self-name inconsistently and can collide (e.g. two both call themselves the same thing). Names are for human blame/log-matching only; the claim line + committed code are what actually coordinate.

</details>
