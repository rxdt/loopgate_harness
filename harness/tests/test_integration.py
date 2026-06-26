"""Integration tests: commits and pushes run through the REAL git hooks.

Unlike a stubbed hook, `real_hook_repo` (see conftest) wires the tracked `.githooks/*` to the
repo's real `.venv/bin/harness`, so a commit or push here drives the true chain:
git hook -> harness preflight/gate -> run_checks -> real tools. These prove the hooks actually
invoke the real harness — the thing no other test exercised.

Some assertions describe the INTENDED contract (a clean commit is allowed). Those depend on the
source fix that stops `run_preflight` from leaking the `problems` dict keys into `fail`; until
that lands they fail, which is the point — they are the spec for that fix, not a stub of it.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from harness import gate
from harness.tests.conftest import run_cmd


def attempt_commit(repo: Path, message: str, loop: bool, no_verify: bool) -> subprocess.CompletedProcess[str]:
    """Try a commit with optional RALPH_LOOP in the env and optional --no-verify."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    if loop:
        env["RALPH_LOOP"] = "1"
    args = ["git", "commit", "-q", "-m", message]
    if no_verify:
        args.append("--no-verify")
    return subprocess.run(args, cwd=repo, capture_output=True, text=True, check=False, env=env)


def stage(repo: Path, name: str, content: str) -> None:
    """Write a file inside the repo and stage it."""
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_cmd(["git", "add", name], repo)


def committed_files(repo: Path) -> list[str]:
    """Paths in the most recent commit."""
    return run_cmd(["git", "show", "--name-only", "--format=", "HEAD"], repo).split()


def log(repo: Path) -> str:
    """One-line git log of the repo."""
    return run_cmd(["git", "log", "--oneline"], repo)


# --------------------------------------------------------------------------- pre-commit (real hook)


def test_clean_commit_is_allowed(real_hook_repo: Path) -> None:
    """A clean, formatted, lint-passing commit passes the real preflight hook and lands.

    INTENDED contract — depends on the run_preflight `fail` fix (dict keys must not count as
    failures).
    """
    stage(real_hook_repo, "feature.py", "y = 2\n")
    result = attempt_commit(real_hook_repo, "clean work", loop=False, no_verify=False)
    assert result.returncode == 0, result.stderr
    assert "feature.py" in committed_files(real_hook_repo)


def test_lint_error_blocks_the_commit(real_hook_repo: Path) -> None:
    """A real lint error (unused import) makes the real preflight hook reject the commit."""
    stage(real_hook_repo, "bad.py", "import os\ny = 2\n")
    result = attempt_commit(real_hook_repo, "lint break", loop=False, no_verify=False)
    assert result.returncode != 0
    assert "lint break" not in log(real_hook_repo)


def test_format_difference_does_not_block_the_commit(real_hook_repo: Path) -> None:
    """A lint-clean but badly-FORMATTED file still commits: the real hook runs the format report
    (which streams a nonzero reformat diff) but format is informational, so it never blocks.
    """
    stage(real_hook_repo, "messy.py", "x=1\n")  # ruff-check clean, but ruff format would reformat it
    result = attempt_commit(real_hook_repo, "unformatted but clean", loop=False, no_verify=False)
    assert result.returncode == 0, result.stderr  # format reported, did not reject
    assert "messy.py" in committed_files(real_hook_repo)


def test_loop_unstages_forbidden_file_but_keeps_legit_work(real_hook_repo: Path) -> None:
    """RALPH_LOOP + real hook: a forbidden file is unstaged (file by file), the legit file commits.

    INTENDED contract — depends on the containment/`fail` fix.
    """
    stage(real_hook_repo, "harness/evil.py", "value = 1\n")
    stage(real_hook_repo, "feature.py", "y = 2\n")
    result = attempt_commit(real_hook_repo, "work beside evil", loop=True, no_verify=False)
    assert result.returncode == 0, result.stderr
    committed = committed_files(real_hook_repo)
    assert "feature.py" in committed  # legit work landed
    assert "harness/evil.py" not in committed  # forbidden path kept out of the commit
    assert (real_hook_repo / "harness" / "evil.py").exists()  # left in the working tree, not deleted


def test_banned_pattern_blocks_the_commit(real_hook_repo: Path) -> None:
    """A staged banned pattern can't be unstaged, so the real hook rejects the commit."""
    stage(real_hook_repo, "x.py", "value = 1  # noqa\n")
    result = attempt_commit(real_hook_repo, "sneaky", loop=True, no_verify=False)
    assert result.returncode != 0
    assert "sneaky" not in log(real_hook_repo)


# ---- These four assert the banned-pattern scan actually works; they FAIL against the current source.
# BUG 1: run_preflight tests `line.startswith("*")`, but git diff added lines start with "+", so the
#        scan never matches an added line.
# BUG 2: `found.join(...)` discards its result (found stays "") and the message that is appended is a
#        bound method, so the fail bucket is empty and the commit is wrongly allowed.


def test_bug1_added_noqa_line_blocks_commit(real_hook_repo: Path) -> None:
    """An added ('+') line with `noqa` must block the commit. Fails now (scan matches '*', not '+')."""
    stage(real_hook_repo, "hatch.py", "value = 1  # noqa\n")
    result = attempt_commit(real_hook_repo, "noqa slips in", loop=True, no_verify=False)
    assert result.returncode != 0
    assert "noqa slips in" not in log(real_hook_repo)


def test_bug1_added_no_verify_line_blocks_commit(real_hook_repo: Path) -> None:
    """An added line containing `--no-verify` must block. Fails now (scan matches '*', not '+')."""
    stage(real_hook_repo, "hatch.py", "CMD = 'git commit --no-verify'\n")
    result = attempt_commit(real_hook_repo, "no-verify slips in", loop=True, no_verify=False)
    assert result.returncode != 0
    assert "no-verify slips in" not in log(real_hook_repo)


def test_bug2_banned_pattern_reports_a_message(real_hook_repo: Path) -> None:
    """The rejection must print a readable message naming the pattern and line. Fails now: `found`
    stays empty so nothing is reported (and the appended value is a bound method, not text).
    """
    stage(real_hook_repo, "hatch.py", "value = 1  # type: ignore\n")
    result = attempt_commit(real_hook_repo, "typed ignore", loop=True, no_verify=False)
    report = result.stderr + result.stdout
    assert "type: ignore" in report
    assert "line:" in report


def test_bug2_banned_pattern_does_not_silently_pass(real_hook_repo: Path) -> None:
    """A staged banned pattern must never yield a clean 'ok: preflight pass'. Fails now: the discarded
    `found.join` leaves the fail bucket empty, so preflight passes and the commit lands.
    """
    stage(real_hook_repo, "hatch.py", "value = 1  # pytest.mark.skip\n")
    result = attempt_commit(real_hook_repo, "skip marker", loop=True, no_verify=False)
    assert result.returncode != 0


def test_no_verify_bypasses_the_hook(real_hook_repo: Path) -> None:
    """--no-verify skips the hook entirely: containment is best-effort, not a jail."""
    stage(real_hook_repo, "harness/evil.py", "value = 1\n")
    result = attempt_commit(real_hook_repo, "bypass", loop=True, no_verify=True)
    assert result.returncode == 0


@pytest.mark.parametrize("name", ["RUFF.toml", "AGENTS.md", ".coveragERC"])
def test_loop_undoes_deletion_of_forbidden_file_case_variant(name: str, real_hook_repo: Path) -> None:
    """RALPH_LOOP + real hook: staging the DELETION of a forbidden file (matched case-insensitively)
    is reset, so an agent can't remove protected config by changing its case. The deletion never
    lands in the commit and the file stays on disk.
    """
    stage(real_hook_repo, name, "seed\n")
    attempt_commit(real_hook_repo, f"add {name}", loop=False, no_verify=True)  # seed it, bypass hook
    run_cmd(["git", "rm", "-q", name], real_hook_repo)  # stage the deletion
    result = attempt_commit(real_hook_repo, f"delete {name}", loop=True, no_verify=False)
    assert result.returncode == 0, result.stderr  # ejection self-heals, commit proceeds
    assert name not in committed_files(real_hook_repo)  # the staged deletion never lands in the commit
    tracked = run_cmd(["git", "ls-tree", "-r", "--name-only", "HEAD"], real_hook_repo).split()
    assert name in tracked  # forbidden file still tracked in HEAD: the delete was reset out of the index


def test_loop_neutral_allowed_file_with_banned_patterns_and_lambda(real_hook_repo: Path) -> None:
    """RALPH_LOOP + real hook: an allowed file NeutralAllowed.py an agent edits with escape hatches
    (hooksPath, ts-ignore, noqa) plus a lambda and a harmless 'Any time you use' comment is rejected.
    The lambda alone is caught by the real preferences.py; the banned patterns should also block once
    the banned-pattern scan bug is fixed. Real source, no stub.
    """
    source = (
        "double = lambda v: v * 2\n"
        "# Any time you use this function\n"
        "SETTINGS = {'hooksPath': '.githooks'}  # ts-ignore\n"
        "value = 1  # noqa\n"
    )
    stage(real_hook_repo, "NeutralAllowed.py", source)
    result = attempt_commit(real_hook_repo, "sneaky edit", loop=True, no_verify=False)
    assert result.returncode != 0  # rejected (lambda via prefs; patterns once the scan is fixed)
    assert "sneaky edit" not in log(real_hook_repo)


def test_loop_pyc_file_is_not_preference_checked(real_hook_repo: Path) -> None:
    """RALPH_LOOP + real hook: a staged sOmEfIlE.pyc (not .py) is skipped by the preference loop even
    though its bytes are syntactically invalid Python (`def problems(x:*)`), so the hook does not
    crash on the invalid source. Real source, no stub.
    """
    source = (
        "def problems(x:*) -> Any:\n"
        "        problems = [\n"
        "            f\"banned pattern '{pattern}' in line: {line[1:].strip()}\"\n"
        "            for line in added\n"
        '            if line.startswith("+") and not line.startswith("+++")\n'
        "            for pattern in FORBIDDEN_PATTERNS\n"
        "            if pattern.casefold() in line.casefold()\n"
        "        ]\n"
        "    return problems\n"
    )
    stage(real_hook_repo, "sOmEfIlE.pyc", source)
    result = attempt_commit(real_hook_repo, "add pyc", loop=True, no_verify=False)
    assert "Traceback" not in result.stderr  # .pyc skipped: no ast.parse crash on invalid bytes


# --------------------------------------------------------------------------- pre-push (real hook wiring)


def self_contained_full_checks(repo: Path) -> dict[str, list[str]]:
    """FULL_CHECKS retargeted at the fake repo's OWN package, not this project's tree.

    Two commands hardcode this project's layout and must be repointed to run inside a throwaway repo,
    changing only WHAT they scan, never how strictly:
      - pytest: the real command has no rootdir, so inside a fake repo it walks up and collects THIS
        suite, recursively re-running the whole gate. We pin --rootdir to the fake repo and point it at
        the fake repo's own test. Still real uv/pytest/coverage, just scoped so recursion is impossible.
      - pylint: the real command lints `src harness` (dirs that don't exist here), so we lint the fake
        repo's own `pkg` package instead.
    Every other check (ruff lint, format, types, security) already scans `.` and needs no change.
    """
    checks = dict(gate.FULL_CHECKS)
    checks["pytest"] = [
        "uv",
        "run",
        "--no-cache",
        "--no-sync",
        "pytest",
        "--rootdir",
        str(repo),
        "-p",
        "no:cacheprovider",
        str(repo),
        "--cov",
        "--cov-report=term-missing",
        "--cov-fail-under=100",
    ]
    checks["pylint"] = ["uv", "run", "--no-sync", "pylint", "pkg"]
    return checks


def test_full_gate_end_to_end_and_pre_push_hook_blocks(real_hook_repo: Path) -> None:
    """One real end-to-end pass on one fake repo, covering three cases:

    1. run_checks drives every real gate check (lint, format, types, pylint, security, pytest) on a
       green self-contained project — all of them run and pass. Nothing is stubbed. The pytest check is
       retargeted (see self_contained_full_checks) only to keep it from recursively collecting this
       suite; it still really runs uv/pytest/coverage on the fake repo's own package.
    2. format is a REPORT, not a gate: the tree is deliberately unformatted, yet format lands in pass
       and never in fail (it can never block).
    3. the real pre-push hook drives the real gate: after breaking the code with a lint error, a push
       to a local bare remote is rejected by the hook-invoked gate.

    NESTED PYTEST (session 3 of 3 in a full run): case 1 runs the real gate, whose `pytest` check spawns
    one real pytest subprocess. This is THE one full end-to-end run and is the only place the passing
    coverage path is exercised for real. self_contained_full_checks pins --rootdir to this throwaway
    repo so the nested pytest collects only the fake repo's own test and can never recurse into the real
    suite. Keep this the single end-to-end; do not add more real-gate spawns elsewhere.
    """
    repo = real_hook_repo
    # A green, self-contained, deliberately-UNFORMATTED project (x=1 is lint/type-clean but reformats).
    stage(repo, "pyrightconfig.json", '{"typeCheckingMode": "strict"}\n')
    stage(
        repo,
        "pyproject.toml",
        "[tool.coverage.run]\nsource = ['pkg']\n[tool.coverage.report]\nfail_under = 100\n",
    )
    stage(repo, "pkg/__init__.py", '"""Fake package for the end-to-end gate run."""\n')
    # Docstrings keep pylint and ruff D-rules happy. The x=1 body stays unformatted on purpose so the
    # format report has a real reformat diff to prove it ran.
    module = (
        '"""One covered function."""\n\n\ndef used() -> int:\n    """Return one."""\n    x=1\n    return x\n'
    )
    stage(repo, "pkg/m.py", module)
    test_body = (
        '"""Test the fake package."""\n\n'
        "from pkg.m import used\n\n\n"
        'def test_used() -> None:\n    """used returns 1."""\n    assert used() == 1\n'
    )
    stage(repo, "test_m.py", test_body)

    result = gate.run_checks(repo, self_contained_full_checks(repo))

    # Every real check ran and passed on the green project.
    ran = set(result["pass"]) | set(result["fail"])
    assert ran == {"ruff lint", "ruff format (no fail)", "types", "pylint", "security", "pytest"}
    assert result["fail"] == []  # nothing blocks: the project is clean
    # Format is a report: present in pass, never in fail, even though the tree reformats.
    assert "ruff format (no fail)" in result["pass"]
    assert "ruff format (no fail)" not in result["fail"]

    # Break the code, then the REAL pre-push hook must reject the push.
    bare = repo.parent / "origin.git"
    run_cmd(["git", "init", "--bare", "-q", str(bare)], repo)
    run_cmd(["git", "remote", "add", "origin", str(bare)], repo)
    stage(repo, "pushbad.py", "import os\ny = 2\n")  # real lint error
    attempt_commit(repo, "push me", loop=False, no_verify=True)  # bypass pre-commit, keep the file
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    push = subprocess.run(
        ["git", "push", "-q", "origin", "HEAD:main"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert push.returncode != 0  # the real pre-push gate rejected the push
