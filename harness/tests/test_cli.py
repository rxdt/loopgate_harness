"""Tests for the harness CLI (harness.cli). Commands drive the real Typer app against a temp git repo;
only the external toolchain (gate checks, package managers, the worker subprocess) is stubbed.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import DEFAULT, Mock, call, create_autospec
from zipfile import ZipFile

import pytest
import tomlkit as tomllib
from click import unstyle
from typer import Abort
from typer.testing import CliRunner

from harness import cli, config, gate
from harness.gate import Gate, gates
from harness.tests.conftest import REPO_ROOT, fake_home, fake_popen

if TYPE_CHECKING:
    from collections.abc import Callable

runner = CliRunner()
INIT_PROJECT_COMMENT = "# keep this project comment"
INSTALLED_CONFIG_PROBE = """
import json
from importlib.metadata import distribution
from pathlib import Path

from harness import config

print(json.dumps({
    "distribution_root": str(Path(distribution("loopgate").locate_file("")).resolve()),
    "site_packages": str(config.site_packages.resolve()),
    "package_root": str(config.package_root.resolve()),
    "repo_root": str(config.repo_root.resolve()),
    "assets": {
        name: [str(path.resolve()) for path in paths]
        for name, paths in config.ASSETS.items()
    },
}))
"""


def run_checked(command: list[str]) -> None:
    """Run an integration-test setup command and expose its complete failure output."""
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stdout + completed.stderr


def install_local_wheel(test_root: Path) -> Path:
    """Build the current wheel and install it with dependencies in an isolated environment."""
    wheel_dir = test_root / "dist"
    environment_dir = test_root / ".venv"
    environment_bin = environment_dir / ("Scripts" if sys.platform == "win32" else "bin")
    environment_python = environment_bin / ("python.exe" if sys.platform == "win32" else "python")

    run_checked(["uv", "build", "--wheel", "--out-dir", str(wheel_dir), str(REPO_ROOT)])
    wheels = list(wheel_dir.glob("loopgate-*.whl"))
    assert len(wheels) == 1, wheels
    with ZipFile(wheels[0]) as wheel:
        cache_members = [name for name in wheel.namelist() if "__pycache__" in name or name.endswith(".pyc")]
    assert not cache_members, cache_members
    run_checked(["uv", "venv", "--no-project", "--python", sys.executable, str(environment_dir)])
    run_checked(["uv", "pip", "install", "--python", str(environment_python), str(wheels[0])])
    return environment_python


def isolated_environment(environment_python: Path, home: Path) -> dict[str, str]:
    """Give an installed command its own environment and home without source-checkout imports."""
    environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}
    }
    environment["PATH"] = f"{environment_python.parent}{os.pathsep}{environment.get('PATH', '')}"
    environment["VIRTUAL_ENV"] = str(environment_python.parents[1])
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    return environment


def assert_installed_config_paths(foreign_repo: Path) -> Path:
    """A wheel installed into an isolated environment resolves assets into the active repository."""
    environment_python = install_local_wheel(foreign_repo.parent / f"{foreign_repo.name}-package")
    probe_cwd = foreign_repo / "nested"
    probe_cwd.mkdir()
    probe = subprocess.run(
        [environment_python, "-c", INSTALLED_CONFIG_PROBE],
        cwd=probe_cwd,
        capture_output=True,
        text=True,
        env=isolated_environment(environment_python, foreign_repo.parent / "probe-home"),
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    observed = json.loads(probe.stdout)
    installed_root = Path(observed["distribution_root"])
    repo_root = foreign_repo.resolve()
    assets: dict[str, tuple[Path, Path]] = {
        name: (Path(paths[0]), Path(paths[1])) for name, paths in observed["assets"].items()
    }
    expected_assets = {
        name: (
            installed_root / source.relative_to(config.site_packages),
            repo_root / destination.relative_to(config.repo_root),
        )
        for name, (source, destination) in config.ASSETS.items()
    }
    assert {
        "site_packages": Path(observed["site_packages"]),
        "package_root": Path(observed["package_root"]),
        "repo_root": Path(observed["repo_root"]),
        "assets": assets,
    } == {
        "site_packages": installed_root,
        "package_root": installed_root / "harness",
        "repo_root": repo_root,
        "assets": expected_assets,
    }
    assert all(source.is_file() or source.is_dir() for source, _ in assets.values())
    return environment_python


CONFIG_SOURCE_MATRIX = (
    {"standalone": False, "pyproject": False, "other": False, "detected": False},
    {"standalone": False, "pyproject": False, "other": True, "detected": True},
    {"standalone": False, "pyproject": True, "other": False, "detected": True},
    {"standalone": False, "pyproject": True, "other": True, "detected": True},
    {"standalone": True, "pyproject": False, "other": False, "detected": True},
    {"standalone": True, "pyproject": False, "other": True, "detected": True},
    {"standalone": True, "pyproject": True, "other": False, "detected": True},
    {"standalone": True, "pyproject": True, "other": True, "detected": True},
)


def stub_toolchain(git_dir: Path, poetry_python: str = "") -> Mock:
    """Report successful tool commands and Poetry's selected interpreter.

    `poetry_python` is what `poetry env info --executable` reports, the way the real Poetry does.
    """
    reports = [f"{git_dir}\n"] * 20
    if poetry_python:
        reports[1] = f"{poetry_python}\n"
    results = [subprocess.CompletedProcess([], 0, report) for report in reports]
    return Mock(side_effect=results)


def fake_agent(captured: list[list[str]], code: int = 0) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Stand in for the worker: record the launched command and write one jsonl line to its stdout."""

    def fake(
        command: list[str],
        cwd: str | Path | None = None,
        stdout: io.TextIOBase | None = None,
        text: bool = False,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, text, check
        captured.append(list(command))
        if stdout is not None:
            stdout.write('{"type":"result","result":"ok"}\n')
        return subprocess.CompletedProcess(list(command), code)

    return fake


def which_finds(tools: tuple[str, ...]) -> Callable[[str], str | None]:
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
        return datetime(2099, 1, 2, tzinfo=timezone.utc)

    monkeypatch.setattr(cli, "datetime", SimpleNamespace(now=now))


def write_executable(path: Path, text: str) -> None:
    """Write an executable script for the end-to-end loop test."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def test_entry_point_propagates_exit_codes_and_rejects_unknown_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """The console script lets typer.Exit reach the shell; unknown or missing commands are usage errors."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])

    assert exit_info.value.code == 0
    assert runner.invoke(cli.app, ["bogus"]).exit_code == 2
    assert runner.invoke(cli.app, []).exit_code == 2

    fake_popen(monkeypatch, fails=[gates().commit_checks["ruff_lint"], gates().commit_checks["ruff_format"]])
    rejected = runner.invoke(cli.app, ["preflight"])
    summary = " ".join(unstyle(rejected.stdout).split())

    assert rejected.exit_code == 1
    assert "FAILED ruff_lint" in summary
    assert "WARNED ruff_format" in summary
    assert "rejected by harness" in summary


def test_help_and_info_surface_every_check_agent_and_containment_rule() -> None:
    """Nobody has to open pyproject.toml: info renders both phases with their argv, the containment
    lists and the agents, while help offers the human commands and hides the git-only plumbing.
    """
    info = runner.invoke(cli.app, ["info"])

    assert info.exit_code == 0
    flat = " ".join(unstyle(info.output).replace("│", " ").split())
    for phase in ("preflight", "gate"):
        assert phase in flat
    for name, command in gates().gate_checks.items():
        assert name in flat
        assert command[0] in flat
    for pattern in gates().forbidden_patterns:
        assert pattern in flat
    for path in (*gates().forbidden_dirs, *gates().forbidden_files):
        assert path in flat
    for agent in gates().agents:
        assert agent in flat

    run_help = runner.invoke(cli.app, ["run", "--help"])

    assert run_help.exit_code == 0
    for agent in gates().agents:
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
    agents: dict[str, list[str]] = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "harness"
    ]["agents"]

    assert agents == gates().agents
    assert set(agents) == {"claude", "codex", "agy", "copilot"}
    assert all(isinstance(command, list) and bool(command) for command in agents.values())
    assert all(isinstance(argument, str) and bool(argument) for command in agents.values() for argument in command)


@pytest.mark.parametrize(
    ("command", "source", "expected"),
    [
        pytest.param(
            "preflight",
            "value = 1\n",
            (0, "Harness Summary RESULT CHECK PASSED mutmut ok: preflight pass"),
            id="preflight-passes",
        ),
        pytest.param(
            "gate",
            "_bad = 1\n",
            (
                1,
                (
                    "Harness Summary RESULT CHECK PASSED mutmut FAILED "
                    "PREFERENCES IGNORED: src/mod.py:1: Name '_bad' starts with underscore "
                    "and is not in a class rejected by harness"
                ),
            ),
            id="gate-rejects",
        ),
    ],
)
def test_cli_summaries_report_complete_agent_check_results(
    command: str, source: str, expected: tuple[int, str], monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Preflight and gate render every containment phase and preserve the final verdict exactly."""
    exit_code, summary = expected
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gates(), "commit_checks" if command == "preflight" else "gate_checks", {})
    monkeypatch.setattr(cli.console, "print", Mock(wraps=cli.console.print))
    source_path = git_repo / "src" / "mod.py"
    source_path.parent.mkdir()
    source_path.write_text(source, encoding="utf-8")
    gate.run_git(["add", "src/mod.py"], git_repo)
    result = runner.invoke(cli.app, [command])
    plain_output = unstyle(result.stdout)
    output = " ".join(plain_output.split())

    phase_output = (
        "PHASE: AGENT CHECKS\nrunning non-human agent checks\n"
        "PHASE: BANNED PATTERNS FOR AGENT\ncheck for banned patterns in staged files\nIssues:\nset()\n"
        "PHASE: REPO PREFERENCES\nchecking repo preferences are respected by agents\nIssues:\n"
    )
    diff_size_output = (
        "PHASE: DIFF SIZE 1 lines of code modified (insertions + deletions in staged files). "
        "Agents get WARN at 75% 375, ERROR at 500. "
    )
    mutation_output = (
        "killed 132 survived 0 total 133 no_tests 0 skipped 0 suspicious 0 timeout 1 "
        "check_was_interrupted_by_user 0 segfault 0 Mutation Score: 100.0"
    )
    assert result.exit_code == exit_code
    assert plain_output.startswith(phase_output)
    assert diff_size_output in output
    assert mutation_output in output
    assert output.endswith(unstyle(summary))
    assert (
        cli.console.print.call_args_list[-2].args[0].title_style,
        cli.console.print.call_args_list[-2].args[0].box,
        cli.console.print.call_args_list[-2].args[0].padding,
        [(column.header, column.style) for column in cli.console.print.call_args_list[-2].args[0].columns],
    ) == ("bold grey82", None, (0, 5, 0, 5), [("RESULT", ""), ("CHECK", "bold dim white")])
    assert [printed.kwargs for printed in cli.console.print.call_args_list[-2:]] == [
        {"justify": "center"},
        {"justify": "center"},
    ]


def test_status_counts_run_receipts_and_names_the_newest(git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The installed and importable status commands count receipts and name the newest three."""
    runs = git_repo / "scratchpad" / "runs"
    command = [str(Path(sys.executable).with_name("harness.exe" if cli.IS_WINDOWS else "harness")), "status"]
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    empty = subprocess.run(command, cwd=git_repo, capture_output=True, text=True, env=environment, check=False)

    assert empty.returncode == 0, empty.stderr
    assert empty.stdout == f"0 run log(s) in {runs}\nnewest:\n\n"

    runs.mkdir(parents=True)
    (runs / "0001-claude.jsonl").write_text("{}\n", encoding="utf-8")
    (runs / "0002-codex.jsonl").write_text("{}\n", encoding="utf-8")
    (runs / "0003-claude.jsonl").write_text("{}\n", encoding="utf-8")
    (runs / "0004-codex.jsonl").write_text("{}\n", encoding="utf-8")

    counted = subprocess.run(command, cwd=git_repo, capture_output=True, text=True, env=environment, check=False)

    assert counted.returncode == 0, counted.stderr
    lines = counted.stdout.splitlines()
    assert lines[0] == f"4 run log(s) in {runs}"
    assert lines[1:] == [
        "newest:",
        str(runs / "0004-codex.jsonl"),
        str(runs / "0003-claude.jsonl"),
        str(runs / "0002-codex.jsonl"),
    ]
    monkeypatch.setattr(cli, "REPO_ROOT", git_repo)
    imported = runner.invoke(cli.app, ["status"])
    assert imported.exit_code == 0, imported.output
    assert imported.stdout == counted.stdout


def test_setup_git_hooks_records_exact_posix_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hook setup records the selected environment and configures the tracked hook directory."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    env_bin = tmp_path / ".venv" / "bin"
    run_git = Mock(return_value=f"{git_dir}\n")
    run = Mock()
    print_message = Mock()
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(tmp_path))
    monkeypatch.setattr(cli, "run_git", run_git)
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli, "rprint", print_message)

    write_text = create_autospec(Path.write_text, wraps=Path.write_text)
    monkeypatch.setattr(Path, "write_text", write_text)
    recorded = cli.setup_git_hooks(env_bin)

    assert recorded == git_dir / "harness-path"
    assert recorded.read_text(encoding="utf-8") == f"{(env_bin / 'harness').as_posix()}\n"
    write_text.assert_called_once_with(
        recorded, f"{(env_bin / 'harness').as_posix()}\n", encoding="utf-8", newline="\n"
    )
    run_git.assert_called_once_with(["rev-parse", "--git-common-dir"])
    assert run.call_args_list == [
        call(["git", "config", "core.hooksPath", ".githooks"], cwd=str(tmp_path), check=True),
        call(("ls", "-l", ".githooks"), cwd=str(tmp_path), check=True),
    ]
    assert print_message.call_args_list == [
        call("\n[cyan2]setting git hooks[/cyan2] `git config core.hooksPath .githooks`"),
        call(f"\nRecorded in {recorded} is the path to executable {env_bin}"),
    ]


def test_setup_git_hooks_reports_windows_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Windows hook setup reports its experimental status without invoking the POSIX listing."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    env_bin = tmp_path / ".venv" / "Scripts"
    run = Mock()
    print_message = Mock()
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(tmp_path))
    monkeypatch.setattr(cli, "run_git", Mock(return_value=f"{git_dir}\n"))
    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli, "rprint", print_message)

    recorded = cli.setup_git_hooks(env_bin)

    run.assert_called_once_with(["git", "config", "core.hooksPath", ".githooks"], cwd=str(tmp_path), check=True)
    assert print_message.call_args_list == [
        call("\n[cyan2]setting git hooks[/cyan2] `git config core.hooksPath .githooks`"),
        call("Windows is experimental. Reoprt issues https://github.com/rxdt/loopgate_harness/issues"),
        call(f"\nRecorded in {recorded} is the path to executable {env_bin}"),
    ]


def test_configure_agents_updates_configs_and_creates_backups(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Agent config is additive, rules are refreshed, and original files are backed up."""
    home = tmp_path / "home"
    claude_path = home / ".claude" / "settings.json"
    codex_path = home / ".codex" / "config.toml"
    rules_path = home / ".codex" / "rules" / "loopgate.rules"
    default_rules_path = rules_path.with_name("default.rules")
    claude_path.parent.mkdir(parents=True)
    rules_path.parent.mkdir(parents=True)

    claude_original = (
        json.dumps(
            {
                "theme": "dark",
                "env": {"KEEP": "yes"},
                "permissions": {"allow": ["Read(*)"], "deny": ["Bash(existing)"]},
            },
            indent=2,
        )
        + "\n"
    )
    codex_original = 'model = "existing"\n\n[shell_environment_policy]\ninherit = "core"\nset = { KEEP = "yes" }\n'
    claude_path.write_text(claude_original, encoding="utf-8")
    codex_path.write_text(codex_original, encoding="utf-8")
    rules_path.write_text("stale loopgate rules\n", encoding="utf-8")
    default_rules_path.write_text("user rule\n", encoding="utf-8")
    monkeypatch.setattr(cli.Path, "home", fake_home(home))

    with runner.isolation(input="y\n" * 2):
        cli.configure_agents()

    assert claude_path.with_name("settings.json.bak").read_text(encoding="utf-8") == claude_original
    assert codex_path.with_name("config.toml.bak").read_text(encoding="utf-8") == codex_original
    claude = json.loads(claude_path.read_text(encoding="utf-8"))
    assert claude["theme"] == "dark"
    assert claude["env"] == {"KEEP": "yes", "RALPH_LOOP": "1"}
    assert set(claude["permissions"]["deny"]) == {"Bash(existing)"} | cli.CLAUDE_RULES
    codex = tomllib.parse(codex_path.read_text(encoding="utf-8"))
    assert codex["model"] == "existing"
    assert codex["shell_environment_policy"]["inherit"] == "core"
    assert codex["shell_environment_policy"]["set"] == {"KEEP": "yes", "RALPH_LOOP": "1"}
    assert rules_path.read_text(encoding="utf-8") == f"{cli.CODEX_RULES.strip()}\n"
    assert default_rules_path.read_text(encoding="utf-8") == "user rule\n"


def test_configure_agents_creates_missing_configs_without_backups(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fresh home receives both configs and the owned rules file without empty backup files."""
    home = tmp_path / "home"
    monkeypatch.setattr(cli.Path, "home", fake_home(home))

    with runner.isolation(input="y\n" * 2):
        cli.configure_agents()

    claude_path = home / ".claude" / "settings.json"
    codex_path = home / ".codex" / "config.toml"
    assert json.loads(claude_path.read_text(encoding="utf-8"))["env"]["RALPH_LOOP"] == "1"
    assert tomllib.parse(codex_path.read_text(encoding="utf-8"))["shell_environment_policy"]["set"] == {
        "RALPH_LOOP": "1"
    }
    assert (home / ".codex" / "rules" / "loopgate.rules").is_file()
    assert not claude_path.with_name("settings.json.bak").exists()
    assert not codex_path.with_name("config.toml.bak").exists()


@pytest.mark.parametrize("answers", ["n\n", "y\nn\n"])
def test_configure_agents_aborts_without_writing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answers: str) -> None:
    """Declining either required agent configuration leaves the home directory untouched."""
    home = tmp_path / "home"
    monkeypatch.setattr(cli.Path, "home", fake_home(home))

    with runner.isolation(input=answers), pytest.raises(Abort):
        cli.configure_agents()

    assert not home.exists()


def test_write_harness_config_is_idempotent_with_existing_wiring(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Reconfiguring an already-wired project leaves its generated configuration unchanged."""
    pyproject = git_repo / "pyproject.toml"
    original = f'{INIT_PROJECT_COMMENT}\n[tool.project]\nsentinel = "keep"\n\n[tool.harness.gate]\ntest = ["pytest"]\n'
    pyproject.write_text(original, encoding="utf-8")
    monkeypatch.setattr(cli, "TOOLS", {})
    confirm = Mock()
    monkeypatch.setattr(cli, "confirm", confirm)

    first_checks = cli.write_harness_config()
    first_config = pyproject.read_text(encoding="utf-8")
    second_checks = cli.write_harness_config()
    configured = tomllib.loads(first_config)

    assert first_checks == second_checks == {"test"}
    assert INIT_PROJECT_COMMENT in first_config
    assert configured["tool"]["project"] == {"sentinel": "keep"}
    assert configured["tool"]["harness"]["gate"]["test"] == ["pytest"]
    assert pyproject.read_text(encoding="utf-8") == first_config
    confirm.assert_not_called()


def test_write_harness_config_selects_installed_user_tools(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Config generation preserves user settings and selects project and standalone tool configs."""
    installed = {name: object() for name in cli.TOOLS if name != "pylint"}
    monkeypatch.setattr(cli.util, "find_spec", Mock(side_effect=installed.get))
    monkeypatch.setattr(cli, "which", Mock(return_value=None))
    (git_repo / "pyproject.toml").write_text("[tool.ruff]\nline-length = 99\n", encoding="utf-8")
    monkeypatch.setattr(Path, "with_name", create_autospec(Path.with_name, wraps=Path.with_name))
    monkeypatch.setattr(Path, "read_text", create_autospec(Path.read_text, wraps=Path.read_text))
    monkeypatch.setattr(Path, "write_text", create_autospec(Path.write_text, wraps=Path.write_text))
    monkeypatch.setattr(cli.ConfigParser, "read", create_autospec(cli.ConfigParser.read, wraps=cli.ConfigParser.read))
    cli.write_harness_config()
    Path.with_name.assert_called_once_with(Path(cli.__file__).resolve(), "temp.pyproject.toml")
    assert Path.read_text.call_args_list == [
        call(git_repo / "pyproject.toml", encoding="utf-8"),
        call(Path(cli.__file__).resolve().parent / "temp.pyproject.toml", encoding="utf-8"),
        call(git_repo / "pyproject.toml", encoding="utf-8"),
    ]
    assert Path.write_text.call_args.args[0] == git_repo / "pyproject.toml"
    assert Path.write_text.call_args.kwargs == {"encoding": "utf-8"}
    assert cli.ConfigParser.read.call_args.args[1:] == ([git_repo / "tox.ini", git_repo / "setup.cfg"],)
    assert cli.ConfigParser.read.call_args.kwargs == {"encoding": "utf-8"}
    ruff_lint_no_format = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert ruff_lint_no_format["tool"]["harness"]["preflight"] == {
        "complexity": ["complexipy", ".", "--suggest-refactors"],
        "ruff_format": ["ruff", "format", "--no-cache", "--check"],
        "ruff_lint": ["ruff", "check", "--no-cache", "--show-fixes", "."],
    }
    assert ruff_lint_no_format["tool"]["ruff"] == {"line-length": 99}
    assert ruff_lint_no_format["tool"]["harness"]["preflight"].get("pylint") is None

    (git_repo / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = ['F']\n", encoding="utf-8")
    cli.write_harness_config()
    tool_ruff_adds_format = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert tool_ruff_adds_format["tool"]["ruff"]["lint"] == {"select": ["F"]}
    assert tool_ruff_adds_format["tool"]["harness"]["preflight"] == {
        "complexity": ["complexipy", ".", "--suggest-refactors"],
        "ruff_format": ["ruff", "format", "--no-cache", "--check"],
        "ruff_lint": ["ruff", "check", "--no-cache", "--show-fixes", "."],
    }
    assert tool_ruff_adds_format["tool"].get("pylint") is None
    assert tool_ruff_adds_format["tool"]["harness"]["preflight"].get("pylint") is None

    with monkeypatch.context() as no_ruff:
        installed_without_ruff = installed.copy()
        installed_without_ruff.pop("ruff_lint")
        installed_without_ruff.pop("ruff_format")
        (git_repo / ".flake8").touch()
        (git_repo / "pyproject.toml").write_text(
            "[tool.black]\nline-length = 88\n[tool.ruff.lint]\nselect = ['ALL']\n", encoding="utf-8"
        )
        no_ruff.setattr(cli.util, "find_spec", Mock(side_effect=installed_without_ruff.get))
        no_ruff.setattr(cli, "which", Mock(return_value=None))
        cli.write_harness_config()
        configured_not_installed = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
        assert configured_not_installed["tool"]["ruff"] == {"lint": {"select": ["ALL"]}}
        assert configured_not_installed["tool"]["black"] == {"line-length": 88}
        preflight_args = configured_not_installed["tool"]["harness"]["preflight"]
        assert preflight_args == {
            "complexity": cli.TOOLS["complexipy"]["args"],
            "black": cli.TOOLS["black"]["args"],
            "flake8": cli.TOOLS["flake8"]["args"],
        }
    (git_repo / ".flake8").unlink()

    (git_repo / "pyproject.toml").write_text("", encoding="utf-8")
    cli.write_harness_config()
    dotted_format = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert dotted_format["tool"]["ruff"]["format"]["quote-style"] == "double"
    assert dotted_format["tool"]["harness"]["preflight"]["ruff_format"] == cli.TOOLS["ruff_format"]["args"]
    assert dotted_format["tool"]["harness"]["preflight"]["ruff_lint"] == cli.TOOLS["ruff_lint"]["args"]
    assert dotted_format["tool"]["harness"]["preflight"].get("pylint") is None

    (git_repo / "pyproject.toml").write_text(
        "[tool.ruff.lint]\nselect = ['F']\n[tool.ruff.format]\nquote-style = 'single'\n", encoding="utf-8"
    )
    cli.write_harness_config()
    lint_and_format = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert lint_and_format["tool"]["ruff"]["lint"]["select"] == ["F"]
    assert lint_and_format["tool"]["ruff"]["format"]["quote-style"] == "single"
    assert lint_and_format["tool"]["harness"]["preflight"] == {
        "complexity": ["complexipy", ".", "--suggest-refactors"],
        "ruff_format": ["ruff", "format", "--no-cache", "--check"],
        "ruff_lint": ["ruff", "check", "--no-cache", "--show-fixes", "."],
    }
    assert lint_and_format["tool"]["harness"]["preflight"]["ruff_format"] == cli.TOOLS["ruff_format"]["args"]
    assert lint_and_format["tool"]["harness"]["preflight"]["ruff_lint"] == cli.TOOLS["ruff_lint"]["args"]
    assert lint_and_format["tool"]["harness"]["preflight"].get("pylint") is None

    (git_repo / "pyproject.toml").write_text(
        "[tool.black]\nline-length = 88\n[tool.ruff]\nline-length = 120\n[tool.ruff.lint]\nselect = ['F']\n",
        encoding="utf-8",
    )
    cli.write_harness_config()
    user_project = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert user_project["tool"]["harness"]["preflight"]["ruff_format"] == cli.TOOLS["ruff_format"]["args"]
    assert user_project["tool"]["ruff"]["lint"] == {"select": ["F"]}
    assert user_project["tool"]["harness"]["preflight"] == {
        "ruff_lint": ["ruff", "check", "--no-cache", "--show-fixes", "."],
        "ruff_format": ["ruff", "format", "--no-cache", "--check"],
        "black": ["black", "--check", "."],
        "complexity": ["complexipy", ".", "--suggest-refactors"],
    }
    assert user_project["tool"]["ruff"]["line-length"] == 120
    assert user_project["tool"]["black"] == {"line-length": 88}
    assert user_project["tool"]["ruff"].get("format") is None
    assert user_project["tool"]["harness"]["preflight"].get("pylint") is None

    installed.update({"pylint": cli.TOOLS["pylint"]})
    (git_repo / "ruff.toml").write_text('select = ["ALL"]\nexclude = ["**/*"]\n')
    (git_repo / ".flake8").touch()
    (git_repo / "pyproject.toml").write_text(
        "[project]\nname = 'existing'\n[tool.black]\nline-length = 100\n[tool.keep]\nuser = 'wins'\n", encoding="utf-8"
    )
    (git_repo / "tox.ini").write_text("[testenv]\n", encoding="utf-8")
    (git_repo / "setup.cfg").write_text("[tool:pytest]\n", encoding="utf-8")
    cli.write_harness_config()
    user_project = tomllib.loads((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert user_project["project"]["name"] == "existing"
    assert user_project["tool"]["black"] == {"line-length": 100}
    assert user_project["tool"]["keep"] == {"user": "wins"}
    assert user_project["tool"].get("ruff") is None  # no table because standalone file exists
    assert user_project["tool"]["harness"]["preflight"] == {
        "complexity": ["complexipy", ".", "--suggest-refactors"],
        "ruff_format": ["ruff", "format", "--no-cache", "--check"],
        "ruff_lint": ["ruff", "check", "--no-cache", "--show-fixes", "."],
        "pylint": ["pylint", "."],
        "black": ["black", "--check", "."],
        "flake8": ["flake8", "."],
    }
    assert user_project["tool"]["harness"]["preflight"]["pylint"] == ["pylint", "."]

    assert not {"format", "pytest", "template-format", "template-lint"} & user_project["tool"].keys()

    (git_repo / "setup.cfg").unlink()
    monkeypatch.setattr(cli.util, "find_spec", Mock(side_effect={"pytest": object()}.get))
    monkeypatch.setattr(cli, "which", which_finds(("tox",)))
    (git_repo / "pyproject.toml").write_text("[project]\nname = 'existing'\n[tool.black]\n", encoding="utf-8")
    cli.write_harness_config()
    user_project = tomllib.loads((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert user_project["tool"]["harness"]["gate"] == {
        "security": cli.TOOLS["semgrep"]["args"],
        "types": ["pyright", "--outputjson"],
        "test": ["tox"],
    }
    monkeypatch.setattr(cli.util, "find_spec", Mock(side_effect={"bandit": object()}.get))
    for case in CONFIG_SOURCE_MATRIX:
        if case["standalone"]:
            (git_repo / ".bandit").touch()
        else:
            (git_repo / ".bandit").unlink(missing_ok=True)
        (git_repo / "setup.cfg").write_text("[bandit]\n" if case["other"] else "", encoding="utf-8")
        (git_repo / "pyproject.toml").write_text("[tool.bandit]\n" if case["pyproject"] else "", encoding="utf-8")
        cli.write_harness_config()
        user_project = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))

        assert (user_project["tool"]["harness"]["gate"].get("security") == cli.TOOLS["bandit"]["args"]) is case[
            "detected"
        ]


def test_init_declines_without_mutating_repo(git_repo: Path) -> None:
    """The initial opt-in is non-aborting and leaves an existing repository untouched."""
    result = runner.invoke(cli.app, ["init"], input="n\n")

    assert (
        result.exit_code,
        "Run `harness init`" in result.output,
        (git_repo / "pyproject.toml").exists(),
        (git_repo / "scratchpad").exists(),
    ) == (0, True, False, False)
    fresh = subprocess.run(
        [sys.executable, "-c", "from harness.cli import main; main(['init'])"],
        cwd=git_repo,
        input="n\n",
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        check=False,
    )
    assert fresh.returncode == 0, fresh.stderr
    assert "Run `harness init`" in fresh.stdout


def test_init_hoists_and_records_the_installed_harness(git_repo: Path) -> None:
    """A wheel-installed harness creates a foreign project's config and its hooks work from Git."""
    assert not (git_repo / "pyproject.toml").exists()
    environment_python = assert_installed_config_paths(git_repo)
    installed_harness = harness_executable(environment_python.parent)
    write_executable(environment_python.parent / "gtimeout", "")
    environment = isolated_environment(environment_python, git_repo.parent / "home")
    initialized = subprocess.run(
        [installed_harness, "init"],
        cwd=git_repo,
        input="y\n" * 5,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    generated = tomllib.loads((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert generated["tool"]["harness"]["preflight"]
    assert generated["tool"]["harness"]["gate"]
    recorded = git_repo / ".git" / "harness-path"
    assert recorded.read_text(encoding="utf-8") == f"{installed_harness.as_posix()}\n"
    assert gate.run_git(["config", "--get", "core.hooksPath"], git_repo).strip() == ".githooks"
    required_assets = (
        "docs/PROMPT.md",
        "preferences/preferences.py",
        "mutation/check_mutmut.py",
        "tests/preferences/test_preferences.py",
        "tests/mutation/test_check_mutmut.py",
    )
    for path in required_assets:
        assert (git_repo / path).is_file()
    assert all(
        (git_repo / ".githooks" / name).is_file()
        for name in ("_resolve", "pre-commit", "pre-push", "prepare-commit-msg")
    )
    status = subprocess.run(
        [installed_harness, "status"], cwd=git_repo, capture_output=True, text=True, env=environment, check=False
    )

    (git_repo / "hook_check.py").write_text('"""Hook integration fixture."""\n\nVALUE = 1\n', encoding="utf-8")
    gate.run_git(["add", "hook_check.py"], git_repo)
    committed = subprocess.run(
        ["git", "commit", "-q", "-m", "exercise installed hooks"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        env=environment | {"RALPH_LOOP": "1"},
        check=False,
    )
    assert committed.returncode == 0, committed.stdout + committed.stderr
    assert gate.run_git(["show", "--name-only", "--format=", "HEAD"], git_repo).splitlines() == ["hook_check.py"]
    assert status.returncode == 0, status.stderr
    assert status.stdout == f"0 run log(s) in {git_repo / 'scratchpad/runs'}\nnewest:\n\n"
    cache_files = []
    for destination in ("preferences", "mutation", "tests"):
        cache_files.extend(
            path for path in (git_repo / destination).rglob("*") if "__pycache__" in path.parts or path.suffix == ".pyc"
        )
    assert not cache_files, "\n".join(str(path.relative_to(git_repo)) for path in cache_files)
    assert "Can likely run loops with checks: True" in unstyle(initialized.stdout)


def test_init_aborts_when_required_hooks_are_declined(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Init stops when its required hook confirmation aborts and reports successful setup."""
    (git_repo / "pyproject.toml").write_text("", encoding="utf-8")
    confirm = Mock(side_effect=[True, Abort()])
    write_config = Mock()
    message = Mock()
    monkeypatch.setattr(cli, "confirm", confirm)
    monkeypatch.setattr(cli, "write_harness_config", write_config)
    monkeypatch.setattr(cli, "rprint", message)

    with pytest.raises(Abort):
        cli.init()

    write_config.assert_called_once()
    message.assert_not_called()

    confirm.side_effect = None
    confirm.return_value = True
    write_config.return_value = {"test"}
    monkeypatch.setattr(cli, "hoist", Mock(return_value=True))
    monkeypatch.setattr(cli, "setup_git_hooks", Mock(return_value=git_repo / ".githooks"))
    monkeypatch.setattr(cli, "check_for_timeout_and_prompt", Mock(return_value="timeout"))
    monkeypatch.setattr(cli, "configure_agents", Mock(return_value=True))
    cli.init()

    assert "\n[bold cyan2]Can likely run loops with checks: [/]True\n" in message.call_args.args[0]


def test_hoist_rejects_missing_required_assets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hoisting stops before prompting when the installed package lacks required asset directories."""
    message = Mock()
    monkeypatch.setattr(
        cli,
        "ASSETS",
        {
            "docs": (tmp_path / "missing-docs", tmp_path / "docs"),
            "githooks": (tmp_path / "missing-hooks", tmp_path / ".githooks"),
        },
    )
    monkeypatch.setattr(cli, "rprint", message)

    assert cli.hoist() is False
    message.assert_called_once_with("Harness is missing required assets: `docs/` and `githooks/`")
    (tmp_path / "docs").mkdir()
    (tmp_path / "missing-hooks").mkdir()
    assert cli.hoist() is False

    repo = tmp_path / "repo"
    package_docs = tmp_path / "package" / "docs"
    package_hooks = tmp_path / "package" / ".githooks"
    (package_docs / "nested").mkdir(parents=True)
    package_hooks.mkdir(parents=True)
    (package_docs / "nested" / "new.txt").write_text("new\n", encoding="utf-8")
    (package_docs / "existing.txt").write_text("packaged\n", encoding="utf-8")
    (package_hooks / "hoist").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "existing.txt").write_text("existing\n", encoding="utf-8")
    monkeypatch.setattr(
        cli, "ASSETS", {"docs": (package_docs, repo / "docs"), "githooks": (package_hooks, repo / ".githooks")}
    )
    monkeypatch.setattr(cli, "REPO_ROOT", repo)
    confirm = Mock(return_value=True)
    run_git = Mock()
    message.reset_mock()
    monkeypatch.setattr(cli, "confirm", confirm)
    monkeypatch.setattr(cli, "run_git", run_git)
    monkeypatch.setattr(Path, "mkdir", create_autospec(Path.mkdir, wraps=Path.mkdir))
    monkeypatch.setattr(Path, "touch", create_autospec(Path.touch, wraps=Path.touch))
    monkeypatch.setattr(cli.console, "print", Mock(wraps=cli.console.print))

    assert cli.hoist() is True
    assert (repo / "docs" / "nested" / "new.txt").read_text(encoding="utf-8") == "new\n"
    assert (repo / "docs" / "existing.txt").read_text(encoding="utf-8") == "existing\n"
    assert (repo / "scratchpad" / "runs" / ".gitkeep").is_file()
    assert confirm.call_args_list == [
        call(cli.style("\n2. Can we wire githooks so quality checks run?", fg=10), default=True, abort=True),
        call(
            cli.style(
                "\n4. Confirm, loopgate can add those files? Pre-existing files in the expected paths will remain "
                "and loopgate will skip adding them.",
                fg=10,
            ),
            default=True,
            abort=True,
        ),
    ]
    Path.mkdir.assert_any_call(repo / ".githooks", parents=True, exist_ok=True)
    assert Path.mkdir.call_args_list.count(call(repo / "docs" / "nested", parents=True, exist_ok=True)) == 2
    Path.mkdir.assert_any_call(repo / "scratchpad" / "runs", parents=True, exist_ok=True)
    Path.touch.assert_called_once_with(repo / "scratchpad" / "runs" / ".gitkeep")
    cli.console.print.assert_called_once_with("[green]\n3. Ran script to make `.githooks` executable[/]\n")
    assert message.call_args_list[0] == call(
        "\n[bold yellow]We will need to add these files[/]\n* `.githooks` are what ensure quality checks run"
        "\n* Mutation tests promote better tests. Docs and code for example test at `mutation/` and `tests/mutation`"
        "\n* `preferences/` allows for checks beyond what tooling catches"
        "and demonstrates Hypothesis property tests\n* `docs` contain the instructions and memory for loops "
        "\n*`scratchpad/` allows local agent use and contains a `runs/` directory for logs."
    )
    assert message.call_args_list[1:4] == [
        call(f"Skipped existing `{repo / 'docs/existing.txt'}` during copy"),
        call(f"`docs/` exists {(repo / 'docs').exists()}"),
        call(f"`githooks/` exists {(repo / '.githooks').exists()}"),
    ]
    assert message.call_args_list[-1] == call(f"`scratchpad/` and `runs/` also added at {repo}")
    run_git.assert_called_once_with(
        ["-c", 'alias.loopgate-hoist=!f() { sh "$1"; }; f', "loopgate-hoist", (package_hooks / "hoist").as_posix()],
        repo,
    )


def test_hoist_aborts_before_writing_when_declined(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Declining file hoisting creates none of the destination paths."""
    package_docs = tmp_path / "package" / "docs"
    package_hooks = tmp_path / "package" / ".githooks"
    package_docs.mkdir(parents=True)
    package_hooks.mkdir(parents=True)
    repo = tmp_path / "repo"
    monkeypatch.setattr(
        cli, "ASSETS", {"docs": (package_docs, repo / "docs"), "githooks": (package_hooks, repo / ".githooks")}
    )

    with runner.isolation(input="n\n"), pytest.raises(Abort):
        cli.hoist()

    assert not repo.exists()


def test_installing_the_template_cleans_the_repo_and_sets_hooks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Install a fresh template checkout with real process, filesystem, and Git boundaries."""
    template_repo = tmp_path / "template"
    subprocess.run(["git", "clone", "--quiet", "--shared", str(REPO_ROOT), str(template_repo)], check=True)
    expected_project = (REPO_ROOT / "harness" / "temp.pyproject.toml").read_text(encoding="utf-8")
    (template_repo / "harness" / "temp.pyproject.toml").write_text(expected_project, encoding="utf-8")
    for module in ("cli.py", "config.py"):
        (template_repo / "harness" / module).write_text(
            (REPO_ROOT / "harness" / module).read_text(encoding="utf-8"), encoding="utf-8"
        )
    source_project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    env_bin = template_repo / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    monkeypatch.setattr(cli, "REPO_ROOT", template_repo)
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(template_repo))
    monkeypatch.setattr(gates(), "repo_root", template_repo)
    monkeypatch.setattr(cli, "check_for_timeout_and_prompt", Mock(return_value="timeout"))

    result = runner.invoke(cli.app, ["install"])

    assert result.exit_code == 0, result.output
    recorded_harness = (template_repo / ".git" / "harness-path").read_text(encoding="utf-8").strip()
    assert normalized_path(recorded_harness) == normalized_path(harness_executable(env_bin))
    installed = subprocess.run(
        [recorded_harness, "--help"], cwd=template_repo, capture_output=True, text=True, check=False
    )
    assert installed.returncode == 0, installed.stderr
    assert "install" in installed.stdout
    assert gate.run_git(["config", "--get", "core.hooksPath"], template_repo).strip() == ".githooks"
    assert (template_repo / "preferences").is_dir()
    assert (template_repo / "tests" / "preferences").is_dir()

    leftovers = [
        name
        for name in (
            "README.template.md",
            ".assets",
            ".github/workflows/publish.yml",
            "CONTRIBUTING.md",
            "mutation-score.json",
            "harness/tests",
        )
        if (template_repo / name).exists()
    ]
    assert (template_repo / "pyproject.toml").read_text(encoding="utf-8") == expected_project
    assert not leftovers, f"template leftovers after install: {leftovers}"

    installed = subprocess.run(
        [env_bin / ("ruff.exe" if sys.platform == "win32" else "ruff"), "check", "--no-cache", "--show-fixes", "."],
        cwd=template_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert "has no effect because preview is not enabled" not in installed.stdout + installed.stderr
    installed_project = tomllib.loads((template_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert (
        installed_project["tool"]["ruff"]["line-length"],
        installed_project["tool"]["pylint"]["format"]["max-line-length"],
        installed_project["tool"]["ruff"]["lint"]["pycodestyle"]["max-doc-length"],
    ) == (
        source_project["tool"]["ruff"]["line-length"],
        source_project["tool"]["pylint"]["format"]["max-line-length"],
        source_project["tool"]["ruff"]["lint"]["pycodestyle"]["max-doc-length"],
    )


def test_cleanup_updates_a_pristine_single_commit_template(tmp_path: Path) -> None:
    """A pristine template's only commit is amended with the installed project files."""
    replacement_project = (REPO_ROOT / "harness" / "temp.pyproject.toml").read_text(encoding="utf-8")
    replacement_readme = "replacement project readme\n"
    gate.run_git(["init", "-q"], tmp_path)
    gate.run_git(["config", "user.email", "harness@test.local"], tmp_path)
    gate.run_git(["config", "user.name", "harness-test"], tmp_path)
    (tmp_path / "README.md").write_text("template instructions\n", encoding="utf-8")
    (tmp_path / "README.template.md").write_text(replacement_readme, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "template"\n', encoding="utf-8")
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness" / "temp.pyproject.toml").write_text(replacement_project, encoding="utf-8")
    gate.run_git(["add", "-A"], tmp_path)
    gate.run_git(["commit", "-q", "-m", "template state"], tmp_path)
    assert not gate.run_git(["status", "--porcelain"], tmp_path)
    original_head = gate.run_git(["rev-parse", "HEAD"], tmp_path).strip()
    original_message = gate.run_git(["log", "-1", "--format=%B"], tmp_path).strip()

    assert cli.cleanup(tmp_path) is True

    amended_head = gate.run_git(["rev-parse", "HEAD"], tmp_path).strip()
    assert amended_head != original_head
    assert gate.run_git(["rev-list", "--count", "HEAD"], tmp_path).strip() == "1"
    assert gate.run_git(["log", "-1", "--format=%B"], tmp_path).strip() == original_message
    assert not gate.run_git(["status", "--porcelain"], tmp_path)
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == replacement_readme
    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == replacement_project
    assert not (tmp_path / "harness" / "temp.pyproject.toml").exists()


@pytest.mark.parametrize(("has_readme", "has_project"), [(False, False), (True, False), (False, True)])
def test_cleanup_requires_both_template_files(has_readme: bool, has_project: bool, tmp_path: Path) -> None:
    """Cleanup does nothing unless both template inputs identify a template checkout."""
    if has_readme:
        (tmp_path / "README.template.md").touch()
    if has_project:
        (tmp_path / "harness").mkdir()
        (tmp_path / "harness" / "temp.pyproject.toml").touch()

    assert cli.cleanup(tmp_path) is False


def test_cleanup_does_not_amend_a_dirty_template(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Real dirty state prevents cleanup from amending the historical template commit."""
    replacement_project = (REPO_ROOT / "harness" / "temp.pyproject.toml").read_text(encoding="utf-8")
    (git_repo / "pyproject.toml").write_text('[project]\nname = "template"\n', encoding="utf-8")
    (git_repo / "README.template.md").write_text("replacement project readme\n", encoding="utf-8")
    (git_repo / "harness").mkdir()
    (git_repo / "harness" / "temp.pyproject.toml").write_text(replacement_project, encoding="utf-8")
    gate.run_git(["add", "-A"], git_repo)
    gate.run_git(["commit", "-q", "-m", "template state"], git_repo)
    assert not gate.run_git(["status", "--porcelain"], git_repo)
    original_head = gate.run_git(["rev-parse", "HEAD"], git_repo).strip()
    (git_repo / "README.md").write_text("uncommitted user work\n", encoding="utf-8")
    run_git = Mock(wraps=cli.run_git, side_effect=[DEFAULT, "867f2df\n"])
    monkeypatch.setattr(cli, "run_git", run_git)

    assert cli.cleanup(git_repo) is True

    assert gate.run_git(["rev-parse", "HEAD"], git_repo).strip() == original_head
    assert gate.run_git(["status", "--porcelain"], git_repo)


@pytest.mark.parametrize(
    ("lockfile", "virtual_env", "manager"),
    [
        pytest.param("uv.lock", None, "uv", id="uv-lockfile"),
        pytest.param("poetry.lock", None, "poetry", id="poetry-lockfile"),
        pytest.param(None, "uv-managed", "uv", id="uv-environment"),
        pytest.param(None, "pypoetry-cache", "poetry", id="poetry-environment"),
        pytest.param(None, None, "pip", id="no-manager-signal"),
    ],
)
def test_install_picks_the_package_manager_from_project_signals(
    lockfile: str | None, virtual_env: str | None, manager: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Lockfiles and active Poetry environments select the manager whose harness path hooks record.

    With no manager signal, install falls back to the current interpreter's pip environment.
    """
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    python_name = "python.exe" if sys.platform == "win32" else "python"
    interpreter = git_repo / (virtual_env or ".pyenv") / scripts
    poetry_bin = git_repo / ".poetry" / "virtualenvs" / "project" / scripts
    monkeypatch.setenv("UV_TESTING", "1")
    for name in tuple(os.environ):
        if name.startswith("UV_"):
            monkeypatch.delenv(name)
    assert not any(name.startswith("UV_") for name in os.environ)
    if virtual_env == "uv-managed":
        monkeypatch.setenv("UV_TESTING", "1")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(cli.sys, "executable", str(interpreter / python_name))
    monkeypatch.setattr(cli, "which", Mock(wraps=which_finds(("timeout",))))
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(git_repo))
    (git_repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    if lockfile:
        (git_repo / lockfile).touch()
    toolchain = stub_toolchain(git_repo / ".git", str(poetry_bin / python_name))
    monkeypatch.setattr(subprocess, "run", toolchain)

    assert runner.invoke(cli.app, ["install"]).exit_code == 0
    calls = [tuple(record.args[0]) for record in toolchain.call_args_list]

    managers = {
        "uv": ("uv", "sync"),
        "poetry": ("poetry", "install"),
        "pip": (str(interpreter / python_name), "-m", "pip", "install", "-r", "requirements.txt", "-e", "."),
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
    ("on_path", "answer", "installs_coreutils", "offers_coreutils", "shows_homebrew_hint"),
    [
        pytest.param(("timeout",), None, False, False, False, id="timeout-present"),
        pytest.param(("gtimeout",), None, False, False, False, id="gtimeout-present"),
        pytest.param((), None, False, True, True, id="no-timeout-no-homebrew"),
        pytest.param(("brew",), True, True, True, False, id="confirmed"),
        pytest.param(("brew",), False, False, True, False, id="declined"),
    ],
)
def test_install_offers_coreutils_only_when_no_timeout_tool_exists(
    on_path: tuple[str, ...],
    answer: bool | None,
    installs_coreutils: bool,
    offers_coreutils: bool,
    shows_homebrew_hint: bool,
    monkeypatch: pytest.MonkeyPatch,
    git_repo: Path,
) -> None:
    """macOS needs coreutils to time out a loop iteration, so install probes for it and offers the
    install only when Homebrew can do it. It never prompts when a timeout tool is already there.
    """
    prompts: list[str] = []

    def confirm(prompt: str, abort: bool = False) -> bool:
        assert abort is True
        prompts.append(prompt)
        return bool(answer)

    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "which", which_finds(on_path))
    monkeypatch.setattr(cli, "confirm", confirm)
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(git_repo))
    (git_repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    toolchain = stub_toolchain(git_repo / ".git")
    monkeypatch.setattr(subprocess, "run", toolchain)

    result = runner.invoke(cli.app, ["install"])
    calls = [tuple(record.args[0]) for record in toolchain.call_args_list]
    output = unstyle(result.stdout)

    assert result.exit_code == 0
    assert (("brew", "install", "coreutils") in calls) is installs_coreutils
    assert prompts == ([] if answer is None else ["\nInstall `brew install coreutils` now?"])
    assert ("macOS harness needs timeout/gtimeout from coreutils" in output) is offers_coreutils
    assert ("Get Homebrew https://brew.sh" in output) is shows_homebrew_hint


def test_windows_skips_posix_steps_and_launches_the_powershell_twin(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Windows has no POSIX shell, ls or coreutils, so install records harness.exe and warns that the
    support is experimental, and a run goes through PowerShell instead of ralph.sh.
    """
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "which", which_finds(("uv",)))
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(git_repo))
    (git_repo / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (git_repo / "uv.lock").touch()
    toolchain = stub_toolchain(git_repo / ".git")
    monkeypatch.setattr(subprocess, "run", toolchain)

    installed = runner.invoke(cli.app, ["install"])
    calls = [tuple(record.args[0]) for record in toolchain.call_args_list]

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

    validation = Mock()
    monkeypatch.setattr(cli, "raise_issues", validation)
    monkeypatch.setattr(cli, "run_worker", capture_worker)
    (git_repo / "docs").mkdir()
    (git_repo / "docs" / "PROMPT.md").write_text("do the most important thing\n", encoding="utf-8")

    assert runner.invoke(cli.app, ["run", "claude", "2", "5"]).exit_code == 0
    validation.assert_called_once_with("claude", 2, 5)
    assert launched[0][:3] == ["powershell.exe", "-NoProfile", "-File"]
    assert launched[0][3].endswith("ralph.ps1")
    assert launched[0][4:6] == ["2", "5"]
    assert launched[0][6:] == list(gates().agents["claude"])


def test_windows_run_uses_powershell_without_path_lookup(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Windows relies on its system PowerShell command without resolving a separate executable."""
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    path_lookup = Mock(return_value=None)
    worker = Mock(return_value=0)
    monkeypatch.setattr(cli, "which", path_lookup)
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


@pytest.mark.parametrize("hook", ["pre-commit", "pre-push", "prepare-commit-msg"])
def test_tracked_hooks_call_registered_commands_without_venv_paths(hook: str) -> None:
    """Each hook invokes a harness command that exists and assumes no POSIX virtualenv layout."""
    text = (REPO_ROOT / ".githooks" / hook).read_text(encoding="utf-8")
    called: list[str] = []
    for line in text.splitlines():
        words = line.split()
        for index, word in enumerate(words):
            if word == '"$HARNESS"' and index + 1 < len(words):
                called.append(words[index + 1])

    assert called, f"{hook} does not invoke harness"
    for command in called:
        assert runner.invoke(cli.app, [command, "--help"]).exit_code == 0
    assert ".venv/bin/harness" not in text
    assert ".venv/bin/python" not in text
    assert "uv" not in text


@pytest.mark.parametrize(
    ("arguments", "code"),
    [pytest.param([".git/COMMIT_EDITMSG", "merge"], 1, id="blocked-merge"), pytest.param([], 0, id="no-arguments")],
)
def test_prepare_commit_msg_forwards_gits_own_arguments(
    arguments: list[str], code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook command hands git's own arguments to the gate logic and exits with its status."""
    seen: list[list[str]] = []

    def commit_msg(gate_instance: Gate, argv: list[str]) -> int:
        del gate_instance
        seen.append(argv)
        return code

    monkeypatch.setattr(Gate, "prepare_commit_msg", commit_msg)

    result = runner.invoke(cli.app, ["prepare-commit-msg", *arguments])

    assert result.exit_code == code
    assert seen == [["prepare-commit-msg", *arguments]]


def test_zero_iterations_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero iterations is refused instead of reporting a vacuous success."""
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "check_for_timeout_and_prompt", Mock(return_value="timeout"))

    result = runner.invoke(cli.app, ["run", "claude", "0", "1"])

    assert result.exit_code == 2
    assert result.stderr.strip() == "iterations and max_minutes must be >= 1"


def test_zero_minutes_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero minutes is refused because it would disable the per-iteration timeout."""
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "check_for_timeout_and_prompt", Mock(return_value="timeout"))

    result = runner.invoke(cli.app, ["run", "claude", "1", "0"])

    assert result.exit_code == 2
    assert "iterations and max_minutes must be >= 1" in result.stderr


def test_posix_run_rejected_without_a_timeout_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid run is refused when POSIX cannot enforce its per-iteration time budget."""
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    timeout_probe = Mock(return_value="")
    monkeypatch.setattr(cli, "check_for_timeout_and_prompt", timeout_probe)

    result = runner.invoke(cli.app, ["run", "claude", "1", "1"])

    assert result.exit_code == 2
    assert "gtimeout or timeout is required" in result.stderr
    timeout_probe.assert_called_once_with()


@pytest.mark.parametrize(
    ("is_windows", "timeout", "expected"),
    [
        pytest.param(False, "gtimeout", (True, "gtimeout"), id="posix"),
        pytest.param(True, None, (False, ""), id="windows"),
    ],
)
def test_valid_run_inputs_set_the_timeout_environment(
    is_windows: bool, timeout: str | None, expected: tuple[bool, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validated inputs normalize the agent name and publish the platform timeout for the launcher."""
    monkeypatch.setattr(cli, "IS_WINDOWS", is_windows)
    monkeypatch.setattr(cli, "check_for_timeout_and_prompt", Mock(return_value=timeout))

    assert (cli.raise_issues("CLAUDE", 1, 1), os.environ["TIMEOUT"]) == expected


def test_invalid_run_reports_every_problem(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Independent validation failures are all reported in one usage error."""
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "check_for_timeout_and_prompt", Mock(return_value=""))
    secho = Mock(wraps=cli.secho)
    monkeypatch.setattr(cli, "secho", secho)

    with pytest.raises(cli.Exit) as exit_info:
        cli.raise_issues("BOGUS", 0, 0)

    assert exit_info.value.exit_code == 2
    message = "Unknown agent name 'bogus' iterations and max_minutes must be >= 1 gtimeout or timeout is required"
    assert " ".join(capsys.readouterr().err.split()) == message
    secho.assert_called_once_with(message, err=True, fg=cli.colors.MAGENTA, bold=True)


def test_a_harnessed_run_writes_numbered_receipts_and_propagates_exit_codes(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A day of runs replayed: bad arguments are refused before anything is created, each accepted run
    launches ralph.sh with the agent's preset and lands its own numbered receipt beside the earlier
    ones, an overridden model replaces the preset's, and the worker's exit code reaches the shell.
    """
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "check_for_timeout_and_prompt", Mock(return_value="timeout"))
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
        assert "iterations and max_minutes must be >= 1" in refused.stderr
    assert not (git_repo / "scratchpad").exists()
    assert launched == []

    first = runner.invoke(cli.app, ["run", "claude", "1", "2", "False"])

    assert first.exit_code == 0
    assert not first.stdout
    assert launched[0][0].endswith("ralph.sh")
    assert launched[0][1:3] == ["1", "2"]
    assert launched[0][3:] == list(gates().agents["claude"])
    receipts = git_repo / "scratchpad" / "runs" / "20990102" / "claude"
    assert (receipts / "0001.jsonl").read_text(encoding="utf-8") == '{"type":"result","result":"ok"}\n'
    assert os.environ["RALPH_PROMPT"] == ("Your agent id prefix is `claude-0001`\n\ndo the most important thing")

    second = runner.invoke(cli.app, ["run", "claude", "1", "2", "False", "--model", "haiku"])

    assert second.exit_code == 0
    swapped = list(gates().agents["claude"])
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
    streaming_worker = [sys.executable, "-c", 'print(\'{ "type" : "result" }\'); print("not json")']
    real_popen = subprocess.Popen
    popen = Mock(wraps=real_popen)
    monkeypatch.setattr(Path, "open", create_autospec(Path.open, wraps=Path.open))
    monkeypatch.setattr(cli.subprocess, "Popen", popen)
    monkeypatch.setattr(cli, "JSON", Mock(wraps=cli.JSON))
    monkeypatch.setattr(cli.console, "print", Mock(wraps=cli.console.print))

    assert cli.run_worker(streaming_worker, log, verbose=True) == 0
    popen.assert_called_once_with(streaming_worker, cwd=str(tmp_path), stdout=subprocess.PIPE, text=True)
    assert cli.JSON.call_args_list == [call('{ "type" : "result" }\n', indent=None), call("not json\n", indent=None)]
    assert [printed.kwargs for printed in cli.console.print.call_args_list] == [{"end": "\n"}, {"end": "\n"}]

    streamed = capsys.readouterr().out
    assert '"type"' in streamed
    assert '"result"' in streamed
    assert "not json" in streamed
    assert log.read_text(encoding="utf-8") == '{ "type" : "result" }\nnot json\n'

    failing_worker = [sys.executable, "-c", 'print("worker output"); raise SystemExit(3)']
    real_run = subprocess.run
    run = Mock(wraps=real_run)
    monkeypatch.setattr(cli.subprocess, "run", run)

    assert cli.run_worker(failing_worker, log, verbose=False) == 3
    assert run.call_args.args == (failing_worker,)
    assert run.call_args.kwargs["cwd"] == str(tmp_path)
    assert run.call_args.kwargs["stdout"].name == str(log)
    assert run.call_args.kwargs["check"] is False

    assert not capsys.readouterr().out
    assert log.read_text(encoding="utf-8") == "worker output\n"
    assert Path.open.call_args_list[::2] == [call(log, "w", encoding="utf-8")] * 2


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
    monkeypatch.setitem(gates().agents, "claude", preset)
    monkeypatch.chdir(git_repo)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(git_repo))
    freeze_run_day(monkeypatch)
    (git_repo / "docs").mkdir()
    (git_repo / "docs" / "PROMPT.md").write_text("build from specs\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["run", "claude", "2", "1"])

    assert result.exit_code == 0
    assert (git_repo / "claude-count").read_text(encoding="utf-8") == "2"
    identity = "Your agent id prefix is `claude-0001`\n\n"
    assert (git_repo / "prompt-1.txt").read_text(encoding="utf-8") == (
        f"{identity}build from specs\n\nRALPH_ITERATION=1/2\n"
    )
    assert (git_repo / "prompt-2.txt").read_text(encoding="utf-8") == (
        f"{identity}build from specs\n\nRALPH_ITERATION=2/2\n"
    )
    preset_args = preset[2:]
    assert (git_repo / "claude-args.txt").read_text(encoding="utf-8").splitlines() == [*preset_args, *preset_args]
    receipt = git_repo / "scratchpad" / "runs" / "20990102" / "claude" / "0001.jsonl"
    events = [json.loads(line) for line in receipt.read_text(encoding="utf-8").splitlines()]
    assert [event["type"] for event in events] == ["ralph", "result", "ralph", "result", "ralph"]
    assert [event.get("iteration") for event in events] == [1, None, 2, None, None]
    assert events[-1]["completed"] == 2
