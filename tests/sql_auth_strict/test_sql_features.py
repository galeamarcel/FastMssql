from __future__ import annotations

from collections.abc import Callable

from fastmssql import Connection, PoolConfig, SslConfig
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def _single_session_connection(config: SqlAuthConfig) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=1,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )


@case("SQL-001")
@pytest.mark.asyncio
async def test_parameterized_single_row_select(owner_connection: Connection) -> None:
    result = await owner_connection.query(
        "SELECT @P1 AS integer_value, @P2 AS string_value",
        [42, "strict-sql-auth"],
    )
    row = result.fetchone()
    assert row is not None
    assert row["integer_value"] == 42
    assert row["string_value"] == "strict-sql-auth"
    assert result.len() == 1


@case("SQL-002")
@pytest.mark.asyncio
async def test_empty_result(owner_connection: Connection) -> None:
    result = await owner_connection.query(
        "SELECT CAST(1 AS INT) AS value WHERE 1 = 0"
    )
    assert result.len() == 0
    assert result.fetchone() is None
    assert result.rows() == []


@case("SQL-003")
@pytest.mark.asyncio
async def test_ordered_multirow_result(owner_connection: Connection) -> None:
    result = await owner_connection.query(
        """
        SELECT value
        FROM (VALUES (3), (1), (2)) AS source(value)
        ORDER BY value
        """
    )
    assert [row["value"] for row in result.rows()] == [1, 2, 3]


@case("SQL-004")
@pytest.mark.asyncio
async def test_large_result_set(owner_connection: Connection) -> None:
    result = await owner_connection.query(
        """
        WITH numbers AS (
            SELECT 1 AS value
            UNION ALL
            SELECT value + 1 FROM numbers WHERE value < 5000
        )
        SELECT value FROM numbers ORDER BY value
        OPTION (MAXRECURSION 0)
        """
    )
    rows = result.rows()
    assert len(rows) == 5000
    assert rows[0]["value"] == 1
    assert rows[-1]["value"] == 5000


@case("SQL-005")
@pytest.mark.asyncio
async def test_simple_query_raw_statement(owner_connection: Connection) -> None:
    result = await owner_connection.simple_query(
        "SELECT CAST(73 AS INT) AS raw_value"
    )
    row = result.fetchone()
    assert row is not None
    assert row["raw_value"] == 73


@case("SQL-006", "SQL-007", "SQL-008", "SQL-009")
@pytest.mark.asyncio
async def test_dml_row_counts_and_persisted_state(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_dml"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value NVARCHAR(100))"
    )

    assert (
        await owner_connection.execute(
            f"INSERT INTO {table} (id, value) VALUES (@P1, @P2)",
            [1, "one"],
        )
        == 1
    )
    assert await scalar(owner_connection, f"SELECT value FROM {table}") == "one"

    assert (
        await owner_connection.execute(
            f"UPDATE {table} SET value = @P1 WHERE id = @P2",
            ["updated", 1],
        )
        == 1
    )
    assert (
        await scalar(owner_connection, f"SELECT value FROM {table}")
        == "updated"
    )

    assert (
        await owner_connection.execute(
            f"UPDATE {table} SET value = @P1 WHERE id = @P2",
            ["missing", 99],
        )
        == 0
    )
    assert (
        await owner_connection.execute(
            f"DELETE FROM {table} WHERE id = @P1", [1]
        )
        == 1
    )
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0


@case("SQL-010")
@pytest.mark.asyncio
async def test_ddl_create_alter_and_drop(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_ddl"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")

    await owner_connection.execute(f"CREATE TABLE {table} (id INT NOT NULL)")
    await owner_connection.execute(
        f"ALTER TABLE {table} ADD payload NVARCHAR(50) NULL"
    )
    assert (
        await scalar(
            owner_connection,
            """
            SELECT COUNT(*)
            FROM sys.columns
            WHERE object_id = OBJECT_ID(@P1) AND name = N'payload'
            """,
            [table[1:-1]],
        )
        == 1
    )
    await owner_connection.execute(f"DROP TABLE {table}")
    assert await scalar(owner_connection, "SELECT OBJECT_ID(@P1)", [table[1:-1]]) is None


@case("SQL-011")
@pytest.mark.asyncio
async def test_cte_and_recursive_cte(owner_connection: Connection) -> None:
    ordinary = await owner_connection.query(
        """
        WITH source AS (
            SELECT value FROM (VALUES (1), (2), (3)) AS valueset(value)
        )
        SELECT SUM(value) AS total FROM source
        """
    )
    assert ordinary.fetchone()["total"] == 6

    recursive = await owner_connection.query(
        """
        WITH numbers AS (
            SELECT 1 AS value
            UNION ALL
            SELECT value + 1 FROM numbers WHERE value < 10
        )
        SELECT SUM(value) AS total FROM numbers
        """
    )
    assert recursive.fetchone()["total"] == 55


@case("SQL-012")
@pytest.mark.asyncio
async def test_joins_grouping_windows_and_subqueries(
    owner_connection: Connection,
) -> None:
    result = await owner_connection.query(
        """
        WITH departments AS (
            SELECT * FROM (VALUES (1, N'Alpha'), (2, N'Beta'))
                AS value_set(id, name)
        ),
        employees AS (
            SELECT * FROM (
                VALUES (1, 1, 20), (2, 1, 30), (3, 2, 40)
            ) AS value_set(id, department_id, score)
        ),
        ranked AS (
            SELECT
                d.name,
                e.score,
                SUM(e.score) OVER (PARTITION BY d.id) AS department_total,
                ROW_NUMBER() OVER (
                    PARTITION BY d.id ORDER BY e.score DESC
                ) AS rank_in_department
            FROM departments AS d
            JOIN employees AS e ON e.department_id = d.id
            WHERE e.score >= (SELECT MIN(score) FROM employees)
        )
        SELECT name, department_total, rank_in_department
        FROM ranked
        WHERE rank_in_department = 1
        ORDER BY name
        """
    )
    assert [row.to_dict() for row in result.rows()] == [
        {
            "name": "Alpha",
            "department_total": 50,
            "rank_in_department": 1,
        },
        {
            "name": "Beta",
            "department_total": 40,
            "rank_in_department": 1,
        },
    ]


@case("SQL-013")
@pytest.mark.asyncio
async def test_output_clause(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_output"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value NVARCHAR(30))"
    )
    result = await owner_connection.query(
        f"""
        INSERT INTO {table} (id, value)
        OUTPUT INSERTED.id, INSERTED.value
        VALUES (@P1, @P2)
        """,
        [5, "output-value"],
    )
    row = result.fetchone()
    assert row is not None
    assert row["id"] == 5
    assert row["value"] == "output-value"
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 1


@case("SQL-014")
@pytest.mark.asyncio
async def test_merge_behavior_and_row_count(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_merge"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value NVARCHAR(30))"
    )
    await owner_connection.execute(
        f"INSERT INTO {table} (id, value) VALUES (1, N'old')"
    )

    affected = await owner_connection.execute(
        f"""
        MERGE {table} AS target
        USING (VALUES (1, N'updated'), (2, N'inserted'))
            AS source(id, value)
        ON target.id = source.id
        WHEN MATCHED THEN UPDATE SET value = source.value
        WHEN NOT MATCHED THEN INSERT (id, value)
            VALUES (source.id, source.value);
        """
    )
    assert affected == 2
    rows = await owner_connection.query(
        f"SELECT id, value FROM {table} ORDER BY id"
    )
    assert [row.to_dict() for row in rows.rows()] == [
        {"id": 1, "value": "updated"},
        {"id": 2, "value": "inserted"},
    ]


@case("SQL-015")
@pytest.mark.asyncio
async def test_view_create_query_and_drop(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    view = quote_identifier(unique_sql_name("strict_view"))
    cleanup_registry.add(f"DROP VIEW IF EXISTS {view}")
    await owner_connection.simple_query(
        f"CREATE VIEW {view} AS SELECT CAST(17 AS INT) AS value"
    )
    assert await scalar(owner_connection, f"SELECT value FROM {view}") == 17
    await owner_connection.execute(f"DROP VIEW {view}")
    assert await scalar(owner_connection, "SELECT OBJECT_ID(@P1)", [view[1:-1]]) is None


@case("SQL-016")
@pytest.mark.asyncio
async def test_scalar_and_table_valued_functions(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    scalar_function = quote_identifier(unique_sql_name("strict_scalar_fn"))
    table_function = quote_identifier(unique_sql_name("strict_table_fn"))
    cleanup_registry.add(f"DROP FUNCTION IF EXISTS {table_function}")
    cleanup_registry.add(f"DROP FUNCTION IF EXISTS {scalar_function}")

    await owner_connection.simple_query(
        f"""
        CREATE FUNCTION {scalar_function} (@value INT)
        RETURNS INT
        AS
        BEGIN
            RETURN @value * 2
        END
        """
    )
    await owner_connection.simple_query(
        f"""
        CREATE FUNCTION {table_function} (@value INT)
        RETURNS TABLE
        AS
        RETURN (SELECT @value AS original, @value + 1 AS successor)
        """
    )

    assert (
        await scalar(
            owner_connection,
            f"SELECT dbo.{scalar_function}(@P1)",
            [9],
        )
        == 18
    )
    row = (
        await owner_connection.query(
            f"SELECT original, successor FROM dbo.{table_function}(@P1)",
            [9],
        )
    ).fetchone()
    assert row is not None
    assert row.to_dict() == {"original": 9, "successor": 10}


@case("SQL-017")
@pytest.mark.asyncio
async def test_stored_procedure_input_and_rows(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = quote_identifier(unique_sql_name("strict_input_proc"))
    cleanup_registry.add(f"DROP PROCEDURE IF EXISTS {procedure}")
    await owner_connection.simple_query(
        f"""
        CREATE PROCEDURE {procedure} @value INT
        AS
        BEGIN
            SET NOCOUNT ON;
            SELECT @value AS original, @value * 3 AS tripled;
        END
        """
    )
    row = (
        await owner_connection.query(f"EXEC {procedure} @value = @P1", [7])
    ).fetchone()
    assert row is not None
    assert row.to_dict() == {"original": 7, "tripled": 21}


@case("SQL-018")
@pytest.mark.asyncio
async def test_stored_procedure_return_status(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    procedure = quote_identifier(unique_sql_name("strict_return_proc"))
    cleanup_registry.add(f"DROP PROCEDURE IF EXISTS {procedure}")
    await owner_connection.simple_query(
        f"""
        CREATE PROCEDURE {procedure}
        AS
        BEGIN
            RETURN 37;
        END
        """
    )
    assert (
        await scalar(
            owner_connection,
            f"""
            DECLARE @status INT;
            EXEC @status = {procedure};
            SELECT @status AS return_status;
            """,
        )
        == 37
    )


@case("SQL-019")
@pytest.mark.asyncio
async def test_trigger_side_effects(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    source = quote_identifier(unique_sql_name("strict_trigger_source"))
    audit = quote_identifier(unique_sql_name("strict_trigger_audit"))
    trigger = quote_identifier(unique_sql_name("strict_trigger"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {audit}")
    cleanup_registry.add(f"DROP TABLE IF EXISTS {source}")
    cleanup_registry.add(f"DROP TRIGGER IF EXISTS {trigger}")
    await owner_connection.execute(f"CREATE TABLE {source} (id INT PRIMARY KEY)")
    await owner_connection.execute(
        f"CREATE TABLE {audit} (source_id INT NOT NULL)"
    )
    await owner_connection.simple_query(
        f"""
        CREATE TRIGGER {trigger}
        ON {source}
        AFTER INSERT
        AS
        BEGIN
            SET NOCOUNT ON;
            INSERT INTO {audit} (source_id) SELECT id FROM inserted;
        END
        """
    )

    assert await owner_connection.execute(f"INSERT INTO {source} VALUES (11)") == 1
    assert await scalar(owner_connection, f"SELECT source_id FROM {audit}") == 11


@case("SQL-020")
@pytest.mark.asyncio
async def test_identity_sequence_default_and_computed_columns(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_generated"))
    sequence = quote_identifier(unique_sql_name("strict_sequence"))
    cleanup_registry.add(f"DROP SEQUENCE IF EXISTS {sequence}")
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE SEQUENCE {sequence} AS INT START WITH 100 INCREMENT BY 1"
    )
    await owner_connection.execute(
        f"""
        CREATE TABLE {table} (
            id INT IDENTITY(1, 1) PRIMARY KEY,
            base_value INT NOT NULL CONSTRAINT
                {quote_identifier(unique_sql_name("strict_default"))}
                DEFAULT 7,
            sequence_value INT NOT NULL DEFAULT NEXT VALUE FOR {sequence},
            computed_value AS base_value * 2
        )
        """
    )
    assert await owner_connection.execute(f"INSERT INTO {table} DEFAULT VALUES") == 1
    row = (
        await owner_connection.query(
            f"""
            SELECT id, base_value, sequence_value, computed_value
            FROM {table}
            """
        )
    ).fetchone()
    assert row is not None
    assert row.to_dict() == {
        "id": 1,
        "base_value": 7,
        "sequence_value": 100,
        "computed_value": 14,
    }


@case("SQL-021")
@pytest.mark.asyncio
async def test_local_temp_table_on_single_connection_pool(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _single_session_connection(sql_auth_config)
    async with connection:
        await connection.execute("CREATE TABLE #strict_local (value INT NOT NULL)")
        await connection.execute("INSERT INTO #strict_local VALUES (29)")
        assert await scalar(connection, "SELECT value FROM #strict_local") == 29
        await connection.execute("DROP TABLE #strict_local")


@case("SQL-022")
@pytest.mark.asyncio
async def test_local_temp_table_persists_on_transaction(
    transaction_factory: Callable,
) -> None:
    async with transaction_factory() as transaction:
        await transaction.execute(
            "CREATE TABLE #strict_transaction_local (value INT NOT NULL)"
        )
        await transaction.execute(
            "INSERT INTO #strict_transaction_local VALUES (31)"
        )
        assert (
            await scalar(
                transaction, "SELECT value FROM #strict_transaction_local"
            )
            == 31
        )


@case("SQL-023")
@pytest.mark.asyncio
async def test_multiple_result_sets_return_first_set(
    owner_connection: Connection,
) -> None:
    result = await owner_connection.query(
        "SELECT CAST(1 AS INT) AS first_value; "
        "SELECT CAST(2 AS INT) AS second_value;"
    )
    assert result.len() == 1
    row = result.fetchone()
    assert row is not None
    assert row.to_dict() == {"first_value": 1}


@case("SQL-024")
@pytest.mark.asyncio
async def test_session_set_state_on_single_connection_pool(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _single_session_connection(sql_auth_config)
    async with connection:
        await connection.simple_query("SET NOCOUNT ON")
        assert (
            await scalar(
                connection,
                "SELECT CASE WHEN (@@OPTIONS & 512) = 512 THEN 1 ELSE 0 END",
            )
            == 1
        )
        await connection.simple_query("SET NOCOUNT OFF")
        assert (
            await scalar(
                connection,
                "SELECT CASE WHEN (@@OPTIONS & 512) = 512 THEN 1 ELSE 0 END",
            )
            == 0
        )


@case("SQL-025")
@pytest.mark.asyncio
async def test_comments_multiline_sql_and_trailing_semicolon(
    owner_connection: Connection,
) -> None:
    result = await owner_connection.query(
        """
        -- leading line comment
        SELECT
            CAST(41 AS INT) + 1 AS answer /* inline block comment */
        ;
        """
    )
    assert result.fetchone()["answer"] == 42
