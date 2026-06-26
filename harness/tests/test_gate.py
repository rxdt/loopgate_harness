"""Tests for the preflight/gate checks and loop containment (harness.gate)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from harness import gate
from harness.tests.conftest import run_cmd

REPO_ROOT = Path(__file__).resolve().parents[2]


def stage(repo: Path, name: str, content: str) -> None:
    """Write a file inside the repo and stage it."""
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    run_cmd(["git", "add", name], repo)


def staged(repo: Path) -> list[str]:
    """Paths currently in the index, via the gate's own git helper (run_git returns raw stdout)."""
    return gate.run_git(repo, ["diff", "--cached", "--name-only"]).splitlines()


TOOL_CHECK_NAMES = set(gate.COMMIT_CHECKS)


def containment_fail(repo: Path) -> list[str]:
    """Real run_preflight, keeping only containment problems from fail.

    Drives the true entry point (real git, real ruff, real prefs) under the loop, then drops the tool
    check NAMES (ruff lint / format) so assertions judge containment alone, not ruff's own verdict on
    the deliberately-dirty fixtures.
    """
    fail = gate.run_preflight(repo)["fail"]
    return [problem for problem in fail if problem not in TOOL_CHECK_NAMES]


# --------------------------------------------------------------------------- run_git


def test_run_git_returns_stdout(git_repo: Path) -> None:
    """run_git runs git in the repo and returns its raw stdout string (callers .splitlines())."""
    stage(git_repo, "pkg/a.py", "x = 1\n")
    assert gate.run_git(git_repo, ["diff", "--cached", "--name-only"]) == "pkg/a.py\n"


def test_run_git_ignores_poisoned_hook_env(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A poisoned GIT_DIR a hook exported does not redirect the gate's git calls: run_git strips GIT_*,
    so it still runs against the real repo. (Without stripping, git would honor the bogus GIT_DIR and
    fail — this asserts the strip is load-bearing, not just that staging happens to work.)
    """
    monkeypatch.setenv("GIT_DIR", str(git_repo / "does-not-exist" / ".git"))
    stage(git_repo, "pkg/a.py", "x = 1\n")
    assert staged(git_repo) == ["pkg/a.py"]  # real index read despite the poisoned GIT_DIR


# --------------------------------------------------------------------------- run_checks (break cases)


def test_run_checks_reports_fully(git_repo: Path) -> None:
    """Each check is recorded by name under 'pass' or 'fail' by its exit code.

    Runs in a real repo (as the gate always does), so under the loop run_checks' containment finds an
    empty index and stays silent: only the command exit codes bucket the result, in either env.
    """
    captured = gate.run_checks(git_repo, {"boom": ["false"], "fine": ["true"]})
    assert captured == {"pass": ["fine"], "fail": ["boom"]}


def test_run_checks_messages_what_happened(git_repo: Path) -> None:
    """A passing check is recorded under 'pass' with nothing in 'fail' (a clean gate)."""
    assert gate.run_checks(git_repo, {"ok": ["true"]}) == {"pass": ["ok"], "fail": []}


def test_run_checks_records_a_failing_check_by_name(git_repo: Path) -> None:
    """A failing check lands under 'fail' by its name, with nothing in 'pass'."""
    captured = gate.run_checks(git_repo, {"random_check": ["false"]})
    assert captured == {"pass": [], "fail": ["random_check"]}


def test_run_checks_streams_command_output_live(git_repo: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """run_checks streams each command's stdout through as it runs, so humans and agents watch it."""
    captured = gate.run_checks(git_repo, {"echoer": ["printf", "hello from the check\n"]})
    assert captured == {"pass": ["echoer"], "fail": []}
    assert "hello from the check" in capfd.readouterr().out  # the line was streamed live


def test_types_check_actually_catches_a_type_error(git_repo: Path) -> None:
    """The 'types' check really runs pyright and fails on a type error anywhere in the tree,
    proving it is not narrowed to selected paths. Runs in a real staged repo, as the gate does: the
    type error is the only violation, so containment adds nothing and 'types' is the lone failure.
    """
    stage(git_repo, "pyrightconfig.json", '{"typeCheckingMode": "strict"}\n')
    stage(git_repo, "boom.py", "x: int = 'not an int'\n")

    result = gate.run_checks(git_repo, {"types": gate.FULL_CHECKS["types"]})
    assert result["fail"] == ["types"]
    assert result["pass"] == []


def test_lint_command_shows_fixes_and_passes_clean_code(
    git_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """The lint command runs `ruff check --show-fixes .`; clean code passes with no failures."""
    assert "--show-fixes" in gate.COMMIT_CHECKS["ruff lint"]
    stage(git_repo, "ok.py", "x = 1\n")

    result = gate.run_checks(git_repo, {"ruff lint": gate.COMMIT_CHECKS["ruff lint"]})
    assert result["pass"] == ["ruff lint"]
    assert result["fail"] == []
    assert "All checks passed!" in capfd.readouterr().out


def test_types_check_prints_pyright_summary_line(git_repo: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """The 'types' check runs pyright, so a clean tree streams its clean report through run_checks —
    either the JSON summary (with --outputjson) or the plain text line if a user simplifies the flags.
    """
    stage(git_repo, "ok.py", "x: int = 1\n")
    gate.run_checks(git_repo, {"types": gate.FULL_CHECKS["types"]})
    output = capfd.readouterr().out  # read once: readouterr() drains the buffer, a second call is empty
    assert '"summary"' in output or "0 errors, 0 warnings, 0 informations" in output


def test_types_check_passes_on_clean_code(git_repo: Path) -> None:
    """The same 'types' check passes when the tree is well-typed."""
    stage(git_repo, "ok.py", "x: int = 1\n")
    result = gate.run_checks(git_repo, {"types": gate.FULL_CHECKS["types"]})
    assert result["pass"] == ["types"]
    assert result["fail"] == []


# ------------------------------------------------- real tool failures propagate through run_checks


def test_lint_check_really_fails_on_an_error(git_repo: Path) -> None:
    """The lint command runs real ruff and puts 'lint' in fail on an actual error. The staged F401 is
    not a containment violation (no banned pattern, not a forbidden path), so 'ruff lint' stands alone.
    """
    stage(git_repo, "bad.py", "import os\nx = 1\n")  # F401 unused import
    result = gate.run_checks(git_repo, {"ruff lint": gate.COMMIT_CHECKS["ruff lint"]})
    assert result["fail"] == ["ruff lint"]
    assert result["pass"] == []


def test_security_error_flag_propagates_nonzero_exit(git_repo: Path) -> None:
    """Semgrep's --error makes a finding a nonzero exit, so run_checks records the check as fail.

    Uses a local inline rule (deterministic, offline) to prove the --error/exit-code wiring.
    FULL_CHECKS['security'] uses --config auto, which needs the network and cannot be forced to
    fail in an isolated test; that command's real flags are guarded by
    test_full_gate_keeps_the_semgrep_security_check. Staged in a real repo as the gate runs it: the
    fixtures carry no containment violation, so 'security' is the only failure.
    """
    stage(
        git_repo,
        "rule.yaml",
        "rules:\n"
        "  - id: no-eval\n"
        "    pattern: eval(...)\n"
        "    message: eval is forbidden\n"
        "    languages: [python]\n"
        "    severity: ERROR\n",
    )
    stage(git_repo, "bad.py", 'x = eval("1 + 1")\n')
    rule = git_repo / "rule.yaml"
    command = ["uv", "run", "--no-sync", "semgrep", "scan", "--config", str(rule), "--error", "--quiet", "."]
    result = gate.run_checks(git_repo, {"security": command})
    assert result["fail"] == ["security"]


# ------------------------------------------------------------------------- local gate ⊇ CI parity


def test_ci_runs_every_local_gate_check() -> None:
    """CI is the unbypassable backstop, so every local gate command must appear verbatim in ci.yml.
    If a check drifts or gets dropped from CI, it stops being enforced there — this catches that.
    """
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for name, command in gate.FULL_CHECKS.items():
        if "format" in name:
            continue  # format is a report, not a gate; its ci sync is checked separately below
        assert " ".join(command) in ci, f"{name} is in the local gate but not in ci.yml"


def test_ci_runs_the_format_report() -> None:
    """The format report is not a gate, but it must still run in CI (as a continue-on-error step) so
    the local report and CI stay in sync.
    """
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert " ".join(gate.COMMIT_CHECKS["ruff format (no fail)"]) in ci  # same ruff format --check in ci.yml


# --------------------------------------------------------------------- run_gate vs run_preflight routing


def covered_project(git_repo: Path, module_body: str, test_body: str) -> Path:
    """A real staged repo whose pyproject drives the REAL coverage command: no --cov target on the CLI,
    so pytest must read [tool.coverage] run.source from pyproject to know what to measure. That is the
    wiring the gate's bare `--cov` relies on. Files are staged so run_checks' loop containment runs for
    real against a true index, exactly as the gate calls it.
    """
    stage(
        git_repo,
        "pyproject.toml",
        "[tool.coverage.run]\nsource = ['pkg']\n[tool.coverage.report]\nfail_under = 100\n",
    )
    stage(git_repo, "pkg/__init__.py", "")
    stage(git_repo, "pkg/m.py", module_body)
    stage(git_repo, "test_m.py", test_body)
    return git_repo


def test_gate_pytest_check_fails_on_a_pyproject_driven_coverage_gap(git_repo: Path) -> None:
    """The gate's ACTUAL pytest command (real FULL_CHECKS['pytest'], bare `--cov` with no target) FAILS
    on an uncovered line, and fails ONLY because pytest resolved [tool.coverage] run.source from the
    staged pyproject and applied --cov-fail-under=100 (not because a string contains a flag).

    Only the FAILING direction lives here (one real pytest run). The passing direction — the same bare
    `--cov` reading run.source and going green at full coverage — is already proven end-to-end by
    test_integration's test_full_gate_end_to_end_and_pre_push_hook_blocks, so it is not repeated.

    NESTED PYTEST (session 2 of 3 in a full run): this test spawns one real `pytest` subprocess. It is
    unavoidable — proving that --cov-fail-under actually BLOCKS on an uncovered line can only be done by
    running pytest for real. It is scoped to the throwaway `covered_project` repo (its own single test),
    so it cannot recurse into this suite. Do not add a second spawn here for the passing case.
    """
    uncovered = covered_project(
        git_repo,
        "def used():\n    return 1\n\n\ndef never():\n    return 2\n",  # `never` is uncovered
        "from pkg.m import used\n\n\ndef test_used():\n    assert used() == 1\n",
    )
    result = gate.run_checks(uncovered, {"tests": gate.FULL_CHECKS["pytest"]})
    assert result["fail"] == ["tests"]  # the pyproject-driven 100% coverage gate bit


def test_preflight_invokes_only_lint_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Integrated routing: run_preflight (no loop) runs the commit checks (lint + format report). Real
    ruff on a clean tree: lint passes and format always passes; the heavy gate checks do not run.
    """
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    result = gate.run_preflight(tmp_path)
    ran = set(result["pass"]) | set(result["fail"])
    assert ran == {"ruff lint", "ruff format (no fail)"}  # only fast commit checks run; format is a report
    assert result["fail"] == []  # clean tree, real ruff passes


def test_run_gate_delegates_to_run_checks_with_full_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_gate is a thin router: it runs exactly FULL_CHECKS on the given repo and returns that result.

    The real end-to-end behaviour of every gate check is proven in test_integration's single full-gate
    test; here we only pin the routing (repo + FULL_CHECKS in, run_checks' result out) without paying
    for real tools or risking the pytest check recursively collecting this suite.

    NO NESTED PYTEST: run_checks is stubbed with `spy`, so run_gate spawns nothing. This is the pattern
    to copy for any new routing assertion — stub run_checks instead of adding a real-pytest spawn.
    """
    seen: dict[str, object] = {}

    def spy(repo: Path, checks: dict[str, list[str]]) -> dict[str, list[str]]:
        seen["repo"], seen["checks"] = repo, checks
        return {"pass": ["types"], "fail": []}

    monkeypatch.setattr(gate, "run_checks", spy)
    result = gate.run_gate(tmp_path)
    assert seen == {"repo": tmp_path, "checks": gate.FULL_CHECKS}
    assert result == {"pass": ["types"], "fail": []}


# --------------------------------------------------------------------------- containment (loop only)


def test_preflight_ejects_forbidden_file_under_loop(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A staged forbidden FILE (exact-path set) is dropped from the index, kept in the tree."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "pyproject.toml", "x = 1\n")
    assert containment_fail(git_repo) == []  # self-heals, not blocked
    assert "pyproject.toml" not in staged(git_repo)
    assert (git_repo / "pyproject.toml").exists()  # edit survives in the working tree


@pytest.mark.parametrize("path", ["harness/util.py", "tests/harness/x.py", ".github/ci.yml", ".githooks/x"])
def test_preflight_ejects_forbidden_dir_under_loop(
    path: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged file under any forbidden DIR (dir-set ancestor match) is dropped from the index."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, path, "value = 1\n")
    assert containment_fail(git_repo) == []
    assert path not in staged(git_repo)


def test_preflight_keeps_legit_work_beside_forbidden(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Only the forbidden path is dropped; the agent's own work still commits."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "harness/util.py", "value = 1\n")
    stage(git_repo, "src/feature.py", "y = 2\n")
    assert containment_fail(git_repo) == []
    after = staged(git_repo)
    assert "harness/util.py" not in after
    assert "src/feature.py" in after


def test_ejected_forbidden_py_is_not_preference_checked(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A forbidden .py that ALSO breaks a preference is ejected AND self-heals: because ejection
    removes it from the judged set, its preference break must NOT land in fail (ejecting is exit-0).
    Regression: the prefs loop once iterated the pre-eject staged list and blocked the commit.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "harness/evil.py", "_bad = 1\n")  # forbidden DIR + underscore-name preference break
    assert containment_fail(git_repo) == []  # ejected, not judged: commit still succeeds
    assert "harness/evil.py" not in staged(git_repo)


def test_forbidden_file_match_is_case_insensitive(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """The forbidden-file set is matched case-insensitively, so a mixed-case protected filename is
    still ejected (an agent can't smuggle it past by changing case).
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "PyProject.TOML", "x = 1\n")  # same file as pyproject.toml, different case
    assert containment_fail(git_repo) == []
    assert "PyProject.TOML" not in staged(git_repo)  # ejected despite the casing


def test_banned_pattern_in_ejected_file_is_not_flagged(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A banned pattern living in a forbidden file is not a failure: ejection happens BEFORE the
    banned-pattern scan re-reads the staged diff, so the ejected file's noqa never reaches it.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "pyproject.toml", "x = 1  # noqa\n")  # forbidden file that also holds a banned pattern
    assert containment_fail(git_repo) == []  # ejected before the scan; nothing to block
    assert "pyproject.toml" not in staged(git_repo)


def test_preflight_ejects_staged_deletion_of_forbidden(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged DELETION of a forbidden file is undone, so the agent can't remove protected files."""
    stage(git_repo, "pyproject.toml", "x = 1\n")
    run_cmd(["git", "commit", "-q", "-m", "add pyproject"], git_repo)
    run_cmd(["git", "rm", "-q", "pyproject.toml"], git_repo)
    monkeypatch.setenv("RALPH_LOOP", "1")
    assert containment_fail(git_repo) == []
    assert "pyproject.toml" not in staged(git_repo)  # the deletion was reset out of the index


def test_preflight_skips_containment_without_loop(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Without RALPH_LOOP, a human may stage forbidden paths: nothing is ejected."""
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    stage(git_repo, "harness/util.py", "value = 1\n")
    assert containment_fail(git_repo) == []
    assert "harness/util.py" in staged(git_repo)  # left staged: containment is loop-only


@pytest.mark.parametrize("pattern", ["noqa", "type: ignore", "--no-verify"])
def test_preflight_flags_banned_pattern_under_loop(
    pattern: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A banned escape-hatch in an added line is flagged (so the commit is rejected)."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/x.py", f"value = 1  # {pattern}\n")
    assert any(f"'{pattern}' line:" in problem for problem in containment_fail(git_repo))


def test_preflight_banned_pattern_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Mixed-case escape hatches are still caught."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/x.py", "value = 1  # NoQA\n")
    assert any("'noqa' line:" in problem for problem in containment_fail(git_repo))


@pytest.mark.parametrize(
    ("typed", "canonical"),
    [("tS-ignoRe", "ts-ignore"), ("Pylint:", "pylint:"), ("PRAGMA: no cover", "pragma: no cover")],
)
def test_preflight_flags_weird_case_banned_patterns(
    typed: str, canonical: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """An added line carrying a banned pattern in odd mixed casing is still flagged: the pattern set is
    hardcoded lowercase and the scan casefolds only the line, so the message uses that lowercase pattern.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/x.py", f"value = 1  # {typed}\n")
    fail = containment_fail(git_repo)
    assert any(isinstance(p, str) and p.startswith(f"'{canonical}' line:") for p in fail)


def test_preflight_flags_preferences_break_under_loop(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged Python file that breaks a preference (underscore name) is flagged."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/mod.py", "_bad = 1\n")
    assert any("'_bad'" in problem for problem in containment_fail(git_repo))


def test_preflight_judges_staged_not_working_tree(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Preferences judge the INDEX, not disk. Stage a clean file, then dirty the working tree with a
    violation that is never staged: the commit is not blocked (only staged content counts).
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/mod.py", "good = 1\n")  # index: clean
    (git_repo / "src/mod.py").write_text("_bad = 1\n", encoding="utf-8")  # working tree only: violation
    assert containment_fail(git_repo) == []


def test_preflight_preferences_read_one_file_at_a_time(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Each prefs() call receives exactly one staged file's source, never several concatenated."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    sources: list[str] = []

    def record(path: str, source: str) -> str:
        del path
        sources.append(source)
        return ""

    monkeypatch.setattr(gate, "prefs", record)
    stage(git_repo, "src/a.py", "a = 1\n")
    stage(git_repo, "src/b.py", "b = 2\n")
    gate.run_preflight(git_repo)
    # each call gets exactly one file's staged source (git show preserves the trailing newline)
    assert sorted(s.rstrip("\n") for s in sources) == ["a = 1", "b = 2"]


def test_preflight_skips_preferences_on_non_python_staged_file(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged non-.py file is not preference-checked (only Python style is judged), so it never
    lands in fail even with loop containment on.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "notes.txt", "_bad = 1\n")  # underscore name, but not Python — must be ignored
    assert containment_fail(git_repo) == []


def test_preflight_skips_preferences_for_staged_deletion(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged DELETION of a .py file is filtered out (--diff-filter=d) before any `git show :path`,
    so preference checking skips it (nothing to judge) rather than crashing.
    """
    stage(git_repo, "src/gone.py", "value = 1\n")
    run_cmd(["git", "commit", "-q", "-m", "add gone"], git_repo)
    run_cmd(["git", "rm", "-q", "src/gone.py"], git_repo)  # staged deletion: no :path blob
    monkeypatch.setenv("RALPH_LOOP", "1")
    assert containment_fail(git_repo) == []


def test_preflight_tolerates_missing_preferences(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """If preferences.py was deleted (prefs is None), the Python style check is skipped, not crashed."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gate, "prefs", None)
    stage(git_repo, "src/mod.py", "_bad = 1\n")
    assert containment_fail(git_repo) == []


def test_gate_imports_cleanly_without_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    """If preferences.py is absent, gate still imports and prefs is None (the ImportError branch)."""
    monkeypatch.setitem(sys.modules, "harness.preferences", None)
    importlib.reload(gate)
    assert gate.prefs is None
    monkeypatch.undo()
    importlib.reload(gate)
    assert gate.prefs is not None


# --------------------------------------------------------- spec tests (FAIL against the current bugs)


def test_staged_noqa_produces_a_noqa_line_message_in_fail(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A staged `noqa` must land in fail as the scan's own message `'noqa' line: <code>`. FAILS now:
    `found.join(...)` discards its result, so the banned-pattern scan appends nothing.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/x.py", "value = 1  # noqa\n")
    fail = containment_fail(git_repo)
    assert any(isinstance(p, str) and p.startswith("'noqa' line:") for p in fail)


def test_reset_ejects_only_forbidden_keeping_legit_staged(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Ejection resets ONLY forbidden paths; a legit file staged alongside stays in the index. FAILS
    now: reset is passed every staged path, so the legit file is unstaged too.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "pyproject.toml", "x = 1\n")  # forbidden
    stage(git_repo, "src/feature.py", "y = 2\n")  # legit
    containment_fail(git_repo)
    after = staged(git_repo)
    assert "pyproject.toml" not in after  # forbidden ejected
    assert "src/feature.py" in after  # legit work survives


def test_prefs_skips_non_python_invalid_source(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A staged non-.py file is not fed to prefs/ast.parse. Its bytes are invalid Python, so if the
    `.py` filter were missing the run would crash. FAILS now: no suffix filter, `ast.parse` raises.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "data.json", "{not: valid python (((\n")  # invalid Python; must never reach prefs
    assert containment_fail(git_repo) == []


def test_ejected_forbidden_py_is_not_re_judged_by_prefs(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """A forbidden .py that breaks a preference is ejected, so prefs must NOT re-judge it (ejecting is
    exit-0). FAILS now: the prefs loop reads the post-eject staged list but has no forbidden filter,
    and the ejected file is still on disk / in the diff path set, so its break lands in fail.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "harness/evil.py", "_bad = 1\n")  # forbidden DIR + underscore-name break
    fail = containment_fail(git_repo)
    assert fail == []  # ejected, not re-judged
    assert "harness/evil.py" not in staged(git_repo)


# ----------------------------------------- banned-pattern scan only matches added ('+', not '+++') lines
# git diff --cached --unified=0 emits, per hunk: `--- a/f`, `+++ b/f` (headers), `-old` (removed),
# `+new` (added). The scan (gate.run_preflight line 163) must flag ONLY the real added line ('+',
# excluding the '+++' file header); removed ('-') and header ('+++') lines carrying a banned pattern
# must be ignored. A '*'-prefixed line can never occur in unified diff output, so nothing starting
# with '*' is ever matched — proven here by the removed-line case (only '+' counts).


def test_banned_scan_flags_added_plus_line(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """An ADDED ('+') line carrying noqa is flagged. FAILS now: the scan's message is char-shredded."""
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/x.py", "value = 1  # noqa\n")  # a pure addition -> a '+' hunk line
    fail = containment_fail(git_repo)
    assert any(isinstance(p, str) and p.startswith("'noqa' line:") for p in fail)


def test_banned_scan_ignores_plus_plus_plus_header_line(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """The '+++ b/<path>' file-HEADER line is not an added code line: a banned pattern living only in
    the path (a file literally named with 'noqa') must not be flagged by the header, since the scan
    excludes lines starting with '+++'.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    stage(git_repo, "src/noqa_helpers.py", "value = 1\n")  # 'noqa' appears in the '+++ b/...' header
    fail = containment_fail(git_repo)
    assert not any(isinstance(p, str) and p.startswith("'noqa' line:") for p in fail)


def test_banned_scan_ignores_removed_minus_line(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A REMOVED ('-') line carrying a banned pattern is ignored: deleting a `# noqa` line is good,
    not a violation. Also proves only '+' is matched (never '-', and never a '*' prefix).
    """
    stage(git_repo, "src/x.py", "value = 1  # noqa\n")
    run_cmd(["git", "commit", "-q", "-m", "seed noqa"], git_repo)  # committed; not in the diff anymore
    stage(git_repo, "src/x.py", "value = 1\n")  # drops the escape-hatch line -> a removed ('-') hunk line
    monkeypatch.setenv("RALPH_LOOP", "1")
    fail = containment_fail(git_repo)
    assert not any(isinstance(p, str) and "noqa" in p for p in fail)  # removed line is not flagged
