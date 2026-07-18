"""Property-based tests for harness.gate and harness.preferences using Hypothesis.

"With Hypothesis, you write tests which should pass for all inputs in whatever range you describe, and let
Hypothesis randomly choose which of those inputs to check - including edge cases you might not have thought
about."
Hypothesis docs: https://hypothesis.readthedocs.io/

This file keeps Hypothesis tests and the small example-based tests that share their helpers together.

Covered behavior:
  * banned-pattern scanning in gate.run_non_human_checks
  * preferences.py checks for names, classes, comprehensions, and continue
  * regression coverage for casefold-colliding forbidden paths in gate.py

Hypothesis persistence: Do not set database=None by default. Local runs use Hypothesis's example
database under .hypothesis/examples, so past failures are replayed first and users can debug them
quickly. CI automatically uses Hypothesis's built-in `ci` profile, which is stateless and deterministic.
If a generated input is important, save it as @example(...) or a normal regression test instead of
relying on the local database. The generated .hypothesis/ directory is gitignored.

Test hygiene: keep strategies at module scope. Set max_examples only when a test needs a runtime cap. Do
not use function-scoped fixtures with @given; patch per-example state inside helper functions instead.
"""

from __future__ import annotations

import importlib
import keyword
import string
import sys
from collections.abc import Callable
from unittest import mock

import pytest
from hypothesis import assume, example, given, settings
from hypothesis import strategies as st

from harness import gate

# preferences.py is optional. Gate tests in this file should still run when it is absent.
preferences_violations: Callable[[str, str], str] | None
try:
    from harness.preferences import preferences_violations
except ImportError:
    preferences_violations = None

# Patterns in a deterministic order so hypothesis shrinks toward the first entry predictably.
IDENTIFIER_START = string.ascii_letters + "_"
IDENTIFIER_REST = IDENTIFIER_START + string.digits


@st.composite
def identifiers(draw: st.DrawFn) -> str:
    """Draw a valid ASCII Python identifier that is not a keyword."""
    first = draw(st.sampled_from(IDENTIFIER_START))
    rest = draw(st.text(alphabet=IDENTIFIER_REST, max_size=24))
    name = first + rest
    assume(not keyword.iskeyword(name))
    return name


IDENTIFIERS = identifiers()


# ============================================================ banned-pattern scan (gate) helpers


def scan_banned(diff: str, patterns: set[str] | None = None) -> list[str]:
    """Drive run_non_human_checks' banned-pattern scan over a canned unified diff, no real git.

    Stubs run_git so the banned-pattern scan sees `diff` (returned for the `--unified=0` call). The
    name-only ACMRD call returns one non-forbidden path so the empty-commit guard treats the index as
    non-empty and nothing is ejected; every other git call returns "". prefs is forced to None so the
    scan under test is the sole source of problems.

    Args:
        diff: The `git diff --cached --unified=0` output the scan should read.
        patterns: Optional override for FORBIDDEN_PATTERNS (to inject a mixed-case entry).

    Returns:
        The problems list run_non_human_checks produced for that diff.
    """

    def fake_git(args: list[str]) -> str:
        if "--unified=0" in args:
            return diff
        if "--name-only" in args and "--diff-filter=ACMRD" in args:
            return "src/x.py\n"  # a real staged file, so the empty-commit guard doesn't short-circuit
        return ""

    with (
        mock.patch.object(gate, "run_git", fake_git),
        mock.patch.object(gate, "prefs", None),
        mock.patch.object(
            gate, "FORBIDDEN_PATTERNS", gate.FORBIDDEN_PATTERNS if patterns is None else patterns
        ),
    ):
        return gate.run_non_human_checks()


@st.composite
def added_line_with_pattern(draw: st.DrawFn) -> tuple[str, str]:
    """Draw a forbidden pattern and an added '+' diff line that embeds it in arbitrary casing.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        (pattern, diff_line) where diff_line is a '+' added line whose casefold contains the pattern.
    """
    pattern = draw(st.sampled_from(gate.FORBIDDEN_PATTERNS))
    # Recase each alphabetic character independently; symbols (e.g. in '--no-verify') pass through.
    recased = "".join(
        draw(st.sampled_from([char.lower(), char.upper()])) if char.isalpha() else char for char in pattern
    )
    return pattern, f"+value = 1  # {recased}"


@settings(max_examples=50)
@given(case=added_line_with_pattern())
@example(case=("# noqa", "+value = 1  # noqa"))  # lowercase-alpha pattern
@example(case=("--no-verify", "+value = 1  # --NO-verify"))  # symbol-heavy pattern
def test_banned_pattern_detected_across_arbitrary_line_casing(case: tuple[str, str]) -> None:
    """Every forbidden pattern is flagged on a '+value = 1  # {pattern}' add line however that line is
    cased. Generation earns its keep here: it recases each alpha char of each pattern independently,
    covering casings no finite table would enumerate. It does NOT prove the pattern-side casefold fix
    (all current patterns are lowercase, so recasing only the line never exercises it) -- that is
    test_mixed_case_forbidden_entry_still_matches' job.
    """
    pattern, diff_line = case
    problems = scan_banned(diff_line)
    assert any(p.startswith(f"'{pattern}' line:") for p in problems)


@pytest.mark.parametrize("diff_line", ["+++ b/hooksPath_helper.py", "-value = 1  # noqa"])
def test_banned_pattern_ignores_header_and_removed_lines(diff_line: str) -> None:
    """A pattern living only in a '+++' header or a removed '-' line is never flagged (only '+' adds)."""
    assert scan_banned(diff_line) == []


@pytest.mark.parametrize("entry_casing", ["hookspath", "HooksPath", "HOOKSPATH"])
def test_mixed_case_forbidden_entry_still_matches(entry_casing: str) -> None:
    """A mixed-case ENTRY in FORBIDDEN_PATTERNS is still detected on an added line. This is the
    regression guard for the source fix: with the old `pattern in line.casefold()`, a mixed-case entry
    like 'HooksPath' can never be a substring of a casefolded line, so it silently never matches. It
    FAILS on the pre-fix code (for the mixed-case entry) and PASSES once the comparison casefolds the
    pattern too. The lowercase entry is the control that matched under the old code as well.
    """
    problems = scan_banned("+value = 1  # hookspath", patterns={entry_casing})
    assert any(p.startswith(f"'{entry_casing}' line:") for p in problems)


# ============================================================ AST style checks (preferences)


def flags(source: str, needle: str) -> bool:
    """Whether preferences_violations reports a message containing needle for source."""
    if preferences_violations is None:
        pytest.skip("harness.preferences is optional and absent")
    return needle in preferences_violations("m.py", source)


# ------------------------------------------------------- preferences.py-absent fallback (optional module)


def test_module_tolerates_absent_preferences_on_import() -> None:
    """When harness.preferences cannot be imported (a human deleted it), this module still loads and its
    preferences_violations is None, so the gate tests here keep running. mock.patch.dict maps the module
    name to None (the standard way to make `import` raise ImportError) and auto-restores it; reloading
    this module under that patch exercises the ImportError fallback, then a final reload restores it.
    """
    module = sys.modules[__name__]
    try:
        with mock.patch.dict(sys.modules, {"harness.preferences": None}):
            reloaded = importlib.reload(module)
            assert reloaded.preferences_violations is None
    finally:
        importlib.reload(module)  # restore the real preferences_violations for the rest of the suite


def test_flags_skips_when_preferences_absent() -> None:
    """flags() skips (not crashes) when preferences_violations is None, so the AST-property tests skip as
    a group when the optional module is gone.
    """
    skipped = pytest.skip.Exception
    with mock.patch.object(sys.modules[__name__], "preferences_violations", None), pytest.raises(skipped):
        flags("x = 1\n", "anything")


# --------------------------------------------------------------------- underscore-lead identifier rule


@given(name=IDENTIFIERS)
def test_underscore_lead_flagged_iff_leading_underscore_not_dunder(name: str) -> None:
    """An assignment target trips the underscore rule IFF it starts with '_' and does not end with '__'.
    Covers the whole identifier domain, including dunders and lone '_', in one property.
    """
    expected = name.startswith("_") and not name.endswith("__")
    assert flags(f"{name} = 1\n", "starts with underscore") is expected


@given(name=IDENTIFIERS)
def test_underscore_rule_holds_for_function_and_argument_names(name: str) -> None:
    """The same underscore rule applies to function names and argument names, not just assignments."""
    assume(not name.endswith("__"))  # keep dunder methods/args (__init__ etc.) out of this slice
    expected = name.startswith("_")
    assert flags(f"def {name}():\n    return 1\n", "starts with underscore") is expected
    assert flags(f"def f({name}):\n    return {name}\n", "starts with underscore") is expected


# ------------------------------------------------------------------------------- pointless-class rule


@st.composite
def class_source(draw: st.DrawFn) -> tuple[str, bool]:
    """Draw a class definition varying base/decorator/keyword presence and method count.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        (source, should_flag) where should_flag is the documented intent: trip IFF the class has no
        base, no decorator, no keyword, and at most one method.
    """
    has_base = draw(st.booleans())
    has_decorator = draw(st.booleans())
    has_keyword = draw(st.booleans())
    method_count = draw(st.integers(min_value=0, max_value=3))

    decorator = "@deco\n" if has_decorator else ""
    header_bits = (["Base"] if has_base else []) + (["metaclass=type"] if has_keyword else [])
    header = f"({', '.join(header_bits)})" if header_bits else ""
    body = "".join(f"    def m{i}(self):\n        return {i}\n" for i in range(method_count)) or "    x = 1\n"
    source = f"{decorator}class C{header}:\n{body}"

    should_flag = not has_base and not has_decorator and not has_keyword and method_count <= 1
    return source, should_flag


@given(case=class_source())
def test_pointless_class_flagged_iff_plain_and_at_most_one_method(case: tuple[str, bool]) -> None:
    """A class trips the pointless-class rule IFF it is plain (no base/decorator/keyword) with <= 1
    method. Any base, decorator, keyword, or a second method exempts it.
    """
    source, should_flag = case
    assert flags(source, "no base, decorator, or behavior") is should_flag


# ------------------------------------------------------------------------ complex-comprehension rule


@st.composite
def comprehension_source(draw: st.DrawFn) -> tuple[str, bool]:
    """Draw a list comprehension with a chosen generator count and which generator (if any) filters.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        (source, should_flag) where should_flag is the documented intent: trip IFF there is more than
        one generator AND at least one generator carries an `if`. Crucially the filtered generator may
        be a LATER one, exercising the check's early-return-on-first-match loop.
    """
    generator_count = draw(st.integers(min_value=1, max_value=3))
    # -1 means "no if on any generator"; otherwise the index of the single generator that filters.
    if_on = draw(st.integers(min_value=-1, max_value=generator_count - 1))

    clauses: list[str] = []
    for index in range(generator_count):
        clause = f"for v{index} in xs{index}"
        if index == if_on:
            clause += f" if v{index}"
        clauses.append(clause)
    source = f"[v0 {' '.join(clauses)}]\n"

    should_flag = generator_count > 1 and if_on != -1
    return source, should_flag


@given(case=comprehension_source())
def test_complex_comprehension_flagged_iff_multi_generator_with_filter(case: tuple[str, bool]) -> None:
    """A comprehension trips IFF it has multiple generators AND at least one has an `if` -- regardless of
    WHICH generator carries the `if`. The later-generator case guards the check's early return, which
    scans generators in order and returns on the first one that filters.
    """
    source, should_flag = case
    assert flags(source, "Overly complex comprehension") is should_flag


# --------------------------------------------------------------------------- chaotic-continue rule


@st.composite
def nested_continue_source(draw: st.DrawFn) -> str:
    """Draw a `continue` wrapped in an outer `for` plus TWO-to-four more if/for blocks.

    The rule allows one `if` guard directly inside a loop (`for: if: continue`), so to always be
    over-nested we stack at least two blocks below the outer loop.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        Source whose `continue` sits at least two if/for blocks below its enclosing loop, so the
        over-nesting rule always flags it.
    """
    depth = draw(st.integers(min_value=2, max_value=4))
    blocks = draw(st.lists(st.sampled_from(["if cond", "for i in xs"]), min_size=depth, max_size=depth))

    lines = ["for outer in items:"]  # an outer loop the continue always belongs to
    indent = "    "
    for block in blocks:
        lines.append(f"{indent}{block}:")
        indent += "    "
    lines.append(f"{indent}continue")
    return "\n".join(lines) + "\n"


@given(source=nested_continue_source())
def test_continue_nested_under_stacked_blocks_is_flagged(source: str) -> None:
    """A `continue` stacked two or more if/for blocks below its enclosing loop is flagged as overly
    nested, whatever mix of if/for those blocks are.
    """
    assert flags(source, "Overly-nested 'continue'")


def test_single_if_guard_in_a_loop_is_not_flagged() -> None:
    """The common, readable `for ...: if ...: continue` (one if guard in one loop) is NOT over-nested."""
    assert not flags("for i in items:\n    if skip:\n        continue\n", "Overly-nested 'continue'")


def test_shallow_continue_in_single_loop_is_not_flagged() -> None:
    """Control (example, not property): a `continue` directly in one `for` -- parent For, grandparent
    module -- is not overly nested, so it is not flagged.
    """
    assert not flags("for x in items:\n    continue\n", "Overly-nested 'continue'")


def test_continue_in_while_loop_is_flagged() -> None:
    """Control (example): a `continue` anywhere inside a while loop is flagged (freeze risk), a separate
    branch from the nested-if detection.
    """
    assert flags("while cond:\n    continue\n", "while loop banned")


# ============================================================ casefold path-collision ejection (gate)
# RED regression: fails until run_non_human_checks ejects EVERY casefold-colliding forbidden path.


def reset_paths_for(staged: list[str]) -> list[str] | None:
    """Run run_non_human_checks over a canned staged list and capture the paths passed to `git reset`.

    Stubs run_git so `--name-only` returns the given staged paths and every other git call (the reset,
    the unified diff, prefs) returns "". prefs is forced to None. Returns the path arguments of the
    `reset -q HEAD --` call, or None if no reset was issued. Two colliding staged strings are injected
    here rather than as real files (a case-insensitive filesystem could not hold both), so this exercises
    the pure casefold-map logic.

    Args:
        staged: The staged paths the ejection scan should see.

    Returns:
        The list of paths git reset was asked to unstage, or None if ejection did not run.
    """
    reset_args: list[str] | None = None

    def fake_git(args: list[str]) -> str:
        nonlocal reset_args
        if args[:1] == ["reset"]:
            reset_args = args[args.index("--") + 1 :]
            return ""
        if "--name-only" in args and "--diff-filter=ACMRD" in args:
            return "\n".join(staged)
        return ""

    with mock.patch.object(gate, "run_git", fake_git), mock.patch.object(gate, "prefs", None):
        gate.run_non_human_checks()
    return reset_args


def test_casefold_colliding_forbidden_paths_are_both_ejected() -> None:
    """Two forbidden staged paths that differ only by case must BOTH be handed to git reset. Intended
    behavior: neither slips past ejection. Fails hard while `{sf.casefold(): sf}` collapses them to one
    key -- a real containment bug; this red test is the hand-off signal to fix run_non_human_checks.
    """
    colliding = ["harness/Gate.py", "harness/gate.py"]  # both under the forbidden 'harness/' dir
    reset = reset_paths_for(colliding)
    assert reset is not None, "ejection did not run for forbidden paths"
    assert set(reset) == set(colliding), f"only {reset} ejected; a casefold-colliding path slipped through"
