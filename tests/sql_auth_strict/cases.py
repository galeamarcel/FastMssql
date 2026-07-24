from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
import re

import pytest


CASE_PATTERN = re.compile(r"`([A-Z]+-\d{3})`")
CASE_MARKER = "sql_auth_case"


def case(*case_ids: str) -> pytest.MarkDecorator:
    invalid = not case_ids or any(
        not re.fullmatch(r"[A-Z]+-\d{3}", item) for item in case_ids
    )
    if invalid:
        raise ValueError(f"invalid SQL-auth case IDs: {case_ids!r}")
    return getattr(pytest.mark, CASE_MARKER)(*case_ids)


def spec_case_ids(path: Path) -> frozenset[str]:
    return frozenset(CASE_PATTERN.findall(path.read_text(encoding="utf-8")))


def source_case_ids(paths: Iterable[Path]) -> frozenset[str]:
    return frozenset(source_case_occurrences(paths))


def source_case_occurrences(paths: Iterable[Path]) -> Counter[str]:
    found: Counter[str] = Counter()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "case":
                continue
            for argument in node.args:
                if (
                    isinstance(argument, ast.Constant)
                    and isinstance(argument.value, str)
                ):
                    found[argument.value] += 1
    return found
