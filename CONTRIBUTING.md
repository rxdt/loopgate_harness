# Contributing to L∞PGate Harness

Thanks for taking the time to contribute. This project is a Python harness for running agent loops behind local and CI gates. Good contributions scoped, tested, and explicit about the workflow they change.

## Before You Start

- Use Python 3.11 or newer
- Read [README.md](README.md) for the harness model, safety notes, and command overview.
- Checking existing issues and pull recommended before starting larger changes.

## Local Setup

```sh
# if on macOS, for gtimeout/timeout:
brew install coreutils

# uv recommended for fast installs
# https://docs.astral.sh/uv/getting-started/installation/#installation-methods, e.g. for macOS with curl:
curl -LsSf https://astral.sh/uv/install.sh | sh

uv sync
source .venv/bin/activate

uv run harness install loopgate  # for first time use. After: `harness install loopgate`
```

`harness install <project-name>` is the installation behavior users will experience. It rewrites the template project name and sets up hooks for a downstream project. If you run `harness install loopgate`, it runs `uv sync`, installs/enables git hooks, and sets the project name to `loopgate`.

## Project Layout

Humans edit

- `pyproject.toml`: Python and tool configuration.
- `harness/`: CLI, gate, loop runner, preferences, and harness tests. Wraps the loop that starts agents.
- `docs/PROMPT.md`: tells agents how to operate headless in the repo (mechanics)

Humans set, agents update

- `docs/specs/`: definitions of work that will end up in `src/`
- `src/`: Product code (what the agents in the loop and humans build)
- repo: THE source of truth (is work actually done). Always more reliable than docs.
- `docs/PROJECT_STATUS.md`: a human-readable state record — a summary for people to glance at, NOT authoritative. It reflects state; it doesn't define it.

Agents use

- `scratchpad/`: agent sandbox and logs directory

## Making Changes

1. Create a branch with a focused name.
2. Keep the change small enough to review.
3. Add or update tests for behavior changes.
4. Prefer simple, readable Python over clever control flow.
5. Avoid suppressions such as `# noqa`, `type: ignore`, skipped tests, or coverage pragmas (comments that tell coverage tools to ignore code).
6. Keep generated or temporary files out of commits unless they are intentional project assets.

Changes to the [`harness/`](harness) itself should preserve the core contract: **local checks should mirror CI**, agents should be 'contained' at commit time via [`harness/gate.py`](harness/gate.py), and humans should be able to understand why a gate failed.

## Checks

### Where tests live

The harness's own tests live in [`harness/tests/`](harness/tests), including the Hypothesis property tests in [`test_properties.py`](harness/tests/test_properties.py).

The full suite runs as part of `harness gate` (at 100% coverage). To run only the harness tests while iterating:

```sh
uv run pytest harness/tests
```

Fast check while working [`harness/gate.py line 164`](harness/gate.py#L164)

```sh
harness preflight
```

The full gate before a pull request [`harness/gate.py line 177`](harness/gate.py#L177)

```sh
harness gate
# or, to mimic what an agent will see:
RALPH_LOOP=1 harness gate
```

Get the last loop's log

```sh
harness status
```

## Pull Requests

Good to have:

- A short summary of what changed.
- Why the change is needed.
- The checks you ran and their result.
- Screenshots or terminal output only when they clarify behavior.
- Any follow-up work that is deliberately left out.

Documentation-only changes do not need new tests, but they should be accurate against the repo.

## AI-Assisted Contributions

Given the nature of this tool, AI use is expected. The contributor is responsible for the result.

- Review generated changes before submitting.
- Run the relevant checks yourself.
