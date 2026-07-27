"""
Tests for advanced parameter type conversions and edge cases

This module tests parameter handling for various SQL types, edge cases,
and boundary conditions to ensure proper type conversion between Python and SQL Server.
"""

import datetime
from decimal import Decimal
from uuid import UUID

import pytest
from conftest import Config

try:
    from fastmssql import (
        Connection,
        ConversionError,
        Parameter,
        TypedNull,
    )
except ImportError:
    pytest.fail("fastmssql not available - run 'maturin develop' first")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_int_types(test_config: Config):
    """Test integer parameter type conversions."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # Small integer
            result = await conn.query("SELECT @P1 as value", [42])
            assert result.rows()[0]["value"] == 42

            # Large integer
            result = await conn.query("SELECT @P1 as value", [9223372036854775807])
            assert result.rows()[0]["value"] == 9223372036854775807

            # Negative integer
            result = await conn.query("SELECT @P1 as value", [-42])
            assert result.rows()[0]["value"] == -42

            # Zero
            result = await conn.query("SELECT @P1 as value", [0])
            assert result.rows()[0]["value"] == 0
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_string_types(test_config: Config):
    """Test string parameter type conversions."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # Regular string
            result = await conn.query("SELECT @P1 as value", ["hello"])
            assert result.rows()[0]["value"] == "hello"

            # Empty string
            result = await conn.query("SELECT @P1 as value", [""])
            assert result.rows()[0]["value"] == ""

            # String with spaces
            result = await conn.query("SELECT @P1 as value", ["hello world"])
            assert result.rows()[0]["value"] == "hello world"

            # String with special characters
            result = await conn.query("SELECT @P1 as value", ["test'quote"])
            assert "quote" in result.rows()[0]["value"]

            # Unicode string
            result = await conn.query("SELECT @P1 as value", ["こんにちは"])
            assert result.rows()[0]["value"] == "こんにちは"

            # Long string
            long_str = "x" * 1000
            result = await conn.query("SELECT @P1 as value", [long_str])
            assert result.rows()[0]["value"] == long_str
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_float_types(test_config: Config):
    """Test float parameter type conversions."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # Regular float
            result = await conn.query("SELECT @P1 as value", [3.14])
            value = result.rows()[0]["value"]
            assert abs(value - 3.14) < 0.001

            # Zero
            result = await conn.query("SELECT @P1 as value", [0.0])
            assert result.rows()[0]["value"] == 0.0

            # Negative float
            result = await conn.query("SELECT @P1 as value", [-3.14])
            value = result.rows()[0]["value"]
            assert abs(value - (-3.14)) < 0.001

            # Very small float
            result = await conn.query("SELECT @P1 as value", [0.0001])
            value = result.rows()[0]["value"]
            assert abs(value - 0.0001) < 0.00001
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_bool_types(test_config: Config):
    """Test boolean parameter type conversions."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # True
            result = await conn.query("SELECT CAST(@P1 AS BIT) as value", [True])
            value = result.rows()[0]["value"]
            assert value in [1, True]

            # False
            result = await conn.query("SELECT CAST(@P1 AS BIT) as value", [False])
            value = result.rows()[0]["value"]
            assert value in [0, False]

            # Integer as bool
            result = await conn.query("SELECT CAST(@P1 AS BIT) as value", [1])
            value = result.rows()[0]["value"]
            assert value in [1, True]

            result = await conn.query("SELECT CAST(@P1 AS BIT) as value", [0])
            value = result.rows()[0]["value"]
            assert value in [0, False]
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_none_values(test_config: Config):
    """Test None/NULL parameter handling."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # None parameter
            result = await conn.query("SELECT @P1 as value", [None])
            assert result.rows()[0]["value"] is None

            # Multiple parameters with None
            result = await conn.query(
                "SELECT @P1 as val1, @P2 as val2, @P3 as val3", [1, None, "test"]
            )
            row = result.rows()[0]
            assert row["val1"] == 1
            assert row["val2"] is None
            assert row["val3"] == "test"
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_datetime_types(test_config: Config):
    """Test datetime parameter type conversions (via string)."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # datetime types are not directly supported - convert to string
            dt_val = datetime.datetime(2023, 12, 25, 14, 30, 45)
            dt_str = dt_val.isoformat()
            result = await conn.query("SELECT @P1 as value", [dt_str])
            returned_dt = result.rows()[0]["value"]
            assert returned_dt is not None
            assert "2023" in str(returned_dt)

            # Date as string
            date_val = datetime.date(2023, 12, 25)
            date_str = date_val.isoformat()
            result = await conn.query("SELECT @P1 as value", [date_str])
            returned_date = result.rows()[0]["value"]
            assert returned_date is not None
            assert "2023-12-25" in str(returned_date)
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_decimal_types(test_config: Config):
    """Decimal parameters preserve value and trailing-zero scale exactly."""
    async with Connection(test_config.connection_string) as conn:
        value = Decimal("1234567890.123400")

        for _ in range(3):
            result = await conn.query("SELECT @P1 AS value", [value])
            returned = result.rows()[0]["value"]

            assert type(returned) is Decimal
            assert returned == Decimal("1234567890.123400")
            assert returned.as_tuple().exponent == -6
            value = returned


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_bytes_types(test_config: Config):
    """Test bytes parameter type conversions."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # Bytes
            bytes_val = b"hello"
            result = await conn.query("SELECT @P1 as value", [bytes_val])
            returned = result.rows()[0]["value"]
            # Bytes might be returned as string or bytes
            if isinstance(returned, bytes):
                assert returned == bytes_val
            elif isinstance(returned, str):
                # Might be hex-encoded or base64
                assert len(returned) > 0
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_list_only(test_config: Config):
    """Test parameter passing as list (tuples not supported)."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # List parameters
            result = await conn.query("SELECT @P1 as val1, @P2 as val2", [1, "test"])
            row = result.rows()[0]
            assert row["val1"] == 1
            assert row["val2"] == "test"

            # List with different values
            result = await conn.query("SELECT @P1 as val1, @P2 as val2", [2, "list"])
            row = result.rows()[0]
            assert row["val1"] == 2
            assert row["val2"] == "list"
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_many_parameters(test_config: Config):
    """Test queries with many parameters (>16)."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # Create query with 20 parameters
            params = list(range(1, 21))
            param_placeholders = ", ".join([f"@P{i}" for i in range(1, 21)])
            query = f"SELECT {param_placeholders}"

            result = await conn.query(query, params)
            assert result.has_rows()
            row = result.rows()[0]

            # Verify each parameter
            for i in range(1, 21):
                # The exact column naming depends on implementation
                assert row is not None
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_repeated_values(test_config: Config):
    """Test using same parameter multiple times."""
    try:
        async with Connection(test_config.connection_string) as conn:
            result = await conn.query("SELECT @P1 + @P1 + @P1 as result", [5])
            # Some systems might not support this; check if result is valid
            if result.has_rows():
                value = result.rows()[0]["result"]
                assert value == 15
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_special_string_characters(test_config: Config):
    """Test parameters with special characters."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # Single quote
            result = await conn.query("SELECT @P1 as value", ["it's"])
            value = result.rows()[0]["value"]
            assert "'" in value or "'" in value

            # Double quote
            result = await conn.query("SELECT @P1 as value", ['he said "hello"'])
            value = result.rows()[0]["value"]
            assert '"' in value or "hello" in value

            # Backslash
            result = await conn.query("SELECT @P1 as value", ["path\\to\\file"])
            value = result.rows()[0]["value"]
            assert "path" in value

            # Null character (might be stripped)
            result = await conn.query("SELECT @P1 as value", ["test\x00null"])
            value = result.rows()[0]["value"]
            assert value is not None
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_whitespace_handling(test_config: Config):
    """Test parameter handling with various whitespace."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # Leading/trailing whitespace
            result = await conn.query("SELECT @P1 as value", ["  test  "])
            value = result.rows()[0]["value"]
            assert "test" in value

            # Tab and newline characters
            result = await conn.query("SELECT @P1 as value", ["test\ttab\nline"])
            value = result.rows()[0]["value"]
            assert "test" in value

            # Multiple spaces
            result = await conn.query(
                "SELECT @P1 as value", ["test     multiple     spaces"]
            )
            value = result.rows()[0]["value"]
            assert "test" in value
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_boundary_integers(test_config: Config):
    """Test boundary value integers."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # INT min/max
            result = await conn.query("SELECT @P1 as value", [-2147483648])
            assert result.rows()[0]["value"] == -2147483648

            result = await conn.query("SELECT @P1 as value", [2147483647])
            assert result.rows()[0]["value"] == 2147483647

            # BIGINT min/max
            result = await conn.query("SELECT @P1 as value", [-9223372036854775808])
            assert result.rows()[0]["value"] == -9223372036854775808

            result = await conn.query("SELECT @P1 as value", [9223372036854775807])
            assert result.rows()[0]["value"] == 9223372036854775807
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_mixed_types_in_batch(test_config: Config):
    """Test batch operations with mixed parameter types."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # Create an isolated test table
            await conn.execute("""
                IF OBJECT_ID('dbo.fm_mixed_types', 'U') IS NOT NULL
                    DROP TABLE dbo.fm_mixed_types
            """)

            await conn.execute("""
                CREATE TABLE dbo.fm_mixed_types (
                    id INT,
                    name VARCHAR(50),
                    score FLOAT,
                    active BIT
                )
            """)

            # Batch with different types
            batch_items = [
                (
                    "INSERT INTO dbo.fm_mixed_types VALUES (@P1, @P2, @P3, @P4)",
                    [1, "Alice", 95.5, True],
                ),
                (
                    "INSERT INTO dbo.fm_mixed_types VALUES (@P1, @P2, @P3, @P4)",
                    [2, "Bob", 87.3, False],
                ),
                (
                    "INSERT INTO dbo.fm_mixed_types VALUES (@P1, @P2, @P3, @P4)",
                    [3, "Charlie", 92.1, True],
                ),
            ]

            results = await conn.execute_batch(batch_items)
            assert len(results) == 3

            # Verify data
            result = await conn.query("SELECT COUNT(*) as cnt FROM dbo.fm_mixed_types")
            assert result.rows()[0]["cnt"] == 3

            # Cleanup
            await conn.execute("DROP TABLE dbo.fm_mixed_types")
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_type_explicit_casting(test_config: Config):
    """Test explicit type casting in queries."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # Cast to INT
            result = await conn.query("SELECT CAST(@P1 AS INT) as value", [42.7])
            assert result.rows()[0]["value"] == 42

            # Cast to VARCHAR
            result = await conn.query("SELECT CAST(@P1 AS VARCHAR(50)) as value", [123])
            value = str(result.rows()[0]["value"])
            assert "123" in value

            # Cast to FLOAT
            result = await conn.query("SELECT CAST(@P1 AS FLOAT) as value", ["3.14"])
            value = result.rows()[0]["value"]
            if value is not None:
                assert abs(float(value) - 3.14) < 0.1
    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parameter_typed_null(test_config: Config):
    """Test explicit typed nulls in queries (unfortunately cant use stored procedures here *yet*)."""
    try:
        async with Connection(test_config.connection_string) as conn:
            # This should yield NULL (1 + tinyint of null)
            res = await conn.query("SELECT 1 + @P1 as value", [None])
            value = res.rows()[0]["value"]
            assert value is None

            # Due to ms-sql casting rules, these are the only types that seem to trigger for this specific
            # check
            for typ in [
                (TypedNull.GUID, "uniqueidentifier"),
                (TypedNull.TIME, "time"),
                (TypedNull.DATE, "date"),
                (TypedNull.DATETIME2, "datetime2"),
                (TypedNull.DATETIMEOFFSET, "datetimeoffset"),
            ]:
                # Test 1: ms-sql casting
                err = None
                try:
                    res = await conn.query("SELECT 1 + @P1 as value", [typ[0]])
                except Exception as e:
                    err = str(e)
                assert err is not None and f"{typ[1]} is incompatible with int" in err

                # Test 2: sproc with non-typed null + typed null

                # Create test sproc, note that simple_query must be used for CREATE OR ALTER PROCEDURE
                await conn.simple_query(f"""
                    CREATE OR ALTER PROCEDURE test_sproc (@Foo {typ[1]})
                    AS
                    BEGIN
                        SELECT 5 AS status
                    END
                """)

                # This fails as normal None is a 'tinyint null'
                err = None
                try:
                    res = await conn.query("EXECUTE test_sproc @P1", [None])
                except Exception as e:
                    err = str(e)
                assert (
                    err is not None and f"tinyint is incompatible with {typ[1]}" in err
                )

                # This works as normal None is a 'Typed null'
                res = await conn.query("EXECUTE test_sproc @P1", [typ[0]])
                assert res[0]["status"] == 5

            await conn.simple_query("DROP PROCEDURE IF EXISTS test_sproc")

    except Exception as e:
        pytest.fail(f"Database not available: {e}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_explicit_parameter_value_validation_is_exact_and_redacted(
    test_config: Config,
):
    """Every explicit family rejects the wrong kind, range or length locally."""
    secret = "TypedConversionSecret_MustNotLeak_2026"
    invalid = [
        (1, "BIT", "BIT"),
        (-1, "TINYINT", "TINYINT"),
        (256, "TINYINT", "TINYINT"),
        (-(2**15) - 1, "SMALLINT", "SMALLINT"),
        (2**15, "SMALLINT", "SMALLINT"),
        (-(2**31) - 1, "INT", "INT"),
        (2**31, "INT", "INT"),
        (-(2**63) - 1, "BIGINT", "BIGINT"),
        (2**63, "BIGINT", "BIGINT"),
        (float("nan"), "REAL", "REAL"),
        (float("inf"), "FLOAT(53)", "FLOAT(53)"),
        ("not-a-number", "DECIMAL(9,2)", "DECIMAL(9,2)"),
        (Decimal("10000000"), "DECIMAL(8,2)", "DECIMAL(8,2)"),
        (b"bytes", "CHAR(5)", "CHAR(5)"),
        (secret, "VARCHAR(8)", "VARCHAR(8)"),
        ("😀", "NVARCHAR(1)", "NVARCHAR(1)"),
        ("text", "BINARY(4)", "BINARY(4)"),
        (b"12345", "VARBINARY(4)", "VARBINARY(4)"),
        ("not-a-uuid", "UNIQUEIDENTIFIER", "UNIQUEIDENTIFIER"),
        (
            datetime.datetime(2026, 7, 26, 12, 0),
            "DATE",
            "DATE",
        ),
        (
            datetime.time(12, 0, tzinfo=datetime.timezone.utc),
            "TIME(7)",
            "TIME(7)",
        ),
        (
            datetime.datetime(
                2026,
                7,
                26,
                12,
                0,
                tzinfo=datetime.timezone.utc,
            ),
            "DATETIME",
            "DATETIME",
        ),
        (
            datetime.datetime(
                2026,
                7,
                26,
                12,
                0,
                tzinfo=datetime.timezone.utc,
            ),
            "DATETIME2(7)",
            "DATETIME2(7)",
        ),
        (
            datetime.datetime(2026, 7, 26, 12, 0),
            "DATETIMEOFFSET(7)",
            "DATETIMEOFFSET(7)",
        ),
        (b"<root/>", "XML", "XML"),
    ]

    async with Connection(test_config.connection_string) as conn:
        for value, declaration, canonical in invalid:
            with pytest.raises(ConversionError) as error:
                await conn.query(
                    "SELECT @P1 AS value",
                    [Parameter(value, declaration)],
                )

            assert error.value.parameter_index == 0
            assert error.value.sql_type == canonical
            assert isinstance(error.value.reason, str)
            assert error.value.reason
            assert error.value.retryable is False
            assert secret not in str(error.value)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_explicit_parameter_valid_boundary_values_round_trip(
    test_config: Config,
):
    """Valid exact-width boundaries and identity strings reach SQL unchanged."""
    integer_cases = [
        (False, "BIT", False),
        (0, "TINYINT", 0),
        (255, "TINYINT", 255),
        (-(2**15), "SMALLINT", -(2**15)),
        (2**15 - 1, "SMALLINT", 2**15 - 1),
        (-(2**31), "INT", -(2**31)),
        (2**31 - 1, "INT", 2**31 - 1),
        (-(2**63), "BIGINT", -(2**63)),
        (2**63 - 1, "BIGINT", 2**63 - 1),
    ]
    uuid_value = UUID("12345678-1234-5678-9234-567812345678")

    async with Connection(test_config.connection_string) as conn:
        for value, declaration, expected in integer_cases:
            row = (
                await conn.query(
                    "SELECT @P1 AS value",
                    [Parameter(value, declaration)],
                )
            ).fetchone()
            assert row["value"] == expected

        uuid_row = (
            await conn.query(
                "SELECT @P1 AS value",
                [Parameter(str(uuid_value), "UNIQUEIDENTIFIER")],
            )
        ).fetchone()
        assert uuid_row["value"] == uuid_value
