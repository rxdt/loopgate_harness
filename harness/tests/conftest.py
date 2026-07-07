"""Shared fixtures and helpers for harness tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def run_cmd(args: list[str], cwd: Path) -> str:
    """Run a command in a directory with hook-safe env, failing the test on error."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, env=env)
    return result.stdout


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
def git_repo(tmp_path: Path) -> Path:
    """Provide a git repo with an identity, the tracked git hooks, and a clean initial commit."""
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
    return tmp_path
