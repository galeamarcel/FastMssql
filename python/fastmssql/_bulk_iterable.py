"""Bounded sync/async producer coordination for native TDS bulk inserts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ._bounded_sequence import run_bounded_sequence


_PRODUCER_ERROR = (
    "rows must be an iterable or async iterable of row sequences"
)
_INVALID_PRODUCER_TYPES = (str, bytes, bytearray, memoryview, Mapping)
_INVALID_ROW_TYPES = (str, bytes, bytearray, memoryview, Mapping)


def _normalize_row(row: object) -> list[Any]:
    if isinstance(row, _INVALID_ROW_TYPES) or not isinstance(row, Sequence):
        raise TypeError("each native bulk row must be a non-string sequence")
    return list(row)


async def native_bulk_insert_iterable(
    raw_owner: object,
    table: str,
    columns: list[str],
    rows: object,
    *,
    chunk_size: int,
) -> int:
    """Upload one bounded chunk at a time from a sync or async producer."""
    if isinstance(rows, _INVALID_PRODUCER_TYPES):
        raise TypeError(_PRODUCER_ERROR)

    # Validate identifiers and chunk_size before acquiring or advancing caller
    # code. Construction itself performs no lifecycle, pool, SQL, or metric work.
    sequence = raw_owner._native_bulk_sequence(
        table,
        columns,
        chunk_size=chunk_size,
    )
    return await run_bounded_sequence(
        sequence,
        rows,
        chunk_size=chunk_size,
        normalize_item=_normalize_row,
        producer_error=_PRODUCER_ERROR,
        task_name_prefix="fastmssql-native-bulk",
    )
