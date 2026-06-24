"""Command-line interface for the ralph harness. Plain pass-through commands, no objects."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import tomlkit
import typer
from packaging.utils import canonicalize_name

from harness import gate as gate_module

app = typer.Typer(
    name="ralph-harness", help="Commands to harness the loops", no_args_is_help=True, add_completion=False
)

AGENTS: dict[str, tuple[str, ...]] = {
    "claude": (
        "claude",
        "-p",
        "--permission-mode",
        "acceptEdits",
        "--output-format",
        "stream-json",
        "--verbose",
    ),
    "codex": ("codex", "exec", "-m", "gpt-5.5", "--json", "--sandbox", "workspace-write", "-"),
    "agy": ("agy", "--log-file", "agy.log", "--print"),
    "copilot": ("sh", "-c", 'copilot --output-format json --stream on --allow-all-tools -p "$(cat)"'),
}


def run_worker(command: list[str], cwd: Path, log: Path, verbose: bool) -> int:
    """Run the worker command, always saving stdout and optionally streaming it live."""
    with log.open("w", encoding="utf-8") as handle:
        if not verbose:
            return subprocess.run(command, cwd=str(cwd), stdout=handle, check=False).returncode
        jq = shutil.which("jq")
        with subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            text=True,
        ) as process:
            for line in process.stdout or ():
                handle.write(line)
                handle.flush()
                sys.stdout.write(format_live_line(line, jq))
                sys.stdout.flush()
            return process.wait()


def format_live_line(line: str, jq: str | None) -> str:
    """Compact valid JSONL for terminal output; preserve invalid lines exactly."""
    if jq:
        rendered = subprocess.run(
            (jq, "-C", "-c", "."),
            input=line,
            text=True,
            capture_output=True,
            check=False,
        )
        if rendered.returncode == 0 and rendered.stdout:
            return rendered.stdout
    try:
        return f"{json.dumps(json.loads(line), separators=(',', ':'))}\n"
    except json.JSONDecodeError:
        return line


@app.command(help="Fast pre-commit checks (lint/format) plus agent containment")
def preflight() -> None:
    """Dumb pass-through to the fast pre-commit gate; exit nonzero if rejected."""
    problems = gate_module.run_preflight(Path.cwd())
    for problem in problems:
        typer.echo(f"gate: {problem}", err=True)
    typer.echo("rejected by harness" if problems else "ok: preflight passed", err=True)
    raise typer.Exit(code=1 if problems else 0)


@app.command(help="Full pre-push gate: lint, format, pyright, pylint, semgrep, pytest + coverage")
def gate() -> None:
    """Dumb pass-through to the full pre-push gate; exit nonzero if anything fails."""
    problems = gate_module.run_gate(Path.cwd())
    for problem in problems:
        typer.echo(f"gate: {problem}", err=True)
    typer.echo("rejected by harness" if problems else "ok: gate passed", err=True)
    raise typer.Exit(code=1 if problems else 0)


@app.command(help="Count agent run receipts under scratchpad/runs")
def status() -> None:
    """Count run logs and point at the newest one."""
    runs = Path.cwd() / "scratchpad" / "runs"
    logs = sorted(runs.glob("*.jsonl")) if runs.is_dir() else []
    typer.secho(f"{len(logs)} run log(s) in {runs}", fg=typer.colors.CYAN, bold=True)
    if logs:
        typer.secho(f"newest: {logs[-1]}", fg=typer.colors.GREEN, bold=True)


@app.command(help="Setup project: rename in pyproject, sync deps, point git at .githooks")
def install(name: str) -> None:
    """Inject NAME (PEP 503) into pyproject, sync deps, and activate the git hooks."""
    cwd = Path.cwd()
    pyproject = cwd / "pyproject.toml"
    document = tomlkit.parse(pyproject.read_text(encoding="utf-8"))
    document.setdefault("project", tomlkit.table())["name"] = canonicalize_name(name, validate=True)
    pyproject.write_text(tomlkit.dumps(document), encoding="utf-8")
    subprocess.run(("uv", "sync"), cwd=str(cwd), check=True)
    subprocess.run(("git", "-C", str(cwd), "config", "core.hooksPath", ".githooks"), check=True)
    typer.echo("ok: project named, deps synced, git hooks path set to .githooks", err=True)


@app.command(help="Run one harnessed ralph loop with <agent>, e.g. harness run claude 3 20")
def run(
    agent: str,
    num_iterations: Annotated[int, typer.Argument()] = 2,
    max_minutes: Annotated[int, typer.Argument()] = 20,
    verbose: Annotated[bool, typer.Argument()] = True,
) -> None:
    """ralph.sh runs once for one agent."""
    agent = agent.lower()
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
    """Console-script entry point: run the app so typer.Exit sets the process exit code."""
    app(args=argv)
