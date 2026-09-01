"""Only hardcoded values go in here and only with explicit permission. This is a no-bloat zone."""

from __future__ import annotations

from importlib.metadata import distribution, packages_distributions
from pathlib import Path
from typing import Any

from harness.gate import gates

distribution_name = packages_distributions()["harness"][0]
site_packages = Path(str(distribution(distribution_name).locate_file("")))
package_root = site_packages / "harness"
repo_root = gates().repo_root

CATEGORIES: dict[str, str] = {
    "audit": "audit",
    "complexity": "complexity",
    "format": "ruff_format",
    "lint": "ruff_lint",
    "security": "security",
    "test": "test",
    "types": "types",
}

PHASES = (
    ("agents", gates().agents),
    ("preflight", gates().commit_checks),
    ("gate", gates().gate_checks),
    ("forbidden", gates().forbidden),
)

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

TOOLS: dict[str, dict[str, Any]] = {
    "audit": {"category": "audit", "args": gates().gate_checks["audit"]},
    "bandit": {
        "category": "security",
        "filenames": [".bandit"],
        "pyproject": ["bandit"],
        "args": ["bandit", "-r", ".", "-x", "build,tox,docs,tests,.venv,scratchpad,mutants,tests"],
    },
    "ruff_format": {
        "category": "format",
        "filenames": [".ruff.toml", "ruff.toml"],
        "pyproject": ["ruff", "format"],
        "args": gates().commit_checks["ruff_format"],
    },
    "ruff_lint": {
        "category": "lint",
        "filenames": [".ruff.toml", "ruff.toml"],
        "pyproject": ["ruff", "lint"],
        "args": gates().commit_checks["ruff_lint"],
    },
    "black": {"category": "format", "pyproject": ["black"], "args": ["black", "--check", "."]},
    "coverage": {
        "category": "test",
        "filenames": [".coveragerc", ".coveragerc.toml"],
        "pyproject": ["coverage"],
    },
    "flake8": {"category": "lint", "filenames": [".flake8"], "args": ["flake8", "."]},
    "hypothesis": {"category": "test", "pyproject": ["hypothesis"]},
    "lint": {"category": "lint", "pyproject": ["lint"]},
    "pytest": {
        "category": "test",
        "filenames": ["pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini"],
        "pyproject": ["pytest", "ini_options"],
        "args": gates().gate_checks["test"],
    },
    "pyright": {
        "category": "types",
        "filenames": ["pyrightconfig.json"],
        "pyproject": ["pyright"],
        "args": gates().gate_checks["types"],
    },
    "pylint": {
        "category": "lint",
        "filenames": ["pylintrc", "pylintrc.toml", ".pylintrc", ".pylintrc.toml"],
        "pyproject": ["pylint"],
        "args": gates().commit_checks["pylint"],
    },
    "mypy": {
        "category": "types",
        "filenames": ["mypy.ini", ".mypy.ini"],
        "pyproject": ["mypy"],
        "args": ["mypy", "."],
    },
    "mutmut": {"category": "test", "pyproject": ["mutmut"]},
    "radon": {
        "category": "complexity",
        "filenames": ["radon.cfg"],
        "pyproject": ["radon"],
        "args": [
            "radon",
            "cc",
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
        "args": ["ty", "check"],
    },
    "complexipy": {
        "category": "complexity",
        "filenames": ["complexipy.toml", ".complexipy.toml"],
        "pyproject": ["complexipy"],
        "args": gates().commit_checks["complexity"],
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
        "args": gates().gate_checks["security"],
    },
    "pyrefly": {
        "category": "types",
        "filenames": ["pyrefly.toml", ".pyrefly.toml"],
        "pyproject": ["pyrefly"],
        "args": ["pyrefly", "check"],
    },
    "xenon": {
        "category": "complexity",
        "filenames": [".xenon.yml"],
        "args": ["xenon", "--max-absolute", "B", "--max-modules", "A", "--max-average", "A", "."],
    },
    "zuban": {
        "category": "types",
        "filenames": [".zuban.toml"],
        "pyproject": ["zuban"],
        "args": ["zuban", "check"],
    },
}

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
    "preferences": (site_packages / "preferences", repo_root / "preferences"),
    "mutation": (site_packages / "mutation", repo_root / "mutation"),
    "pref_tests": (package_root / "tests/preferences", repo_root / "tests/preferences"),
    "mutation_tests": (package_root / "tests/mutation", repo_root / "tests/mutation"),
}
