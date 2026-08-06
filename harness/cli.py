"""Command-line interface for the ralph harness. Plain pass-through commands, no objects."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import tomlkit
import typer
from packaging.utils import canonicalize_name, is_normalized_name
from rich import print as rprint
from rich.console import Console
from rich.json import JSON
from rich.table import Table

from harness.gate import gates, run_git

app = typer.Typer(
    name="loopgate",
    help="Commands to harness the loops",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)
console = Console(force_terminal=True)
REPO_ROOT_STR = str(gates.REPO_ROOT)


def setup_git_hooks(env_bin: Path, is_windows: bool) -> Path:
    """Saves the installed `harness` executable's PATH for Git hooks to run.

    `harness install` calls setup_git_hooks after dependencies and git hooks are in. Because we have this in
    pyproject.toml we create an executable: `[project.scripts] harness = "harness.cli:main"`
    With an executable and recorded path, there's no dependance. A hook uses a path instead of needing
    e.g. active `.venv` or calling `uv run...`

    Arguments:
        env_bin: bin directory of the environment the dependency install just populated
        is_windows: Operating System platform is Windows "win32"

    Returns:
        The path of the file that records the harness command.
    """
    rprint("\n[cyan2]Setting git hooks[/cyan2] `git config core.hooksPath .githooks`")
    subprocess.run(
        ["git", "config", "core.hooksPath", ".githooks"], cwd=REPO_ROOT_STR, check=True
    )
    binary = env_bin / ("harness.exe" if is_windows else "harness")
    recorded = (
        Path(
            run_git(["rev-parse", "--path-format=absolute", "--git-common-dir"]).strip()
        )
        / "harness-path"
    )
    recorded.write_text(f"{binary.as_posix()}\n", encoding="utf-8", newline="\n")
    if is_windows:
        rprint(
            "Windows is experimental. Reoprt issues https://github.com/rxdt/loopgate_harness/issues"
        )
    else:
        subprocess.run(("ls", "-l", ".githooks"), cwd=REPO_ROOT_STR, check=True)
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
            return subprocess.run(
                command, cwd=REPO_ROOT_STR, stdout=handle, check=False
            ).returncode
        with subprocess.Popen(
            command, cwd=REPO_ROOT_STR, stdout=subprocess.PIPE, text=True
        ) as process:
            for line in process.stdout or ():
                handle.write(line)
                handle.flush()
                try:
                    rendered = JSON(
                        line, indent=None
                    )  # JSON() parses the string itself, raises on non-JSON
                except json.JSONDecodeError:
                    rendered = line
                with console.capture() as captured:
                    console.print(rendered, end="\n")
                sys.stdout.write(captured.get())
                sys.stdout.flush()
            return process.wait()


def check(
    name: str, command: Callable[[], dict[str, list[str]]]
) -> dict[str, list[str]]:
    """Run a named phase (preflight or gate), render its summary, and exit by its verdict.

    Args:
        name: Phase label shown in the summary (e.g. "preflight" or "gate").
        command: Callable that runs the phase for a repo. Returns pass/fail buckets.

    Raises:
        typer.Exit: always — code 1 if anything failed, else code 0.
    """
    results = command()
    table = Table(
        title="\nHarness Summary\n", title_style="bold grey74", box=None, padding=(0, 5)
    )
    table.add_column("RESULT")
    table.add_column("CHECK", style="bold dim white")
    for passed in results["pass"]:
        table.add_row("[green]PASSED[/]", passed)
    for fail in results["fail"]:
        table.add_row("[bold red]FAILED[/]", fail)
    for warn in results["warn"]:
        table.add_row("[yellow]WARNED[/]", warn)
    console.print(table, justify="center")
    final = (
        "\n[bold red]rejected by harness[/]"
        if results["fail"]
        else f"[green]ok: {name} pass[/]"
    )
    console.print(final, justify="center")

    raise typer.Exit(code=1 if results["fail"] else 0)


@app.command(help="Fast pre-commit checks (lint/format) plus agent containment")
def preflight() -> None:
    """Dumb pass-through to the fast pre-commit gate."""
    check("preflight", gates.run_preflight)


@app.command(
    help="Pre-push checks match the CI gate exactly (lint, types, security, etc.)"
)
def gate() -> None:
    """Dumb pass-through to the full pre-push gate; exit nonzero if anything fails."""
    check("gate", gates.run_gate)


@app.command(
    hidden=True, help="Git prepare-commit-msg hook. Called by .githooks, not by people."
)
def prepare_commit_msg(
    args: Annotated[
        list[str] | None, typer.Argument(help="What git passes the hook")
    ] = None,
) -> None:
    """Dumb pass-through to prepare_commit_msg hook logic. Hidden git-only usage, not a human command.

    Args:
        args: The hook's own arguments: message file, then optionally the source and its commit.

    Raises:
        typer.Exit: the hook's status; git aborts the commit on 1.
    """
    raise typer.Exit(
        code=gates.prepare_commit_msg(["prepare-commit-msg", *(args or [])])
    )


@app.command(help="Show harness configuration and capabilitie in pyproject.toml")
def info() -> None:
    """Print everything the harness reads from [tool.harness] so nobody has to open pyproject.toml."""
    table = Table(
        title="\n[cyan2]Basic Harness Configuration Settings[/]\n[dim cyan2]See pyproject.toml for more[/]",
        box=None,
        padding=(0, 2),
    )
    phases = (
        ("agents", gates.AGENTS),
        ("preflight", gates.COMMIT_CHECKS),
        ("gate", gates.FULL_CHECKS),
        ("forbidden", gates.FORBIDDEN),
    )
    for title, checks in phases:
        table.add_row(f"[bold cyan]{title}[/]", "")
        for name, command in checks.items():
            table.add_row(f"  {name}", f"[dim]{' '.join(command)}[/]")
    console.print(table)


@app.command(help="Count agent run logs under scratchpad/runs")
def status() -> None:
    """Count run logs and point at the newest one."""
    runs = gates.REPO_ROOT / "scratchpad" / "runs"
    logs = sorted(runs.glob("*.jsonl")) if runs.is_dir() else []
    typer.secho(f"{len(logs)} run log(s) in {runs}", fg=typer.colors.CYAN, bold=True)
    if logs:
        typer.secho(f"newest: {logs[-1]}", fg=typer.colors.GREEN, bold=True)


def cleanup(cwd: Path, name: str | None) -> bool:
    """Cleans the new local repository of old loopgate things
    Arguments:
        cwd: the current working directory to leave a clean template in
        name: the new name for the project

    Returns:
        bool True if successful
    """
    if not (cwd / "README.template.md").is_file():
        return False
    (cwd / "README.template.md").replace(cwd / "README.md")
    for file_name in (
        ".banner.svg",
        ".diagram.png",
        ".infin.png",
        ".loops_agents.svg",
        ".loops.svg",
        ".github/workflows/publish.yml",
        "CONTRIBUTING.md",
    ):
        (cwd / file_name).unlink(missing_ok=True)
    for directory in (cwd / "dist", cwd / "harness" / "tests"):
        if directory.exists():
            shutil.rmtree(directory)
    gitignore = cwd / ".gitignore"
    gitignore.write_text(
        gitignore.read_text(encoding="utf-8").removesuffix("\nuv.lock\n"),
        encoding="utf-8",
    )
    document = tomlkit.parse((cwd / "pyproject.toml").read_text(encoding="utf-8"))
    project = document.setdefault("project", tomlkit.table())
    project.update({
        "name": canonicalize_name(name)
        if name and is_normalized_name(name)
        else "my-app-name",
        "version": "0.0.0",
    })
    tool = document.setdefault("tool", tomlkit.table())
    tool.setdefault("pyright", tomlkit.table()).update({
        "include": ["src", "preferences"]
    })
    tool.setdefault("pytest", tomlkit.table()).setdefault(
        "ini_options", tomlkit.table()
    ).update({"testpaths": ["tests"], "pythonpath": [".", "src"]})
    coverage = tool.setdefault("coverage", tomlkit.table())
    coverage.setdefault("run", tomlkit.table()).update({
        "source": ["src", "preferences"]
    })
    tool.setdefault("complexipy", tomlkit.table()).update({
        "paths": ["src", "preferences"]
    })
    tool.setdefault("ruff", tomlkit.table()).setdefault(
        "exclude", tomlkit.array()
    ).append("harness")
    tool.setdefault("pylint", tomlkit.table()).setdefault(
        "main", tomlkit.table()
    ).setdefault("ignore", tomlkit.array()).append("harness")
    rprint(f"\n[cyan2]project name[/cyan2] '{project['name']}' set in `pyproject.toml`")
    (cwd / "pyproject.toml").write_text(tomlkit.dumps(document), encoding="utf-8")
    return True


@app.command(
    help="Only run this if setting up a project from the template cloned from Github at project root: injects"
    " project name in pyproject.toml, syncs dependencies, adds githooks, DELETES unecessary files!"
)
def install(
    name: Annotated[str | None, typer.Argument(help="Set up project for loops")] = None,
) -> None:
    """Used by template cloned from Github. Not used by library from PyPi.
    Injects NAME (PEP 503) into pyproject, syncs dependencies, and activates the git hooks.

    Args:
        name: Optional project name, canonicalized to a PEP 503 form before being written. If name is given,
        will overwrite existing name in pyproject.toml. When ommitted, project name is left untouched.
    """
    rprint("\n[cyan2]installing dependencies[/cyan2]")
    is_windows = sys.platform == "win32"
    # Record the env the manager just filled that holds the harness executable
    if (gates.REPO_ROOT / "uv.lock").is_file():
        subprocess.run(("uv", "sync"), cwd=REPO_ROOT_STR, check=True)
        env_bin = gates.REPO_ROOT / ".venv" / ("Scripts" if is_windows else "bin")
    elif (gates.REPO_ROOT / "poetry.lock").is_file():
        subprocess.run(("poetry", "install"), cwd=REPO_ROOT_STR, check=True)
        poetry_env = subprocess.run(
            ("poetry", "env", "info", "--executable"),
            capture_output=True,
            text=True,
            check=True,
        )
        env_bin = Path(poetry_env.stdout.strip()).parent
        name = "harness"
    else:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                "requirements.txt",
                "-e",
                ".",
            ],
            cwd=REPO_ROOT_STR,
            check=True,
        )
        env_bin = Path(sys.executable).parent
    cleanup(gates.REPO_ROOT, name)
    recorded = setup_git_hooks(env_bin, is_windows)
    if not is_windows:
        check_for_timeout_and_prompt(env_bin)
    rprint(f"\nRecorded in {recorded} is the path to executable {env_bin}")
    rprint("\n[red]COMMIT UNSTAGED CHANGES[/red]")


def check_for_timeout_and_prompt(env_bin: Path) -> None:
    """Offer install when macOS lacks a timeout tool. Linux has `timeout`, macOS needs coreutils.gtimeout.
    Args:
        env_bin: Path to the harness executable
    """
    if not (shutil.which("timeout") or shutil.which("gtimeout")):
        rprint(
            "\n[yellow]macOS harness needs timeout/gtimeout from coreutils to loop[/yellow]"
        )
        if not shutil.which("brew"):
            rprint(
                "Get Homebrew https://brew.sh then run `brew install coreutils` or `sudo port install`"
            )
        elif typer.confirm("[magenta]Install now `brew install coreutils`?[/magenta]"):
            subprocess.run(("brew", "install", "coreutils"), check=False)
        else:
            rprint(
                "[yellow]skipped[/yellow]: run `brew install coreutils` before `harness run`."
            )
    rprint(
        "\nIf timeout or gtimeout is installed, you can run loops after activating the environment"
        f"\nActivate env with [turquoise2]`source {env_bin / 'activate'}`[/turquoise2] "
        "to use the [green]`harness`[/green] commands.\n"
        "\n[turquoise2]python:[/turquoise2] project supports >=3.11"
        "\nOptionally pin newer local Python with [turquoise2]`uv python pin 3.13 && uv sync`[/turquoise2]"
    )


@app.command(
    help="Run one harnessed ralph loop with <agent>, e.g. harness run claude 3 20.\n\n"
    f"Integrated agents (from tool.harness.agents): {', '.join(gates.AGENTS)}"
)
def run(
    agent: str,
    num_iterations: Annotated[int, typer.Argument()] = 2,
    max_minutes: Annotated[int, typer.Argument()] = 20,
    verbose: Annotated[bool, typer.Argument()] = True,
    model: Annotated[
        str | None, typer.Option(help="Override the agent's model")
    ] = None,
) -> None:
    """ralph.sh runs once for one agent.

    Args:
        agent: Agent key to run. Case-folded and looked up in AGENTS.
        num_iterations: Number of ralph loop iterations. Must be >= 1.
        max_minutes: Wall-clock budget per run in minutes. Must be >= 1.
        verbose: When True, stream the worker's output live to the terminal.
        model: Optional model id replaces the default

    Raises:
        typer.Exit: code 2 for an unknown agent or non-positive counts, else the worker's exit code.
    """
    agent = agent.casefold()
    if agent not in gates.AGENTS:
        typer.secho(
            f"Unknown agent name '{agent}'",
            err=True,
            fg=typer.colors.MAGENTA,
            bold=True,
        )
        raise typer.Exit(code=2)
    if num_iterations < 1 or max_minutes < 1:
        typer.secho(
            "num_iterations and max_minutes must be >= 1",
            err=True,
            fg=typer.colors.MAGENTA,
            bold=True,
        )
        raise typer.Exit(code=2)
    cwd = Path.cwd()
    runs = cwd / "scratchpad" / "runs" / datetime.now(tz=UTC).strftime("%Y%m%d") / agent
    runs.mkdir(parents=True, exist_ok=True)
    worker_id = f"{max((int(p.stem) for p in runs.glob('[0-9][0-9][0-9][0-9].jsonl')), default=0) + 1:04d}"
    # Hand the agent a fixed identity to use in claims and commits
    prompt = (cwd / "docs" / "PROMPT.md").read_text(encoding="utf-8").rstrip("\n")
    os.environ["RALPH_PROMPT"] = f"Your agent id is `{worker_id}`\n\n{prompt}"
    log = (
        runs / f"{worker_id}.jsonl"
    )  # each log file is one run / ralph invocation, not one iteration
    loop_dir = Path(__file__).resolve().parent
    # Windows has no POSIX shell/timeout so run PowerShell ralph.ps1 twin
    launcher = (
        ["powershell.exe", "-NoProfile", "-File", str(loop_dir / "ralph.ps1")]
        if sys.platform == "win32"  # support windows
        else [str(loop_dir / "ralph.sh")]
    )
    agent_argv = [tok.replace("{log_path}", str(log)) for tok in gates.AGENTS[agent]]
    if model:
        agent_argv[agent_argv.index("--model") + 1] = model
    command = [*launcher, str(num_iterations), str(max_minutes), *agent_argv]
    typer.echo(f"harness: {' '.join(command)} -> {log}", err=True)
    raise typer.Exit(code=run_worker(command, log, verbose))


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point: run the app so typer.Exit sets the process exit code.

    Args:
        argv: Command-line arguments to pass to the app, or None to read from sys.argv.
    """
    app(args=argv)
