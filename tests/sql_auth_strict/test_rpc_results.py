from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import re
from uuid import UUID

from fastmssql import (
    Connection,
    Parameter,
    Parameters,
    PoolConfig,
    SqlError,
    SslConfig,
    Transaction,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, scalar


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]

PROCEDURE_COMPONENT = re.compile(
    r"^[A-Za-z_#][A-Za-z0-9_@$#]{0,127}$"
)


def _quote_component(value: str) -> str:
    if not PROCEDURE_COMPONENT.fullmatch(value):
        raise ValueError("unsafe test procedure identifier")
    return f"[{value}]"


async def _create_procedure(
    connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
    prefix: str,
    definition: str,
    *,
    raw_name: str | None = None,
) -> str:
    procedure = raw_name or unique_sql_name(prefix)
    quoted = _quote_component(procedure)
    cleanup_registry.add(f"DROP PROCEDURE IF EXISTS {quoted}")
    await connection.simple_query(
        f"""
        CREATE PROCEDURE {quoted}
        {definition}
        """
    )
    return procedure


def _require_callproc(owner: Connection | Transaction):
    method = getattr(owner, "callproc", None)
    assert callable(method), (
        f"{type(owner).__name__}.callproc() direct RPC API is missing"
    )
    return method


async def _callproc(
    owner: Connection | Transaction,
    procedure: str,
    params: object | None = None,
    *,
    buffer_size: int = 64,
):
    method = _require_callproc(owner)
    return await method(procedure, params, buffer_size=buffer_size)


async def _drain(response) -> tuple[
    list[tuple[int, tuple[str, ...], list[dict[str, object]]]],
    object,
]:
    assert response.complete is False
    with pytest.raises(
        RuntimeError,
        match="summary is unavailable before normal completion",
    ):
        _ = response.summary

    result_sets: list[
        tuple[int, tuple[str, ...], list[dict[str, object]]]
    ] = []
    async for result_set in response:
        rows = [row.to_dict() async for row in result_set]
        result_sets.append(
            (result_set.index, result_set.column_names, rows)
        )

    assert response.complete is True
    assert response.closed is True
    return result_sets, response.summary


def _single_pool_connection(
    config: SqlAuthConfig,
    *,
    application_name: str,
    max_size: int = 1,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=max_size,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=5,
            test_on_check_out=False,
            retry_connection=False,
        ),
        application_name=application_name,
    )


async def _connection_identity(
    connection: Connection,
) -> tuple[int, str]:
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


async def _wait_for_active(
    connection: Connection,
    expected: int,
    *,
    timeout: float = 5.0,
) -> dict[str, object]:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        stats = await connection.pool_stats()
        if stats["active_connections"] == expected:
            return stats
        await asyncio.sleep(0.01)
    observed = await connection.pool_stats()
    raise AssertionError(
        f"expected {expected} active connection(s), observed {observed}"
    )


def _exact_identifier(
    unique_sql_name: Callable[[str], str],
    prefix: str,
    length: int,
) -> str:
    base = unique_sql_name(prefix)
    assert len(base) <= length
    value = base + ("x" * (length - len(base)))
    assert len(value) == length
    assert PROCEDURE_COMPONENT.fullmatch(value)
    return value


def test_rpc_fixture_identifier_quoting_uses_closed_grammar() -> None:
    assert _quote_component("p") == "[p]"
    assert _quote_component("#" + ("x" * 127)) == (
        "[#" + ("x" * 127) + "]"
    )
    for invalid in (
        "",
        "@procedure",
        "1procedure",
        "procedure.name",
        "[procedure]",
        "procedure;SELECT 1",
        "prócédure",
        "p" * 129,
    ):
        with pytest.raises(ValueError, match="unsafe test procedure"):
            _quote_component(invalid)


@case("RPC-001")
@pytest.mark.asyncio
async def test_direct_no_parameter_rpc_preserves_signed_return_status(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "rpc_001_status",
        """
        AS
        BEGIN
            SET NOCOUNT ON;
            RETURN -7;
        END
        """,
    )

    response = await _callproc(owner_connection, procedure, buffer_size=1)
    result_sets, summary = await _drain(response)

    assert result_sets == []
    assert summary.result_set_count == 0
    assert summary.return_status == -7
    assert summary.output_parameters == {}


@case("RPC-002")
@pytest.mark.asyncio
async def test_integer_input_output_and_input_output_round_trip(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "rpc_002_integer",
        """
            @input_value INT,
            @output_value INT OUTPUT,
            @input_output_value INT OUTPUT
        AS
        BEGIN
            SET NOCOUNT ON;
            SET @output_value = @input_value * 2;
            SET @input_output_value = @input_output_value + @input_value;
            RETURN 17;
        END
        """,
    )
    params = Parameters()
    params.add(7, "INT")
    params.add(None, "INT", direction="OUTPUT")
    params.add(5, "INT", direction="INPUT_OUTPUT")

    response = await _callproc(owner_connection, procedure, params)
    result_sets, summary = await _drain(response)

    assert result_sets == []
    assert summary.return_status == 17
    first_snapshot = summary.output_parameters
    second_snapshot = summary.output_parameters
    assert first_snapshot == {1: 14, 2: 12}
    assert second_snapshot == first_snapshot
    assert second_snapshot is not first_snapshot
    first_snapshot[1] = -1
    assert summary.output_parameters == {1: 14, 2: 12}


@case("RPC-003")
@pytest.mark.asyncio
async def test_scalar_output_types_preserve_exact_python_values(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "rpc_003_types",
        """
            @unicode_value NVARCHAR(30) OUTPUT,
            @binary_value VARBINARY(8) OUTPUT,
            @decimal_value DECIMAL(19, 4) OUTPUT,
            @uuid_value UNIQUEIDENTIFIER OUTPUT,
            @date_value DATE OUTPUT,
            @time_value TIME(6) OUTPUT,
            @datetime2_value DATETIME2(3) OUTPUT,
            @offset_value DATETIMEOFFSET(6) OUTPUT
        AS
        BEGIN
            SET NOCOUNT ON;
            SET @unicode_value = N'București 🚀';
            SET @binary_value = 0x00FF1020;
            SET @decimal_value = CAST(
                -1234567890.1200 AS DECIMAL(19, 4)
            );
            SET @uuid_value = CONVERT(
                UNIQUEIDENTIFIER,
                '00112233-4455-6677-8899-aabbccddeeff'
            );
            SET @date_value = DATEFROMPARTS(2024, 2, 29);
            SET @time_value = CAST('12:34:56.123456' AS TIME(6));
            SET @datetime2_value = CAST(
                '2024-02-29T23:59:58.123' AS DATETIME2(3)
            );
            SET @offset_value = CAST(
                '2024-02-29T23:59:58.123456+05:30'
                AS DATETIMEOFFSET(6)
            );
            RETURN 3;
        END
        """,
    )
    params = Parameters(
        **{
            "@unicode_value": Parameter(
                None,
                "NVARCHAR(30)",
                direction="OUTPUT",
            ),
            "binary_value": Parameter(
                None,
                "VARBINARY(8)",
                direction="OUTPUT",
            ),
            "decimal_value": Parameter(
                None,
                "DECIMAL(19,4)",
                direction="OUTPUT",
            ),
            "uuid_value": Parameter(
                None,
                "UNIQUEIDENTIFIER",
                direction="OUTPUT",
            ),
            "date_value": Parameter(
                None,
                "DATE",
                direction="OUTPUT",
            ),
            "time_value": Parameter(
                None,
                "TIME(6)",
                direction="OUTPUT",
            ),
            "datetime2_value": Parameter(
                None,
                "DATETIME2(3)",
                direction="OUTPUT",
            ),
            "offset_value": Parameter(
                None,
                "DATETIMEOFFSET(6)",
                direction="OUTPUT",
            ),
        }
    )

    response = await _callproc(owner_connection, procedure, params)
    result_sets, summary = await _drain(response)

    assert result_sets == []
    assert summary.return_status == 3
    assert summary.output_parameters == {
        "unicode_value": "București 🚀",
        "binary_value": b"\x00\xff\x10\x20",
        "decimal_value": Decimal("-1234567890.1200"),
        "uuid_value": UUID("00112233-4455-6677-8899-aabbccddeeff"),
        "date_value": date(2024, 2, 29),
        "time_value": time(12, 34, 56, 123456),
        "datetime2_value": datetime(2024, 2, 29, 23, 59, 58, 123000),
        "offset_value": datetime(
            2024,
            2,
            29,
            23,
            59,
            58,
            123456,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        ),
    }


@case("RPC-004")
@pytest.mark.asyncio
async def test_result_sets_finish_before_terminal_output_summary(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "rpc_004_sets",
        """
            @answer INT OUTPUT
        AS
        BEGIN
            SET NOCOUNT ON;
            SELECT CAST(1 AS INT) AS first_value;
            SELECT CAST(NULL AS INT) AS empty_value WHERE 1 = 0;
            SELECT CAST(3 AS INT) AS third_value;
            SET @answer = 44;
            RETURN 4;
        END
        """,
    )
    params = Parameters()
    params.set("answer", None, "INT", direction="OUTPUT")

    response = await _callproc(
        owner_connection,
        procedure,
        params,
        buffer_size=1,
    )
    result_sets, summary = await _drain(response)

    assert result_sets == [
        (0, ("first_value",), [{"first_value": 1}]),
        (1, ("empty_value",), []),
        (2, ("third_value",), [{"third_value": 3}]),
    ]
    assert summary.result_set_count == 3
    assert summary.return_status == 4
    assert summary.output_parameters == {"answer": 44}


@case("RPC-005")
@pytest.mark.asyncio
async def test_reordered_max_outputs_match_original_names_and_ordinals(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "rpc_005_reordered",
        """
            @large_text NVARCHAR(MAX) OUTPUT,
            @small_value INT OUTPUT,
            @large_binary VARBINARY(MAX) OUTPUT
        AS
        BEGIN
            SET NOCOUNT ON;
            SET @large_text = REPLICATE(
                CAST(N'λ' AS NVARCHAR(MAX)),
                5000
            );
            SET @small_value = 505;
            SET @large_binary = CONVERT(
                VARBINARY(MAX),
                REPLICATE(CAST('x' AS VARCHAR(MAX)), 9000)
            );
            RETURN 5;
        END
        """,
    )
    expected_named = {
        "large_text": "λ" * 5000,
        "small_value": 505,
        "large_binary": b"x" * 9000,
    }
    named_params = Parameters(
        **{
            "large_text": Parameter(
                None,
                "NVARCHAR(MAX)",
                direction="OUTPUT",
            ),
            "small_value": Parameter(
                None,
                "INT",
                direction="OUTPUT",
            ),
            "large_binary": Parameter(
                None,
                "VARBINARY(MAX)",
                direction="OUTPUT",
            ),
        }
    )

    named_sets, named_summary = await _drain(
        await _callproc(owner_connection, procedure, named_params)
    )
    assert named_sets == []
    assert named_summary.return_status == 5
    assert named_summary.output_parameters == expected_named

    positional_params = Parameters(
        Parameter(None, "NVARCHAR(MAX)", direction="OUTPUT"),
        Parameter(None, "INT", direction="OUTPUT"),
        Parameter(None, "VARBINARY(MAX)", direction="OUTPUT"),
    )
    positional_sets, positional_summary = await _drain(
        await _callproc(owner_connection, procedure, positional_params)
    )
    assert positional_sets == []
    assert positional_summary.return_status == 5
    assert positional_summary.output_parameters == {
        0: expected_named["large_text"],
        1: expected_named["small_value"],
        2: expected_named["large_binary"],
    }


@case("RPC-006")
@pytest.mark.asyncio
async def test_zero_return_status_is_distinct_from_absent_status(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "rpc_006_zero",
        """
        AS
        BEGIN
            SET NOCOUNT ON;
            RETURN 0;
        END
        """,
    )
    params = Parameters(
        **{
            "status": Parameter(
                None,
                direction="RETURN_VALUE",
            )
        }
    )

    _, rpc_summary = await _drain(
        await _callproc(owner_connection, procedure, params)
    )
    _, batch_summary = await _drain(
        await owner_connection.batch(
            "SELECT CAST(6 AS INT) AS value",
            buffer_size=1,
        )
    )

    assert rpc_summary.return_status == 0
    assert rpc_summary.output_parameters == {"status": 0}
    assert batch_summary.return_status is None
    assert batch_summary.output_parameters == {}


@case("RPC-007")
@pytest.mark.asyncio
async def test_rpc_error_cancel_and_early_close_have_exact_disposition(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    error_procedure = await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "rpc_007_error",
        """
        AS
        BEGIN
            SET NOCOUNT ON;
            RAISERROR(N'expected direct RPC failure', 16, 1);
        END
        """,
    )
    wait_procedure = await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "rpc_007_wait",
        """
        AS
        BEGIN
            SET NOCOUNT ON;
            WAITFOR DELAY '00:00:00.500';
            SELECT @@SPID AS session_id, CAST(7 AS INT) AS value;
            RETURN 7;
        END
        """,
    )
    connection = _single_pool_connection(
        sql_auth_config,
        application_name=unique_sql_name("rpc_007_app"),
    )
    try:
        await connection.connect()
        original = await _connection_identity(connection)
        before_error = await connection.pool_stats()

        error_response = await _callproc(
            connection,
            error_procedure,
            buffer_size=1,
        )
        with pytest.raises(SqlError) as captured:
            await error_response.finish()
        assert captured.value.code == 50000
        assert captured.value.severity == 16
        assert captured.value.connection_discarded is False
        await _wait_for_active(connection, 0)
        after_error = await connection.pool_stats()
        assert (
            after_error["connections_closed_broken"]
            == before_error["connections_closed_broken"]
        )
        assert await _connection_identity(connection) == original

        cancel_response = await _callproc(
            connection,
            wait_procedure,
            buffer_size=1,
        )
        pending = asyncio.ensure_future(cancel_response.__anext__())
        await asyncio.sleep(0.05)
        assert pending.done() is False
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        result_set = await cancel_response.__anext__()
        rows = [row.to_dict() async for row in result_set]
        assert rows == [{"session_id": original[0], "value": 7}]
        assert (await cancel_response.finish()).return_status == 7
        assert await _connection_identity(connection) == original

        before_close = await connection.pool_stats()
        close_response = await _callproc(
            connection,
            wait_procedure,
            buffer_size=1,
        )
        await _wait_for_active(connection, 1)
        await close_response.aclose()
        assert close_response.complete is False
        await _wait_for_active(connection, 0)
        replacement = await _connection_identity(connection)
        after_close = await connection.pool_stats()
        assert replacement[1] != original[1]
        assert (
            after_close["connections_closed_broken"]
            == before_close["connections_closed_broken"] + 1
        )
        assert await scalar(connection, "SELECT 7") == 7
    finally:
        await connection.disconnect()


@case("RPC-008")
@pytest.mark.asyncio
async def test_rpc_modes_directions_and_identifier_grammars_are_local(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = _exact_identifier(
        unique_sql_name,
        "rpc_008_procedure",
        128,
    )
    long_parameter = _exact_identifier(
        unique_sql_name,
        "rpc_008_parameter",
        127,
    )
    special_parameter = "p@x$y#z"
    await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "unused",
        f"""
            @p INT OUTPUT,
            @{long_parameter} INT OUTPUT,
            @{special_parameter} INT OUTPUT
        AS
        BEGIN
            SET NOCOUNT ON;
            SET @p = 1;
            SET @{long_parameter} = 127;
            SET @{special_parameter} = 8;
            RETURN 8;
        END
        """,
        raw_name=procedure,
    )
    valid_params = Parameters(
        **{
            "@p": Parameter(None, "INT", direction="OUTPUT"),
            long_parameter: Parameter(None, "INT", direction="OUTPUT"),
            f"@{special_parameter}": Parameter(
                None,
                "INT",
                direction="OUTPUT",
            ),
        }
    )
    database_name = str(await scalar(owner_connection, "SELECT DB_NAME()"))
    assert PROCEDURE_COMPONENT.fullmatch(database_name)

    for qualified_name in (
        procedure,
        f"dbo.{procedure}",
        f"{database_name}.dbo.{procedure}",
    ):
        _, summary = await _drain(
            await _callproc(
                owner_connection,
                qualified_name,
                valid_params,
            )
        )
        assert summary.return_status == 8
        assert summary.output_parameters == {
            "p": 1,
            long_parameter: 127,
            special_parameter: 8,
        }

    validation = _single_pool_connection(
        sql_auth_config,
        application_name=unique_sql_name("rpc_008_validation"),
    )
    await validation.connect(validate=False)
    try:
        before = await validation.pool_stats()
        secret = "RpcIdentifierSecret_MustNotLeak_2026"
        invalid_procedures = (
            "",
            "a.b.c.d",
            "dbo..procedure",
            "1procedure",
            "$procedure",
            "próc",
            "dbo.procedure name",
            "[dbo].[procedure]",
            f"dbo.procedure;SELECT '{secret}'",
            "dbo.procedure\n",
            "p" * 129,
        )
        for invalid in invalid_procedures:
            with pytest.raises(ValueError) as captured:
                await _callproc(validation, invalid)
            assert secret not in str(captured.value)

        invalid_parameter_names = (
            "",
            "@",
            "@@p",
            "1parameter",
            "$parameter",
            "parámetro",
            "parameter name",
            "[parameter]",
            f"parameter;{secret}",
            "p" * 128,
        )
        for invalid in invalid_parameter_names:
            params = Parameters(
                **{
                    invalid: Parameter(
                        None,
                        "INT",
                        direction="OUTPUT",
                    )
                }
            )
            with pytest.raises(ValueError) as captured:
                await _callproc(validation, procedure, params)
            assert secret not in str(captured.value)

        invalid_parameter_sets = (
            Parameters(
                Parameter(1, "INT"),
                named=Parameter(2, "INT"),
            ),
            Parameters(
                **{
                    "p": Parameter(None, "INT", direction="OUTPUT"),
                    "@p": Parameter(None, "INT", direction="OUTPUT"),
                }
            ),
            Parameters(
                Parameter(1, "INT", direction="OUTPUT"),
            ),
            Parameters(
                Parameter(None, direction="OUTPUT"),
            ),
            Parameters(
                Parameter(1, direction="INPUT_OUTPUT"),
            ),
            Parameters(
                Parameter(1, "INT", direction="RETURN_VALUE"),
            ),
            Parameters(
                Parameter(None, "BIGINT", direction="RETURN_VALUE"),
            ),
            Parameters(
                Parameter(None, "INT", direction="RETURN_VALUE"),
                Parameter(None, "INT", direction="RETURN_VALUE"),
            ),
            Parameters(Parameter([1, 2], "INT")),
            Parameters(*range(2101)),
        )
        for params in invalid_parameter_sets:
            with pytest.raises(ValueError):
                await _callproc(validation, procedure, params)

        for invalid_buffer in (True, False, 0, 1_025, -1, 8.0, "8", None):
            with pytest.raises((TypeError, ValueError)):
                await _callproc(
                    validation,
                    procedure,
                    valid_params,
                    buffer_size=invalid_buffer,
                )

        after = await validation.pool_stats()
        for key in (
            "connections",
            "active_connections",
            "get_started",
            "connections_created",
        ):
            assert after[key] == before[key]
    finally:
        await validation.disconnect()


@case("RPC-009")
@pytest.mark.asyncio
async def test_connection_and_both_transaction_paths_have_rpc_parity(
    owner_connection: Connection,
    transaction_factory: Callable[..., Transaction],
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "rpc_009_parity",
        """
            @input_value INT,
            @output_value INT OUTPUT
        AS
        BEGIN
            SET NOCOUNT ON;
            SET @output_value = @input_value + 90;
            SELECT @input_value AS input_value;
            RETURN 9;
        END
        """,
    )

    async def exercise(
        owner: Connection | Transaction,
        *,
        plain_list: bool = False,
    ):
        descriptors = (
            Parameter(9, "INT"),
            Parameter(None, "INT", direction="OUTPUT"),
        )
        params = list(descriptors) if plain_list else Parameters(*descriptors)
        result_sets, summary = await _drain(
            await _callproc(owner, procedure, params, buffer_size=1)
        )
        return (
            result_sets,
            summary.return_status,
            summary.output_parameters,
        )

    expected = (
        [(0, ("input_value",), [{"input_value": 9}])],
        9,
        {1: 99},
    )
    assert await exercise(owner_connection, plain_list=True) == expected

    pooled = owner_connection.transaction()
    try:
        await pooled.begin()
        assert await exercise(pooled) == expected
        await pooled.rollback()
    finally:
        await pooled.close()

    direct = transaction_factory()
    try:
        await direct.begin()
        assert await exercise(direct) == expected
        await direct.rollback()
    finally:
        await direct.close()


@case("RPC-010")
@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_concurrent_rpc_outputs_remain_bounded_by_pool_sessions(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "rpc_010_concurrent",
        """
            @operation_id INT,
            @doubled INT OUTPUT
        AS
        BEGIN
            SET NOCOUNT ON;
            WAITFOR DELAY '00:00:00.030';
            SET @doubled = @operation_id * 2;
            SELECT
                @operation_id AS operation_id,
                @@SPID AS session_id;
            RETURN @operation_id;
        END
        """,
    )
    pool_size = 4
    concurrency = 16
    operations = 64
    connection = _single_pool_connection(
        sql_auth_config,
        application_name=unique_sql_name("rpc_010_app"),
        max_size=pool_size,
    )
    stop_sampler = asyncio.Event()
    peak_active = 0
    results: list[tuple[int, int, int, int] | None] = [None] * operations
    operation_ids = iter(range(operations))

    async def sample_pool() -> None:
        nonlocal peak_active
        while not stop_sampler.is_set():
            stats = await connection.pool_stats()
            peak_active = max(
                peak_active,
                int(stats["active_connections"]),
            )
            await asyncio.sleep(0.001)

    async def worker() -> None:
        for operation_id in operation_ids:
            params = Parameters(
                **{
                    "operation_id": Parameter(operation_id, "INT"),
                    "doubled": Parameter(
                        None,
                        "INT",
                        direction="OUTPUT",
                    ),
                }
            )
            result_sets, summary = await _drain(
                await _callproc(
                    connection,
                    procedure,
                    params,
                    buffer_size=1,
                )
            )
            assert len(result_sets) == 1
            row = result_sets[0][2][0]
            results[operation_id] = (
                int(row["operation_id"]),
                int(row["session_id"]),
                int(summary.return_status),
                int(summary.output_parameters["doubled"]),
            )

    await connection.connect()
    try:
        _require_callproc(connection)
        sampler = asyncio.create_task(sample_pool())
        try:
            async with asyncio.TaskGroup() as group:
                for _ in range(concurrency):
                    group.create_task(worker())
        finally:
            stop_sampler.set()
            await sampler

        assert all(result is not None for result in results)
        completed = [result for result in results if result is not None]
        assert [result[0] for result in completed] == list(range(operations))
        assert [result[2] for result in completed] == list(range(operations))
        assert [result[3] for result in completed] == [
            operation_id * 2 for operation_id in range(operations)
        ]
        session_ids = {result[1] for result in completed}
        assert 1 < len(session_ids) <= pool_size
        assert 1 < peak_active <= pool_size
        final = await _wait_for_active(connection, 0)
        assert final["connections"] <= pool_size
        assert await scalar(connection, "SELECT 10") == 10
    finally:
        await connection.disconnect()


@case("RPC-011")
@pytest.mark.asyncio
async def test_recycled_session_is_read_committed_before_named_rpc(
    sql_auth_config: SqlAuthConfig,
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = await _create_procedure(
        owner_connection,
        unique_sql_name,
        cleanup_registry,
        "rpc_011_isolation",
        """
            @reported INT OUTPUT
        AS
        BEGIN
            SET NOCOUNT ON;
            SELECT @reported = transaction_isolation_level
            FROM sys.dm_exec_sessions
            WHERE session_id = @@SPID;

            SELECT
                @@SPID AS session_id,
                CONVERT(NVARCHAR(36), connection_id) AS connection_id,
                @reported AS isolation_level
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID;
            RETURN 11;
        END
        """,
    )
    connection = _single_pool_connection(
        sql_auth_config,
        application_name=unique_sql_name("rpc_011_app"),
    )
    transaction = connection.transaction()
    try:
        await transaction.begin()
        original_session = int(await scalar(transaction, "SELECT @@SPID"))
        original_connection = str(
            await scalar(
                transaction,
                """
                SELECT CONVERT(NVARCHAR(36), connection_id)
                FROM sys.dm_exec_connections
                WHERE session_id = @@SPID
                """,
            )
        )
        await transaction.execute(
            "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
        )
        await transaction.commit()
        await transaction.close()

        params = Parameters(
            **{
                "@reported": Parameter(
                    None,
                    "INT",
                    direction="OUTPUT",
                )
            }
        )
        result_sets, summary = await _drain(
            await _callproc(
                connection,
                f"dbo.{procedure}",
                params,
                buffer_size=1,
            )
        )
        assert result_sets == [
            (
                0,
                ("session_id", "connection_id", "isolation_level"),
                [
                    {
                        "session_id": original_session,
                        "connection_id": original_connection,
                        "isolation_level": 2,
                    }
                ],
            )
        ]
        assert summary.return_status == 11
        assert summary.output_parameters == {"reported": 2}

        before_invalid = await connection.pool_stats()
        secret = "RpcProcedurePunctuationSecret_MustNotLeak_2026"
        with pytest.raises(ValueError) as captured:
            await _callproc(
                connection,
                f"dbo.{procedure};SELECT '{secret}'",
                params,
            )
        assert secret not in str(captured.value)
        after_invalid = await connection.pool_stats()
        assert (
            after_invalid["get_started"]
            == before_invalid["get_started"]
        )
        assert after_invalid["active_connections"] == 0
    finally:
        await transaction.close()
        await connection.disconnect()
