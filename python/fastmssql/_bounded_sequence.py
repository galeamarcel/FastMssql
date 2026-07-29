"""Reusable bounded coordination for synchronous and asynchronous producers."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar


_END_OF_PRODUCER = object()
_Item = TypeVar("_Item")
_ErrorAnnotator = Callable[[BaseException, int, object | None], None]


async def _run_cleanup(awaitable: Awaitable[Any]) -> Any:
    return await awaitable


async def _await_cleanup(awaitable: Awaitable[Any], *, name: str) -> Any:
    """Run mandatory cleanup to completion despite repeated cancellation."""
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


def _acquire_producer(
    source: object,
    *,
    error_message: str,
) -> tuple[bool, object]:
    """Acquire exactly one preferred producer protocol without advancing it."""
    if getattr(type(source), "__aiter__", None) is not None:
        return True, aiter(source)
    if getattr(type(source), "__iter__", None) is not None:
        return False, iter(source)
    raise TypeError(error_message)


async def run_bounded_sequence(
    sequence: object,
    source: object,
    *,
    chunk_size: int,
    normalize_item: Callable[[object], _Item],
    producer_error: str,
    task_name_prefix: str,
    preflight_item: Callable[[object], object] | None = None,
    annotate_error: _ErrorAnnotator | None = None,
) -> int:
    """Pull, normalize and push one bounded chunk at a time."""
    asynchronous, producer = _acquire_producer(
        source,
        error_message=producer_error,
    )
    reservation_started = False
    activated = False
    expired = False
    current_index = 0

    async def expire() -> None:
        nonlocal expired
        expired = True
        await _await_cleanup(
            sequence.expire(),
            name=f"{task_name_prefix}-expire",
        )
        raise RuntimeError("Bounded sequence expiry returned without an error")

    async def check_deadline() -> None:
        remaining = sequence.remaining_timeout()
        if remaining is not None and remaining <= 0:
            await expire()

    async def pull_one() -> object:
        if not asynchronous:
            await check_deadline()
            try:
                item = next(producer)
            except StopIteration:
                await check_deadline()
                return _END_OF_PRODUCER
            except BaseException:
                await check_deadline()
                raise
            await check_deadline()
            return item

        remaining = sequence.remaining_timeout()
        if remaining is not None and remaining <= 0:
            await expire()
        if remaining is None:
            try:
                item = await anext(producer)
            except StopAsyncIteration:
                await check_deadline()
                return _END_OF_PRODUCER
            await check_deadline()
            return item

        timeout = asyncio.timeout(remaining)
        try:
            async with timeout:
                item = await anext(producer)
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
        return item

    try:
        # The await may be cancelled after the reservation becomes effective
        # but before Python observes the successful return.
        reservation_started = True
        await sequence.reserve()
        chunk: list[_Item] = []

        while True:
            item = await pull_one()
            if item is _END_OF_PRODUCER:
                break

            if preflight_item is not None:
                try:
                    item = preflight_item(item)
                except BaseException:
                    await check_deadline()
                    raise
                await check_deadline()

            if not activated:
                await sequence.activate()
                activated = True
                await check_deadline()

            try:
                chunk.append(normalize_item(item))
            except BaseException:
                await check_deadline()
                raise
            await check_deadline()
            current_index += 1

            if len(chunk) == chunk_size:
                await sequence.push(chunk)
                chunk = []
                item = _END_OF_PRODUCER
                await check_deadline()

        if chunk:
            await sequence.push(chunk)
            chunk = []
            await check_deadline()
        return await sequence.finish()
    except BaseException as primary:
        cleanup_error: BaseException | None = None
        abort_progress: object | None = None
        if reservation_started and not expired:
            outcome = (
                "cancelled"
                if isinstance(primary, asyncio.CancelledError)
                else "error"
            )
            try:
                abort_progress = await _await_cleanup(
                    sequence.abort(outcome),
                    name=f"{task_name_prefix}-abort",
                )
            except BaseException as error:
                cleanup_error = error

        try:
            await _await_cleanup(
                _close_producer(producer),
                name=f"{task_name_prefix}-producer-close",
            )
        except BaseException as error:
            if cleanup_error is None:
                cleanup_error = error

        if annotate_error is not None:
            try:
                annotate_error(primary, current_index, abort_progress)
            except BaseException as error:
                if cleanup_error is None:
                    cleanup_error = error

        if cleanup_error is not None and primary.__cause__ is None:
            primary.__cause__ = cleanup_error
        raise
