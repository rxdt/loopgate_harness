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


def containment_fail(repo: Path) -> list[str]:
    """Run only the loop-containment checks against the staged index."""
    return gate.run_non_human_checks(repo)


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


# --------------------------------------------------------------------------- tool dispatch


def test_call_tools_reports_fully(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Each check is recorded by name under 'pass' or 'fail' from the seam's exit code."""

    def fake_run(name: str, command: list[str], repo: Path, env: dict[str, str]) -> int:
        del command, repo, env
        return 1 if name == "boom" else 0

    monkeypatch.setattr(gate, "run_one_check", fake_run)
    captured = gate.call_tools(git_repo, {"boom": ["tool"], "fine": ["tool"]})
    assert captured == {"pass": ["fine"], "fail": ["boom"]}


def test_call_tools_messages_what_happened(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A passing check is recorded under 'pass' with nothing in 'fail'."""

    def fake_run(name: str, command: list[str], repo: Path, env: dict[str, str]) -> int:
        del name, command, repo, env
        return 0

    monkeypatch.setattr(gate, "run_one_check", fake_run)
    assert gate.call_tools(git_repo, {"ok": ["tool"]}) == {"pass": ["ok"], "fail": []}


def test_call_tools_records_a_failing_check_by_name(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A failing check lands under 'fail' by its name, with nothing in 'pass'."""

    def fake_run(name: str, command: list[str], repo: Path, env: dict[str, str]) -> int:
        del name, command, repo, env
        return 1

    monkeypatch.setattr(gate, "run_one_check", fake_run)
    captured = gate.call_tools(git_repo, {"random_check": ["tool"]})
    assert captured == {"pass": [], "fail": ["random_check"]}


def test_run_one_check_streams_command_output_live(git_repo: Path, capfd: pytest.CaptureFixture[str]) -> None:
    """The production seam streams the child process output and returns its exit code."""
    exit_code = gate.run_one_check(
        "echoer",
        ["/bin/sh", "-c", "printf 'hello from the check\\n'"],
        git_repo,
        {},
    )
    assert exit_code == 0
    assert "hello from the check" in capfd.readouterr().out


def test_run_one_check_returns_nonzero_exit_code(git_repo: Path) -> None:
    """The production seam returns the child process status without translating it."""
    assert gate.run_one_check("failing", ["/bin/sh", "-c", "exit 7"], git_repo, {}) == 7


def test_call_tools_appends_containment_only_under_loop(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Loop containment is appended only when RALPH_LOOP is present."""

    def fake_run(name: str, command: list[str], repo: Path, env: dict[str, str]) -> int:
        del name, command, repo, env
        return 0

    def fake_containment(repo: Path) -> list[str]:
        del repo
        return ["containment problem"]

    monkeypatch.setattr(gate, "run_one_check", fake_run)
    monkeypatch.setattr(gate, "run_non_human_checks", fake_containment)
    monkeypatch.delenv("RALPH_LOOP", raising=False)
    assert gate.call_tools(git_repo, {"ok": ["tool"]}) == {"pass": ["ok"], "fail": []}
    monkeypatch.setenv("RALPH_LOOP", "1")
    assert gate.call_tools(git_repo, {"ok": ["tool"]}) == {
        "pass": ["ok"],
        "fail": ["containment problem"],
    }


def test_lint_command_keeps_show_fixes_flag() -> None:
    """The lint command asks ruff to show applied and suggested fixes."""
    assert gate.COMMIT_CHECKS["ruff lint"] == [
        "uv",
        "run",
        "--no-cache",
        "--no-sync",
        "ruff",
        "check",
        "--show-fixes",
        ".",
    ]


def test_types_check_uses_pyright_json_output() -> None:
    """The types check runs pyright in JSON mode for stable machine-readable output."""
    assert gate.FULL_CHECKS["types"] == ["uv", "run", "--no-sync", "pyright", "--outputjson"]


def test_security_check_uses_semgrep_auto_and_secrets_configs() -> None:
    """The security check includes both Semgrep auto rules and the secrets ruleset."""
    command = gate.FULL_CHECKS["security"]
    assert command[:5] == ["uv", "run", "--no-sync", "semgrep", "scan"]
    assert "--config" in command
    assert "auto" in command
    assert "p/secrets" in command


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


def test_gate_pytest_command_enforces_full_coverage_and_buckets_failures() -> None:
    """The gate's pytest command keeps coverage reporting and the 100% coverage threshold."""
    pytest_command = gate.FULL_CHECKS["pytest"]
    assert pytest_command[:5] == ["uv", "run", "--no-cache", "--no-sync", "pytest"]
    assert "--cov" in pytest_command
    assert "--cov-report=term-missing" in pytest_command
    assert "--cov-fail-under=100" in pytest_command


def test_gate_buckets_a_failing_pytest_check(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """When the pytest command exits nonzero (e.g. a coverage gap), call_tools records it under 'fail'.
    Faking the run_one_check seam proves the bucketing without a real, recursive pytest subprocess.
    """

    def pytest_fails(name: str, command: list[str], repo: Path, env: dict[str, str]) -> int:
        del command, repo, env
        return 1 if name == "tests" else 0

    monkeypatch.setattr(gate, "run_one_check", pytest_fails)
    result = gate.call_tools(git_repo, {"tests": gate.FULL_CHECKS["pytest"]})
    assert result["fail"] == ["tests"]


def test_preflight_invokes_only_lint_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Integrated routing: run_preflight runs only the commit checks."""
    ran: list[str] = []

    def pass_check(name: str, command: list[str], repo: Path, env: dict[str, str]) -> int:
        del command, repo, env
        ran.append(name)
        return 0

    monkeypatch.delenv("RALPH_LOOP", raising=False)
    monkeypatch.setattr(gate, "run_one_check", pass_check)
    result = gate.run_preflight(tmp_path)
    assert set(ran) == {
        "ruff lint",
        "ruff format (no fail)",
        "complexipy",
    }
    assert result["pass"] == ran
    assert result["fail"] == []


def test_run_gate_delegates_to_call_tools_with_full_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_gate is a thin router: it runs exactly FULL_CHECKS on the given repo and returns that result.

    The real end-to-end behaviour of every gate check is proven in test_integration's single full-gate
    test; here we only pin the routing (repo + FULL_CHECKS in, call_tools' result out) without paying
    for real tools or risking the pytest check recursively collecting this suite.

    NO NESTED PYTEST: call_tools is stubbed with `spy`, so run_gate spawns nothing. This is the pattern
    to copy for any new routing assertion — stub call_tools instead of adding a real-pytest spawn.
    """
    seen: dict[str, object] = {}

    def spy(repo: Path, checks: dict[str, list[str]]) -> dict[str, list[str]]:
        seen["repo"], seen["checks"] = repo, checks
        return {"pass": ["types"], "fail": []}

    monkeypatch.setattr(gate, "call_tools", spy)
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

    def pass_check(name: str, command: list[str], repo: Path, env: dict[str, str]) -> int:
        del name, command, repo, env
        return 0

    monkeypatch.delenv("RALPH_LOOP", raising=False)
    monkeypatch.setattr(gate, "run_one_check", pass_check)
    stage(git_repo, "harness/util.py", "value = 1\n")
    assert gate.run_preflight(git_repo)["fail"] == []
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
    containment_fail(git_repo)
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


# ------------------------------------------------- check_for_bad_patterns (direct, no ejection wrapper)


def test_check_for_bad_patterns_flags_a_banned_pattern(git_repo: Path) -> None:
    """Called directly, it returns a banned-pattern problem for a staged added line carrying one."""
    stage(git_repo, "src/x.py", "value = 1  # noqa\n")
    problems = gate.check_for_bad_patterns(git_repo)
    assert any(problem.startswith("'noqa' line:") for problem in problems)


def test_check_for_bad_patterns_appends_a_preference_violation(git_repo: Path) -> None:
    """A staged .py file that breaks a preference contributes its violation to the returned problems."""
    stage(git_repo, "src/mod.py", "_bad = 1\n")  # lone-underscore name trips a preference
    problems = gate.check_for_bad_patterns(git_repo)
    assert any("'_bad'" in problem for problem in problems)


def test_check_for_bad_patterns_clean_staged_file_has_no_problems(git_repo: Path) -> None:
    """A staged file with no banned patterns and no preference breaks yields an empty problem list."""
    stage(git_repo, "src/ok.py", "value = 1\n")
    assert gate.check_for_bad_patterns(git_repo) == []


def test_check_for_bad_patterns_empty_index_warns_and_returns_no_problems(
    git_repo: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """With nothing staged, the diff is empty so the prefs branch is skipped: it warns to stage real
    work and returns no problems (the else branch that prints the stage-real-work notice).
    """
    problems = gate.check_for_bad_patterns(git_repo)  # clean index: seed commit only, nothing staged
    assert problems == []
    assert "Stage real work" in capfd.readouterr().out


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
