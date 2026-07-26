from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib

from asgi_lifespan import LifespanManager
import fastmssql
from flask import Flask
import pytest
import pytest_asyncio

from fastmssql import Connection, PoolConfig, SqlConnectionError, SqlError
from httpx import ASGITransport, AsyncClient
from sql_auth_strict.cases import case
from sql_auth_strict.framework_apps import (
    FrameworkState,
    IntentionalRollback,
    adapted_flask_lifespan,
    create_adapted_flask_app,
    create_fastapi_app,
    create_flask_app,
    session_count,
    wait_for_pool_active,
    wait_for_lifecycle_state,
    wait_for_sql_request,
    wait_for_session_count,
)
from sql_auth_strict.helpers import event_loop_ticks, quote_identifier, scalar
from sql_auth_strict.operation_metrics_assertions import (
    OUTCOME_KEYS,
    assert_operation_stats,
    operation_delta,
    zero_outcomes,
)


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


def _timeout_load_kind(index: int) -> str:
    remainder = index % 5
    if remainder < 3:
        return "success"
    if remainder == 3:
        return "operation"
    return "acquire"


async def _sample_framework_pool(
    connection: Connection,
    stop: asyncio.Event,
) -> int:
    maximum = 0
    while not stop.is_set():
        stats = await connection.pool_stats()
        maximum = max(maximum, int(stats["connections"]))
        await asyncio.sleep(0.01)
    stats = await connection.pool_stats()
    return max(maximum, int(stats["connections"]))


async def _sample_framework_request_connection_ids(
    observer: Connection,
    application_name: str,
    stop: asyncio.Event,
) -> set[str]:
    observed: set[str] = set()
    while not stop.is_set():
        result = await observer.query(
            """
            SELECT CONVERT(NVARCHAR(36), connection.connection_id)
            FROM sys.dm_exec_requests AS request
            JOIN sys.dm_exec_sessions AS session
              ON session.session_id = request.session_id
            JOIN sys.dm_exec_connections AS connection
              ON connection.session_id = request.session_id
            CROSS APPLY sys.dm_exec_sql_text(request.sql_handle) AS sql_text
            WHERE request.session_id <> @@SPID
              AND session.program_name = @P1
              AND sql_text.text LIKE @P2
            """,
            [
                application_name,
                f"%{application_name}_timeout_route%",
            ],
        )
        observed.update(str(row[0]) for row in result.rows())
        await asyncio.sleep(0.005)
    return observed


async def _close_holders(holders: list) -> None:
    if not holders:
        return
    await asyncio.gather(*(holder.close() for holder in holders))


async def _run_timeout_wave(
    *,
    client: AsyncClient,
    state: FrameworkState,
    observer: Connection,
    indices: range,
    serialized_wsgi: bool,
) -> dict[str, object]:
    starts = {
        "success": asyncio.Event(),
        "operation": asyncio.Event(),
        "acquire": asyncio.Event(),
    }

    async def request_one(index: int) -> tuple[str, int, dict[str, object]]:
        kind = _timeout_load_kind(index)
        await starts[kind].wait()
        profile = "wait" if kind == "operation" else "immediate"
        response = await client.get(
            f"/timeout/{index}?profile={profile}"
        )
        return kind, response.status_code, response.json()

    tasks = {
        index: asyncio.create_task(request_one(index))
        for index in indices
    }
    tasks_by_kind = {
        kind: [
            task
            for index, task in tasks.items()
            if _timeout_load_kind(index) == kind
        ]
        for kind in starts
    }
    assert {kind: len(items) for kind, items in tasks_by_kind.items()} == {
        "success": 60,
        "operation": 20,
        "acquire": 20,
    }

    pool_stop = asyncio.Event()
    pool_sampler = asyncio.create_task(
        _sample_framework_pool(state.connection, pool_stop)
    )
    holders = [state.connection.transaction() for _ in range(20)]
    results: list[tuple[str, int, dict[str, object]]] = []
    request_ids: set[str] = set()
    id_stop: asyncio.Event | None = None
    id_sampler: asyncio.Task | None = None
    try:
        await asyncio.gather(*(holder.begin() for holder in holders))
        await wait_for_pool_active(state.connection, expected=20)

        starts["acquire"].set()
        acquire_results = await asyncio.gather(*tasks_by_kind["acquire"])
        results.extend(acquire_results)
        assert all(
            status == 504
            and payload["phase"] == "acquire"
            and payload["operation"] == "query"
            for _, status, payload in acquire_results
        )

        await asyncio.gather(*(holder.rollback() for holder in holders))
        await wait_for_pool_active(state.connection, expected=0)

        id_stop = asyncio.Event()
        id_sampler = asyncio.create_task(
            _sample_framework_request_connection_ids(
                observer,
                state.application_name,
                id_stop,
            )
        )
        starts["operation"].set()
        if not serialized_wsgi:
            await wait_for_pool_active(state.connection, expected=20)
        operation_results = await asyncio.gather(
            *tasks_by_kind["operation"]
        )
        id_stop.set()
        request_ids = await id_sampler
        id_sampler = None
        results.extend(operation_results)
        assert all(
            status == 504
            and payload["phase"] == "operation"
            and payload["operation"] == "query"
            for _, status, payload in operation_results
        )
        assert request_ids

        starts["success"].set()
        success_results = await asyncio.gather(*tasks_by_kind["success"])
        results.extend(success_results)
        assert all(status == 200 for _, status, _ in success_results)
        assert {
            int(payload["value"])
            for _, _, payload in success_results
        } == {
            index
            for index in indices
            if _timeout_load_kind(index) == "success"
        }
        successful_connection_ids = {
            str(payload["connection_id"])
            for _, _, payload in success_results
        }
        assert request_ids.isdisjoint(successful_connection_ids)
    finally:
        for event in starts.values():
            event.set()
        for task in tasks.values():
            if not task.done():
                task.cancel()
        for task in tasks.values():
            if not task.done() or task.cancelled():
                with pytest.raises(asyncio.CancelledError):
                    await task
        if id_stop is not None:
            id_stop.set()
        if id_sampler is not None:
            await id_sampler
        await _close_holders(holders)
        pool_stop.set()
        maximum_connections = await pool_sampler

    assert len(results) == 100
    return {
        "results": results,
        "maximum_connections": maximum_connections,
        "retired_connection_ids": request_ids,
    }


async def _run_timeout_lane(
    *,
    client: AsyncClient,
    state: FrameworkState,
    observer: Connection,
    serialized_wsgi: bool,
) -> dict[str, object]:
    stop = asyncio.Event()
    ticker = asyncio.create_task(event_loop_ticks(stop, interval=0.01))
    all_results: list[tuple[str, int, dict[str, object]]] = []
    maximum_connections = 0
    retired_connection_ids: set[str] = set()
    try:
        for wave in range(5):
            result = await _run_timeout_wave(
                client=client,
                state=state,
                observer=observer,
                indices=range(wave * 100, (wave + 1) * 100),
                serialized_wsgi=serialized_wsgi,
            )
            all_results.extend(result["results"])
            maximum_connections = max(
                maximum_connections,
                int(result["maximum_connections"]),
            )
            retired_connection_ids.update(
                result["retired_connection_ids"]
            )
        assert await scalar(state.connection, "SELECT 1010") == 1010
    finally:
        stop.set()
        ticks = await ticker

    timeout_payloads = [
        payload
        for _, status, payload in all_results
        if status == 504
    ]
    return {
        "total": len(all_results),
        "successful": sum(
            status == 200 for _, status, _ in all_results
        ),
        "acquire_timeouts": sum(
            payload.get("phase") == "acquire"
            for payload in timeout_payloads
        ),
        "operation_timeouts": sum(
            payload.get("phase") == "operation"
            for payload in timeout_payloads
        ),
        "timeout_payloads": timeout_payloads,
        "maximum_connections": maximum_connections,
        "ticks": ticks,
        "retired_connection_ids": retired_connection_ids,
    }


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


@case("FRAME-025")
@pytest.mark.asyncio
async def test_fastapi_lifespan_rejects_unreachable_sql_before_serving(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
) -> None:
    unreachable = replace(sql_auth_config, host="127.0.0.1", port=1)
    state = FrameworkState.create(
        unreachable,
        application_name=unique_sql_name("strict_fastapi_unreachable"),
        max_size=1,
        min_idle=0,
    )
    app = create_fastapi_app(state, "[unused_framework_table]")
    serving_started = False

    with pytest.raises(SqlConnectionError):
        async with LifespanManager(app):
            serving_started = True

    assert serving_started is False
    assert await state.connection.is_connected() is False
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )


@case("FRAME-026")
@pytest.mark.asyncio
async def test_adapted_flask_startup_rejects_unreachable_sql_before_serving(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
) -> None:
    unreachable = replace(sql_auth_config, host="127.0.0.1", port=1)
    state = FrameworkState.create(
        unreachable,
        application_name=unique_sql_name("strict_flask_asgi_unreachable"),
        max_size=1,
        min_idle=0,
    )
    serving_started = False

    with pytest.raises(SqlConnectionError):
        async with adapted_flask_lifespan(state):
            serving_started = True

    assert serving_started is False
    assert await state.connection.is_connected() is False
    assert await state.connection.disconnect() is False
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )


@pytest.fixture
def framework_modes(sql_auth_config, unique_sql_name):
    def fastapi_mode():
        state = FrameworkState.create(
            sql_auth_config,
            application_name=unique_sql_name("strict_mode_fastapi"),
        )
        return (
            "FastAPI/native ASGI",
            create_fastapi_app(state, "[unused_framework_table]"),
            state,
            True,
        )

    def flask_wsgi_mode():
        state = FrameworkState.create(
            sql_auth_config,
            application_name=unique_sql_name("strict_mode_flask_wsgi"),
        )
        return "Flask/WSGI", create_flask_app(state), state, False

    def flask_asgi_mode():
        state = FrameworkState.create(
            sql_auth_config,
            application_name=unique_sql_name("strict_mode_flask_asgi"),
        )
        return (
            "Flask/WsgiToAsgi",
            create_adapted_flask_app(state),
            state,
            True,
        )

    return fastapi_mode, flask_wsgi_mode, flask_asgi_mode


@case("FRAME-003")
@pytest.mark.asyncio
async def test_every_framework_mode_uses_owner_sql_auth(
    framework_modes,
    sql_auth_config,
    record_framework_metric,
) -> None:
    observed = {}
    for factory in framework_modes:
        name, app, state, is_asgi = factory()
        try:
            if name == "FastAPI/native ASGI":
                async with LifespanManager(app):
                    async with asgi_client(app) as client:
                        response = await client.get("/principal")
                        principal = response.json()["principal"]
            elif is_asgi:
                await state.connection.connect()
                async with asgi_client(app) as client:
                    response = await client.get("/principal")
                    principal = response.json()["principal"]
            else:
                await state.connection.connect()
                response = await flask_request(app, "/principal")
                principal = response.get_json()["principal"]
            assert principal == sql_auth_config.owner_user
            observed[name] = principal
        finally:
            if await state.connection.is_connected():
                await state.connection.disconnect()
    record_framework_metric("FRAME-003", principals=observed)


@case("FRAME-004")
@pytest.mark.asyncio
async def test_framework_outputs_never_disclose_credentials(
    framework_modes,
    sql_auth_config,
    caplog,
) -> None:
    visible = []
    for factory in framework_modes:
        _name, app, state, is_asgi = factory()
        await state.connection.connect()
        try:
            if is_asgi:
                async with asgi_client(
                    app,
                    raise_app_exceptions=False,
                ) as client:
                    visible.append((await client.get("/sql-error")).text)
            else:
                app.testing = False
                visible.append(
                    (await flask_request(app, "/sql-error")).get_data(
                        as_text=True
                    )
                )
        finally:
            await state.connection.disconnect()
    combined = "\n".join(visible) + "\n" + caplog.text
    for secret in (
        sql_auth_config.owner_password,
        sql_auth_config.sa_password,
        sql_auth_config.readonly_password,
        sql_auth_config.denied_password,
    ):
        assert secret not in combined


@case("TIME-010")
@pytest.mark.framework
@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_framework_operation_timeout_recovery_load(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
) -> None:
    TimeoutConfig = getattr(fastmssql, "TimeoutConfig", None)
    assert TimeoutConfig is not None
    pool_config = PoolConfig(
        max_size=20,
        min_idle=5,
        test_on_check_out=False,
        retry_connection=False,
    )
    timeout_config = TimeoutConfig(
        acquire_timeout_secs=0.10,
        operation_timeout_secs=0.12,
    )

    fastapi_state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_timeout_fastapi"),
        pool_config=pool_config,
        timeout_config=timeout_config,
    )
    fastapi_app = create_fastapi_app(
        fastapi_state,
        "[unused_timeout_table]",
    )
    async with LifespanManager(fastapi_app):
        async with asgi_client(fastapi_app) as client:
            fastapi_result = await _run_timeout_lane(
                client=client,
                state=fastapi_state,
                observer=sa_connection,
                serialized_wsgi=False,
            )
    await wait_for_session_count(
        sa_connection,
        fastapi_state.application_name,
        expected=0,
    )

    flask_state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_timeout_flask_asgi"),
        pool_config=pool_config,
        timeout_config=timeout_config,
    )
    async with adapted_flask_lifespan(flask_state) as adapted_app:
        async with asgi_client(adapted_app) as client:
            flask_result = await _run_timeout_lane(
                client=client,
                state=flask_state,
                observer=sa_connection,
                # asgiref's WsgiToAsgi dispatch is thread-sensitive and
                # serializes WSGI calls. The 100 scheduled tasks still prove
                # persistent-loop correctness; held pooled transactions create
                # deterministic checkout pressure without claiming native-ASGI
                # inter-request parallelism for Flask.
                serialized_wsgi=True,
            )
    await wait_for_session_count(
        sa_connection,
        flask_state.application_name,
        expected=0,
    )

    # Plain Flask/WSGI remains a functional compatibility lane only. Its
    # per-request event loop is deliberately excluded from the 1,000-operation
    # true-async load accounting.
    wsgi_state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_timeout_flask_wsgi"),
        max_size=1,
        min_idle=0,
        timeout_config=timeout_config,
    )
    wsgi_app = create_flask_app(wsgi_state)
    try:
        wsgi_response = await flask_request(
            wsgi_app,
            "/timeout/10?profile=wait",
        )
        assert wsgi_response.status_code == 504
        assert wsgi_response.get_json()["type"] == "OperationTimeoutError"
        assert wsgi_response.get_json()["phase"] == "operation"
        assert await scalar(wsgi_state.connection, "SELECT 1010") == 1010
    finally:
        await wsgi_state.connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        wsgi_state.application_name,
        expected=0,
    )

    lanes = (fastapi_result, flask_result)
    total_operations = sum(int(lane["total"]) for lane in lanes)
    successful = sum(int(lane["successful"]) for lane in lanes)
    acquire_timeouts = sum(
        int(lane["acquire_timeouts"]) for lane in lanes
    )
    operation_timeouts = sum(
        int(lane["operation_timeouts"]) for lane in lanes
    )
    timeout_payloads = [
        payload
        for lane in lanes
        for payload in lane["timeout_payloads"]
    ]
    max_observed_pool_connections = max(
        int(lane["maximum_connections"]) for lane in lanes
    )

    assert total_operations == 1000
    assert successful == 600
    assert acquire_timeouts == 200
    assert operation_timeouts == 200
    assert {
        payload["type"] for payload in timeout_payloads
    } == {"OperationTimeoutError"}
    assert all(
        payload["retryable"] is (payload["phase"] == "acquire")
        for payload in timeout_payloads
    )
    assert all(
        payload["connection_discarded"]
        is (payload["phase"] == "operation")
        for payload in timeout_payloads
    )
    assert all(
        payload["outcome_unknown"]
        is (payload["phase"] == "operation")
        for payload in timeout_payloads
    )
    assert max_observed_pool_connections == 20
    assert all(len(lane["ticks"]) >= 20 for lane in lanes)
    assert all(lane["retired_connection_ids"] for lane in lanes)


@case("LIFE-015")
@pytest.mark.timeout(60)
@pytest.mark.asyncio
async def test_framework_lifecycle_shutdown_modes(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
) -> None:
    LifecycleConfig = getattr(fastmssql, "LifecycleConfig", None)
    ConnectionLifecycleState = getattr(
        fastmssql,
        "ConnectionLifecycleState",
        None,
    )
    assert LifecycleConfig is not None
    assert ConnectionLifecycleState is not None

    lifecycle_config = LifecycleConfig(
        shutdown_timeout_secs=3.0,
        force_timeout_secs=1.0,
    )

    async def heartbeat(
        stop: asyncio.Event,
        ticks: list[float],
    ) -> None:
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            ticks.append(loop.time())
            await asyncio.sleep(0.01)

    expected_late_payload = {
        "type": "ConnectionLifecycleError",
        "operation": "query",
        "state": "Closing",
        "forced": False,
    }

    fastapi_state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_lifecycle_fastapi"),
        lifecycle_config=lifecycle_config,
    )
    fastapi_app = create_fastapi_app(
        fastapi_state,
        "[unused_lifecycle_table]",
    )
    fastapi_manager = LifespanManager(fastapi_app)
    fastapi_manager_open = False
    fastapi_client_context = asgi_client(
        fastapi_app,
        raise_app_exceptions=False,
    )
    fastapi_client = None
    fastapi_request: asyncio.Task | None = None
    fastapi_shutdown: asyncio.Task | None = None
    fastapi_stop = asyncio.Event()
    fastapi_ticks: list[float] = []
    fastapi_heartbeat = asyncio.create_task(
        heartbeat(fastapi_stop, fastapi_ticks)
    )
    try:
        await fastapi_manager.__aenter__()
        fastapi_manager_open = True
        fastapi_client = await fastapi_client_context.__aenter__()
        fastapi_request = asyncio.create_task(
            fastapi_client.get("/wait/15?profile=short")
        )
        await wait_for_sql_request(
            sa_connection,
            fastapi_state.application_name,
            present=True,
            timeout=3.0,
        )

        fastapi_shutdown = asyncio.create_task(
            fastapi_manager.__aexit__(None, None, None)
        )
        await wait_for_lifecycle_state(
            fastapi_state.connection,
            ConnectionLifecycleState.CLOSING,
        )
        late_response = await fastapi_client.get(
            "/timeout/99?profile=immediate"
        )
        assert late_response.status_code == 504
        assert late_response.json() == expected_late_payload
        assert fastapi_shutdown.done() is False
        assert (await fastapi_request).json() == {"value": 15}
        fastapi_request = None
        # LifespanManager follows the async context-manager protocol and
        # returns False to leave exception suppression disabled.
        assert await fastapi_shutdown is False
        fastapi_shutdown = None
        fastapi_manager_open = False
        assert (
            fastapi_state.connection.lifecycle_state
            == ConnectionLifecycleState.CLOSED
        )
    finally:
        fastapi_stop.set()
        await fastapi_heartbeat
        if fastapi_request is not None and not fastapi_request.done():
            fastapi_request.cancel()
            with suppress(asyncio.CancelledError):
                await fastapi_request
        if fastapi_shutdown is not None:
            await fastapi_shutdown
            fastapi_manager_open = False
        if fastapi_client is not None:
            await fastapi_client_context.__aexit__(None, None, None)
        if fastapi_manager_open:
            await fastapi_manager.__aexit__(None, None, None)
        await fastapi_state.connection.disconnect()
    assert len(fastapi_ticks) >= 10
    await wait_for_session_count(
        sa_connection,
        fastapi_state.application_name,
        expected=0,
    )

    flask_asgi_state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name(
            "strict_lifecycle_flask_asgi"
        ),
        lifecycle_config=lifecycle_config,
    )
    flask_lifespan = adapted_flask_lifespan(flask_asgi_state)
    flask_lifespan_open = False
    flask_client_context = None
    flask_client = None
    flask_holder: asyncio.Task | None = None
    flask_shutdown: asyncio.Task | None = None
    flask_stop = asyncio.Event()
    flask_ticks: list[float] = []
    flask_heartbeat = asyncio.create_task(
        heartbeat(flask_stop, flask_ticks)
    )
    try:
        adapted_app = await flask_lifespan.__aenter__()
        flask_lifespan_open = True
        flask_client_context = asgi_client(
            adapted_app,
            raise_app_exceptions=False,
        )
        flask_client = await flask_client_context.__aenter__()

        # WsgiToAsgi serializes WSGI calls. A direct operation on the same
        # application-scoped connection is used as the admitted holder so the
        # late HTTP request can actually reach the driver during Closing.
        flask_holder = asyncio.create_task(
            scalar(
                flask_asgi_state.connection,
                (
                    f"/* {flask_asgi_state.application_name} */ "
                    "WAITFOR DELAY '00:00:01'; SELECT 15"
                ),
            )
        )
        await wait_for_sql_request(
            sa_connection,
            flask_asgi_state.application_name,
            present=True,
            timeout=3.0,
        )
        flask_shutdown = asyncio.create_task(
            flask_lifespan.__aexit__(None, None, None)
        )
        await wait_for_lifecycle_state(
            flask_asgi_state.connection,
            ConnectionLifecycleState.CLOSING,
        )
        late_response = await flask_client.get(
            "/timeout/99?profile=immediate"
        )
        assert late_response.status_code == 504
        assert late_response.json() == expected_late_payload
        assert flask_shutdown.done() is False
        assert await flask_holder == 15
        flask_holder = None
        assert await flask_shutdown is False
        flask_shutdown = None
        flask_lifespan_open = False
        assert (
            flask_asgi_state.connection.lifecycle_state
            == ConnectionLifecycleState.CLOSED
        )
    finally:
        flask_stop.set()
        await flask_heartbeat
        if flask_holder is not None and not flask_holder.done():
            flask_holder.cancel()
            with suppress(asyncio.CancelledError):
                await flask_holder
        if flask_shutdown is not None:
            await flask_shutdown
            flask_lifespan_open = False
        if flask_client_context is not None and flask_client is not None:
            await flask_client_context.__aexit__(None, None, None)
        if flask_lifespan_open:
            await flask_lifespan.__aexit__(None, None, None)
        await flask_asgi_state.connection.disconnect()
    assert len(flask_ticks) >= 10
    await wait_for_session_count(
        sa_connection,
        flask_asgi_state.application_name,
        expected=0,
    )

    # Plain Flask/WSGI gets a fresh event loop per async request. This lane
    # proves functional close/reopen compatibility only, not true async
    # inter-request concurrency.
    flask_wsgi_state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name(
            "strict_lifecycle_flask_wsgi"
        ),
        max_size=1,
        lifecycle_config=lifecycle_config,
    )
    flask_wsgi_app = create_flask_app(flask_wsgi_state)
    try:
        first = await flask_request(flask_wsgi_app, "/loop")
        assert first.status_code == 200
        assert first.get_json()["sql_value"] == 15
        assert await flask_wsgi_state.connection.disconnect() is True
        assert (
            flask_wsgi_state.connection.lifecycle_state
            == ConnectionLifecycleState.CLOSED
        )
        await wait_for_session_count(
            sa_connection,
            flask_wsgi_state.application_name,
            expected=0,
        )

        second = await flask_request(flask_wsgi_app, "/loop")
        assert second.status_code == 200
        assert second.get_json()["sql_value"] == 15
        assert first.get_json()["loop_id"] != second.get_json()["loop_id"]
        assert len(flask_wsgi_state.loops) == 2
        assert (
            flask_wsgi_state.connection.lifecycle_state
            == ConnectionLifecycleState.OPEN
        )
        assert await flask_wsgi_state.connection.disconnect() is True
        assert (
            flask_wsgi_state.connection.lifecycle_state
            == ConnectionLifecycleState.CLOSED
        )
    finally:
        await flask_wsgi_state.connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        flask_wsgi_state.application_name,
        expected=0,
    )


def _assert_framework_query_successes(
    before: dict[str, object],
    after: dict[str, object],
    expected: int,
) -> None:
    delta = operation_delta(before, after, "query")
    assert delta["started"] == delta["completed"] == expected
    assert {key: delta[key] for key in OUTCOME_KEYS} == zero_outcomes(
        succeeded=expected
    )
    assert after["operations"]["query"]["in_flight"] == 0


@case("OPMET-014")
@pytest.mark.asyncio
async def test_fastapi_operation_metrics_are_exact_under_concurrency(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
    record_framework_metric,
) -> None:
    request_count = 100
    worker_count = 20
    max_size = 8
    metrics_type = getattr(fastmssql, "OperationMetricsConfig")
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_opmet_fastapi"),
        max_size=max_size,
        operation_metrics_config=metrics_type(enabled=True),
    )
    app = create_fastapi_app(state, "[unused_opmet_table]")
    after_requests: dict[str, object] | None = None

    async with LifespanManager(app):
        before = await state.connection.operation_stats()
        stop = asyncio.Event()
        ticker_task = asyncio.create_task(
            event_loop_ticks(stop, interval=0.0)
        )
        pool_task = asyncio.create_task(
            _sample_framework_pool(state.connection, stop)
        )

        async def worker(worker_id: int) -> list[int]:
            values: list[int] = []
            async with asgi_client(app) as client:
                for value in range(worker_id, request_count, worker_count):
                    response = await client.get(
                        f"/wait/{value}?profile=none"
                    )
                    assert response.status_code == 200
                    values.append(int(response.json()["value"]))
            return values

        started = time.monotonic()
        try:
            worker_results = await asyncio.gather(
                *(worker(worker_id) for worker_id in range(worker_count))
            )
        finally:
            stop.set()
            ticks = await ticker_task
            maximum_connections = await pool_task
        elapsed = time.monotonic() - started
        assert sorted(
            value
            for worker_values in worker_results
            for value in worker_values
        ) == list(range(request_count))
        after_requests = await state.connection.operation_stats()
        assert_operation_stats(after_requests, enabled=True)
        _assert_framework_query_successes(
            before,
            after_requests,
            request_count,
        )
        assert len(ticks) > 10
        assert maximum_connections <= max_size

    assert after_requests is not None
    after_shutdown = await state.connection.operation_stats()
    disconnect_delta = operation_delta(
        after_requests,
        after_shutdown,
        "disconnect",
    )
    assert disconnect_delta["started"] == disconnect_delta["completed"] == 1
    assert {
        key: disconnect_delta[key] for key in OUTCOME_KEYS
    } == zero_outcomes(succeeded=1)
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )
    record_framework_metric(
        "OPMET-014",
        request_count=request_count,
        worker_count=worker_count,
        pool_max_size=max_size,
        maximum_connections=maximum_connections,
        event_loop_ticks=len(ticks),
        elapsed_seconds=elapsed,
        exact_query_delta=True,
    )


@case("OPMET-015")
@pytest.mark.asyncio
async def test_flask_wsgi_operation_metrics_preserve_loop_limits(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
    record_framework_metric,
) -> None:
    request_count = 40
    worker_count = 10
    metrics_type = getattr(fastmssql, "OperationMetricsConfig")
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_opmet_flask_wsgi"),
        max_size=10,
        operation_metrics_config=metrics_type(enabled=True),
    )
    app = create_flask_app(state)
    disconnected = False
    try:
        before = await state.connection.operation_stats()
        first = await flask_request(app, "/loop")
        second = await flask_request(app, "/loop")

        async def worker(worker_id: int) -> list[int]:
            values: list[int] = []
            for value in range(worker_id, request_count, worker_count):
                response = await flask_request(app, f"/value/{value}")
                assert response.status_code == 200
                values.append(int(response.get_json()["value"]))
            return values

        worker_results = await asyncio.gather(
            *(worker(worker_id) for worker_id in range(worker_count))
        )
        after_requests = await state.connection.operation_stats()
        assert first.status_code == second.status_code == 200
        assert first.get_json()["sql_value"] == 15
        assert second.get_json()["sql_value"] == 15
        assert len(state.loops) == 2
        assert state.loops[0] is not state.loops[1]
        assert sorted(
            value
            for worker_values in worker_results
            for value in worker_values
        ) == list(range(request_count))
        assert_operation_stats(after_requests, enabled=True)
        _assert_framework_query_successes(
            before,
            after_requests,
            request_count + 2,
        )

        assert await state.connection.disconnect() is True
        disconnected = True
        after_shutdown = await state.connection.operation_stats()
        disconnect_delta = operation_delta(
            after_requests,
            after_shutdown,
            "disconnect",
        )
        assert disconnect_delta["started"] == disconnect_delta["completed"] == 1
        assert {
            key: disconnect_delta[key] for key in OUTCOME_KEYS
        } == zero_outcomes(succeeded=1)
    finally:
        if not disconnected:
            await state.connection.disconnect()
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )
    record_framework_metric(
        "OPMET-015",
        execution_model="WSGI per-request event loop",
        query_count=request_count + 2,
        worker_count=worker_count,
        distinct_request_loops=True,
    )


@case("OPMET-016")
@pytest.mark.asyncio
async def test_flask_asgi_operation_metrics_use_persistent_loop(
    sql_auth_config,
    sa_connection,
    unique_sql_name,
    record_framework_metric,
) -> None:
    request_count = 40
    worker_count = 10
    metrics_type = getattr(fastmssql, "OperationMetricsConfig")
    state = FrameworkState.create(
        sql_auth_config,
        application_name=unique_sql_name("strict_opmet_flask_asgi"),
        max_size=10,
        operation_metrics_config=metrics_type(enabled=True),
    )
    after_requests: dict[str, object] | None = None

    async with adapted_flask_lifespan(state) as adapted_app:
        before = await state.connection.operation_stats()
        stop = asyncio.Event()
        ticker_task = asyncio.create_task(
            event_loop_ticks(stop, interval=0.0)
        )

        async def worker(worker_id: int) -> list[int]:
            values: list[int] = []
            async with asgi_client(adapted_app) as client:
                for value in range(worker_id, request_count, worker_count):
                    response = await client.get(f"/value/{value}")
                    assert response.status_code == 200
                    values.append(int(response.json()["value"]))
            return values

        try:
            async with asgi_client(adapted_app) as client:
                first = await client.get("/loop")
                second = await client.get("/loop")
                worker_results = await asyncio.gather(
                    *(
                        worker(worker_id)
                        for worker_id in range(worker_count)
                    )
                )
        finally:
            stop.set()
            ticks = await ticker_task

        assert first.status_code == second.status_code == 200
        assert first.json()["sql_value"] == 15
        assert second.json()["sql_value"] == 15
        assert len(state.loops) == 2
        assert state.loops[0] is state.loops[1]
        assert sorted(
            value
            for worker_values in worker_results
            for value in worker_values
        ) == list(range(request_count))
        after_requests = await state.connection.operation_stats()
        assert_operation_stats(after_requests, enabled=True)
        _assert_framework_query_successes(
            before,
            after_requests,
            request_count + 2,
        )
        assert len(ticks) > 10

    assert after_requests is not None
    after_shutdown = await state.connection.operation_stats()
    disconnect_delta = operation_delta(
        after_requests,
        after_shutdown,
        "disconnect",
    )
    assert disconnect_delta["started"] == disconnect_delta["completed"] == 1
    assert {
        key: disconnect_delta[key] for key in OUTCOME_KEYS
    } == zero_outcomes(succeeded=1)
    await wait_for_session_count(
        sa_connection,
        state.application_name,
        expected=0,
    )
    record_framework_metric(
        "OPMET-016",
        execution_model="persistent ASGI loop around Flask/WSGI",
        query_count=request_count + 2,
        worker_count=worker_count,
        event_loop_ticks=len(ticks),
        persistent_loop=True,
    )
