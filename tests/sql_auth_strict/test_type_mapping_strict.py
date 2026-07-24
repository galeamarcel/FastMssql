from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import math

from fastmssql import Connection, ConversionError, ProtocolError
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


@case("TYPE-001")
@pytest.mark.asyncio
async def test_integer_type_mapping_and_boundaries(
    owner_connection: Connection,
) -> None:
    cases = [
        ("CAST(0 AS TINYINT)", 0),
        ("CAST(255 AS TINYINT)", 255),
        ("CAST(-32768 AS SMALLINT)", -32768),
        ("CAST(32767 AS SMALLINT)", 32767),
        ("CAST(-2147483648 AS INT)", -(2**31)),
        ("CAST(2147483647 AS INT)", 2**31 - 1),
        ("CAST(-9223372036854775808 AS BIGINT)", -(2**63)),
        ("CAST(9223372036854775807 AS BIGINT)", 2**63 - 1),
    ]
    for expression, expected in cases:
        value = await scalar(owner_connection, f"SELECT {expression}")
        assert type(value) is int
        assert value == expected

    for sql_type in ("TINYINT", "SMALLINT", "INT", "BIGINT"):
        assert (
            await scalar(
                owner_connection, f"SELECT CAST(NULL AS {sql_type})"
            )
            is None
        )


@case("TYPE-002")
@pytest.mark.asyncio
async def test_bit_maps_to_bool(owner_connection: Connection) -> None:
    false_value = await scalar(
        owner_connection, "SELECT CAST(0 AS BIT)"
    )
    true_value = await scalar(owner_connection, "SELECT CAST(1 AS BIT)")
    null_value = await scalar(owner_connection, "SELECT CAST(NULL AS BIT)")
    assert type(false_value) is bool
    assert false_value is False
    assert type(true_value) is bool
    assert true_value is True
    assert null_value is None


@case("TYPE-003")
@pytest.mark.asyncio
async def test_real_and_float_mapping(owner_connection: Connection) -> None:
    real_value = await scalar(
        owner_connection, "SELECT CAST(-123.5 AS REAL)"
    )
    float_value = await scalar(
        owner_connection, "SELECT CAST(1.23456789012345E+200 AS FLOAT)"
    )
    assert type(real_value) is float
    assert real_value == pytest.approx(-123.5)
    assert type(float_value) is float
    assert math.isfinite(float_value)
    assert float_value == pytest.approx(1.23456789012345e200)
    assert (
        await scalar(owner_connection, "SELECT CAST(NULL AS REAL)") is None
    )
    assert (
        await scalar(owner_connection, "SELECT CAST(NULL AS FLOAT)") is None
    )


@case("TYPE-004")
@pytest.mark.asyncio
async def test_decimal_and_numeric_preserve_precision_and_scale(
    owner_connection: Connection,
) -> None:
    cases = [
        ("CAST(-0.0001 AS DECIMAL(10,4))", Decimal("-0.0001"), -4),
        ("CAST(12.3400 AS NUMERIC(10,4))", Decimal("12.3400"), -4),
        (
            "CAST(99999999999999999999999999999999999999 "
            "AS DECIMAL(38,0))",
            Decimal("99999999999999999999999999999999999999"),
            0,
        ),
    ]
    for expression, expected, exponent in cases:
        value = await scalar(owner_connection, f"SELECT {expression}")
        assert type(value) is Decimal
        assert value == expected
        assert value.as_tuple().exponent == exponent

    assert (
        await scalar(
            owner_connection, "SELECT CAST(NULL AS DECIMAL(38,18))"
        )
        is None
    )


@case("TYPE-005")
@pytest.mark.asyncio
async def test_money_types_preserve_four_decimal_places(
    owner_connection: Connection,
) -> None:
    cases = [
        ("CAST(-922337203685477.5808 AS MONEY)", Decimal("-922337203685477.5808")),
        ("CAST(214748.3647 AS SMALLMONEY)", Decimal("214748.3647")),
        ("CAST(12.3400 AS MONEY)", Decimal("12.3400")),
    ]
    for expression, expected in cases:
        value = await scalar(owner_connection, f"SELECT {expression}")
        assert type(value) is Decimal
        assert value == expected
        assert value.as_tuple().exponent == -4

    assert (
        await scalar(owner_connection, "SELECT CAST(NULL AS MONEY)") is None
    )
    assert (
        await scalar(owner_connection, "SELECT CAST(NULL AS SMALLMONEY)")
        is None
    )


@case("TYPE-006")
@pytest.mark.asyncio
async def test_ansi_character_and_legacy_text_mapping(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_ansi_types"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            fixed CHAR(4) NULL,
            variable VARCHAR(20) NULL,
            maximum VARCHAR(MAX) NULL,
            legacy TEXT NULL
        )
        """
    )
    await owner_connection.execute(
        f"""
        INSERT INTO {table} (fixed, variable, maximum, legacy)
        VALUES ('A', 'case-Test', REPLICATE('x', 9000), 'legacy-text')
        """
    )
    row = (
        await owner_connection.query(
            f"""
            SELECT
                fixed,
                variable COLLATE Latin1_General_100_CI_AS AS variable,
                maximum,
                legacy
            FROM {table}
            """
        )
    ).fetchone()
    assert row is not None
    assert row["fixed"] == "A   "
    assert row["variable"] == "case-Test"
    assert row["maximum"] == "x" * 9000
    assert row["legacy"] == "legacy-text"
    assert all(
        type(row[column]) is str
        for column in ("fixed", "variable", "maximum", "legacy")
    )


@case("TYPE-007")
@pytest.mark.asyncio
async def test_unicode_character_and_legacy_ntext_mapping(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_unicode_types"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            fixed NCHAR(4) NULL,
            variable NVARCHAR(40) NULL,
            maximum NVARCHAR(MAX) NULL,
            legacy NTEXT NULL
        )
        """
    )
    await owner_connection.execute(
        f"""
        INSERT INTO {table} (fixed, variable, maximum, legacy)
        VALUES (
            N'Ș',
            N'Română 🧪',
            REPLICATE(CAST(N'界' AS NVARCHAR(MAX)), 5000),
            N'legacy-șț'
        )
        """
    )
    row = (await owner_connection.query(f"SELECT * FROM {table}")).fetchone()
    assert row is not None
    assert row["fixed"] == "Ș   "
    assert row["variable"] == "Română 🧪"
    assert row["maximum"] == "界" * 5000
    assert row["legacy"] == "legacy-șț"


@case("TYPE-008")
@pytest.mark.asyncio
async def test_binary_legacy_image_and_rowversion_mapping(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_binary_types"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            id INT IDENTITY PRIMARY KEY,
            fixed BINARY(4) NULL,
            variable VARBINARY(20) NULL,
            maximum VARBINARY(MAX) NULL,
            legacy IMAGE NULL,
            version ROWVERSION NOT NULL
        )
        """
    )
    await owner_connection.execute(
        f"""
        INSERT INTO {table} (fixed, variable, maximum, legacy)
        VALUES (
            0x0102,
            0x0001FF,
            CONVERT(VARBINARY(MAX), REPLICATE('z', 9000)),
            0xDEADBEEF
        )
        """
    )
    row = (
        await owner_connection.query(
            f"SELECT fixed, variable, maximum, legacy, version FROM {table}"
        )
    ).fetchone()
    assert row is not None
    assert row["fixed"] == b"\x01\x02\x00\x00"
    assert row["variable"] == b"\x00\x01\xff"
    assert row["maximum"] == b"z" * 9000
    assert row["legacy"] == b"\xde\xad\xbe\xef"
    assert type(row["version"]) is bytes
    assert len(row["version"]) == 8


@case("TYPE-009")
@pytest.mark.asyncio
async def test_date_mapping(owner_connection: Connection) -> None:
    value = await scalar(
        owner_connection, "SELECT CAST('0001-01-01' AS DATE)"
    )
    assert type(value) is date
    assert value == date.min
    maximum = await scalar(
        owner_connection, "SELECT CAST('9999-12-31' AS DATE)"
    )
    assert type(maximum) is date
    assert maximum == date.max
    assert await scalar(owner_connection, "SELECT CAST(NULL AS DATE)") is None


@case("TYPE-010")
@pytest.mark.asyncio
async def test_time_precision_mapping(owner_connection: Connection) -> None:
    cases = [
        ("CAST('00:00:00' AS TIME(0))", time(0, 0, 0)),
        ("CAST('12:34:56.123' AS TIME(3))", time(12, 34, 56, 123000)),
        (
            "CAST('23:59:59.1234560' AS TIME(7))",
            time(23, 59, 59, 123456),
        ),
    ]
    for expression, expected in cases:
        value = await scalar(owner_connection, f"SELECT {expression}")
        assert type(value) is time
        assert value == expected
    assert await scalar(owner_connection, "SELECT CAST(NULL AS TIME)") is None


@case("TYPE-011")
@pytest.mark.asyncio
async def test_datetime_family_mapping(owner_connection: Connection) -> None:
    cases = [
        (
            "CAST('2024-02-29T12:34:00' AS SMALLDATETIME)",
            datetime(2024, 2, 29, 12, 34),
        ),
        (
            "CAST('2024-02-29T12:34:56.120' AS DATETIME)",
            datetime(2024, 2, 29, 12, 34, 56, 120000),
        ),
        (
            "CAST('2024-02-29T12:34:56.123456' AS DATETIME2(6))",
            datetime(2024, 2, 29, 12, 34, 56, 123456),
        ),
    ]
    for expression, expected in cases:
        value = await scalar(owner_connection, f"SELECT {expression}")
        assert type(value) is datetime
        assert value == expected

    for sql_type in ("SMALLDATETIME", "DATETIME", "DATETIME2"):
        assert (
            await scalar(
                owner_connection, f"SELECT CAST(NULL AS {sql_type})"
            )
            is None
        )


@case("TYPE-012")
@pytest.mark.asyncio
async def test_datetimeoffset_retains_instant_and_offset(
    owner_connection: Connection,
) -> None:
    value = await scalar(
        owner_connection,
        """
        SELECT CAST(
            '2024-02-29T12:34:56.123456+02:30'
            AS DATETIMEOFFSET(6)
        )
        """,
    )
    expected = datetime(
        2024,
        2,
        29,
        12,
        34,
        56,
        123456,
        tzinfo=timezone(timedelta(hours=2, minutes=30)),
    )
    assert type(value) is datetime
    assert value == expected
    assert value.utcoffset() == timedelta(hours=2, minutes=30)
    assert value.astimezone(timezone.utc) == expected.astimezone(timezone.utc)
    assert (
        await scalar(owner_connection, "SELECT CAST(NULL AS DATETIMEOFFSET)")
        is None
    )


@case("TYPE-013")
@pytest.mark.asyncio
async def test_uniqueidentifier_mapping(owner_connection: Connection) -> None:
    value = await scalar(
        owner_connection,
        """
        SELECT CAST(
            '12345678-1234-5678-9234-567812345678'
            AS UNIQUEIDENTIFIER
        )
        """,
    )
    assert type(value) is str
    assert value == "12345678-1234-5678-9234-567812345678"
    assert (
        await scalar(owner_connection, "SELECT CAST(NULL AS UNIQUEIDENTIFIER)")
        is None
    )


@case("TYPE-014")
@pytest.mark.asyncio
async def test_xml_mapping(owner_connection: Connection) -> None:
    value = await scalar(
        owner_connection,
        "SELECT CAST(N'<root><value>șț</value></root>' AS XML)",
    )
    assert type(value) is str
    assert value == "<root><value>șț</value></root>"
    assert await scalar(owner_connection, "SELECT CAST(NULL AS XML)") is None


@case("TYPE-015")
@pytest.mark.asyncio
async def test_nullable_columns_and_mixed_rows(
    owner_connection: Connection,
) -> None:
    result = await owner_connection.query(
        """
        SELECT id, integer_value, text_value
        FROM (
            VALUES
                (1, CAST(NULL AS INT), CAST(N'present' AS NVARCHAR(20))),
                (2, CAST(7 AS INT), CAST(NULL AS NVARCHAR(20)))
        ) AS source(id, integer_value, text_value)
        ORDER BY id
        """
    )
    rows = result.rows()
    assert [row.to_dict() for row in rows] == [
        {"id": 1, "integer_value": None, "text_value": "present"},
        {"id": 2, "integer_value": 7, "text_value": None},
    ]


@case("TYPE-016")
@pytest.mark.asyncio
async def test_unusual_and_duplicate_column_names(
    owner_connection: Connection,
) -> None:
    row = (
        await owner_connection.query(
            """
            SELECT
                1 AS [duplicate],
                2 AS [duplicate],
                3 AS [name with spaces],
                4 AS [MiXeD],
                N'valoare' AS [coloană]
            """
        )
    ).fetchone()
    assert row is not None
    assert row.columns() == [
        "duplicate",
        "duplicate",
        "name with spaces",
        "MiXeD",
        "coloană",
    ]
    assert row[0] == 1
    assert row[1] == 2
    assert row["duplicate"] == 2
    assert row["name with spaces"] == 3
    assert row["MiXeD"] == 4
    assert row["coloană"] == "valoare"
    assert row.to_dict() == {
        "duplicate": 2,
        "name with spaces": 3,
        "MiXeD": 4,
        "coloană": "valoare",
    }


@case("TYPE-017")
@pytest.mark.asyncio
async def test_unsupported_complex_types_are_explicit(
    owner_connection: Connection,
) -> None:
    string_variant = await scalar(
        owner_connection,
        "SELECT CAST(CAST(N'variant-text' AS NVARCHAR(30)) AS SQL_VARIANT)",
    )
    assert type(string_variant) is str
    assert string_variant == "variant-text"

    unsupported_expressions = [
        "CAST(CAST(123 AS INT) AS SQL_VARIANT)",
        "hierarchyid::Parse('/1/')",
        "geometry::Point(1, 2, 0)",
        "geography::Point(1, 2, 4326)",
    ]
    for expression in unsupported_expressions:
        with pytest.raises((ValueError, ConversionError, ProtocolError)):
            await owner_connection.query(f"SELECT {expression} AS value")
