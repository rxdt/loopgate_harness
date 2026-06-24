"""AST-based structural style checks for staged Python files.

OPTIONAL for humans to use or edit! The functions below are examples to use. Or delete.

Agents in the loop cannot edit this file. It's in `FORBIDDEN_FILES` at `harness/gate.py`.

This module should reflect the repo owner's personal coding style hates. It's personal.
e.g. indiscriminate __underscore_names, **star-unpacking, pointless classes, loops instead of Set math.

Use this file ONLY for rules that ruff, pylint, and pyright cannot express but you want enforced. Keep short.
"""

from __future__ import annotations

import ast


def underscore_violations(path: str, tree: ast.Module) -> list[str]:
    """No function or argument starts with an underscore."""
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found = (node.name, node.lineno)
        elif isinstance(node, ast.arg):
            found = (node.arg, node.lineno)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            found = (node.id, node.lineno)
        else:
            continue
        if found[0].startswith("_") and not found[0].endswith("__"):
            problems.append(f"{path}:{found[1]}: name '{found[0]}' starts with underscore")
    return problems


def star_violations(path: str, tree: ast.Module) -> list[str]:
    """Double-star unpacking in function signnatures or assignments."""
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Starred):
            problems.append(f"{path}:{node.lineno}: star unpacking; pass explicit values")
        elif isinstance(node, ast.keyword) and node.arg is None:
            problems.append(f"{path}:{node.lineno}: double-star unpacking; pass explicit arguments")
    return problems


def class_violations_must_be_pydantic(path: str, tree: ast.Module) -> list[str]:
    """Flag plain classes that should be functions or a Pydantic class."""
    problems: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.bases or node.keywords or node.decorator_list:
            continue
        methods = [item for item in node.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)]
        if len(methods) <= 1:
            problems.append(
                f"{path}:{node.lineno} '{node.name}': no base,decorator,or behavior. Use function or Pydantic"
            )
    return problems


def preferences_violations(path: str, source: str) -> list[str]:
    """Run every structural check on one Python file"""
    tree = ast.parse(source)
    problems = underscore_violations(path, tree)
    problems.extend(star_violations(path, tree))
    problems.extend(class_violations_must_be_pydantic(path, tree))
    return problems
