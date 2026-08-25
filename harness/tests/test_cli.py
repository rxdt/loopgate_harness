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
from shutil import which
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import DEFAULT, Mock, call, create_autospec

import pytest
import tomlkit as tomllib
from click import unstyle
from typer import Abort
from typer.testing import CliRunner

from harness import cli, gate
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
    "distribution_root": str(Path(distribution("harness").locate_file("")).resolve()),
    "site_packages": str(config.site_packages.resolve()),
    "package_root": str(config.package_root.resolve()),
    "repo_root": str(config.repo_root.resolve()),
    "assets": {
        name: [str(path.resolve()) for path in paths]
        for name, paths in config.ASSETS.items()
    },
}))
"""


def assert_installed_config_paths(
    environment: dict[str, str], foreign_repo: Path
) -> dict[str, tuple[Path, Path]]:
    """The installed config keeps package sources separate from the active repository targets."""
    probe_cwd = foreign_repo / "nested"
    probe_cwd.mkdir()
    probe = subprocess.run(
        [sys.executable, "-c", INSTALLED_CONFIG_PROBE],
        cwd=probe_cwd,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    observed = json.loads(probe.stdout)
    installed_root = Path(observed["distribution_root"])
    repo_root = foreign_repo.resolve()
    assets: dict[str, tuple[Path, Path]] = {
        name: (Path(paths[0]), Path(paths[1])) for name, paths in observed["assets"].items()
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
        "assets": {
            "docs": (installed_root / "harness/docs", repo_root / "docs"),
            "githooks": (installed_root / "harness/.githooks", repo_root / ".githooks"),
            "preferences": (installed_root / "preferences", repo_root / "preferences"),
            "mutation": (installed_root / "mutation", repo_root / "mutation"),
            "pref_tests": (installed_root / "harness/tests/preferences", repo_root / "tests/preferences"),
            "mutation_tests": (installed_root / "harness/tests/mutation", repo_root / "tests/mutation"),
        },
    }
    return assets


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


def test_entry_point_propagates_exit_codes_and_rejects_unknown_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The console script lets typer.Exit reach the shell; unknown or missing commands are usage errors."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])

    assert exit_info.value.code == 0
    assert runner.invoke(cli.app, ["bogus"]).exit_code == 2
    assert runner.invoke(cli.app, []).exit_code == 2

    fake_popen(monkeypatch, fails=[gates().commit_checks["lint"], gates().commit_checks["format"]])
    rejected = runner.invoke(cli.app, ["preflight"])
    summary = " ".join(unstyle(rejected.stdout).split())

    assert rejected.exit_code == 1
    assert "FAILED lint" in summary
    assert "WARNED format" in summary
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
    agents: dict[str, list[str]] = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["harness"]["agents"]

    assert agents == gates().agents
    assert set(agents) == {"claude", "codex", "agy", "copilot"}
    assert all(isinstance(command, list) and bool(command) for command in agents.values())
    assert all(
        isinstance(argument, str) and bool(argument) for command in agents.values() for argument in command
    )


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


def test_status_counts_run_receipts_and_names_the_newest(git_repo: Path) -> None:
    """Status reports its empty placeholder, then counts receipts and points at the last one."""
    runs = git_repo / "scratchpad" / "runs"
    empty = runner.invoke(cli.app, ["status"])

    assert empty.exit_code == 0
    assert empty.stdout == f"1 run log(s) in {runs}\nnewest: \n"

    runs.mkdir(parents=True)
    (runs / "0001-claude.jsonl").write_text("{}\n", encoding="utf-8")
    (runs / "0002-codex.jsonl").write_text("{}\n", encoding="utf-8")

    counted = runner.invoke(cli.app, ["status"])

    assert counted.exit_code == 0
    assert counted.stdout == f"2 run log(s) in {runs}\nnewest: {runs / '0002-codex.jsonl'}\n"


def test_setup_git_hooks_records_exact_posix_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    run.assert_called_once_with(
        ["git", "config", "core.hooksPath", ".githooks"], cwd=str(tmp_path), check=True
    )
    assert print_message.call_args_list == [
        call("\n[cyan2]setting git hooks[/cyan2] `git config core.hooksPath .githooks`"),
        call("Windows is experimental. Reoprt issues https://github.com/rxdt/loopgate_harness/issues"),
        call(f"\nRecorded in {recorded} is the path to executable {env_bin}"),
    ]


def test_configure_agents_updates_configs_and_creates_backups(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    codex_original = (
        'model = "existing"\n\n[shell_environment_policy]\ninherit = "core"\nset = { KEEP = "yes" }\n'
    )
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
def test_configure_agents_aborts_without_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, answers: str
) -> None:
    """Declining either required agent configuration leaves the home directory untouched."""
    home = tmp_path / "home"
    monkeypatch.setattr(cli.Path, "home", fake_home(home))

    with runner.isolation(input=answers), pytest.raises(Abort):
        cli.configure_agents()

    assert not home.exists()


def test_write_harness_config_aborts_when_existing_wiring_is_declined(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Declining an already-wired configuration leaves pyproject.toml unchanged."""
    pyproject = git_repo / "pyproject.toml"
    original = '[tool.harness.gate]\ntest = ["pytest"]\n'
    pyproject.write_text(original, encoding="utf-8")
    monkeypatch.setattr(cli, "TOOLS", {})

    with runner.isolation(input="n\n"), pytest.raises(Abort):
        cli.write_harness_config()

    assert pyproject.read_text(encoding="utf-8") == original


def test_write_harness_config_selects_installed_user_tools(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Config generation preserves user settings and selects project and standalone tool configs."""
    installed = {name: object() for name in cli.TOOLS if name != "pylint"}
    monkeypatch.setattr(cli.util, "find_spec", Mock(side_effect=installed.get))
    monkeypatch.setattr(cli, "which", Mock(return_value=None))
    (git_repo / "pyproject.toml").write_text("[tool.ruff]\nline-length = 99\n", encoding="utf-8")
    cli.write_harness_config()
    ruff_lint_no_format = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert ruff_lint_no_format["tool"]["harness"]["preflight"] == {
        "complexity": ["complexipy", ".", "--suggest-refactors"],
        "format": ["ruff", "format", "--no-cache", "--check"],
        "lint": ["ruff", "check", "--no-cache", "--show-fixes", "."],
    }
    assert ruff_lint_no_format["tool"]["ruff"] == {"line-length": 99}
    assert ruff_lint_no_format["tool"]["harness"]["preflight"].get("pylint") is None

    (git_repo / "pyproject.toml").write_text("[tool.ruff.lint]\nselect = ['F']\n", encoding="utf-8")
    cli.write_harness_config()
    tool_ruff_adds_format = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert tool_ruff_adds_format["tool"]["ruff"]["lint"] == {"select": ["F"]}
    assert tool_ruff_adds_format["tool"]["harness"]["preflight"] == {
        "complexity": ["complexipy", ".", "--suggest-refactors"],
        "format": ["ruff", "format", "--no-cache", "--check"],
        "lint": ["ruff", "check", "--no-cache", "--show-fixes", "."],
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
            "format": cli.TOOLS["black"]["args"],
            "lint": cli.TOOLS["flake8"]["args"],
        }
    (git_repo / ".flake8").unlink()

    (git_repo / "pyproject.toml").write_text("", encoding="utf-8")
    cli.write_harness_config()
    dotted_format = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert dotted_format["tool"]["ruff"]["format"]["quote-style"] == "double"
    assert dotted_format["tool"]["harness"]["preflight"]["format"] == cli.TOOLS["ruff_format"]["args"]
    assert dotted_format["tool"]["harness"]["preflight"]["lint"] == cli.TOOLS["ruff_lint"]["args"]
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
        "format": ["ruff", "format", "--no-cache", "--check"],
        "lint": ["ruff", "check", "--no-cache", "--show-fixes", "."],
    }
    assert lint_and_format["tool"]["harness"]["preflight"]["format"] == cli.TOOLS["ruff_format"]["args"]
    assert lint_and_format["tool"]["harness"]["preflight"]["lint"] == cli.TOOLS["ruff_lint"]["args"]
    assert lint_and_format["tool"]["harness"]["preflight"].get("pylint") is None

    (git_repo / "pyproject.toml").write_text(
        "[tool.black]\nline-length = 88\n[tool.ruff]\nline-length = 120\n[tool.ruff.lint]\nselect = ['F']\n",
        encoding="utf-8",
    )
    cli.write_harness_config()
    user_project = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))
    assert user_project["tool"]["harness"]["preflight"]["format"] == cli.TOOLS["black"]["args"]
    assert user_project["tool"]["ruff"]["lint"] == {"select": ["F"]}
    assert user_project["tool"]["harness"]["preflight"] == {
        "lint": ["ruff", "check", "--no-cache", "--show-fixes", "."],
        "format": ["black", "--check", "."],
        "ruff_format": ["ruff", "format", "--no-cache", "--check"],
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
        "[project]\nname = 'existing'\n[tool.black]\nline-length = 100\n[tool.keep]\nuser = 'wins'\n",
        encoding="utf-8",
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
        "format": ["black", "--check", "."],
        "lint": ["flake8", "."],
        "pylint": ["pylint", "."],
        "ruff_format": ["ruff", "format", "--no-cache", "--check"],
        "ruff_lint": ["ruff", "check", "--no-cache", "--show-fixes", "."],
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
        (git_repo / "pyproject.toml").write_text(
            "[tool.bandit]\n" if case["pyproject"] else "", encoding="utf-8"
        )
        cli.write_harness_config()
        user_project = tomllib.parse((git_repo / "pyproject.toml").read_text(encoding="utf-8"))

        assert (
            user_project["tool"]["harness"]["gate"].get("security") == cli.TOOLS["bandit"]["args"]
        ) is case["detected"]


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


def test_init_hoists_and_records_the_installed_harness(tmp_path: Path) -> None:
    """The installed console command initializes a disposable repo and home from packaged assets."""
    git_repo = tmp_path
    gate.run_git(["init", "-q"], git_repo)
    (git_repo / ".githooks").mkdir()
    (git_repo / "uv.lock").touch()
    (git_repo / "pyproject.toml").write_text(
        f"{INIT_PROJECT_COMMENT}\n[project]\nname = 'existing'\n{INIT_PROJECT_COMMENT}", encoding="utf-8"
    )
    (git_repo / ".githooks" / "pre-commit").write_bytes(b"#!/bin/sh\nprintf '%s\\n' existing-pre-commit\n")
    (git_repo / ".githooks" / "pre-commit").chmod(0o755)
    executable = Path(sys.executable).with_name("harness.exe" if sys.platform == "win32" else "harness")
    git_path = which("git")
    assert executable.is_file()
    assert git_path is not None
    home = git_repo / "home"
    bin_path = git_repo / "bin"
    bin_path.mkdir()
    (bin_path / "timeout").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_path / "timeout").chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("UV_")} | {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(home),
        "PATH": os.pathsep.join((
            str(bin_path),
            str(executable.parent),
            str(Path(git_path).parent),
            os.defpath,
        )),
        "USERPROFILE": str(home),
        "VIRTUAL_ENV": str(executable.parent.parent),
        "XDG_CONFIG_HOME": str(home / ".config"),
    }
    environment.pop("PYTHONPATH", None)
    installed_assets = assert_installed_config_paths(environment, git_repo)
    packaged_pre_push = (installed_assets["githooks"][0] / "pre-push").read_bytes()
    (git_repo / ".githooks" / "pre-push").write_bytes(
        packaged_pre_push.partition(b"\n")[0]
        + b'\n"$(dirname "$0")/loopgate-pre-push" "$@" || exit # loopgate\n'
        + packaged_pre_push.partition(b"\n")[2]
    )
    (git_repo / ".githooks" / "loopgate-pre-push").write_bytes(packaged_pre_push)

    result = subprocess.run(
        [str(executable), "init"],
        cwd=git_repo,
        input="y\n" * 5,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert (result.returncode, result.stderr) == (0, "")
    assert "RESULT:" in unstyle(result.stdout)
    assert "Can likely run loops: True" in unstyle(result.stdout)
    assert "Ensure your environemnt is activated" in unstyle(result.stdout)
    assert (
        subprocess.run(
            [
                git_path,
                "-c",
                'alias.loopgate-hoist=!f() { sh "$1"; }; f',
                "loopgate-hoist",
                (installed_assets["githooks"][0] / "hoist").as_posix(),
            ],
            cwd=git_repo,
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        ).returncode
        == 0
    )
    written_pyproject = (git_repo / "pyproject.toml").read_text(encoding="utf-8")
    assert tomllib.loads(written_pyproject)["project"] == {"name": "existing"}
    assert written_pyproject.count(INIT_PROJECT_COMMENT) == 2
    assert normalized_path(
        (git_repo / ".git" / "harness-path").read_text(encoding="utf-8").strip()
    ) == normalized_path(executable)
    assert gate.run_git(["config", "--get", "core.hooksPath"], git_repo).strip() == ".githooks"
    assert (git_repo / "scratchpad" / "runs" / ".gitkeep").is_file()
    assert (git_repo / ".githooks" / "pre-commit").read_bytes() == (
        b"#!/bin/sh\n(\n"
        b'    . "$(dirname "$0")/_resolve"\n'
        b'    exec "$HARNESS" preflight\n'
        b") || exit # loopgate\n"
        b"printf '%s\\n' existing-pre-commit\n"
    )
    for name in ("pre-push", "prepare-commit-msg"):
        assert (git_repo / ".githooks" / name).read_bytes() == (
            installed_assets["githooks"][0] / name
        ).read_bytes()
    assert (git_repo / ".githooks" / "_resolve").read_bytes() == (
        installed_assets["githooks"][0] / "_resolve"
    ).read_bytes()
    assert all(
        os.access(git_repo / ".githooks" / name, os.X_OK)
        for name in ("pre-commit", "pre-push", "prepare-commit-msg")
    )
    assert not list((git_repo / ".githooks").glob("loopgate-*"))
    assert not list((git_repo / ".githooks").glob(".loopgate-original-*"))
    assert {
        "docs/PROMPT.md": (git_repo / "docs" / "PROMPT.md").read_bytes(),
        "mutation/check_mutmut.py": (git_repo / "mutation" / "check_mutmut.py").read_bytes(),
        "preferences/preferences.py": (git_repo / "preferences" / "preferences.py").read_bytes(),
        "tests/mutation/test_check_mutmut.py": (
            git_repo / "tests" / "mutation" / "test_check_mutmut.py"
        ).read_bytes(),
        "tests/preferences/test_preferences.py": (
            git_repo / "tests" / "preferences" / "test_preferences.py"
        ).read_bytes(),
    } == {
        "docs/PROMPT.md": (installed_assets["docs"][0] / "PROMPT.md").read_bytes(),
        "mutation/check_mutmut.py": (installed_assets["mutation"][0] / "check_mutmut.py").read_bytes(),
        "preferences/preferences.py": (installed_assets["preferences"][0] / "preferences.py").read_bytes(),
        "tests/mutation/test_check_mutmut.py": (
            installed_assets["mutation_tests"][0] / "test_check_mutmut.py"
        ).read_bytes(),
        "tests/preferences/test_preferences.py": (
            installed_assets["pref_tests"][0] / "test_preferences.py"
        ).read_bytes(),
    }


def test_init_writes_config_before_hook_consent(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Declining required hooks aborts after config generation and before any asset or hook mutation."""
    monkeypatch.setattr(cli, "TOOLS", {})
    bin_dir = git_repo / "bin"
    bin_dir.mkdir()
    timeout = bin_dir / "gtimeout"
    write_executable(timeout, "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(Path, "home", fake_home(git_repo / "home"))
    hoist = Mock(return_value=False)
    monkeypatch.setattr(cli, "hoist", hoist)

    result = runner.invoke(cli.app, ["init"], input="y\nn\n")

    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert "harness" in tomllib.loads((git_repo / "pyproject.toml").read_text(encoding="utf-8"))["tool"]
    hoist.assert_not_called()

    retry = runner.invoke(cli.app, ["init"], input="y\n" * 5)

    assert retry.exit_code == 0, retry.output
    assert "2. Can we wire githooks so quality checks run?" in retry.output
    retry_output = unstyle(retry.output)
    assert "Can likely run loops: False" in retry_output
    assert "Success. Try running loops with `harness run <agent>`" not in retry_output
    assert "macOS harness needs timeout/gtimeout from coreutils" not in retry_output
    assert "Install `brew install coreutils` now?" not in retry_output

    hoist.assert_called_once_with()

    hoist.return_value = True
    (git_repo / ".git" / "harness-path").write_text("harness\n", encoding="utf-8")
    setup_hooks = Mock(return_value=git_repo / ".git" / "harness-path")
    configure = Mock()
    monkeypatch.setattr(cli, "setup_git_hooks", setup_hooks)
    monkeypatch.setattr(cli, "configure_agents", configure)

    success = runner.invoke(cli.app, ["init"], input="y\n" * 3)

    assert success.exit_code == 0, success.output
    assert "Can likely run loops: True" in unstyle(success.output)
    setup_hooks.assert_called_once_with(Path(sys.executable).parent)
    configure.assert_called_once_with()


def test_init_aborts_when_required_hooks_are_declined(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Init stops when its required hook confirmation aborts."""
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
        cli,
        "ASSETS",
        {"docs": (package_docs, repo / "docs"), "githooks": (package_hooks, repo / ".githooks")},
    )
    monkeypatch.setattr(cli, "REPO_ROOT", repo)
    confirm = Mock(return_value=True)
    run_git = Mock()
    monkeypatch.setattr(cli, "confirm", confirm)
    monkeypatch.setattr(cli, "run_git", run_git)

    assert cli.hoist() is True
    assert (repo / "docs" / "nested" / "new.txt").read_text(encoding="utf-8") == "new\n"
    assert (repo / "docs" / "existing.txt").read_text(encoding="utf-8") == "existing\n"
    assert (repo / "scratchpad" / "runs" / ".gitkeep").is_file()
    confirm.assert_called_once()
    assert confirm.call_args.kwargs == {"default": True, "abort": True}
    run_git.assert_called_once_with(
        [
            "-c",
            'alias.loopgate-hoist=!f() { sh "$1"; }; f',
            "loopgate-hoist",
            (package_hooks / "hoist").as_posix(),
        ],
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
        cli,
        "ASSETS",
        {"docs": (package_docs, repo / "docs"), "githooks": (package_hooks, repo / ".githooks")},
    )

    with runner.isolation(input="n\n"), pytest.raises(Abort):
        cli.hoist()

    assert not repo.exists()


def test_installing_the_template_cleans_the_repo_and_sets_hooks(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Install cleans template files, syncs dependencies, activates hooks, and is idempotent."""
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
    replacement_project = "[project]\nname = 'replacement'\n"
    (git_repo / "temp.pyproject.toml").write_text(replacement_project, encoding="utf-8")
    template_files = (".banner.svg", ".diagram.png", ".infin.png", ".loops_agents.svg", ".loops.svg")
    (git_repo / ".assets").mkdir()
    for file_name in template_files:
        (git_repo / ".assets" / file_name).touch()
    (git_repo / ".github" / "workflows").mkdir(parents=True)
    (git_repo / ".github" / "workflows" / "publish.yml").touch()
    (git_repo / "CONTRIBUTING.md").touch()
    (git_repo / "dist").mkdir()
    (git_repo / "dist" / "stale.whl").touch()
    for directory in ("harness/tests", "preferences", "tests/preferences"):
        (git_repo / directory).mkdir(parents=True)
    monkeypatch.setattr(cli, "which", which_finds(("timeout",)))
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(git_repo))
    toolchain = stub_toolchain(git_repo / ".git")
    monkeypatch.setattr(subprocess, "run", toolchain)

    monkeypatch.setattr(Path, "is_file", create_autospec(Path.is_file, wraps=Path.is_file))
    monkeypatch.setattr(Path, "replace", create_autospec(Path.replace, wraps=Path.replace))
    monkeypatch.setattr(Path, "unlink", create_autospec(Path.unlink, wraps=Path.unlink))
    monkeypatch.setattr(cli, "rmtree", Mock(wraps=cli.rmtree))
    result = runner.invoke(cli.app, ["install"])

    assert result.exit_code == 0
    assert (git_repo / "pyproject.toml").read_text(encoding="utf-8") == replacement_project
    assert not (git_repo / "temp.pyproject.toml").exists()
    assert (git_repo / "README.md").read_text(encoding="utf-8") == "seed\n"
    assert not (git_repo / "README.template.md").exists()
    assert not (git_repo / ".assets").exists()
    assert not (git_repo / ".github" / "workflows" / "publish.yml").exists()
    assert not (git_repo / "CONTRIBUTING.md").exists()
    assert not (git_repo / "dist").exists()
    assert not (git_repo / "harness" / "tests").exists()
    assert (git_repo / "preferences").is_dir()
    assert (git_repo / "tests" / "preferences").is_dir()
    Path.is_file.assert_any_call(git_repo / "README.template.md")
    Path.is_file.assert_any_call(git_repo / "temp.pyproject.toml")
    Path.replace.assert_any_call(git_repo / "README.template.md", git_repo / "README.md")
    Path.replace.assert_any_call(git_repo / "temp.pyproject.toml", git_repo / "pyproject.toml")
    Path.unlink.assert_any_call(git_repo / ".github" / "workflows" / "publish.yml", missing_ok=True)
    Path.unlink.assert_any_call(git_repo / "CONTRIBUTING.md", missing_ok=True)
    cli.rmtree.assert_any_call(git_repo / "dist")
    cli.rmtree.assert_any_call(git_repo / "harness" / "tests")
    cli.rmtree.assert_any_call(git_repo / ".assets")
    toolchain.assert_any_call(("uv", "sync"), cwd=str(git_repo), check=True)
    recorded_harness = (git_repo / ".git" / "harness-path").read_text(encoding="utf-8").strip()
    env_bin = git_repo / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    assert normalized_path(recorded_harness) == normalized_path(harness_executable(env_bin))
    toolchain.assert_any_call(
        ["git", "config", "core.hooksPath", ".githooks"], cwd=cli.REPO_ROOT_STR, check=True
    )

    head = gate.run_git(["rev-parse", "HEAD"], git_repo)
    status = gate.run_git(["status", "--porcelain"], git_repo)
    generated_project = (git_repo / "pyproject.toml").read_text(encoding="utf-8")
    again = runner.invoke(cli.app, ["install"])

    assert again.exit_code == 0
    assert gate.run_git(["rev-parse", "HEAD"], git_repo) == head
    assert gate.run_git(["status", "--porcelain"], git_repo) == status
    assert (git_repo / "pyproject.toml").read_text(encoding="utf-8") == generated_project
    assert (git_repo / "README.md").read_text(encoding="utf-8") == "seed\n"


def test_cleanup_updates_the_pristine_historical_template_commit(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """The historical pristine-template probes trigger the real Git commit update after real cleanup."""
    (git_repo / "pyproject.toml").write_text('[project]\nname = "template"\n', encoding="utf-8")
    replacement_project = "[project]\nname = 'replacement'\n"
    (git_repo / "temp.pyproject.toml").write_text(replacement_project, encoding="utf-8")
    replacement_readme = "the project readme\n"
    (git_repo / "README.template.md").write_text(replacement_readme, encoding="utf-8")
    real_run_git = cli.run_git
    run_git = Mock(wraps=real_run_git, side_effect=["", "867f2df", DEFAULT])
    monkeypatch.setattr(cli, "run_git", run_git)

    assert cli.cleanup(git_repo) is True
    assert run_git.call_args_list[:2] == [
        call(["status", "--porcelain"], git_repo),
        call(["rev-parse", "--short", "HEAD"], git_repo),
    ]
    wrong_repo_amend = call(["commit", "-a", "--amend", "--no-edit"])
    correct_repo_amend = call(["commit", "-a", "--amend", "--no-edit"], git_repo)
    assert run_git.call_args_list[-1] != wrong_repo_amend
    assert run_git.call_args_list[-1] == correct_repo_amend
    assert (git_repo / "README.md").read_text(encoding="utf-8") == replacement_readme
    assert (git_repo / "pyproject.toml").read_text(encoding="utf-8") == replacement_project
    assert not (git_repo / "temp.pyproject.toml").exists()


@pytest.mark.parametrize("present", [(), ("README.template.md",), ("temp.pyproject.toml",)])
def test_cleanup_requires_both_template_files(present: tuple[str, ...], tmp_path: Path) -> None:
    """Cleanup does nothing unless both template inputs identify a template checkout."""
    for name in present:
        (tmp_path / name).touch()

    assert cli.cleanup(tmp_path) is False


def test_cleanup_does_not_amend_a_dirty_template(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cleanup only rewrites the historical template commit when the checkout started clean."""
    (tmp_path / "README.template.md").write_text("readme\n", encoding="utf-8")
    (tmp_path / "temp.pyproject.toml").write_text("[project]\n", encoding="utf-8")
    run_git = Mock(side_effect=[" M README.md\n", "867f2df"])
    monkeypatch.setattr(cli, "run_git", run_git)

    assert cli.cleanup(tmp_path) is True
    assert run_git.call_args_list == [
        call(["status", "--porcelain"], tmp_path),
        call(["rev-parse", "--short", "HEAD"], tmp_path),
    ]


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
    lockfile: str | None,
    virtual_env: str | None,
    manager: str,
    monkeypatch: pytest.MonkeyPatch,
    git_repo: Path,
) -> None:
    """Lockfiles and active Poetry environments select the manager whose harness path hooks record.

    With no manager signal, install falls back to the current interpreter's pip environment.
    """
    scripts = "Scripts" if sys.platform == "win32" else "bin"
    python_name = "python.exe" if sys.platform == "win32" else "python"
    interpreter = git_repo / ".pyenv" / scripts
    poetry_bin = git_repo / ".poetry" / "virtualenvs" / "project" / scripts
    monkeypatch.setenv("UV_TESTING", "1")
    for name in tuple(os.environ):
        if name.startswith("UV_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("VIRTUAL_ENV", str(git_repo / virtual_env) if virtual_env else "")
    monkeypatch.setattr(cli.sys, "executable", str(interpreter / python_name))
    monkeypatch.setattr(cli, "which", which_finds(("timeout",)))
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


def test_windows_run_uses_powershell_without_path_lookup(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
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


def test_invalid_run_reports_every_problem(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Independent validation failures are all reported in one usage error."""
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "check_for_timeout_and_prompt", Mock(return_value=""))
    secho = Mock(wraps=cli.secho)
    monkeypatch.setattr(cli, "secho", secho)

    with pytest.raises(cli.Exit) as exit_info:
        cli.raise_issues("BOGUS", 0, 0)

    assert exit_info.value.exit_code == 2
    message = (
        "Unknown agent name 'bogus' iterations and max_minutes must be >= 1 gtimeout or timeout is required"
    )
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
    assert os.environ["RALPH_PROMPT"] == (
        "Your agent id prefix is `claude-0001`\n\ndo the most important thing"
    )

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
    assert cli.JSON.call_args_list == [
        call('{ "type" : "result" }\n', indent=None),
        call("not json\n", indent=None),
    ]
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
    assert (git_repo / "claude-args.txt").read_text(encoding="utf-8").splitlines() == [
        *preset_args,
        *preset_args,
    ]
    receipt = git_repo / "scratchpad" / "runs" / "20990102" / "claude" / "0001.jsonl"
    events = [json.loads(line) for line in receipt.read_text(encoding="utf-8").splitlines()]
    assert [event["type"] for event in events] == ["ralph", "result", "ralph", "result", "ralph"]
    assert [event.get("iteration") for event in events] == [1, None, 2, None, None]
    assert events[-1]["completed"] == 2
