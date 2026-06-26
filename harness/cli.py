"""Command-line interface for the ralph harness. Plain pass-through commands, no objects."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import tomlkit
import typer
from packaging.utils import canonicalize_name
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from harness import gate as gate_module

app = typer.Typer(
    name="ralph-harness",
    help="Commands to harness the loops",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)

AGENTS: dict[str, tuple[str, ...]] = {
    "claude": (
        "claude",
        "-p",
        "--permission-mode",
        "acceptEdits",
        #        "--bare",  # one-shot minimal run. skips MCP, hooks, plugins, CLAUDE.md, reduced startup
        # sets CLAUDE_CODE_SIMPLE, no use CLAUDE_CODE_OAUTH_TOKEN (ANTHROPIC_API_KEY billed), no ~/.claude log
        "--no-session-persistence",  # no-save session data is good for disposable automation tasks
        "--output-format",
        "stream-json",
        "--verbose",
    ),
    "codex": (
        "env",  # run with modfied `env`
        "-u",  # unset the following variable
        "CODEX_THREAD_ID",  # 1. clear session thread before launch agents:
        "-u",
        "CODEX_CONVERSATION_ID",  # 2. cleared so a child agent does not bind to parent conversation
        "-u",
        "CODEX_SESSION_ID",  # 3. ensures clean session, even with Orchestrator agent
        "codex",
        "exec",
        "-m",
        "gpt-5.5",
        "--json",
        "--sandbox",
        "danger-full-access",
        "-",
    ),
    "agy": ("agy", "--log-file", "scratchpad/runs/agy/agy.log", "--print", "--dangerously-skip-permissions"),
    "copilot": ("sh", "-c", 'copilot --output-format json --stream on --allow-all-tools -p "$(cat)"'),
}


def run_worker(command: list[str], cwd: Path, log: Path, verbose: bool) -> int:
    """Run the worker command, always saving stdout and optionally streaming it live.

    Args:
        command: The worker argv to execute.
        cwd: Working directory for the subprocess.
        log: File path that always receives the raw stdout.
        verbose: When True, also stream compacted output live to the terminal.

    Returns:
        The worker process's exit code.
    """
    with log.open("w", encoding="utf-8") as handle:
        if not verbose:
            return subprocess.run(command, cwd=str(cwd), stdout=handle, check=False).returncode
        jq = shutil.which("jq")
        with subprocess.Popen(command, cwd=str(cwd), stdout=subprocess.PIPE, text=True) as process:
            for line in process.stdout or ():
                handle.write(line)
                handle.flush()
                sys.stdout.write(format_live_line(line, jq))
                sys.stdout.flush()
            return process.wait()


def format_live_line(line: str, jq: str | None) -> str:
    """Compact valid JSONL for terminal output; preserve invalid lines exactly.

    Args:
        line: A single raw output line from the worker.
        jq: Path to the `jq` binary, or None if unavailable.

    Returns:
        The compacted single-line JSON, or the original line if it is not valid JSON.
    """
    if jq:
        rendered = subprocess.run(
            (jq, "-C", "-c", "."), input=line, text=True, capture_output=True, check=False
        )
        if rendered.returncode == 0 and rendered.stdout:
            return rendered.stdout
    try:
        return f"{json.dumps(json.loads(line), separators=(',', ':'))}\n"
    except json.JSONDecodeError:
        return line


def check(name: str, command: Callable[[Path], dict[str, list[str]]]) -> dict[str, list[str]]:
    """Run a named phase (preflight or gate), render its summary, and exit by its verdict.

    Args:
        name: Phase label shown in the summary (e.g. "preflight" or "gate").
        command: Callable that runs the phase for a repo. Returns pass/fail buckets.

    Raises:
        typer.Exit: always — code 1 if anything failed, else code 0.
    """
    results = command(Path.cwd())

    table = Table(title="\nHarness Summary\n", title_style="bold grey74", box=None, padding=(0, 5))
    console = Console(force_terminal=True, stderr=True)
    table.add_column("PASSED", style="bold dim white")
    table.add_column("FAILED")
    for passed in results["pass"]:
        table.add_row(passed, "[green]✔ PASSED[/]")
    for fail in results["fail"]:
        table.add_row(fail, "[bold red]✖ FAILED[/]")
    console.print(table, justify="center")
    final = "\n[bold red]rejected by harness[/]" if results["fail"] else f"[green]ok: {name} pass[/]"
    console.print(final, justify="center")

    raise typer.Exit(code=1 if results["fail"] else 0)


@app.command(help="Fast pre-commit checks (lint/format) plus agent containment")
def preflight() -> None:
    """Dumb pass-through to the fast pre-commit gate."""
    check("preflight", gate_module.run_preflight)


@app.command(help="Pre-push checks match the CI gate exactly (lint, types, security, etc.)")
def gate() -> None:
    """Dumb pass-through to the full pre-push gate; exit nonzero if anything fails."""
    check("gate", gate_module.run_gate)


@app.command(help="Count agent run logs under scratchpad/runs")
def status() -> None:
    """Count run logs and point at the newest one."""
    runs = Path.cwd() / "scratchpad" / "runs"
    logs = sorted(runs.glob("*.jsonl")) if runs.is_dir() else []
    typer.secho(f"{len(logs)} run log(s) in {runs}", fg=typer.colors.CYAN, bold=True)
    if logs:
        typer.secho(f"newest: {logs[-1]}", fg=typer.colors.GREEN, bold=True)


@app.command(help="Setup project: inject project name, sync dependencies, set up githooks")
def install(name: str) -> None:
    """Inject NAME (PEP 503) into pyproject, sync deps, and activate the git hooks.

    Args:
        name: Project name, canonicalized to a PEP 503 form before being written.
    """
    cwd = Path.cwd()

    pyproject = cwd / "pyproject.toml"
    document = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
    document.setdefault("project", tomlkit.table())["name"] = canonicalize_name(name, validate=True)
    pyproject.write_text(tomlkit.dumps(document), encoding="utf-8")
    new_name = tomlkit.parse(pyproject.read_text(encoding="utf-8"))["project"]["name"]
    rprint(f"\n[cyan2]project name[/cyan2] '{new_name}' set in `pyproject.toml`")

    rprint("\n[cyan2]installing dependencies[/cyan2] with `uv sync`")
    subprocess.run(("uv", "sync"), cwd=str(cwd), check=True)

    rprint("\n[cyan2]setting git hooks[/cyan2] with `git config core.hooksPath .githooks`:")
    subprocess.run(("git", "config", "core.hooksPath", ".githooks"), cwd=str(cwd), check=True)
    typer.echo(
        subprocess.run(
            ("git", "config", "core.hooksPath"), cwd=str(cwd), capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    subprocess.run(("ls", "-l", ".githooks"), cwd=str(cwd), check=True)

    rprint(
        "\n[turquoise2]You must ACTIVATE env[/turquoise2] `source .venv/bin/activate`"
        " to use the [green]`harness`[/green] command.\n"
        "\n[turquoise2]python:[/turquoise2] project supports >=3.11"
        "\n[turquoise2]PIN NEWER[/turquoise2] local Python e.g. `uv python pin 3.13 && uv sync`"
    )


@app.command(help="Run one harnessed ralph loop with <agent>, e.g. harness run claude 3 20")
def run(
    agent: str,
    num_iterations: Annotated[int, typer.Argument()] = 2,
    max_minutes: Annotated[int, typer.Argument()] = 20,
    verbose: Annotated[bool, typer.Argument()] = True,
) -> None:
    """ralph.sh runs once for one agent.

    Args:
        agent: Agent key to run. Case-folded and looked up in AGENTS.
        num_iterations: Number of ralph loop iterations. Must be >= 1.
        max_minutes: Wall-clock budget per run in minutes. Must be >= 1.
        verbose: When True, stream the worker's output live to the terminal.

    Raises:
        typer.Exit: code 2 for an unknown agent or non-positive counts, else the worker's exit code.
    """
    agent = agent.casefold()
    if agent not in AGENTS:
        typer.secho(
            f"unknown agent '{agent}'; choose from {', '.join(AGENTS)}",
            err=True,
            fg=typer.colors.MAGENTA,
            bold=True,
        )
        raise typer.Exit(code=2)
    if num_iterations < 1 or max_minutes < 1:
        typer.secho(
            "num_iterations and max_minutes must be >= 1", err=True, fg=typer.colors.MAGENTA, bold=True
        )
        raise typer.Exit(code=2)
    cwd = Path.cwd()
    runs = cwd / "scratchpad" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    seq = 1 + max((int(p.name.split("-", 1)[0]) for p in runs.glob("[0-9]*-*.jsonl")), default=0)
    log = runs / f"{seq:04d}-{agent}.jsonl"
    ralph = Path(__file__).resolve().parent / "ralph.sh"
    command = [str(ralph), str(num_iterations), str(max_minutes)]
    command.extend(AGENTS[agent])
    typer.echo(f"harness: {' '.join(command)} -> {log}", err=True)
    returncode = run_worker(command, cwd, log, verbose)
    raise typer.Exit(code=returncode)


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point: run the app so typer.Exit sets the process exit code.

    Args:
        argv: Command-line arguments to pass to the app, or None to read from sys.argv.
    """
    app(args=argv)
