from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from sql_auth_strict.cases import case


ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.sql_auth_strict,
    pytest.mark.integration,
    pytest.mark.framework,
]
FRAMEWORK_DISTRIBUTIONS = (
    "fastapi",
    "flask",
    "httpx",
    "asgiref",
    "asgi-lifespan",
)


@case("FRAME-001")
def test_framework_dependencies_are_development_only_and_locked(
    record_framework_metric,
) -> None:
    pyproject = tomllib.loads(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    runtime = tuple(pyproject["project"].get("dependencies", ()))
    development = tuple(pyproject["dependency-groups"]["dev"])
    assert runtime == ()
    for expected in FRAMEWORK_DISTRIBUTIONS:
        assert any(
            item.lower().replace("_", "-").startswith(expected)
            for item in development
        )
    versions = {
        name: importlib.metadata.version(name)
        for name in FRAMEWORK_DISTRIBUTIONS
    }
    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert all(f'name = "{name}"' in lock_text for name in versions)
    record_framework_metric("FRAME-001", versions=versions)


@case("FRAME-002")
def test_importing_fastmssql_does_not_import_frameworks() -> None:
    probe = """
import json
import sys
import fastmssql

blocked = ("fastapi", "flask", "httpx", "asgiref", "asgi_lifespan")
print(json.dumps(sorted(name for name in blocked if name in sys.modules)))
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "python")
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == []
