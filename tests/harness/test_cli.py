"""Tests for the ralph CLI (harness.cli). Commands drive the real Typer app; only the external
toolchain (gate checks, uv sync, the worker subprocess) is stubbed at the boundary."""

from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import typer
from conftest import run_cmd
from packaging.utils import InvalidName
from typer.testing import CliRunner

from harness import cli, gate

if TYPE_CHECKING:
    from collections.abc import Callable

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]


def returns(problems: list[str]) -> Callable[[Path], list[str]]:
    """Build a typed stand-in for gate.run_preflight / gate.run_gate that returns fixed problems."""

    def check(repo: Path) -> list[str]:
        del repo
        return problems

    return check


def no_jq(name: str) -> None:
    """Typed stand-in for shutil.which when jq is unavailable."""
    del name


def stub_toolchain(real: Callable[..., object], calls: list[tuple[str, ...]]) -> Callable[..., object]:
    """Run git for real, stub everything else (uv sync) with a clean exit."""

    def fake(args: tuple[str, ...] | list[str], **kwargs: object) -> object:
        calls.append(tuple(args))
        if tuple(args)[:1] == ("git",):
            return real(args, **kwargs)
        return subprocess.CompletedProcess(list(args), 0)

    return fake


def fake_agent(captured: dict[str, list[list[str]]], code: int = 0) -> Callable[..., object]:
    """Stand in for the worker: record the launched command and write canned jsonl to its stdout."""

    def fake(command: list[str], *, stdout: io.TextIOBase | None = None, **kwargs: object) -> object:
        del kwargs
        captured.setdefault("commands", []).append(list(command))
        if stdout is not None:
            stdout.write('{"type":"result","result":"ok"}\n')  # the "agent" emits one line
        return subprocess.CompletedProcess(list(command), code)

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
    """run accepts positional verbose and does not expose a --no-verbose CLI flag."""
    result = runner.invoke(cli.app, ["run", "--help"])
    assert result.exit_code == 0
    assert "verbose" in result.output
    assert "--verbose" not in result.output
    assert "--no-verbose" not in result.output


# --------------------------------------------------------------------------- preflight / gate


def test_preflight_passes_when_gate_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """preflight is a dumb pass-through: no problems → exit 0, says ok."""
    monkeypatch.setattr(gate, "run_preflight", returns([]))
    result = runner.invoke(cli.app, ["preflight"])
    assert result.exit_code == 0
    assert "ok: preflight passed" in result.stderr


def test_preflight_rejects_and_prints_each_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    """A problem from run_preflight is surfaced and exits 1."""
    monkeypatch.setattr(gate, "run_preflight", returns(["banned pattern 'noqa' in line: x"]))
    result = runner.invoke(cli.app, ["preflight"])
    assert result.exit_code == 1
    assert "gate: banned pattern 'noqa'" in result.stderr
    assert "rejected by harness" in result.stderr


def test_gate_passes_when_checks_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """gate pass-through to run_gate: clean → exit 0."""
    monkeypatch.setattr(gate, "run_gate", returns([]))
    result = runner.invoke(cli.app, ["gate"])
    assert result.exit_code == 0
    assert "ok: gate passed" in result.stderr


def test_gate_rejects_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing full gate check surfaces and exits 1."""
    monkeypatch.setattr(gate, "run_gate", returns(["types failed:\npyright"]))
    result = runner.invoke(cli.app, ["gate"])
    assert result.exit_code == 1
    assert "gate: types failed" in result.stderr
    assert "rejected by harness" in result.stderr


def test_verify_passes_when_gate_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify is gone, so it cannot pass through to run_gate."""
    monkeypatch.setattr(gate, "run_gate", pytest.fail)
    result = runner.invoke(cli.app, ["verify"])
    assert result.exit_code == 2
    assert "No such command 'verify'" in result.output
    assert "ok: verify passed" not in result.output


def test_verify_rejects_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify is gone, so even a failing gate stub is never called."""
    monkeypatch.setattr(gate, "run_gate", pytest.fail)
    result = runner.invoke(cli.app, ["verify"])
    assert result.exit_code == 2
    assert "No such command 'verify'" in result.output
    assert "gate: security failed" not in result.output


# --------------------------------------------------------------------------- status


def test_status_reports_zero_when_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No logs → reports 0, no crash."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0
    assert "0 run log(s)" in result.stdout


def test_status_counts_logs_and_names_newest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """status counts the *.jsonl receipts and points at the newest (last sorted)."""
    monkeypatch.chdir(tmp_path)
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
    """install rewrites the project name (PEP 503), runs uv sync, and points git at .githooks."""
    monkeypatch.chdir(git_repo)
    (git_repo / "pyproject.toml").write_text('[project]\nname = "old"\n', encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(subprocess, "run", stub_toolchain(subprocess.run, calls))
    assert runner.invoke(cli.app, ["install", "My_Cool.Project"]).exit_code == 0
    assert ("uv", "sync") in calls
    assert 'name = "my-cool-project"' in (git_repo / "pyproject.toml").read_text(encoding="utf-8")
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
    monkeypatch.setattr(subprocess, "run", pytest.fail)
    result = runner.invoke(cli.app, ["run", "bogus"])
    assert result.exit_code == 2
    assert result.stderr.strip() == "unknown agent 'bogus'; choose from claude, codex, agy, copilot"
    assert not (tmp_path / "scratchpad").exists()


def test_run_builds_ralph_command_and_writes_sequential_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run fires ralph.sh with the preset and the worker writes the NNNN-agent.jsonl receipt."""
    monkeypatch.chdir(tmp_path)
    captured: dict[str, list[list[str]]] = {}
    monkeypatch.setattr(subprocess, "run", fake_agent(captured))
    result = runner.invoke(cli.app, ["run", "claude", "1", "2", "False"])
    assert result.exit_code == 0
    command = captured["commands"][0]
    assert command[0].endswith("ralph.sh")
    assert command[1:3] == ["1", "2"]
    assert command[3:] == list(cli.AGENTS["claude"])  # preset expanded verbatim
    log = tmp_path / "scratchpad" / "runs" / "0001-claude.jsonl"
    assert log.read_text(encoding="utf-8") == '{"type":"result","result":"ok"}\n'


def test_agent_presets_are_frozen() -> None:
    """The exact per-agent worker commands are pinned and must not silently change or lose flags."""
    assert cli.AGENTS == {
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
        "copilot": (
            "sh",
            "-c",
            'copilot --output-format json --stream on --allow-all-tools -p "$(cat)"',
        ),
    }


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
    monkeypatch.setattr(cli.shutil, "which", no_jq)
    (tmp_path / "PROMPT.md").write_text("build from specs\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["run", "claude", "2", "1"])

    assert result.exit_code == 0
    assert (tmp_path / "claude-count").read_text(encoding="utf-8") == "2"
    assert (tmp_path / "prompt-1.txt").read_text(encoding="utf-8") == (
        "build from specs\n\nRALPH_ITERATION=1/2\n"
    )
    assert (tmp_path / "prompt-2.txt").read_text(encoding="utf-8") == (
        "build from specs\n\nRALPH_ITERATION=2/2\n"
    )
    assert (tmp_path / "claude-args.txt").read_text(encoding="utf-8").splitlines() == [
        "-p",
        "--permission-mode",
        "acceptEdits",
        "--output-format",
        "stream-json",
        "--verbose",
        "-p",
        "--permission-mode",
        "acceptEdits",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    assert (tmp_path / "scratchpad" / "runs" / "0001-claude.jsonl").read_text(
        encoding="utf-8"
    ) == '{ "type" : "result", "result" : "ok" }\n{ "type" : "result", "result" : "ok" }\n'
    assert result.stdout == '{"type":"result","result":"ok"}\n{"type":"result","result":"ok"}\n'


def test_run_log_sequence_increments_past_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The receipt number is max(existing leading int) + 1, so a prior run is never overwritten."""
    write_log(tmp_path, "0007-codex.jsonl")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_agent({}))
    assert runner.invoke(cli.app, ["run", "claude", "2", "20", "False"]).exit_code == 0
    assert (tmp_path / "scratchpad" / "runs" / "0008-claude.jsonl").exists()


@pytest.mark.parametrize("args", [["claude", "0", "1"], ["claude", "1", "0"]])
def test_run_rejects_nonpositive_limits_before_creating_log(
    args: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nonpositive loop limits fail in the CLI before any run receipt is opened."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", pytest.fail)
    result = runner.invoke(cli.app, ["run", *args])
    assert result.exit_code == 2
    assert "num_iterations and max_minutes must be >= 1" in result.stderr
    assert not (tmp_path / "scratchpad").exists()


@pytest.mark.parametrize("code", [0, 1, 2, 124])
def test_run_propagates_worker_exit_code(code: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ralph.sh's exit code (success, abort, usage, timeout) reaches the shell."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", fake_agent({}, code))
    assert runner.invoke(cli.app, ["run", "codex", "2", "20", "False"]).exit_code == code


def test_format_live_line_compacts_json_and_preserves_invalid_lines() -> None:
    """Verbose terminal output is compact JSONL without corrupting non-JSON lines."""
    assert cli.format_live_line('{ "type" : "result", "result" : "ok" }\n', None) == (
        '{"type":"result","result":"ok"}\n'
    )
    assert cli.format_live_line("not json\n", None) == "not json\n"


def test_format_live_line_uses_jq_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """When jq exists, verbose terminal output comes from jq's colored compact renderer."""

    def fake_run(args: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args == ("/usr/bin/jq", "-C", "-c", ".")
        assert kwargs["input"] == '{ "type" : "result" }\n'
        assert kwargs["text"] is True
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        return subprocess.CompletedProcess(args, 0, stdout='\x1b[1;39m{"type":"result"}\x1b[0m\n')

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert cli.format_live_line('{ "type" : "result" }\n', "/usr/bin/jq") == (
        '\x1b[1;39m{"type":"result"}\x1b[0m\n'
    )


def test_run_accepts_positional_verbose_false(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A fourth positional False disables live terminal streaming."""
    monkeypatch.chdir(tmp_path)
    captured: dict[str, list[list[str]]] = {}
    monkeypatch.setattr(subprocess, "run", fake_agent(captured))
    result = runner.invoke(cli.app, ["run", "claude", "1", "2", "False"])
    assert result.exit_code == 0
    assert not result.stdout


def test_run_accepts_python_verbose_false(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Calling run(..., verbose=False) keeps output in the receipt only."""
    monkeypatch.chdir(tmp_path)
    captured: dict[str, list[list[str]]] = {}
    monkeypatch.setattr(subprocess, "run", fake_agent(captured))
    with pytest.raises(typer.Exit) as exit_info:
        cli.run("claude", 2, 20, verbose=False)
    assert exit_info.value.exit_code == 0
    assert (tmp_path / "scratchpad" / "runs" / "0001-claude.jsonl").read_text(
        encoding="utf-8"
    ) == '{"type":"result","result":"ok"}\n'
