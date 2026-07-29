"""Bounded sync/async producer coordination for native TDS bulk inserts."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any


_END_OF_PRODUCER = object()
_INVALID_PRODUCER_TYPES = (str, bytes, bytearray, memoryview, Mapping)
_INVALID_ROW_TYPES = (str, bytes, bytearray, memoryview, Mapping)


async def _run_cleanup(awaitable: Awaitable[Any]) -> Any:
    return await awaitable


async def _await_cleanup(awaitable: Awaitable[Any], *, name: str) -> Any:
    """Run mandatory cleanup to completion despite repeated caller cancellation."""
    task = asyncio.create_task(_run_cleanup(awaitable), name=name)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _close_producer(producer: object) -> None:
    close = getattr(producer, "aclose", None)
    if close is None:
        close = getattr(producer, "close", None)
    if close is None:
        return

    result = close()
    if inspect.isawaitable(result):
        await result


def _acquire_producer(rows: object) -> tuple[bool, object]:
    """Acquire exactly one preferred producer protocol without advancing it."""
    if getattr(type(rows), "__aiter__", None) is not None:
        return True, aiter(rows)
    if getattr(type(rows), "__iter__", None) is not None:
        return False, iter(rows)
    raise TypeError("rows must be an iterable or async iterable of row sequences")


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
        raise TypeError("rows must be an iterable or async iterable of row sequences")

    # Validate identifiers and chunk_size before acquiring or advancing caller
    # code. Construction itself performs no lifecycle, pool, SQL, or metric work.
    sequence = raw_owner._native_bulk_sequence(
        table,
        columns,
        chunk_size=chunk_size,
    )
    asynchronous, producer = _acquire_producer(rows)
    reserved = False
    activated = False
    expired = False

    async def expire() -> None:
        nonlocal expired
        expired = True
        await _await_cleanup(
            sequence.expire(),
            name="fastmssql-native-bulk-expire",
        )
        raise RuntimeError("Native bulk sequence expiry returned without an error")

    async def check_deadline() -> None:
        remaining = sequence.remaining_timeout()
        if remaining is not None and remaining <= 0:
            await expire()

    async def pull_one() -> object:
        if not asynchronous:
            await check_deadline()
            try:
                row = next(producer)
            except StopIteration:
                await check_deadline()
                return _END_OF_PRODUCER
            except BaseException:
                await check_deadline()
                raise
            await check_deadline()
            return row

        remaining = sequence.remaining_timeout()
        if remaining is not None and remaining <= 0:
            await expire()
        if remaining is None:
            try:
                row = await anext(producer)
            except StopAsyncIteration:
                await check_deadline()
                return _END_OF_PRODUCER
            await check_deadline()
            return row

        timeout = asyncio.timeout(remaining)
        try:
            async with timeout:
                row = await anext(producer)
        except StopAsyncIteration:
            if timeout.expired():
                await expire()
            await check_deadline()
            return _END_OF_PRODUCER
        except TimeoutError:
            if timeout.expired():
                await expire()
            await check_deadline()
            raise
        except BaseException as error:
            if timeout.expired():
                await expire()
            if not isinstance(error, asyncio.CancelledError):
                await check_deadline()
            raise
        if timeout.expired():
            await expire()
        await check_deadline()
        return row

    try:
        await sequence.reserve()
        reserved = True
        chunk: list[list[Any]] = []

        while True:
            row = await pull_one()
            if row is _END_OF_PRODUCER:
                break
            if not activated:
                await sequence.activate()
                activated = True
                await check_deadline()

            try:
                normalized = _normalize_row(row)
            except BaseException:
                await check_deadline()
                raise
            await check_deadline()
            chunk.append(normalized)
            if len(chunk) == chunk_size:
                await sequence.push(chunk)
                chunk = []
                await check_deadline()

        if chunk:
            await sequence.push(chunk)
            chunk = []
            await check_deadline()
        return await sequence.finish()
    except BaseException as primary:
        cleanup_error: BaseException | None = None
        if reserved and not expired:
            outcome = (
                "cancelled"
                if isinstance(primary, asyncio.CancelledError)
                else "error"
            )
            try:
                await _await_cleanup(
                    sequence.abort(outcome),
                    name="fastmssql-native-bulk-abort",
                )
            except BaseException as error:
                cleanup_error = error

        try:
            await _await_cleanup(
                _close_producer(producer),
                name="fastmssql-native-bulk-producer-close",
            )
        except BaseException as error:
            if cleanup_error is None:
                cleanup_error = error

        if cleanup_error is not None and primary.__cause__ is None:
            primary.__cause__ = cleanup_error
        raise
