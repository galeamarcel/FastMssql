from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime

from fastmssql import (
    Connection,
    OperationMetricsConfig,
    Parameter,
    Parameters,
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


def _bracket(value: str) -> str:
    return f"[{value.replace(']', ']]')}]"


async def _wait_for_request(
    sa_connection: Connection,
    token: str,
    *,
    present: bool,
    timeout: float = 3.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        count = await scalar(
            sa_connection,
            """
            SELECT COUNT(*)
            FROM sys.dm_exec_requests AS request
            CROSS APPLY sys.dm_exec_sql_text(request.sql_handle) AS sql_text
            WHERE request.session_id <> @@SPID
              AND sql_text.text LIKE @P1
            """,
            [f"%{token}%"],
        )
        if (count > 0) is present:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"request token {token!r} did not reach present={present}")


def _isolated_connection(config: SqlAuthConfig) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=1,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )


@case("BATCH-001")
@pytest.mark.asyncio
async def test_empty_single_and_multiple_query_batches(
    owner_connection: Connection,
) -> None:
    assert await owner_connection.query_batch([]) == []

    single = await owner_connection.query_batch(
        [("SELECT CAST(1 AS INT) AS value", None)]
    )
    assert len(single) == 1
    assert single[0].fetchone()["value"] == 1

    multiple = await owner_connection.query_batch(
        [
            ("SELECT CAST(1 AS INT) AS value", None),
            ("SELECT CAST(2 AS INT) AS value", None),
            ("SELECT CAST(3 AS INT) AS value", None),
        ]
    )
    assert [result.fetchone()["value"] for result in multiple] == [1, 2, 3]


@case("BATCH-002")
@pytest.mark.asyncio
async def test_parameterized_mixed_query_batch(
    owner_connection: Connection,
) -> None:
    results = await owner_connection.query_batch(
        [
            ("SELECT @P1 AS value", [11]),
            (
                "SELECT @P1 AS text_value, @P2 AS integer_value",
                ["mixed", 22],
            ),
            ("SELECT CAST(33 AS INT) AS value", None),
            (
                """
                SELECT
                    @P1 AS value,
                    CONVERT(
                        VARCHAR(128),
                        SQL_VARIANT_PROPERTY(@P1, 'BaseType')
                    ) AS base_type
                """,
                [Parameter(44, "INT")],
            ),
            (
                "SELECT @P1 AS value",
                Parameters(Parameter("typed", "VARCHAR(10)")),
            ),
        ]
    )
    assert results[0].fetchone().to_dict() == {"value": 11}
    assert results[1].fetchone().to_dict() == {
        "text_value": "mixed",
        "integer_value": 22,
    }
    assert results[2].fetchone().to_dict() == {"value": 33}
    assert results[3].fetchone().to_dict() == {
        "value": 44,
        "base_type": "int",
    }
    assert results[4].fetchone().to_dict() == {"value": "typed"}


@case("BATCH-003")
@pytest.mark.asyncio
async def test_query_batch_order_and_independent_results(
    owner_connection: Connection,
) -> None:
    results = await owner_connection.query_batch(
        [
            (
                "SELECT value FROM (VALUES (1), (2)) AS source(value) ORDER BY value",
                None,
            ),
            (
                "SELECT value FROM (VALUES (10), (20)) AS source(value) ORDER BY value",
                None,
            ),
        ]
    )
    assert results[0].fetchone()["value"] == 1
    assert results[1].position() == 0
    assert results[1].fetchone()["value"] == 10
    assert results[0].fetchone()["value"] == 2
    assert results[1].fetchone()["value"] == 20


@case("BATCH-004")
@pytest.mark.asyncio
async def test_query_batch_midstream_sql_error(
    owner_connection: Connection,
) -> None:
    with pytest.raises(SqlError):
        await owner_connection.query_batch(
            [
                ("SELECT CAST(1 AS INT) AS value", None),
                ("SELECT * FROM dbo.table_that_must_not_exist_strict", None),
                ("SELECT CAST(3 AS INT) AS value", None),
            ]
        )
    assert await scalar(owner_connection, "SELECT 4") == 4


@case("BATCH-005")
@pytest.mark.asyncio
async def test_empty_single_and_multiple_command_batches(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_command_batch"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )

    assert await owner_connection.execute_batch([]) == []
    assert await owner_connection.execute_batch(
        [(f"INSERT INTO {table} VALUES (@P1, @P2)", [1, 10])]
    ) == [1]
    assert await owner_connection.execute_batch(
        [
            (f"INSERT INTO {table} VALUES (@P1, @P2)", [2, 20]),
            (f"INSERT INTO {table} VALUES (@P1, @P2)", [3, 30]),
        ]
    ) == [1, 1]
    assert await owner_connection.execute_batch(
        [
            (
                f"INSERT INTO {table} VALUES (@P1, @P2)",
                [Parameter(4, "INT"), Parameter(40, "INT")],
            ),
            (
                f"INSERT INTO {table} VALUES (@P1, @P2)",
                Parameters(
                    Parameter(5, "INT"),
                    Parameter(50, "INT"),
                ),
            ),
        ]
    ) == [1, 1]
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 5


@case("BATCH-006")
@pytest.mark.asyncio
async def test_command_batch_row_count_order(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_batch_counts"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    counts = await owner_connection.execute_batch(
        [
            (f"INSERT INTO {table} VALUES (1, 10), (2, 20)", None),
            (f"UPDATE {table} SET value = 11 WHERE id = 1", None),
            (f"DELETE FROM {table} WHERE id = 99", None),
        ]
    )
    assert counts == [2, 1, 0]
    rows = await owner_connection.query(f"SELECT id, value FROM {table} ORDER BY id")
    assert [row.to_dict() for row in rows.rows()] == [
        {"id": 1, "value": 11},
        {"id": 2, "value": 20},
    ]


@case("BATCH-007")
@pytest.mark.asyncio
async def test_command_batch_rolls_back_fully_on_failure(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_batch_atomic"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    with pytest.raises(SqlError):
        await owner_connection.execute_batch(
            [
                (f"INSERT INTO {table} VALUES (1, 10)", None),
                (f"INSERT INTO {table} VALUES (1, 20)", None),
                (f"INSERT INTO {table} VALUES (2, 30)", None),
            ]
        )
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0


@case("BATCH-008")
@pytest.mark.asyncio
async def test_malformed_batch_items_are_rejected(
    owner_connection: Connection,
) -> None:
    malformed = [
        [["SELECT 1", None]],
        [("SELECT 1",)],
        [("SELECT 1", None, "extra")],
        [(123, None)],
        [("SELECT @P1", "not-a-list")],
    ]
    for items in malformed:
        with pytest.raises((TypeError, ValueError)):
            await owner_connection.query_batch(items)
        with pytest.raises((TypeError, ValueError)):
            await owner_connection.execute_batch(items)


@case("BATCH-009")
@pytest.mark.asyncio
async def test_basic_bulk_insert_and_persisted_rows(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_bulk_basic")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value NVARCHAR(30))"
    )
    affected = await owner_connection.bulk_insert(
        raw_table,
        ["id", "value"],
        [[1, "one"], [2, "two"], [3, "three"]],
    )
    assert affected == 3
    rows = await owner_connection.query(f"SELECT id, value FROM {table} ORDER BY id")
    assert [row.to_dict() for row in rows.rows()] == [
        {"id": 1, "value": "one"},
        {"id": 2, "value": "two"},
        {"id": 3, "value": "three"},
    ]


@case("BATCH-010")
@pytest.mark.asyncio
async def test_empty_bulk_data_is_noop(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_bulk_empty")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT)"
    )
    assert await owner_connection.bulk_insert(raw_table, ["id", "value"], []) == 0
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0


@case("BATCH-011")
@pytest.mark.asyncio
async def test_bulk_mixed_types_and_typed_nulls(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_bulk_types")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            id INT PRIMARY KEY,
            text_value NVARCHAR(40) NULL,
            flag BIT NULL,
            number FLOAT NULL,
            binary_value VARBINARY(20) NULL,
            date_value DATE NULL,
            datetime_value DATETIME2(6) NULL
        )
        """
    )
    rows = [
        [
            1,
            "mixed",
            True,
            1.25,
            b"\x00\xff",
            date(2024, 2, 29),
            datetime(2024, 2, 29, 12, 34, 56, 123456),
        ],
        [
            2,
            None,
            None,
            None,
            None,
            None,
            None,
        ],
        [
            3,
            TypedNull.STRING,
            TypedNull.BIT,
            TypedNull.FLOAT64,
            TypedNull.BINARY,
            TypedNull.DATE,
            TypedNull.DATETIME2,
        ],
    ]
    assert (
        await owner_connection.bulk_insert(
            raw_table,
            [
                "id",
                "text_value",
                "flag",
                "number",
                "binary_value",
                "date_value",
                "datetime_value",
            ],
            rows,
        )
        == 3
    )
    result = await owner_connection.query(
        f"""
        SELECT id, text_value, flag, number, binary_value,
               date_value, datetime_value
        FROM {table}
        ORDER BY id
        """
    )
    returned = result.rows()
    assert returned[0].to_dict() == {
        "id": 1,
        "text_value": "mixed",
        "flag": True,
        "number": 1.25,
        "binary_value": b"\x00\xff",
        "date_value": date(2024, 2, 29),
        "datetime_value": datetime(2024, 2, 29, 12, 34, 56, 123456),
    }
    assert all(
        returned[index][column] is None
        for index in (1, 2)
        for column in (
            "text_value",
            "flag",
            "number",
            "binary_value",
            "date_value",
            "datetime_value",
        )
    )


@case("BATCH-012")
@pytest.mark.asyncio
async def test_bulk_exact_internal_parameter_chunk_boundary(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_bulk_boundary")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            id INT PRIMARY KEY,
            value_a INT,
            value_b INT,
            value_c INT
        )
        """
    )
    rows = [[index, index + 1, index + 2, index + 3] for index in range(500)]
    assert (
        await owner_connection.bulk_insert(
            raw_table, ["id", "value_a", "value_b", "value_c"], rows
        )
        == 500
    )
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 500


@case("BATCH-013")
@pytest.mark.asyncio
async def test_bulk_multiple_chunks(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_bulk_chunks")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    rows = [[index, index * 2] for index in range(1201)]
    assert await owner_connection.bulk_insert(raw_table, ["id", "value"], rows) == len(
        rows
    )
    aggregates = (
        await owner_connection.query(
            f"""
            SELECT COUNT(*) AS row_count,
                   SUM(CAST(value AS BIGINT)) AS value_sum
            FROM {table}
            """
        )
    ).fetchone()
    assert aggregates["row_count"] == 1201
    assert aggregates["value_sum"] == sum(index * 2 for index in range(1201))


@case("BATCH-014")
@pytest.mark.asyncio
async def test_bulk_wide_table(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_bulk_wide")
    table = quote_identifier(raw_table)
    columns = [f"column_{index:03d}" for index in range(100)]
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    definitions = ", ".join(
        f"{quote_identifier(column)} INT NOT NULL" for column in columns
    )
    await owner_connection.execute(f"CREATE TABLE {table} ({definitions})")
    rows = [
        [row_index * 1000 + column_index for column_index in range(100)]
        for row_index in range(21)
    ]
    assert await owner_connection.bulk_insert(raw_table, columns, rows) == len(rows)
    selected = (
        await owner_connection.query(
            f"""
            SELECT COUNT(*) AS row_count,
                   SUM(CAST([column_000] AS BIGINT)) AS first_sum,
                   SUM(CAST([column_099] AS BIGINT)) AS last_sum
            FROM {table}
            """
        )
    ).fetchone()
    assert selected["row_count"] == 21
    assert selected["first_sum"] == sum(row[0] for row in rows)
    assert selected["last_sum"] == sum(row[-1] for row in rows)


@case("BATCH-015")
@pytest.mark.asyncio
async def test_bulk_quotes_schema_table_and_column_identifiers(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    schema = unique_sql_name("strict_bulk_schema")
    suffix = unique_sql_name("suffix")
    table_name = f"order detail]{suffix}"
    first_column = "select"
    second_column = "na me]"
    qualified_raw = f"{schema}.{table_name}"
    qualified_sql = f"{_bracket(schema)}.{_bracket(table_name)}"
    cleanup_registry.add(f"DROP SCHEMA IF EXISTS {_bracket(schema)}")
    cleanup_registry.add(f"DROP TABLE IF EXISTS {qualified_sql}")
    await owner_connection.execute(f"CREATE SCHEMA {_bracket(schema)}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {qualified_sql} (
            {_bracket(first_column)} INT PRIMARY KEY,
            {_bracket(second_column)} NVARCHAR(30)
        )
        """
    )
    assert (
        await owner_connection.bulk_insert(
            qualified_raw,
            [first_column, second_column],
            [[1, "quoted"]],
        )
        == 1
    )
    row = (
        await owner_connection.query(
            f"""
            SELECT {_bracket(first_column)}, {_bracket(second_column)}
            FROM {qualified_sql}
            """
        )
    ).fetchone()
    assert row[0] == 1
    assert row[1] == "quoted"


@case("BATCH-016")
@pytest.mark.asyncio
async def test_bulk_malformed_and_malicious_identifiers(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_guard = unique_sql_name("strict_bulk_guard")
    guard = quote_identifier(raw_guard)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {guard}")
    await owner_connection.execute(f"CREATE TABLE {guard} (id INT PRIMARY KEY)")

    with pytest.raises(ValueError, match="At least one column"):
        await owner_connection.bulk_insert(raw_guard, [], [])
    with pytest.raises(ValueError, match="null byte"):
        await owner_connection.bulk_insert(f"{raw_guard}\x00suffix", ["id"], [[1]])
    malicious = f"missing]; DROP TABLE {guard}; --"
    with pytest.raises(SqlError):
        await owner_connection.bulk_insert(malicious, ["id"], [[1]])
    assert await scalar(owner_connection, "SELECT OBJECT_ID(@P1)", [raw_guard])


@case("BATCH-017")
@pytest.mark.asyncio
async def test_bulk_row_width_mismatch(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_bulk_width")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT)"
    )
    with pytest.raises(ValueError, match="Row has 1 values but 2 columns specified"):
        await owner_connection.bulk_insert(raw_table, ["id", "value"], [[1], [2, 20]])
    with pytest.raises(ValueError, match="Row has 3 values but 2 columns specified"):
        await owner_connection.bulk_insert(raw_table, ["id", "value"], [[1, 10, 100]])
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0


@case("BATCH-018")
@pytest.mark.asyncio
async def test_bulk_constraint_failure_rolls_back_every_chunk(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_bulk_atomic")
    table = quote_identifier(raw_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            id INT PRIMARY KEY,
            value INT NOT NULL CHECK (value >= 0)
        )
        """
    )
    rows = [[index, index] for index in range(1000)]
    rows.append([1000, -1])
    with pytest.raises(SqlError):
        await owner_connection.bulk_insert(raw_table, ["id", "value"], rows)
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0


@case("BATCH-019")
@pytest.mark.asyncio
async def test_bulk_identity_default_computed_and_trigger_interactions(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_bulk_generated")
    raw_audit = unique_sql_name("strict_bulk_audit")
    raw_trigger = unique_sql_name("strict_bulk_trigger")
    table = quote_identifier(raw_table)
    audit = quote_identifier(raw_audit)
    trigger = quote_identifier(raw_trigger)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {audit}")
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    cleanup_registry.add(f"DROP TRIGGER IF EXISTS {trigger}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            id INT IDENTITY PRIMARY KEY,
            payload NVARCHAR(30) NOT NULL,
            status INT NOT NULL DEFAULT 7,
            payload_length AS LEN(payload)
        )
        """
    )
    await owner_connection.execute(f"CREATE TABLE {audit} (inserted_id INT NOT NULL)")
    await owner_connection.simple_query(
        f"""
        CREATE TRIGGER {trigger}
        ON {table}
        AFTER INSERT
        AS
        BEGIN
            SET NOCOUNT ON;
            INSERT INTO {audit} (inserted_id) SELECT id FROM inserted;
        END
        """
    )
    assert (
        await owner_connection.bulk_insert(
            raw_table, ["payload"], [["alpha"], ["beta"]]
        )
        == 2
    )
    rows = await owner_connection.query(
        f"""
        SELECT id, payload, status, payload_length
        FROM {table}
        ORDER BY id
        """
    )
    assert [row.to_dict() for row in rows.rows()] == [
        {
            "id": 1,
            "payload": "alpha",
            "status": 7,
            "payload_length": 5,
        },
        {
            "id": 2,
            "payload": "beta",
            "status": 7,
            "payload_length": 4,
        },
    ]
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {audit}") == 2


@case("BATCH-020")
@pytest.mark.asyncio
async def test_batch_and_bulk_cancellation_cleanup(
    owner_connection: Connection,
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_batch_table = unique_sql_name("strict_cancel_batch")
    batch_table = quote_identifier(raw_batch_table)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {batch_table}")
    await owner_connection.execute(f"CREATE TABLE {batch_table} (id INT PRIMARY KEY)")
    batch_token = unique_sql_name("strict_batch_wait")
    batch_task = asyncio.ensure_future(
        owner_connection.execute_batch(
            [
                (f"INSERT INTO {batch_table} VALUES (1)", None),
                (
                    f"WAITFOR DELAY '00:00:05'; SELECT 1; -- {batch_token}",
                    None,
                ),
                (f"INSERT INTO {batch_table} VALUES (2)", None),
            ]
        )
    )
    await _wait_for_request(sa_connection, batch_token, present=True)
    batch_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await batch_task
    await _wait_for_request(sa_connection, batch_token, present=False)
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {batch_table}") == 0

    raw_bulk_table = unique_sql_name("strict_cancel_bulk")
    raw_trigger = unique_sql_name("strict_cancel_bulk_trigger")
    bulk_table = quote_identifier(raw_bulk_table)
    trigger = quote_identifier(raw_trigger)
    cleanup_registry.add(f"DROP TABLE IF EXISTS {bulk_table}")
    cleanup_registry.add(f"DROP TRIGGER IF EXISTS {trigger}")
    await owner_connection.execute(f"CREATE TABLE {bulk_table} (id INT PRIMARY KEY)")
    await owner_connection.simple_query(
        f"""
        CREATE TRIGGER {trigger}
        ON {bulk_table}
        AFTER INSERT
        AS
        BEGIN
            WAITFOR DELAY '00:00:05';
        END
        """
    )

    bulk_connection = _isolated_connection(sql_auth_config)
    try:
        await bulk_connection.connect()
        bulk_task = asyncio.ensure_future(
            bulk_connection.bulk_insert(raw_bulk_table, ["id"], [[1]])
        )
        await _wait_for_request(sa_connection, raw_bulk_table, present=True)
        bulk_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bulk_task
        assert (
            await asyncio.wait_for(scalar(bulk_connection, "SELECT 1"), timeout=3.0)
            == 1
        )
        assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {bulk_table}") == 0
    finally:
        await bulk_connection.disconnect()


@case("BULK-001")
@pytest.mark.asyncio
async def test_bulk_late_conversion_failure_rolls_back_sent_chunk(
    owner_connection: Connection,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_bulk_late_conversion")
    raw_trigger = unique_sql_name("strict_bulk_late_conversion_trigger")
    table = quote_identifier(raw_table)
    trigger = quote_identifier(raw_trigger)
    cleanup_registry.add(f"DROP TRIGGER IF EXISTS {trigger}")
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    await owner_connection.simple_query(
        f"""
        CREATE TRIGGER {trigger}
        ON {table}
        AFTER INSERT
        AS
        BEGIN
            SET NOCOUNT ON;
            IF EXISTS (SELECT 1 FROM inserted WHERE id = 0)
                WAITFOR DELAY '00:00:02';
        END
        """
    )
    rows: list[list[object]] = [[index, index] for index in range(1000)]
    rows.append([1000, object()])

    awaitable = owner_connection.bulk_insert(
        raw_table,
        ["id", "value"],
        rows,
    )
    task = asyncio.ensure_future(awaitable)
    try:
        await _wait_for_request(
            sa_connection,
            raw_table,
            present=True,
            timeout=5.0,
        )
        with pytest.raises(ValueError, match="Unsupported type"):
            await task
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif not task.cancelled():
            task.exception()

    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0


@case("BULK-002")
@pytest.mark.asyncio
async def test_empty_bulk_has_no_pool_or_metric_activity() -> None:
    connection = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="bulk_probe",
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

    assert await connection.bulk_insert("dbo.target", ["value"], []) == 0
    assert await connection.is_connected() is False
    snapshot = await connection.operation_stats()
    assert snapshot["operations"]["bulk_insert"]["started"] == 0
    assert snapshot["operations"]["bulk_insert"]["completed"] == 0
