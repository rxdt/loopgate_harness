"""Property-based tests for preferences.preferences.py using Hypothesis.

"With Hypothesis, you write tests which should pass for all inputs in whatever range you describe, and let
Hypothesis randomly choose which of those inputs to check, including edge cases you might not have thought
about." TESTS THE CODE WITH A RANGE OF INPUTS.
Hypothesis docs: https://hypothesis.readthedocs.io/

Tests that preferences.py checks for names, classes, comprehensions, and continue

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

from hypothesis import assume, given, strategies

# preferences.py are optional AST-style checks. Repo-owner can keep or delete.
preferences_violations: Callable[[str, str], str] | None
try:
    from preferences.preferences import preferences_violations
except ImportError:
    preferences_violations = None

# Patterns in a deterministic order so hypothesis shrinks toward the first entry predictably.
IDENTIFIER_START = string.ascii_letters + "_"
IDENTIFIER_REST = IDENTIFIER_START + string.digits


@strategies.composite
def identifiers(draw: strategies.DrawFn) -> str:
    """Draw a valid ASCII Python identifier that is not a keyword."""
    first = draw(strategies.sampled_from(IDENTIFIER_START))
    rest = draw(strategies.text(alphabet=IDENTIFIER_REST, max_size=24))
    name = first + rest
    assume(not keyword.iskeyword(name))
    return name


IDENTIFIERS = identifiers()


def flags(source: str, needle: str) -> bool:
    """Whether preferences_violations reports a message containing needle for source."""
    return needle in preferences_violations("m.py", source) if preferences_violations else False


# ------------------------------------------------------- preferences.py-absent fallback (optional module)


def test_module_tolerates_absent_preferences_on_import() -> None:
    """When preferences.preferences.py can't be imported (e.g. human deleted), this module still loads and its
    preferences_violations is None, so its gate tests here keep running. mock.patch.dict maps the module
    name to None (the standard way to make `import` raise ImportError) and auto-restores it; reloading
    this module under that patch exercises the ImportError fallback, then a final reload restores it.
    """
    module = sys.modules[__name__]
    try:
        with mock.patch.dict(sys.modules, {"preferences.preferences": None}):
            reloaded = importlib.reload(module)
            assert reloaded.preferences_violations is None
    finally:
        importlib.reload(module)  # restore the real preferences_violations for the rest of the suite


def test_flags_never_calls_preferences_when_it_is_absent() -> None:
    """Deleting preferences.py must not crash flags(). It reports no violation instead.

    A variable named with a leading underscore breaks the underscore rule. The first assert confirms
    the rule fires on it. The second feeds in that same variable with the module gone and gets no
    violation back, which is only possible if preferences_violations was never called.
    """
    source, rule = "_bad = 1\n", "starts with underscore"
    assert flags(source, rule) is True
    with mock.patch.object(sys.modules[__name__], "preferences_violations", None):
        assert flags(source, rule) is False


# --------------------------------------------------------------------- underscore-lead identifier rule


@given(name=IDENTIFIERS)
def test_underscore_lead_flagged_iff_leading_underscore_not_dunder(name: str) -> None:
    """An assignment target trips the underscore rule IFF it has a prohibited leading underscore.
    Covers the whole identifier domain, including dunders and the exempt lone '_', in one property.
    """
    expected = name != "_" and name.startswith("_") and not name.startswith("__") and not name.endswith("__")
    assert flags(f"{name} = 1\n", "starts with underscore") is expected


@given(name=IDENTIFIERS)
def test_underscore_rule_holds_for_function_and_argument_names(name: str) -> None:
    """The same underscore rule applies to function names and argument names, not just assignments."""
    assume(not name.endswith("__"))  # keep dunder methods/args (__init__ etc.) out of this slice
    expected = name != "_" and name.startswith("_") and not name.startswith("__")
    assert flags(f"def {name}():\n    return 1\n", "starts with underscore") is expected
    assert flags(f"def f({name}):\n    return {name}\n", "starts with underscore") is expected


# ------------------------------------------------------------------------------- pointless-class rule


@strategies.composite
def class_source(draw: strategies.DrawFn) -> tuple[str, bool]:
    """Draw a class definition varying base/decorator/keyword presence and method count.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        (source, should_flag) where should_flag is the documented intent: trip IFF the class has no
        base, no decorator, no keyword, and at most one method.
    """
    has_base = draw(strategies.booleans())
    has_decorator = draw(strategies.booleans())
    has_keyword = draw(strategies.booleans())
    method_count = draw(strategies.integers(min_value=0, max_value=3))

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


@strategies.composite
def comprehension_source(draw: strategies.DrawFn) -> tuple[str, bool]:
    """Draw a list comprehension with a chosen generator count and which generator (if any) filters.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        (source, should_flag) where should_flag is the documented intent: trip IFF there is more than
        one generator AND at least one generator carries an `if`. Crucially the filtered generator may
        be a LATER one, exercising the check's early-return-on-first-match loop.
    """
    generator_count = draw(strategies.integers(min_value=1, max_value=3))
    # -1 means "no if on any generator"; otherwise the index of the single generator that filters.
    if_on = draw(strategies.integers(min_value=-1, max_value=generator_count - 1))

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


@strategies.composite
def nested_continue_source(draw: strategies.DrawFn) -> str:
    """Draw a `continue` wrapped in an outer `for` plus TWO-to-four more if/for blocks.

    The rule allows one `if` guard directly inside a loop (`for: if: continue`), so to always be
    over-nested we stack at least two blocks below the outer loop.

    Args:
        draw: Hypothesis draw callable.

    Returns:
        Source whose `continue` sits at least two if/for blocks below its enclosing loop, so the
        over-nesting rule always flags it.
    """
    depth = draw(strategies.integers(min_value=2, max_value=4))
    blocks = draw(strategies.lists(strategies.sampled_from(["if cond", "for i in xs"]), min_size=depth, max_size=depth))

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
