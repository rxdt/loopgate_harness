from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import tomlkit
from typer.testing import CliRunner

from harness import cli
from harness.gate import gates
from harness.tests.conftest import REPO_ROOT

runner = CliRunner()
TEMPLATE: dict[str, Any] = tomlkit.parse(
    (REPO_ROOT / "harness" / "temp.pyproject.toml").read_text(encoding="utf-8")
).unwrap()["tool"]
TOOLS = cli.get_tools([])
MODULES = frozenset(TOOLS)


def configure_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pyproject: str = "",
    tox_ini: str = "",
    setup_cfg: str = "",
    root_files: tuple[str, ...] = (),
    modules: frozenset[str] = MODULES,
    executables: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Run write_harness_config in tmp_path with tool discovery pinned, returning the written [tool] table.

    Args:
        tmp_path: stands in for the repository root init runs from.
        monkeypatch: pins REPO_ROOT and tool discovery for one test.
        pyproject: the user's pyproject.toml text, written when non-empty.
        tox_ini: the user's tox.ini text, written when non-empty.
        setup_cfg: the user's setup.cfg text, written when non-empty.
        root_files: standalone config files created empty; detection reads only their existence.
        modules: importable module names; find_spec resolves exactly these.
        executables: names on PATH; which resolves exactly these.

    Returns:
        The [tool] table of the pyproject.toml that write_harness_config wrote.
    """
    for name, text in ("pyproject.toml", pyproject), ("tox.ini", tox_ini), ("setup.cfg", setup_cfg):
        if text:
            (tmp_path / name).write_text(text, encoding="utf-8")
    for name in root_files:
        (tmp_path / name).write_text("", encoding="utf-8")

    def fake_find_spec(tool: str) -> str | None:
        return tool if tool in modules else None

    def fake_which(name: str) -> str | None:
        return name if name in executables else None

    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli.util, "find_spec", fake_find_spec)
    monkeypatch.setattr(cli, "which", fake_which)
    cli.write_harness_config(TOOLS)
    return tomlkit.parse((tmp_path / "pyproject.toml").read_text(encoding="utf-8")).unwrap()["tool"]


def test_mypy_ini_replaces_the_types_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, root_files=("mypy.ini",))
    assert written["harness"]["gate"]["types"] == TOOLS["mypy"]["args"]


def test_black_table_replaces_the_format_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, pyproject="[tool.black]\n")
    assert written["harness"]["preflight"]["black"] == TOOLS["black"]["args"]
    assert "ruff_format" not in written["harness"]["preflight"]


def test_flake8_section_replaces_the_lint_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, setup_cfg="[flake8]\n")
    assert written["harness"]["preflight"]["flake8"] == TOOLS["flake8"]["args"]
    assert "ruff_lint" not in written["harness"]["preflight"]


def test_bandit_file_replaces_the_security_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, root_files=(".bandit",))
    assert written["harness"]["gate"]["security"] == TOOLS["bandit"]["args"]


def test_additional_type_checker_gets_named_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, root_files=("pyrightconfig.json", "mypy.ini"))
    assert written["harness"]["gate"]["types"] == TOOLS["pyright"]["args"]
    assert written["harness"]["gate"]["mypy"] == TOOLS["mypy"]["args"]


def test_additional_formatter_gets_named_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, pyproject="[tool.black]\n[tool.ruff]\n")
    assert written["harness"]["preflight"]["ruff_format"] == TOOLS["ruff_format"]["args"]
    assert written["harness"]["preflight"]["black"] == TOOLS["black"]["args"]


def test_additional_linter_gets_named_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, setup_cfg="[flake8]\n", root_files=("ruff.toml",))
    assert written["harness"]["preflight"]["ruff_lint"] == TOOLS["ruff_lint"]["args"]
    assert written["harness"]["preflight"]["flake8"] == TOOLS["flake8"]["args"]


def test_additional_complexity_tool_gets_named_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, root_files=("complexipy.toml", ".xenon.yml"))
    assert written["harness"]["preflight"]["complexity"] == TOOLS["complexipy"]["args"]
    assert written["harness"]["preflight"]["xenon"] == TOOLS["xenon"]["args"]


def test_primary_pylint_does_not_leave_duplicate_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, root_files=(".pylintrc",))
    assert written["harness"]["preflight"]["pylint"] == TOOLS["pylint"]["args"]
    assert "ruff_lint" not in written["harness"]["preflight"]


def test_mypy_replacement_drops_pyright_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, root_files=("mypy.ini",))
    assert "pyright" not in written, "mypy replaced the pyright check but left [tool.pyright] behind"


@pytest.mark.parametrize(
    ("file_name", "table"),
    [
        pytest.param("pyrightconfig.json", "pyright", id="pyrightconfig"),
        pytest.param(".pytest.ini", "pytest", id="pytest-ini"),
        pytest.param("ruff.toml", "ruff", id="ruff-toml"),
        pytest.param(".pylintrc", "pylint", id="pylintrc"),
        pytest.param("complexipy.toml", "complexipy", id="complexipy-toml"),
    ],
)
def test_standalone_config_drops_packaged_table(
    file_name: str, table: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written = configure_repo(tmp_path, monkeypatch, root_files=(file_name,))
    assert table not in written, f"{file_name} is authoritative but template [tool.{table}] was written"
    assert cli.find_configured_command(
        table, {"args": [table], "filenames": [file_name]}, [table, f"tool:{table}"], {table: {}}, False
    ) == ([table], False), f"{file_name} did not take precedence over pyproject.toml and INI configuration"


def test_user_pyproject_table_is_preserved_exactly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, pyproject="[tool.pylint]\njobs = 2\n")
    assert written["pylint"] == {"jobs": 2}, "the user's [tool.pylint] was not preserved exactly"
    assert written["harness"]["preflight"]["pylint"] == TOOLS["pylint"]["args"]
    assert "ruff_lint" not in written["harness"]["preflight"]
    assert cli.find_configured_command("pylint", TOOLS["pylint"], ["pylint"], written, False) == (
        TOOLS["pylint"]["args"],
        True,
    ), "pyproject.toml did not take precedence over INI configuration"


def test_tox_ini_pytest_section_selects_pytest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, tox_ini="[pytest]\n")
    assert written["harness"]["gate"]["test"] == TOOLS["pytest"]["args"]


def test_setup_cfg_pytest_section_drops_packaged_pytest_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, setup_cfg="[tool:pytest]\n")
    assert "pytest" not in written, "setup.cfg [tool:pytest] is authoritative but [tool.pytest] was written"


def test_testenv_with_tox_available_selects_tox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(
        tmp_path, monkeypatch, tox_ini="[tox]\n[testenv]\n", setup_cfg="[tool:pytest]\n", executables=frozenset({"tox"})
    )
    assert written["harness"]["gate"]["test"] == ["tox"], "tox.ini did not take precedence over setup.cfg"


def test_testenv_without_tox_keeps_the_init_pytest_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, tox_ini="[tox]\n[testenv]\n")
    assert written["harness"]["gate"]["test"] == TOOLS["pytest"]["args"]
    assert written["pytest"] == TEMPLATE["pytest"]


def test_init_uses_lenient_coverage_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch)
    assert written["coverage"] == {
        "run": {"source": ["."]},
        "report": {
            "show_missing": True,
            "skip_covered": False,
            "fail_under": TEMPLATE["coverage"]["report"]["fail_under"],
        },
        "skip_covered": False,
    }
    assert set(written) == {  # table keys for new toml
        "complexipy",
        "coverage",
        "harness",
        "hatch",
        "hypothesis",
        "mutmut",
        "pyright",
        "pytest",
        "ruff",  # no pylint present
    }
    assert "pylint" not in written["harness"]["preflight"]
    assert "pylint" not in written
    assert written["harness"]["gate"]["test"] == TOOLS["pytest"]["args"]


def test_user_coverage_threshold_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, pyproject="[tool.coverage.report]\nfail_under = 80\n")
    assert written["coverage"]["report"]["fail_under"] == 80


def test_explicit_pytest_config_beats_tox_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(
        tmp_path, monkeypatch, tox_ini="[tox]\n[testenv]\n[pytest]\n", executables=frozenset({"tox"})
    )
    assert written["harness"]["gate"]["test"] == TOOLS["pytest"]["args"]


def test_testenv_ruff_section_is_not_ruff_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, tox_ini="[tox]\n[testenv:ruff]\n")
    assert written["harness"]["preflight"]["ruff_lint"] == TEMPLATE["harness"]["preflight"]["ruff_lint"]


def test_unavailable_tool_loses_check_and_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, modules=MODULES - {"pylint"})
    assert "pylint" not in written["harness"]["preflight"], "pylint cannot run but its check remains"
    assert "pylint" not in written, "pylint cannot run but [tool.pylint] was still written"


def test_available_audit_module_keeps_the_audit_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch)  # pip-audit is not on PATH; the module name resolves
    assert written["harness"]["gate"]["audit"] == TEMPLATE["harness"]["gate"]["audit"]


def test_unavailable_audit_removes_the_audit_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, modules=MODULES - {"audit"})
    assert "audit" not in written["harness"]["gate"], "pip-audit cannot run but its check remains"


def test_untouched_checks_keep_exact_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written = configure_repo(tmp_path, monkeypatch, root_files=("mypy.ini",))
    for stage in ("preflight", "gate"):
        if stage == "gate":
            assert len(written["harness"][stage].items()) == 4
            for name, argv in written["harness"][stage].items():
                if name != "types":
                    expected = TOOLS["pytest"]["args"] if name == "test" else TEMPLATE["harness"][stage][name]
                    assert argv == expected, f"{name} changed but only types was replaced"
                else:
                    assert argv[0] == "mypy"
        if stage == "preflight":
            assert len(written["harness"][stage].items()) == 3
        assert "pyright" not in written["harness"][stage]


def test_init_writes_detected_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One CLI smoke run: init succeeds, rewrites pyproject.toml in place, and delegates detection."""
    pytest_ini = (
        "[pytest]\n"
        "testpaths       = tests/unittests/\n"
        "addopts         = --ff --show-capture=stderr --maxfail 5 --cov=hubblestack "
        "--cov-report=html:tests/unittests/output/coverage\n"
        "log_cli         = no\n"
        "log_cli_level   = CRITICAL\n"
        "log_cli_format  = %(asctime)s %(name)17s %(levelname)5s %(message)s\n"
        "log_date_format = %H:%M:%S\n"
        "\n"
        "filterwarnings  =\n"
        "    ignore::urllib3.exceptions.InsecureRequestWarning\n"
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'mine'\n[tool.black]\n", encoding="utf-8")
    (tmp_path / "mypy.ini").write_text("", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text(pytest_ini, encoding="utf-8")
    monkeypatch.setattr(gates(), "repo_root", tmp_path)
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "REPO_ROOT_STR", str(tmp_path))
    monkeypatch.setattr(cli, "hoist", Mock(return_value=False))
    monkeypatch.setattr(cli, "setup_git_hooks", Mock(return_value=tmp_path / "harness-path"))
    monkeypatch.setattr(cli, "check_for_timeout_and_prompt", Mock(return_value="timeout"))
    monkeypatch.setattr(cli, "configure_agents", Mock(return_value=True))
    monkeypatch.setattr(cli.util, "find_spec", TOOLS.get)
    monkeypatch.setattr(cli, "which", Mock(return_value=None))

    result = runner.invoke(cli.app, ["init"], input="y\nletta_evals\n" + "y\n" * 4)

    assert result.exit_code == 0, result.output
    written = tomlkit.parse((tmp_path / "pyproject.toml").read_text(encoding="utf-8")).unwrap()
    assert written["project"] == {"name": "mine"}
    assert written["tool"]["harness"]["preflight"] == {
        "ruff_lint": ["ruff", "check", "--no-cache", "--show-fixes", "letta_evals"],
        "complexity": ["complexipy", "letta_evals"],
        "black": ["black", "--check", "letta_evals"],
    }
    assert written["tool"]["harness"]["gate"] == {
        "audit": ["pip-audit"],
        "security": [
            "semgrep",
            "scan",
            "--no-error",
            "--config",
            "auto",
            "--config",
            "p/secrets",
            "--exclude-rule",
            "yaml.github-actions.security.github-actions-mutable-action-tag.github-actions-mutable-action-tag",
            "letta_evals",
        ],
        "types": ["mypy", "letta_evals"],
        "test": [
            "pytest",
            "-p",
            "no:cacheprovider",
            "-n",
            "auto",
            "--cov",
            "--cov-report=term-missing",
            "--cov-fail-under=25",
            "--durations=5",
        ],
    }
    written["tool"]["coverage"]["run"]["source"].sort()
    assert written["tool"]["coverage"] == {
        "run": {"source": ["letta_evals", "mutation", "preferences"]},
        "report": {"show_missing": True, "skip_covered": False, "fail_under": 25},
        "skip_covered": False,
    }
    assert written["tool"]["mutmut"] == {
        "max-children": 2,
        "source_paths": ["letta_evals"],
        "also_copy": ["mutation", ".githooks"],
    }
    assert written["tool"]["complexipy"] == {
        "paths": ["letta_evals"],
        "exclude": ["**/tests/**"],
        "max-complexity-allowed": 30,
        "no-ignore": False,
        "report-ignored": True,
        "failed": True,
        "sort": "asc",
        "quiet": False,
        "ignore-complexity": False,
    }
    assert (tmp_path / "pytest.ini").read_text(encoding="utf-8") == pytest_ini
    parsed_pytest_ini = cli.ConfigParser(interpolation=None)
    parsed_pytest_ini.read_string(pytest_ini)
    pytest_settings = parsed_pytest_ini["pytest"]
    assert pytest_settings == {
        "testpaths": "tests/unittests/",
        "addopts": (
            "--ff --show-capture=stderr --maxfail 5 --cov=hubblestack --cov-report=html:tests/unittests/output/coverage"
        ),
        "log_cli": "no",
        "log_cli_level": "CRITICAL",
        "log_cli_format": "%(asctime)s %(name)17s %(levelname)5s %(message)s",
        "log_date_format": "%H:%M:%S",
        "filterwarnings": "\nignore::urllib3.exceptions.InsecureRequestWarning",
    }
