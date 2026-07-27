from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastmssql import (
    Connection,
    ConversionError,
    OperationMetricsConfig,
    OperationTimeoutError,
    Parameter,
    PoolConfig,
    SqlError,
    SslConfig,
    TimeoutConfig,
    TypedNull,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def _isolated_connection(
    config: SqlAuthConfig,
    *,
    application_name: str | None = None,
    operation_timeout_secs: float | None = None,
    metrics: bool = False,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        application_name=application_name,
        pool_config=PoolConfig(
            max_size=1,
            min_idle=1,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            test_on_check_out=False,
            retry_connection=False,
        ),
        timeout_config=TimeoutConfig(
            connect_timeout_secs=2.0,
            acquire_timeout_secs=2.0,
            operation_timeout_secs=operation_timeout_secs,
            rollback_timeout_secs=2.0,
        ),
        operation_metrics_config=OperationMetricsConfig(enabled=metrics),
    )


async def _physical_identity(connection: Connection) -> tuple[int, str]:
    row = (
        await connection.query(
            """
            SELECT
                @@SPID AS session_id,
                CONVERT(NVARCHAR(36), connection_id) AS connection_id
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """
        )
    ).fetchone()
    assert row is not None
    return int(row["session_id"]), str(row["connection_id"])


async def _wait_for_lock_wait(
    observer: Connection,
    session_id: int,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        waiting = await scalar(
            observer,
            """
            SELECT COUNT(*)
            FROM sys.dm_exec_requests
            WHERE session_id = @P1
              AND wait_type LIKE N'LCK_M_%'
            """,
            [session_id],
        )
        if waiting == 1:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"session {session_id} did not enter the expected SQL Server lock wait"
    )


async def _wait_for_identity_absent(
    observer: Connection,
    session_id: int,
    connection_id: str,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        count = await scalar(
            observer,
            """
            SELECT COUNT(*)
            FROM sys.dm_exec_connections
            WHERE session_id = @P1
              AND CONVERT(NVARCHAR(36), connection_id) = @P2
            """,
            [session_id, connection_id],
        )
        if count == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(
        "cancelled/timed-out native-bulk physical connection remained alive"
    )


def _render_error(error: BaseException) -> str:
    return f"{error!s}\n{error!r}\n{getattr(error, '__dict__', {})!r}"


@case("BULK-006")
@pytest.mark.asyncio
async def test_native_bulk_surface_validation_and_empty_zero_io() -> None:
    connection = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="native_bulk_probe",
        password="not-used",
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
        timeout_config=TimeoutConfig(
            connect_timeout_secs=1.0,
            acquire_timeout_secs=1.0,
        ),
        operation_metrics_config=OperationMetricsConfig(enabled=True),
    )

    assert (
        await connection.native_bulk_insert(
            "dbo.target",
            ["value"],
            [],
        )
        == 0
    )
    assert await connection.is_connected() is False
    snapshot = await connection.operation_stats()
    assert snapshot["operations"]["bulk_insert"]["started"] == 0
    assert snapshot["operations"]["bulk_insert"]["completed"] == 0

    sentinel = object()
    for invalid in (True, False, 0, -1, 10_001, 1.5, "1"):
        with pytest.raises((TypeError, ValueError), match="chunk_size"):
            await connection.native_bulk_insert(
                "dbo.target",
                ["value"],
                [[sentinel]],
                chunk_size=invalid,
            )

    assert await connection.is_connected() is False
    after_invalid = await connection.operation_stats()
    assert after_invalid["operations"]["bulk_insert"]["started"] == 0


@case("BULK-007")
@pytest.mark.asyncio
async def test_native_bulk_ordered_subset_defaults_nulls_and_count(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_native_bulk_subset")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            identity_id INT IDENTITY(1, 1) PRIMARY KEY,
            value INT NOT NULL,
            note NVARCHAR(40) NULL,
            defaulted INT NOT NULL
                CONSTRAINT {_constraint_name(raw_table, "default")}
                DEFAULT (73)
        )
        """
    )

    affected = await owner_connection.native_bulk_insert(
        raw_table,
        ["note", "value"],
        [["first", 11], [None, 22], ["third", 33]],
        chunk_size=2,
    )
    assert affected == 3

    rows = (
        await owner_connection.query(
            f"""
            SELECT identity_id, value, note, defaulted
            FROM {table}
            ORDER BY identity_id
            """
        )
    ).rows()
    assert [row.to_dict() for row in rows] == [
        {
            "identity_id": 1,
            "value": 11,
            "note": "first",
            "defaulted": 73,
        },
        {
            "identity_id": 2,
            "value": 22,
            "note": None,
            "defaulted": 73,
        },
        {
            "identity_id": 3,
            "value": 33,
            "note": "third",
            "defaulted": 73,
        },
    ]


def _constraint_name(table: str, suffix: str) -> str:
    return quote_identifier(f"{table}_{suffix}")


@case("BULK-008")
@pytest.mark.asyncio
async def test_native_bulk_target_guided_mixed_types_and_typed_nulls(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_native_bulk_types")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            row_id INT PRIMARY KEY,
            bit_value BIT NULL,
            tiny_value TINYINT NULL,
            small_value SMALLINT NULL,
            int_value INT NULL,
            big_value BIGINT NULL,
            real_value REAL NULL,
            float_value FLOAT NULL,
            amount DECIMAL(19,4) NULL,
            ansi_value VARCHAR(20) NULL,
            unicode_value NVARCHAR(20) NULL,
            binary_value VARBINARY(20) NULL,
            entity_id UNIQUEIDENTIFIER NULL,
            date_value DATE NULL,
            time_value TIME(3) NULL,
            datetime_value DATETIME NULL,
            small_datetime_value SMALLDATETIME NULL,
            datetime2_value DATETIME2(3) NULL,
            offset_value DATETIMEOFFSET(3) NULL,
            xml_value XML NULL
        )
        """
    )

    entity_id = UUID("12345678-1234-5678-9234-567812345678")
    offset_value = datetime(
        2024,
        2,
        29,
        12,
        34,
        56,
        123000,
        tzinfo=timezone(timedelta(hours=2)),
    )
    columns = [
        "row_id",
        "bit_value",
        "tiny_value",
        "small_value",
        "int_value",
        "big_value",
        "real_value",
        "float_value",
        "amount",
        "ansi_value",
        "unicode_value",
        "binary_value",
        "entity_id",
        "date_value",
        "time_value",
        "datetime_value",
        "small_datetime_value",
        "datetime2_value",
        "offset_value",
        "xml_value",
    ]
    rows = [
        [
            1,
            True,
            255,
            -32_768,
            2_147_483_647,
            9_223_372_036_854_775_807,
            1.25,
            2.5,
            Parameter(Decimal("123456789012345.6789"), "DECIMAL(19,4)"),
            "ansi",
            "șț unicode",
            b"\x00\x01\xff",
            entity_id,
            date(2024, 2, 29),
            time(12, 34, 56, 123000),
            datetime(2024, 2, 29, 12, 34, 56, 120000),
            datetime(2024, 2, 29, 12, 34),
            Parameter(
                datetime(2024, 2, 29, 12, 34, 56, 123000),
                "DATETIME2(3)",
            ),
            offset_value,
            '<root value="7"/>',
        ],
        [
            2,
            TypedNull.BIT,
            TypedNull.TINYINT,
            TypedNull.SMALLINT,
            TypedNull.INT,
            TypedNull.BIGINT,
            TypedNull.FLOAT32,
            TypedNull.FLOAT64,
            TypedNull.NUMERIC,
            TypedNull.STRING,
            TypedNull.STRING,
            TypedNull.BINARY,
            TypedNull.GUID,
            TypedNull.DATE,
            TypedNull.TIME,
            TypedNull.DATETIME,
            TypedNull.SMALLDATETIME,
            TypedNull.DATETIME2,
            TypedNull.DATETIMEOFFSET,
            TypedNull.XML,
        ],
    ]

    assert (
        await owner_connection.native_bulk_insert(
            raw_table,
            columns,
            rows,
            chunk_size=1,
        )
        == 2
    )

    first = (
        await owner_connection.query(
            f"""
            SELECT
                row_id, bit_value, tiny_value, small_value, int_value,
                big_value, real_value, float_value, amount, ansi_value,
                unicode_value, binary_value, entity_id, date_value,
                time_value, datetime_value, small_datetime_value,
                datetime2_value, offset_value,
                CONVERT(NVARCHAR(MAX), xml_value) AS xml_value
            FROM {table}
            WHERE row_id = 1
            """
        )
    ).fetchone()
    assert first is not None
    assert first["row_id"] == 1
    assert first["bit_value"] is True
    assert first["tiny_value"] == 255
    assert first["small_value"] == -32_768
    assert first["int_value"] == 2_147_483_647
    assert first["big_value"] == 9_223_372_036_854_775_807
    assert first["real_value"] == pytest.approx(1.25)
    assert first["float_value"] == pytest.approx(2.5)
    assert first["amount"] == Decimal("123456789012345.6789")
    assert first["ansi_value"] == "ansi"
    assert first["unicode_value"] == "șț unicode"
    assert first["binary_value"] == b"\x00\x01\xff"
    assert first["entity_id"] == entity_id
    assert first["date_value"] == date(2024, 2, 29)
    assert first["time_value"] == time(12, 34, 56, 123000)
    assert first["datetime_value"] == datetime(2024, 2, 29, 12, 34, 56, 120000)
    assert first["small_datetime_value"] == datetime(2024, 2, 29, 12, 34)
    assert first["datetime2_value"] == datetime(2024, 2, 29, 12, 34, 56, 123000)
    assert first["offset_value"] == offset_value
    assert first["xml_value"] == '<root value="7"/>'

    second = (
        await owner_connection.query(f"SELECT * FROM {table} WHERE row_id = 2")
    ).fetchone()
    assert second is not None
    assert second["row_id"] == 2
    assert all(second[column] is None for column in columns[1:])


@case("BULK-009")
@pytest.mark.asyncio
async def test_native_bulk_restricted_and_unsupported_targets_are_private(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table_sentinel = "native_bulk_private_target"
    raw_table = unique_sql_name(table_sentinel)
    table = quote_identifier(raw_table)
    restricted_columns = {
        "identity": unique_sql_name("native_bulk_identity"),
        "computed": unique_sql_name("native_bulk_computed"),
        "rowversion": unique_sql_name("native_bulk_rowversion"),
        "money": unique_sql_name("native_bulk_money"),
        "variant": unique_sql_name("native_bulk_variant"),
    }
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            {quote_identifier(restricted_columns["identity"])}
                INT IDENTITY(1, 1) NOT NULL,
            base_value INT NOT NULL,
            {quote_identifier(restricted_columns["computed"])}
                AS (base_value + 1),
            {quote_identifier(restricted_columns["rowversion"])}
                ROWVERSION,
            {quote_identifier(restricted_columns["money"])}
                MONEY NULL,
            {quote_identifier(restricted_columns["variant"])}
                SQL_VARIANT NULL
        )
        """
    )

    value_sentinel = "native_bulk_value_must_not_leak"
    attempts = (
        (restricted_columns["identity"], 7),
        (restricted_columns["computed"], 7),
        (restricted_columns["rowversion"], b"12345678"),
        (restricted_columns["money"], Decimal("1.23")),
        (restricted_columns["variant"], value_sentinel),
    )
    for column, value in attempts:
        with pytest.raises(ConversionError) as captured:
            await owner_connection.native_bulk_insert(
                raw_table,
                [column],
                [[value]],
            )
        rendered = _render_error(captured.value)
        for sentinel in (
            table_sentinel,
            raw_table,
            column,
            value_sentinel,
        ):
            assert sentinel not in rendered
        assert captured.value.retryable is False
        assert captured.value.outcome_unknown is False
        assert await scalar(owner_connection, "SELECT 1") == 1

    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0


@case("BULK-010")
@pytest.mark.asyncio
async def test_native_bulk_late_failures_choose_reuse_or_immediate_retirement(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table_sentinel = "native_bulk_late_private"
    value_sentinel = "native_bulk_late_value_private"
    raw_table = unique_sql_name(table_sentinel)
    raw_column = unique_sql_name("native_bulk_late_column")
    table = quote_identifier(raw_table)
    column = quote_identifier(raw_column)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, {column} INT NOT NULL)"
    )

    class UnsupportedCell:
        def __repr__(self) -> str:
            return value_sentinel

    connection = _isolated_connection(sql_auth_config)
    try:
        await connection.connect()
        original_identity = await _physical_identity(connection)
        with pytest.raises(ConversionError) as captured:
            await connection.native_bulk_insert(
                raw_table,
                ["id", raw_column],
                [[1, 10], [2, 20], [3, UnsupportedCell()]],
                chunk_size=2,
            )

        error = captured.value
        assert error.row_index == 2
        assert error.column_index == 1
        assert error.parameter_index == 5
        assert error.sql_type == "INT"
        assert error.reason == "wrong_value_kind"
        assert error.wire_sent is True
        assert error.connection_discarded is False
        assert error.outcome_unknown is False
        rendered = _render_error(error)
        for sentinel in (
            table_sentinel,
            raw_table,
            raw_column,
            value_sentinel,
        ):
            assert sentinel not in rendered

        assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0
        assert await _physical_identity(connection) == original_identity
        assert await scalar(connection, "SELECT 1") == 1
    finally:
        await connection.disconnect()

    raw_utf8_table = unique_sql_name("native_bulk_utf8_overflow")
    utf8_table = quote_identifier(raw_utf8_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {utf8_table}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {utf8_table} (
            id INT NOT NULL PRIMARY KEY,
            encoded_value VARCHAR(1)
                COLLATE Latin1_General_100_CI_AS_SC_UTF8 NOT NULL
        )
        """
    )

    encoding_connection = _isolated_connection(sql_auth_config)
    try:
        await encoding_connection.connect()
        original_identity = await _physical_identity(encoding_connection)
        with pytest.raises(ConversionError) as captured:
            await encoding_connection.native_bulk_insert(
                raw_utf8_table,
                ["id", "encoded_value"],
                [[1, "é"]],
                chunk_size=1,
            )

        error = captured.value
        assert error.reason == "bulk_encoding_failed"
        assert error.wire_sent is True
        assert error.connection_discarded is True
        assert error.outcome_unknown is False
        assert error.__cause__ is None
        assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {utf8_table}") == 0
        assert await _physical_identity(encoding_connection) != original_identity
        assert await scalar(encoding_connection, "SELECT 1") == 1
    finally:
        await encoding_connection.disconnect()


@case("BULK-011")
@pytest.mark.asyncio
async def test_native_bulk_transaction_is_neutral_then_rollback_only(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_native_bulk_transaction")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")

    transaction = owner_connection.transaction()
    try:
        await transaction.begin()
        assert (
            await transaction.native_bulk_insert(
                raw_table,
                ["id"],
                [[1]],
            )
            == 1
        )
        assert await scalar(transaction, f"SELECT COUNT(*) FROM {table}") == 1
        assert await scalar(transaction, "SELECT @@TRANCOUNT") == 1
        assert await scalar(transaction, "SELECT XACT_STATE()") == 1

        with pytest.raises(SqlError):
            await transaction.native_bulk_insert(
                raw_table,
                ["id"],
                [[2], [2]],
            )

        for rejected in (
            lambda: transaction.query("SELECT 1"),
            lambda: transaction.execute(f"INSERT INTO {table} VALUES (3)"),
            lambda: transaction.native_bulk_insert(raw_table, ["id"], [[4]]),
            transaction.commit,
        ):
            with pytest.raises(RuntimeError, match="rollback required"):
                await rejected()

        await transaction.rollback()
        assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0
    finally:
        await transaction.close()


@case("BULK-012")
@pytest.mark.asyncio
async def test_native_bulk_cancel_and_timeout_retire_physical_sessions(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    sa_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_parent = unique_sql_name("strict_native_bulk_parent")
    raw_child = unique_sql_name("strict_native_bulk_child")
    parent = quote_identifier(raw_parent)
    child = quote_identifier(raw_child)
    foreign_key = quote_identifier(unique_sql_name("strict_native_bulk_fk"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {parent}")
    cleanup_registry.add(f"DROP TABLE IF EXISTS {child}")
    await owner_connection.execute(
        f"CREATE TABLE {parent} (id INT PRIMARY KEY, touched INT NOT NULL)"
    )
    await owner_connection.execute(
        f"""
        CREATE TABLE {child} (
            id INT PRIMARY KEY,
            parent_id INT NOT NULL,
            CONSTRAINT {foreign_key}
                FOREIGN KEY (parent_id) REFERENCES {parent}(id)
        )
        """
    )
    await owner_connection.execute(f"INSERT INTO {parent} VALUES (1, 0)")

    async def hold_parent_lock():
        blocker = transaction_factory()
        await blocker.begin()
        await blocker.execute(f"UPDATE {parent} SET touched = touched + 1 WHERE id = 1")
        return blocker

    cancel_connection = _isolated_connection(
        sql_auth_config,
        application_name=unique_sql_name("strict_native_bulk_cancel_app"),
        metrics=True,
    )
    cancel_blocker = await hold_parent_lock()
    cancel_task: asyncio.Task | None = None
    try:
        await cancel_connection.connect()
        cancel_identity = await _physical_identity(cancel_connection)
        cancel_task = asyncio.create_task(
            cancel_connection.native_bulk_insert(
                raw_child,
                ["id", "parent_id"],
                [[1, 1]],
            )
        )
        await _wait_for_lock_wait(sa_connection, cancel_identity[0])
        cancel_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancel_task
        await cancel_blocker.rollback()
        await _wait_for_identity_absent(sa_connection, *cancel_identity)

        replacement = await _physical_identity(cancel_connection)
        assert replacement != cancel_identity
        assert await scalar(cancel_connection, "SELECT 1") == 1
        assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {child}") == 0
        metrics = await cancel_connection.operation_stats()
        assert metrics["operations"]["bulk_insert"]["started"] == 1
        assert metrics["operations"]["bulk_insert"]["cancelled"] == 1
    finally:
        if cancel_task is not None and not cancel_task.done():
            cancel_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancel_task
        elif cancel_task is not None and not cancel_task.cancelled():
            cancel_task.exception()
        await cancel_blocker.close()
        await cancel_connection.disconnect()

    timeout_connection = _isolated_connection(
        sql_auth_config,
        application_name=unique_sql_name("strict_native_bulk_timeout_app"),
        operation_timeout_secs=1.0,
    )
    timeout_blocker = await hold_parent_lock()
    timeout_task: asyncio.Task | None = None
    try:
        await timeout_connection.connect()
        timeout_identity = await _physical_identity(timeout_connection)
        timeout_task = asyncio.create_task(
            timeout_connection.native_bulk_insert(
                raw_child,
                ["id", "parent_id"],
                [[2, 1]],
            )
        )
        await _wait_for_lock_wait(sa_connection, timeout_identity[0])
        with pytest.raises(OperationTimeoutError) as captured:
            await timeout_task
        error = captured.value
        assert error.operation == "bulk_insert"
        assert error.connection_discarded is True
        assert error.outcome_unknown is False
        await timeout_blocker.rollback()
        await _wait_for_identity_absent(sa_connection, *timeout_identity)

        replacement = await _physical_identity(timeout_connection)
        assert replacement != timeout_identity
        assert await scalar(timeout_connection, "SELECT 1") == 1
        assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {child}") == 0
    finally:
        if timeout_task is not None and not timeout_task.done():
            timeout_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await timeout_task
        elif timeout_task is not None and not timeout_task.cancelled():
            timeout_task.exception()
        await timeout_blocker.close()
        await timeout_connection.disconnect()
