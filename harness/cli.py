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

from harness.config import ASSETS, CATEGORIES, CLAUDE_RULES, CODEX_RULES, PHASES, TOOLS
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
def prepare_commit_msg(args: Annotated[list[str] | None, Argument(help="What git passes the hook")] = None) -> None:
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
    if not ((cwd / "README.template.md").is_file() and (cwd / "harness" / "temp.pyproject.toml").is_file()):
        return False
    clean_tree = not run_git(["status", "--porcelain"], cwd).strip()
    (cwd / "README.template.md").replace(cwd / "README.md")
    (cwd / "harness" / "temp.pyproject.toml").replace(cwd / "pyproject.toml")
    for file_name in ("mutation-score.json", ".github/workflows/publish.yml", "CONTRIBUTING.md"):
        (cwd / file_name).unlink(missing_ok=True)
    for directory in (cwd / "harness" / "tests", cwd / ".assets", cwd / ".*cache"):
        if directory.exists():
            rmtree(directory)
    if run_git(["rev-list", "--count", "HEAD"], cwd).strip() == "1" and clean_tree:
        run_git(["commit", "-a", "--amend", "--no-edit"], cwd)
    return True


@app.command(
    help="Only run this if setting up a project from the template cloned from Github at project root: injects"
    " project name in pyproject.toml, syncs dependencies, adds githooks, DELETES unecessary files!"
)
def install() -> None:
    """Used by template cloned from Github. Syncs dependencies, and activates the git hooks."""
    rprint("\n[cyan2]installing dependencies[/cyan2]")
    env_bin = infer_env_manager_and_install()
    cleanup(REPO_ROOT)
    setup_git_hooks(env_bin)
    check_for_timeout_and_prompt()
    rprint("\n[cyan2]If install left git dirty, commit unstaged changes[/]")


def infer_env_manager_and_install() -> Path:
    """Infer the dependency manager, install dependencies, and return its bin directory."""
    python_path = sys.executable
    env = os.environ.get("VIRTUAL_ENV", "")
    env_bin = Path(python_path).parent
    scripts = "Scripts" if IS_WINDOWS else "bin"
    if (
        (REPO_ROOT / "uv.lock").is_file()
        or any(key.startswith("UV_") for key in os.environ)
        or which("uv") is not None
        or "uv" in Path(env).name.lower()
    ):
        env_bin = REPO_ROOT / ".venv" / scripts
        args = ["uv", "sync"]
    elif (REPO_ROOT / "poetry.lock").is_file() or "pypoetry" in env or "pypoetry" in python_path:
        args = ["poetry", "install"]
    else:
        args = [python_path, "-m", "pip", "install", "-r", "requirements.txt", "-e", "."]
    subprocess.run(tuple(args), cwd=REPO_ROOT_STR, check=True)
    if args[0] == "poetry":
        poetry_python = subprocess.run(
            ["poetry", "env", "info", "--executable"], cwd=REPO_ROOT_STR, check=True, capture_output=True, text=True
        ).stdout.strip()
        env_bin = Path(poetry_python).parent

    return env_bin


@app.command(hidden=True)
def check_for_timeout_and_prompt() -> str | None:
    """Offer install when macOS lacks a timeout tool. Linux has `timeout`, macOS needs coreutils.gtimeout.
    Returns:
        which gtimeout/timeout a Linux or MocOS user has installed
    """
    if IS_WINDOWS:
        return None
    if not (which("gtimeout") or which("timeout")):
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
    if not confirm(
        style("\n1. Confirm loopgate can read configs and write configs to wire checks:", fg=10), default=True
    ):
        secho("Run `harness init` to configure loopgate", fg=colors.MAGENTA, bold=True)
        raise Exit(code=0)
    if write_harness_config():
        confirm(style("\n2. Can we wire githooks so quality checks run?", fg=10), default=True, abort=True)
    hoisted = hoist()
    hooks = setup_git_hooks(Path(sys.executable).parent)
    timeout = check_for_timeout_and_prompt() or IS_WINDOWS
    agents = configure_agents()
    rprint(
        f"\n[bold cyan2]RESULT:[/]\nfiles added: {hoisted}\ngit hooks available via path: {hooks}"
        f"\ntimeout-ready: {timeout}\nagents configured: {agents}"
        f"\n[bold cyan2]Can likely run loops: [/]{bool(hoisted and hooks and timeout)}\n"
        "\n[italic]Ensure your environemnt is activated to use the `harness run` command[/]\n"
    )


def write_harness_config() -> bool:
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
            confirm(style("Seems loopgate may be wired already. Continue?", fg=10), default=True, abort=True)
    template: Path = Path(__file__).resolve().with_name("temp.pyproject.toml")
    template_contents: TOMLDocument = parse(template.read_text(encoding="utf-8"))
    for t in template_contents["tool"]:
        if t in user_pyproject_tools:
            template_contents["tool"][t] = deepcopy(user_pyproject_tools[t])
    contents = ConfigParser(interpolation=None)
    contents.read([(REPO_ROOT / "tox.ini"), (REPO_ROOT / "setup.cfg")], encoding="utf-8")
    section_names = contents.sections()
    inspect_configs(section_names, user_pyproject_tools, deepcopy(CATEGORIES), template_contents["tool"])
    user_pyproject.setdefault("tool", {}).update(template_contents["tool"])
    return bool(pyproject_path.write_text(dumps(user_pyproject), encoding="utf-8"))


def find_configured_command(
    tool_name: str,
    tool_config: dict[str, Any],
    section_names: list[str],
    user_pyproject_tools: dict[str, Any],
    test_configured: bool,
) -> tuple[list[str], bool] | None:
    """Find a configured command using the configuration file precedence.

    Args:
        tool_name: Name of the tool being inspected.
        tool_config: Harness defaults for the tool.
        section_names: Sections in tox.ini and setup.cfg, if found.
        user_pyproject_tools: The tool's tables found in a user's pyproject.toml, if it exists.
        test_configured: Whether a test tool was already added to the harness config.

    Returns:
        Command arguments and whether to keep their template table, or None if no configuration was found.
    """
    args = tool_config["args"]
    if any((REPO_ROOT / filename).is_file() for filename in tool_config.get("filenames", [])):
        return args, False
    if args[0] in user_pyproject_tools:
        return args, True
    if tool_name in section_names:
        return args, False
    tox_fallback = not test_configured and tool_name == "pytest" and "testenv" in section_names and which("tox")
    if tox_fallback:
        args = ["tox"]
    return (args, False) if tox_fallback or f"tool:{args[0]}" in section_names else None


def inspect_configs(
    section_names: list[str],
    user_pyproject_tools: dict[str, Any],
    categories: dict[str, str],
    template: dict[str, Any],
) -> None:
    """For each tool in a pre-built internal map, see if user has the tool installed and configured.
    Args:
        section_names: Configuration section names found outside pyproject.toml.
        user_pyproject_tools: Tool configurations from the user's pyproject.toml.
        categories: Primary template check remaining for each category.
        template: Tool configurations from the harness template.
    """
    for tool_name, tool_config in TOOLS.items():
        category: str = tool_config["category"]
        harness_stage = template["harness"]["preflight" if category in {"complexity", "format", "lint"} else "gate"]
        args = tool_config.get("args")
        if not args:
            continue
        if util.find_spec(tool_name) is None and which(args[0]) is None:
            harness_stage.pop(tool_name, None)
            template.pop(args[0], None)
            continue
        configured_command = find_configured_command(
            tool_name, tool_config, section_names, user_pyproject_tools, "test" not in categories
        )
        if configured_command is None:
            continue
        args, keep_template_table = configured_command
        replaced_check = categories.pop(category, tool_name)
        current_args = harness_stage.pop(replaced_check, args)
        harness_stage[category if replaced_check == category else tool_name] = args
        if not keep_template_table:
            template.pop(current_args[0], None)
            template.pop(args[0], None)


def hoist() -> bool:
    """Hoist files expected for loops to root of repo."""
    if not (ASSETS["docs"][0].is_dir() and ASSETS["githooks"][0].is_dir()):
        rprint("Harness is missing required assets: `docs/` and `githooks/`")
        return False
    rprint(
        "\n[bold yellow]We will need to add these files[/]\n* Git hooks are what ensure quality checks run"
        "\n* Mutation tests promote good tests.\n* `preferences` allow for checks beyond what tooling catches"
        "and demonstrate Hypothesis property tests\n* `docs` contain the instructions and memory for loops "
        "\n*`scratchpad/` allows local agent use and contains a `runs/` directory for logs."
    )
    confirm(
        style(
            "3. Confirm, loopgate can add those files? Pre-existing files in the expected paths will remain "
            "and loopgate will skip adding them.",
            fg=10,
        ),
        default=True,
        abort=True,
    )
    repo_root = REPO_ROOT
    ASSETS["githooks"][1].mkdir(parents=True, exist_ok=True)
    hoist_script: str = (ASSETS["githooks"][0] / "hoist").as_posix()
    run_git(["-c", 'alias.loopgate-hoist=!f() { sh "$1"; }; f', "loopgate-hoist", hoist_script], REPO_ROOT)
    console.print(f"[green]\n4. Ran {hoist_script}[/]\n")
    for key, paths in ASSETS.items():
        source, destination = paths
        for source_path in source.rglob("*"):
            destination_path = destination / source_path.relative_to(source)
            if source_path.is_dir():
                destination_path.mkdir(parents=True, exist_ok=True)
            elif not destination_path.exists():
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                copy2(source_path, destination_path)
        rprint(f"`{key}/` exists {destination.exists()}")
    (repo_root / "scratchpad" / "runs").mkdir(parents=True, exist_ok=True)
    (repo_root / "scratchpad" / "runs" / ".gitkeep").touch()
    rprint(f"`scratchpad/` and `runs/` also added at {repo_root}")
    return True


@app.command(
    help="This will set an env variable in Claude and Codex configuration files and add rules that they "
    "cannot edit that variable. This ensures interactive agents (e.g. in IDEs) are held to the same standards"
    " as headless agents. It makes gates un-bypassable. You can always return to rerun this later too."
)
def configure_agents() -> bool:
    """Configure Claude and Codex for contained loops."""
    home = Path.home()
    claude_path, codex_path, bak = (
        home / ".claude/settings.json",
        home / ".codex/config.toml",
        home / ".codex/config.toml.bak",
    )
    claude = json.loads(claude_path.read_text("utf-8") if claude_path.is_file() else "{}")
    codex = parse(codex_path.read_text("utf-8") if codex_path.is_file() else "")
    cl_confirm = confirm(style("\n5.1. Can we update CLAUDE rules and settings?", fg=10), default=True, abort=True)
    if cl_confirm and claude_path.is_file():
        rprint(f"settings.json edited. Original copy at {copy2(claude_path, f'{claude_path}.bak')}")
    claude.setdefault("env", {})["RALPH_LOOP"] = "1"
    permissions: dict[str, Any] = claude.setdefault("permissions", {})
    permissions["deny"] = list(set(permissions.get("deny", [])) | CLAUDE_RULES)
    cx_confirm = confirm(style("5.2. Can we update CODEX rules and settings?", fg=10), default=True, abort=True)
    if cx_confirm and codex_path.is_file():
        rprint(f"config.toml edited. Original backup at {copy2(codex_path, bak)}\n")
    codex.setdefault("shell_environment_policy", {}).setdefault("set", {})["RALPH_LOOP"] = "1"
    claude_path.parent.mkdir(parents=True, exist_ok=True)
    claude_path.write_text(f"{json.dumps(claude, indent=2)}\n", encoding="utf-8")
    codex_path.parent.mkdir(parents=True, exist_ok=True)
    codex_path.write_text(dumps(codex), encoding="utf-8")
    codex_rules_path = home / ".codex" / "rules" / "loopgate.rules"
    codex_rules_path.parent.mkdir(parents=True, exist_ok=True)
    codex_rules_path.write_text(CODEX_RULES, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point: run the app so typer.Exit sets the process exit code.
    Args:
        argv: Optional command-line arguments passed to the Typer app.
    """
    app(args=argv)
