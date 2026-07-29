"""Bounded sync/async producer coordination for repeated SQL execution."""

from __future__ import annotations

from collections.abc import Mapping

from ._bounded_sequence import run_bounded_sequence
from ._parameter_sets import (
    INVALID_PRODUCER_TYPES,
    preflight_parameter_set,
)


_PRODUCER_ERROR = (
    "parameter_sets must be an iterable or async iterable of lists or "
    "Parameters objects"
)
_ATOMIC_UNSET = object()


def _progress_field(
    progress: object | None,
    field: str,
    default: object,
) -> object:
    if isinstance(progress, Mapping):
        return progress.get(field, default)
    if progress is None:
        return default
    return getattr(progress, field, default)


def _non_negative_int(value: object, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return default


class _ProgressTrackingSequence:
    """Track acknowledged Python-side chunks while delegating Rust cleanup."""

    def __init__(self, sequence: object, *, chunk_commits: bool) -> None:
        self._sequence = sequence
        self._chunk_commits = chunk_commits
        self.confirmed_committed_parameter_sets = 0
        self.expired_between_native_calls = False

    async def reserve(self) -> None:
        await self._sequence.reserve()

    async def activate(self) -> None:
        await self._sequence.activate()

    async def push(self, parameter_sets: list[object]) -> int:
        affected = await self._sequence.push(parameter_sets)
        if self._chunk_commits:
            self.confirmed_committed_parameter_sets += len(parameter_sets)
        return affected

    async def finish(self) -> int:
        return await self._sequence.finish()

    async def abort(self, outcome: str) -> object:
        return await self._sequence.abort(outcome)

    async def expire(self) -> None:
        self.expired_between_native_calls = True
        await self._sequence.expire()
        raise RuntimeError("execute_many sequence expiry returned without an error")

    def remaining_timeout(self) -> float | None:
        return self._sequence.remaining_timeout()


def _annotate_execute_many_error(
    error: BaseException,
    current_index: int,
    abort_progress: object | None,
    *,
    tracked_commits: int,
    prefer_current_index: bool,
) -> None:
    active_index = _progress_field(
        abort_progress,
        "active_parameter_set_index",
        None,
    )
    existing_index = getattr(error, "parameter_set_index", None)
    if prefer_current_index:
        parameter_set_index = current_index
    elif isinstance(active_index, int) and not isinstance(active_index, bool):
        parameter_set_index = active_index
    elif isinstance(existing_index, int) and not isinstance(existing_index, bool):
        parameter_set_index = existing_index
    else:
        parameter_set_index = current_index

    existing_commits = _non_negative_int(
        getattr(error, "confirmed_committed_parameter_sets", 0)
    )
    rust_commits = _non_negative_int(
        _progress_field(
            abort_progress,
            "confirmed_committed_parameter_sets",
            0,
        )
    )
    confirmed_commits = max(
        existing_commits,
        rust_commits,
        tracked_commits,
    )
    partial_commit_possible = bool(
        getattr(error, "partial_commit_possible", False)
        or _progress_field(
            abort_progress,
            "partial_commit_possible",
            False,
        )
        or tracked_commits
    )

    error.parameter_set_index = parameter_set_index
    error.confirmed_committed_parameter_sets = confirmed_commits
    error.partial_commit_possible = partial_commit_possible


async def execute_many_iterable(
    raw_owner: object,
    sql: str,
    parameter_sets: object,
    *,
    atomic: object = _ATOMIC_UNSET,
    chunk_size: int,
) -> int:
    """Execute one statement over one bounded sync or async producer."""
    if isinstance(parameter_sets, INVALID_PRODUCER_TYPES):
        raise TypeError(_PRODUCER_ERROR)

    # Rust validates SQL extraction, atomic and chunk_size before Python
    # acquires or advances caller code. Sequence construction itself is
    # side-effect-free.
    if atomic is _ATOMIC_UNSET:
        raw_sequence = raw_owner._execute_many_sequence(
            sql,
            chunk_size=chunk_size,
        )
    else:
        raw_sequence = raw_owner._execute_many_sequence(
            sql,
            atomic=atomic,
            chunk_size=chunk_size,
        )

    sequence = _ProgressTrackingSequence(
        raw_sequence,
        chunk_commits=atomic is False,
    )

    def annotate_error(
        error: BaseException,
        current_index: int,
        abort_progress: object | None,
    ) -> None:
        _annotate_execute_many_error(
            error,
            current_index,
            abort_progress,
            tracked_commits=sequence.confirmed_committed_parameter_sets,
            prefer_current_index=sequence.expired_between_native_calls,
        )

    return await run_bounded_sequence(
        sequence,
        parameter_sets,
        chunk_size=chunk_size,
        normalize_item=lambda parameter_set: parameter_set,
        preflight_item=lambda item: preflight_parameter_set(
            item,
            operation="execute_many",
        ),
        producer_error=_PRODUCER_ERROR,
        task_name_prefix="fastmssql-execute-many",
        annotate_error=annotate_error,
    )
