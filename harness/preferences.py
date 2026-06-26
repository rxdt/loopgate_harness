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
    """Catch continue statements inside nested blocks.

    Args:
        node: The AST node under inspection.

    Returns:
        A complaint if the node is a banned/nested `continue`, else None.
    """
    # Ban continue inside while loops to prevent infinite freezes
    if isinstance(node, ast.While):
        for child in ast.walk(node):
            if isinstance(child, ast.Continue):
                return "'continue' inside a while loop banned to prevent infinite freezes"
    # Stop 'continue' hiding inside nested if-statements
    if isinstance(node, ast.Continue):
        parent = getattr(node, "parent", None)
        if isinstance(parent, (ast.If, ast.For)):
            grandparent = getattr(parent, "parent", None)
            great_grand = getattr(grandparent, "parent", None)
            # if the grandparent or great-grand is also an if/for node, we are nested
            if isinstance(grandparent, (ast.If, ast.For)) or isinstance(great_grand, (ast.If, ast.For)):
                return "Overly-nested 'continue' detected inside multiple if-statements"
    return None


def lazy_any_type_hints(node: ast.AST) -> str | None:
    """Catch agents using 'Any' to escape strict type checks.

    Args:
        node: The AST node under inspection.

    Returns:
        A complaint if the arg is annotated `Any`/`typing.Any`, else None.
    """
    if isinstance(node, ast.arg):
        item_is_any = (
            node.annotation and isinstance(node.annotation, ast.Name) and node.annotation.id == "Any"
        )  # matches item Any
        uses_typing_dot_any = (
            node.annotation
            and isinstance(node.annotation, ast.Attribute)
            and isinstance(node.annotation.value, ast.Name)
            and node.annotation.value.id == "typing"
            and node.annotation.attr == "Any"
        )  # matches item typing.Any
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


def star_unpacking(node: ast.AST) -> str | None:
    """A *seq splat, or a **mapping splat (a keyword with no argument name).

    Args:
        node: The AST node under inspection.

    Returns:
        A complaint if the node is a star/double-star unpack, else None.
    """
    if isinstance(node, ast.Starred) or (isinstance(node, ast.keyword) and node.arg is None):
        return "Star unpacking pass explicit values"
    return None


def pointless_class(node: ast.AST) -> str | None:
    """A plain class with no base/decorator/keyword and at most one method. Beyond too-few-public-methods
    R0903 because leaves classes with parents and only attacks bare classes.

    Args:
        node: The AST node under inspection.

    Returns:
        A complaint if the node is such a pointless class, else None.
    """
    if not isinstance(node, ast.ClassDef) or node.bases or node.keywords or node.decorator_list:
        return None
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
    "function_argument_assignment_has_star": function_argument_assignment_underscore_lead,
    "star": star_unpacking,
    "class": pointless_class,
    "lasy_assert": lazy_assert,
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
    violations = []
    tree = ast.parse(source)
    for parent in ast.walk(tree):  # link each node to its parent so checks can inspect nesting
        for child in ast.iter_child_nodes(parent):
            child.__dict__["parent"] = parent  # ast nodes carry no 'parent'; add it for nesting checks
    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", "?")
        for check in CHECKS.values():
            message = check(node)
            if message:
                violations.append(f"{path}:{lineno}: {message}")

    return "\n".join(violations) if violations else ""
