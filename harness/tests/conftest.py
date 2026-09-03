"""Shared fixtures and helpers for harness tests."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from subprocess import PIPE

import pytest
from typing_extensions import Self

from harness import cli, gate
from harness.gate import gates
from mutation.check_mutmut import analyze_mutmut_report_passed

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
    test points gates().repo_root at. Only the checks around it are stand-ins.

    Every faked check reports exit 0 (pass) unless its exact argv is in fails, which reports exit 1.
    Every faked launch is recorded (command, cwd, env) so dispatch tests can assert what run_checks ran.

    Returns:
        The live list of recorded launches.
    """
    failing = fails or []
    calls: list[tuple[list[str], Path, dict[str, str]]] = []
    real_popen = gate.subprocess.Popen

    def spawn(
        command: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdout: int | None = None,
        stderr: int | None = None,
        text: bool = False,
    ) -> subprocess.Popen[str] | FakePopen:
        del stdout, stderr, text  # run_git's capture settings, rebuilt below rather than forwarded
        if command[:1] == ["git"]:
            return real_popen(command, env=env, stdout=PIPE, stderr=PIPE, text=True)
        calls.append((command, cwd or REPO_ROOT, env or {}))
        return FakePopen(1 if command in failing else 0)

    monkeypatch.setattr(gate.subprocess, "Popen", spawn)
    return calls


def fake_home(home: Path) -> Callable[[], Path]:
    """A Path.home stand-in so configure_agents writes under a test directory, never the real home.

    Args:
        home: the directory to report as the user's home.

    Returns:
        Zero-argument callable that returns home, matching how Path.home is called.
    """

    def home_path() -> Path:
        return home

    return home_path


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
    mutants = tmp_path / "mutants"
    mutants.mkdir()
    report = mutants / "mutmut-cicd-stats.json"
    shutil.copy2(
        REPO_ROOT / "tests" / "mutation" / "mutmut-cicd-stats.json",
        report,
    )
    gate.run_git(["add", ".gitignore", "README.md", "README.template.md"], tmp_path)
    gate.run_git(["commit", "-q", "-m", "seed"], tmp_path)
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(tmp_path))
    monkeypatch.setattr(gates(), "repo_root", tmp_path)
    monkeypatch.setattr(
        gate,
        "analyze_mutmut_report_passed",
        partial(analyze_mutmut_report_passed, str(report)),
    )
    return tmp_path


@pytest.fixture
def real_hook_repo(request: pytest.FixtureRequest, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Wire selected tracked hooks to a recorded executable in the disposable repository."""
    hooks = git_repo / ".active-hooks"
    hooks.mkdir()
    for name in (*request.param, "_resolve"):
        shutil.copy2(REPO_ROOT / ".githooks" / name, hooks / name)
    gate.run_git(["config", "core.hooksPath", ".active-hooks"], git_repo)

    executable = git_repo / "recorded-harness"
    executable.write_text(
        f"""#!{Path(sys.executable).as_posix()}
import json
import os
import sys
from pathlib import Path

repo = Path.cwd()
arguments = sys.argv[1:]
command = arguments[0] if arguments else ""
recorded = arguments.copy()
if command == "prepare-commit-msg" and len(recorded) > 1:
    recorded[1] = Path(recorded[1]).name
with (repo / "harness.calls").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"arguments": recorded, "RALPH_LOOP": os.environ.get("RALPH_LOOP")}}) + "\\n")
real_file = repo / "harness.real"
real_commands = real_file.read_text(encoding="utf-8").splitlines() if real_file.exists() else []
if command == "prepare-commit-msg" or command in real_commands:
    sys.path.insert(0, {str(REPO_ROOT)!r})
    os.chdir({str(REPO_ROOT)!r})
    from harness import cli
    from harness.gate import gates
    os.chdir(repo)
    gates().repo_root = repo
    cli.REPO_ROOT = repo
    cli.REPO_ROOT_STR = str(repo)
    if command == "preflight":
        gates().commit_checks = {{}}
    cli.main(arguments)
status_file = repo / "harness.exit"
raise SystemExit(int(status_file.read_text(encoding="utf-8")) if status_file.exists() else 0)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    (git_repo / ".git" / "harness-path").write_text(f"{executable}\n", encoding="utf-8")
    monkeypatch.setenv("RALPH_LOOP", "1")
    return git_repo


@pytest.fixture(scope="module")
def scan_repo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A temp repo shared by the generated examples, since @given cannot take a per-test fixture."""
    repo = tmp_path_factory.mktemp("banned-patterns")
    gate.run_git(["init", "-q"], repo)
    gate.run_git(["config", "user.email", "harness@test.local"], repo)
    gate.run_git(["config", "user.name", "harness-test"], repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    gate.run_git(["add", "README.md"], repo)
    gate.run_git(["commit", "-q", "-m", "seed"], repo)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("RALPH_LOOP", "1")
        patch.setattr(gates(), "repo_root", repo)
        patch.setattr(gates(), "commit_checks", {})
        yield repo
