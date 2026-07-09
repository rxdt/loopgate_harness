"""Integration tests for real git hooks with a fake harness, plus hermetic gate dispatch."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from harness import gate
from harness.tests.conftest import fake_popen, run_cmd


def attempt_commit(repo: Path, message: str, loop: bool, no_verify: bool) -> subprocess.CompletedProcess[str]:
    """Try a commit with optional RALPH_LOOP in the env and optional hook bypass."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    if loop:
        env["RALPH_LOOP"] = "1"
    args = ["git", "commit", "-q", "-m", message]
    if no_verify:
        args.append("--no-verify")
    return subprocess.run(args, cwd=repo, capture_output=True, text=True, check=False, env=env)


def stage(repo: Path, name: str, content: str) -> None:
    """Write a file inside the repo and stage it."""
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_cmd(["git", "add", name], repo)


def committed_files(repo: Path) -> list[str]:
    """Paths in the most recent commit."""
    return run_cmd(["git", "show", "--name-only", "--format=", "HEAD"], repo).split()


def log(repo: Path) -> str:
    """One-line git log of the repo."""
    return run_cmd(["git", "log", "--oneline"], repo)


def fake_harness_args(repo: Path) -> str:
    """Return the fake harness argv recorded by the hook."""
    return (repo / "harness.args").read_text(encoding="utf-8")


def fake_harness_loop(repo: Path) -> str:
    """Return the RALPH_LOOP value recorded by the fake harness."""
    return (repo / "harness.loop").read_text(encoding="utf-8")


def set_fake_harness_exit(repo: Path, code: int) -> None:
    """Choose the fake harness process exit code for this repo."""
    (repo / "harness.exit").write_text(f"{code}\n", encoding="utf-8")


def push_head(repo: Path) -> subprocess.CompletedProcess[str]:
    """Push HEAD to a local bare remote with hook-safe environment."""
    bare = repo.parent / "origin.git"
    run_cmd(["git", "init", "--bare", "-q", str(bare)], repo)
    run_cmd(["git", "remote", "add", "origin", str(bare)], repo)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", "push", "-q", "origin", "HEAD:main"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


# --------------------------------------------------------------------------- pre-commit hook wiring


def test_clean_commit_invokes_preflight_and_lands(fake_hook_repo: Path) -> None:
    """A passing fake harness lets the real pre-commit hook commit staged work."""
    stage(fake_hook_repo, "feature.py", "y = 2\n")
    result = attempt_commit(fake_hook_repo, "clean work", loop=False, no_verify=False)
    assert result.returncode == 0, result.stderr
    assert fake_harness_args(fake_hook_repo) == "preflight\n"
    assert "feature.py" in committed_files(fake_hook_repo)


def test_failing_preflight_blocks_the_commit(fake_hook_repo: Path) -> None:
    """A nonzero fake harness exit makes the real pre-commit hook reject the commit."""
    set_fake_harness_exit(fake_hook_repo, 1)
    stage(fake_hook_repo, "bad.py", "import os\ny = 2\n")
    result = attempt_commit(fake_hook_repo, "blocked work", loop=False, no_verify=False)
    assert result.returncode != 0
    assert fake_harness_args(fake_hook_repo) == "preflight\n"
    assert "blocked work" not in log(fake_hook_repo)


def test_format_difference_does_not_block_when_preflight_passes(fake_hook_repo: Path) -> None:
    """Hook wiring allows a commit whenever the harness preflight command exits cleanly."""
    stage(fake_hook_repo, "messy.py", "x=1\n")
    result = attempt_commit(fake_hook_repo, "unformatted but clean", loop=False, no_verify=False)
    assert result.returncode == 0, result.stderr
    assert fake_harness_args(fake_hook_repo) == "preflight\n"
    assert "messy.py" in committed_files(fake_hook_repo)


def test_loop_commit_passes_loop_env_to_preflight(fake_hook_repo: Path) -> None:
    """A hook-run harness inherits RALPH_LOOP from the committing process."""
    stage(fake_hook_repo, "feature.py", "value = 1\n")
    result = attempt_commit(fake_hook_repo, "loop work", loop=True, no_verify=False)
    assert result.returncode == 0, result.stderr
    assert fake_harness_args(fake_hook_repo) == "preflight\n"
    assert fake_harness_loop(fake_hook_repo) == "1\n"


def test_no_verify_bypasses_the_hook(fake_hook_repo: Path) -> None:
    """The git hook itself is skipped when git is asked not to run hooks."""
    stage(fake_hook_repo, "harness/evil.py", "value = 1\n")
    result = attempt_commit(fake_hook_repo, "bypass", loop=True, no_verify=True)
    assert result.returncode == 0
    assert not (fake_hook_repo / "harness.args").exists()


# --------------------------------------------------------------------------- full gate dispatch


def test_full_gate_end_to_end_and_pre_push_hook_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Full gate dispatch runs the real header/bucket path and faults only the tool seam."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    calls = fake_popen(monkeypatch)
    result = gate.run_checks(tmp_path, gate.FULL_CHECKS)

    assert [command for command, cwd, env in calls] == list(gate.FULL_CHECKS.values())
    assert all(cwd == tmp_path for command, cwd, env in calls)
    assert all(env["FORCE_COLOR"] == "1" for command, cwd, env in calls)
    assert set(result["pass"]) | set(result["fail"]) == set(gate.FULL_CHECKS)
    assert result["fail"] == []
    assert "ruff format (no fail)" in result["pass"]


def test_format_report_stays_pass_when_runner_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Format reports are informational, so a nonzero format check is still bucketed as pass."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)  # dispatch-only test: skip the containment git branch
    fake_popen(monkeypatch, fails=[["fmt"]])
    result = gate.run_checks(tmp_path, {"ruff lint": ["lint"], "ruff format (no fail)": ["fmt"]})
    assert result == {"pass": ["ruff lint", "ruff format (no fail)"], "fail": []}


# --------------------------------------------------------------------------- pre-push hook wiring


def test_pre_push_hook_invokes_gate_and_blocks_on_fake_harness_failure(fake_hook_repo: Path) -> None:
    """The real pre-push hook calls `.venv/bin/harness gate` and respects its nonzero exit."""
    stage(fake_hook_repo, "pushable.py", "value = 1\n")
    commit = attempt_commit(fake_hook_repo, "push me", loop=False, no_verify=False)
    assert commit.returncode == 0, commit.stderr
    set_fake_harness_exit(fake_hook_repo, 1)

    push = push_head(fake_hook_repo)

    assert push.returncode != 0
    assert fake_harness_args(fake_hook_repo) == "gate\n"
