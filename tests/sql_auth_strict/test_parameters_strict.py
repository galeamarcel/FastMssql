from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal
import math
import re
from uuid import UUID

from fastmssql import (
    Connection,
    ConversionError,
    Parameter,
    Parameters,
    SqlError,
    TypedNull,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


class _DateDependentTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta | None:
        if value is None:
            return None
        return timedelta(hours=3, minutes=30)

    def dst(self, value: datetime | None) -> timedelta:
        del value
        return timedelta(0)

    def tzname(self, value: datetime | None) -> str:
        del value
        return "DateDependent/+03:30"


class _SemanticallyNaiveTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> None:
        del value
        return None

    def dst(self, value: datetime | None) -> None:
        del value
        return None

    def tzname(self, value: datetime | None) -> None:
        del value
        return None


class _FailingTimezone(tzinfo):
    def utcoffset(self, value: datetime | None) -> timedelta:
        del value
        raise RuntimeError("internal tzinfo secret")

    def dst(self, value: datetime | None) -> timedelta:
        del value
        return timedelta(0)

    def tzname(self, value: datetime | None) -> str:
        del value
        return "Failing"


@case("PARAM-001")
@pytest.mark.asyncio
async def test_none_with_inferable_sql_type(owner_connection: Connection) -> None:
    result = await owner_connection.query(
        "SELECT CAST(@P1 AS INT) AS value", [None]
    )
    assert result.fetchone()["value"] is None


@case("PARAM-002")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "typed_null",
    [
        TypedNull.TINYINT,
        TypedNull.SMALLINT,
        TypedNull.INT,
        TypedNull.BIGINT,
        TypedNull.FLOAT32,
        TypedNull.FLOAT64,
        TypedNull.BIT,
        TypedNull.STRING,
        TypedNull.GUID,
        TypedNull.BINARY,
        TypedNull.NUMERIC,
        TypedNull.XML,
        TypedNull.DATETIME,
        TypedNull.SMALLDATETIME,
        TypedNull.TIME,
        TypedNull.DATE,
        TypedNull.DATETIME2,
        TypedNull.DATETIMEOFFSET,
    ],
    ids=str,
)
async def test_every_typed_null_variant(
    owner_connection: Connection, typed_null: TypedNull
) -> None:
    result = await owner_connection.query("SELECT @P1 AS value", [typed_null])
    assert result.fetchone()["value"] is None


@case("PARAM-003")
@pytest.mark.asyncio
@pytest.mark.parametrize("value", [False, True])
async def test_bool_parameter(owner_connection: Connection, value: bool) -> None:
    row = (
        await owner_connection.query(
            """
            SELECT
                @P1 AS value,
                CONVERT(
                    VARCHAR(128),
                    SQL_VARIANT_PROPERTY(@P1, 'BaseType')
                ) AS base_type,
                CONVERT(
                    INT,
                    SQL_VARIANT_PROPERTY(@P1, 'Precision')
                ) AS precision_value,
                CONVERT(
                    INT,
                    SQL_VARIANT_PROPERTY(@P1, 'MaxLength')
                ) AS max_length
            """,
            [value],
        )
    ).fetchone()
    assert row is not None
    assert row["base_type"] == "bit"
    assert row["precision_value"] == 1
    assert row["max_length"] == 1
    assert row["value"] is value

    integer_row = (
        await owner_connection.query(
            """
            SELECT
                @P1 AS value,
                CONVERT(
                    VARCHAR(128),
                    SQL_VARIANT_PROPERTY(@P1, 'BaseType')
                ) AS base_type
            """,
            [int(value)],
        )
    ).fetchone()
    assert integer_row is not None
    assert integer_row["base_type"] == "bigint"
    assert type(integer_row["value"]) is int
    assert integer_row["value"] == int(value)


@case("PARAM-004")
@pytest.mark.asyncio
async def test_signed_integer_boundaries_and_overflow(
    owner_connection: Connection,
) -> None:
    for value in (-(2**63), -1, 0, 1, 2**63 - 1):
        result = await owner_connection.query(
            "SELECT CAST(@P1 AS BIGINT) AS value", [value]
        )
        assert result.fetchone()["value"] == value

    for value in (-(2**63) - 1, 2**63):
        with pytest.raises(ValueError, match="^Int too large$"):
            await owner_connection.query("SELECT @P1", [value])


@case("PARAM-005")
@pytest.mark.asyncio
async def test_float_finite_signed_zero_and_nonfinite_behavior(
    owner_connection: Connection,
) -> None:
    for value in (-1.25, 0.0, 1.25):
        result = await owner_connection.query(
            "SELECT CAST(@P1 AS FLOAT) AS value", [value]
        )
        returned = result.fetchone()["value"]
        assert returned == value

    negative_zero = (
        await owner_connection.query(
            "SELECT CAST(@P1 AS FLOAT) AS value", [-0.0]
        )
    ).fetchone()["value"]
    assert negative_zero == 0.0
    assert math.copysign(1.0, negative_zero) == -1.0

    for value in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(SqlError):
            await owner_connection.query(
                "SELECT CAST(@P1 AS FLOAT) AS value", [value]
            )


@case("PARAM-006")
@pytest.mark.asyncio
async def test_decimal_parameters_are_exact_and_errors_are_redacted(
    owner_connection: Connection,
) -> None:
    cases = [
        (Decimal("0"), Decimal("0"), 0, 1, 0),
        (Decimal("-0.0001"), Decimal("-0.0001"), -4, 4, 4),
        (Decimal("12.3400"), Decimal("12.3400"), -4, 6, 4),
        (Decimal("1E-38"), Decimal("1E-38"), -38, 38, 38),
        (Decimal("9" * 38), Decimal("9" * 38), 0, 38, 0),
        (
            Decimal("-" + "9" * 38),
            Decimal("-" + "9" * 38),
            0,
            38,
            0,
        ),
        (Decimal("1E+3"), Decimal("1000"), 0, 4, 0),
    ]
    for value, expected, exponent, precision, scale in cases:
        row = (
            await owner_connection.query(
                """
                SELECT
                    @P1 AS value,
                    CONVERT(
                        VARCHAR(128),
                        SQL_VARIANT_PROPERTY(@P1, 'BaseType')
                    ) AS base_type,
                    CONVERT(
                        INT,
                        SQL_VARIANT_PROPERTY(@P1, 'Precision')
                    ) AS precision_value,
                    CONVERT(
                        INT,
                        SQL_VARIANT_PROPERTY(@P1, 'Scale')
                    ) AS scale_value
                """,
                [value],
            )
        ).fetchone()
        assert row is not None
        assert type(row["value"]) is Decimal
        assert row["value"] == expected
        assert row["value"].as_tuple().exponent == exponent
        assert row["base_type"] == "numeric"
        assert row["precision_value"] == precision
        assert row["scale_value"] == scale

    invalid_cases = [
        (Decimal("NaN"), "non_finite", "Decimal parameter must be finite"),
        (Decimal("sNaN"), "non_finite", "Decimal parameter must be finite"),
        (
            Decimal("Infinity"),
            "non_finite",
            "Decimal parameter must be finite",
        ),
        (
            Decimal("-Infinity"),
            "non_finite",
            "Decimal parameter must be finite",
        ),
        (
            Decimal("1" + "0" * 38),
            "precision_overflow",
            "Decimal parameter exceeds SQL Server NUMERIC precision 38",
        ),
        (
            Decimal("1E-39"),
            "precision_overflow",
            "Decimal parameter exceeds SQL Server NUMERIC precision 38",
        ),
        (
            Decimal("1E+38"),
            "precision_overflow",
            "Decimal parameter exceeds SQL Server NUMERIC precision 38",
        ),
    ]
    for value, reason, message in invalid_cases:
        rendered_value = str(value)
        with pytest.raises(
            ConversionError, match=rf"^{re.escape(message)}$"
        ) as error:
            await owner_connection.query("SELECT @P1", [value])

        assert rendered_value not in str(error.value)
        assert error.value.message == message
        assert error.value.parameter_index == 0
        assert error.value.sql_type == "NUMERIC"
        assert error.value.reason == reason
        assert error.value.retryable is False


@case("PARAM-007")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "plain ASCII",
        "Română: ăâîșț",
        "Ελληνικά",
        "日本語",
        "العربية",
    ],
)
async def test_ascii_and_unicode_strings(
    owner_connection: Connection, value: str
) -> None:
    result = await owner_connection.query(
        "SELECT CAST(@P1 AS NVARCHAR(MAX)) AS value", [value]
    )
    assert result.fetchone()["value"] == value


@case("PARAM-008")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "emoji: 🧪🚀",
        "supplementary: \U00020000",
        "combining: e\u0301",
        "embedded\x00nul",
        "line1\r\nline2\nline3",
    ],
)
async def test_complex_unicode_and_control_content(
    owner_connection: Connection, value: str
) -> None:
    result = await owner_connection.query(
        "SELECT CAST(@P1 AS NVARCHAR(MAX)) AS value", [value]
    )
    assert result.fetchone()["value"] == value


@case("PARAM-009")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        "",
        "x" * 4000,
        "y" * 4001,
        "z" * 100_000,
    ],
    ids=["empty", "nvarchar-4000", "nvarchar-max-transition", "large"],
)
async def test_empty_and_boundary_length_strings(
    owner_connection: Connection, value: str
) -> None:
    result = await owner_connection.query(
        "SELECT CAST(@P1 AS NVARCHAR(MAX)) AS value", [value]
    )
    assert result.fetchone()["value"] == value


@case("PARAM-010")
@pytest.mark.asyncio
async def test_binary_and_binary_like_inputs(
    owner_connection: Connection,
) -> None:
    for value in (b"", b"\x00\x01\xff", bytes(range(256)) * 4096):
        result = await owner_connection.query(
            "SELECT CAST(@P1 AS VARBINARY(MAX)) AS value", [value]
        )
        assert result.fetchone()["value"] == value

    for value in (
        bytearray(b"\x00bytearray\xff"),
        memoryview(b"\x00memoryview\xff"),
    ):
        result = await owner_connection.query(
            "SELECT CAST(@P1 AS VARBINARY(MAX)) AS value", [value]
        )
        assert result.fetchone()["value"] == bytes(value)


@case("PARAM-011")
@pytest.mark.asyncio
async def test_date_parameter(owner_connection: Connection) -> None:
    value = date(2024, 2, 29)
    result = await owner_connection.query(
        "SELECT CAST(@P1 AS DATE) AS value", [value]
    )
    assert result.fetchone()["value"] == value


@case("PARAM-012")
@pytest.mark.asyncio
async def test_naive_datetime_and_aware_datetimeoffset_parameters(
    owner_connection: Connection,
) -> None:
    naive_values = [
        datetime(2024, 2, 29, 23, 58, 57, 123456),
        datetime(
            2024,
            2,
            29,
            23,
            58,
            57,
            123456,
            tzinfo=_SemanticallyNaiveTimezone(),
        ),
    ]
    for naive in naive_values:
        naive_row = (
            await owner_connection.query(
                """
                SELECT
                    @P1 AS value,
                    CONVERT(
                        VARCHAR(128),
                        SQL_VARIANT_PROPERTY(@P1, 'BaseType')
                    ) AS base_type,
                    CONVERT(
                        INT,
                        SQL_VARIANT_PROPERTY(@P1, 'Scale')
                    ) AS scale_value
                """,
                [naive],
            )
        ).fetchone()
        assert naive_row is not None
        assert type(naive_row["value"]) is datetime
        assert naive_row["value"] == naive.replace(tzinfo=None)
        assert naive_row["value"].tzinfo is None
        assert naive_row["base_type"] == "datetime2"
        assert naive_row["scale_value"] == 7

    timezones = [
        _DateDependentTimezone(),
        timezone(timedelta(hours=2, minutes=30)),
        timezone(-timedelta(hours=5, minutes=45)),
        timezone(timedelta(hours=14)),
        timezone(-timedelta(hours=14)),
    ]
    for zone in timezones:
        aware = datetime(
            2024,
            2,
            29,
            12,
            34,
            56,
            123456,
            tzinfo=zone,
        )
        row = (
            await owner_connection.query(
                """
                SELECT
                    @P1 AS value,
                    CONVERT(
                        VARCHAR(128),
                        SQL_VARIANT_PROPERTY(@P1, 'BaseType')
                    ) AS base_type,
                    CONVERT(
                        INT,
                        SQL_VARIANT_PROPERTY(@P1, 'Scale')
                    ) AS scale_value
                """,
                [aware],
            )
        ).fetchone()
        assert row is not None
        returned = row["value"]
        assert type(returned) is datetime
        assert row["base_type"] == "datetimeoffset"
        assert row["scale_value"] == 7
        assert returned.utcoffset() == aware.utcoffset()
        assert returned.astimezone(timezone.utc) == aware.astimezone(timezone.utc)

    invalid_cases = [
        (
            timezone(timedelta(seconds=30)),
            "offset_not_whole_minute",
            "Datetime offset must be a whole number of minutes",
        ),
        (
            timezone(timedelta(hours=14, minutes=1)),
            "offset_out_of_range",
            "Datetime offset must be between -14:00 and +14:00",
        ),
        (
            timezone(-timedelta(hours=14, minutes=1)),
            "offset_out_of_range",
            "Datetime offset must be between -14:00 and +14:00",
        ),
        (
            _FailingTimezone(),
            "invalid_datetime",
            "Datetime parameter conversion failed",
        ),
    ]
    for invalid_zone, reason, message in invalid_cases:
        value = datetime(
            2024,
            2,
            29,
            12,
            34,
            56,
            123456,
            tzinfo=invalid_zone,
        )
        with pytest.raises(ConversionError, match=rf"^{re.escape(message)}$") as error:
            await owner_connection.query("SELECT @P1", [value])

        assert error.value.message == message
        assert error.value.parameter_index == 0
        assert error.value.sql_type == "DATETIMEOFFSET(7)"
        assert error.value.reason == reason
        assert error.value.retryable is False
        assert "internal tzinfo secret" not in str(error.value)


@case("PARAM-013")
@pytest.mark.asyncio
async def test_time_parameter_uses_time_7_and_rejects_offsets(
    owner_connection: Connection,
) -> None:
    for value in (
        time(0, 0, 0),
        time(12, 34, 56, 123456),
        time(23, 59, 59, 999999),
    ):
        row = (
            await owner_connection.query(
                """
                SELECT
                    @P1 AS value,
                    CONVERT(
                        VARCHAR(128),
                        SQL_VARIANT_PROPERTY(@P1, 'BaseType')
                    ) AS base_type,
                    CONVERT(
                        INT,
                        SQL_VARIANT_PROPERTY(@P1, 'Scale')
                    ) AS scale_value
                """,
                [value],
            )
        ).fetchone()
        assert row is not None
        assert type(row["value"]) is time
        assert row["value"] == value
        assert row["base_type"] == "time"
        assert row["scale_value"] == 7

    message = (
        "Aware time parameter is not supported because SQL Server TIME "
        "has no offset"
    )
    with pytest.raises(
        ConversionError, match=rf"^{re.escape(message)}$"
    ) as error:
        await owner_connection.query(
            "SELECT @P1",
            [
                time(
                    12,
                    34,
                    56,
                    123456,
                    tzinfo=timezone(timedelta(hours=2)),
                )
            ],
        )

    assert error.value.message == message
    assert error.value.parameter_index == 0
    assert error.value.sql_type == "TIME(7)"
    assert error.value.reason == "timezone_not_supported"
    assert error.value.retryable is False


@case("PARAM-014")
@pytest.mark.asyncio
async def test_uuid_currently_rejected_deterministically(
    owner_connection: Connection,
) -> None:
    with pytest.raises(ValueError, match="^Unsupported type: UUID$"):
        await owner_connection.query(
            "SELECT CAST(@P1 AS UNIQUEIDENTIFIER) AS value",
            [UUID("12345678-1234-5678-9234-567812345678")],
        )


@case("PARAM-015")
@pytest.mark.asyncio
async def test_parameter_and_parameters_positional_apis(
    owner_connection: Connection,
) -> None:
    integer = Parameter(9, "INT")
    string = Parameter("positional", "NVARCHAR")
    parameters = Parameters(integer, string)
    assert parameters.positional[0] is integer
    assert parameters.positional[1] is string
    assert parameters.to_list() == [9, "positional"]

    row = (
        await owner_connection.query(
            "SELECT @P1 AS integer_value, @P2 AS string_value",
            parameters,
        )
    ).fetchone()
    assert row.to_dict() == {
        "integer_value": 9,
        "string_value": "positional",
    }


@case("PARAM-016")
def test_parameters_named_construction_is_rejected_by_wire_conversion() -> None:
    named = Parameters(user_id=42, label="named")
    assert len(named) == 2
    assert named.named["user_id"].value == 42
    assert named.named["label"].value == "named"
    with pytest.raises(
        ValueError, match="Named parameters are not supported"
    ) as error:
        named.to_list()
    assert "user_id" in str(error.value)
    assert "label" in str(error.value)


@case("PARAM-017")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values",
    [
        [1, 2, 3],
        (1, 2, 3),
        {1, 2, 3},
    ],
    ids=["list", "tuple", "set"],
)
async def test_iterable_expansion_for_in(
    owner_connection: Connection, values
) -> None:
    result = await owner_connection.query(
        """
        SELECT value
        FROM (VALUES (1), (2), (3), (4)) AS source(value)
        WHERE value IN (@P1, @P2, @P3)
        ORDER BY value
        """,
        [values],
    )
    assert [row["value"] for row in result.rows()] == [1, 2, 3]


@case("PARAM-018")
@pytest.mark.asyncio
async def test_empty_iterable_expands_to_no_parameters(
    owner_connection: Connection,
) -> None:
    with pytest.raises(SqlError, match="@P1"):
        await owner_connection.query("SELECT @P1 AS value", [[]])


@case("PARAM-019")
@pytest.mark.asyncio
async def test_nested_and_unsupported_objects_are_rejected(
    owner_connection: Connection,
) -> None:
    class UnsupportedValue:
        pass

    for value, type_name in (
        ([[1]], "list"),
        (UnsupportedValue(), "UnsupportedValue"),
    ):
        with pytest.raises(
            ValueError,
            match=rf"^Unsupported type: {re.escape(type_name)}$",
        ):
            await owner_connection.query("SELECT @P1", [value])


@case("PARAM-020")
@pytest.mark.asyncio
async def test_placeholder_count_mismatch(owner_connection: Connection) -> None:
    with pytest.raises(SqlError, match="@P2"):
        await owner_connection.query(
            "SELECT @P1 AS first_value, @P2 AS second_value", [1]
        )

    result = await owner_connection.query(
        "SELECT @P1 AS first_value", [1, 2]
    )
    row = result.fetchone()
    assert row is not None
    assert row.to_dict() == {"first_value": 1}


@case("PARAM-021")
@pytest.mark.asyncio
async def test_parameter_order_and_repeated_placeholders(
    owner_connection: Connection,
) -> None:
    row = (
        await owner_connection.query(
            """
            SELECT
                @P2 AS second_value,
                @P1 AS first_value,
                @P1 AS repeated_first
            """,
            ["first", "second"],
        )
    ).fetchone()
    assert row.to_dict() == {
        "second_value": "second",
        "first_value": "first",
        "repeated_first": "first",
    }


@case("PARAM-022")
@pytest.mark.asyncio
async def test_sql_server_rpc_parameter_boundary(
    owner_connection: Connection,
) -> None:
    # Tiberius executes parameterized statements through sp_executesql and
    # contributes the internal @stmt and @params RPC parameters. That leaves
    # 2,098 user parameters inside SQL Server's 2,100-parameter RPC ceiling.
    at_limit = list(range(2098))
    result = await owner_connection.query(
        "SELECT @P2098 AS boundary_value", at_limit
    )
    assert result.fetchone()["boundary_value"] == 2097

    above_limit = list(range(2099))
    with pytest.raises(
        ValueError,
        match=(
            "^Too many parameters: 2099 provided, but FastMssql supports "
            "maximum 2,098 user parameters per query "
            r"\(SQL Server RPC limit 2,100 minus 2 internal parameters\)$"
        ),
    ):
        await owner_connection.query("SELECT 1", above_limit)

    with pytest.raises(
        ValueError,
        match=(
            "^Parameter expansion exceeded FastMssql limit of 2,098 "
            "user parameters per query$"
        ),
    ):
        await owner_connection.query("SELECT 1", [above_limit])


@case("PARAM-023")
@pytest.mark.asyncio
async def test_parameterized_injection_payload_remains_data(
    owner_connection: Connection,
    unique_sql_name,
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_injection"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    payload = f"'; DROP TABLE {table}; --"

    result = await owner_connection.query(
        "SELECT CAST(@P1 AS NVARCHAR(MAX)) AS value", [payload]
    )
    assert result.fetchone()["value"] == payload
    assert await scalar(owner_connection, "SELECT OBJECT_ID(@P1)", [table[1:-1]])


@case("PARAM-024")
@pytest.mark.asyncio
async def test_conversion_error_is_stable_and_redacted(
    owner_connection: Connection,
) -> None:
    secret = "SensitiveToken_MustNotLeak_2026"

    class SecretValue:
        def __repr__(self) -> str:
            return secret

    with pytest.raises(
        ValueError, match="^Unsupported type: SecretValue$"
    ) as error:
        await owner_connection.query("SELECT @P1", [SecretValue()])
    assert secret not in str(error.value)


@case("PARAM-031")
def test_parameter_repr_is_metadata_only_and_redacted() -> None:
    secret = "StrictParameterReprSecret_MustNotLeak_2026"
    calls = 0

    class SecretValue:
        def __repr__(self) -> str:
            nonlocal calls
            calls += 1
            return secret

    parameters = [
        Parameter(secret, "NVARCHAR(MAX)"),
        Parameter(secret.encode(), "VARBINARY(MAX)"),
        Parameter(Decimal("1234567890.123400"), "DECIMAL(18,6)"),
        Parameter(SecretValue()),
        Parameter([secret, secret], "NVARCHAR(MAX)"),
    ]

    for parameter in parameters:
        rendered = repr(parameter)
        assert secret not in rendered
        assert "value=<redacted>" in rendered
        assert "direction='INPUT'" in rendered

    assert calls == 0
