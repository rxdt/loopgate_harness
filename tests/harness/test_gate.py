"""Tests for the preflight/gate checks and loop containment (harness.gate)."""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

import pytest
from conftest import run_cmd

from harness import gate

REPO_ROOT = Path(__file__).resolve().parents[2]


def stage(repo: Path, name: str, content: str) -> None:
    """Write a file inside the repo and stage it."""
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_cmd(["git", "add", name], repo)


def staged(repo: Path) -> list[str]:
    """Paths currently in the index, via the gate's own git helper."""
    return gate.run_git(repo, ["diff", "--cached", "--name-only"]).split()


def clean_checks(repo: Path, checks: object) -> list[str]:
    """Stub run_checks so containment tests don't shell out to ruff/pytest."""
    del repo, checks
    return []


# --------------------------------------------------------------------------- run_git


def test_run_git_returns_stdout(git_repo: Path) -> None:
    """run_git runs git in the repo and returns its stdout."""
    stage(git_repo, "pkg/a.py", "x = 1\n")
    assert gate.run_git(git_repo, ["diff", "--cached", "--name-only"]).split() == ["pkg/a.py"]


def test_run_git_ignores_poisoned_hook_env(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A GIT_DIR a hook exported does not redirect the gate's git calls (GIT_* is stripped)."""
    monkeypatch.setenv("GIT_DIR", str(git_repo / "does-not-exist" / ".git"))
    stage(git_repo, "pkg/a.py", "x = 1\n")
    assert staged(git_repo) == ["pkg/a.py"]


# --------------------------------------------------------------------------- run_checks (break cases)


def test_run_checks_reports_only_failures(tmp_path: Path) -> None:
    """A failing check command is reported (with its name); a passing one is not."""
    failures = gate.run_checks(tmp_path, {"boom": ("false",), "fine": ("true",)})
    assert len(failures) == 1
    assert failures[0].startswith("boom failed:")


def test_run_checks_empty_when_all_pass(tmp_path: Path) -> None:
    """No failures means an empty list (and thus a clean gate)."""
    assert gate.run_checks(tmp_path, {"ok": ("true",)}) == []


def test_check_sets_invoke_tools_via_uv_run() -> None:
    """Every check command runs its tool through `uv run --no-sync`; FULL extends COMMIT."""
    for command in gate.FULL_CHECKS.values():
        assert command[:3] == ("uv", "run", "--no-sync")
    assert gate.COMMIT_CHECKS.items() <= gate.FULL_CHECKS.items()


def test_gate_constants_are_well_formed() -> None:
    """Forbidden collections are sets of str; every check command is a tuple of str."""
    assert isinstance(gate.FORBIDDEN_DIRS, set)
    assert isinstance(gate.FORBIDDEN_FILES, set)
    for name in gate.FORBIDDEN_DIRS | gate.FORBIDDEN_FILES:
        assert isinstance(name, str)
    for pattern in gate.FORBIDDEN_PATTERNS:
        assert isinstance(pattern, str)
    for checks in (gate.COMMIT_CHECKS, gate.FULL_CHECKS):
        for command in checks.values():
            assert isinstance(command, tuple)
            for part in command:
                assert isinstance(part, str)


def test_full_gate_runs_pyright_on_everything() -> None:
    """The full gate runs bare pyright; do not narrow it to selected paths."""
    assert gate.FULL_CHECKS["types"] == ("uv", "run", "--no-sync", "pyright")


def test_full_gate_keeps_the_semgrep_security_check() -> None:
    """The pre-push gate must still run semgrep and fail hard on a finding."""
    security = gate.FULL_CHECKS["security"]
    assert "semgrep" in security
    assert "p/secrets" in security
    assert "--error" in security


def test_full_coverage_requirement_is_enforced() -> None:
    """100% coverage is both required and actually measured by the gate."""
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["coverage"]["report"]["fail_under"] == 100
    assert gate.FULL_CHECKS["tests"] == (
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "--cov",
        "--cov-report=term-missing",
        "--cov-fail-under=100",
    )


def test_ci_workflow_runs_the_same_full_gate_commands() -> None:
    """GitHub CI keeps the same full-check command shape as the local gate."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    run_lines = [line.strip().removeprefix("run: ") for line in workflow.splitlines() if "run: " in line]

    for command in gate.FULL_CHECKS.values():
        if command[3] == "pylint":
            assert any(
                line.split()[:4] == list(command[:4]) and set(line.split()[4:]) == set(command[4:])
                for line in run_lines
            )
            continue
        assert f"run: {' '.join(command)}" in workflow


# --------------------------------------------------------------------------- run_gate / run_preflight wiring


def test_run_gate_runs_the_full_check_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """run_gate forwards FULL_CHECKS to run_checks and surfaces failures."""
    recorded: dict[str, object] = {}

    def record(repo: Path, checks: object) -> list[str]:
        del repo
        recorded["checks"] = checks
        return ["tests failed"]

    monkeypatch.setattr(gate, "run_checks", record)
    assert "tests failed" in gate.run_gate(tmp_path)
    assert recorded["checks"] == gate.FULL_CHECKS


def test_run_preflight_runs_commit_checks_for_everyone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no RALPH_LOOP, preflight just runs COMMIT_CHECKS (no containment)."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    recorded: dict[str, object] = {}

    def record(repo: Path, checks: object) -> list[str]:
        del repo
        recorded["checks"] = checks
        return []

    monkeypatch.setattr(gate, "run_checks", record)
    assert gate.run_preflight(tmp_path) == []
    assert recorded["checks"] == gate.COMMIT_CHECKS


def test_run_preflight_surfaces_a_failing_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A failing quality check (e.g. semgrep) is surfaced so the commit is rejected."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)

    def fails(repo: Path, checks: object) -> list[str]:
        del repo, checks
        return ["security failed:\nca-certs: empty trust anchors"]

    monkeypatch.setattr(gate, "run_checks", fails)
    problems = gate.run_preflight(tmp_path)
    assert any("security failed" in problem for problem in problems)


# --------------------------------------------------------------------------- containment (loop only)


def test_preflight_ejects_forbidden_file_under_loop(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A staged forbidden FILE (exact-path set) is dropped from the index, kept in the tree."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "run_checks", clean_checks)
    stage(git_repo, "pyproject.toml", "x = 1\n")
    assert gate.run_preflight(git_repo) == []  # self-heals, not blocked
    assert "pyproject.toml" not in staged(git_repo)
    assert (git_repo / "pyproject.toml").exists()  # edit survives in the working tree


@pytest.mark.parametrize("path", ["harness/util.py", "tests/harness/x.py", ".github/ci.yml", ".githooks/x"])
def test_preflight_ejects_forbidden_dir_under_loop(
    path: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged file under any forbidden DIR (dir-set ancestor match) is dropped from the index."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "run_checks", clean_checks)
    stage(git_repo, path, "value = 1\n")
    assert gate.run_preflight(git_repo) == []
    assert path not in staged(git_repo)


def test_preflight_keeps_legit_work_beside_forbidden(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Only the forbidden path is dropped; the agent's own work still commits."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "run_checks", clean_checks)
    stage(git_repo, "harness/util.py", "value = 1\n")
    stage(git_repo, "src/feature.py", "y = 2\n")
    assert gate.run_preflight(git_repo) == []
    after = staged(git_repo)
    assert "harness/util.py" not in after
    assert "src/feature.py" in after


def test_preflight_ejects_staged_deletion_of_forbidden(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged DELETION of a forbidden file is undone, so the agent can't remove protected files."""
    stage(git_repo, "pyproject.toml", "x = 1\n")
    run_cmd(["git", "commit", "-q", "-m", "add pyproject"], git_repo)
    run_cmd(["git", "rm", "-q", "pyproject.toml"], git_repo)
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "run_checks", clean_checks)
    assert gate.run_preflight(git_repo) == []
    assert "pyproject.toml" not in staged(git_repo)  # the deletion was reset out of the index


def test_preflight_skips_containment_without_loop(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Without RALPH_LOOP, a human may stage forbidden paths: nothing is ejected."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    monkeypatch.setattr(gate, "run_checks", clean_checks)
    stage(git_repo, "harness/util.py", "value = 1\n")
    assert gate.run_preflight(git_repo) == []
    assert "harness/util.py" in staged(git_repo)  # left staged: containment is loop-only


@pytest.mark.parametrize("pattern", ["noqa", "type: ignore", "--no-verify"])
def test_preflight_flags_banned_pattern_under_loop(
    pattern: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A banned escape-hatch in an added line is flagged (so the commit is rejected)."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "run_checks", clean_checks)
    stage(git_repo, "src/x.py", f"value = 1  # {pattern}\n")
    assert any(f"banned pattern '{pattern}'" in problem for problem in gate.run_preflight(git_repo))


def test_preflight_banned_pattern_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Mixed-case escape hatches are still caught."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "run_checks", clean_checks)
    stage(git_repo, "src/x.py", "value = 1  # NoQA\n")
    assert any("banned pattern 'noqa'" in problem for problem in gate.run_preflight(git_repo))


def test_preflight_flags_preferences_break_under_loop(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged Python file that breaks a preference (underscore name) is flagged."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "run_checks", clean_checks)
    stage(git_repo, "src/mod.py", "_bad = 1\n")
    assert any("'_bad'" in problem for problem in gate.run_preflight(git_repo))


def test_preflight_tolerates_missing_preferences(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """If preferences.py was deleted (prefs is None), the Python style check is skipped, not crashed."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "run_checks", clean_checks)
    monkeypatch.setattr(gate, "prefs", None)
    stage(git_repo, "src/mod.py", "_bad = 1\n")
    assert gate.run_preflight(git_repo) == []


def test_gate_imports_cleanly_without_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    """If preferences.py is absent, gate still imports and prefs is None (the ImportError branch)."""
    monkeypatch.setitem(sys.modules, "harness.preferences", None)
    importlib.reload(gate)
    assert gate.prefs is None
    monkeypatch.undo()
    importlib.reload(gate)
    assert gate.prefs is not None
