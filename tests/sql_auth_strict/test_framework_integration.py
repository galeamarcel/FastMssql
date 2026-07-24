from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib

from asgi_lifespan import LifespanManager
from flask import Flask
import pytest
import pytest_asyncio

from fastmssql import Connection, SqlError
from httpx import ASGITransport, AsyncClient
from sql_auth_strict.cases import case
from sql_auth_strict.framework_apps import (
    FrameworkState,
    IntentionalRollback,
    create_adapted_flask_app,
    create_fastapi_app,
    create_flask_app,
    session_count,
    wait_for_pool_active,
    wait_for_sql_request,
    wait_for_session_count,
)
from sql_auth_strict.helpers import event_loop_ticks, quote_identifier, scalar


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


def asgi_client(app, *, raise_app_exceptions: bool = True) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(
            app=app,
            raise_app_exceptions=raise_app_exceptions,
        ),
        base_url="http://framework.test",
    )


async def flask_request(app: Flask, path: str):
    def request_once():
        with app.test_client() as client:
            return client.get(path)

    return await asyncio.to_thread(request_once)


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


@pytest_asyncio.fixture
async def framework_table(
    owner_connection,
    unique_sql_name,
    cleanup_registry,
):
    table = quote_identifier(unique_sql_name("strict_framework_items"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value NVARCHAR(100) NULL)"
    )
    return table


@pytest_asyncio.fixture
async def framework_fastapi_app(
    sql_auth_config,
    unique_sql_name,
    framework_table,
):
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_fastapi_app"),
    )
    return create_fastapi_app(state, framework_table), state


@case("FRAME-005")
@pytest.mark.asyncio
async def test_fastapi_lifespan_connects_and_disconnects_shared_pool(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
) -> None:
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_fastapi_lifecycle"),
    )
    app = create_fastapi_app(state, "[unused_framework_table]")
    assert await state.connection.is_connected() is False
    async with LifespanManager(app):
        assert await state.connection.is_connected() is True
        assert await scalar(state.connection, "SELECT 1") == 1
        assert await session_count(sa_connection, state.application_name) >= 1
    assert await state.connection.is_connected() is False
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )


@case("FRAME-006")
@pytest.mark.asyncio
async def test_fastapi_parameterized_read_write_routes(
    sql_auth_config,
    owner_connection,
    unique_sql_name,
    cleanup_registry,
) -> None:
    raw_table = unique_sql_name("strict_fastapi_items")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value NVARCHAR(100))"
    )
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_fastapi_data"),
    )
    app = create_fastapi_app(state, table)
    async with LifespanManager(app), asgi_client(app) as client:
        written = await client.post(
            "/items/7",
            json={"value": "Română 🧪"},
        )
        read = await client.get("/items/7")
    assert written.status_code == 201
    assert written.json() == {"affected": 1}
    assert read.status_code == 200
    assert read.json() == {"id": 7, "value": "Română 🧪"}
    assert await scalar(
        owner_connection,
        f"SELECT value FROM {table} WHERE id = 7",
    ) == "Română 🧪"


@case("FRAME-007")
@pytest.mark.asyncio
async def test_fastapi_request_transaction_commits(
    framework_fastapi_app,
    owner_connection,
    framework_table,
) -> None:
    app, _state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        response = await client.post("/transaction/17")
    assert response.status_code == 201
    assert await scalar(
        owner_connection,
        f"SELECT COUNT(*) FROM {framework_table} WHERE id = 17",
    ) == 1


@case("FRAME-008")
@pytest.mark.asyncio
async def test_fastapi_request_transaction_rolls_back(
    framework_fastapi_app,
    owner_connection,
    framework_table,
) -> None:
    app, _state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        with pytest.raises(IntentionalRollback):
            await client.post("/transaction/18?fail=true")
    assert await scalar(
        owner_connection,
        f"SELECT COUNT(*) FROM {framework_table} WHERE id = 18",
    ) == 0


@case("FRAME-012")
@pytest.mark.asyncio
async def test_fastapi_sql_error_preserves_type_and_code(
    framework_fastapi_app,
) -> None:
    app, state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        with pytest.raises(SqlError) as captured:
            await client.get("/sql-error")
        assert captured.value.code == 208
        assert await scalar(state.connection, "SELECT 12") == 12


@case("FRAME-013")
@pytest.mark.asyncio
async def test_fastapi_500_response_and_logs_redact_credentials(
    sql_auth_config,
    unique_sql_name,
    caplog,
) -> None:
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_fastapi_safe_error"),
    )
    app = create_fastapi_app(
        state,
        "[unused_framework_table]",
        safe_errors=True,
    )
    async with LifespanManager(app), asgi_client(
        app,
        raise_app_exceptions=False,
    ) as client:
        response = await client.get("/sql-error")
    combined = response.text + "\n" + caplog.text
    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    for secret in (
        sql_auth_config.owner_password,
        sql_auth_config.sa_password,
        sql_auth_config.readonly_password,
        sql_auth_config.denied_password,
    ):
        assert secret not in combined


@case("FRAME-009")
@pytest.mark.asyncio
async def test_fastapi_concurrent_requests_beat_sequential_baseline(
    framework_fastapi_app,
    record_framework_metric,
) -> None:
    app, _state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        sequential_started = time.monotonic()
        sequential = [
            (await client.get(f"/wait/{value}?profile=short")).json()["value"]
            for value in range(4)
        ]
        sequential_elapsed = time.monotonic() - sequential_started

        concurrent_started = time.monotonic()
        responses = await asyncio.gather(
            *(client.get(f"/wait/{value}?profile=short") for value in range(4))
        )
        concurrent_elapsed = time.monotonic() - concurrent_started
    assert sequential == list(range(4))
    assert [response.json()["value"] for response in responses] == list(
        range(4)
    )
    assert concurrent_elapsed < sequential_elapsed * 0.65
    record_framework_metric(
        "FRAME-009",
        sequential_seconds=sequential_elapsed,
        concurrent_seconds=concurrent_elapsed,
        ratio=concurrent_elapsed / sequential_elapsed,
    )


@case("FRAME-010")
@pytest.mark.asyncio
async def test_fastapi_event_loop_ticks_during_sql_wait(
    framework_fastapi_app,
    record_framework_metric,
) -> None:
    app, _state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        stop = asyncio.Event()
        ticker = asyncio.create_task(event_loop_ticks(stop, interval=0.02))
        try:
            response = await client.get("/wait/10?profile=short")
            assert response.json() == {"value": 10}
        finally:
            stop.set()
            ticks = await ticker
    assert len(ticks) >= 10
    record_framework_metric("FRAME-010", ticker_count=len(ticks))


@case("FRAME-011")
@pytest.mark.asyncio
async def test_fastapi_request_cancellation_recovers_immediately(
    framework_fastapi_app,
    sa_connection,
    record_framework_metric,
) -> None:
    app, state = framework_fastapi_app
    async with LifespanManager(app), asgi_client(app) as client:
        request = asyncio.create_task(client.get("/wait/11?profile=long"))
        await wait_for_pool_active(state.connection, expected=1)
        await wait_for_sql_request(
            sa_connection,
            state.application_name,
            present=True,
            timeout=1.0,
        )
        started = time.monotonic()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(request, timeout=0.5)
        cancellation_elapsed = time.monotonic() - started
        await wait_for_pool_active(
            state.connection,
            expected=0,
            timeout=1.0,
        )
        await wait_for_sql_request(
            sa_connection,
            state.application_name,
            present=False,
            timeout=1.0,
        )
        recovered = await asyncio.wait_for(
            client.get("/wait/111?profile=none"),
            timeout=2.0,
        )
        pool_after = await state.connection.pool_stats()
    assert recovered.json() == {"value": 111}
    record_framework_metric(
        "FRAME-011",
        cancellation_seconds=cancellation_elapsed,
        pool_after=pool_after,
    )


@pytest.fixture
def framework_flask_app(sql_auth_config, unique_sql_name):
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_flask_wsgi"),
    )
    return create_flask_app(state), state


@case("FRAME-014")
@pytest.mark.asyncio
async def test_flask_async_wsgi_view_executes_real_query(
    framework_flask_app,
) -> None:
    app, state = framework_flask_app
    try:
        response = await flask_request(app, "/value/14")
        assert response.status_code == 200
        assert response.get_json() == {"value": 14}
    finally:
        await state.connection.disconnect()


@case("FRAME-015")
@pytest.mark.asyncio
async def test_flask_shared_pool_crosses_distinct_request_loops(
    framework_flask_app,
    record_framework_metric,
) -> None:
    app, state = framework_flask_app
    try:
        first = await flask_request(app, "/loop")
        second = await flask_request(app, "/loop")
        assert first.status_code == second.status_code == 200
        assert len(state.loops) == 2
        assert state.loops[0] is not state.loops[1]
        assert first.get_json()["sql_value"] == 15
        assert second.get_json()["sql_value"] == 15
        assert await scalar(state.connection, "SELECT 15") == 15
        record_framework_metric(
            "FRAME-015",
            distinct_request_loops=True,
            loop_ids=[id(loop) for loop in state.loops],
        )
    finally:
        await state.connection.disconnect()


@case("FRAME-016")
@pytest.mark.asyncio
async def test_flask_one_async_view_overlaps_database_operations(
    framework_flask_app,
    record_framework_metric,
) -> None:
    app, state = framework_flask_app
    try:
        response = await flask_request(app, "/gather")
        payload = response.get_json()
        assert payload["sequential"] == [0, 1, 2, 3]
        assert payload["concurrent"] == [0, 1, 2, 3]
        assert payload["concurrent_seconds"] < (
            payload["sequential_seconds"] * 0.65
        )
        record_framework_metric("FRAME-016", **payload)
    finally:
        await state.connection.disconnect()


@case("FRAME-017")
@pytest.mark.asyncio
async def test_flask_wsgi_worker_requests_are_correct_and_measured(
    framework_flask_app,
    record_framework_metric,
) -> None:
    app, state = framework_flask_app
    try:
        started = time.monotonic()
        responses = await asyncio.gather(
            *(flask_request(app, f"/wait/{value}") for value in range(4))
        )
        elapsed = time.monotonic() - started
        assert [response.get_json()["value"] for response in responses] == list(
            range(4)
        )
        record_framework_metric(
            "FRAME-017",
            execution_model="WSGI worker-bound",
            requests=4,
            elapsed_seconds=elapsed,
        )
    finally:
        await state.connection.disconnect()


@case("FRAME-018")
@pytest.mark.asyncio
async def test_flask_wsgi_error_is_typed_redacted_and_pool_recovers(
    framework_flask_app,
    sql_auth_config,
) -> None:
    app, state = framework_flask_app
    app.testing = True
    try:
        with pytest.raises(SqlError) as captured:
            await flask_request(app, "/sql-error")
        assert captured.value.code == 208
        assert sql_auth_config.owner_password not in str(captured.value)
        assert await scalar(state.connection, "SELECT 18") == 18
    finally:
        await state.connection.disconnect()


@case("FRAME-019")
@pytest.mark.asyncio
async def test_flask_wsgi_explicit_shutdown_removes_app_sessions(
    framework_flask_app,
    sa_connection,
) -> None:
    app, state = framework_flask_app
    try:
        response = await flask_request(app, "/value/19")
        assert response.get_json() == {"value": 19}
        assert await session_count(sa_connection, state.application_name) >= 1
    finally:
        await state.connection.disconnect()
    assert await state.connection.is_connected() is False
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )


@pytest.fixture
def framework_adapted_flask_app(sql_auth_config, unique_sql_name):
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_flask_asgi"),
    )
    return create_adapted_flask_app(state), state


@case("FRAME-020")
@pytest.mark.asyncio
async def test_adapted_flask_executes_real_sql_auth_query(
    framework_adapted_flask_app,
) -> None:
    app, state = framework_adapted_flask_app
    await state.connection.connect()
    try:
        async with asgi_client(app) as client:
            response = await client.get("/value/20")
        assert response.status_code == 200
        assert response.json() == {"value": 20}
    finally:
        await state.connection.disconnect()


@case("FRAME-021")
@pytest.mark.asyncio
async def test_adapted_flask_reuses_persistent_asgi_loop(
    framework_adapted_flask_app,
    record_framework_metric,
) -> None:
    app, state = framework_adapted_flask_app
    await state.connection.connect()
    try:
        async with asgi_client(app) as client:
            first = await client.get("/loop")
            second = await client.get("/loop")
        assert first.status_code == second.status_code == 200
        assert first.json()["sql_value"] == 15
        assert second.json()["sql_value"] == 15
        assert len(state.loops) == 2
        assert state.loops[0] is state.loops[1]
        record_framework_metric(
            "FRAME-021",
            persistent_loop=True,
            loop_id=first.json()["loop_id"],
        )
    finally:
        await state.connection.disconnect()


@case("FRAME-022")
@pytest.mark.asyncio
async def test_adapted_flask_concurrent_requests_are_correct_and_measured(
    framework_adapted_flask_app,
    record_framework_metric,
) -> None:
    app, state = framework_adapted_flask_app
    await state.connection.connect()
    try:
        async with asgi_client(app) as client:
            started = time.monotonic()
            responses = await asyncio.gather(
                *(client.get(f"/wait/{value}") for value in range(4))
            )
            elapsed = time.monotonic() - started
        assert [response.json()["value"] for response in responses] == list(
            range(4)
        )
        record_framework_metric(
            "FRAME-022",
            execution_model="persistent ASGI loop around Flask/WSGI",
            requests=4,
            elapsed_seconds=elapsed,
        )
    finally:
        await state.connection.disconnect()


@case("FRAME-023")
@pytest.mark.asyncio
async def test_adapted_flask_cancellation_has_bounded_recovery(
    framework_adapted_flask_app,
    sa_connection,
    record_framework_metric,
) -> None:
    app, state = framework_adapted_flask_app
    await state.connection.connect()
    try:
        async with asgi_client(app) as client:
            request = asyncio.create_task(client.get("/wait/23"))
            await wait_for_pool_active(state.connection, expected=1)
            await wait_for_sql_request(
                sa_connection,
                state.application_name,
                present=True,
                timeout=1.0,
            )
            started = time.monotonic()
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(request, timeout=0.5)
            cancellation_elapsed = time.monotonic() - started
            await wait_for_pool_active(
                state.connection,
                expected=0,
                timeout=3.5,
            )
            await wait_for_sql_request(
                sa_connection,
                state.application_name,
                present=False,
                timeout=3.5,
            )
            recovered = await client.get("/value/223")
            pool_after = await state.connection.pool_stats()
        assert recovered.json() == {"value": 223}
        record_framework_metric(
            "FRAME-023",
            cancellation_seconds=cancellation_elapsed,
            recovery_bound_seconds=3.5,
            pool_after=pool_after,
        )
    finally:
        await state.connection.disconnect()


@case("FRAME-024")
@pytest.mark.asyncio
async def test_adapted_flask_shutdown_removes_app_sessions(
    framework_adapted_flask_app,
    sa_connection,
) -> None:
    app, state = framework_adapted_flask_app
    await state.connection.connect()
    try:
        async with asgi_client(app) as client:
            assert (await client.get("/value/24")).json() == {"value": 24}
        assert await session_count(sa_connection, state.application_name) >= 1
    finally:
        await state.connection.disconnect()
    assert await state.connection.is_connected() is False
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )
