"""Tests for AST-based structural style checks (harness.preferences).

The preferences API is a registry of single-node `Check` functions (each takes one `ast.AST`
node and returns a complaint string or None), plus `preferences_violations`, which walks a file
once and returns a dict grouping every complaint by check kind. These tests exercise that API
with real code that trips — and real code that must not trip — each check.
"""

from __future__ import annotations

import ast

import pytest

# preferences.py is optional — humans may delete it; skip its tests when it is gone.
preferences = pytest.importorskip("harness.preferences")
preferences_violations = preferences.preferences_violations
CHECKS = preferences.CHECKS


def complaints(check_name: str, source: str) -> list[str]:
    """Run one registered single-node check over every node in source, collecting its complaints."""
    check = CHECKS[check_name]
    found = []
    for node in ast.walk(ast.parse(source)):
        message = check(node)
        if message is not None:
            found.append(message)
    return found


# --------------------------------------------------------------------------- single-node checks


def test_underscore_names_flagged() -> None:
    """Function, argument, and assigned names starting with a lone underscore are flagged."""
    source = "def _hidden(_arg):\n    _value = 1\n    return _value\n"
    found = complaints("function_argument_assignment_has_star", source)
    assert len(found) == 3
    assert any("'_hidden'" in message for message in found)
    assert any("'_arg'" in message for message in found)
    assert any("'_value'" in message for message in found)


def test_bare_underscore_flagged() -> None:
    """The throwaway underscore variable is also banned."""
    assert len(complaints("function_argument_assignment_has_star", "for _ in [1]:\n    pass\n")) == 1


def test_dunder_names_exempt() -> None:
    """Dunder names like __all__ and __init__ are not flagged."""
    source = "__all__ = []\n\n\nclass Box(dict):\n    def __init__(self):\n        super().__init__()\n"
    assert complaints("function_argument_assignment_has_star", source) == []


def test_star_unpacking_flagged() -> None:
    """Call splats, double-star splats, and starred assignment are flagged."""
    source = "f(*items)\ng(**options)\nfirst, *rest = [1, 2]\n"
    assert len(complaints("star", source)) == 3


def test_star_signatures_not_flagged() -> None:
    """*args/**kwargs in a signature are allowed; only call/assignment splats are flagged."""
    source = "def f(*args):\n    return args\n\n\ndef g(**kwargs):\n    return kwargs\n"
    assert complaints("star", source) == []


def test_pointless_class_flagged() -> None:
    """A class with no base, decorator, and one method is flagged."""
    found = complaints("class", "class Holder:\n    def get(self):\n        return 1\n")
    assert len(found) == 1
    assert "'Holder'" in found[0]


def test_useful_classes_pass() -> None:
    """Dataclasses, subclasses, keyword-based classes, and stateful classes pass."""
    source = (
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\n"
        "class Point:\n    x: int\n\n\n"
        "class CustomError(Exception):\n    pass\n\n\n"
        "class Meta(metaclass=type):\n    pass\n\n\n"
        "class Machine:\n"
        "    def start(self):\n        return 1\n\n"
        "    def stop(self):\n        return 0\n"
    )
    assert complaints("class", source) == []


def test_lambda_flagged() -> None:
    """Every lambda is flagged, not only the E731 name-assignment case ruff catches."""
    assert len(complaints("lambda_found", "sorted(xs, key=lambda item: item.rank)\n")) == 1


def test_lazy_any_type_hint_flagged() -> None:
    """An argument annotated Any (bare or typing.Any) is flagged."""
    assert len(complaints("lazy_any_type_hints", "def f(x: Any):\n    return x\n")) == 1
    assert len(complaints("lazy_any_type_hints", "def g(x: typing.Any):\n    return x\n")) == 1


def test_continue_in_while_loop_flagged() -> None:
    """A continue inside a while loop is flagged (infinite-freeze risk)."""
    assert complaints("chaotic_continue_statements", "while True:\n    if x:\n        continue\n")


def test_continue_nested_in_stacked_ifs_flagged() -> None:
    """A continue nested under two if-statements is flagged on its own line (line 4 here), proving the
    parent links let the check see the grandparent If and that the reported line number is real.
    """
    source = "for i in items:\n    if a:\n        if b:\n            continue\n"
    violations = preferences_violations("m.py", source)
    assert "m.py:4: Overly-nested 'continue'" in violations


def test_continue_in_elif_is_flagged_as_nested() -> None:
    """KNOWN BEHAVIOR (arguably a false positive): a continue in an `elif` trips the nested-if check,
    because `elif` desugars to an If in the outer If's orelse, so parent.parent is an If. This test
    pins the current source behavior so a change to it is a deliberate, visible decision.
    """
    source = "for i in x:\n    if a:\n        pass\n    elif b:\n        continue\n"
    assert "Overly-nested" in preferences_violations("m.py", source)


def test_plain_continue_in_for_loop_not_flagged() -> None:
    """A continue in a simple for loop (not a while loop, not nested in ifs) is allowed."""
    assert complaints("chaotic_continue_statements", "for i in items:\n    continue\n") == []


def test_lazy_assert_flagged() -> None:
    """An assert on a constant or literal container tests nothing and is flagged."""
    assert complaints("lasy_assert", "assert True\n")  # constant
    assert complaints("lasy_assert", "assert []\n")  # literal container
    assert complaints("lasy_assert", "assert real_condition\n") == []  # a real check passes


def test_globals_and_locals_injection_flagged() -> None:
    """Calling globals()/locals() to poke the runtime registry is flagged; a plain call is not."""
    assert complaints("objects_injected_into_runtime_memory", "globals()['x'] = 1\n")
    assert complaints("objects_injected_into_runtime_memory", "locals()\n")
    assert complaints("objects_injected_into_runtime_memory", "sorted(items)\n") == []


def test_complex_multi_generator_comprehension_flagged() -> None:
    """A comprehension with multiple generators AND a filter is flagged; a simple one is not."""
    assert complaints("complex_comprehension", "[a for row in grid for a in row if a]\n")
    assert complaints("complex_comprehension", "[a for a in row if a]\n") == []


# --------------------------------------------------------------------- preferences_violations (the walk)


def test_preferences_violations_returns_grouped_str() -> None:
    """The walk returns a string; a clean file produces the empty string (no groups)."""
    violations = preferences_violations("m.py", "VALUE = 1\n")
    assert isinstance(violations, str)
    assert not violations


def test_clean_file_has_no_complaints() -> None:
    """A compliant module produces no complaints (the empty string)."""
    source = (
        '"""Module."""\n\n'
        "VALUE = 1\n\n\n"
        "def double(number: int) -> int:\n"
        '    """Double the number."""\n'
        "    return number * 2\n"
    )
    assert not preferences_violations("m.py", source)


def test_a_clean_file_reports_only_the_kind_that_fired() -> None:
    """A file that trips exactly one check reports that check's message and no other: only lambda_found
    fires here, so the underscore/star messages must be absent.
    """
    violations = preferences_violations("m.py", "value = lambda a: a\n")  # only lambda_found fires
    assert violations == "m.py:1: Lambda found hurting readability and adding complexity."
    assert "starts with underscore" not in violations
    assert "Star unpacking" not in violations


def test_dirty_file_lists_each_violation_on_its_own_line() -> None:
    """Two checks trip on line 1; each is rendered as one `m.py:1: <message>` line with the real line
    number, and the two are newline-joined into a single string — pinning the exact flat format.
    """
    violations = preferences_violations("m.py", "_x = lambda a: a\n")
    assert "m.py:1: Name '_x' starts with underscore" in violations
    assert "m.py:1: Lambda found" in violations
    assert violations.count("\n") == 1  # two messages joined by a single newline


def test_line_number_in_message_is_accurate() -> None:
    """The reported line number is the violation's real line, not always 1: a lambda on line 3
    reports :3:, proving the walk carries each node's lineno into its message.
    """
    source = "value = 1\n\n\nother = lambda a: a\n"  # lambda is on line 4
    assert "m.py:4: Lambda found" in preferences_violations("m.py", source)


def test_repeated_violations_of_one_kind_each_get_a_line() -> None:
    """Multiple hits of the same check each produce their own line (not collapsed): two lambdas on two
    lines yield two `Lambda found` messages on two separate lines.
    """
    violations = preferences_violations("m.py", "a = lambda x: x\nb = lambda y: y\n")
    assert violations.count("Lambda found") == 2
    assert violations.count("\n") == 1  # two messages, one joining newline


def test_syntax_error_raises() -> None:
    """Unparseable source raises SyntaxError; preferences does not swallow it."""
    with pytest.raises(SyntaxError):
        preferences_violations("m.py", "def broken(:\n")
