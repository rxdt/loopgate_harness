"""AST-based structural style checks for staged Python files.

OPTIONAL for humans to use or edit! The functions below are examples to use. Or delete.

Agents in the loop cannot edit this file. It's in `FORBIDDEN_FILES` at `harness/gate.py`.

This module should reflect the repo owner's personal coding style hates. It's personal.
e.g. indiscriminate __underscore_names, **star-unpacking, pointless classes, loops instead of Set math.

Use this file ONLY for rules that ruff, pylint, and pyright cannot express but you want enforced. Keep short.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

# A check looks at ONE AST node and returns a complaint or None if the node is fine.
# It never walks the tree. root preferences_violations does the walk and feeds nodes to functions.
Check = Callable[[ast.AST], "str | None"]


def chaotic_continue_statements(node: ast.AST) -> str | None:
    """Catch continue statements that are hard to follow: inside a while loop, or buried two or more
    if/for blocks deep. A single `if` guard directly inside a loop is fine -- that is normal Python.

    Example:
        for x in xs:              -> fine: a plain continue in its loop
            continue
        for x in xs:              -> flagged: two `if` blocks deep
            if a:
                if b:
                    continue
        for x in xs:              -> flagged: nested loops
            for y in ys:
                continue
        while cond:               -> flagged: any continue in a while loop (freeze risk)
            continue

    Args:
        node: One piece of the parsed code to look at.

    Returns:
        A short message if the continue is in a while loop or over-nested, otherwise None.
    """
    # Ban continue inside while loops to prevent infinite freezes.
    if isinstance(node, ast.While) and any(isinstance(child, ast.Continue) for child in ast.walk(node)):
        return "'continue' inside a while loop banned to prevent infinite freezes"
    # Only continue statements can be over-nested; a guard here keeps the walk below un-nested.
    if not isinstance(node, ast.Continue):
        return None
    # A continue is over-nested when it sits two or more if/for blocks deep. One 'if' guard directly
    # inside its loop (the common `for ...: if ...: continue`) is fine; anything deeper is not.
    blocks: list[str] = []
    ancestor = getattr(node, "parent", None)
    while ancestor is not None:
        if isinstance(ancestor, ast.If | ast.For | ast.While):
            blocks.append(type(ancestor).__name__)
        ancestor = getattr(ancestor, "parent", None)
    # Every continue needs one enclosing loop; a single 'if' above that loop is still fine.
    if len(blocks) >= 2 and blocks not in (["If", "For"], ["If", "While"]):
        return "Overly-nested 'continue' detected inside multiple if/for blocks"
    return None


def lazy_any_type_hints(node: ast.AST) -> str | None:
    """Catch agents using 'Any' to escape strict type checks.

    Args:
        node: The AST node under inspection.

    Returns:
        A complaint if the arg is annotated `Any`/`typing.Any`, else None.
    """
    if isinstance(node, ast.arg) and node.annotation:
        item_is_any = isinstance(node.annotation, ast.Name) and node.annotation.id == "Any"  # matches Any
        uses_typing_dot_any = (
            isinstance(node.annotation, ast.Attribute)
            and isinstance(node.annotation.value, ast.Name)
            and node.annotation.value.id == "typing"
            and node.annotation.attr == "Any"
        )  # matches typing.Any
        if item_is_any or uses_typing_dot_any:
            return f"Lazy 'Any' type hint detected for argument '{node.arg}'"
    return None


def lambda_found(node: ast.AST) -> str | None:
    """Catches all lambdas. Ruff E731 only flags lambdas directly assigned to a variable name.

    Args:
        node: The AST node under inspection.

    Returns:
        A complaint if the node is a lambda, else None.
    """
    if isinstance(node, ast.Lambda):
        return "Lambda found hurting readability and adding complexity."
    return None


def function_argument_assignment_underscore_lead(node: ast.AST) -> str | None:
    """A def, arg, or assignment target.

    Args:
        node: The AST node under inspection.

    Returns:
        A complaint if the name leads with a lone underscore, else None.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        name = node.name
    elif isinstance(node, ast.arg):
        name = node.arg
    elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        name = node.id
    else:
        return None

    if name.startswith("_") and not name.endswith("__"):
        # Check if def, arg, assigment target name starts with a lone underscore
        return f"Name '{name}' starts with underscore"
    return None


def hidden_signature_star_args(node: ast.AST) -> str | None:
    """Spell out a function's arguments instead of using *args or **kwargs.

    When the arguments are named, anyone reading the function knows what to pass, and their editor can
    suggest the arguments for them. We flag this everywhere, even for wrappers and decorators, because
    the code alone can't tell us whether *args is a real need or just a shortcut.

    Example:
        def send(*args, **kwargs): ...  -> flagged: the caller can't see what to pass
        def send(to, subject): ...      -> fine: the arguments are spelled out

    Args:
        node: One piece of the parsed code to look at.

    Returns:
        A short message if the function uses *args or **kwargs, otherwise None.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (node.args.vararg or node.args.kwarg):
        return "'*args'/'**kwargs' hide the function signature; use explicit parameters"
    return None


def dynamic_star_call(node: ast.AST) -> str | None:
    """Do not spread a variable in a call's arguments with *, e.g. f(*items).

    When you write f(*items), you can't tell how many arguments f is really getting, and the call breaks
    if the list is the wrong length. Spreading a list or tuple written out right there, like f(*[1, 2, 3]),
    is fine because its length is plain to see. A variable, or a list that spreads something else inside
    it like f(*[1, *items]), is not.

    We only look at '*', not '**'. Keyword unpacking like f(**opts) is a normal, readable Python idiom
    (passing config through, or super().__init__(**kwargs)), and the one wasteful case, f(**{"a": 1}),
    is already caught by ruff's PIE804. Positional '*' is the riskier one: the wrong length is a crash.

    Example:
        f(*items)        -> flagged: 'items' could be any length
        f(*[1, *items])  -> flagged: the list grows with 'items'
        f(*[1, 2, 3])    -> fine: exactly three arguments always
        f(**kwargs)      -> left alone on purpose: keyword unpacking is a normal, readable pattern

    Args:
        node: One piece of the parsed code to look at.

    Returns:
        A short message if a * argument is not a plain, fixed-length list or tuple, otherwise None.
    """
    if isinstance(node, ast.Call):
        for arg in node.args:
            # A '*' spread is fine only on a written-out list/tuple whose length you can see.
            # A variable, or a literal that spreads something inside (like [1, *more]), hides it.
            if isinstance(arg, ast.Starred) and not (
                isinstance(arg.value, ast.List | ast.Tuple)
                and not any(isinstance(element, ast.Starred) for element in arg.value.elts)
            ):
                return "Dynamic '*' call hides positional arguments; pass explicit arguments"
    return None


def pointless_class(node: ast.AST) -> str | None:
    """A plain class with no base/decorator/keyword and at most one method. Beyond too-few-public-methods
    R0903 because leaves classes with parents and only attacks bare classes.

    Args:
        node: The AST node under inspection.

    Returns:
        A complaint if the node is such a pointless class, else None.
    """
    if isinstance(node, ast.ClassDef) and not (node.bases or node.keywords or node.decorator_list):
        methods = [item for item in node.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)]
        if len(methods) <= 1:
            return f"'{node.name}': no base, decorator, or behavior: use function or Pydantic"
    return None


def lazy_assert(node: ast.AST) -> str | None:
    """No empty checks or lazy conditions but test nothing.
    ast.Constant catches True, False, None, 1, 0, 'pass'
    The others catch literal list/dict/tuple structures like [] or {}

    Args:
        node: The AST node under inspection.

    Returns:
        A complaint if the node is a lazy constant/literal assert, else None.
    """
    if isinstance(node, ast.Assert) and isinstance(node.test, (ast.Constant, ast.List, ast.Dict, ast.Tuple)):
        return "Lazy test assertion detected"
    return None


def objects_injected_into_runtime_memory(node: ast.AST) -> str | None:
    """Check ast.Call nodes to find name calls that manipulate global state.
    Python keeps internal memory dictionary of each current variable/function. Do not allow calling globals()
    or locals() to grab/inject variables to runtime (instead of writing e.g. a dict).

    Args:
        node: The AST node under inspection.

    Returns:
        A complaint if the node calls globals()/locals(), else None.
    """
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"globals", "locals"}
    ):
        return "Dynamic injection of memory registry spotted"
    return None


def complex_comprehension(node: ast.AST) -> str | None:
    """Use Type Set math or loops when comprehensions become complex.

    Args:
        node: The AST node under inspection.

    Returns:
        A complaint if a multi-generator comprehension also filters, else None.
    """
    if (
        isinstance(node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp)
        and len(node.generators) > 1
    ):
        for generator in node.generators:
            if generator.ifs:
                return "Overly complex comprehension, use a loop or type Set math"
    return None


# To add a style rule: write a dumb one-node function above and register it here under its kind.
CHECKS: dict[str, Check] = {
    "function_argument_assignment_underscore_lead": function_argument_assignment_underscore_lead,
    "hidden_signature_star_args": hidden_signature_star_args,
    "dynamic_star_call": dynamic_star_call,
    "pointless_class": pointless_class,
    "lazy_assert": lazy_assert,
    "objects_injected_into_runtime_memory": objects_injected_into_runtime_memory,
    "lambda_found": lambda_found,
    "lazy_any_type_hints": lazy_any_type_hints,
    "chaotic_continue_statements": chaotic_continue_statements,
    "complex_comprehension": complex_comprehension,
}


def preferences_violations(path: str, source: str) -> str:
    """Run every registered check on one Python file in a single AST walk.

    Args:
        path: File path used to prefix each violation message.
        source: Python source text to parse and walk.

    Returns:
        Newline-joined violation messages, or "" when the file is clean.
    """
    violations: list[str] = []
    tree = ast.parse(source)
    for parent in ast.walk(tree):  # link each node to its parent so checks can inspect nesting
        for child in ast.iter_child_nodes(parent):
            child.__dict__["parent"] = parent  # nodes are parent->child, add parent<-child for nested checks
    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", "?")
        for check in CHECKS.values():
            message = check(node)
            if message:
                violations.append(f"{path}:{lineno}: {message}")

    return "\n".join(violations) if violations else ""
