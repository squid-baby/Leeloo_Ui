"""Python 3.9 compatibility guard.

Raspberry Pi OS Bullseye ships Python 3.9. PEP 604 unions (``str | None``)
in annotations raise TypeError at import time there, unless the module
uses ``from __future__ import annotations``.
"""
import ast
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "odds-and-ends", "archive", ".claude", "tests", "leeloo_server", "__pycache__"}


def _device_python_files():
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def _has_future_annotations(tree):
    return any(
        isinstance(node, ast.ImportFrom) and node.module == "__future__"
        and any(a.name == "annotations" for a in node.names)
        for node in tree.body
    )


def _annotations(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for a in args.args + args.kwonlyargs + args.posonlyargs:
                if a.annotation is not None:
                    yield a.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


@pytest.mark.parametrize("path", sorted(_device_python_files()))
def test_no_runtime_pep604_unions(path):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    if _has_future_annotations(tree):
        return
    for ann in _annotations(tree):
        for node in ast.walk(ann):
            assert not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)), (
                f"{os.path.relpath(path, REPO_ROOT)}:{ann.lineno} uses 'X | Y' in an "
                "annotation, which fails on Python 3.9"
            )
