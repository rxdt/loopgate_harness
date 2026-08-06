"""Property-based tests for the banned-pattern scan and forbidden-path ejection in harness.gate.

"With Hypothesis, you write tests which should pass for all inputs in whatever range you describe, and let
Hypothesis randomly choose which of those inputs to check, including edge cases you might not have thought
about." TESTS THE CODE WITH A RANGE OF INPUTS.
Hypothesis docs: https://hypothesis.readthedocs.io/

Every test here stages real files in a temp repo and calls the real gate functions, which reach git
through the real gate.run_git. Nothing about git is stubbed.

Hypothesis persistence: Do not set database=None by default. Local runs use Hypothesis's example
database under .hypothesis/examples, so past failures are replayed first and users can debug them
quickly. CI automatically uses Hypothesis's built-in `ci` profile, which is stateless and deterministic.
If a generated input is important, save it as @example(...) or a normal regression test instead of
relying on the local database. The generated .hypothesis/ directory is gitignored.

Test hygiene: keep strategies at module scope. Set max_examples only when a test needs a runtime cap. Do
not use function-scoped fixtures with @given; patch per-example state inside helper functions instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import example, given, settings, strategies

from harness import gate
from harness.gate import gates


def scan_staged(repo: Path, source: str) -> list[str]:
    """Stage one file, run the real banned-pattern scan over the real index, then clear the index."""
    (repo / "x.py").write_text(source, encoding="utf-8")
    gate.run_git(["add", "x.py"], repo)
    problems = gates.run_preflight()["fail"]
    gate.run_git(["reset", "-q"], repo)
    return problems


@strategies.composite
def recased_pattern(draw: strategies.DrawFn) -> tuple[str, str]:
    """Draw a forbidden pattern and the same pattern in arbitrary casing.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        (pattern, recased) where recased differs from pattern only in the case of its letters.
    """
    pattern = draw(strategies.sampled_from(gates.forbidden_patterns))
    # Recase each alphabetic character independently; symbols (e.g. in '--no-verify') pass through.
    recased = "".join(
        draw(strategies.sampled_from([char.lower(), char.upper()])) if char.isalpha() else char
        for char in pattern
    )
    return pattern, recased


@settings(max_examples=50, deadline=None)
@given(case=recased_pattern())
@example(case=("# noqa", "# noqa"))  # lowercase-alpha pattern
@example(case=("--no-verify", "--NO-verify"))  # symbol-heavy pattern
def test_banned_pattern_detected_across_arbitrary_line_casing(case: tuple[str, str], scan_repo: Path) -> None:
    """An agent cannot smuggle an escape hatch past the scan by changing its capitalization. Every
    forbidden pattern is caught on an added line however that line is cased.
    """
    pattern, recased = case
    problems = scan_staged(scan_repo, f"value = 1  # {recased}\n")
    assert any(problem.startswith(f"'{pattern}' line:") for problem in problems)


@pytest.mark.parametrize("pattern_in_toml", ["hookspath", "HooksPath", "HOOKSPATH"])
def test_mixed_case_forbidden_entry_still_matches(
    pattern_in_toml: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Editing pyproject.toml is how people add forbidden patterns, and they will not all type in
    lowercase. A mixed-case entry has to match too, which it only does because the scan casefolds the
    pattern as well as the line.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gates, "commit_checks", {})
    monkeypatch.setattr(gates, "forbidden_patterns", (pattern_in_toml,))
    problems = scan_staged(git_repo, "value = 1  # hookspath\n")
    assert any(problem.startswith(f"'{pattern_in_toml}' line:") for problem in problems)


def test_banned_pattern_ignores_the_diff_file_header(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A file whose name contains a forbidden pattern puts that pattern in the diff's '+++ b/...'
    header. The header is not code an agent added, so it must not be reported.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gates, "commit_checks", {})
    (git_repo / "noqa_helpers.py").write_text("value = 1\n", encoding="utf-8")
    gate.run_git(["add", "noqa_helpers.py"], git_repo)
    assert gates.run_preflight() == {"pass": [], "fail": [], "warn": []}


def test_banned_pattern_ignores_a_removed_line(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Deleting a line that carries an escape hatch is the fix, not the offense, so a removed '-' line
    is never reported.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gates, "commit_checks", {})
    scan_staged(git_repo, "value = 1  # noqa\n")
    gate.run_git(["add", "x.py"], git_repo)
    gate.run_git(["commit", "-q", "-m", "seed noqa"], git_repo)
    assert scan_staged(git_repo, "value = 1\n") == []


def test_casefold_colliding_forbidden_paths_are_both_ejected(
    monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Two forbidden paths differing only in case must both be unstaged. A case-insensitive filesystem
    cannot hold both as files, so they go into the index directly; neither may slip through.
    """
    colliding = ["harness/Gate.py", "harness/gate.py"]
    monkeypatch.setenv("RALPH_LOOP", "1")
    blob = gate.run_git(["hash-object", "-w", "README.md"], git_repo).strip()
    for path in colliding:
        gate.run_git(["update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"], git_repo)
    monkeypatch.setattr(gates, "commit_checks", {})
    assert (
        gates.run_preflight(),
        gate.run_git(["diff", "--cached", "--name-only"]).splitlines(),
    ) == ({"pass": [], "fail": [], "warn": []}, [])
