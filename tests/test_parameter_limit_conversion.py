from __future__ import annotations

from fastmssql import Connection, ConversionError, Parameter
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


def test_batch_validation_adds_item_context_without_erasing_conversion_metadata(
    disconnected_connection: Connection,
) -> None:
    with pytest.raises(
        ConversionError,
        match=(
            "^Batch item 0 parameter validation failed: "
            "ConversionError: Python value has the wrong kind for the "
            "declared SQL parameter type$"
        ),
    ) as error:
        disconnected_connection.query_batch(
            [("SELECT @P1", [Parameter("not-an-integer", "INT")])]
        )

    assert error.value.batch_index == 0
    assert error.value.parameter_index == 0
    assert error.value.sql_type == "INT"
    assert error.value.reason == "wrong_value_kind"
    assert error.value.retryable is False
    assert error.value.wire_sent is False
    assert error.value.connection_discarded is False
    assert error.value.outcome_unknown is False
