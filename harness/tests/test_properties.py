"""Property-based tests for the banned-pattern scan and forbidden-path ejection in harness.gate.

"With Hypothesis, you write tests which should pass for all inputs in whatever range you describe, and let
Hypothesis randomly choose which of those inputs to check, including edge cases you might not have thought
about." TESTS THE CODE WITH A RANGE OF INPUTS.
Hypothesis docs: https://hypothesis.readthedocs.io/

Integration tests here stage real files in a temp repo and reach Git through the real gate.run_git.
The casing property feeds a production-shaped staged diff through the real preflight matcher.

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
from unittest.mock import Mock

import pytest
from hypothesis import given, settings, strategies

from harness import gate
from harness.gate import gates


def scan_staged(repo: Path, source: str) -> list[str]:
    """Stage one file, run the real banned-pattern scan over the real index, then clear the index."""
    (repo / "x.py").write_text(source, encoding="utf-8")
    gate.run_git(["add", "x.py"], repo)
    problems = gates().run_preflight()["fail"]
    gate.run_git(["reset", "-q"], repo)
    return problems


def scan_synthetic_diff(source: str) -> list[str]:
    """Run the real preflight over a synthetic staged diff."""
    staged_diff = "+++ b/x.py\n" + "".join(f"a{line}" for line in source.splitlines(keepends=True))
    git = Mock(side_effect=["HEAD\n", "x.py\n", staged_diff, "", "1\t0\tx.py\n"])
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("RALPH_LOOP", "1")
        patch.setattr(gates(), "commit_checks", {})
        patch.setattr(gate, "run_git", git)
        return gates().run_preflight()["fail"]


def assert_all_patterns_reported(problems: list[str]) -> None:
    """Assert one finding for every configured forbidden pattern."""
    reported = set(problems[0].splitlines()[1:])
    expected = {f"x.py: '{pattern}'" for pattern in gates().forbidden_patterns}

    assert problems[0].splitlines()[0] == "FORBIDDEN FOR AGENT:"
    assert reported == expected


@strategies.composite
def recased_patterns(draw: strategies.DrawFn) -> tuple[str, ...]:
    """Draw a forbidden pattern and the same pattern in arbitrary casing.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        (pattern, recased) where recased differs from pattern only in the case of its letters.
    """
    return tuple(
        "".join(
            draw(strategies.sampled_from((char.lower(), char.upper()))) if char.isalpha() else char
            for char in pattern
        )
        for pattern in gates().forbidden_patterns
    )


@settings(max_examples=50, deadline=None)
@given(recased=recased_patterns())
def test_banned_pattern_detected_across_arbitrary_line_casing(recased: tuple[str, ...]) -> None:
    """An agent cannot smuggle an escape hatch past the scan by changing its capitalization. Every
    forbidden pattern is caught on an added line however that line is cased.
    """
    source = "".join(f"value_{index} = 1  # {pattern}\n" for index, pattern in enumerate(recased))
    source += "lowercase = 1  # noqa\nsymbol_heavy = 1  # --NO-verify\n"
    assert_all_patterns_reported(scan_synthetic_diff(source))


def test_banned_pattern_scan_uses_real_staged_diff(scan_repo: Path) -> None:
    """The production Git diff path reports every configured forbidden pattern."""
    source = "".join(
        f"value_{index} = 1  # {pattern}\n" for index, pattern in enumerate(gates().forbidden_patterns)
    )
    assert_all_patterns_reported(scan_staged(scan_repo, source))


@pytest.mark.parametrize("pattern_in_toml", ["hookspath", "HooksPath", "HOOKSPATH"])
def test_mixed_case_forbidden_entry_still_matches(
    pattern_in_toml: str, monkeypatch: pytest.MonkeyPatch, git_repo: Path
) -> None:
    """Editing pyproject.toml is how people add forbidden patterns, and they will not all type in
    lowercase. A mixed-case entry has to match too, which it only does because the scan casefolds the
    pattern as well as the line.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gates(), "commit_checks", {})
    monkeypatch.setattr(gates(), "forbidden_patterns", (pattern_in_toml,))
    problems = scan_staged(git_repo, "value = 1  # hookspath\n")
    assert problems == [f"FORBIDDEN FOR AGENT:\nx.py: '{pattern_in_toml}'"]


def test_banned_pattern_ignores_the_diff_file_header(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """A file whose name contains a forbidden pattern puts that pattern in the diff's '+++ b/...'
    header. The header is not code an agent added, so it must not be reported.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gates(), "commit_checks", {})
    (git_repo / "noqa_helpers.py").write_text("value = 1\n", encoding="utf-8")
    gate.run_git(["add", "noqa_helpers.py"], git_repo)
    assert gates().run_preflight() == {"pass": ["mutmut"], "fail": [], "warn": []}


def test_banned_pattern_ignores_a_removed_line(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    """Deleting a line that carries an escape hatch is the fix, not the offense, so a removed '-' line
    is never reported.
    """
    monkeypatch.setenv("RALPH_LOOP", "1")
    monkeypatch.setattr(gates(), "commit_checks", {})
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
    monkeypatch.setattr(gates(), "commit_checks", {})
    assert (gates().run_preflight(), gate.run_git(["diff", "--cached", "--name-only"]).splitlines()) == (
        {"pass": ["mutmut"], "fail": [], "warn": []},
        [],
    )
