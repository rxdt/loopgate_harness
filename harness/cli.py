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
from packaging.utils import canonicalize_name
from rich import print as rprint
from rich.console import Console
from rich.json import JSON
from rich.table import Table

from harness import gate as gate_module

app = typer.Typer(
    name="loopgate",
    help="Commands to harness the loops",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)


def run_worker(command: list[str], cwd: Path, log: Path, verbose: bool) -> int:
    """Run the worker command, always saving stdout and optionally streaming it live.

    ralph.sh gets the prompt as a string to pass to the worker in the command

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
        console = Console()
        with subprocess.Popen(command, cwd=str(cwd), stdout=subprocess.PIPE, text=True) as process:
            for line in process.stdout or ():
                handle.write(line)
                handle.flush()
                try:
                    rendered = JSON(line, indent=None)  # JSON() parses the string itself, raises on non-JSON
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
    if os.environ.get("RALPH_LOOP"):
        typer.secho(
            json.dumps(
                {
                    "Harness Summary": {
                        "PASSED": results["pass"],
                        "FAILED": results["fail"],
                        "result": "rejected by harness" if results["fail"] else f"ok: {name} pass",
                    }
                },
                indent=0,
            )
        )
    else:
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


@app.command()
def ensure_timeout_tool() -> None:
    """Warn (and offer to install) when `harness run` lacks a timeout tool, macOS case.
    Windows uses ralph.ps1 and has no timeout tool so no-op here.

    Linux ships `timeout`; macOS needs `brew install coreutils` (for `gtimeout`). If neither is on PATH
    on a non-Windows host, prompt to install via Homebrew.
    """
    if sys.platform == "win32" or shutil.which("timeout") or shutil.which("gtimeout"):
        return
    rprint("\n[yellow]macOS harness needs timeout/gtimeout from coreutils[/yellow]")
    if not shutil.which("brew"):
        rprint(
            "no Homebrew https://brew.sh then run `brew install coreutils`, or `sudo port install coreutils`"
        )
    elif typer.confirm("Allow install now with `brew install coreutils`?"):
        subprocess.run(("brew", "install", "coreutils"), check=False)
    else:
        rprint("[yellow]skipped[/yellow] — run `brew install coreutils` before `harness run`.")


@app.command(help="Setup project: inject project name in pyproject, sync dependencies, set up githooks")
def install(name: Annotated[str | None, typer.Argument(help="Set up project for loops")] = None) -> None:
    """Injects NAME (PEP 503) into pyproject, sync deps, and activate the git hooks.

    Args:
        name: Optional project name, canonicalized to a PEP 503 form before being written. If name is given,
        will overwrite existing name in pyproject.toml. When ommitted, project name is left untouched.
    """
    cwd = Path.cwd()

    pyproject = cwd / "pyproject.toml"
    document = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
    # Set the requested name (if any); default a missing version to 0.0.0 but never clobber an existing one.
    project = document.setdefault("project", tomlkit.table())
    if name:
        project["name"] = canonicalize_name(name, validate=True)
        rprint(f"\n[cyan2]project name[/cyan2] '{project['name']}' set in `pyproject.toml`")
    if not project.get("version"):
        project["version"] = "0.0.0"
    pyproject.write_text(tomlkit.dumps(document), encoding="utf-8")
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

    ensure_timeout_tool()

    rprint(
        "\nActivate env by running command [turquoise2]`source .venv/bin/activate`[/turquoise2] "
        "to use the [green]`harness`[/green] command.\n"
        "\n[turquoise2]python:[/turquoise2] project supports >=3.11"
        "\nPIN NEWER local Python with [turquoise2]`uv python pin 3.13 && uv sync`[/turquoise2]"
    )


@app.command(help="Run one harnessed ralph loop with <agent>, e.g. harness run claude 3 20")
def run(
    agent: str,
    num_iterations: Annotated[int, typer.Argument()] = 2,
    max_minutes: Annotated[int, typer.Argument()] = 20,
    verbose: Annotated[bool, typer.Argument()] = True,
    model: Annotated[str | None, typer.Option(help="Override the agent's model")] = None,
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
    if agent not in gate_module.AGENTS:
        typer.secho(f"Unknown agent name '{agent}'", err=True, fg=typer.colors.MAGENTA, bold=True)
        raise typer.Exit(code=2)
    if num_iterations < 1 or max_minutes < 1:
        typer.secho(
            "num_iterations and max_minutes must be >= 1", err=True, fg=typer.colors.MAGENTA, bold=True
        )
        raise typer.Exit(code=2)
    cwd = Path.cwd()
    runs = cwd / "scratchpad" / "runs" / datetime.now(tz=UTC).strftime("%Y%m%d") / agent
    runs.mkdir(parents=True, exist_ok=True)
    worker_id = f"{max((int(p.stem) for p in runs.glob('[0-9][0-9][0-9][0-9].jsonl')), default=0) + 1:04d}"
    # Hand the agent a fixed identity to use in claims and commits
    prompt = (cwd / "docs" / "PROMPT.md").read_text(encoding="utf-8").rstrip("\n")
    os.environ["RALPH_PROMPT"] = f"Your agent id is `{worker_id}`\n\n{prompt}"
    # each log file is one run / ralph invocation, not one iteration
    log = runs / f"{worker_id}.jsonl"
    loop_dir = Path(__file__).resolve().parent
    # Windows has no POSIX shell/timeout so run PowerShell twin, ralph.sh otherwise
    launcher = (
        ["powershell.exe", "-NoProfile", "-File", str(loop_dir / "ralph.ps1")]
        if sys.platform == "win32"  # support windows
        else [str(loop_dir / "ralph.sh")]
    )
    agent_argv = [tok.replace("{log_path}", str(log)) for tok in gate_module.AGENTS[agent]]
    if model:
        agent_argv[agent_argv.index("--model") + 1] = model
    command = [*launcher, str(num_iterations), str(max_minutes), *agent_argv]
    typer.echo(f"harness: {' '.join(command)} -> {log}", err=True)
    raise typer.Exit(code=run_worker(command, cwd, log, verbose))


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point: run the app so typer.Exit sets the process exit code.

    Args:
        argv: Command-line arguments to pass to the app, or None to read from sys.argv.
    """
    app(args=argv)
