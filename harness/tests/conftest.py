"""Shared fixtures and helpers for harness tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Self

import pytest

from harness import gate

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_cmd(args: list[str], cwd: Path) -> str:
    """Run a command in a directory with hook-safe env, failing the test on error."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, env=env)
    return result.stdout


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
    """Fake the Popen seam run_checks uses to spawn each check so no real tool runs.

    Every faked check reports exit 0 (pass) unless its exact argv is in fails, which reports exit 1.
    Every launch is recorded (command, cwd, env) so dispatch tests can assert what run_checks ran.

    This replaces the whole subprocess.Popen used by run_checks. run_git reaches Popen too (via
    subprocess.run), so a test must do its real git — staging, reading the index — before calling
    this, and stay off the RALPH_LOOP containment path that would run git after the fake is in place.

    Returns:
        The live list of recorded launches.
    """
    failing = fails or []
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def spawn(command: list[str], *, cwd: Path, env: dict[str, str]) -> FakePopen:
        calls.append((command, cwd, env))
        return FakePopen(1 if command in failing else 0)

    monkeypatch.setattr(gate.subprocess, "Popen", spawn)
    return calls


@pytest.fixture
def fake_hook_repo(tmp_path: Path) -> Path:
    """A git repo wired to the tracked hooks and a fake harness executable."""
    run_cmd(["git", "init", "-q"], tmp_path)
    run_cmd(["git", "config", "user.email", "harness@test.local"], tmp_path)
    run_cmd(["git", "config", "user.name", "harness-test"], tmp_path)
    hooks = tmp_path / ".githooks"
    hooks.mkdir()
    for hook in ("pre-commit", "pre-push"):
        target = hooks / hook
        target.write_text((REPO_ROOT / ".githooks" / hook).read_text(encoding="utf-8"), encoding="utf-8")
        target.chmod(0o755)
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    harness = bin_dir / "harness"
    harness.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > harness.args\n"
        "printf '%s\\n' \"${RALPH_LOOP:-}\" > harness.loop\n"
        "if test -f harness.exit; then\n"
        '    exit "$(cat harness.exit)"\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    run_cmd(["git", "config", "core.hooksPath", ".githooks"], tmp_path)
    (tmp_path / "seed.py").write_text("x = 1\n", encoding="utf-8")
    run_cmd(["git", "add", "seed.py", ".githooks"], tmp_path)
    run_cmd(["git", "commit", "-q", "-m", "seed", "--no-verify"], tmp_path)
    return tmp_path


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a git repo with an identity, the tracked git hooks, and a clean initial commit.

    Points gate's git calls at this repo (run_git runs `git -C gate.REPO_ROOT`), so containment
    tests stage and read the throwaway repo instead of the real one.
    """
    run_cmd(["git", "init", "-q"], tmp_path)
    run_cmd(["git", "config", "user.email", "harness@test.local"], tmp_path)
    run_cmd(["git", "config", "user.name", "harness-test"], tmp_path)
    hooks = tmp_path / ".githooks"
    hooks.mkdir()
    for hook in ("pre-commit", "pre-push"):
        (hooks / hook).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (hooks / hook).chmod(0o755)
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    run_cmd(["git", "add", "README.md", ".githooks"], tmp_path)
    run_cmd(["git", "commit", "-q", "-m", "seed"], tmp_path)
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def tiny_fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with identity, tracked hooks, fake harness, and a clean seed commit.

    Points gate's git calls at this repo (run_git runs `git -C gate.REPO_ROOT`), so
    prepare_commit_msg's internal zero-arg run_git calls target this fake repo.
    """
    run_cmd(["git", "init", "-q"], tmp_path)
    run_cmd(["git", "config", "user.email", "harness@test.local"], tmp_path)
    run_cmd(["git", "config", "user.name", "harness-test"], tmp_path)
    hooks = tmp_path / ".githooks"
    hooks.mkdir()
    for hook in ("pre-commit", "pre-push", "prepare-commit-msg"):
        target = hooks / hook
        target.write_text((REPO_ROOT / ".githooks" / hook).read_text(encoding="utf-8"), encoding="utf-8")
        target.chmod(0o755)
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    harness = bin_dir / "harness"
    harness.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$@\" > harness.args\n"
        "printf '%s\\n' \"${RALPH_LOOP:-}\" > harness.loop\n"
        "if test -f harness.exit; then\n"
        '    exit "$(cat harness.exit)"\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    # sitecustomize runs at interpreter startup: point the real gate's fixed REPO_ROOT at the
    # invocation cwd (this fake repo) so the tracked prepare-commit-msg hook's internal run_git
    # (git -C gate.REPO_ROOT) reads this repo's index instead of the real project's.
    (bin_dir / "sitecustomize.py").write_text(
        "import os\nfrom harness import gate\n\ngate.REPO_ROOT = os.getcwd()\n",
        encoding="utf-8",
    )
    python = bin_dir / "python"
    python.write_text(
        f"#!/bin/sh\nPYTHONPATH='{bin_dir}:{REPO_ROOT}' exec '{sys.executable}' \"$@\"\n",
        encoding="utf-8",
    )
    python.chmod(0o755)
    run_cmd(["git", "config", "core.hooksPath", ".githooks"], tmp_path)
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    run_cmd(["git", "add", "README.md", ".githooks"], tmp_path)
    run_cmd(["git", "commit", "-q", "-m", "seed", "--no-verify"], tmp_path)
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    return tmp_path
