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
    "complexipy": [
        "uv",
        "run",
        "--no-sync",
        "complexipy",
        ".",
        "--no-ignore",
        "--report-ignored",
        "--suggest-refactors",
        "--sort",
        "file_name",
    ],
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


def run_one_check(name: str, command: list[str], repo: Path, env: dict[str, str]) -> int:
    """Launch one check command under a phase header and return its exit code.

    The ONLY place Popen is used to run a tool. Tests fake the tool boundary by patching this
    seam by name, so no test needs to stub stdlib Popen (which would also hijack run_git).

    Args:
        name: Phase name shown in the rule header.
        command: The argv to launch.
        repo: Working directory the command runs in.
        env: Environment for the launched process.

    Returns:
        The command's exit code.
    """
    colorize(name, " ".join(command))
    sys.stdout.flush()
    with subprocess.Popen(command, cwd=repo, env=env) as process:
        return process.wait()


def call_tools(repo: Path, checks: dict[str, list[str]]) -> dict[str, list[str]]:
    """Run each named command, streaming its output live under a phase header.
    Reports what each command did and leaves the verdict to the caller.

    Args:
        repo: Working directory the checks run in.
        checks: Mapping of check name to the argv that runs it.

    Returns:
        { "pass": [...], "fail": [ problems ] } bucketing each check name by exit code.
        if anything is in "fail", a commit is not allowed
    """
    clean_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    clean_env.update({"FORCE_COLOR": "1", "CLICOLOR_FORCE": "1", "SEMGREP_FORCE_COLOR": "1"})
    results: dict[str, list[str]] = {"pass": [], "fail": []}
    for name, command in checks.items():
        exit_code = run_one_check(name, command, repo, clean_env)
        key = "pass" if exit_code == 0 or "format" in name else "fail"
        results[key].append(name)
    if os.environ.get("RALPH_LOOP"):
        results["fail"].extend(run_non_human_checks(repo))

    return results


def run_non_human_checks(repo: Path) -> list[str]:
    """Runs checks on non-humans only. Checks things that linters or other chekcs to do not check.
    Unstages files that should never be touched.

    Args:
        repo: Working directory the non-human is working in.

    Returns:
        list of problems not caught by lint, type-checking, testing
    """
    problems: list[str] = []
    staged = run_git(
        repo, ["diff", "--cached", "--name-only", "--no-renames", "--diff-filter=ACMRD"]
    ).splitlines()
    forbidden = [
        path
        for path in staged
        if path.casefold() in FORBIDDEN_FILES or path.casefold().startswith(FORBIDDEN_DIRS)
    ]
    if forbidden:
        run_git(repo, ["reset", "-q", "HEAD", "--", *forbidden])
        colorize("EJECTED", f"kept forbidden paths out of the commit: {', '.join(forbidden)}")
    problems.extend(check_for_bad_patterns(repo))
    return problems


def scan_for_banned_patterns(staged_lines: list[str]) -> list[str]:
    """Flag every banned escape-hatch pattern on an ADDED diff line (a '+' line, never a '+++' header).

    Args:
        staged_lines: Lines of `git diff --cached --unified=0` output to scan.

    Returns:
        One problem per (pattern, added line) hit; empty when the staged lines carry no banned pattern.
    """
    colorize("BANNED PATTERNS CHECK", "checking for banned patterns in staged files")
    return [
        f"'{pattern}' line: {line[1:].strip()}"
        for line in staged_lines
        for pattern in FORBIDDEN_PATTERNS
        if line.startswith("+") and not line.startswith("+++") and pattern.casefold() in line.casefold()
    ]


def check_for_user_preferences(repo: Path) -> list[str]:
    """Run the user's preferences.py over each staged (non-deleted) Python file.

    Args:
        repo: Working directory the checks run in.

    Returns:
        One problem per preference a staged .py file breaks; empty when prefs is absent or all pass.
    """
    colorize("USER PREFERENCES", "checking that user's preferences.py are respected")
    staged_python = run_git(
        repo, ["diff", "--cached", "--name-only", "--diff-filter=d", "--", "*.py"]
    ).splitlines()
    violations = (prefs(path, run_git(repo, ["show", f":{path}"])) for path in staged_python) if prefs else ()
    return [violation for violation in violations if violation]


def check_for_bad_patterns(repo: Path) -> list[str]:
    """Check staged files for banned patterns and user-preference breaks (agent-in-loop containment).
    Does not unstage anything; later, if any problem lands in { "fail": ... } the commit is blocked.

    Args:
        repo: Working directory the non-human is working in.

    Returns:
        The banned-pattern hits plus any preference violations found in the staged files.
    """
    staged_lines = run_git(repo, ["diff", "--cached", "--unified=0"]).splitlines()
    problems = scan_for_banned_patterns(staged_lines)
    if prefs and staged_lines:
        problems.extend(check_for_user_preferences(repo))
    else:
        console.print("[yellow]No files staged after preflight. Stage real work.[/]\n", justify="center")
    return problems


def run_preflight(repo: Path) -> dict[str, list[str]]:
    """Pre-commit: lint (blocking) plus an informational format report. For agents in the loop also unstages
    forbidden filepaths and flags banned patterns + human-preferences not honored.

    Args:
        repo: Repository root to inspect and run checks against.

    Returns:
        The COMMIT_CHECKS result with any containment problems appended to "fail" list.
    """
    return call_tools(repo, COMMIT_CHECKS)


def run_gate(repo: Path) -> dict[str, list[str]]:
    """Pre-push / CI: lint, types, pylint, security, pytest/hypothesis (blocking), complexipy, plus an
    informational format report.

    Args:
        repo: Repository root to run the full check suite against.

    Returns:
        The FULL_CHECKS result bucketing each check name into "pass"/"fail" lists.
    """
    return call_tools(repo, FULL_CHECKS)
