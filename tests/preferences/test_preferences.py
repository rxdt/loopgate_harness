"""Tests for AST-based structural style checks (preferences.preferences).

The preferences API is a registry of single-node `Check` functions (each takes one `ast.AST`
node and returns a complaint string or None), plus `preferences_violations`, which walks a file
once and returns a dict grouping every complaint by check kind. These tests exercise that API
with real code that trips — and real code that must not trip — each check.
"""

from __future__ import annotations

import ast
import inspect
import keyword
import string
from unittest.mock import Mock

import pytest
from hypothesis import assume, given, strategies

preferences = pytest.importorskip("preferences.preferences")
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
    `named_with_underscore_and_not_in_class_or_dunder` from the registry would silently stop enforcing it.
    Helpers like `preferences_violations` (two args) and mutmut's generated clones are excluded.
    """
    registered = set(CHECKS.values())
    unregistered = [
        name
        for name, fn in inspect.getmembers(preferences, inspect.isfunction)
        if fn.__module__ == preferences.__name__
        and "mutmut" not in name  # mutmut clones every check; only the trampoline keeps the name
        and list(inspect.signature(fn).parameters) == ["node"]
        and fn.__annotations__.get("return") == "str | None"
        and fn not in registered
    ]
    assert unregistered == [], f"check-shaped functions defined but not registered in CHECKS: {unregistered}"


# --------------------------------------------------------------------------- single-node checks


def test_underscore_names_flagged() -> None:
    """Private-style underscore names are flagged while the exact discard name is exempt."""
    flagged_source = "def _hidden(_arg):\n    _value = 1\n    return _value\n"
    found = complaints("named_with_underscore_and_not_in_class_or_dunder", flagged_source)
    assert len(found) == 3
    assert any("'_hidden'" in message for message in found)
    assert any("'_arg'" in message for message in found)
    assert any("'_value'" in message for message in found)
    allowed_source = "_ = 1\nvalue, _ = pair\nfor _ in values:\n    pass\n\ndef consume(_):\n    return None\n"
    assert complaints("named_with_underscore_and_not_in_class_or_dunder", allowed_source) == []
    assert not preferences_violations("harness/cli.py", "env_bin, _ = infer_env_manager()\n")
    class_source = "class Box:\n    def _private(self):\n        _value = 1\n        return _value\n"
    assert "starts with underscore" not in preferences_violations("m.py", class_source)


def test_dunder_names_exempt() -> None:
    """Dunder names like __all__ and __init__ are not flagged."""
    source = "__all__ = []\n\n\nclass Box(dict):\n    def __init__(self):\n        super().__init__()\n"
    assert complaints("named_with_underscore_and_not_in_class_or_dunder", source) == []
    assert complaints("named_with_underscore_and_not_in_class_or_dunder", "__private = 1\n") == [
        "Name '__private starts with a dunder, rename it"
    ]


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


def test_bare_star_and_slash_separators_not_allowed() -> None:
    """The '*' in a def using them with named parameters is unallowed - obfuscates types."""
    message = "'*args', '**kwargs', '*', and '/' hide the function signature, use explicit parameters"
    assert complaints("hidden_signature_star_args", "def f(a, *, b):\n    return b\n") == [message]  # kw-only
    assert complaints("hidden_signature_star_args", "def f(a, b, /):\n    return a\n") == [message]  # pos-only


def test_dynamic_star_call_flagged() -> None:
    """Splatting a non-literal sequence (a name, or a literal containing a '*') into a call is flagged."""
    assert complaints("dynamic_star_call", "f(*my_list)\n") == [
        "Dynamic '*' call hides positional arguments; pass explicit arguments"
    ]
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
    assert complaints("chaotic_continue_statements", "while True:\n    if x:\n        continue\n") == [
        "'continue' inside a while loop banned to prevent infinite freezes"
    ]


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


def test_continue_in_two_nested_loops_flagged() -> None:
    """Two nested loops are the minimum prohibited continue depth."""
    source = "for outer in xs:\n    for inner in ys:\n        continue\n"
    assert preferences_violations("m.py", source) == (
        "m.py:3: Overly-nested 'continue' detected inside multiple if/for blocks"
    )


def test_while_continue_reports_the_while_message_not_the_nested_one() -> None:
    """When a continue sits in an if inside a while, the while-loop ban is reported (that branch runs
    first and returns), not the nested-if message.
    """
    violations = preferences_violations("m.py", "while cond:\n    if a:\n        continue\n")
    assert "while loop banned" in violations
    assert "Overly-nested" not in violations


def test_lazy_assert_flagged() -> None:
    """An assert on a constant or literal container tests nothing and is flagged."""
    assert complaints("lazy_assert", "assert True\n") == ["Lazy test assertion detected"]  # constant
    assert complaints("lazy_assert", "assert []\n")  # literal container
    assert complaints("lazy_assert", "assert real_condition\n") == []  # a real check passes


def test_globals_and_locals_injection_flagged() -> None:
    """Calling globals()/locals() to poke the runtime registry is flagged; a plain call is not."""
    assert complaints("objects_injected_into_runtime_memory", "globals()['x'] = 1\n")
    assert complaints("objects_injected_into_runtime_memory", "locals()\n") == [
        "Dynamic injection of memory registry spotted"
    ]
    assert complaints("objects_injected_into_runtime_memory", "sorted(items)\n") == []


def test_complex_multi_generator_comprehension_flagged() -> None:
    """A comprehension with multiple generators AND a filter is flagged; a simple one is not."""
    assert complaints("complex_comprehension", "[a for row in grid for a in row if a]\n") == [
        "Overly complex comprehension, use a loop or type Set math"
    ]
    assert complaints("complex_comprehension", "[a for a in row if a]\n") == []


# --------------------------------------------------------------------- preferences_violations (the walk)


def test_preferences_violations_returns_grouped_str() -> None:
    """The walk returns a string; a clean file produces the empty string (no groups)."""
    violations = preferences_violations("m.py", "VALUE = 1\n")
    assert isinstance(violations, str)
    assert not violations


def test_locationless_node_violation_reports_unknown_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `preferences` check reports '?' for missing lineno when its AST node has no parser-provided line.

    Args:
        monkeypatch: Sets/restores the AST parser for the test.
    """
    source = "value = lambda: None\n"
    tree = ast.parse(source)
    lambda_node = next(node for node in ast.walk(tree) if isinstance(node, ast.Lambda))
    del lambda_node.lineno
    monkeypatch.setattr(ast, "parse", Mock(return_value=tree))

    expected = "m.py:?: Lambda found hurting readability and adding complexity, prefer map() or filter()"
    assert preferences_violations("m.py", source) == expected


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
    assert violations == ("m.py:1: Lambda found hurting readability and adding complexity, prefer map() or filter()")
    assert "starts with underscore" not in violations
    assert "Star unpacking" not in violations


def test_dirty_file_lists_each_violation_on_its_own_line() -> None:
    """Two checks trip on line 1; each is rendered as one `m.py:1: <message>` line with the real line
    number, and the two are newline-joined into a single string — pinning the exact flat format.
    """
    violations = preferences_violations("m.py", "_x = lambda a: a\n")
    assert violations == (
        "m.py:1: Name '_x' starts with underscore and is not in a class\n"
        "m.py:1: Lambda found hurting readability and adding complexity, prefer map() or filter()"
    )


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


def test_lambda_in_a_test_file_is_flagged() -> None:
    """A test file gets no path-based exemption: a lambda in test_gate.py is flagged like any other file.
    Regression guard — the gate unstages harness test files, so preferences never scanned them there and
    lambdas slipped in; this proves preferences_violations itself flags them regardless of the path.
    """
    source = "def test_x() -> None:\n    fake(lambda command: 0)\n"
    violations = preferences_violations("harness/tests/test_gate.py", source)
    assert "harness/tests/test_gate.py:2: Lambda found" in violations


def test_syntax_error_raises() -> None:
    """Unparseable source raises SyntaxError; preferences does not swallow it."""
    with pytest.raises(SyntaxError):
        preferences_violations("m.py", "def broken(:\n")


# --------------------------------------------------------------------- generated behavior


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


def flags(source: str, needle: str) -> bool:
    """Return whether the preferences output contains the expected text."""
    checker = preferences_violations
    return needle in checker("m.py", source) if checker else False


@given(name=identifiers())
def test_underscore_lead_flagged_iff_leading_underscore_not_dunder(name: str) -> None:
    """Assignment targets are flagged exactly when they use a non-dunder leading underscore."""
    expected = name != "_" and name.startswith("_") and not name.startswith("__") and not name.endswith("__")
    assert flags(f"{name} = 1\n", "starts with underscore") is expected


@given(name=identifiers())
def test_underscore_rule_holds_for_function_and_argument_names(name: str) -> None:
    """The underscore rule applies equally to function and argument names."""
    assume(not name.endswith("__"))
    expected = name != "_" and name.startswith("_") and not name.startswith("__")
    assert flags(f"def {name}():\n    return 1\n", "starts with underscore") is expected
    assert flags(f"def f({name}):\n    return {name}\n", "starts with underscore") is expected


@strategies.composite
def class_source(draw: strategies.DrawFn) -> tuple[str, bool]:
    """Draw a class and whether the pointless-class rule should flag it."""
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
    """Only plain classes with at most one method are pointless."""
    source, should_flag = case
    assert flags(source, "no base, decorator, or behavior") is should_flag


@strategies.composite
def comprehension_source(draw: strategies.DrawFn) -> tuple[str, bool]:
    """Draw a comprehension and whether its generators make it too complex."""
    generator_count = draw(strategies.integers(min_value=1, max_value=3))
    if_on = draw(strategies.integers(min_value=-1, max_value=generator_count - 1))

    clauses: list[str] = []
    for index in range(generator_count):
        clause = f"for v{index} in xs{index}"
        if index == if_on:
            clause += f" if v{index}"
        clauses.append(clause)
    source = f"[v0 {' '.join(clauses)}]\n"
    return source, generator_count > 1 and if_on != -1


@given(case=comprehension_source())
def test_complex_comprehension_flagged_iff_multi_generator_with_filter(case: tuple[str, bool]) -> None:
    """Comprehensions are complex only with multiple generators and a filter."""
    source, should_flag = case
    assert flags(source, "Overly complex comprehension") is should_flag


@strategies.composite
def nested_continue_source(draw: strategies.DrawFn) -> str:
    """Draw a continue nested beneath an outer loop and two to four more blocks."""
    depth = draw(strategies.integers(min_value=2, max_value=4))
    blocks = draw(strategies.lists(strategies.sampled_from(["if cond", "for i in xs"]), min_size=depth, max_size=depth))

    lines = ["for outer in items:"]
    indent = "    "
    for block in blocks:
        lines.append(f"{indent}{block}:")
        indent += "    "
    lines.append(f"{indent}continue")
    return "\n".join(lines) + "\n"


@given(source=nested_continue_source())
def test_continue_nested_under_stacked_blocks_is_flagged(source: str) -> None:
    """Mixed nested if/for blocks always trigger the continue nesting rule."""
    assert flags(source, "Overly-nested 'continue'")


def test_single_if_guard_in_a_loop_is_not_flagged() -> None:
    """A single if guard directly inside a loop remains readable."""
    assert not flags("for i in items:\n    if skip:\n        continue\n", "Overly-nested 'continue'")


def test_shallow_continue_in_single_loop_is_not_flagged() -> None:
    """A continue directly inside one for loop is allowed."""
    assert not flags("for x in items:\n    continue\n", "Overly-nested 'continue'")


def test_continue_in_while_loop_is_flagged() -> None:
    """A continue inside a while loop is banned as a freeze risk."""
    assert flags("while cond:\n    continue\n", "while loop banned")
