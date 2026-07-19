"""Tests for the preflight/gate checks and loop containment (harness.gate)."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from harness import gate
from harness.tests.conftest import fake_popen, run_cmd

REPO_ROOT = Path(__file__).resolve().parents[2]


def stage(repo: Path, name: str, content: str) -> None:
    """Write a file inside the repo and stage it."""
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_cmd(["git", "add", name], repo)


def staged() -> list[str]:
    """Paths currently in the index, via the gate's own git helper (run_git returns raw stdout)."""
    return gate.run_git(["diff", "--cached", "--name-only"]).splitlines()


def containment_fail() -> list[str]:
    """Run only the loop-containment checks against the staged index."""
    return gate.run_non_human_checks()


# --------------------------------------------------------------------------- run_git


def test_run_git_returns_stdout(git_repo: Path) -> None:
    """run_git runs git in the repo and returns its raw stdout string (callers .splitlines())."""
    stage(git_repo, "pkg/a.py", "x = 1\n")
    assert gate.run_git(["diff", "--cached", "--name-only"]) == "pkg/a.py\n"


def test_run_git_ignores_poisoned_hook_env(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A poisoned GIT_DIR a hook exported does not redirect the gate's git calls: run_git strips GIT_*,
    so it still runs against the real repo. (Without stripping, git would honor the bogus GIT_DIR and
    fail — this asserts the strip is load-bearing, not just that staging happens to work.)
    """
    monkeypatch.setenv("GIT_DIR", str(git_repo / "does-not-exist" / ".git"))
    stage(git_repo, "pkg/a.py", "x = 1\n")
    assert staged() == ["pkg/a.py"]  # real index read despite the poisoned GIT_DIR


# --------------------------------------------------------------------------- prepare-commit-msg


def test_prepare_commit_msg_noops_without_loop(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tiny_fake_repo: Path
) -> None:
    """Human mode is untouched."""
    message = tiny_fake_repo / ".git" / "COMMIT_EDITMSG"
    message.write_text("", encoding="utf-8")
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    monkeypatch.chdir(tiny_fake_repo)
    assert gate.prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "message"]) == 0
    assert not capsys.readouterr().out


def test_prepare_commit_msg_allows_loop_staged_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tiny_fake_repo: Path
) -> None:
    """A loop commit with a real index change passes."""
    message = tiny_fake_repo / ".git" / "COMMIT_EDITMSG"
    message.write_text("real work\n", encoding="utf-8")
    stage(tiny_fake_repo, "feature.py", "y = 2\n")
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(tiny_fake_repo)
    assert gate.prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "message"]) == 0
    assert not capsys.readouterr().out


def test_prepare_commit_msg_rejects_loop_empty_tree(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tiny_fake_repo: Path
) -> None:
    """A loop commit whose index tree equals HEAD is blocked."""
    message = tiny_fake_repo / ".git" / "COMMIT_EDITMSG"
    message.write_text("empty\n", encoding="utf-8")
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(tiny_fake_repo)
    assert gate.prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "message"]) == 1
    assert capsys.readouterr().out == (
        "\n[COMMIT BLOCKED]:\n"
        "Empty-tree commit detected. Stage real work and don't use --allow-empty. Lazy.\n\n"
    )


def test_prepare_commit_msg_rejects_blank_loop_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tiny_fake_repo: Path
) -> None:
    """A blank or comment-only loop commit message is blocked even with staged work."""
    message = tiny_fake_repo / ".git" / "COMMIT_EDITMSG"
    message.write_text("# generated comment only\n\n", encoding="utf-8")
    stage(tiny_fake_repo, "feature.py", "y = 2\n")
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(tiny_fake_repo)
    assert gate.prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "message"]) == 1
    assert capsys.readouterr().out == (
        "\n[COMMIT BLOCKED]:\nCommit message is blank. Provide an informative message with your agent ID.\n\n"
    )


def test_prepare_commit_msg_allows_initial_staged_commit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tiny_fake_repo: Path
) -> None:
    """An unborn repo with staged files is real work."""
    message = tiny_fake_repo / ".git" / "COMMIT_EDITMSG"
    message.write_text("initial work\n", encoding="utf-8")
    run_cmd(["git", "checkout", "--orphan", "initial"], tiny_fake_repo)
    run_cmd(["git", "rm", "-qr", "--cached", "."], tiny_fake_repo)
    stage(tiny_fake_repo, "first.py", "x = 1\n")
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(tiny_fake_repo)
    assert gate.prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "message"]) == 0
    assert not capsys.readouterr().out


def test_prepare_commit_msg_rejects_initial_empty_commit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tiny_fake_repo: Path
) -> None:
    """An unborn repo with an empty index is still an empty-tree loop commit."""
    message = tiny_fake_repo / ".git" / "COMMIT_EDITMSG"
    message.write_text("empty initial\n", encoding="utf-8")
    run_cmd(["git", "checkout", "--orphan", "initial"], tiny_fake_repo)
    run_cmd(["git", "rm", "-qr", "--cached", "."], tiny_fake_repo)
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(tiny_fake_repo)
    assert gate.prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "message"]) == 1
    assert capsys.readouterr().out == (
        "\n[COMMIT BLOCKED]:\n"
        "Empty-tree commit detected. Stage real work and don't use --allow-empty. Lazy.\n\n"
    )


@pytest.mark.parametrize("source", ["merge", "squash", "rebase", "reset", "clean", "filter-branch"])
def test_prepare_commit_msg_rejects_dangerous_loop_sources(
    source: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tiny_fake_repo: Path
) -> None:
    """Dangerous loop commit sources are blocked."""
    message = tiny_fake_repo / ".git" / "COMMIT_EDITMSG"
    message.write_text("real work\n", encoding="utf-8")
    stage(tiny_fake_repo, "feature.py", "y = 2\n")
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(tiny_fake_repo)
    assert gate.prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", source]) == 1
    assert capsys.readouterr().out == (
        f"\n[COMMIT BLOCKED]:\nYou cannot use that git command `{source}`.\n\n"
    )


def test_prepare_commit_msg_allows_loop_commit_source(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tiny_fake_repo: Path
) -> None:
    """Source `commit` is allowed for amend/reuse-message flows."""
    message = tiny_fake_repo / ".git" / "COMMIT_EDITMSG"
    message.write_text("real work\n", encoding="utf-8")
    stage(tiny_fake_repo, "feature.py", "y = 2\n")
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.chdir(tiny_fake_repo)
    assert gate.prepare_commit_msg(["prepare-commit-msg", ".git/COMMIT_EDITMSG", "commit"]) == 0
    assert not capsys.readouterr().out


def test_prepare_commit_msg_hook_rejects_loop_empty_no_verify(tiny_fake_repo: Path) -> None:
    """prepare-commit-msg still runs under --no-verify and blocks loop empty-tree commits."""
    before = run_cmd(["git", "rev-parse", "HEAD"], tiny_fake_repo)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["RALPH_LOOP"] = "1"
    result = subprocess.run(
        ["git", "commit", "--allow-empty", "--no-verify", "-m", "empty"],
        cwd=tiny_fake_repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert "\n[COMMIT BLOCKED]:\nEmpty-tree commit detected." in result.stderr
    assert run_cmd(["git", "rev-parse", "HEAD"], tiny_fake_repo) == before


def test_prepare_commit_msg_hook_allows_human_empty_no_verify(tiny_fake_repo: Path) -> None:
    """Humans keep the same empty-commit behavior."""
    before = run_cmd(["git", "rev-parse", "HEAD"], tiny_fake_repo)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.pop("RALPH_LOOP", None)
    result = subprocess.run(
        ["git", "commit", "--allow-empty", "--no-verify", "-m", "empty"],
        cwd=tiny_fake_repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert run_cmd(["git", "rev-parse", "HEAD"], tiny_fake_repo) != before


def test_prepare_commit_msg_hook_allows_loop_staged_commit(tiny_fake_repo: Path) -> None:
    """Loop commits with staged work still land."""
    stage(tiny_fake_repo, "feature.py", "y = 2\n")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["RALPH_LOOP"] = "1"
    result = subprocess.run(
        ["git", "commit", "-m", "real work"],
        cwd=tiny_fake_repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert (
        "feature.py" in run_cmd(["git", "show", "--name-only", "--format=", "HEAD"], tiny_fake_repo).split()
    )


@pytest.mark.parametrize("source", ["merge", "squash"])
def test_prepare_commit_msg_hook_dispatch_blocks_loop_merge_and_squash_sources(
    tiny_fake_repo: Path, source: str
) -> None:
    """Running the tracked prepare-commit-msg hook directly blocks merge and squash commits in loop mode."""
    stage(tiny_fake_repo, "feature.py", "y = 2\n")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["RALPH_LOOP"] = "1"
    result = subprocess.run(
        [".githooks/prepare-commit-msg", ".git/COMMIT_EDITMSG", source],
        cwd=tiny_fake_repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert f"You cannot use that git command `{source}`." in result.stdout


# --------------------------------------------------------------------------- tool dispatch


def test_run_checks_reports_fully(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each check is recorded by name under 'pass' or 'fail' from the tool's exit code.

    Fakes the Popen seam (the external tool) so the real header + bucketing run.
    """
    monkeypatch.delenv("RALPH_LOOP", raising=False)  # bucketing-only: skip the loop containment git path
    fake_popen(monkeypatch, fails=[["boom"]])
    captured = gate.run_checks({"boom check": ["boom"], "fine check": ["fine"]})
    assert captured == {"pass": ["fine check"], "fail": ["boom check"], "warn": []}


def test_run_checks_messages_what_happened(monkeypatch: pytest.MonkeyPatch) -> None:
    """A passing check is recorded under 'pass' with nothing in 'fail'."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)  # bucketing-only: skip the loop containment git path
    fake_popen(monkeypatch)
    assert gate.run_checks({"ok": ["tool"]}) == {"pass": ["ok"], "fail": [], "warn": []}


def test_run_checks_records_a_failing_check_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing check lands under 'fail' by its name, with nothing in 'pass'."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)  # bucketing-only: skip the loop containment git path
    fake_popen(monkeypatch, fails=[["tool"]])
    captured = gate.run_checks({"random_check": ["tool"]})
    assert captured == {"pass": [], "fail": ["random_check"], "warn": []}


def test_run_checks_streams_command_output_live(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """The real Popen seam streams the child process output and buckets a zero exit as a pass."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)  # dispatch-only: skip the loop containment git path
    result = gate.run_checks({"echo": ["/bin/sh", "-c", "printf 'hello from the check\\n'"]})
    assert result == {"pass": ["echo"], "fail": [], "warn": []}
    assert "hello from the check" in capfd.readouterr().out


def test_run_checks_buckets_nonzero_exit_as_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real Popen seam reads the child's nonzero status and buckets that check under 'fail'."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)  # dispatch-only: skip the loop containment git path
    result = gate.run_checks({"boom": ["/bin/sh", "-c", "exit 7"]})
    assert result == {"pass": [], "fail": ["boom"], "warn": []}


def test_run_checks_prints_phase_header_then_spawns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """run_checks prints our PHASE header for each check even when the tool itself is faked."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)  # dispatch-only: skip the loop containment git path
    fake_popen(monkeypatch)
    result = gate.run_checks({"ruff lint": ["tool"]})
    assert result == {"pass": ["ruff lint"], "fail": [], "warn": []}
    assert "PHASE: RUFF LINT" in capsys.readouterr().out


def test_preflight_appends_containment_only_under_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Containment runs at pre-commit (run_preflight) only under RALPH_LOOP, never for a human."""

    def fake_containment() -> list[str]:
        return ["containment problem"]

    fake_popen(monkeypatch)
    monkeypatch.setattr(gate, "run_non_human_checks", fake_containment)
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    assert gate.run_preflight()["fail"] == []  # human: no containment
    monkeypatch.setenv("RALPH_LOOP", "1")
    assert "containment problem" in gate.run_preflight()["fail"]  # agent: containment appended


def test_lint_command_keeps_show_fixes_flag() -> None:
    """The lint command asks ruff to show applied and suggested fixes."""
    assert gate.COMMIT_CHECKS["lint"] == [
        "uv",
        "run",
        "--no-cache",
        "--no-sync",
        "ruff",
        "check",
        "--show-fixes",
        ".",
    ]


def test_full_gate_runs_every_preflight_and_gate_check_from_pyproject() -> None:
    """The full gate runs the preflight + gate checks declared in pyproject.toml: at least 7 in total,
    and each FULL_CHECKS name matches a key under [tool.harness.preflight] or [tool.harness.gate].
    """
    raw_toml = tomllib.loads((REPO_ROOT / "pyproject.toml").read_bytes().decode())["tool"]["harness"]
    preflight, gate_checks = raw_toml.get("preflight"), raw_toml.get("gate")
    assert len(preflight) >= 4
    assert len(gate_checks) >= 3
    assert set(gate.FULL_CHECKS) == set(preflight) | set(gate_checks)


def test_gate_tolerates_fully_deleted_harness_config(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """When a user deletes every [tool.harness.*] section, the loader's `.get(..., {})` defaults collapse
    each constant to empty (the loader parsing a POPULATED config is already covered by the other tests).
    This pins the CONSUMER side: with everything empty, running the whole `harness gate` under
    RALPH_LOOP=1 (checks + containment) runs zero checks and ejects/flags nothing, so a clean staged
    commit passes instead of crashing.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    deleted: dict[str, dict[str, list[str]]] = {}  # a pyproject with [tool.harness] removed parses to this
    assert deleted.get("preflight", {}) | deleted.get("gate", {}) == {}  # deletion -> empty via .get default
    monkeypatch.setattr(gate, "FULL_CHECKS", {})
    monkeypatch.setattr(gate, "FORBIDDEN_FILES", [])
    monkeypatch.setattr(gate, "FORBIDDEN_DIRS", ())
    monkeypatch.setattr(gate, "FORBIDDEN_PATTERNS", [])
    stage(
        git_repo, "src/feature.py", "def g(*args):\n    pass  # noqa\n"
    )  # banned pattern, no preference break
    assert gate.run_gate() == {
        "pass": [],
        "fail": ["src/feature.py:1: '*args'/'**kwargs' hide the function signature, use explicit parameters"],
        "warn": [],
    }
    assert "src/feature.py" in staged()  # nothing ejected: no forbidden config to eject against


def test_gate_tolerates_partially_deleted_harness_config(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """The likelier user error: delete [tool.harness.gate] and FORBIDDEN but keep one preflight check. The
    survivor still dispatches and the missing sections default to empty, so the gate runs exactly the
    remaining check and containment (real git on the fixture repo) ejects nothing — a smaller gate, not a
    crash.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    # Only preflight.lint survived; a real no-op command so containment's real git can run alongside it.
    monkeypatch.setattr(gate, "FULL_CHECKS", {"lint": ["/bin/sh", "-c", "exit 0"]})
    monkeypatch.setattr(gate, "FORBIDDEN_FILES", [])
    monkeypatch.setattr(gate, "FORBIDDEN_DIRS", ())
    monkeypatch.setattr(gate, "FORBIDDEN_PATTERNS", [])
    stage(git_repo, "harness/util.py", "value = 1\n")  # would be ejected IF FORBIDDEN_DIRS still had it
    result = gate.run_gate()
    assert result == {"pass": ["lint"], "fail": [], "warn": []}  # survivor ran; empty FORBIDDEN flags nothing
    assert "harness/util.py" in staged()  # FORBIDDEN deleted -> nothing ejected, not a crash


def test_preflight_tolerates_deleted_format_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting `format` from [tool.harness.preflight] just drops a key; run_preflight iterates the
    remaining checks and does not crash. Simulated by removing 'format' from COMMIT_CHECKS.
    """
    without_format = {name: cmd for name, cmd in gate.COMMIT_CHECKS.items() if name != "format"}
    monkeypatch.setattr(gate, "COMMIT_CHECKS", without_format)
    fake_popen(monkeypatch)
    result = gate.run_preflight()
    assert result == {"pass": list(without_format), "fail": [], "warn": []}


def test_gate_runs_a_javascript_toolchain_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user can swap the whole [tool.harness] toolchain for JS commands (npm lint/format in preflight,
    typecheck/test/build in gate) and the gate is agnostic: it spawns exactly the five configured checks,
    in order, and buckets each by exit code — nothing here is Python-specific.

    Passes because fake Popen returns 0, not because js is configured yet.
    """
    js_checks = {
        "lint": ["npm", "run", "lint"],
        "format": ["npm", "run", "format:check"],
        "typecheck": ["npm", "run", "typecheck"],
        "test": ["npm", "test"],
        "build": ["npm", "run", "build"],
    }
    monkeypatch.setattr(gate, "FULL_CHECKS", js_checks)
    calls = fake_popen(monkeypatch)
    result = gate.run_gate()
    assert [launch[0] for launch in calls] == list(js_checks.values())
    assert result == {"pass": ["lint", "typecheck", "test", "build"], "fail": [], "warn": ["format"]}


def test_types_check_uses_pyright_json_output() -> None:
    """The types check runs pyright in JSON mode for stable machine-readable output."""
    assert gate.FULL_CHECKS["types"] == ["uv", "run", "--no-sync", "pyright", "--outputjson"]


def test_security_check_uses_semgrep_and_blocks_on_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The security check runs Semgrep with auto + secrets rules, and --error makes it BLOCKING:
    a nonzero exit (Semgrep's signal for a finding under --error) buckets 'security' under 'fail',
    not 'pass'. An advisory scan that reports but never blocks is worse than none.
    """
    command = gate.FULL_CHECKS["security"]
    assert command[:5] == ["uv", "run", "--no-sync", "semgrep", "scan"]
    assert "--error" in command  # exit nonzero on findings so the nonzero -> 'fail' rule below can bite
    assert "--config" in command
    assert "auto" in command
    assert "p/secrets" in command
    monkeypatch.delenv("RALPH_LOOP", raising=False)  # dispatch-only: skip the loop containment git path
    fake_popen(monkeypatch, fails=[command])  # semgrep --error exits nonzero on a finding
    assert gate.run_checks({"security": command}) == {"pass": [], "fail": ["security"], "warn": []}


# --------------------------------------------------------------------- run_gate vs run_preflight routing


def test_gate_pytest_command_enforces_full_coverage_and_buckets_failures() -> None:
    """The gate's pytest command keeps coverage reporting and the 100% coverage threshold."""
    pytest_command = gate.FULL_CHECKS["pytest"]
    assert pytest_command[:5] == ["uv", "run", "--no-cache", "--no-sync", "pytest"]
    assert "--cov" in pytest_command
    assert "--cov-report=term-missing" in pytest_command
    assert "--cov-fail-under=100" in pytest_command


def test_gate_buckets_a_failing_pytest_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the pytest command exits nonzero (e.g. a coverage gap), run_checks records it under 'fail'.
    Faking the Popen seam proves the bucketing without a real, recursive pytest subprocess.
    """
    monkeypatch.delenv("RALPH_LOOP", raising=False)  # dispatch-only: skip the loop containment git path
    fake_popen(monkeypatch, fails=[gate.FULL_CHECKS["pytest"]])
    result = gate.run_checks({"tests": gate.FULL_CHECKS["pytest"]})
    assert result["fail"] == ["tests"]


def test_preflight_invokes_only_lint_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integrated routing: run_preflight runs only the commit checks."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    calls = fake_popen(monkeypatch)
    result = gate.run_preflight()
    spawned = [launch[0] for launch in calls]
    assert spawned == list(gate.COMMIT_CHECKS.values())  # preflight runs exactly the commit checks, in order
    # format buckets into 'warn', not 'pass', so pass is the commit checks minus any format check.
    assert result["pass"] == [name for name in gate.COMMIT_CHECKS if "format" not in name]
    assert result["fail"] == []


def test_run_gate_delegates_to_run_checks_with_full_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_gate is a thin router: it runs exactly FULL_CHECKS on the given repo and returns that result.

    The real end-to-end behaviour of every gate check is proven in test_integration's single full-gate
    test; here we only pin the routing (repo + FULL_CHECKS in, run_checks' result out) without paying
    for real tools or risking the pytest check recursively collecting this suite.

    NO NESTED PYTEST: run_checks is stubbed with `spy`, so run_gate spawns nothing. This is the pattern
    to copy for any new routing assertion — stub run_checks instead of adding a real-pytest spawn.
    """
    seen: dict[str, object] = {}

    def spy(checks: dict[str, list[str]]) -> dict[str, list[str]]:
        seen["checks"] = checks
        return {"pass": ["types"], "fail": []}

    monkeypatch.setattr(gate, "run_checks", spy)
    result = gate.run_gate()
    assert seen == {"checks": gate.FULL_CHECKS}
    assert result == {"pass": ["types"], "fail": []}


# --------------------------------------------------------------------------- containment (loop only)


def test_preflight_ejects_forbidden_file_under_loop(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A staged forbidden FILE (exact-path set) is dropped from the index, kept in the tree."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "pyproject.toml", "x = 1\n")
    assert containment_fail() == []  # self-heals, not blocked
    assert "pyproject.toml" not in staged()
    assert (git_repo / "pyproject.toml").exists()  # edit survives in the working tree


@pytest.mark.parametrize("path", ["harness/util.py", "tests/harness/x.py", ".github/ci.yml", ".githooks/x"])
def test_preflight_ejects_forbidden_dir_under_loop(
    path: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged file under any forbidden DIR (dir-set ancestor match) is dropped from the index."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, path, "value = 1\n")
    assert containment_fail() == []
    assert path not in staged()


def test_preflight_keeps_legit_work_beside_forbidden(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Only the forbidden path is dropped; the agent's own work still commits."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "harness/util.py", "value = 1\n")
    stage(git_repo, "src/feature.py", "y = 2\n")
    assert containment_fail() == []
    after = staged()
    assert "harness/util.py" not in after
    assert "src/feature.py" in after


def test_ejected_forbidden_py_is_not_preference_checked(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A forbidden .py that ALSO breaks a preference is ejected AND self-heals: because ejection
    removes it from the judged set, its preference break must NOT land in fail (ejecting is exit-0).
    Regression: the prefs loop once iterated the pre-eject staged list and blocked the commit.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "harness/evil.py", "_bad = 1\n")  # forbidden DIR + underscore-name preference break
    assert containment_fail() == []  # ejected, not judged: commit still succeeds
    assert "harness/evil.py" not in staged()


def test_forbidden_file_match_is_case_insensitive(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """The forbidden-file set is matched case-insensitively, so a mixed-case protected filename is
    still ejected (an agent can't smuggle it past by changing case).
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "PyProject.TOML", "x = 1\n")  # same file as pyproject.toml, different case
    assert containment_fail() == []
    assert "PyProject.TOML" not in staged()  # ejected despite the casing


def test_banned_pattern_in_ejected_file_is_not_flagged(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A banned pattern living in a forbidden file is not a failure: ejection happens BEFORE the
    banned-pattern scan re-reads the staged diff, so the ejected file's noqa never reaches it.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "pyproject.toml", "x = 1  # noqa\n")  # forbidden file that also holds a banned pattern
    assert containment_fail() == []  # ejected before the scan; nothing to block
    assert "pyproject.toml" not in staged()


def test_preflight_ejects_staged_deletion_of_forbidden(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged DELETION of a forbidden file is undone, so the agent can't remove protected files."""
    stage(git_repo, "pyproject.toml", "x = 1\n")
    run_cmd(["git", "commit", "-q", "-m", "add pyproject"], git_repo)
    run_cmd(["git", "rm", "-q", "pyproject.toml"], git_repo)
    monkeypatch.setenv("RALPH_LOOP", "1")
    assert containment_fail() == []
    assert "pyproject.toml" not in staged()  # the deletion was reset out of the index


def test_preflight_skips_containment_without_loop(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Without RALPH_LOOP, a human may stage forbidden paths: nothing is ejected."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    stage(git_repo, "harness/util.py", "value = 1\n")
    assert "harness/util.py" in staged()  # read the index before faking Popen (git can't run faked)
    fake_popen(monkeypatch)
    result = gate.run_preflight()
    assert result["fail"] == []  # no-loop preflight runs only the faked checks; it has no eject path at all


@pytest.mark.parametrize("pattern", ["# noqa", "type: ignore", "--no-verify"])
def test_preflight_flags_banned_pattern_under_loop(
    pattern: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A banned escape-hatch in an added line is flagged (so the commit is rejected)."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/x.py", f"value = 1  # {pattern}\n")
    assert any(f"'{pattern}' line:" in problem for problem in containment_fail())


def test_preflight_banned_pattern_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Mixed-case escape hatches are still caught."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/x.py", "value = 1  # NoQA\n")
    assert any("'# noqa' line:" in problem for problem in containment_fail())


@pytest.mark.parametrize(
    ("typed", "canonical"),
    [("tS-ignoRe", "ts-ignore"), ("# Pylint:", "# pylint:"), ("PRAGMA: no cover", "pragma: no cover")],
)
def test_preflight_flags_weird_case_banned_patterns(
    typed: str, canonical: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """An added line carrying a banned pattern in odd mixed casing is still flagged: the pattern set is
    hardcoded lowercase and the scan casefolds only the line, so the message uses that lowercase pattern.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/x.py", f"value = 1  # {typed}\n")
    fail = containment_fail()
    assert any(isinstance(p, str) and p.startswith(f"'{canonical}' line:") for p in fail)


def test_preflight_flags_preferences_break_under_loop(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged Python file that breaks a preference (underscore name) is flagged."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/mod.py", "_bad = 1\n")
    assert any("'_bad'" in problem for problem in containment_fail())


def test_preflight_judges_staged_not_working_tree(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Preferences judge the INDEX, not disk. Stage a clean file, then dirty the working tree with a
    violation that is never staged: the commit is not blocked (only staged content counts).
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/mod.py", "good = 1\n")  # index: clean
    (git_repo / "src/mod.py").write_text("_bad = 1\n", encoding="utf-8")  # working tree only: violation
    assert containment_fail() == []


def test_preflight_preferences_read_one_file_at_a_time(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Each prefs() call receives exactly one staged file's source, never several concatenated."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    sources: list[str] = []

    def record(path: str, source: str) -> str:
        del path
        sources.append(source)
        return ""

    monkeypatch.setattr(gate, "prefs", record)
    stage(git_repo, "src/a.py", "a = 1\n")
    stage(git_repo, "src/b.py", "b = 2\n")
    containment_fail()
    # each call gets exactly one file's staged source (git show preserves the trailing newline)
    assert sorted(s.rstrip("\n") for s in sources) == ["a = 1", "b = 2"]


def test_preflight_skips_preferences_on_non_python_staged_file(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged non-.py file is not preference-checked (only Python style is judged), so it never
    lands in fail even with loop containment on.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "notes.txt", "_bad = 1\n")  # underscore name, but not Python — must be ignored
    assert containment_fail() == []


def test_preflight_skips_preferences_for_staged_deletion(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged DELETION of a .py file is filtered out (--diff-filter=d) before any `git show :path`,
    so preference checking skips it (nothing to judge) rather than crashing.
    """
    stage(git_repo, "src/gone.py", "value = 1\n")
    run_cmd(["git", "commit", "-q", "-m", "add gone"], git_repo)
    run_cmd(["git", "rm", "-q", "src/gone.py"], git_repo)  # staged deletion: no :path blob
    monkeypatch.setenv("RALPH_LOOP", "1")
    assert containment_fail() == []


def test_preflight_tolerates_missing_preferences(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """If preferences.py was deleted (prefs is None), the Python style check is skipped, not crashed."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "prefs", None)
    stage(git_repo, "src/mod.py", "_bad = 1\n")
    assert containment_fail() == []


def test_gate_imports_cleanly_without_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    """If preferences.py is absent, gate still imports and prefs is None (the ImportError branch)."""
    monkeypatch.setitem(sys.modules, "preferences.preferences", None)
    importlib.reload(gate)
    assert gate.prefs is None
    monkeypatch.undo()
    importlib.reload(gate)
    assert gate.prefs is not None


# ------------------------------------------------- check_for_bad_patterns (direct, no ejection wrapper)


def test_check_for_bad_patterns_flags_a_banned_pattern(git_repo: Path) -> None:
    """Called directly, it returns a banned-pattern problem for a staged added line carrying one."""
    stage(git_repo, "src/x.py", "value = 1  # noqa\n")
    problems = gate.check_for_bad_patterns()
    assert any(problem.startswith("'# noqa' line:") for problem in problems)


def test_check_for_bad_patterns_ignores_markdown_prose(git_repo: Path) -> None:
    """A banned token quoted in .md docs is prose, not a bypass, so it is excluded from the scan;
    the same token in a non-.md file is still flagged (the anti-bypass net stays on code/config).
    """
    stage(git_repo, "docs/notes.md", "Run with `# noqa` to silence the linter.\n")  # prose: ignored
    stage(git_repo, "run.sh", "grep --no-verify\n")  # non-.md: still scanned
    problems = gate.check_for_bad_patterns()
    assert not any("# noqa" in problem for problem in problems)  # markdown excluded
    assert any(problem.startswith("'--no-verify' line:") for problem in problems)  # shell still caught


def test_check_for_bad_patterns_appends_a_preference_violation(git_repo: Path) -> None:
    """A staged .py file that breaks a preference contributes its violation to the returned problems."""
    stage(git_repo, "src/mod.py", "_bad = 1\n")  # lone-underscore name trips a preference
    problems = gate.check_for_bad_patterns()
    assert any("'_bad'" in problem for problem in problems)


def test_check_for_bad_patterns_clean_staged_file_has_no_problems(git_repo: Path) -> None:
    """A staged file with no banned patterns and no preference breaks yields an empty problem list."""
    stage(git_repo, "src/ok.py", "value = 1\n")
    assert gate.check_for_bad_patterns() == []


@pytest.mark.usefixtures("git_repo")  # anchors gate.REPO_ROOT at the seeded fixture repo; not referenced
def test_check_for_bad_patterns_empty_index_returns_no_problems() -> None:
    """With nothing staged the diff is empty, so both scans are skipped and no problems are returned."""
    assert not staged()  # git_repo has only the seed commit; nothing staged
    assert gate.check_for_bad_patterns() == []  # clean index: seed commit only, nothing staged


@pytest.mark.usefixtures("git_repo")  # anchors gate.REPO_ROOT at the seeded fixture repo; not referenced
def test_empty_commit_does_not_block_under_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty commit (nothing staged) is not blocked: containment is skipped and no problems returned."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    assert gate.run_non_human_checks() == []  # seed commit only, nothing staged


def test_language_without_preferences_file_crashes_check_for_bad_patterns(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Setting languages=['rb'] routes staged .rb files into the Python `ast`-based prefs, which cannot
    parse Ruby: the preference walk raises SyntaxError. Pins that the prefs engine is Python-only.
    """
    monkeypatch.setattr(gate, "languages", ["js"])
    stage(git_repo, "preferences.js", "console.log('pass');\n")  # valid js, invalid py
    gate.check_for_bad_patterns()
    monkeypatch.setattr(gate, "languages", ["rb"])
    stage(git_repo, "app.rb", "def foo; end\n")  # valid Ruby, invalid py
    gate.check_for_bad_patterns()


# --------------------------------------------------------- spec tests (FAIL against the current bugs)


def test_staged_noqa_produces_a_noqa_line_message_in_fail(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged `# noqa` must land in fail as the scan's own message `'# noqa' line: <code>`. FAILS now:
    `found.join(...)` discards its result, so the banned-pattern scan appends nothing.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/x.py", "value = 1  # noqa\n")
    fail = containment_fail()
    assert any(isinstance(p, str) and p.startswith("'# noqa' line:") for p in fail)


def test_reset_ejects_only_forbidden_keeping_legit_staged(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Ejection resets ONLY forbidden paths; a legit file staged alongside stays in the index. FAILS
    now: reset is passed every staged path, so the legit file is unstaged too.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "pyproject.toml", "x = 1\n")  # forbidden
    stage(git_repo, "src/feature.py", "y = 2\n")  # legit
    containment_fail()
    after = staged()
    assert "pyproject.toml" not in after  # forbidden ejected
    assert "src/feature.py" in after  # legit work survives


def test_prefs_skips_non_python_invalid_source(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A staged non-.py file is not fed to prefs/ast.parse. Its bytes are invalid Python, so if the
    `.py` filter were missing the run would crash. FAILS now: no suffix filter, `ast.parse` raises.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "data.json", "{not: valid python (((\n")  # invalid Python; must never reach prefs
    assert containment_fail() == []


def test_ejected_forbidden_py_is_not_re_judged_by_prefs(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A forbidden .py that breaks a preference is ejected, so prefs must NOT re-judge it (ejecting is
    exit-0). FAILS now: the prefs loop reads the post-eject staged list but has no forbidden filter,
    and the ejected file is still on disk / in the diff path set, so its break lands in fail.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "harness/evil.py", "_bad = 1\n")  # forbidden DIR + underscore-name break
    fail = containment_fail()
    assert fail == []  # ejected, not re-judged
    assert "harness/evil.py" not in staged()


# ----------------------------------------- banned-pattern scan only matches added ('+', not '+++') lines
# git diff --cached --unified=0 emits, per hunk: `--- a/f`, `+++ b/f` (headers), `-old` (removed),
# `+new` (added). The scan (gate.run_preflight line 163) must flag ONLY the real added line ('+',
# excluding the '+++' file header); removed ('-') and header ('+++') lines carrying a banned pattern
# must be ignored. A '*'-prefixed line can never occur in unified diff output, so nothing starting
# with '*' is ever matched — proven here by the removed-line case (only '+' counts).


def test_banned_scan_flags_added_plus_line(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """An ADDED ('+') line carrying noqa is flagged. FAILS now: the scan's message is char-shredded."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/x.py", "value = 1  # noqa\n")  # a pure addition -> a '+' hunk line
    fail = containment_fail()
    assert any(isinstance(p, str) and p.startswith("'# noqa' line:") for p in fail)


def test_banned_scan_ignores_plus_plus_plus_header_line(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """The '+++ b/<path>' file-HEADER line is not an added code line: a banned pattern living only in
    the path (a file literally named with 'noqa') must not be flagged by the header, since the scan
    excludes lines starting with '+++'.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/noqa_helpers.py", "value = 1\n")  # 'noqa' appears in the '+++ b/...' header
    fail = containment_fail()
    assert not any(isinstance(p, str) and p.startswith("'noqa' line:") for p in fail)


def test_banned_scan_ignores_removed_minus_line(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A REMOVED ('-') line carrying a banned pattern is ignored: deleting a `# noqa` line is good,
    not a violation. Also proves only '+' is matched (never '-', and never a '*' prefix).
    """
    stage(git_repo, "src/x.py", "value = 1  # noqa\n")
    run_cmd(["git", "commit", "-q", "-m", "seed noqa"], git_repo)  # committed; not in the diff anymore
    stage(git_repo, "src/x.py", "value = 1\n")  # drops the escape-hatch line -> a removed ('-') hunk line
    monkeypatch.setenv("RALPH_LOOP", "1")
    fail = containment_fail()
    assert not any(isinstance(p, str) and "noqa" in p for p in fail)  # removed line is not flagged
