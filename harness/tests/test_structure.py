"""Tests for AST-based structural style checks (harness.preferences).

The preferences API is a registry of single-node `Check` functions (each takes one `ast.AST`
node and returns a complaint string or None), plus `preferences_violations`, which walks a file
once and returns a dict grouping every complaint by check kind. These tests exercise that API
with real code that trips — and real code that must not trip — each check.
"""

from __future__ import annotations

import ast
import inspect

import pytest

# preferences.py is optional — humans may delete it; skip its tests when it is gone.
preferences = pytest.importorskip("harness.preferences")
preferences_violations = preferences.preferences_violations
CHECKS = preferences.CHECKS


def complaints(check_name: str, source: str) -> list[str]:
    """Run one registered single-node check over every node in source, collecting its complaints."""
    check = CHECKS[check_name]
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        message = check(node)
        if message is not None:
            found.append(message)
    return found


# --------------------------------------------------------------------------- registry integrity


def test_every_check_key_matches_its_function_name() -> None:
    """Each CHECKS key must equal its check function's __name__, so the registry label never lies about
    (or drifts from) the rule it runs. Guards renames and copy-paste key mistakes for every entry.
    """
    mismatched = {key: fn.__name__ for key, fn in CHECKS.items() if key != fn.__name__}
    assert mismatched == {}, f"CHECKS keys must match their function names; mismatches: {mismatched}"


def test_every_check_shaped_function_is_registered() -> None:
    """Every check-shaped function in preferences.py (one param, returns `str | None`) must be in CHECKS.
    Catches a check that is defined but never wired up -- e.g. dropping
    `function_argument_assignment_underscore_lead` from the registry would silently stop enforcing it.
    Helpers like `starless_literal` (returns bool) and `preferences_violations` (two args) are excluded.
    """
    registered = set(CHECKS.values())
    unregistered = [
        name
        for name, fn in inspect.getmembers(preferences, inspect.isfunction)
        if fn.__module__ == preferences.__name__
        and list(inspect.signature(fn).parameters) == ["node"]
        and fn.__annotations__.get("return") == "str | None"
        and fn not in registered
    ]
    assert unregistered == [], f"check-shaped functions defined but not registered in CHECKS: {unregistered}"


# --------------------------------------------------------------------------- single-node checks


def test_underscore_names_flagged() -> None:
    """Function, argument, and assigned names starting with a lone underscore are flagged."""
    source = "def _hidden(_arg):\n    _value = 1\n    return _value\n"
    found = complaints("function_argument_assignment_underscore_lead", source)
    assert len(found) == 3
    assert any("'_hidden'" in message for message in found)
    assert any("'_arg'" in message for message in found)
    assert any("'_value'" in message for message in found)


def test_bare_underscore_flagged() -> None:
    """The throwaway underscore variable is also banned."""
    assert len(complaints("function_argument_assignment_underscore_lead", "for _ in [1]:\n    pass\n")) == 1


def test_dunder_names_exempt() -> None:
    """Dunder names like __all__ and __init__ are not flagged."""
    source = "__all__ = []\n\n\nclass Box(dict):\n    def __init__(self):\n        super().__init__()\n"
    assert complaints("function_argument_assignment_underscore_lead", source) == []


def test_hidden_signature_star_args_flagged() -> None:
    """A def declaring *args or **kwargs hides its signature and is flagged (strict, no exemption)."""
    assert len(complaints("hidden_signature_star_args", "def f(*args, **kwargs):\n    return args\n")) == 1
    assert len(complaints("hidden_signature_star_args", "def g(*args):\n    return args\n")) == 1
    assert len(complaints("hidden_signature_star_args", "def h(**kwargs):\n    return kwargs\n")) == 1


def test_hidden_signature_flags_even_decorated_and_inner_wrappers() -> None:
    """No wrapper/decorator exemption: intent is not AST-detectable, so decorated and inner *args/**kwargs
    defs are flagged too. This is a strict, optional house-style rule.
    """
    assert complaints("hidden_signature_star_args", "@deco\ndef w(*args, **kwargs):\n    return 1\n")
    inner = "def deco(fn):\n    def wrapper(*args):\n        return fn(*args)\n    return wrapper\n"
    assert complaints("hidden_signature_star_args", inner)


def test_explicit_signature_not_flagged() -> None:
    """A def with explicit parameters (no *args/**kwargs) is not flagged."""
    assert complaints("hidden_signature_star_args", "def f(x, y):\n    return x\n") == []


def test_hidden_signature_flags_async_def() -> None:
    """An async def with *args is flagged too (AsyncFunctionDef, not just FunctionDef)."""
    assert len(complaints("hidden_signature_star_args", "async def f(*args):\n    return args\n")) == 1


def test_bare_star_and_slash_separators_not_flagged() -> None:
    """The '*' keyword-only separator and '/' positional-only marker are not *args/**kwargs, so a def
    using them with named parameters is allowed.
    """
    assert complaints("hidden_signature_star_args", "def f(a, *, b):\n    return b\n") == []  # kw-only
    assert complaints("hidden_signature_star_args", "def f(a, b, /):\n    return a\n") == []  # pos-only


# Nested dict unpacking is owned by Ruff PIE800 (see test_gate.py::
# test_ruff_catches_nested_dict_unpacking_and_suggests_flat_merge), so preferences.py does not duplicate it.


def test_dynamic_star_call_flagged() -> None:
    """Splatting a non-literal sequence (a name, or a literal containing a '*') into a call is flagged."""
    assert len(complaints("dynamic_star_call", "f(*my_list)\n")) == 1
    assert len(complaints("dynamic_star_call", "f(*[1, *items])\n")) == 1
    assert len(complaints("dynamic_star_call", "f(*(1, *items))\n")) == 1


def test_literal_star_call_and_double_star_not_flagged() -> None:
    """A fixed-shape list/tuple literal splat is fine, and '**' keyword unpacking in a call is allowed."""
    assert complaints("dynamic_star_call", "f(*[1, 2, 3])\n") == []
    assert complaints("dynamic_star_call", "f(*(1, 2))\n") == []
    assert complaints("dynamic_star_call", "f(**kwargs)\n") == []
    assert complaints("dynamic_star_call", "f(a, b)\n") == []


def test_dynamic_star_flagged_alongside_normal_args_and_on_methods() -> None:
    """The '*' splat is judged on its own: a normal argument beside it does not excuse it, and method
    calls (obj.m(*x)) are calls too. Two splats in one call still report (the first one hit).
    """
    assert len(complaints("dynamic_star_call", "f(a, *rest)\n")) == 1  # normal arg + dynamic splat
    assert len(complaints("dynamic_star_call", "obj.method(*rest)\n")) == 1  # attribute call
    assert len(complaints("dynamic_star_call", "f(*xs, *ys)\n")) == 1  # returns on the first splat


def test_empty_literal_splat_not_flagged() -> None:
    """An empty list/tuple literal is a fixed (zero) length, so f(*[]) is not flagged."""
    assert complaints("dynamic_star_call", "f(*[])\n") == []


def test_pointless_class_flagged() -> None:
    """A class with no base, decorator, and one method is flagged."""
    found = complaints("pointless_class", "class Holder:\n    def get(self):\n        return 1\n")
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
    assert complaints("pointless_class", source) == []


def test_pointless_class_exempt_by_any_single_signal() -> None:
    """Any one of a base, a decorator, or a class keyword exempts an otherwise-bare class -- the rule
    only fires when all three are absent.
    """
    assert complaints("pointless_class", "class C(Base):\n    x = 1\n") == []  # base only
    assert complaints("pointless_class", "@deco\nclass C:\n    x = 1\n") == []  # decorator only
    assert complaints("pointless_class", "class C(metaclass=M):\n    x = 1\n") == []  # keyword only


def test_pointless_class_with_two_methods_not_flagged() -> None:
    """A bare class earns its keep once it has more than one method (real behavior)."""
    source = "class C:\n    def a(self):\n        return 1\n    def b(self):\n        return 2\n"
    assert complaints("pointless_class", source) == []


def test_bare_class_with_zero_methods_flagged() -> None:
    """A bare class with only data and no methods is still pointless (use a function or Pydantic)."""
    assert len(complaints("pointless_class", "class C:\n    x = 1\n")) == 1


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


def test_continue_deeply_nested_in_loops_flagged() -> None:
    """A continue three for-loops deep trips the nested rule: its parent and grandparent are both For."""
    source = "for i in x:\n    for j in y:\n        for k in z:\n            continue\n"
    assert "Overly-nested" in preferences_violations("m.py", source)


def test_while_continue_reports_the_while_message_not_the_nested_one() -> None:
    """When a continue sits in an if inside a while, the while-loop ban is reported (that branch runs
    first and returns), not the nested-if message.
    """
    violations = preferences_violations("m.py", "while cond:\n    if a:\n        continue\n")
    assert "while loop banned" in violations
    assert "Overly-nested" not in violations


def test_lazy_assert_flagged() -> None:
    """An assert on a constant or literal container tests nothing and is flagged."""
    assert complaints("lazy_assert", "assert True\n")  # constant
    assert complaints("lazy_assert", "assert []\n")  # literal container
    assert complaints("lazy_assert", "assert real_condition\n") == []  # a real check passes


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
