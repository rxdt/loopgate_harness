"""Command-line interface for the ralph harness. Plain pass-through commands, no objects."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from configparser import ConfigParser
from copy import deepcopy
from datetime import datetime, timezone
from importlib import util
from pathlib import Path
from shutil import copy2, rmtree, which
from typing import Annotated, Any

from rich import print as rprint
from rich.json import JSON
from rich.table import Table, box
from tomlkit import TOMLDocument, document, dumps, parse, table
from typer import Argument, Exit, Option, Typer, colors, confirm, echo, secho, style

from harness.config import ASSETS, CLAUDE_RULES, CODEX_RULES, PHASES, TOOLS
from harness.gate import console, gates, run_git

app = Typer(
    name="loopgate",
    help="Commands to harness loops into meeting quality standards",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None if os.environ.get("RALPH_LOOP") else "rich",
)
REPO_ROOT = gates().repo_root
REPO_ROOT_STR = str(REPO_ROOT)
IS_WINDOWS = sys.platform == "win32"


def setup_git_hooks(env_bin: Path) -> Path:
    """Saves the installed `harness` executable's PATH for Git hooks to run. `pyproject.toml [project.scripts]
    harness = "harness.cli:main"` creates an executable and we record its path here. A hook uses a path "
    instead of needing e.g. active `.venv` or calling `uv run`

    Arguments:
        env_bin: bin directory of the environment the dependency install just populated

    Returns:
        The path of the file that records the harness command.
    """
    rprint("\n[cyan2]setting git hooks[/cyan2] `git config core.hooksPath .githooks`")
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=REPO_ROOT_STR, check=True)
    binary = env_bin / ("harness.exe" if IS_WINDOWS else "harness")
    recorded = REPO_ROOT / run_git(["rev-parse", "--git-common-dir"]).strip() / "harness-path"
    recorded.write_text(f"{binary.as_posix()}\n", encoding="utf-8", newline="\n")
    if IS_WINDOWS:
        rprint("Windows is experimental. Reoprt issues https://github.com/rxdt/loopgate_harness/issues")
    else:
        subprocess.run(("ls", "-l", ".githooks"), cwd=REPO_ROOT_STR, check=True)
    rprint(f"\nRecorded in {recorded} is the path to executable {env_bin}")

    return recorded


def run_worker(command: list[str], log: Path, verbose: bool) -> int:
    """Run the worker command, always saving stdout and optionally streaming it live.

    ralph.sh gets the prompt as a string to pass to the worker in the command

    Args:
        command: The worker argv to execute.
        log: File path that always receives the raw stdout.
        verbose: When True, also stream compacted output live to the terminal.

    Returns:
        The worker process's exit code.
    """
    with log.open("w", encoding="utf-8") as handle:
        if not verbose:
            return subprocess.run(command, cwd=REPO_ROOT_STR, stdout=handle, check=False).returncode
        with subprocess.Popen(command, cwd=REPO_ROOT_STR, stdout=subprocess.PIPE, text=True) as process:
            for line in process.stdout or ():
                handle.write(line)
                handle.flush()
                try:
                    rendered = JSON(line, indent=None)
                except json.JSONDecodeError:
                    rendered = line
                with console.capture() as captured:
                    console.print(rendered, end="\n")
                sys.stdout.write(captured.get())
                sys.stdout.flush()
            return process.wait()


def check(name: str, command: Callable[[], dict[str, list[str]]]) -> dict[str, list[str]]:
    """Run a named phase (preflight or gate), render its summary, and exit by its verdict.

    Args:
        name: Phase label shown in the summary (e.g. "preflight" or "gate").
        command: Callable that runs the phase for a repo. Returns pass/fail buckets.

    Raises:
        typer.Exit: always — code 1 if anything failed, else code 0.
    """
    results = command()
    summary = Table(title="\nHarness Summary\n", title_style="bold grey82", box=None, padding=(0, 5))
    summary.add_column("RESULT")
    summary.add_column("CHECK", style="bold dim white")
    for passed in results["pass"]:
        summary.add_row("[green]PASSED[/]", passed)
    for fail in results["fail"]:
        summary.add_row("[bold red]FAILED[/]", fail)
    for warn in results["warn"]:
        summary.add_row("[yellow]WARNED[/]", warn)
    console.print(summary, justify="center")
    final = "\n[bold red]rejected by harness[/]" if results["fail"] else f"[green]ok: {name} pass[/]"
    console.print(final, justify="center")
    raise Exit(code=1 if results["fail"] else 0)


@app.command(help="Fast pre-commit checks (lint/format) plus agent containment")
def preflight() -> None:
    """Dumb pass-through to the fast pre-commit gate."""
    check("preflight", gates().run_preflight)


@app.command(help="Pre-push checks match the CI gate exactly (lint, types, security, etc.)")
def gate() -> None:
    """Dumb pass-through to the full pre-push gate; exit nonzero if anything fails."""
    check("gate", gates().run_gate)


@app.command(hidden=True, help="Git prepare-commit-msg hook. Called by .githooks, not by people.")
def prepare_commit_msg(
    args: Annotated[list[str] | None, Argument(help="What git passes the hook")] = None,
) -> None:
    """Dumb pass-through to prepare_commit_msg hook logic. Hidden git-only usage, not a human command.

    Args:
        args: The hook's own arguments: message file, then optionally the source and its commit.

    Raises:
        typer.Exit: the hook's status; git aborts the commit on 1.
    """
    raise Exit(code=gates().prepare_commit_msg(["prepare-commit-msg", *(args or [])]))


@app.command(help="Show harness configuration and capabilitie in pyproject.toml")
def info() -> None:
    """Print everything the harness reads from [tool.harness] so nobody has to open pyproject.toml."""
    config = Table(
        title="\n[cyan2]Basic Harness Configuration Settings[/]\n[dim cyan2]See pyproject.toml for more[/]",
        box=box.MINIMAL,
    )
    for title, checks in PHASES:
        config.add_row(f"[bold cyan]{title}[/]", "")
        for name, command in checks.items():
            config.add_row(f"  {name}", f"[dim]{' '.join(command)}[/]")
    console.print(config)


@app.command(help="Count agent run logs under scratchpad/runs")
def status() -> None:
    """Count run logs and point at the newest one."""
    runs = REPO_ROOT / "scratchpad" / "runs"
    logs = sorted(runs.glob("*.jsonl")) if runs.is_dir() else [""]
    secho(f"{len(logs)} run log(s) in {runs}\nnewest: {logs[-1]}", fg=colors.CYAN, bold=True)


def cleanup(cwd: Path) -> bool:
    """Cleans the new local repository of old loopgate things.

    Arguments:
        cwd: the current working directory to leave a clean template in

    Returns:
        bool True if successful
    """
    if not ((cwd / "README.template.md").is_file() and (cwd / "temp.pyproject.toml").is_file()):
        return False
    clean_tree = not run_git(["status", "--porcelain"], cwd).strip()
    (cwd / "README.template.md").replace(cwd / "README.md")
    (cwd / "temp.pyproject.toml").replace(cwd / "pyproject.toml")
    for file_name in (".github/workflows/publish.yml", "CONTRIBUTING.md"):
        (cwd / file_name).unlink(missing_ok=True)
    for directory in (cwd / "dist", cwd / "harness" / "tests", cwd / ".assets"):
        if directory.exists():
            rmtree(directory)
    if run_git(["rev-parse", "--short", "HEAD"], cwd).startswith("867f2df") and clean_tree:
        run_git(["commit", "-a", "--amend", "--no-edit"], cwd)
    return True


@app.command(
    help="Only run this if setting up a project from the template cloned from Github at project root: injects"
    " project name in pyproject.toml, syncs dependencies, adds githooks, DELETES unecessary files!"
)
def install() -> None:
    """Used by template cloned from Github. Syncs dependencies, and activates the git hooks."""
    rprint("\n[cyan2]installing dependencies[/cyan2]")
    env_bin, args = infer_env_manager()
    subprocess.run(tuple(args), cwd=REPO_ROOT_STR, check=True)
    if args[0] == "poetry":
        poetry_python = subprocess.run(
            ["poetry", "env", "info", "--executable"],
            cwd=REPO_ROOT_STR,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        env_bin = Path(poetry_python).parent
    cleanup(REPO_ROOT)
    setup_git_hooks(env_bin)
    check_for_timeout_and_prompt()
    rprint("\n[cyan2]If install left git dirty, commit unstaged changes[/]")


def infer_env_manager() -> tuple[Path, list[str]]:
    """Use environment signals to make a best-guess of which dependency manager is used.

    Returns:
        env_bin: the path to the binary of the virtual env
        args: arguments needed to install dependencies
    """
    python_path = sys.executable
    env = os.environ.get("VIRTUAL_ENV", "")
    env_bin = Path(python_path).parent
    scripts = "Scripts" if IS_WINDOWS else "bin"
    if (REPO_ROOT / "uv.lock").is_file():
        env_bin = REPO_ROOT / ".venv" / scripts
        args = ["uv", "sync"]
    elif (REPO_ROOT / "poetry.lock").is_file():
        args = ["poetry", "install"]
    elif (
        any(key.startswith("UV_") for key in os.environ)
        or which("uv") is not None
        or "uv" in Path(env).name.lower()
    ):
        env_bin = REPO_ROOT / ".venv" / scripts
        args = ["uv", "sync"]
    elif "pypoetry" in env or "pypoetry" in python_path:
        args = ["poetry", "install"]
    else:
        args = [python_path, "-m", "pip", "install", "-r", "requirements.txt", "-e", "."]
    return env_bin, args


@app.command(hidden=True)
def check_for_timeout_and_prompt() -> str | None:
    """Offer install when macOS lacks a timeout tool. Linux has `timeout`, macOS needs coreutils.gtimeout.
    Returns:
        which gtimeout/timeout a Linux or MocOS user has installed
    """
    if IS_WINDOWS:
        return None
    if not which("gtimeout") or not which("timeout"):
        rprint("\n[yellow]macOS harness needs timeout/gtimeout from coreutils to run loops[/yellow]")
        if not which("brew"):
            rprint("Get Homebrew https://brew.sh then run `brew install coreutils` or `sudo port install`")
        elif (
            confirm("\nInstall `brew install coreutils` now?", abort=True)
            and subprocess.run(("brew", "install", "coreutils"), check=False).returncode == 0
        ):
            rprint(
                "\nIf timeout or gtimeout is installed, you can run loops with `harness run`"
                "\nIf using `uv` or `pip` activate env to use [green]`harness`[/green] commands[turquoise2]"
            )
    return which("gtimeout") or which("timeout")


@app.command(
    help="Run one harnessed ralph loop with <agent>, e.g. harness run claude 3 20.\n\n"
    f"Agents in pyproject.toml (from tool.harness.agents): {', '.join(gates().agents)}"
)
def run(
    agent: str,
    num_iterations: Annotated[int, Argument()] = 2,
    max_minutes: Annotated[int, Argument()] = 20,
    verbose: Annotated[bool, Argument()] = True,
    model: Annotated[str | None, Option(help="Override the agent's model")] = None,
) -> None:
    """ralph.sh runs once for one agent.

    Args:
        agent: Agent key to run.
        num_iterations: Number of ralph loop iterations.
        max_minutes: Wall-clock budget per run in minutes.
        verbose: When True, stream the worker's output live to the terminal.
        model: Optional model id replaces the default

    Raises:
        typer.Exit: code 2 for an unknown agent or non-positive counts, else the worker's exit code.
    """
    raise_issues(agent, num_iterations, max_minutes)
    cwd = Path.cwd()
    runs = cwd / "scratchpad" / "runs" / datetime.now(tz=timezone.utc).strftime("%Y%m%d") / agent
    runs.mkdir(parents=True, exist_ok=True)
    worker_id = f"{max((int(p.stem) for p in runs.glob('[0-9][0-9][0-9][0-9].jsonl')), default=0) + 1:04d}"
    prompt = (cwd / "docs" / "PROMPT.md").read_text(encoding="utf-8").rstrip("\n")  # hand agent fixed ID
    os.environ["RALPH_PROMPT"] = f"Your agent id prefix is `{agent}-{worker_id}`\n\n{prompt}"
    log = runs / f"{worker_id}.jsonl"  # each log file is one run / ralph invocation, not one iteration
    loop_dir = Path(__file__).resolve().parent
    launcher = (
        ["powershell.exe", "-NoProfile", "-File", str(loop_dir / "ralph.ps1")]
        if IS_WINDOWS  # support windows with twin script
        else [str(loop_dir / "ralph.sh")]
    )
    agent_argv = [tok.replace("{log_path}", str(log)) for tok in gates().agents[agent]]
    if model:
        agent_argv[agent_argv.index("--model") + 1] = model
    command = [*launcher, str(num_iterations), str(max_minutes), *agent_argv]
    echo(f"harness: {' '.join(command)} -> {log}", err=True)
    raise Exit(code=run_worker(command, log, verbose))


def raise_issues(agent: str, num_iterations: int, max_minutes: int):
    """Raise issues with input or otherwise

    Args:
        agent: Agent name to loop. Case-folded and looked up in AGENTS.
        num_iterations: Number of ralph loop iterations. Must be >= 1.
        max_minutes: Wall-clock budget per run in minutes. Must be >= 1.

    Raises:
        Exit: typer.Exit(code=2) for an unknown agent or non-positive counts, else the worker's exit code.

    Returns:
        true if successfully set env var and no issues raised
    """
    msg1 = msg2 = msg3 = ""
    agent = agent.casefold()
    if agent not in gates().agents:
        msg1 = f"Unknown agent name '{agent}'"
    if num_iterations < 1 or max_minutes < 1:
        msg2 = "iterations and max_minutes must be >= 1"
    timeout = check_for_timeout_and_prompt()
    if not IS_WINDOWS and not timeout:
        msg3 = "gtimeout or timeout is required"
    if msg1 or msg2 or msg3:
        secho(f"{msg1} {msg2} {msg3}", err=True, fg=colors.MAGENTA, bold=True)
        raise Exit(code=2)
    os.environ["TIMEOUT"] = timeout or ""
    return bool(os.environ["TIMEOUT"])


@app.command(
    help="Bootstraps harness into an existing repository. Set up your project for loops and gates. Run this "
    "if you've installed into an existing project and are setting up for the first time. Adds to configs."
)
def init() -> None:
    """Add harness assets, merge tool config, write the CI gate, and enable hooks"""
    init_confirm = confirm(
        style("Confirm loopgate can read configs and write configs to wire checks:", fg=10), abort=False
    )
    if not init_confirm:
        secho("Run `harness init` if you do want to configure loopgate", fg=colors.MAGENTA, bold=True)
        raise Exit(code=0)
    write_harness_config()
    if confirm(style("Can we wire harness into githooks so quality checks run?", fg=10), abort=True):
        if hoist() and setup_git_hooks(Path(sys.executable).parent):
            check_for_timeout_and_prompt()
            configure_agents()
            rprint("\n[cyan2]Success. Try running loops with `harness run <agent>`[/]")
    else:
        rprint(["\n[186]Part or all of the harness failed to install "])


def write_harness_config() -> None:
    """Takes user's configs and creates a pyproject.toml or appends to an existing pyproject.toml."""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    user_pyproject: TOMLDocument = document()
    user_pyproject_tools: dict[str, Any] = {}
    if pyproject_path.is_file():
        user_pyproject: TOMLDocument = parse(pyproject_path.read_text(encoding="utf-8"))
        user_pyproject_tools: dict[str, Any] = user_pyproject.setdefault("tool", table())
        user_harness = user_pyproject_tools.get("harness", {})
        checks = user_harness.get("gate", {})
        if checks and checks.get("test"):
            confirm(style("Seems loopgate may be wired already. Continue?", fg=10))
    template: Path = Path(__file__).resolve().with_name("temp.pyproject.toml")
    template_contents: TOMLDocument = parse(template.read_text(encoding="utf-8"))
    for t in template_contents["tool"]:
        if t in user_pyproject_tools:
            template_contents["tool"][t] = deepcopy(user_pyproject_tools[t])
    contents = ConfigParser(interpolation=None)
    contents.read([(REPO_ROOT / "tox.ini"), (REPO_ROOT / "setup.cfg")], encoding="utf-8")
    section_names = contents.sections()
    categories: dict[str, dict[str, list[str]]] = {
        "audit": {},
        "complexity": {},
        "format": {},
        "lint": {},
        "security": {},
        "test": {},
        "types": {},
    }
    inspect_configs(section_names, user_pyproject_tools, categories, template_contents["tool"])
    user_pyproject.setdefault("tool", {}).update(template_contents["tool"])
    pyproject_path.write_text(dumps(user_pyproject), encoding="utf-8")


# ruff: ignore[complex-structure,too-many-branches,too-many-locals] # complexipy: ignore
def inspect_configs(  # pylint: disable=locally-disabled,too-many-branches,too-many-locals
    section_names: list[str],
    user_pyproject_tools: dict[str, Any],
    categories: dict[str, dict[str, list[str]]],
    template: dict[str, Any],
) -> None:
    """For each tool in a pre-built internal map, see if user has the tool installed and configured.

    Args:
        section_names: Configuration section names found outside pyproject.toml.
        user_pyproject_tools: Tool configurations from the user's pyproject.toml.
        categories: Installed tools grouped by check category.
        template: Tool configurations from the harness template.
    """
    preflight_stage = template["harness"]["preflight"]
    gate_stage = template["harness"]["gate"]
    for tool_name, tool_config in TOOLS.items():
        standalone, in_pyproject, other_config, pop_table = False, False, False, True
        category: str = tool_config["category"]
        harness_stage = preflight_stage if category in {"complexity", "format", "lint"} else gate_stage
        args = tool_config.get("args")
        if not args:
            continue
        if not (util.find_spec(tool_name) or which(args[0])):
            harness_stage.pop(tool_name, None)
            template.pop(args[0], None)
            continue
        for name in tool_config.get("filenames", []):
            if (REPO_ROOT / name).is_file():
                standalone = True
        if standalone is False and args[0] in user_pyproject_tools:
            pop_table = False
            in_pyproject = True
        if not (standalone or in_pyproject):
            if tool_name in section_names or f"tool:{args[0]}" in section_names:
                other_config = True
            elif (
                not categories["test"]
                and tool_name == "pytest"
                and "testenv" in section_names
                and which("tox")
            ):
                other_config = True
                command = "tox"
                args = [command]

        if standalone or in_pyproject or other_config:
            current_tool_in_category = harness_stage.get(category) or tool_name
            if not categories[category]:
                if "ruff" in current_tool_in_category and not tool_name.startswith("ruff"):
                    template.get("ruff", {}).pop(category[0], None)
                elif pop_table:
                    template.pop(current_tool_in_category[0], None)
            harness_stage.pop(tool_name, None)
            harness_stage[category if not categories[category] else tool_name] = args
            categories[category][tool_name] = args
            if pop_table:
                template.pop(args[0], None)


def hoist() -> bool:
    """Hoist files expected for loops to root of repo."""
    if not (ASSETS["docs"][0].is_dir() and ASSETS["githooks"][0].is_dir()):
        rprint("Harness is missing required assets: `docs/` and `githooks/`")
        return False
    confirm(
        "Can loopgate add add needed files? If you have pre-existing files they will remain.\nWHY ADD:"
        "\nMutation tests promote good tests.\n`preferences` allow for checks beyond what tooling catches and"
        " demonstrate Hypothesis property tests\n`docs` contain the instructions and memory for loops\nGit "
        "hooks ensure agents follow quality standards!\n`.github` workflows configure CI\n`scratchpad/` "
        "allows local agent use and contains a `runs/` directory for logs. "
        "",
        abort=True,
    )
    repo_root = REPO_ROOT
    ASSETS["githooks"][1].mkdir(parents=True, exist_ok=True)
    hoist_script = (ASSETS["githooks"][0] / "hoist").as_posix()
    run_git(["-c", 'alias.loopgate-hoist=!f() { sh "$1"; }; f', "loopgate-hoist", hoist_script], REPO_ROOT)
    for key, paths in ASSETS.items():
        source, destination = paths
        for source_path in source.rglob("*"):
            destination_path = destination / source_path.relative_to(source)
            if source_path.is_dir():
                destination_path.mkdir(parents=True, exist_ok=True)
            elif not destination_path.exists():
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                copy2(source_path, destination_path)
        rprint(f"{key} add attempt at {destination}")
    (repo_root / "scratchpad" / "runs").mkdir(parents=True, exist_ok=True)
    (repo_root / "scratchpad" / "runs" / ".gitkeep").touch()
    rprint(f"scratchpad/ and runs/ added at {repo_root}")
    return True


@app.command(
    help="This will set an env variable in Claude and Codex configuration files and add rules that they "
    "cannot edit that variable. This ensures interactive agents (e.g. in IDEs) are held to the same standards"
    " as headless agents. It makes gates un-bypassable. You can always return to rerun this later too."
)
def configure_agents() -> None:
    """Configure Claude and Codex for contained loops."""
    home = Path.home()
    claude_path, codex_path, bak = (
        home / ".claude/settings.json",
        home / ".codex/config.toml",
        home / ".codex/config.toml.bak",
    )
    claude = json.loads(claude_path.read_text("utf-8") if claude_path.is_file() else "{}")
    codex = parse(codex_path.read_text("utf-8") if codex_path.is_file() else "")
    cl_confirm = confirm(style("Can we update CLAUDE rules and settings?", fg=10), abort=True)
    if cl_confirm and claude_path.is_file():
        rprint(f"settings.json edited. Original copy at {copy2(claude_path, f'{claude_path}.bak')}")
    claude.setdefault("env", {})["RALPH_LOOP"] = "1"
    permissions: dict[str, Any] = claude.setdefault("permissions", {})
    permissions["deny"] = list(set(permissions.get("deny", [])) | CLAUDE_RULES)
    cx_confirm = confirm(style("Can we update CODEX rules and settings?", fg=10), abort=True)
    if cx_confirm and codex_path.is_file():
        rprint(f"config.toml edited. Original backup at {copy2(codex_path, bak)}")
    codex.setdefault("shell_environment_policy", {}).setdefault("set", {})["RALPH_LOOP"] = "1"
    for path, contents in {
        claude_path: f"{json.dumps(claude, indent=2)}\n",
        codex_path: dumps(codex),
        home / ".codex" / "rules" / "loopgate.rules": CODEX_RULES,
    }.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point: run the app so typer.Exit sets the process exit code.
    Args:
        argv: Optional command-line arguments passed to the Typer app.
    """
    app(args=argv)
