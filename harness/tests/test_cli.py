"""Tests for the harness CLI (harness.cli). Commands drive the real Typer app against a temp git repo;
only the external toolchain (gate checks, package managers, the worker subprocess) is stubbed.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest
from click import unstyle
from typer.testing import CliRunner

from harness import cli, gate
from harness.gate import (
    AGENTS,
    FORBIDDEN_DIRS,
    FORBIDDEN_FILES,
    FORBIDDEN_PATTERNS,
    FULL_CHECKS,
)
from harness.tests.conftest import REPO_ROOT, fake_popen

if TYPE_CHECKING:
    from collections.abc import Callable

runner = CliRunner()


def stub_toolchain(
    real: Callable[..., subprocess.CompletedProcess[str]],
    calls: list[tuple[str, ...]],
    poetry_python: str = "",
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Record every launched command, running git for real and reporting a clean exit for the rest.

    `poetry_python` is what `poetry env info --executable` reports, the way the real Poetry does.
    """

    def fake(args: tuple[str, ...] | list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(args))
        if tuple(args)[:1] == ("git",):
            return real(args, **kwargs)
        reported = f"{poetry_python}\n" if tuple(args)[:2] == ("poetry", "env") else ""
        return subprocess.CompletedProcess(list(args), 0, reported)

    return fake


def fake_agent(captured: list[list[str]], code: int = 0) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Stand in for the worker: record the launched command and write one jsonl line to its stdout."""

    def fake(
        command: list[str], *, stdout: io.TextIOBase | None = None, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        captured.append(list(command))
        if stdout is not None:
            stdout.write('{"type":"result","result":"ok"}\n')
        return subprocess.CompletedProcess(list(command), code)

    return fake


def which_finds(*tools: str) -> Callable[[str], str | None]:
    """A shutil.which stand-in that finds only the named tools on PATH."""

    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in tools else None

    return which


def normalized_path(path: str | Path) -> str:
    """Normalize recorded executable paths for comparisons across operating systems."""
    return os.path.normcase(os.path.normpath(str(path)))


def harness_executable(env_bin: Path) -> Path:
    """Return the installed console-script path for the current platform."""
    return env_bin / ("harness.exe" if sys.platform == "win32" else "harness")


def freeze_run_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin cli.run's dated receipt dir to 20990102 so path assertions cannot race midnight."""

    def now(tz: object) -> datetime:
        del tz
        return datetime(2099, 1, 2, tzinfo=UTC)

    monkeypatch.setattr(cli, "datetime", SimpleNamespace(now=now))


def write_executable(path: Path, text: str) -> None:
    """Write an executable script for the end-to-end loop test."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def test_entry_point_propagates_exit_codes_and_rejects_unknown_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console script lets typer.Exit reach the shell; unknown or missing commands are usage errors."""
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])

    assert exit_info.value.code == 0
    assert runner.invoke(cli.app, ["bogus"]).exit_code == 2
    assert runner.invoke(cli.app, []).exit_code == 2

    fake_popen(monkeypatch, fails=[gate.COMMIT_CHECKS["lint"], gate.COMMIT_CHECKS["format"]])
    rejected = runner.invoke(cli.app, ["preflight"])
    summary = " ".join(unstyle(rejected.stdout).split())

    assert rejected.exit_code == 1
    assert "FAILED lint" in summary
    assert "WARNED format" in summary
    assert "rejected by harness" in summary


def test_help_and_info_surface_every_check_agent_and_containment_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nobody has to open pyproject.toml: info renders both phases with their argv, the containment
    lists and the agents, while help offers the human commands and hides the git-only plumbing.
    """
    monkeypatch.setattr(cli.console, "width", 40)
    info = runner.invoke(cli.app, ["info"])

    assert info.exit_code == 0
    flat = " ".join(unstyle(info.output).split())
    for phase in ("preflight", "gate"):
        assert phase in flat
    for name, command in FULL_CHECKS.items():
        assert name in flat
        assert command[0] in flat
    for pattern in FORBIDDEN_PATTERNS:
        assert pattern in flat
    for path in (*FORBIDDEN_DIRS, *FORBIDDEN_FILES):
        assert path in flat
    for agent in AGENTS:
        assert agent in flat

    run_help = runner.invoke(cli.app, ["run", "--help"])

    assert run_help.exit_code == 0
    for agent in AGENTS:
        assert agent in run_help.output
    assert "verbose" in run_help.output
    assert "--verbose" not in run_help.output
    assert "--no-verbose" not in run_help.output

    root_help = runner.invoke(cli.app, ["--help"])

    assert root_help.exit_code == 0
    assert "preflight" in root_help.output
    assert "prepare-commit-msg" not in root_help.output
    assert "--install-completion" not in root_help.output
    assert "--show-completion" not in root_help.output


def test_every_supported_agent_has_a_nonempty_command() -> None:
    """Every advertised agent has a usable argv preset rather than a missing or blank command."""
    agents: dict[str, list[str]] = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["harness"]["agents"]

    assert agents == AGENTS
    assert set(agents) == {"claude", "codex", "agy", "copilot"}
    assert all(isinstance(command, list) and bool(command) for command in agents.values())
    assert all(
        isinstance(argument, str) and bool(argument) for command in agents.values() for argument in command
    )


def test_preflight_summary_names_every_check_for_agents(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """The CLI renders the result produced by the real preflight containment path."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "COMMIT_CHECKS", {})
    source = git_repo / "src" / "mod.py"
    source.parent.mkdir()
    source.write_text("value = 1\n", encoding="utf-8")
    gate.run_git(["add", "src/mod.py"], git_repo)
    result = runner.invoke(cli.app, ["preflight"])
    output = " ".join(unstyle(result.stdout).split())

    assert (result.exit_code, output) == (
        0,
        (
            f"PHASE: DIFF SIZE 1 lines modified warn at {gate.WARN_DIFF_LINES} "
            f"block at {gate.ERROR_DIFF_LINES} Suggestion: Refactor bloat, inline helpers, "
            "reduce mis-direction, re-use fixtures, cut duplication. "
            "PHASE: BANNED PATTERNS CHECK checking for banned patterns in staged files "
            "PHASE: USER PREFERENCES checking that user's preferences are respected "
            "Harness Summary RESULT CHECK ok: preflight pass"
        ),
    )


def test_gate_summary_names_every_check_for_agents(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """The CLI rejects the result produced by the real gate containment path."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "FULL_CHECKS", {})
    source = git_repo / "src" / "mod.py"
    source.parent.mkdir()
    source.write_text("_bad = 1\n", encoding="utf-8")
    gate.run_git(["add", "src/mod.py"], git_repo)
    result = runner.invoke(cli.app, ["gate"])
    output = " ".join(unstyle(result.stdout).split())

    assert (result.exit_code, output) == (
        1,
        (
            f"PHASE: DIFF SIZE 1 lines modified warn at {gate.WARN_DIFF_LINES} "
            f"block at {gate.ERROR_DIFF_LINES} Suggestion: Refactor bloat, inline helpers, "
            "reduce mis-direction, re-use fixtures, cut duplication. "
            "PHASE: BANNED PATTERNS CHECK checking for banned patterns in staged files "
            "PHASE: USER PREFERENCES checking that user's preferences are respected "
            "Harness Summary RESULT CHECK FAILED problems: src/mod.py:1: Name '_bad' starts with underscore "
            "rejected by harness"
        ),
    )


def test_status_counts_run_receipts_and_names_the_newest(git_repo: Path) -> None:
    """Status reports zero without crashing, then counts the receipts and points at the last one."""
    empty = runner.invoke(cli.app, ["status"])

    assert empty.exit_code == 0
    assert "0 run log(s)" in empty.stdout

    runs = git_repo / "scratchpad" / "runs"
    runs.mkdir(parents=True)
    (runs / "0001-claude.jsonl").write_text("{}\n", encoding="utf-8")
    (runs / "0002-codex.jsonl").write_text("{}\n", encoding="utf-8")

    counted = runner.invoke(cli.app, ["status"])

    assert counted.exit_code == 0
    assert "2 run log(s)" in counted.stdout
    assert "newest: " in counted.stdout
    assert "0002-codex.jsonl" in counted.stdout


def test_installing_the_template_cleans_the_repo_sets_hooks_and_reruns_cleanly(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Install turns a freshly cloned template into the user's own project: it names the project, starts
    it at v0, scopes the project checks away from the embedded harness, deletes what the template
    shipped for itself, syncs dependencies and activates the git hooks. Running it again is harmless.
    """
    (git_repo / "pyproject.toml").write_text(
        "[project]\n"
        'name = "old-name"\n'
        'version = "2.3.4"\n'
        'description = "the user\'s own project"\n'
        'authors = [{ name = "someone" }]\n'
        'requires-python = ">=3.11"\n'
        "\n[project.scripts]\n"
        'harness = "harness.cli:main"\n'
        "\n[tool.pyright]\n"
        'typeCheckingMode = "strict"\n'
        'include = ["src", "harness"]\n'
        "\n[tool.pytest.ini_options]\n"
        'addopts = ["-ra"]\n'
        'testpaths = ["tests", "harness"]\n'
        'pythonpath = ["src", "harness"]\n'
        "\n[tool.coverage]\n"
        'run.source = ["src", "harness"]\n'
        "report.fail_under = 100\n"
        "\n[tool.complexipy]\n"
        'paths = ["src", "harness"]\n'
        "max-complexity-allowed = 10\n"
        "\n[tool.ruff]\n"
        'exclude = [".git"]\n'
        "\n[tool.pylint.main]\n"
        'ignore = [".git"]\n',
        encoding="utf-8",
    )
    (git_repo / "uv.lock").touch()
    template_files = (
        ".banner.svg",
        ".diagram.png",
        ".infin.png",
        ".loops_agents.svg",
        ".loops.svg",
    )
    for file_name in template_files:
        (git_repo / file_name).touch()
    (git_repo / ".github" / "workflows").mkdir(parents=True)
    (git_repo / ".github" / "workflows" / "publish.yml").touch()
    (git_repo / "CONTRIBUTING.md").touch()
    (git_repo / "dist").mkdir()
    (git_repo / "dist" / "stale.whl").touch()
    for directory in ("harness/tests", "preferences", "tests/preferences"):
        (git_repo / directory).mkdir(parents=True)
    monkeypatch.setattr(cli.shutil, "which", which_finds("timeout"))
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(git_repo))
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(subprocess, "run", stub_toolchain(subprocess.run, calls))

    result = runner.invoke(cli.app, ["install", "fresh-project"])

    assert result.exit_code == 0
    with (git_repo / "pyproject.toml").open("rb") as handle:
        document = tomllib.load(handle)
    assert document["project"] == {
        "name": "fresh-project",
        "version": "0.0.0",
        "description": "the user's own project",
        "authors": [{"name": "someone"}],
        "requires-python": ">=3.11",
        "scripts": {"harness": "harness.cli:main"},
    }
    assert document["tool"]["pyright"] == {
        "typeCheckingMode": "strict",
        "include": ["src", "preferences"],
    }
    assert document["tool"]["pytest"]["ini_options"] == {
        "addopts": ["-ra"],
        "testpaths": ["tests"],
        "pythonpath": ["src"],
    }
    assert document["tool"]["coverage"] == {
        "run": {"source": ["src", "preferences"]},
        "report": {"fail_under": 100},
    }
    assert document["tool"]["complexipy"] == {
        "paths": ["src", "preferences"],
        "max-complexity-allowed": 10,
    }
    assert document["tool"]["ruff"]["exclude"] == [".git", "harness"]
    assert document["tool"]["pylint"]["main"]["ignore"] == [".git", "harness"]
    assert (git_repo / "README.md").read_text(encoding="utf-8") == "seed\n"
    assert not (git_repo / "README.template.md").exists()
    assert all(not (git_repo / name).exists() for name in template_files)
    assert not (git_repo / ".github" / "workflows" / "publish.yml").exists()
    assert not (git_repo / "CONTRIBUTING.md").exists()
    assert not (git_repo / "dist").exists()
    assert not (git_repo / "harness" / "tests").exists()
    assert (git_repo / "preferences").is_dir()
    assert (git_repo / "tests" / "preferences").is_dir()
    assert ("uv", "sync") in calls
    recorded_harness = (git_repo / ".git" / "harness-path").read_text(encoding="utf-8").strip()
    env_bin = git_repo / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    assert normalized_path(recorded_harness) == normalized_path(harness_executable(env_bin))
    assert gate.run_git(["config", "core.hooksPath"], git_repo).strip() == ".githooks"

    again = runner.invoke(cli.app, ["install"])

    assert again.exit_code == 0
    assert (git_repo / "README.md").read_text(encoding="utf-8") == "seed\n"


@pytest.mark.parametrize(
    ("lockfile", "manager"),
    [
        pytest.param("uv.lock", "uv", id="uv-lockfile"),
        pytest.param("poetry.lock", "poetry", id="poetry-lockfile"),
        pytest.param(None, "pip", id="no-lockfile"),
    ],
)
def test_install_picks_the_package_manager_from_the_lockfile(
    lockfile: str | None, manager: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """The lockfile picks the package manager, and the hooks record the harness of the environment
    that manager filled, which is not the interpreter running install unless pip did the work.
    """
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    python_name = "python.exe" if sys.platform == "win32" else "python"
    interpreter = git_repo / ".pyenv" / scripts
    poetry_bin = git_repo / ".poetry" / "virtualenvs" / "project" / scripts
    monkeypatch.setattr(cli.sys, "executable", str(interpreter / python_name))
    monkeypatch.setattr(cli.shutil, "which", which_finds("timeout"))
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(git_repo))
    (git_repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    if lockfile:
        (git_repo / lockfile).touch()
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        stub_toolchain(subprocess.run, calls, str(poetry_bin / python_name)),
    )

    assert runner.invoke(cli.app, ["install"]).exit_code == 0

    managers = {
        "uv": ("uv", "sync"),
        "poetry": ("poetry", "install"),
        "pip": (
            str(interpreter / python_name),
            "-m",
            "pip",
            "install",
            "-r",
            "requirements.txt",
            "-e",
            ".",
        ),
    }
    recorded = {
        "uv": harness_executable(git_repo / ".venv" / scripts),
        "poetry": harness_executable(poetry_bin),
        "pip": harness_executable(interpreter),
    }
    assert [call for call in calls if call in managers.values()] == [managers[manager]]
    installed = (git_repo / ".git" / "harness-path").read_text(encoding="utf-8").strip()
    assert normalized_path(installed) == normalized_path(recorded[manager])


@pytest.mark.parametrize(
    ("on_path", "answer", "outcome"),
    [
        pytest.param(("timeout",), None, (False, ""), id="timeout-present"),
        pytest.param(("gtimeout",), None, (False, ""), id="gtimeout-present"),
        pytest.param((), None, (False, "brew.sh"), id="no-timeout-no-homebrew"),
        pytest.param(("brew",), True, (True, ""), id="confirmed"),
        pytest.param(("brew",), False, (False, "skipped"), id="declined"),
    ],
)
def test_install_offers_coreutils_only_when_no_timeout_tool_exists(
    on_path: tuple[str, ...],
    answer: bool | None,
    outcome: tuple[bool, str],
    monkeypatch: pytest.MonkeyPatch,
    git_repo: Path,
) -> None:
    """macOS needs coreutils to time out a loop iteration, so install probes for it and offers the
    install only when Homebrew can do it. It never prompts when a timeout tool is already there.
    """
    installs_coreutils, hint = outcome
    prompts: list[str] = []

    def confirm(prompt: str) -> bool:
        prompts.append(prompt)
        return bool(answer)

    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli.shutil, "which", which_finds(*on_path))
    monkeypatch.setattr(cli.typer, "confirm", confirm)
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(git_repo))
    (git_repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(subprocess, "run", stub_toolchain(subprocess.run, calls))

    result = runner.invoke(cli.app, ["install"])

    assert result.exit_code == 0
    assert (("brew", "install", "coreutils") in calls) is installs_coreutils
    assert prompts == ([] if answer is None else ["[magenta]Install now `brew install coreutils`?[/magenta]"])
    assert hint in result.stdout


def test_windows_skips_posix_steps_and_launches_the_powershell_twin(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Windows has no POSIX shell, ls or coreutils, so install records harness.exe and warns that the
    support is experimental, and a run goes through PowerShell instead of ralph.sh.
    """
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    monkeypatch.setattr(cli.shutil, "which", which_finds("uv"))
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(git_repo))
    (git_repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (git_repo / "uv.lock").touch()
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(subprocess, "run", stub_toolchain(subprocess.run, calls))

    installed = runner.invoke(cli.app, ["install"])

    assert installed.exit_code == 0
    assert ("ls", "-l", ".githooks") not in calls
    assert ("brew", "install", "coreutils") not in calls
    assert "source .venv/bin/activate" not in installed.stdout
    assert "Windows is experimental. Reoprt issues" in installed.stdout
    assert "https://github.com/rxdt/loopgate_harness/issues" in installed.stdout
    assert (git_repo / ".git" / "harness-path").read_text(encoding="utf-8") == (
        f"{(git_repo / '.venv' / 'Scripts' / 'harness.exe').as_posix()}\n"
    )

    launched: list[list[str]] = []

    def capture_worker(command: list[str], log: Path, verbose: bool) -> int:
        del log, verbose
        launched.append(command)
        return 0

    monkeypatch.setattr(cli, "run_worker", capture_worker)
    (git_repo / "docs").mkdir()
    (git_repo / "docs" / "PROMPT.md").write_text("do the most important thing\n", encoding="utf-8")

    assert runner.invoke(cli.app, ["run", "claude", "2", "5"]).exit_code == 0
    assert launched[0][:3] == ["powershell.exe", "-NoProfile", "-File"]
    assert launched[0][3].endswith("ralph.ps1")
    assert launched[0][4:6] == ["2", "5"]
    assert launched[0][6:] == list(AGENTS["claude"])


def test_windows_run_uses_powershell_without_path_lookup(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Windows relies on its system PowerShell command without resolving a separate executable."""
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli.sys, "platform", "win32")
    path_lookup = Mock(return_value=None)
    worker = Mock(return_value=0)
    monkeypatch.setattr(cli.shutil, "which", path_lookup)
    monkeypatch.setattr(cli, "run_worker", worker)
    (git_repo / "docs").mkdir()
    (git_repo / "docs" / "PROMPT.md").write_text("do the most important thing\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["run", "claude"])

    assert result.exit_code == 0
    path_lookup.assert_not_called()
    worker.assert_called_once()
    command, log, verbose = worker.call_args.args
    assert command[:3] == ["powershell.exe", "-NoProfile", "-File"]
    assert log.parent.is_dir()
    assert verbose is True


@pytest.mark.parametrize(
    ("initial_name", "requested_name", "expected_name"),
    [
        pytest.param("old-name", "fresh-project", "fresh-project", id="normalized-explicit-name"),
        pytest.param("old-name", "I_build.Things!", "my-app-name", id="non-normalized-name"),
        pytest.param("old-name", '*bad"-name_!/ ', "my-app-name", id="invalid-name"),
        pytest.param("old-name", None, "my-app-name", id="omitted-name"),
        pytest.param(None, None, "my-app-name", id="omitted-name-with-nameless-project"),
    ],
)
def test_cleanup_applies_the_project_name_rules(
    tmp_path: Path,
    initial_name: str | None,
    requested_name: str | None,
    expected_name: str,
) -> None:
    """Cleanup starts the project at v0 and only accepts a name that is already PEP 503 normalized."""
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    (tmp_path / "README.template.md").write_text("seed\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("existing\n\nuv.lock\n", encoding="utf-8")
    project_toml = "[project]\n"
    if initial_name is not None:
        project_toml += f'name = "{initial_name}"\n'
    (tmp_path / "pyproject.toml").write_text(project_toml, encoding="utf-8")

    assert cli.cleanup(tmp_path, requested_name) is True

    with (tmp_path / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["name"] == expected_name
    assert project["version"] == "0.0.0"
    assert not (tmp_path / "README.template.md").exists()
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == "existing\n"


@pytest.mark.parametrize("hook", ["pre-commit", "pre-push", "prepare-commit-msg"])
def test_tracked_hooks_call_registered_commands_without_venv_paths(hook: str) -> None:
    """Each hook invokes a harness command that exists and assumes no POSIX virtualenv layout."""
    text = (REPO_ROOT / ".githooks" / hook).read_text(encoding="utf-8")
    called = [
        words[index + 1]
        for words in (line.split() for line in text.splitlines())
        for index, word in enumerate(words)
        if word == '"$HARNESS"' and index + 1 < len(words)
    ]

    assert called, f"{hook} does not invoke harness"
    for command in called:
        assert runner.invoke(cli.app, [command, "--help"]).exit_code == 0
    assert ".venv/bin/harness" not in text
    assert ".venv/bin/python" not in text
    assert "uv" not in text


@pytest.mark.parametrize(
    ("recorded", "message"),
    [
        pytest.param(
            None,
            "hooks are not installed. Run 'harness install' in this repo.",
            id="never-ran",
        ),
        pytest.param("missing-harness", "is gone. Re-run 'harness install'.", id="stale-record"),
    ],
)
def test_hooks_name_the_fix_when_the_recorded_harness_is_missing_or_stale(
    recorded: str | None, message: str, git_repo: Path
) -> None:
    """A repo without the install record, or one whose environment was rebuilt, gets instructions."""
    shutil.copytree(REPO_ROOT / ".githooks", git_repo / ".githooks", dirs_exist_ok=True)
    if recorded:
        (git_repo / ".git" / "harness-path").write_text(f"{git_repo / recorded}\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    gate.run_git(["config", "core.hooksPath", ".githooks"], git_repo)

    result = subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "exercise pre-commit"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 1
    assert message in result.stderr


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        pytest.param([".git/COMMIT_EDITMSG", "merge"], 1, id="blocked-merge"),
        pytest.param([], 0, id="no-arguments"),
    ],
)
def test_prepare_commit_msg_forwards_gits_own_arguments(
    arguments: list[str], code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook command hands git's own arguments to the gate logic and exits with its status."""
    seen: list[list[str]] = []

    def commit_msg(argv: list[str]) -> int:
        seen.append(argv)
        return code

    monkeypatch.setattr(cli, "commit_msg", commit_msg)

    result = runner.invoke(cli.app, ["prepare-commit-msg", *arguments])

    assert result.exit_code == code
    assert seen == [["prepare-commit-msg", *arguments]]


def test_a_harnessed_run_writes_numbered_receipts_and_propagates_exit_codes(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A day of runs replayed: bad arguments are refused before anything is created, each accepted run
    launches ralph.sh with the agent's preset and lands its own numbered receipt beside the earlier
    ones, an overridden model replaces the preset's, and the worker's exit code reaches the shell.
    """
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli.sys, "platform", "linux")
    monkeypatch.setenv("RALPH_PROMPT", "")
    freeze_run_day(monkeypatch)
    (git_repo / "docs").mkdir()
    (git_repo / "docs" / "PROMPT.md").write_text("do the most important thing\n", encoding="utf-8")
    launched: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", fake_agent(launched))

    unknown = runner.invoke(cli.app, ["run", "bogus"])

    assert unknown.exit_code == 2
    assert unknown.stderr.strip() == "Unknown agent name 'bogus'"
    for limits in (["0", "1"], ["1", "0"]):
        refused = runner.invoke(cli.app, ["run", "claude", *limits])
        assert refused.exit_code == 2
        assert "num_iterations and max_minutes must be >= 1" in refused.stderr
    assert not (git_repo / "scratchpad").exists()
    assert launched == []

    first = runner.invoke(cli.app, ["run", "claude", "1", "2", "False"])

    assert first.exit_code == 0
    assert not first.stdout
    assert launched[0][0].endswith("ralph.sh")
    assert launched[0][1:3] == ["1", "2"]
    assert launched[0][3:] == list(AGENTS["claude"])
    receipts = git_repo / "scratchpad" / "runs" / "20990102" / "claude"
    assert (receipts / "0001.jsonl").read_text(encoding="utf-8") == '{"type":"result","result":"ok"}\n'
    assert os.environ["RALPH_PROMPT"] == "Your agent id is `0001`\n\ndo the most important thing"

    second = runner.invoke(cli.app, ["run", "claude", "1", "2", "False", "--model", "haiku"])

    assert second.exit_code == 0
    swapped = list(AGENTS["claude"])
    swapped[swapped.index("--model") + 1] = "haiku"
    assert launched[1][3:] == swapped
    assert launched[1].count("--model") == 1
    assert (receipts / "0002.jsonl").exists()

    (receipts / "0007.jsonl").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", fake_agent(launched, 124))

    timed_out = runner.invoke(cli.app, ["run", "claude", "2", "20", "False"])

    assert timed_out.exit_code == 124
    assert (receipts / "0008.jsonl").exists()
    assert (receipts / "0007.jsonl").read_text(encoding="utf-8") == "{}\n"


def test_run_worker_logs_every_line_and_streams_only_when_verbose(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The receipt always gets the worker's raw stdout; verbose also renders each line live, JSON or
    not, without crashing on a line that is not JSON. Terminal coloring is cosmetic and not asserted.
    """
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(tmp_path))
    log = tmp_path / "out.jsonl"
    streaming_worker = [
        sys.executable,
        "-c",
        'print(\'{ "type" : "result" }\'); print("not json")',
    ]

    assert cli.run_worker(streaming_worker, log, verbose=True) == 0

    streamed = capsys.readouterr().out
    assert '"type"' in streamed
    assert '"result"' in streamed
    assert "not json" in streamed
    assert log.read_text(encoding="utf-8") == '{ "type" : "result" }\nnot json\n'

    failing_worker = [
        sys.executable,
        "-c",
        'print("worker output"); raise SystemExit(3)',
    ]

    assert cli.run_worker(failing_worker, log, verbose=False) == 3

    assert not capsys.readouterr().out
    assert log.read_text(encoding="utf-8") == "worker output\n"


def test_claude_preset_runs_two_real_loop_iterations(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """The platform runner loops twice and preserves Claude's trailing -p argument."""
    bin_dir = git_repo / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "gtimeout", '#!/bin/sh\nshift\nexec "$@"\n')
    worker = git_repo / "claude_worker.py"
    worker.write_text(
        "from pathlib import Path\n"
        "import json\n"
        "import sys\n"
        "count_path = Path('claude-count')\n"
        "count = int(count_path.read_text() if count_path.exists() else '0') + 1\n"
        "count_path.write_text(str(count))\n"
        "with Path('claude-args.txt').open('a', encoding='utf-8') as handle:\n"
        "    handle.write('\\n'.join(sys.argv[1:]) + '\\n')\n"
        "Path(f'prompt-{count}.txt').write_text(sys.stdin.read(), encoding='utf-8')\n"
        "print(json.dumps({'type': 'result', 'result': 'ok'}))\n",
        encoding="utf-8",
    )
    preset = [sys.executable, str(worker), "--model", "opus", "-p"]
    monkeypatch.setitem(cli.AGENTS, "claude", preset)
    monkeypatch.chdir(git_repo)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(git_repo))
    freeze_run_day(monkeypatch)
    (git_repo / "docs").mkdir()
    (git_repo / "docs" / "PROMPT.md").write_text("build from specs\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["run", "claude", "2", "1"])

    assert result.exit_code == 0
    assert (git_repo / "claude-count").read_text(encoding="utf-8") == "2"
    identity = "Your agent id is `0001`\n\n"
    assert (git_repo / "prompt-1.txt").read_text(encoding="utf-8") == (
        f"{identity}build from specs\n\nRALPH_ITERATION=1/2\n"
    )
    assert (git_repo / "prompt-2.txt").read_text(encoding="utf-8") == (
        f"{identity}build from specs\n\nRALPH_ITERATION=2/2\n"
    )
    preset_args = preset[2:]
    assert (git_repo / "claude-args.txt").read_text(encoding="utf-8").splitlines() == [
        *preset_args,
        *preset_args,
    ]
    assert (git_repo / "scratchpad" / "runs" / "20990102" / "claude" / "0001.jsonl").read_text(
        encoding="utf-8"
    ) == '{"type": "result", "result": "ok"}\n{"type": "result", "result": "ok"}\n'
