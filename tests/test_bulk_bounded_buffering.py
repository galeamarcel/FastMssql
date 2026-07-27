from __future__ import annotations

import asyncio

from fastmssql import (
    Connection,
    OperationMetricsConfig,
    PoolConfig,
    SslConfig,
    TimeoutConfig,
)
import pytest


class _IndexProbe:
    """Expose exactly when PyO3 attempts fallback integer conversion."""

    def __init__(self) -> None:
        self.conversions = 0

    def __index__(self) -> int:
        self.conversions += 1
        return 7


def _offline_connection() -> Connection:
    """Build a connection whose endpoint must never be reached by these tests."""

    return Connection(
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


@pytest.mark.asyncio
async def test_bulk_insert_does_not_convert_cells_during_method_creation() -> None:
    """Calling bulk_insert must be O(1) in row values until its awaitable runs."""

    connection = _offline_connection()
    probe = _IndexProbe()

    awaitable = connection.bulk_insert("dbo.target", ["value"], [[probe]])
    try:
        assert probe.conversions == 0
    finally:
        assert awaitable.cancel()
        with pytest.raises(asyncio.CancelledError):
            await awaitable


@pytest.mark.asyncio
async def test_empty_bulk_insert_is_zero_io_and_zero_metric() -> None:
    """An empty list must not initialize a pool or claim SQL work occurred."""

    connection = _offline_connection()

    assert await connection.bulk_insert("dbo.target", ["value"], []) == 0
    assert await connection.is_connected() is False
    snapshot = await connection.operation_stats()
    assert snapshot["operations"]["bulk_insert"]["started"] == 0
    assert snapshot["operations"]["bulk_insert"]["completed"] == 0


@pytest.mark.asyncio
async def test_bulk_insert_rejects_resized_input_before_pool_activity() -> None:
    """Deferred conversion must not silently consume a resized input list."""

    connection = _offline_connection()
    rows = [[1]]
    awaitable = connection.bulk_insert("dbo.target", ["value"], rows)
    rows.append([2])

    try:
        with pytest.raises(
            ValueError,
            match="bulk_insert data_rows must not be resized while the operation is running",
        ):
            await awaitable
        assert await connection.is_connected() is False
        snapshot = await connection.operation_stats()
        operation = snapshot["operations"]["bulk_insert"]
        assert operation["started"] == operation["completed"] == 1
        assert operation["errors"] == 1
    finally:
        await connection.disconnect()
