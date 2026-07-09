"""Tests for the ralph CLI (harness.cli). Commands drive the real Typer app; only the external
toolchain (gate checks, uv sync, the worker subprocess) is stubbed at the boundary.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import typer
from packaging.utils import InvalidName
from typer.testing import CliRunner

from harness import cli, gate
from harness.tests.conftest import run_cmd

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Self

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]


def returns(fail: list[str], passed: list[str] | None = None) -> Callable[[Path], dict[str, list[str]]]:
    """Build a typed stand-in for gate.run_preflight / gate.run_gate that returns fixed results.

    `fail` is the list of check names that fail; passing an empty list means a clean gate.
    `pass` is the list of checks that pass (defaults to a single 'lint' so the summary always
    renders at least one PASSED row).
    """

    def check(repo: Path) -> dict[str, list[str]]:
        del repo
        return {"pass": passed if passed is not None else ["lint"], "fail": fail}

    return check


def stub_toolchain(real: Callable[..., object], calls: list[tuple[str, ...]]) -> Callable[..., object]:
    """Run git for real, stub everything else (uv sync) with a clean exit."""

    def fake(args: tuple[str, ...] | list[str], **kwargs: object) -> object:
        calls.append(tuple(args))
        if tuple(args)[:1] == ("git",):
            return real(args, **kwargs)
        completed: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(list(args), 0)
        return completed

    return fake


def fake_agent(captured: dict[str, list[list[str]]], code: int = 0) -> Callable[..., object]:
    """Stand in for the worker: record the launched command and write canned jsonl to its stdout."""

    def fake(command: list[str], *, stdout: io.TextIOBase | None = None, **kwargs: object) -> object:
        del kwargs
        captured.setdefault("commands", []).append(list(command))
        if stdout is not None:
            stdout.write('{"type":"result","result":"ok"}\n')  # the "agent" emits one line
        completed: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(list(command), code)
        return completed

    return fake


def write_log(repo: Path, name: str) -> None:
    """Drop a run receipt under scratchpad/runs."""
    runs = repo / "scratchpad" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / name).write_text("{}\n", encoding="utf-8")


def write_executable(path: Path, text: str) -> None:
    """Write an executable script for CLI integration tests."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def seed_prompt(cwd: Path) -> None:
    """Create docs/PROMPT.md so `run` (which reads it into RALPH_PROMPT) has a prompt to pass."""
    (cwd / "docs").mkdir(parents=True, exist_ok=True)
    (cwd / "docs" / "PROMPT.md").write_text("do the most important thing\n", encoding="utf-8")


# --------------------------------------------------------------------------- entry point


def test_main_propagates_exit_code() -> None:
    """The console-script entry point lets typer.Exit reach the shell."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0


def test_unknown_command_is_usage_error() -> None:
    """An unknown command and no command both exit 2."""
    assert runner.invoke(cli.app, ["bogus"]).exit_code == 2
    assert runner.invoke(cli.app, []).exit_code == 2


def test_completion_options_are_not_exposed() -> None:
    """The harness help stays focused on harness commands, not shell completion plumbing."""
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "--install-completion" not in result.output
    assert "--show-completion" not in result.output


def test_git_hooks_call_commands_that_exist() -> None:
    """The git hooks must invoke harness commands that are actually registered."""
    for hook in (".githooks/pre-commit", ".githooks/pre-push"):
        text = (REPO_ROOT / hook).read_text(encoding="utf-8")
        called = [
            tokens[index + 1]
            for tokens in (line.split() for line in text.splitlines())
            for index, token in enumerate(tokens)
            if token.endswith("harness") and index + 1 < len(tokens)
        ]
        assert called, f"{hook} does not invoke harness"
        for command in called:
            assert runner.invoke(cli.app, [command, "--help"]).exit_code == 0


def test_run_exposes_verbose_as_positional_without_disable_flag() -> None:
    """Run accepts positional verbose and does not expose a --no-verbose CLI flag."""
    result = runner.invoke(cli.app, ["run", "--help"])
    assert result.exit_code == 0
    assert "verbose" in result.output
    assert "--verbose" not in result.output
    assert "--no-verbose" not in result.output


# --------------------------------------------------------------------------- preflight / gate


def test_preflight_passes_when_gate_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """A human run of a clean preflight renders the Rich summary (styled) and exits 0."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    monkeypatch.setattr(gate, "run_preflight", returns([], passed=["lint"]))
    result = runner.invoke(cli.app, ["preflight"])
    assert result.exit_code == 0
    assert "\x1b[" in result.stderr  # humans get styled output
    assert "Harness Summary" in result.stderr
    assert "lint" in result.stderr
    assert "ok: preflight pass" in result.stderr
    assert "rejected by harness" not in result.stderr


def test_preflight_rejects_and_names_the_fail_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """A human run with a failing preflight names the check and rejects, styled, exit 1."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    monkeypatch.setattr(gate, "run_preflight", returns(["lint"]))
    result = runner.invoke(cli.app, ["preflight"])
    assert result.exit_code == 1
    assert "\x1b[" in result.stderr
    assert "lint" in result.stderr
    assert "rejected by harness" in result.stderr


def test_gate_passes_when_checks_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """A human run of a clean gate exits 0 and does not reject."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    monkeypatch.setattr(gate, "run_gate", returns([]))
    result = runner.invoke(cli.app, ["gate"])
    assert result.exit_code == 0
    assert "rejected by harness" not in result.stderr
    assert "ok: gate pass" in result.stderr


def test_gate_rejects_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A human run with a failing gate names the check and rejects, exit 1."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    monkeypatch.setattr(gate, "run_gate", returns(["types"]))
    result = runner.invoke(cli.app, ["gate"])
    assert result.exit_code == 1
    assert "types" in result.stderr
    assert "rejected by harness" in result.stderr


def test_agent_gate_summary_is_plain_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under RALPH_LOOP the summary is plain (no ANSI) JSON carrying the same pass/fail info."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "run_gate", returns(["types"], passed=["lint"]))
    result = runner.invoke(cli.app, ["gate"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)["Harness Summary"]
    assert payload == {"PASSED": ["lint"], "FAILED": ["types"], "result": "rejected by harness"}
    assert "\x1b[" not in result.stdout  # agents get no styled output


def test_agent_gate_summary_reports_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under RALPH_LOOP a clean gate emits plain JSON with the pass result and exits 0."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "run_gate", returns([], passed=["lint"]))
    result = runner.invoke(cli.app, ["gate"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)["Harness Summary"]
    assert payload == {"PASSED": ["lint"], "FAILED": [], "result": "ok: gate pass"}
    assert "\x1b[" not in result.stdout


def test_verify_passes_when_gate_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify is gone, so it cannot pass through to run_gate."""
    monkeypatch.setattr(gate, "run_gate", pytest.fail)
    result = runner.invoke(cli.app, ["verify"])
    assert result.exit_code == 2
    assert "No such command 'verify'" in result.output
    assert "ok: verify pass" not in result.output


def test_verify_rejects_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify is gone, so even a failing gate stub is never called."""
    monkeypatch.setattr(gate, "run_gate", pytest.fail)
    result = runner.invoke(cli.app, ["verify"])
    assert result.exit_code == 2
    assert "No such command 'verify'" in result.output
    assert "gate: security fail" not in result.output


# --------------------------------------------------------------------------- status


def test_status_reports_zero_when_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No logs → reports 0, no crash."""
    monkeypatch.chdir(tmp_path)
    seed_prompt(tmp_path)
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "0 run log(s)" in result.stdout


def test_status_counts_logs_and_names_newest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Status counts the *.jsonl logs and points at the newest (last sorted)."""
    monkeypatch.chdir(tmp_path)
    seed_prompt(tmp_path)
    write_log(tmp_path, "0001-claude.jsonl")
    write_log(tmp_path, "0002-codex.jsonl")
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "2 run log(s)" in result.stdout
    assert "newest: " in result.stdout
    assert "0002-codex.jsonl" in result.stdout


def test_cli_does_not_shadow_builtin_print() -> None:
    """CLI output uses Typer helpers, so stderr handling and lint stay clean."""
    assert "print" not in cli.__dict__


# --------------------------------------------------------------------------- install


def test_install_renames_syncs_and_sets_hooks(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Install sets the name (PEP 503) and version 0.0.0, preserves other metadata, syncs, sets hooks."""
    monkeypatch.chdir(git_repo)
    (git_repo / "pyproject.toml").write_text(
        "[project]\n"
        'name = "old-name"\n'
        'version = "2.3.4"\n'
        'description = "the user\'s own project"\n'
        'authors = [{ name = "someone" }]\n'
        'requires-python = ">=3.11"\n'
        "\n[project.scripts]\n"
        'harness = "harness.cli:main"\n',
        encoding="utf-8",
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(subprocess, "run", stub_toolchain(subprocess.run, calls))
    result = runner.invoke(cli.app, ["install", "My_Cool.Project"])
    assert result.exit_code == 0
    assert ("uv", "sync") in calls
    assert ("git", "config", "core.hooksPath", ".githooks") in calls
    assert ("git", "config", "core.hooksPath") in calls
    assert ("ls", "-l", ".githooks") in calls
    with (git_repo / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["name"] == "my-cool-project"  # the requested name is set
    assert project["version"] == "0.0.0"  # version reset for the new project
    # Other metadata is left untouched (not clobbered).
    assert project["description"] == "the user's own project"
    assert project["authors"] == [{"name": "someone"}]
    assert project["requires-python"] == ">=3.11"
    assert project["scripts"] == {"harness": "harness.cli:main"}
    monkeypatch.undo()
    assert run_cmd(["git", "config", "core.hooksPath"], git_repo).strip() == ".githooks"


def test_install_rejects_invalid_name(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A name that can't be canonicalized raises InvalidName before any sync."""
    monkeypatch.chdir(git_repo)
    (git_repo / "pyproject.toml").write_text('[project]\nname = "ok"\n', encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(subprocess, "run", stub_toolchain(subprocess.run, calls))
    result = runner.invoke(cli.app, ["install", 'bad"name'])
    assert isinstance(result.exception, InvalidName)
    assert ("uv", "sync") not in calls


# --------------------------------------------------------------------------- run


def test_run_rejects_unknown_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An agent not in AGENTS exits 2 with a helpful message — before launching anything."""
    monkeypatch.chdir(tmp_path)
    seed_prompt(tmp_path)
    monkeypatch.setattr(subprocess, "run", pytest.fail)
    result = runner.invoke(cli.app, ["run", "bogus"])
    assert result.exit_code == 2
    assert result.stderr.strip() == "unknown agent 'bogus'; choose from claude, codex, agy, copilot"
    assert not (tmp_path / "scratchpad").exists()


def test_run_builds_ralph_command_and_writes_sequential_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run fires ralph.sh with the preset and the worker writes the NNNN-agent.jsonl receipt."""
    monkeypatch.chdir(tmp_path)
    seed_prompt(tmp_path)
    captured: dict[str, list[list[str]]] = {}
    monkeypatch.setattr(subprocess, "run", fake_agent(captured))
    result = runner.invoke(cli.app, ["run", "claude", "1", "2", "False"])
    assert result.exit_code == 0
    command = captured["commands"][0]
    assert command[0].endswith("ralph.sh")
    assert command[1:3] == ["1", "2"]
    assert command[3:] == list(cli.AGENTS["claude"])  # preset expanded verbatim
    assert (tmp_path / "scratchpad" / "runs").is_dir()  # run creates the log dir
    log = tmp_path / "scratchpad" / "runs" / "0001-claude.jsonl"
    assert log.read_text(encoding="utf-8") == '{"type":"result","result":"ok"}\n'


def test_agent_presets_are_registered() -> None:
    """Every supported agent has one nonempty tuple command registered in the CLI."""
    assert set(cli.AGENTS) == {"claude", "codex", "agy", "copilot"}
    for command in cli.AGENTS.values():
        assert isinstance(command, tuple)
        assert command
        assert all(command)


def test_run_claude_executes_real_loop_twice_with_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Claude preset runs through ralph.sh and receives the prompt each iteration."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "gtimeout", '#!/bin/sh\nshift\nexec "$@"\n')
    write_executable(
        bin_dir / "claude",
        (
            "#!/bin/sh\n"
            "count=$(cat claude-count 2>/dev/null || printf 0)\n"
            "count=$((count + 1))\n"
            'printf "%s" "$count" > claude-count\n'
            'printf "%s\\n" "$@" >> claude-args.txt\n'
            'cat > "prompt-$count.txt"\n'
            'printf \'{ "type" : "result", "result" : "ok" }\\n\'\n'
        ),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PROMPT.md").write_text("build from specs\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["run", "claude", "2", "1"])

    assert result.exit_code == 0
    assert (tmp_path / "claude-count").read_text(encoding="utf-8") == "2"
    assert (tmp_path / "prompt-1.txt").read_text(encoding="utf-8") == (
        "build from specs\n\nRALPH_ITERATION=1/2\n"
    )
    assert (tmp_path / "prompt-2.txt").read_text(encoding="utf-8") == (
        "build from specs\n\nRALPH_ITERATION=2/2\n"
    )
    claude_args = list(cli.AGENTS["claude"][1:])
    expected_args = claude_args.copy()
    expected_args.extend(claude_args)
    assert (tmp_path / "claude-args.txt").read_text(encoding="utf-8").splitlines() == expected_args
    assert (tmp_path / "scratchpad" / "runs" / "0001-claude.jsonl").read_text(
        encoding="utf-8"
    ) == '{ "type" : "result", "result" : "ok" }\n{ "type" : "result", "result" : "ok" }\n'


def test_run_log_sequence_increments_past_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The receipt number is max(existing leading int) + 1, so a prior run is never overwritten."""
    write_log(tmp_path, "0007-codex.jsonl")
    monkeypatch.chdir(tmp_path)
    seed_prompt(tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_agent({}))
    assert runner.invoke(cli.app, ["run", "claude", "2", "20", "False"]).exit_code == 0
    assert (tmp_path / "scratchpad" / "runs" / "0008-claude.jsonl").exists()


@pytest.mark.parametrize("args", [["claude", "0", "1"], ["claude", "1", "0"]])
def test_run_rejects_nonpositive_limits_before_creating_log(
    args: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nonpositive loop limits fail in the CLI before any run receipt is opened."""
    monkeypatch.chdir(tmp_path)
    seed_prompt(tmp_path)
    monkeypatch.setattr(subprocess, "run", pytest.fail)
    result = runner.invoke(cli.app, ["run", *args])
    assert result.exit_code == 2
    assert "num_iterations and max_minutes must be >= 1" in result.stderr
    assert not (tmp_path / "scratchpad").exists()


@pytest.mark.parametrize("code", [0, 1, 2, 124])
def test_run_propagates_worker_exit_code(code: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ralph.sh's exit code (success, abort, usage, timeout) reaches the shell."""
    monkeypatch.chdir(tmp_path)
    seed_prompt(tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_agent({}, code))
    assert runner.invoke(cli.app, ["run", "codex", "2", "20", "False"]).exit_code == code


class FakeProcess:
    """Stand in for the worker subprocess: replays canned stdout lines and a fixed exit code."""

    def __init__(self, lines: list[str], code: int) -> None:
        self.stdout = iter(lines)
        self.code = code

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        del exc
        return False

    def wait(self) -> int:
        return self.code


def fake_popen(lines: list[str], code: int = 0) -> Callable[..., FakeProcess]:
    """Stand in for subprocess.Popen: yield canned worker stdout lines, then exit with code."""

    def make(command: list[str], **kwargs: object) -> FakeProcess:
        del command, kwargs
        return FakeProcess(lines, code)

    return make


def test_run_worker_streams_and_logs_json_and_invalid_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verbose streaming writes the raw stdout to the log verbatim, JSON and non-JSON alike, and does
    not crash on a non-JSON line. Terminal coloring is a human cosmetic, so it is not asserted here.
    """
    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen(['{ "type" : "result" }\n', "not json\n"]))
    log = tmp_path / "out.jsonl"

    assert cli.run_worker(["worker"], tmp_path, log, verbose=True) == 0
    assert log.read_text(encoding="utf-8") == '{ "type" : "result" }\nnot json\n'


def test_format_live_line_colorizes_in_process_without_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """A styled console colorizes valid JSON in-process (ANSI present), spawning no subprocess per line."""
    monkeypatch.setattr(cli.subprocess, "run", pytest.fail)  # any per-line subprocess fails the test
    console = cli.Console(force_terminal=True, width=10**9)  # styled: emit ANSI
    out = cli.format_live_line('{ "type" : "result" }\n', console)
    assert "\x1b[" in out  # colored in-process
    assert '"type"' in out  # still the compacted JSON content
    assert '"result"' in out


def test_format_live_line_passes_non_json_through_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-JSON line returns unchanged and never reaches the renderer or any subprocess."""
    monkeypatch.setattr(cli.subprocess, "run", pytest.fail)
    console = cli.Console(force_terminal=True)
    assert cli.format_live_line("not json\n", console) == "not json\n"


def test_run_accepts_positional_verbose_false(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A fourth positional False disables live terminal streaming."""
    monkeypatch.chdir(tmp_path)
    seed_prompt(tmp_path)
    captured: dict[str, list[list[str]]] = {}
    monkeypatch.setattr(subprocess, "run", fake_agent(captured))
    result = runner.invoke(cli.app, ["run", "claude", "1", "2", "False"])
    assert result.exit_code == 0
    assert not result.stdout


def test_run_accepts_python_verbose_false(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Calling run(..., verbose=False) keeps output in the receipt only."""
    monkeypatch.chdir(tmp_path)
    seed_prompt(tmp_path)
    captured: dict[str, list[list[str]]] = {}
    monkeypatch.setattr(subprocess, "run", fake_agent(captured))
    with pytest.raises(typer.Exit) as exit_info:
        cli.run("claude", 2, 20, verbose=False)
    assert exit_info.value.exit_code == 0
    assert (tmp_path / "scratchpad" / "runs" / "0001-claude.jsonl").read_text(
        encoding="utf-8"
    ) == '{"type":"result","result":"ok"}\n'
