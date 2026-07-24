from __future__ import annotations

from fastmssql import Connection
import pytest


@pytest.fixture
def disconnected_connection() -> Connection:
    return Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="conversion_probe",
        password="conversion_probe",
    )


def test_flat_parameters_reject_2099_before_io(
    disconnected_connection: Connection,
) -> None:
    with pytest.raises(
        ValueError,
        match=(
            "^Too many parameters: 2099 provided, but FastMssql supports "
            "maximum 2,098 user parameters per query "
            r"\(SQL Server RPC limit 2,100 minus 2 internal parameters\)$"
        ),
    ):
        disconnected_connection.query("SELECT 1", list(range(2099)))


def test_iterable_expansion_rejects_2099_before_io(
    disconnected_connection: Connection,
) -> None:
    with pytest.raises(
        ValueError,
        match=(
            "^Parameter expansion exceeded FastMssql limit of 2,098 "
            "user parameters per query$"
        ),
    ):
        disconnected_connection.query("SELECT 1", [list(range(2099))])


def test_batch_validation_uses_same_effective_rpc_limit(
    disconnected_connection: Connection,
) -> None:
    with pytest.raises(
        ValueError,
        match=(
            "^Batch item 0 parameter validation failed: "
            "ValueError: Too many parameters: 2099 provided, "
            "but FastMssql supports "
        ),
    ):
        disconnected_connection.query_batch(
            [("SELECT 1", list(range(2099)))]
        )
