from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from fastmssql import Connection
from sql_auth_strict.cases import case
from sql_auth_strict.framework_apps import (
    FrameworkState,
    session_count,
    wait_for_pool_active,
)
from sql_auth_strict.helpers import scalar


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


def test_framework_state_uses_explicit_unique_application_name(
    sql_auth_config,
    unique_sql_name,
) -> None:
    application_name = unique_sql_name("strict_frame_state")
    state = FrameworkState.create(
        sql_auth_config,
        application_name=application_name,
    )
    assert isinstance(state.connection, Connection)
    assert state.application_name == application_name
    assert state.loops == []


@pytest.mark.asyncio
async def test_framework_session_helpers_observe_real_pool(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
) -> None:
    application_name = unique_sql_name("strict_frame_session")
    state = FrameworkState.create(
        sql_auth_config,
        application_name=application_name,
    )
    try:
        await state.connection.connect()
        assert await scalar(state.connection, "SELECT 1") == 1
        assert await session_count(sa_connection, application_name) >= 1
        assert (
            await wait_for_pool_active(state.connection, expected=0)
        )["active_connections"] == 0
    finally:
        await state.connection.disconnect()
