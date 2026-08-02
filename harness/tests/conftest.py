"""Shared fixtures and helpers for harness tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from subprocess import PIPE
from typing import Self

import pytest

from harness import cli, gate

REPO_ROOT = Path(__file__).resolve().parents[2]
collect_ignore = ["test_ralph.py"] if sys.platform == "win32" else ["test_ralph_ps1.py"]


class FakePopen:
    """Stand-in for a subprocess.Popen context manager whose wait() returns a fixed exit code."""

    def __init__(self, exit_code: int) -> None:
        self._exit_code = exit_code

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback

    def wait(self) -> int:
        return self._exit_code


def fake_popen(
    monkeypatch: pytest.MonkeyPatch, fails: list[list[str]] | None = None
) -> list[tuple[list[str], Path, dict[str, str]]]:
    """Stand in for the external tool run_checks spawns, so no real linter or test runner runs.

    Git is never faked. run_git reaches Popen through subprocess.run, so a git command is handed
    straight to the real Popen and the real gate.run_git keeps working against the temp repo the
    test points REPO_ROOT at. Only the checks around it are stand-ins.

    Every faked check reports exit 0 (pass) unless its exact argv is in fails, which reports exit 1.
    Every faked launch is recorded (command, cwd, env) so dispatch tests can assert what run_checks ran.

    Returns:
        The live list of recorded launches.
    """
    failing = fails or []
    calls: list[tuple[list[str], Path, dict[str, str]]] = []
    real_popen = gate.subprocess.Popen

    def spawn(
        command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, **piping: object
    ) -> subprocess.Popen[str] | FakePopen:
        del piping  # run_git's capture settings, rebuilt below rather than forwarded
        if command[:1] == ["git"]:
            return real_popen(command, env=env, stdout=PIPE, stderr=PIPE, text=True)
        calls.append((command, cwd or REPO_ROOT, env or {}))
        return FakePopen(1 if command in failing else 0)

    monkeypatch.setattr(gate.subprocess, "Popen", spawn)
    return calls


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a seeded Git repository and point production commands at it."""
    gate.run_git(["init", "-q"], tmp_path)
    gate.run_git(["config", "user.email", "harness@test.local"], tmp_path)
    gate.run_git(["config", "user.name", "harness-test"], tmp_path)
    (tmp_path / ".githooks").mkdir()
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    (tmp_path / "README.template.md").write_text("seed\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("existing\n", encoding="utf-8")
    gate.run_git(["add", ".gitignore", "README.md", "README.template.md"], tmp_path)
    gate.run_git(["commit", "-q", "-m", "seed"], tmp_path)
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    return tmp_path
