"""1) Preflight pre-commit checks basic quality plus agent containment. `def run_preflight`

2) Full gate on staged files.
`def run_gate` mirrors what will run on Github (CI runs this same `harness gate`).

All containment lists and check commands come from [tool.harness] in pyproject.toml, read once at
import into the constants below. A check is a (name, argv) pair; its `preflight`/`blocking` flags sort
it into the maps and sets this module runs on.
"""

from __future__ import annotations

import os
import subprocess
import sys
from functools import cache
from pathlib import Path

import tomlkit as tomllib
import typer
from rich.console import Console

from mutation.check_mutmut import MINIMUM_MUTATION_SCORE, analyze_mutmut_report_passed

try:
    from preferences.preferences import preferences_violations as prefs
except ImportError:  # humans can delete preferences.py
    prefs = None

console = Console(force_terminal=True, color_system=None if os.environ.get("RALPH_LOOP") else "auto")


class Gate:
    """Contains Gate configuration values and methods."""

    EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # universal empty tree hash

    def __init__(self, root: Path) -> None:
        self.repo_root = root
        pyproject = self.repo_root / "pyproject.toml"
        toml = tomllib.loads(pyproject.read_text(encoding="utf-8")).unwrap() if pyproject.is_file() else {}
        harness = toml.get("tool", {}).get("harness")
        if not harness:
            defaults = tomllib.loads(
                Path(__file__).with_name("temp.pyproject.toml").read_text(encoding="utf-8")
            )
            harness = defaults["tool"]["harness"]
        self.settings = harness.get(
            "settings", {"behavior": "fail", "error_diff_lines": 500, "languages": ["py"]}
        )
        self.forbidden: dict[str, list[str]] = harness.get("FORBIDDEN", {})
        self.agents: dict[str, list[str]] = harness.get("agents", {})
        self.commit_checks: dict[str, list[str]] = harness.get("preflight", {})
        self.gate_checks: dict[str, list[str]] = harness.get("gate", {}) | self.commit_checks
        self.forbidden_files: tuple[str, ...] = tuple(self.forbidden.get("FILES", []))
        self.forbidden_dirs: tuple[str, ...] = tuple(self.forbidden.get("DIRS", []))
        self.forbidden_patterns: tuple[str, ...] = tuple(self.forbidden.get("PATTERNS", []))

    def run_checks(self, checks: dict[str, list[str]]) -> dict[str, list[str]]:
        """Run each named command, streaming its output live under a phase header.
        Reports what each command did and leaves the verdict to the caller.

        Args:
            checks: Mapping of check name to the argv that runs it.

        Returns:
            { "pass": [...], "warn": [...], "fail": [ problems ] } bucketing each check name by exit code.
            If anything is in "fail", a commit is not allowed.
        """
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        if not os.environ.get("RALPH_LOOP"):
            clean_env.update({"FORCE_COLOR": "1", "CLICOLOR_FORCE": "1", "SEMGREP_FORCE_COLOR": "1"})
        results: dict[str, list[str]] = {"pass": [], "fail": [], "warn": []}
        for name, command in checks.items():
            colorize(name, " ".join(command))
            sys.stdout.flush()
            with subprocess.Popen(command, cwd=self.repo_root, env=clean_env) as process:
                exit_code = process.wait()
            if exit_code == 0:
                results["pass"].append(name)
            elif "format" in name:
                results["warn"].append(name)
            else:
                results[self.settings["behavior"]].append(name)
        key = "fail" if os.environ.get("RALPH_LOOP") else "warn"
        colorize("AGENT CHECKs", "running non-human agent checks")
        self._run_non_human_checks(results, key)
        mutmut_key = "pass" if analyze_mutmut_report_passed() >= MINIMUM_MUTATION_SCORE else key
        results[mutmut_key].append("mutmut")
        return results

    def _run_non_human_checks(self, results: dict[str, list[str]], key: str):
        """Runs checks on non-humans only. Checks things that linters or other chekcs to do not check.
        Unstages files that should never be touched.

        Arguments:
            results: The original bucketing of each check name into "pass"/"fail"/"warn" lists
            key: `results` dictionary will use "fail"/"warn" if an agent is being checked
        """
        ref = "HEAD" if run_git(["rev-parse", "--verify", "HEAD"], check=False).strip() else self.EMPTY_TREE
        staged = run_git([
            "diff",
            "--cached",
            "--name-only",
            "--no-renames",
            "--diff-filter=ACMRD",
        ]).splitlines()
        if not staged:
            colorize("EMPTY COMMIT", "nothing staged: do real work, do not commit empty")
            return
        forbidden_paths: list[str] = [
            path
            for path in staged
            if path.casefold() in self.forbidden_files or path.casefold().startswith(self.forbidden_dirs)
        ]
        if forbidden_paths:
            out = "\n".join(forbidden_paths)
            colorize("EJECTED", f"Would keep forbidden paths out of agent commit:[dim green]\n{out}[/]")
            if key == "fail":
                run_git(["reset", "-q", ref, "--", *forbidden_paths])
        if forbidden_patterns := self._check_for_bad_patterns():
            results[key].append("FORBIDDEN FOR AGENT:\n" + "\n".join(forbidden_patterns))
        if ignored_preferences := self._check_for_preferences():
            results[key].append(f"PREFERENCES IGNORED:\n{ignored_preferences}")
        self._check_diff_size(ref, results, key)

    def _check_for_bad_patterns(self) -> list[str]:
        """Check staged files for banned patterns (agent-in-loop containment).
        Does not unstage anything. Later, if any problem lands in { "fail": ... } the commit is blocked.

        Banned patterns are flagged only on ADDED diff lines (a '+' line, never a '+++' header).

        Returns:
            The banned-pattern hits plus any preference violations found in the staged files.
        """
        staged_lines = run_git([
            "diff",
            "--cached",
            "--unified=0",
            "--output-indicator-new=a",
            ":(exclude,icase,glob)**/*.toml",
            ":(exclude,icase,glob)**/*.md",
        ])
        problems: set[str] = set()
        current_file = ""
        for line in staged_lines.splitlines():
            if line.startswith("+++ b/"):
                current_file = line.removeprefix("+++ b/")
            elif line.startswith("a"):
                problems.update(
                    f"{current_file}: '{pattern}'"
                    for pattern in self.forbidden_patterns
                    if pattern.casefold() in line.casefold()
                )
        colorize(
            "BANNED PATTERNS FOR AGENT", f"check for banned patterns in staged files\nIssues:\n{problems}"
        )
        return list(problems)

    def _check_diff_size(self, ref: str, results: dict[str, list[str]], key: str):
        """Report size of staged diff and block a bloated commit if past Lines Of Code (LOC) cap.

        LOC = added + deleted. Count diff lines, staged and unstaged. An edit is
        one deletion plus one addition. Lockfiles and binaries are excluded.

        Arguments:
            ref: Git SHA to run git diff on
            results: The full-checks result bucketing each check name into "pass"/"fail"/"warn" lists.
            key: Whether to "fail" this check because an agent is being checked (not a human)
        """
        stats = run_git(["diff", ref, "--numstat", "--cached", "--find-renames"]).splitlines()
        total = 0
        for line in stats:
            inserted, deleted, path = line.split("\t", 2)
            if not (inserted == "-" or path.lower().endswith(".lock")):  # binary or lockfile
                total += int(inserted) + int(deleted)
        warn_at_75: int = round(self.settings["error_diff_lines"] * 0.75)
        msg = (
            f"{total} lines of code modified (insertions + deletions in staged files). Agents get WARN at "
            f"75% {warn_at_75}, ERROR at {self.settings['error_diff_lines']}."
        )
        do_better = (
            "\nRefactor bloat, reduce mis-direction, re-use fixtures, cut duplication, slim down "
            "code. More code does not mean good code."
        )
        if total > self.settings["error_diff_lines"]:
            results[key].append(msg + do_better)
        elif total > warn_at_75:
            results["warn"].append(msg + do_better)
        colorize("DIFF SIZE", msg)

    def _check_for_preferences(self) -> str:
        """Checks user preferences honored. Currently only a preferences.py file exists. New languages should
        add their own branch.

        Returns:
            The preferences violations and filepath found in staged files.
        """
        problems: list[str] = []
        if "py" in self.settings["languages"]:
            staged = run_git([
                "diff",
                "--cached",
                "--name-only",
                "--diff-filter=d",
                "--",
                "*.py",
            ]).splitlines()
            if prefs:
                for path in staged:
                    messages: str = prefs(path, run_git(["show", f":{path}"]))
                    if messages:
                        problems.append(messages)
        colorize(
            "REPO PREFERENCES", f"checking repo preferences are respected by agents\nIssues:\n{problems}"
        )
        return "\n".join(problems)

    def run_preflight(self) -> dict[str, list[str]]:
        """Pre-commit: checks plus an informational format report. For agents in the loop also
        unstages forbidden filepaths and flags banned patterns and any human-preferences not honored.

        Returns:
            The commit-checks result with any containment problems in a "fail" list.
        """
        return self.run_checks(self.commit_checks)

    def run_gate(self) -> dict[str, list[str]]:
        """Pre-push / CI: lint, types, pylint, security, pytest/hypothesis (blocking), complexipy, plus an
        informational format report.

        Returns:
            results: The full-checks result bucketing each check name into "pass"/"fail"/"warn" lists.
        """
        return self.run_checks(self.gate_checks)

    def prepare_commit_msg(self, argv: list[str]) -> int:
        """Logic for the git prepare-commit-msg hook applicable to agents in the loop.

        Args:
            argv: arguments used to invoke `git commit`

        Returns:
            Status code integer 0 or 1 (git blocks commit on code 1)
        """
        if not os.environ.get("RALPH_LOOP"):
            return 0
        commit_msg_file: str = argv[1] if len(argv) > 1 else ""
        command = argv[2] if len(argv) > 2 else ""
        msg = ""
        if command in {"merge", "squash", "rebase", "reset", "clean", "filter-branch"}:
            msg = f"You cannot use that git command `{command}`.\n"
        ref = "HEAD" if run_git(["rev-parse", "--verify", "HEAD"], check=False).strip() else self.EMPTY_TREE
        if not run_git(["diff-index", "--cached", "--name-only", ref]):
            msg += "Empty commit detected. Stage real work, Don't use --allow-empty. Say if you're blocked\n"
        if Path(commit_msg_file).exists():
            content = Path(commit_msg_file).read_text(encoding="utf-8")
            actual_text = "\n".join([
                line for line in content.splitlines() if not line.startswith("#")
            ]).strip()
            if not actual_text:
                msg += "Commit message is blank. Provide an informative message with your agent ID.\n"
        if msg:
            colorize("prepare-commit-message", msg)
            return 1  # Intercepts git
        return 0


def run_git(args: list[str], repo: Path | None = None, check: bool = True) -> str:
    """Run a git command in the repo and return its stdout.

    Arguments:
        args: Git subcommand and its arguments
        repo: the repository directory to run the git command from. Defaults to REPO_ROOT.
        check: If check is True and the exit code was non-zero, it raises a CalledProcessError.

    Returns:
        The command's raw stdout string (callers will .splitlines() as needed)
    """
    target = gates().repo_root if repo is None else repo
    command = ["git", "-C", str(target), *args]
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
        typer.echo(f"PHASE: {name.upper()}\n{command}")
    else:
        console.rule(f"[bold cyan] PHASE: {name.upper()}[/]", style="blink cyan on grey15")
        console.print(f"[dim italic]{command}[/dim italic]\n", justify="center")


@cache
def gates() -> Gate:
    """Returns the singleton object containing gate checks and configs."""
    root = run_git(["rev-parse", "--show-toplevel"], repo=Path.cwd(), check=False).strip()
    if not root:
        typer.echo("Run this inside a git repository")
    return Gate(Path(root).resolve())
