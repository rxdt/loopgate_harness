"""
1) Preflight pre-commit checks basic quality plus agent containment. `def run_preflight`

2) Full gate on staged files.
`def run_gate` mirrors what will run on Github.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

try:
    from harness.preferences import preferences_violations as prefs
except ImportError:  # humans do what they want with preferences.py
    prefs = None

# A staged file is forbidden if one of its parent dirs is here, or its exact path is in the file set.
FORBIDDEN_DIRS = {"harness", "tests/harness", ".githooks", ".github"}

FORBIDDEN_FILES = {
    "AGENTS.md",
    "pyproject.toml",
    "PROMPT.md",
    "docs/plan.md",
    "uv.lock",
    # tooling/config files that would weaken checks in pyproject.toml
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    ".coveragerc",
    "ruff.toml",
    ".ruff.toml",
    ".semgrepignore",
    "pyrightconfig.json",
    ".pylintrc",
    ".gitmodules",
}

FORBIDDEN_PATTERNS = (
    "noqa",
    "type: ignore",
    "type:ignore",
    "pyright: ignore",
    "mypy: ignore",
    "pragma: no cover",
    "eslint-disable",
    "ts-ignore",
    "ts-nocheck",
    "ts-expect-error",
    "--no-verify",
    "hooksPath",
    "pytest.mark.skip",
    "fail_under",
    "cov-fail-under",
    "pylint:",
    "pytest.mark.xfail",
)

# Tools run the way CI runs them: `uv run --no-sync <tool>` (synced venv, no per-commit resolution).
COMMIT_CHECKS = {
    "lint": ("uv", "run", "--no-sync", "ruff", "check", "."),
    "format": ("uv", "run", "--no-sync", "ruff", "format", "--check", "."),
}

FULL_CHECKS = COMMIT_CHECKS | {
    "types": ("uv", "run", "--no-sync", "pyright"),
    "pylint": ("uv", "run", "--no-sync", "pylint", "harness", "src"),
    "security": (
        "uv",
        "run",
        "--no-sync",
        "semgrep",
        "scan",
        "--config",
        "auto",
        "--config",
        "p/secrets",
        "--error",
        "--quiet",
    ),
    "tests": (
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "--cov",
        "--cov-report=term-missing",
        "--cov-fail-under=100",
    ),
}


def run_git(repo: Path, args: list[str]) -> str:
    """Run a git command in the repo and return its stdout."""
    command = ["git", "-C", str(repo)]
    command.extend(args)
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(command, capture_output=True, text=True, check=True, env=git_env)
    return result.stdout


def run_checks(repo: Path, checks: dict[str, tuple[str, ...]]) -> list[str]:
    """Run each named check command; return one failure entry per command that fails."""
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    failures: list[str] = []
    for name, command in checks.items():
        result = subprocess.run(command, cwd=repo, capture_output=True, text=True, check=False, env=git_env)
        if result.returncode != 0:
            failures.append(f"{name} failed:\n{result.stdout}{result.stderr}")
    return failures


def run_preflight(repo: Path) -> list[str]:
    """Pre-commit: fast lint/format for everyone. For agents in the loop also drop forbidden staged filepaths
    and flag banned patterns + human-preference breaks."""
    problems: list[str] = []
    if os.environ.get("RALPH_LOOP"):
        staged = set(
            run_git(
                repo, ["diff", "--cached", "--name-only", "--no-renames", "--diff-filter=ACMRD"]
            ).splitlines()
        )
        forbidden = (staged & FORBIDDEN_FILES) | {
            f for f in staged if not FORBIDDEN_DIRS.isdisjoint(str(p) for p in PurePosixPath(f).parents)
        }
        if forbidden:
            dropped = sorted(forbidden)
            reset = ["reset", "-q", "HEAD", "--"]
            reset.extend(dropped)
            run_git(repo, reset)
            sys.stderr.write("harness kept forbidden paths out of the commit: " + ", ".join(dropped) + "\n")
        added = run_git(repo, ["diff", "--cached", "--unified=0"]).splitlines()
        problems = [
            f"banned pattern '{pattern}' in line: {line[1:].strip()}"
            for line in added
            if line.startswith("+") and not line.startswith("+++")
            for pattern in FORBIDDEN_PATTERNS
            if pattern.casefold() in line.casefold()  # case-insensitive so mixed-case matches are caught
        ]
        if prefs is not None:
            for path in sorted(staged):
                if path.endswith(".py") and (repo / path).is_file():  # is_file skips staged deletions
                    problems.extend(prefs(path, (repo / path).read_text(encoding="utf-8")))
    problems.extend(run_checks(repo, COMMIT_CHECKS))
    return problems


def run_gate(repo: Path) -> list[str]:
    """Pre-push / CI: lint, format, types, pylint, security, tests."""
    return run_checks(repo, FULL_CHECKS)
