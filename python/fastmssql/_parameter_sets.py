"""Shared validation for bounded positional parameter-set producers."""

from __future__ import annotations

from collections.abc import Mapping

from .fastmssql import Parameters


INVALID_PRODUCER_TYPES: tuple[type, ...] = (
    str,
    bytes,
    bytearray,
    memoryview,
    Mapping,
)
MAX_USER_QUERY_PARAMETERS = 2_098


def parameter_count_error(count: int) -> ValueError:
    return ValueError(
        f"Too many parameters: {count} provided, but FastMssql supports "
        "maximum 2,098 user parameters per query "
        "(SQL Server RPC limit 2,100 minus 2 internal parameters)"
    )


def preflight_parameter_set(
    parameter_set: object,
    *,
    operation: str,
) -> object:
    """Validate one positional parameter set without exposing its values."""
    if isinstance(parameter_set, list):
        count = list.__len__(parameter_set)
    elif isinstance(parameter_set, Parameters):
        count = len(parameter_set)
        if count > MAX_USER_QUERY_PARAMETERS:
            raise parameter_count_error(count)
        if len(parameter_set.named):
            raise ValueError(
                "Named parameters are not supported by the SQL Server wire "
                "protocol. Use positional parameters instead. "
                f"Found {len(parameter_set.named)} named parameter(s)"
            )
    else:
        raise TypeError(
            f"each {operation} parameter set must be a list or Parameters object"
        )
    if count > MAX_USER_QUERY_PARAMETERS:
        raise parameter_count_error(count)
    return parameter_set
