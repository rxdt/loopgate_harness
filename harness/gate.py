"""1) Preflight pre-commit checks basic quality plus agent containment. `def run_preflight`

2) Full gate on staged files.
`def run_gate` mirrors what will run on Github.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console

console = Console(force_terminal=True)
try:
    from harness.preferences import preferences_violations as prefs
except ImportError:  # humans do what they want with preferences.py
    prefs = None

# A staged file is forbidden if one of its parent dirs is here, or its exact path is in the file set.
FORBIDDEN_DIRS = ("harness/", "tests/harness/", ".githooks/", ".github/")

FORBIDDEN_FILES = {
    "agents.md",  # .casefold() for comparison to staged files
    "pyproject.toml",
    "prompt.md",  # .casefold() for comparison to staged files
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

FORBIDDEN_PATTERNS = {
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
    "hookspath",
    "pytest.mark.skip",
    "fail_under",
    "cov-fail-under",
    "pylint:",
    "pytest.mark.xfail",
}  # already .casefold() for easier comparison

# Tools run the way CI runs them. These are the real gates: each one blocks when it fails.
COMMIT_CHECKS = {
    "ruff lint": ["uv", "run", "--no-cache", "--no-sync", "ruff", "check", "--show-fixes", "."],
    "ruff format (no fail)": ["uv", "run", "--no-sync", "ruff", "format", "--check"],
}
FULL_CHECKS = COMMIT_CHECKS | {
    "types": ["uv", "run", "--no-sync", "pyright", "--outputjson"],
    "pylint": ["uv", "run", "--no-sync", "pylint", "src", "harness"],
    "security": [
        "uv",
        "run",
        "--no-sync",
        "semgrep",
        "scan",
        "--config",
        "auto",
        "--config",
        "p/secrets",
        "--exclude-rule",
        "yaml.github-actions.security.github-actions-mutable-action-tag.github-actions-mutable-action-tag",
        ".",
    ],
    "pytest": [
        "uv",
        "run",
        "--no-cache",
        "--no-sync",
        "pytest",
        "--cov",
        "--cov-report=term-missing",
        "--cov-fail-under=100",
    ],
}


def run_git(repo: Path, args: list[str]) -> str:
    """Run a git command in the repo and return its stdout.

    Args:
        repo: Repository root passed to git via -C.
        args: Git subcommand and its arguments.

    Returns:
        The command's raw stdout string (callers .splitlines() as needed).
    """
    command = ["git", "-C", str(repo)]
    command.extend(args)
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(command, capture_output=True, text=True, check=True, env=git_env)
    return result.stdout


def colorize(name: str, command: str) -> None:
    """Rich consosle printing to signpost checks.

    Args:
        name: Phase name shown in the rule header.
        command: The command string printed beneath the header.
    """
    console.rule(f"[bold cyan] PHASE: {name.upper()}[/]", style="blink cyan on grey15")
    console.print(f"[dim italic]{command}[/dim italic]\n", justify="center")


def run_checks(repo: Path, checks: dict[str, list[str]]) -> dict[str, list[str]]:
    """Run each named command, streaming its output live under a phase header.
    Reports what each command did and leaves the verdict to the caller.

    Args:
        repo: Working directory the checks run in.
        checks: Mapping of check name to the argv that runs it.

    Returns:
        {"pass": [...], "fail": [...]} bucketing each check name by exit code.
    """
    clean_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    clean_env.update({"FORCE_COLOR": "1", "CLICOLOR_FORCE": "1", "SEMGREP_FORCE_COLOR": "1"})
    results: dict[str, list[str]] = {"pass": [], "fail": []}
    for name, command in checks.items():
        colorize(name, " ".join(command))
        sys.stdout.flush()
        with subprocess.Popen(command, cwd=repo, env=clean_env) as process:
            exit_code = process.wait()
            key = "pass" if exit_code == 0 or "format" in name else "fail"
            results[key].append(name)
    if os.environ.get("RALPH_LOOP"):
        results["fail"].extend(run_non_human_checks(repo))

    return results


def run_non_human_checks(repo: Path) -> list[str]:
    """Runs checks on non-humans only. Checks things that linters or other chekcs to do not check.
    Calls the human `preferences` checks and ensures non-empty commit is committed. Unstages

    Args:
        repo: Working directory the non-human is working in.

    Returns:
        list of problems not caught by lint, type-checking, testing
    """
    problems = []
    staged = run_git(
        repo, ["diff", "--cached", "--name-only", "--no-renames", "--diff-filter=ACMRD"]
    ).splitlines()
    lower_to_upper_file_map = {sf.casefold(): sf for sf in staged}
    lower_set = set(lower_to_upper_file_map)
    forbidden = lower_set & FORBIDDEN_FILES | {f for f in lower_set if f.startswith(FORBIDDEN_DIRS)}
    if forbidden:
        unstage = ["reset", "-q", "HEAD", "--"] + [lower_to_upper_file_map[f] for f in forbidden]
        run_git(repo, unstage)
        colorize("EJECTED", f"kept forbidden paths out of the commit: {', '.join(forbidden)}")
    remaining_staged_lines = run_git(repo, ["diff", "--cached", "--unified=0"]).splitlines()
    colorize("BANNED PATTERNS CHECK", "checking for banned patterns in staged files")
    for line in remaining_staged_lines:
        problems.extend(
            f"'{pattern}' line: {line[1:].strip()}"
            for pattern in FORBIDDEN_PATTERNS
            if line.startswith("+") and not line.startswith("+++") and pattern in line.casefold()
        )
    if prefs and remaining_staged_lines:
        colorize("USER PREFERENCES", "checking that user's preferences.py are respected")
        remaining = run_git(
            repo, ["diff", "--cached", "--name-only", "--diff-filter=d", "--", "*.py"]
        ).splitlines()
        for path in remaining:
            violation = prefs(path, run_git(repo, ["show", f":{path}"]))
            if violation:
                problems.append(violation)
    else:
        console.print("[yellow]No files staged after preflight. Stage real work.[/]\n", justify="center")
    return problems


def run_preflight(repo: Path) -> dict[str, list[str]]:
    """Pre-commit: fast lint (blocking) plus an informational format report for everyone. For agents
    in the loop also drop forbidden staged filepaths and flag banned patterns + human-preference breaks.

    Args:
        repo: Repository root to inspect and run checks against.

    Returns:
        The COMMIT_CHECKS result with any containment problems appended to "fail".
    """
    return run_checks(repo, COMMIT_CHECKS)


def run_gate(repo: Path) -> dict[str, list[str]]:
    """Pre-push / CI: lint, types, pylint, security, tests (blocking), plus an informational format report.

    Args:
        repo: Repository root to run the full check suite against.

    Returns:
        The FULL_CHECKS result bucketing each check name into "pass"/"fail".
    """
    return run_checks(repo, FULL_CHECKS)
