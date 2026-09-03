"""Only hardcoded values go in here and only with explicit permission. This is a no-bloat zone."""

from __future__ import annotations

from importlib.metadata import distribution
from pathlib import Path
from typing import Any

from harness.gate import gates

site_packages = Path(str(distribution("loopgate").locate_file("")))
package_root = site_packages / "harness"
repo_root = gates().repo_root


def get_tools(paths: set[str]) -> dict[str, dict[str, Any]]:
    """Sets user paths in tool CLI args and pyproject tables.

    Args:
        paths: the source code paths the repo wants checked

    Returns:
        tools dictionary with generic configurations and user's source code paths injected
    """
    if paths:
        source = paths
        unique: list[str] = list(source | {"preferences", "mutation"})
    else:
        source = {"."}
        unique = list(source)
    return {
        "audit": {"category": "audit", "args": ["pip-audit"]},
        "bandit": {
            "category": "security",
            "filenames": [".bandit"],
            "pyproject": ["bandit"],
            "args": ["bandit", "-r", *source, "-x", "build,tox,docs,tests,.venv,scratchpad,mutants,tests"],
        },
        "ruff_format": {
            "category": "format",
            "filenames": [".ruff.toml", "ruff.toml"],
            "pyproject": ["ruff", "format"],
            "args": ["ruff", "format", "--no-cache", "--check", *source],
        },
        "ruff_lint": {
            "category": "lint",
            "filenames": [".ruff.toml", "ruff.toml"],
            "pyproject": ["ruff", "lint"],
            "args": ["ruff", "check", "--no-cache", "--show-fixes", *source],
        },
        "black": {"category": "format", "pyproject": ["black"], "args": ["black", "--check", *source]},
        "coverage": {
            "category": "test",
            "filenames": [".coveragerc", ".coveragerc.toml"],
            "pyproject": ["coverage", "run"],
            "table": {"run": {"source": unique}},
        },
        "flake8": {"category": "lint", "filenames": [".flake8"], "args": ["flake8", *source]},
        "hypothesis": {"category": "test", "pyproject": ["hypothesis"]},
        "pytest": {
            "category": "test",
            "filenames": ["pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini"],
            "pyproject": ["pytest", "ini_options"],
            "args": [
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
        },
        "pyright": {
            "category": "types",
            "filenames": ["pyrightconfig.json"],
            "pyproject": ["pyright"],
            "args": ["pyright", "--outputjson", *source],
            "table": {"include": list(source)},
        },
        "pylint": {
            "category": "lint",
            "filenames": ["pylintrc", "pylintrc.toml", ".pylintrc", ".pylintrc.toml"],
            "pyproject": ["pylint"],
            "args": ["pylint", *source],
        },
        "mypy": {
            "category": "types",
            "filenames": ["mypy.ini", ".mypy.ini"],
            "pyproject": ["mypy"],
            "args": ["mypy", *source],
        },
        "mutmut": {"category": "test", "pyproject": ["mutmut"], "table": {"source_paths": list(source - {"mutation"})}},
        "radon": {
            "category": "complexity",
            "filenames": ["radon.cfg"],
            "pyproject": ["radon"],
            "args": [
                "radon",
                "cc",
                *source,
                "-s",
                "-a",
                "-i",
                "build,tox,docs,tests,.venv,scratchpad,mutants,tests",
                "-e",
                "**/__init__.py.",
            ],
        },
        "safety": {"category": "audit", "args": ["safety", "scan"]},
        "sonarqube": {
            "category": "security",
            "filenames": ["sonar-project.properties"],
            "args": ["sonar-scanner", "-Dsonar.qualitygate.wait=true"],
        },
        "snyk": {"category": "security", "filenames": [".snyk"], "args": ["snyk", "test"]},
        "ty": {
            "category": "types",
            "filenames": [
                "ty.toml",
                "~/.config/ty/ty.toml",
                "$XDG_CONFIG_HOME/ty/ty.toml",  # Linux/macOS
                "%APPDATA%\\ty\\ty.toml",  # Windows
            ],
            "pyproject": ["ty"],
            "args": ["ty", "check", *source],
        },
        "complexipy": {
            "category": "complexity",
            "filenames": ["complexipy.toml", ".complexipy.toml"],
            "pyproject": ["complexipy"],
            "args": ["complexipy", *source],
            "table": {"paths": list(source)},
        },
        "semgrep": {
            "category": "security",
            "filenames": [
                ".semgrep.yml",
                ".semgrep.yaml",
                "semgrep.yml",
                "semgrep.yaml",
                "semgrep.config.yml",
                "semgrep.config.yaml",
            ],
            "args": [
                "semgrep",
                "scan",
                "--no-error",
                "--config",
                "auto",
                "--config",
                "p/secrets",
                "--exclude-rule",
                "yaml.github-actions.security.github-actions-mutable-action-tag.github-actions-mutable-action-tag",
                *source,
            ],
        },
        "pyrefly": {
            "category": "types",
            "filenames": ["pyrefly.toml", ".pyrefly.toml"],
            "pyproject": ["pyrefly"],
            "args": ["pyrefly", "check", *source],
        },
        "xenon": {
            "category": "complexity",
            "filenames": [".xenon.yml"],
            "args": ["xenon", "--max-absolute", "B", "--max-modules", "A", "--max-average", "A", *source],
        },
        "zuban": {
            "category": "types",
            "filenames": [".zuban.toml"],
            "pyproject": ["zuban"],
            "args": ["zuban", "check", *source],
        },
    }


CATEGORIES: dict[str, str] = {
    "audit": "audit",
    "complexity": "complexity",
    "format": "ruff_format",
    "lint": "ruff_lint",
    "security": "security",
    "test": "test",
    "types": "types",
}

PHASES: dict[str, dict[str, list[str]]] = {
    "BLOCKING BEHAVIOR SETTING": {
        "Checks block if [red]`fail`[/] set\n  Pass and warn if [yellow]`warn`[/] set": [
            gates().settings.get("behavior")
        ]
    },
    "preflight": gates().commit_checks,
    "gate": gates().gate,
}

CODEX_RULES = """\
prefix_rule(
    pattern = ["git", ["push", "commit"], ["--no-verify", "-n"]],
    decision = "forbidden",
    justification = "Run git commands without hook-bypass flags.",
)
prefix_rule(
    pattern = [["unset", "unsetenv"], "RALPH_LOOP"],
    decision = "forbidden",
    justification = "Keep RALPH_LOOP=1 so harness containment remains active.",
)
prefix_rule(
    pattern = ["env", "-u", "RALPH_LOOP"],
    decision = "forbidden",
    justification = "Keep RALPH_LOOP=1 so harness containment remains active.",
)
prefix_rule(
    pattern = [
        ["bash", "/bin/bash", "zsh", "/bin/zsh", "sh", "/bin/sh"],
        ["-c", "-lc"],
        ["RALPH_LOOP=0", "export RALPH_LOOP=0"],
    ],
    decision = "forbidden",
    justification = "Keep RALPH_LOOP=1 so harness containment remains active.",
)
"""

CLAUDE_RULES: set[str] = {
    "Bash(*git push*--no-verify*)",
    "Bash(*git commit*--no-verify*)",
    "Bash(*git push* -n*)",
    "Bash(*git commit* -n*)",
    "Bash(*unset RALPH_LOOP*)",
    "Bash(*env -u RALPH_LOOP*)",
    "Bash(*unsetenv RALPH_LOOP*)",
    "Bash(*RALPH_LOOP=0*)",
    "Bash(*export RALPH_LOOP=0*)",
}

ASSETS: dict[str, tuple[Path, Path]] = {
    "docs": (package_root / "docs", repo_root / "docs"),
    "githooks": (package_root / ".githooks", repo_root / ".githooks"),
    "scratchpad": (package_root / "scratchpad/runs/.gitkeep", repo_root / "scratchpad/runs/.gitkeep"),
    "preferences": (site_packages / "preferences/preferences.py", repo_root / "preferences/preferences.py"),
    "mutation": (site_packages / "mutation/check_mutmut.py", repo_root / "mutation/check_mutmut.py"),
    "tests/preferences": (
        package_root / "tests/preferences/test_preferences.py",
        repo_root / "tests/preferences/test_preferences.py",
    ),
    "tests/mutation": (
        package_root / "tests/mutation/test_check_mutmut.py",
        repo_root / "tests/mutation/test_check_mutmut.py",
    ),
}
