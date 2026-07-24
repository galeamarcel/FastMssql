from __future__ import annotations

import tracemalloc

from fastmssql import Connection, FastRow, QueryStream
import pytest

from sql_auth_strict.cases import case


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


async def _ordered_result(
    connection: Connection, count: int = 5
) -> QueryStream:
    values = ", ".join(f"({value})" for value in range(1, count + 1))
    return await connection.query(
        f"""
        SELECT value, CONCAT(N'row-', value) AS label
        FROM (VALUES {values}) AS source(value)
        ORDER BY value
        """
    )


@case("RESULT-001")
@pytest.mark.asyncio
async def test_row_access_by_name_and_index(
    owner_connection: Connection,
) -> None:
    row = (await _ordered_result(owner_connection, 1)).fetchone()
    assert isinstance(row, FastRow)
    assert row["value"] == 1
    assert row["label"] == "row-1"
    assert row[0] == 1
    assert row[1] == "row-1"
    assert row.get("value") == 1
    assert row.get_by_index(1) == "row-1"


@case("RESULT-002")
@pytest.mark.asyncio
async def test_row_negative_and_out_of_range_indices(
    owner_connection: Connection,
) -> None:
    row = (await _ordered_result(owner_connection, 1)).fetchone()
    assert row[-1] == "row-1"
    assert row[-2] == 1
    for index in (-3, 2, 100):
        with pytest.raises(IndexError, match="Column index out of range"):
            row[index]


@case("RESULT-003")
@pytest.mark.asyncio
async def test_missing_column_and_invalid_key_behavior(
    owner_connection: Connection,
) -> None:
    row = (await _ordered_result(owner_connection, 1)).fetchone()
    with pytest.raises(ValueError, match="Column 'missing' not found"):
        row["missing"]
    with pytest.raises(ValueError, match="Column 'missing' not found"):
        row.get("missing")
    with pytest.raises(ValueError, match="Key must be string or integer"):
        row[object()]


@case("RESULT-004")
@pytest.mark.asyncio
async def test_row_columns_values_dict_and_length(
    owner_connection: Connection,
) -> None:
    row = (await _ordered_result(owner_connection, 1)).fetchone()
    assert row.columns() == ["value", "label"]
    assert row.values() == [1, "row-1"]
    assert row.to_dict() == {"value": 1, "label": "row-1"}
    assert len(row) == 2


@case("RESULT-005")
@pytest.mark.asyncio
async def test_row_repr_and_str_are_safe_and_stable(
    owner_connection: Connection,
) -> None:
    row = (await _ordered_result(owner_connection, 1)).fetchone()
    assert str(row) == "FastRow with 2 columns"
    assert repr(row) == "FastRow(columns=[\"value\", \"label\"])"
    assert "row-1" not in repr(row)


@case("RESULT-006")
@pytest.mark.asyncio
async def test_stream_length_presence_emptiness_and_columns(
    owner_connection: Connection,
) -> None:
    result = await _ordered_result(owner_connection, 3)
    assert isinstance(result, QueryStream)
    assert len(result) == 3
    assert result.len() == 3
    assert result.has_rows() is True
    assert result.is_empty() is False
    assert result.columns() == ["value", "label"]
    assert result.position() == 0


@case("RESULT-007")
@pytest.mark.asyncio
async def test_stream_iteration_order_and_exhaustion(
    owner_connection: Connection,
) -> None:
    result = await _ordered_result(owner_connection, 4)
    iterator = iter(result)
    assert iterator is result
    assert [row["value"] for row in iterator] == [1, 2, 3, 4]
    assert result.position() == 4
    assert list(result) == []
    with pytest.raises(StopIteration):
        next(result)


@case("RESULT-008")
@pytest.mark.asyncio
async def test_stream_indexing_negative_index_and_cache(
    owner_connection: Connection,
) -> None:
    result = await _ordered_result(owner_connection, 4)
    first_access = result[1]
    second_access = result[1]
    assert first_access.to_dict() == {"value": 2, "label": "row-2"}
    assert second_access.to_dict() == first_access.to_dict()
    assert first_access is not second_access
    assert result[-1]["value"] == 4
    assert result.position() == 0
    for index in (-5, 4, 99):
        with pytest.raises(IndexError, match="Index out of range"):
            result[index]
    with pytest.raises(ValueError, match="Index must be an integer or slice"):
        result["1"]


@case("RESULT-009")
@pytest.mark.asyncio
async def test_stream_slices_and_invalid_steps(
    owner_connection: Connection,
) -> None:
    result = await _ordered_result(owner_connection, 5)
    assert [row["value"] for row in result[1:4]] == [2, 3, 4]
    assert [row["value"] for row in result[-3:-1]] == [3, 4]
    assert result[2:2] == []
    assert result.position() == 0
    for invalid_slice in (slice(None, None, 2), slice(None, None, -1)):
        with pytest.raises(ValueError, match="Slice step must be 1"):
            result[invalid_slice]


@case("RESULT-010")
@pytest.mark.asyncio
async def test_fetch_methods_and_aliases(owner_connection: Connection) -> None:
    result = await _ordered_result(owner_connection, 5)
    assert result.fetchone()["value"] == 1
    assert [row["value"] for row in result.fetchmany(2)] == [2, 3]
    assert [row["value"] for row in result.fetchall()] == [4, 5]
    assert result.fetchone() is None

    alias_result = await _ordered_result(owner_connection, 5)
    assert [row["value"] for row in alias_result.fetch(2)] == [1, 2]
    assert [row["value"] for row in alias_result.all()] == [3, 4, 5]
    assert alias_result.position() == 5

    rows_alias = await _ordered_result(owner_connection, 3)
    assert [row["value"] for row in rows_alias.rows()] == [1, 2, 3]
    assert rows_alias.position() == 3


@case("RESULT-011")
@pytest.mark.asyncio
async def test_mixed_fetch_methods_share_one_position(
    owner_connection: Connection,
) -> None:
    result = await _ordered_result(owner_connection, 6)
    assert result.position() == 0
    assert result.fetchone()["value"] == 1
    assert result.position() == 1
    assert [row["value"] for row in result.fetchmany(2)] == [2, 3]
    assert result.position() == 3
    assert next(result)["value"] == 4
    assert result.position() == 4
    assert [row["value"] for row in result.fetchall()] == [5, 6]
    assert result.position() == 6
    assert result.fetchmany(2) == []


@case("RESULT-012")
@pytest.mark.asyncio
async def test_reset_restores_stream_position(
    owner_connection: Connection,
) -> None:
    result = await _ordered_result(owner_connection, 3)
    first = result.fetchone()
    assert first["value"] == 1
    assert result.fetchone()["value"] == 2
    assert result.position() == 2
    result.reset()
    assert result.position() == 0
    replayed = result.fetchone()
    assert replayed["value"] == 1
    assert replayed.to_dict() == first.to_dict()
    assert [row["value"] for row in result.fetchall()] == [2, 3]


@case("RESULT-013")
@pytest.mark.asyncio
async def test_empty_result_methods(owner_connection: Connection) -> None:
    result = await owner_connection.query(
        "SELECT CAST(1 AS INT) AS value WHERE 1 = 0"
    )
    assert len(result) == 0
    assert result.len() == 0
    assert result.has_rows() is False
    assert result.is_empty() is True
    assert result.position() == 0
    assert result.fetchone() is None
    assert result.fetchmany(3) == []
    assert result.fetchall() == []
    assert result.all() == []
    assert result.rows() == []
    assert list(result) == []
    with pytest.raises(ValueError, match="No column information available"):
        result.columns()
    with pytest.raises(IndexError, match="Index out of range"):
        result[0]
    assert result[:] == []


@case("RESULT-014")
@pytest.mark.asyncio
async def test_stream_is_sync_not_async_iterable(
    owner_connection: Connection,
) -> None:
    result = await _ordered_result(owner_connection, 3)
    assert iter(result) is result
    assert hasattr(result, "__iter__")
    assert hasattr(result, "__next__")
    assert not hasattr(result, "__aiter__")
    assert not hasattr(result, "__anext__")
    assert [row["value"] for row in result] == [1, 2, 3]


@case("RESULT-015")
@pytest.mark.asyncio
async def test_lazy_conversion_and_python_memory_growth_are_measured(
    owner_connection: Connection,
) -> None:
    lazy_result = await owner_connection.query(
        "SELECT hierarchyid::Parse('/1/') AS unsupported_value"
    )
    assert isinstance(lazy_result, QueryStream)
    assert lazy_result.len() == 1
    with pytest.raises(ValueError, match="Failed to convert column"):
        lazy_result.fetchone()

    tracemalloc.start()
    try:
        result = await owner_connection.query(
            """
            WITH numbers AS (
                SELECT 1 AS value
                UNION ALL
                SELECT value + 1 FROM numbers WHERE value < 128
            )
            SELECT
                REPLICATE(CAST(N'x' AS NVARCHAR(MAX)), 10000)
                    + RIGHT(N'000' + CAST(value AS NVARCHAR(3)), 3)
                    AS payload
            FROM numbers
            ORDER BY value
            OPTION (MAXRECURSION 0)
            """
        )
        raw_current, _ = tracemalloc.get_traced_memory()
        assert result.len() == 128
        assert result.position() == 0

        first = result[0]
        one_current, _ = tracemalloc.get_traced_memory()
        assert len(first["payload"]) == 10003

        rows = result.rows()
        all_current, peak = tracemalloc.get_traced_memory()
        assert len(rows) == 128
        assert all_current > raw_current
        assert all_current - one_current > 600_000
        assert peak >= all_current
    finally:
        tracemalloc.stop()
