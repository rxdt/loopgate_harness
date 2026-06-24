"""Integration tests: containment runs through a real git pre-commit hook.

These commit for real through a hook that calls the actual `harness.gate` containment. They
show what `RALPH_LOOP` and the hook do end to end — and where containment can be bypassed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import run_cmd

REPO_ROOT = Path(__file__).resolve().parents[2]

GATE_HOOK = (
    "import sys, pathlib\n"
    f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
    "from harness import gate\n"
    "gate.run_checks = lambda repo, checks: []  # isolate containment from the real quality tools\n"
    "problems = gate.run_preflight(pathlib.Path.cwd())\n"
    "for problem in problems:\n"
    "    sys.stderr.write(problem + '\\n')\n"
    "sys.exit(1 if problems else 0)\n"
)


def arm_gate_hook(repo: Path) -> None:
    """Install a real pre-commit hook that runs the harness containment check."""
    hooks = repo / ".githooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "gate_hook.py").write_text(GATE_HOOK, encoding="utf-8")
    pre_commit = hooks / "pre-commit"
    pre_commit.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{hooks / "gate_hook.py"}"\n', encoding="utf-8"
    )
    pre_commit.chmod(0o755)
    run_cmd(["git", "config", "core.hooksPath", ".githooks"], repo)


def stage_forbidden(repo: Path) -> None:
    """Write and stage a file under a forbidden path."""
    (repo / "harness").mkdir()
    (repo / "harness" / "evil.py").write_text("value = 1\n", encoding="utf-8")
    run_cmd(["git", "add", "harness/evil.py"], repo)


def attempt_commit(repo: Path, message: str, loop: bool, no_verify: bool) -> subprocess.CompletedProcess[str]:
    """Try a commit with optional RALPH_LOOP in the env and optional --no-verify."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    if loop:
        env["RALPH_LOOP"] = "1"
    args = ["git", "commit", "-q", "-m", message]
    if no_verify:
        args.append("--no-verify")
    return subprocess.run(args, cwd=repo, capture_output=True, text=True, check=False, env=env)


def test_hook_excludes_forbidden_path_under_loop(git_repo: Path) -> None:
    """RALPH_LOOP=1 + active hook: a real commit drops the forbidden path but commits the legit work."""
    arm_gate_hook(git_repo)
    stage_forbidden(git_repo)
    (git_repo / "src").mkdir()
    (git_repo / "src" / "feature.py").write_text("y = 2\n", encoding="utf-8")
    run_cmd(["git", "add", "src/feature.py"], git_repo)
    result = attempt_commit(git_repo, "work beside evil", loop=True, no_verify=False)
    assert result.returncode == 0
    committed = run_cmd(["git", "show", "--name-only", "--format=", "HEAD"], git_repo).split()
    assert "src/feature.py" in committed  # legit work landed
    assert "harness/evil.py" not in committed  # forbidden path kept out of the commit
    assert (git_repo / "harness" / "evil.py").exists()  # but left in the working tree, not reverted


def test_hook_allows_forbidden_path_without_loop(git_repo: Path) -> None:
    """Without RALPH_LOOP (a human), the same commit is allowed — containment is loop-only."""
    arm_gate_hook(git_repo)
    stage_forbidden(git_repo)
    result = attempt_commit(git_repo, "human edit", loop=False, no_verify=False)
    assert result.returncode == 0


def test_no_verify_bypasses_the_hook(git_repo: Path) -> None:
    """--no-verify skips the hook entirely: containment is best-effort, not a jail."""
    arm_gate_hook(git_repo)
    stage_forbidden(git_repo)
    result = attempt_commit(git_repo, "bypass", loop=True, no_verify=True)
    assert result.returncode == 0


def test_hook_blocks_banned_pattern_and_makes_no_commit(git_repo: Path) -> None:
    """A staged banned pattern can't be ejected, so the hook rejects the commit — nothing lands."""
    arm_gate_hook(git_repo)
    (git_repo / "src").mkdir()
    (git_repo / "src" / "x.py").write_text("value = 1  # noqa\n", encoding="utf-8")
    run_cmd(["git", "add", "src/x.py"], git_repo)
    result = attempt_commit(git_repo, "sneaky", loop=True, no_verify=False)
    assert result.returncode != 0
    assert "banned pattern 'noqa'" in result.stderr
    log = run_cmd(["git", "log", "--oneline"], git_repo)
    assert "sneaky" not in log  # the commit never happened
