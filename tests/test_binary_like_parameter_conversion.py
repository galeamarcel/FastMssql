from __future__ import annotations

import asyncio

from fastmssql import Connection
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        pytest.param(bytearray(2_101), id="bytearray"),
        pytest.param(memoryview(bytes(2_101)), id="memoryview"),
    ],
)
async def test_binary_like_value_is_one_parameter(value: object) -> None:
    connection = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="conversion_probe",
        password="conversion_probe",
    )

    pending_query = connection.query(
        "SELECT CAST(@P1 AS VARBINARY(MAX))",
        [value],
    )
    assert isinstance(pending_query, asyncio.Future)

    pending_query.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending_query
