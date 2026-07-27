from __future__ import annotations

from fastmssql import (
    Connection,
    ConversionError,
    OperationMetricsConfig,
    Parameter,
    PoolConfig,
    SslConfig,
    TimeoutConfig,
)
import pytest


TABLE_SENTINEL = "MustNotLeakTable"
COLUMN_SENTINEL = "MustNotLeakColumn"
VALUE_SENTINEL = "MustNotLeakValue"


def _offline_connection() -> Connection:
    """Build a connection which conversion preflight must never initialize."""

    return Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="bulk_descriptor_probe",
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


def _assert_safe_context(
    error: ConversionError,
    *,
    row_index: int,
    column_index: int,
    parameter_index: int,
    sql_type: str,
    reason: str,
) -> None:
    assert error.row_index == row_index
    assert error.column_index == column_index
    assert error.parameter_index == parameter_index
    assert error.sql_type == sql_type
    assert error.reason == reason
    assert error.retryable is False
    assert error.wire_sent is False
    assert error.connection_discarded is False
    assert error.outcome_unknown is False

    rendered = f"{error!s}\n{error!r}\n{vars(error)!r}"
    for sentinel in (TABLE_SENTINEL, COLUMN_SENTINEL, VALUE_SENTINEL):
        assert sentinel not in rendered


@pytest.mark.asyncio
async def test_bulk_descriptor_uses_typed_conversion_with_safe_context() -> None:
    """A typed bulk cell must retain ConversionError and global positions."""

    connection = _offline_connection()

    with pytest.raises(ConversionError) as captured:
        await connection.bulk_insert(
            f"dbo.{TABLE_SENTINEL}",
            [COLUMN_SENTINEL],
            [[Parameter(VALUE_SENTINEL, "INT")]],
        )

    _assert_safe_context(
        captured.value,
        row_index=0,
        column_index=0,
        parameter_index=0,
        sql_type="INT",
        reason="wrong_value_kind",
    )
    assert await connection.is_connected() is False


@pytest.mark.asyncio
async def test_bulk_rejects_expanded_descriptor_as_one_cell() -> None:
    """One bulk cell cannot expand into multiple SQL parameters."""

    connection = _offline_connection()
    rows = [
        [0, 0],
        [1, Parameter([2, 3], "INT")],
    ]

    with pytest.raises(ConversionError) as captured:
        await connection.bulk_insert(
            f"dbo.{TABLE_SENTINEL}",
            ["id", COLUMN_SENTINEL],
            rows,
        )

    _assert_safe_context(
        captured.value,
        row_index=1,
        column_index=1,
        parameter_index=3,
        sql_type="INT",
        reason="expanded_not_supported",
    )
    assert await connection.is_connected() is False


@pytest.mark.parametrize("direction", ["OUTPUT", "INPUT_OUTPUT", "RETURN_VALUE"])
@pytest.mark.asyncio
async def test_bulk_rejects_non_input_descriptor(direction: str) -> None:
    """Bulk rows are input-only even when a descriptor direction is valid RPC."""

    connection = _offline_connection()

    with pytest.raises(ConversionError) as captured:
        await connection.bulk_insert(
            f"dbo.{TABLE_SENTINEL}",
            [COLUMN_SENTINEL],
            [[Parameter(7, "INT", direction=direction)]],
        )

    _assert_safe_context(
        captured.value,
        row_index=0,
        column_index=0,
        parameter_index=0,
        sql_type="INT",
        reason="unsupported_direction",
    )
    assert await connection.is_connected() is False
