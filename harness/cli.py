"""Command-line interface for the ralph harness. Plain pass-through commands, no objects."""

from __future__ import annotations

import itertools
import json
import os
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
from rich.json import JSON
from rich.table import Table

from harness import contextrot
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
        # "--bare",  # one-shot minimal run. skips MCP, hooks, plugins, CLAUDE.md, reduced startup
        # and sets CLAUDE_CODE_SIMPLE no use CLAUDE_CODE_OAUTH_TOKEN (ANTHROPIC_API_KEY billed) no .claude log
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


def _launcher(loop_dir: Path) -> list[str]:
    """The ralph launcher argv for this platform.

    Windows has no POSIX shell/timeout, so it runs the PowerShell twin; every
    other platform runs ralph.sh.

    Args:
        loop_dir: Directory holding ralph.sh / ralph.ps1 (this module's directory).

    Returns:
        The launcher argv prefix.
    """
    if sys.platform == "win32":
        return ["powershell.exe", "-NoProfile", "-File", str(loop_dir / "ralph.ps1")]
    return [str(loop_dir / "ralph.sh")]


def agent_model(agent: str, override: str | None = None) -> str | None:
    """The model an agent runs with: the --model override, else its argv ``-m`` value.

    Codex logs never carry the model, so the harness supplies it to the context-rot
    scorer from the launch command. Claude passes no ``-m`` (the model is in its
    log), so without an override this returns None for it.

    Args:
        agent: An agent key present in AGENTS.
        override: A --model value from the run command, if given.

    Returns:
        The effective model id, or None if neither source names one.
    """
    if override:
        return override
    for flag, value in itertools.pairwise(AGENTS[agent]):
        if flag == "-m":
            return value
    return None


def agent_command(agent: str, model: str | None) -> list[str]:
    """The agent's argv with an optional model override applied.

    Replaces the value after ``-m`` when the command has one (codex); otherwise
    appends ``--model <model>`` (claude). No model -> the command as configured.

    Args:
        agent: An agent key present in AGENTS.
        model: A --model value from the run command, if given.

    Returns:
        The argv to launch.
    """
    command = list(AGENTS[agent])
    if model is None:
        return command
    if "-m" in command:
        command[command.index("-m") + 1] = model
        return command
    return [*command, "--model", model]


def run_worker(command: list[str], cwd: Path, log: Path, verbose: bool) -> int:
    """Run the worker command, always saving stdout and optionally streaming it live.

    The worker inherits the current environment, including RALPH_PROMPT set by `run`, so ralph.sh
    receives the prompt as a string and never reads a prompt file.

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
                sys.stdout.write(format_live_line(line, console))
                sys.stdout.flush()
            return process.wait()


def format_live_line(line: str, console: Console) -> str:
    """Compact and colorize one worker JSONL line for the terminal; pass non-JSON through verbatim.

    Coloring is done in-process with rich (no per-line subprocess); the console's own detection
    decides whether ANSI is emitted, so agents (no tty) get plain text and humans get color.

    Args:
        line: A single raw output line from the worker.
        console: Rich console used to render the compact colored JSON.

    Returns:
        The compacted single-line JSON (colored when the console is styled), or the original line
        if it is not valid JSON.
    """
    try:
        rendered = JSON(line, indent=None)  # JSON() parses the string itself, raises on non-JSON
    except json.JSONDecodeError:
        return line
    with console.capture() as captured:
        console.print(rendered, end="\n")
    return captured.get()


def check(name: str, command: Callable[[Path], dict[str, list[str]]]) -> dict[str, list[str]]:
    """Run a named phase (preflight or gate), render its summary, and exit by its verdict.

    Args:
        name: Phase label shown in the summary (e.g. "preflight" or "gate").
        command: Callable that runs the phase for a repo. Returns pass/fail buckets.

    Raises:
        typer.Exit: always — code 1 if anything failed, else code 0.
    """
    results = command(Path.cwd())
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


@app.command(help="Setup project: inject project name, sync dependencies, set up githooks")
def install(name: str) -> None:
    """Inject NAME (PEP 503) into pyproject, sync deps, and activate the git hooks.

    Args:
        name: Project name, canonicalized to a PEP 503 form before being written.
    """
    cwd = Path.cwd()

    pyproject = cwd / "pyproject.toml"
    document = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
    # Set the requested name; default a missing version to 0.0.0 but never clobber an existing one.
    project = document.setdefault("project", tomlkit.table())
    project["name"] = canonicalize_name(name, validate=True)
    if not project.get("version"):
        project["version"] = "0.0.0"
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

    ensure_timeout_tool()

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
    *,
    model: Annotated[str | None, typer.Option(help="Override the agent's model")] = None,
) -> None:
    """ralph.sh runs once for one agent.

    Args:
        agent: Agent key to run. Case-folded and looked up in AGENTS.
        num_iterations: Number of ralph loop iterations. Must be >= 1.
        max_minutes: Wall-clock budget per run in minutes. Must be >= 1.
        verbose: When True, stream the worker's output live to the terminal.
        model: Model to run instead of the agent's default; also scored against.

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
    worker_id = f"{seq:04d}-{agent}"  # harness-assigned identity; agents can't self-name uniquely
    log = runs / f"{worker_id}.jsonl"
    # Hand the agent a fixed identity to use verbatim in claims and commit trailers (append its spec).
    prompt = (cwd / "docs" / "PROMPT.md").read_text(encoding="utf-8").rstrip("\n")
    os.environ["RALPH_PROMPT"] = f"Your agent id is `{worker_id}`. Use it verbatim.\n\n{prompt}"
    launcher = _launcher(Path(__file__).resolve().parent)
    command = [*launcher, str(num_iterations), str(max_minutes), *agent_command(agent, model)]
    typer.echo(f"harness: {' '.join(command)} -> {log}", err=True)
    code = run_worker(command, cwd, log, verbose)
    # Score the finished log for context-rot pressure and print the verdict. This is
    # out-of-band telemetry: rot_verdict never raises, so it cannot change `code`. It
    # returns "" for agents it can't score (agy/copilot), which we don't print.
    if verdict := contextrot.rot_verdict(agent, log, model=agent_model(agent, model)):
        typer.echo(verdict, err=True)
    raise typer.Exit(code=code)


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point: run the app so typer.Exit sets the process exit code.

    Args:
        argv: Command-line arguments to pass to the app, or None to read from sys.argv.
    """
    app(args=argv)
