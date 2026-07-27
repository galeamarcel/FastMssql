"""
Edge case and error handling tests for Parameter and Parameters classes

Tests various edge cases, error conditions, and boundary scenarios.
"""

import pytest
from conftest import Config

try:
    from fastmssql import (
        Connection,
        ConversionError,
        Parameter,
        Parameters,
        SqlError,
    )
except ImportError:
    pytest.fail("fastmssql not available - run 'maturin develop' first")


class TestParameterEdgeCases:
    """Test edge cases for Parameter class."""

    def test_parameter_with_none_value(self):
        """Test Parameter with None value."""
        param = Parameter(None)
        assert param.value is None
        assert param.sql_type is None
        assert "None" in repr(param)

    def test_parameter_with_empty_string(self):
        """Test Parameter with empty string."""
        param = Parameter("", "VARCHAR")
        assert param.value == ""
        assert param.sql_type == "VARCHAR(8000)"

    def test_parameter_with_zero_values(self):
        """Test Parameter with various zero values."""
        test_cases = [
            (0, "INT", "INT"),
            (0.0, "FLOAT", "FLOAT(53)"),
            (False, "BIT", "BIT"),
        ]

        for value, sql_type, canonical_type in test_cases:
            param = Parameter(value, sql_type)
            assert param.value == value
            assert param.sql_type == canonical_type

    def test_parameter_with_large_values(self):
        """Test Parameter with large values."""
        # Large integer
        large_int = 9223372036854775807  # max int64
        param = Parameter(large_int, "BIGINT")
        assert param.value == large_int

        # Large string
        large_string = "x" * 10000
        param = Parameter(large_string, "NVARCHAR(MAX)")
        assert param.value == large_string
        assert len(param.value) == 10000

    def test_parameter_with_unicode(self):
        """Test Parameter with Unicode strings."""
        unicode_strings = [
            "Hello 世界",
            "Café",
            "🌟⭐✨",
            "Здравствуй мир",
            "مرحبا بالعالم",
        ]

        for unicode_str in unicode_strings:
            param = Parameter(unicode_str, "NVARCHAR")
            assert param.value == unicode_str
            assert param.sql_type == "NVARCHAR(4000)"


class TestParameterSqlTypeGrammar:
    """The SQL declaration surface is closed, canonical and non-executable."""

    @pytest.mark.parametrize(
        ("declaration", "canonical"),
        [
            (" bit ", "BIT"),
            ("tinyint", "TINYINT"),
            ("smallint", "SMALLINT"),
            ("int", "INT"),
            ("bigint", "BIGINT"),
            ("real", "REAL"),
            ("float", "FLOAT(53)"),
            ("float(1)", "FLOAT(1)"),
            ("FLOAT ( 53 )", "FLOAT(53)"),
            ("decimal(1,0)", "DECIMAL(1,0)"),
            ("DECIMAL ( 38 , 38 )", "DECIMAL(38,38)"),
            ("numeric(19,4)", "NUMERIC(19,4)"),
            ("char(1)", "CHAR(1)"),
            ("char(8000)", "CHAR(8000)"),
            ("varchar", "VARCHAR(8000)"),
            ("varchar(1)", "VARCHAR(1)"),
            ("varchar(8000)", "VARCHAR(8000)"),
            ("varchar(max)", "VARCHAR(MAX)"),
            ("nchar(1)", "NCHAR(1)"),
            ("nchar(4000)", "NCHAR(4000)"),
            ("nvarchar", "NVARCHAR(4000)"),
            ("nvarchar(4000)", "NVARCHAR(4000)"),
            ("nvarchar(MAX)", "NVARCHAR(MAX)"),
            ("binary(1)", "BINARY(1)"),
            ("binary(8000)", "BINARY(8000)"),
            ("varbinary", "VARBINARY(8000)"),
            ("varbinary(8000)", "VARBINARY(8000)"),
            ("varbinary(max)", "VARBINARY(MAX)"),
            ("uniqueidentifier", "UNIQUEIDENTIFIER"),
            ("date", "DATE"),
            ("time", "TIME(7)"),
            ("time(0)", "TIME(0)"),
            ("time(7)", "TIME(7)"),
            ("datetime", "DATETIME"),
            ("smalldatetime", "SMALLDATETIME"),
            ("datetime2", "DATETIME2(7)"),
            ("datetime2(0)", "DATETIME2(0)"),
            ("datetime2(7)", "DATETIME2(7)"),
            ("datetimeoffset", "DATETIMEOFFSET(7)"),
            ("datetimeoffset(0)", "DATETIMEOFFSET(0)"),
            ("datetimeoffset(7)", "DATETIMEOFFSET(7)"),
            ("xml", "XML"),
        ],
    )
    def test_closed_grammar_accepts_only_supported_declarations(
        self, declaration, canonical
    ):
        assert Parameter(None, declaration).sql_type == canonical

    @pytest.mark.parametrize(
        "declaration",
        [
            "",
            " ",
            "INT;",
            "INT -- comment",
            "INT/*comment*/",
            "[INT]",
            "dbo.INT",
            "'INT'",
            '"INT"',
            "INT COLLATE SQL_Latin1_General_CP1_CI_AS",
            "INT OUTPUT",
            "VARCHAR(10); SELECT 1",
            "VARCHAR((10))",
            "VARCHAR(10 + 1)",
            "VARCHAR(-1)",
            "VARCHAR(0)",
            "VARCHAR(8001)",
            "VARCHAR(MAX, 1)",
            "NVARCHAR(4001)",
            "CHAR",
            "CHAR(MAX)",
            "NCHAR",
            "NCHAR(MAX)",
            "BINARY",
            "BINARY(MAX)",
            "VARBINARY(8001)",
            "FLOAT(0)",
            "FLOAT(54)",
            "FLOAT(MAX)",
            "DECIMAL",
            "DECIMAL(18)",
            "DECIMAL(0,0)",
            "DECIMAL(39,0)",
            "DECIMAL(10,11)",
            "DECIMAL(10,-1)",
            "NUMERIC(18,2,1)",
            "TIME(-1)",
            "TIME(8)",
            "DATETIME(3)",
            "SMALLDATETIME(0)",
            "DATETIME2(8)",
            "DATETIMEOFFSET(8)",
            "MONEY",
            "SMALLMONEY",
            "TEXT",
            "NTEXT",
            "IMAGE",
            "SQL_VARIANT",
            "GEOGRAPHY",
            "GEOMETRY",
            "HIERARCHYID",
            "ROWVERSION",
            "TABLE",
        ],
    )
    def test_closed_grammar_rejects_malformed_or_unsupported_declarations(
        self, declaration
    ):
        with pytest.raises(ValueError):
            Parameter(None, declaration)

    @pytest.mark.parametrize(
        "factory",
        [
            lambda: Parameter(None, "FLOAT(24)", precision=25),
            lambda: Parameter(None, "DECIMAL(9,2)", precision=10),
            lambda: Parameter(None, "DECIMAL(9,2)", scale=3),
            lambda: Parameter(None, "VARCHAR(10)", length=11),
            lambda: Parameter(None, "VARCHAR(MAX)", length=8000),
            lambda: Parameter(None, "INT", precision=10),
            lambda: Parameter(None, "INT", scale=0),
            lambda: Parameter(None, "INT", length=4),
            lambda: Parameter(None, "DATE", scale=0),
            lambda: Parameter(None, "XML", length="MAX"),
        ],
    )
    def test_conflicting_or_inapplicable_metadata_is_rejected(self, factory):
        with pytest.raises(ValueError):
            factory()

    def test_keyword_metadata_can_complete_declarations(self):
        decimal = Parameter(
            None,
            "DECIMAL",
            precision=19,
            scale=4,
        )
        varchar = Parameter(None, "VARCHAR", length=32)
        binary = Parameter(None, "BINARY", length=16)
        time = Parameter(None, "TIME", scale=3)

        assert decimal.sql_type == "DECIMAL(19,4)"
        assert varchar.sql_type == "VARCHAR(32)"
        assert binary.sql_type == "BINARY(16)"
        assert time.sql_type == "TIME(3)"

    @pytest.mark.parametrize(
        ("declaration", "metadata"),
        [
            ("FLOAT", {"precision": True}),
            ("TIME", {"scale": False}),
            ("FLOAT", {"precision": 1.5}),
            ("TIME", {"scale": "3"}),
            ("FLOAT", {"precision": -1}),
            ("TIME", {"scale": 256}),
        ],
    )
    def test_numeric_metadata_requires_a_plain_unsigned_integer(
        self, declaration, metadata
    ):
        with pytest.raises(ValueError):
            Parameter(None, declaration, **metadata)

    @pytest.mark.parametrize(
        "direction",
        ["", "IN", "INPUT OUTPUT", "INOUT", "RETURN", "SIDEWAYS", 1],
    )
    def test_invalid_directions_are_rejected(self, direction):
        with pytest.raises((TypeError, ValueError)):
            Parameter(None, "INT", direction=direction)

    def test_invalid_declaration_error_does_not_echo_attacker_text(self):
        secret = "TypeGrammarSecret_MustNotLeak_2026"
        declaration = f"INT); SELECT '{secret}' --"

        with pytest.raises(ValueError) as error:
            Parameter(None, declaration)

        assert secret not in str(error.value)

    def test_empty_expanded_non_input_parameter_is_rejected_before_network(self):
        connection = Connection(
            server="127.0.0.1",
            port=1,
            username="not-used",
            password="not-used",
        )
        parameter = Parameter([], "INT", direction="OUTPUT")

        with pytest.raises(ConversionError) as error:
            connection.query("SELECT 1", [parameter])

        assert error.value.parameter_index == 0
        assert error.value.sql_type == "INT"
        assert error.value.reason == "unsupported_direction"
        assert error.value.retryable is False


class TestParametersEdgeCases:
    """Test edge cases for Parameters class."""

    def test_parameters_with_many_positional(self):
        """Test Parameters with many positional arguments."""
        # Create with 20 positional parameters
        values = list(range(20))
        params = Parameters(*values)

        assert len(params) == 20
        assert len(params.positional) == 20
        assert len(params.named) == 0

        result_list = params.to_list()
        assert result_list == values

    def test_parameters_with_many_named(self):
        """Test Parameters with many named parameters."""
        # Create with 15 named parameters
        kwargs = {f"param_{i}": i for i in range(15)}
        params = Parameters(**kwargs)

        assert len(params) == 15
        assert len(params.positional) == 0
        assert len(params.named) == 15

        named = params.named
        for i in range(15):
            assert named[f"param_{i}"].value == i

    def test_parameters_mixed_large(self):
        """Test Parameters with mix of many positional and named."""
        pos_values = list(range(10))
        named_values = {f"named_{i}": i + 100 for i in range(10)}

        params = Parameters(*pos_values, **named_values)

        assert len(params) == 20
        assert len(params.positional) == 10
        assert len(params.named) == 10

    def test_parameters_chaining_many_operations(self):
        """Test long chain of parameter operations."""
        params = Parameters()

        # Chain many add operations
        for i in range(50):
            params = params.add(i)

        # Chain many set operations
        for i in range(25):
            params = params.set(f"named_{i}", i + 1000)

        assert len(params) == 75
        assert len(params.positional) == 50
        assert len(params.named) == 25

    def test_parameters_empty_operations(self):
        """Test Parameters operations on empty object."""
        params = Parameters()

        # Should be empty
        assert len(params) == 0
        assert params.to_list() == []
        assert params.positional == []
        assert params.named == {}

        # Should handle copy operations
        pos_copy = params.positional
        named_copy = params.named
        assert pos_copy == []
        assert named_copy == {}

    def test_parameters_overwrite_named(self):
        """Test overwriting named parameters."""
        params = Parameters(name="original")
        assert params.named["name"].value == "original"

        # Overwrite with set method
        params = params.set("name", "updated")
        assert params.named["name"].value == "updated"
        assert len(params.named) == 1  # Should still be just one


class TestParametersTypeHandling:
    """Test type handling in Parameters."""

    def test_parameters_mixed_parameter_objects(self):
        """Test mixing Parameter objects and raw values."""
        param1 = Parameter(42, "INT")
        param2 = Parameter("test", "VARCHAR")

        # Mix Parameter objects with raw values
        params = Parameters(param1, "raw_string", param2, 3.14)

        assert len(params) == 4

        pos = params.positional
        assert pos[0] is param1  # Should be the same object
        assert pos[1].value == "raw_string"  # Should be wrapped
        assert pos[1].sql_type is None
        assert pos[2] is param2
        assert pos[3].value == 3.14

    def test_parameters_complex_types(self):
        """Test Parameters with complex Python types."""
        import datetime
        from decimal import Decimal

        # Test various complex types
        now = datetime.datetime.now()
        decimal_val = Decimal("123.456")
        bytes_val = b"binary data"

        params = Parameters(now, decimal_val, bytes_val)

        pos = params.positional
        assert pos[0].value == now
        assert pos[1].value == decimal_val
        assert pos[2].value == bytes_val


@pytest.mark.integration
class TestParametersIntegrationEdgeCases:
    """Integration tests for edge cases with database."""

    @pytest.mark.asyncio
    async def test_parameters_with_sql_injection_attempt(self, test_config: Config):
        """Test that parameters properly prevent SQL injection."""
        try:
            async with Connection(test_config.connection_string) as conn:
                # Attempt SQL injection through parameter
                malicious_input = "'; DROP TABLE users; --"

                params = Parameters(malicious_input)

                # This should treat the input as a literal string parameter
                result = await conn.query("SELECT @P1 as user_input", params)

                rows = result.rows() if result.has_rows() else []
                assert len(rows) == 1
                # The malicious input should be returned as-is (safely parameterized)
                assert rows[0]["user_input"] == malicious_input

        except Exception as e:
            pytest.fail(f"Database not available: {e}")

    @pytest.mark.asyncio
    async def test_parameters_with_special_characters(self, test_config: Config):
        """Test parameters with special SQL characters."""
        try:
            async with Connection(test_config.connection_string) as conn:
                special_strings = [
                    "contains'apostrophe",
                    'contains"quote',
                    "contains\\backslash",
                    "contains\nnewline",
                    "contains\ttab",
                    "contains;semicolon",
                    "contains--comment",
                    "contains/*comment*/",
                ]

                for special_str in special_strings:
                    params = Parameters(special_str)

                    result = await conn.query("SELECT @P1 as special_input", params)

                    rows = result.rows() if result.has_rows() else []
                    assert len(rows) == 1
                    assert rows[0]["special_input"] == special_str

        except Exception as e:
            pytest.fail(f"Database not available: {e}")

    @pytest.mark.asyncio
    async def test_parameters_with_very_long_strings(self, test_config: Config):
        """Test parameters with very long strings."""
        try:
            async with Connection(test_config.connection_string) as conn:
                # Create a very long string (but not too long to cause issues)
                long_string = "x" * 4000  # 4KB string

                params = Parameters(long_string)

                result = await conn.query(
                    "SELECT LEN(@P1) as string_length, LEFT(@P1, 10) as string_start",
                    params,
                )

                rows = result.rows() if result.has_rows() else []
                assert len(rows) == 1
                assert rows[0]["string_length"] == 4000
                assert rows[0]["string_start"] == "xxxxxxxxxx"

        except Exception as e:
            pytest.fail(f"Database not available: {e}")

    @pytest.mark.asyncio
    async def test_parameters_with_null_in_different_positions(
        self, test_config: Config
    ):
        """Test NULL parameters in various positions."""
        try:
            async with Connection(test_config.connection_string) as conn:
                # Test NULL in different positions
                test_cases = [
                    (None, "second", "third"),
                    ("first", None, "third"),
                    ("first", "second", None),
                    (None, None, "third"),
                    ("first", None, None),
                    (None, None, None),
                ]

                for case in test_cases:
                    params = Parameters(*case)

                    result = await conn.query(
                        "SELECT @P1 as col1, @P2 as col2, @P3 as col3", params
                    )

                    rows = result.rows() if result.has_rows() else []
                    assert len(rows) == 1
                    row = rows[0]

                    assert row["col1"] == case[0]
                    assert row["col2"] == case[1]
                    assert row["col3"] == case[2]

        except Exception as e:
            pytest.fail(f"Database not available: {e}")

    @pytest.mark.asyncio
    async def test_parameters_mismatch_count(self, test_config: Config):
        """Test error handling when parameter count doesn't match placeholders."""
        try:
            async with Connection(test_config.connection_string) as conn:
                # Too few parameters
                with pytest.raises(Exception):
                    params = Parameters(42)  # Only 1 parameter
                    await conn.query(
                        "SELECT @P1 as col1, @P2 as col2", params
                    )  # 2 placeholders

                # Too many parameters (might be silently ignored by some databases)
                params = Parameters(1, 2, 3)  # 3 parameters
                # This might not raise an error depending on database behavior
                try:
                    result = await conn.query(
                        "SELECT @P1 as col1", params
                    )  # 1 placeholder
                    # If it doesn't error, verify it used the first parameter
                    rows = result.rows() if result.has_rows() else []
                    assert rows[0]["col1"] == 1
                except Exception:
                    # Error is also acceptable for parameter count mismatch
                    pass

        except Exception as e:
            pytest.fail(f"Database not available: {e}")


class TestParameterBoundsChecking:
    """Test parameter count validation against SQL Server limits."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_query_with_many_parameters_within_limit(self, test_config: Config):
        """Query with parameters within SQL Server limit (2,100) should work."""
        try:
            async with Connection(test_config.connection_string) as conn:
                # Create a query with simple parameters within limit
                # Using a WHERE clause with OR conditions
                or_conditions = " OR ".join([f"id = @P{i}" for i in range(1, 51)])
                query = f"SELECT 1 as test WHERE {or_conditions}"
                params = list(range(50))

                # This should work without hitting the parameter limit
                try:
                    result = await conn.query(query, params)
                    assert result is not None
                except SqlError as e:
                    # May fail due to invalid query syntax, but not due to parameter limits
                    assert (
                        "parameter" not in str(e).lower()
                        or "2100" not in str(e).lower()
                    )
        except Exception as e:
            pytest.fail(f"Database not available: {e}")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_query_with_too_many_parameters_fails(self, test_config: Config):
        """Query with parameters exceeding SQL Server limit (2,100) should fail with parameter limit error."""
        try:
            async with Connection(test_config.connection_string) as conn:
                # Create 2101 parameters to exceed the limit
                or_conditions = " OR ".join([f"id = @P{i}" for i in range(1, 2102)])
                query = f"SELECT 1 as test WHERE {or_conditions}"
                params = list(range(2101))

                # Should raise an error about parameter limit
                with pytest.raises(Exception) as exc_info:
                    await conn.query(query, params)

                error_msg = str(exc_info.value).lower()
                # Check that error mentions parameter limit
                assert any(
                    phrase in error_msg
                    for phrase in ["parameter", "limit", "2100", "exceeded"]
                ), f"Expected parameter limit error, got: {exc_info.value}"
        except Exception as e:
            pytest.fail(f"Database not available: {e}")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_parameter_expansion_exceeding_limit_fails(self, test_config: Config):
        """Parameter expansion that results in >2,100 parameters should fail."""
        try:
            async with Connection(test_config.connection_string) as conn:
                # Create parameters with an IN list that expands beyond the limit
                large_in_list = list(range(2101))

                query = "SELECT @P1 as result"

                # Should fail when trying to expand the parameter
                with pytest.raises(Exception) as exc_info:
                    await conn.query(query, [large_in_list])

                error_msg = str(exc_info.value).lower()
                assert any(
                    phrase in error_msg
                    for phrase in ["parameter", "limit", "2100", "exceeded"]
                ), f"Expected parameter limit error, got: {exc_info.value}"
        except Exception as e:
            pytest.fail(f"Database not available: {e}")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_parameter_boundary_exactly_at_limit(self, test_config: Config):
        """Query with exactly 2,100 parameters should fail with proper error."""
        try:
            async with Connection(test_config.connection_string) as conn:
                # Create exactly 2100 parameters
                or_conditions = " OR ".join([f"id = @P{i}" for i in range(1, 2101)])
                query = f"SELECT 1 as test WHERE {or_conditions}"
                params = list(range(2100))

                # Should fail with parameter limit error
                with pytest.raises(Exception) as exc_info:
                    await conn.query(query, params)

                error_msg = str(exc_info.value).lower()
                assert any(
                    phrase in error_msg for phrase in ["parameter", "limit", "2100"]
                ), f"Expected parameter limit error, got: {exc_info.value}"
        except Exception as e:
            pytest.fail(f"Database not available: {e}")


class TestNamedParametersRejected:
    """Finding #14: Parameters.to_list() must raise ValueError for named params.

    SQL Server only supports positional parameters. Named parameters passed via
    Parameters(key=value) or .set() must be rejected with a clear error rather
    than silently discarded.
    """

    def test_to_list_raises_for_named_only(self):
        """to_list() raises ValueError when only named parameters are present."""
        params = Parameters(city="London")
        with pytest.raises(ValueError, match="Named parameters are not supported"):
            params.to_list()

    def test_to_list_raises_for_mixed_positional_and_named(self):
        """to_list() raises ValueError when both positional and named params exist."""
        params = Parameters(42, "hello", city="London")
        with pytest.raises(ValueError, match="Named parameters are not supported"):
            params.to_list()

    def test_to_list_raises_for_set_method(self):
        """to_list() raises ValueError when named param was added via .set()."""
        params = Parameters().add(1).set("key", "value")
        with pytest.raises(ValueError, match="Named parameters are not supported"):
            params.to_list()

    def test_to_list_error_message_includes_param_names(self):
        """ValueError message includes the names of the offending parameters."""
        params = Parameters(user_id=42, city="London")
        with pytest.raises(ValueError) as exc_info:
            params.to_list()
        msg = str(exc_info.value)
        assert "user_id" in msg or "city" in msg

    def test_to_list_succeeds_with_positional_only(self):
        """to_list() still works correctly when there are no named parameters."""
        params = Parameters(1, "hello", 3.14)
        result = params.to_list()
        assert result == [1, "hello", 3.14]

    def test_to_list_succeeds_with_empty_parameters(self):
        """to_list() works on an empty Parameters object (no regression)."""
        params = Parameters()
        assert params.to_list() == []

    def test_len_still_counts_named_params(self):
        """len(Parameters) still reflects named params even though to_list() rejects them."""
        params = Parameters(1, 2, city="London")
        assert len(params) == 3

    def test_named_dict_accessible_before_to_list(self):
        """The .named property is still readable even though to_list() will reject them."""
        params = Parameters(city="London", country="UK")
        assert len(params.named) == 2
        assert params.named["city"].value == "London"
        assert params.named["country"].value == "UK"
