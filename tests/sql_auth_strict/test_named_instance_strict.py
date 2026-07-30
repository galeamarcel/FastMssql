from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from fastmssql import (
    Connection,
    OperationTimeoutError,
    PoolConfig,
    SqlConnectionError,
    SslConfig,
    TimeoutConfig,
    Transaction,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import scalar
from sql_auth_strict.sql_browser_fixture import SqlBrowserFixture


ROOT = Path(__file__).resolve().parents[2]
INSTANCE_NAME = "FASTMSSQL"
DEFAULT_STRESS_METRICS = ROOT / ".artifacts/sql-auth/named-instance-stress.json"

pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def _pool_config(*, max_size: int = 1, min_idle: int = 0) -> PoolConfig:
    return PoolConfig(
        max_size=max_size,
        min_idle=min_idle,
        max_lifetime_secs=None,
        idle_timeout_secs=None,
        connection_timeout_secs=3,
        test_on_check_out=False,
        retry_connection=False,
    )


def _timeout_config(connect_timeout: float = 3.0) -> TimeoutConfig:
    return TimeoutConfig(
        connect_timeout_secs=connect_timeout,
        acquire_timeout_secs=3.0,
        operation_timeout_secs=5.0,
        transaction_timeout_secs=5.0,
        rollback_timeout_secs=3.0,
    )


def _named_connection(
    config: SqlAuthConfig,
    *,
    instance_name: str = INSTANCE_NAME,
    application_name: str = "fastmssql_named_instance_strict",
    connect_timeout: float = 3.0,
    max_size: int = 1,
    min_idle: int = 0,
) -> Connection:
    return Connection(
        server=config.host,
        instance_name=instance_name,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        application_name=application_name,
        ssl_config=SslConfig.development(),
        pool_config=_pool_config(max_size=max_size, min_idle=min_idle),
        timeout_config=_timeout_config(connect_timeout),
    )


def _direct_connection(
    config: SqlAuthConfig,
    *,
    instance_name: str | None = None,
    application_name: str = "fastmssql_named_instance_direct",
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        instance_name=instance_name,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        application_name=application_name,
        ssl_config=SslConfig.development(),
        pool_config=_pool_config(),
        timeout_config=_timeout_config(),
    )


def _ado_named_connection(config: SqlAuthConfig) -> Connection:
    connection_string = (
        rf"Server=tcp:{config.host}\{INSTANCE_NAME};"
        f"Database={config.database};"
        f"User Id={config.owner_user};"
        f"Password={config.owner_password};"
        "Encrypt=True;TrustServerCertificate=True;"
        "Application Name=fastmssql_named_instance_ado"
    )
    return Connection(
        connection_string,
        pool_config=_pool_config(),
        timeout_config=_timeout_config(),
    )


def _response(payload: bytes, *, response_type: int = 0x05) -> bytes:
    return bytes((response_type,)) + len(payload).to_bytes(2, "little") + payload


def _assert_discovery_error(error: BaseException) -> None:
    assert isinstance(error, SqlConnectionError)
    assert not isinstance(error, OperationTimeoutError)
    assert error.stage == "sql_browser_discovery"
    assert error.retryable is True
    assert error.connection_discarded is False
    assert error.outcome_unknown is False


def _assert_connect_timeout(error: BaseException, expected: float) -> None:
    assert isinstance(error, OperationTimeoutError)
    assert error.phase == "connect"
    assert error.retryable is True
    assert error.connection_discarded is False
    assert error.outcome_unknown is False
    assert error.timeout_seconds == pytest.approx(expected, abs=0.05)


async def _disconnect(connection: Connection) -> None:
    await connection.disconnect()


@case("NINST-001", "NINST-003", "NINST-010")
@pytest.mark.asyncio
async def test_pooled_named_instance_observes_exact_request_and_queries_real_sql(
    sql_auth_config: SqlAuthConfig,
) -> None:
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
    )
    connection = _named_connection(sql_auth_config)
    try:
        async with fixture:
            assert await connection.connect() is True
            result = await connection.query(
                "SELECT @P1 + 1 AS answer, @@SPID AS spid",
                [41],
            )
            row = result.fetchone()
            assert row["answer"] == 42
            assert int(row["spid"]) > 0
            snapshot = fixture.snapshot()
            assert snapshot.valid_requests >= 1
            assert snapshot.invalid_requests == 0
            assert set(snapshot.requests) == {fixture.expected_request}
            stats = await connection.pool_stats()
            assert snapshot.valid_requests == stats["connections_created"]
    finally:
        await _disconnect(connection)


@case("NINST-002")
@pytest.mark.asyncio
async def test_instance_name_wire_limits_are_rejected_before_udp(
    sql_auth_config: SqlAuthConfig,
) -> None:
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
    )
    invalid = (
        ("", "empty"),
        ("embedded\x00nul", "nul"),
        ("x" * 33, "32-byte"),
    )
    async with fixture:
        for instance_name, expected_detail in invalid:
            connection = _named_connection(
                sql_auth_config,
                instance_name=instance_name,
            )
            try:
                with pytest.raises(SqlConnectionError) as captured:
                    await connection.connect()
                _assert_discovery_error(captured.value)
                assert expected_detail in str(captured.value).lower()
            finally:
                await _disconnect(connection)
        snapshot = fixture.snapshot()
        assert snapshot.datagrams == 0
        assert snapshot.valid_requests == 0


@case("NINST-004")
@pytest.mark.parametrize(
    "malformed",
    (
        b"",
        b"\x05\x01",
        b"\x04\x00\x00",
        b"\x05\x01\x00",
        b"\x05\x01\x04",
    ),
)
@pytest.mark.asyncio
async def test_malformed_browser_responses_fail_without_escape_or_panic(
    sql_auth_config: SqlAuthConfig,
    malformed: bytes,
) -> None:
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
        mode="malformed",
        malformed_response=malformed,
    )
    connection = _named_connection(sql_auth_config)
    try:
        async with fixture:
            with pytest.raises(SqlConnectionError) as captured:
                await connection.connect()
            _assert_discovery_error(captured.value)
            snapshot = fixture.snapshot()
            assert snapshot.valid_requests == 1
            assert snapshot.responses == 1
    finally:
        await _disconnect(connection)


@case("NINST-005")
@pytest.mark.parametrize(
    "payload",
    (
        b"InstanceName;FASTMSSQL;",
        b"tcp;;",
        b"tcp;abc;",
        b"tcp;0;",
        b"tcp;65536;",
        b"tcp;1433;TCP;1434;",
    ),
)
@pytest.mark.asyncio
async def test_invalid_or_duplicate_tcp_fields_are_rejected(
    sql_auth_config: SqlAuthConfig,
    payload: bytes,
) -> None:
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
        mode="malformed",
        malformed_response=_response(payload),
    )
    connection = _named_connection(sql_auth_config)
    try:
        async with fixture:
            with pytest.raises(SqlConnectionError) as captured:
                await connection.connect()
            _assert_discovery_error(captured.value)
            assert fixture.snapshot().valid_requests == 1
    finally:
        await _disconnect(connection)


@case("NINST-006")
@pytest.mark.asyncio
async def test_wrong_source_response_cannot_select_a_tcp_target(
    sql_auth_config: SqlAuthConfig,
) -> None:
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
        mode="wrong_source",
    )
    connection = _named_connection(sql_auth_config, connect_timeout=2.5)
    started = time.monotonic()
    try:
        async with fixture:
            with pytest.raises(SqlConnectionError) as captured:
                await connection.connect()
            elapsed = time.monotonic() - started
            _assert_discovery_error(captured.value)
            snapshot = fixture.snapshot()
            assert snapshot.valid_requests == 1
            assert snapshot.source_rejection_probes == 1
            assert snapshot.responses == 0
            assert 0.75 <= elapsed < 2.5
    finally:
        await _disconnect(connection)


@case("NINST-007")
@pytest.mark.asyncio
async def test_silent_browser_has_a_bounded_inner_failure(
    sql_auth_config: SqlAuthConfig,
) -> None:
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
        mode="silent",
    )
    connection = _named_connection(sql_auth_config, connect_timeout=2.5)
    started = time.monotonic()
    try:
        async with fixture:
            with pytest.raises(SqlConnectionError) as captured:
                await connection.connect()
            elapsed = time.monotonic() - started
            _assert_discovery_error(captured.value)
            assert fixture.snapshot().valid_requests == 1
            assert 0.75 <= elapsed < 2.5
    finally:
        await _disconnect(connection)


@case("NINST-008", "NINST-016")
@pytest.mark.asyncio
async def test_refused_discovered_tcp_is_structured_and_preserves_detail(
    sql_auth_config: SqlAuthConfig,
) -> None:
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
        mode="refused_tcp",
    )
    connection = _named_connection(sql_auth_config)
    try:
        async with fixture:
            with pytest.raises(SqlConnectionError) as captured:
                await connection.connect()
            _assert_discovery_error(captured.value)
            detail = str(captured.value).lower()
            assert "refused" in detail or "actively refused" in detail
            snapshot = fixture.snapshot()
            assert snapshot.valid_requests == 1
            assert snapshot.responses == 1
    finally:
        await _disconnect(connection)


@case("NINST-009")
@pytest.mark.asyncio
async def test_direct_no_instance_sql_path_remains_unchanged(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _direct_connection(sql_auth_config)
    try:
        assert await scalar(connection, "SELECT @P1 + 1", [8]) == 9
    finally:
        await _disconnect(connection)


@case("NINST-011")
@pytest.mark.asyncio
async def test_ado_named_instance_string_discovers_and_queries(
    sql_auth_config: SqlAuthConfig,
) -> None:
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
    )
    connection = _ado_named_connection(sql_auth_config)
    try:
        async with fixture:
            assert await scalar(connection, "SELECT @P1 + 1", [10]) == 11
            assert fixture.snapshot().valid_requests >= 1
    finally:
        await _disconnect(connection)


@case("NINST-012")
@pytest.mark.asyncio
async def test_direct_transaction_discovers_and_settles(
    sql_auth_config: SqlAuthConfig,
) -> None:
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
    )
    transaction = Transaction(
        server=sql_auth_config.host,
        instance_name=INSTANCE_NAME,
        database=sql_auth_config.database,
        username=sql_auth_config.owner_user,
        password=sql_auth_config.owner_password,
        application_name="fastmssql_named_instance_direct_transaction",
        ssl_config=SslConfig.development(),
        timeout_config=_timeout_config(),
    )
    try:
        async with fixture:
            await transaction.begin()
            assert await scalar(transaction, "SELECT @P1 + 1", [12]) == 13
            await transaction.rollback()
            assert fixture.snapshot().valid_requests == 1
    finally:
        await transaction.close()


@case("NINST-013")
@pytest.mark.asyncio
async def test_explicit_port_bypasses_sql_browser_even_with_instance_metadata(
    sql_auth_config: SqlAuthConfig,
) -> None:
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
    )
    connection = _direct_connection(
        sql_auth_config,
        instance_name=INSTANCE_NAME,
    )
    try:
        async with fixture:
            assert await scalar(connection, "SELECT @P1 + 1", [20]) == 21
            await asyncio.sleep(0.05)
            assert fixture.snapshot().datagrams == 0
    finally:
        await _disconnect(connection)


@case("NINST-014")
@pytest.mark.asyncio
async def test_shorter_outer_connect_deadline_wins_over_browser_timeout(
    sql_auth_config: SqlAuthConfig,
) -> None:
    outer_timeout = 0.2
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
        mode="silent",
    )
    connection = _named_connection(
        sql_auth_config,
        connect_timeout=outer_timeout,
    )
    started = time.monotonic()
    try:
        async with fixture:
            with pytest.raises(OperationTimeoutError) as captured:
                await connection.connect()
            elapsed = time.monotonic() - started
            _assert_connect_timeout(captured.value, outer_timeout)
            assert fixture.snapshot().valid_requests == 1
            assert outer_timeout * 0.5 <= elapsed < 0.8
    finally:
        await _disconnect(connection)


@case("NINST-015")
@pytest.mark.asyncio
async def test_cancelled_discovery_leaves_connection_recoverable(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _named_connection(sql_auth_config, connect_timeout=3.0)
    silent = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
        mode="silent",
    )
    task: asyncio.Task[bool] | None = None
    try:
        async with silent:
            task = asyncio.create_task(connection.connect())
            await silent.wait_for_requests(1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        valid = SqlBrowserFixture(
            host=sql_auth_config.host,
            tcp_port=sql_auth_config.port,
        )
        async with valid:
            assert await connection.connect() is True
            assert await scalar(connection, "SELECT @P1 + 1", [30]) == 31
            assert valid.snapshot().valid_requests >= 1
    finally:
        if task is not None and not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await _disconnect(connection)


@case("NINST-017")
@pytest.mark.asyncio
async def test_concurrent_pool_creation_discovers_per_physical_connection_only(
    sql_auth_config: SqlAuthConfig,
) -> None:
    pool_max = 4
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
    )
    connection = _named_connection(
        sql_auth_config,
        max_size=pool_max,
        min_idle=pool_max,
    )
    try:
        async with fixture:
            await connection.connect()
            results = await asyncio.gather(
                *(
                    connection.query(
                        "WAITFOR DELAY '00:00:00.100'; "
                        "SELECT @P1 AS operation_id, @@SPID AS spid",
                        [operation_id],
                    )
                    for operation_id in range(pool_max * 2)
                )
            )
            assert {
                int(result.fetchone()["operation_id"]) for result in results
            } == set(range(pool_max * 2))
            stats = await connection.pool_stats()
            snapshot = fixture.snapshot()
            assert stats["connections_created"] <= pool_max
            assert snapshot.valid_requests == stats["connections_created"]
            assert snapshot.valid_requests < pool_max * 2
    finally:
        await _disconnect(connection)


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


@case("NINST-018")
@pytest.mark.load
def test_required_named_instance_stress_artifact_is_fresh_and_bounded(
    record_load_metric,
) -> None:
    metrics_path = Path(
        os.getenv(
            "FASTMSSQL_NAMED_INSTANCE_STRESS_METRICS_PATH",
            str(DEFAULT_STRESS_METRICS),
        )
    )
    assert metrics_path.is_file()
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert metrics["schema_version"] == 1
    assert metrics["source_sha"] == _git_head()
    assert metrics["status"] == "passed"
    profile = metrics["profile"]
    operations = int(profile["operations"])
    assert 1_000 <= operations <= 99_999
    assert profile["succeeded"] == operations
    assert profile["failed"] == 0
    assert profile["timed_out"] == 0
    assert profile["completed_id_count"] == operations
    assert profile["missing_ids"] == []
    assert profile["duplicate_ids"] == []
    expected_digest = hashlib.sha256()
    for operation_id in range(operations):
        expected_digest.update(operation_id.to_bytes(8, "little"))
    assert profile["completed_ids_sha256"] == expected_digest.hexdigest()
    assert profile["browser_requests"] == profile["successful_physical_connections"]
    assert profile["browser_requests"] < operations
    assert profile["maximum_pool_connections"] <= profile["pool_max_size"]
    assert profile["maximum_sql_sessions"] <= profile["pool_max_size"]
    assert profile["post_load_smoke"] is True
    assert profile["sessions_after_disconnect"] == 0
    assert profile["transactions_after_disconnect"] == 0
    assert profile["rss_growth_bytes"] <= profile["rss_growth_limit_bytes"]
    assert (
        profile["maximum_event_loop_gap_seconds"]
        <= profile["event_loop_gap_limit_seconds"]
    )
    record_load_metric(
        "NINST-018",
        operations=operations,
        browser_requests=profile["browser_requests"],
        physical_connections=profile["successful_physical_connections"],
        maximum_pool_connections=profile["maximum_pool_connections"],
        maximum_sql_sessions=profile["maximum_sql_sessions"],
        rss_growth_bytes=profile["rss_growth_bytes"],
        maximum_event_loop_gap_seconds=profile["maximum_event_loop_gap_seconds"],
    )


@case("NINST-019", "NINST-020", "NINST-021")
def test_wheel_windows_and_privacy_gates_are_mandatory() -> None:
    rust_workflow = (ROOT / ".github/workflows/rust-unit-tests.yml").read_text(
        encoding="utf-8"
    )
    windows_workflow = (
        ROOT / ".github/workflows/named-instance-windows.yml"
    ).read_text(encoding="utf-8")
    normalized_windows = " ".join(windows_workflow.split())

    assert "tests/test_named_instance_contract.py" in rust_workflow
    assert "maturin build" in windows_workflow
    assert "uv pip install" in windows_workflow
    assert "fastmssql-0.7.7-" in windows_workflow
    assert "PYTHONPATH" in windows_workflow
    assert "-I $smoke" in normalized_windows
    assert 'instance_name="SQLEXPRESS"' in windows_workflow
    assert "SERVERPROPERTY('InstanceName')" in windows_workflow

    secrets = tuple(
        value
        for name in (
            "FASTMSSQL_SQL_AUTH_SA_PASSWORD",
            "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD",
            "FASTMSSQL_SQL_AUTH_READONLY_PASSWORD",
            "FASTMSSQL_SQL_AUTH_DENIED_PASSWORD",
        )
        if (value := os.getenv(name))
    )
    privacy_sources = (
        ROOT / "src/pool_manager.rs",
        ROOT / "tests/sql_auth_strict/sql_browser_fixture.py",
        ROOT / "scripts/sql_auth/named_instance_stress.py",
        ROOT / ".github/workflows/named-instance-windows.yml",
    )
    artifact_root = ROOT / ".artifacts/sql-auth"
    artifacts = (
        tuple(path for path in artifact_root.glob("*") if path.is_file())
        if artifact_root.is_dir()
        else ()
    )
    for path in (*privacy_sources, *artifacts):
        data = path.read_bytes()
        for secret in secrets:
            assert secret.encode("utf-8") not in data


async def _application_activity(
    observer: Connection,
    application_name: str,
) -> tuple[int, int]:
    result = await observer.query(
        """
        SELECT
            COUNT(DISTINCT session.session_id) AS sessions,
            COUNT(DISTINCT transaction_session.transaction_id) AS transactions
        FROM sys.dm_exec_sessions AS session
        LEFT JOIN sys.dm_tran_session_transactions AS transaction_session
          ON transaction_session.session_id = session.session_id
        WHERE session.program_name = @P1
          AND session.session_id <> @@SPID
        """,
        [application_name],
    )
    row = result.fetchone()
    return int(row["sessions"]), int(row["transactions"])


@case("NINST-022")
@pytest.mark.asyncio
async def test_named_instance_teardown_leaves_zero_sessions_and_transactions(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name,
) -> None:
    application_name = unique_sql_name("strict_named_teardown")
    fixture = SqlBrowserFixture(
        host=sql_auth_config.host,
        tcp_port=sql_auth_config.port,
    )
    connection = _named_connection(
        sql_auth_config,
        application_name=application_name,
        max_size=2,
        min_idle=2,
    )
    transaction = Transaction(
        server=sql_auth_config.host,
        instance_name=INSTANCE_NAME,
        database=sql_auth_config.database,
        username=sql_auth_config.owner_user,
        password=sql_auth_config.owner_password,
        application_name=application_name,
        ssl_config=SslConfig.development(),
        timeout_config=_timeout_config(),
    )
    async with fixture:
        try:
            await connection.connect()
            assert await scalar(connection, "SELECT @P1 + 1", [40]) == 41
            await transaction.begin()
            assert await scalar(transaction, "SELECT @P1 + 1", [41]) == 42
            await transaction.rollback()
        finally:
            await transaction.close()
            await connection.disconnect()

    deadline = time.monotonic() + 3.0
    activity = await _application_activity(sa_connection, application_name)
    while activity != (0, 0) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        activity = await _application_activity(sa_connection, application_name)
    assert activity == (0, 0)
