"""1) Preflight pre-commit checks basic quality plus agent containment. `def run_preflight`

2) Full gate on staged files.
`def run_gate` mirrors what will run on Github (CI runs this same `harness gate`).

All containment lists and check commands come from [tool.harness] in pyproject.toml, read once at
import into the constants below. A check is a (name, argv) pair; its `preflight`/`blocking` flags sort
it into the maps and sets this module runs on. Nothing is hardcoded here.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import typer
from rich.console import Console

console = Console(force_terminal=True)
try:
    from harness.preferences import preferences_violations as prefs
except ImportError:  # humans do what they want with preferences.py
    prefs = None

REPO_ROOT = Path(__file__).resolve().parents[1]

raw_toml = tomllib.loads((REPO_ROOT / "pyproject.toml").read_bytes().decode())
harness = raw_toml.get("tool", {}).get("harness", {})
gate = harness.get("gate", {})
AGENTS = harness.get("agents", {})
COMMIT_CHECKS = harness.get("preflight", {})
FULL_CHECKS = COMMIT_CHECKS | gate
FORBIDDEN = harness.get("FORBIDDEN", {})
FORBIDDEN_FILES = FORBIDDEN.get("FILES", [])
FORBIDDEN_DIRS = tuple(FORBIDDEN.get("DIRS", []))
FORBIDDEN_PATTERNS = FORBIDDEN.get("PATTERNS", [])


def run_git(args: list[str], check: bool = True) -> str:
    """Run a git command in the repo and return its stdout.

        Args:
            args: Git subcommand and its arguments.
            check: If check is True and the exit code was non-zero, it raises a
    CalledProcessError which has returncode attribute, and output attribute

        Returns:
            The command's raw stdout string (callers .splitlines() as needed).
    """
    command = ["git", "-C", str(REPO_ROOT)]
    command.extend(args)
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result = subprocess.run(command, capture_output=True, text=True, check=check, env=git_env)
    return result.stdout


def colorize(name: str, command: str) -> None:
    """Rich consosle printing to signpost checks.

    Args:
        name: Phase name shown in the rule header.
        command: The command string printed beneath the header.
    """
    if os.environ.get("RALPH_LOOP"):  # loop agents get plain text (no ANSI)
        typer.echo(f"PHASE: {name.upper()}")
        typer.echo(command)
    else:
        console.rule(f"[bold cyan] PHASE: {name.upper()}[/]", style="blink cyan on grey15")
        console.print(f"[dim italic]{command}[/dim italic]\n", justify="center")


def run_checks(checks: dict[str, list[str]]) -> dict[str, list[str]]:
    """Run each named command, streaming its output live under a phase header.
    Reports what each command did and leaves the verdict to the caller.

    Args:
        checks: Mapping of check name to the argv that runs it.

    Returns:
        { "pass": [...], "warn": [...], "fail": [ problems ] } bucketing each check name by exit code.
        if anything is in "fail", a commit is not allowed
    """
    clean_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    if not os.environ.get("RALPH_LOOP"):
        clean_env.update({"FORCE_COLOR": "1", "CLICOLOR_FORCE": "1", "SEMGREP_FORCE_COLOR": "1"})
    results: dict[str, list[str]] = {"pass": [], "fail": [], "warn": []}
    for name, command in checks.items():
        colorize(name, " ".join(command))
        sys.stdout.flush()
        with subprocess.Popen(command, cwd=REPO_ROOT, env=clean_env) as process:
            exit_code = process.wait()
        key = "warn" if "format" in name else ("pass" if exit_code == 0 else "fail")
        results[key].append(name)
    if os.environ.get("RALPH_LOOP"):
        results["fail"].extend(run_non_human_checks())

    return results


def run_non_human_checks() -> list[str]:
    """Runs checks on non-humans only. Checks things that linters or other chekcs to do not check.
    Unstages files that should never be touched.

    Returns:
        list of problems not caught by lint, type-checking, testing
    """
    problems: list[str] = []
    staged = run_git(["diff", "--cached", "--name-only", "--no-renames", "--diff-filter=ACMRD"]).splitlines()
    if not staged:
        colorize("EMPTY COMMIT", "nothing staged: do real work, do not commit empty")
        return problems  # yell, but don't block
    forbidden = [
        path
        for path in staged
        if path.casefold() in FORBIDDEN_FILES or path.casefold().startswith(FORBIDDEN_DIRS)
    ]
    if forbidden:
        run_git(["reset", "-q", "HEAD", "--", *forbidden])
        colorize("EJECTED", f"kept forbidden paths out of the commit: {', '.join(forbidden)}")
    problems.extend(check_for_bad_patterns())
    return problems


def check_for_bad_patterns() -> list[str]:
    """Check staged files for banned patterns and user-preference breaks (agent-in-loop containment).
    Does not unstage anything. Later, if any problem lands in { "fail": ... } the commit is blocked.

    Banned patterns are flagged only on ADDED diff lines (a '+' line, never a '+++' header).

    Returns:
        The banned-pattern hits plus any preference violations found in the staged files.
    """
    # Scan every staged file type (code, config, shell — the real bypass surface) except .md prose,
    # where a legitimate 'noqa' / 'type: ignore' quoted in docs is a false positive, not a bypass.
    diff_args = ["diff", "--cached", "--unified=0", "--", ".", ":(exclude)*.md"]
    staged_lines = run_git(diff_args).splitlines()
    colorize("BANNED PATTERNS CHECK", "checking for banned patterns in staged files")
    problems = [
        f"'{pattern}' line: {line[1:].strip()}"
        for line in staged_lines
        for pattern in FORBIDDEN_PATTERNS
        if line.startswith("+") and not line.startswith("+++") and pattern.casefold() in line.casefold()
    ]
    staged_python = run_git(["diff", "--cached", "--name-only", "--diff-filter=d", "--", "*.py"]).splitlines()
    if not (staged_python and prefs):
        return problems
    colorize("USER PREFERENCES", "checking that user's preferences.py are respected")
    violations = (prefs(path, run_git(["show", f":{path}"])) for path in staged_python)
    problems.extend(filter(None, violations))
    return problems


def run_preflight() -> dict[str, list[str]]:
    """Pre-commit: lint (blocking) plus an informational format report. For agents in the loop also unstages
    forbidden filepaths and flags banned patterns + human-preferences not honored.

    Returns:
        The commit-checks result with any containment problems appended to "fail" list.
    """
    return run_checks(COMMIT_CHECKS)


def run_gate() -> dict[str, list[str]]:
    """Pre-push / CI: lint, types, pylint, security, pytest/hypothesis (blocking), complexipy, plus an
    informational format report.

    Returns:
        The full-checks result bucketing each check name into "pass"/"fail" lists.
    """
    return run_checks(FULL_CHECKS)


def prepare_commit_msg(argv: list[str]) -> int:
    """Logic for the git prepare-commit-msg hook applicable to agents in the loop.

    Args:
        argv: arguments used to invoke `git commit`

    Returns:
        Status code integer 0 or 1 (git blocks commit on code 1)
    """
    if os.environ.get("RALPH_LOOP") != "1":
        return 0
    commit_msg_file: str = argv[1] if len(argv) > 1 else ""
    command = argv[2] if len(argv) > 2 else ""
    msg = ""
    # Determine if HEAD points to valid commit, check=False to prevent fatal Python runtime crash
    empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # universal empty tree hash
    ref = "HEAD" if run_git(["rev-parse", "--verify", "HEAD"], check=False).strip() else empty_tree
    if command in {"merge", "squash", "rebase", "reset", "clean", "filter-branch"}:
        msg = f"You cannot use that git command `{command}`.\n"
    if not run_git(["diff-index", "--cached", "--name-only", f"{ref}"]):
        msg += "Empty-tree commit detected. Stage real work and don't use --allow-empty. Lazy.\n"
    if Path(commit_msg_file).exists():
        content = Path(commit_msg_file).read_text(encoding="utf-8")
        actual_text = "\n".join([line for line in content.splitlines() if not line.startswith("#")]).strip()
        if not actual_text:
            msg += "Commit message is blank. Provide an informative message with your agent ID.\n"
    if msg:
        sys.stdout.write(f"\n[COMMIT BLOCKED]:\n{msg}\n")
        return 1  # Intercepts git
    return 0
