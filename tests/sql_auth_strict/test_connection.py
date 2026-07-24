from __future__ import annotations

from collections.abc import Callable

from fastmssql import (
    ApplicationIntent,
    Connection,
    PoolConfig,
    SqlConnectionError,
    SslConfig,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import scalar


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def _small_pool() -> PoolConfig:
    return PoolConfig(
        max_size=2,
        min_idle=1,
        max_lifetime_secs=None,
        idle_timeout_secs=None,
        connection_timeout_secs=2,
        retry_connection=False,
    )


def _individual_connection(
    config: SqlAuthConfig,
    *,
    pool_config: PoolConfig | None = None,
    application_name: str | None = None,
    application_intent: ApplicationIntent | str | None = None,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=config.owner_user,
        password=config.owner_password,
        ssl_config=SslConfig.development(),
        pool_config=pool_config or _small_pool(),
        application_name=application_name,
        application_intent=application_intent,
    )


@case("CONN-001")
@pytest.mark.asyncio
async def test_connection_string_parses_host_and_port(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = Connection(
        sql_auth_config.connection_string(
            sql_auth_config.owner_user,
            sql_auth_config.owner_password,
        ),
        pool_config=_small_pool(),
    )
    async with connection:
        assert await scalar(connection, "SELECT 1") == 1


@case("CONN-002")
@pytest.mark.asyncio
async def test_individual_connection_parameters(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    async with connection:
        row = (
            await connection.query(
                """
                SELECT
                  CAST(SERVERPROPERTY('ServerName') AS NVARCHAR(128)) AS server,
                  DB_NAME() AS database_name,
                  CAST(SUSER_SNAME() AS NVARCHAR(128)) AS login_name
                """
            )
        ).fetchone()
        assert row is not None
        assert row["database_name"] == sql_auth_config.database
        assert row["login_name"] == sql_auth_config.owner_user


@case("CONN-003")
@pytest.mark.asyncio
async def test_connection_string_takes_precedence(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = Connection(
        sql_auth_config.connection_string(
            sql_auth_config.owner_user,
            sql_auth_config.owner_password,
        ),
        server="127.0.0.1",
        port=1,
        database="ignored_database",
        username="ignored_user",
        password="ignored_password",
        pool_config=_small_pool(),
    )
    async with connection:
        row = (
            await connection.query(
                """
                SELECT DB_NAME() AS database_name,
                       CAST(SUSER_SNAME() AS NVARCHAR(128)) AS login_name
                """
            )
        ).fetchone()
        assert row is not None
        assert row["database_name"] == sql_auth_config.database
        assert row["login_name"] == sql_auth_config.owner_user


@case("CONN-004")
@pytest.mark.asyncio
async def test_application_name_is_visible(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    application_name = unique_sql_name("fastmssql_app")
    connection = _individual_connection(
        sql_auth_config, application_name=application_name
    )
    async with connection:
        assert await scalar(connection, "SELECT APP_NAME()") == application_name


@case("CONN-005")
@pytest.mark.asyncio
async def test_readwrite_application_intent(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(
        sql_auth_config,
        application_intent=ApplicationIntent.READ_WRITE,
    )
    async with connection:
        assert await scalar(connection, "SELECT 1") == 1
        await connection.execute(
            """
            DECLARE @probe TABLE (id INT PRIMARY KEY);
            INSERT INTO @probe VALUES (1);
            """
        )


@case("CONN-006")
@pytest.mark.asyncio
async def test_readonly_intent_on_standalone_server(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(
        sql_auth_config,
        application_intent=ApplicationIntent.READ_ONLY,
    )
    async with connection:
        updateability = await scalar(
            connection,
            """
            SELECT CAST(
              DATABASEPROPERTYEX(DB_NAME(), 'Updateability')
              AS NVARCHAR(32)
            )
            """,
        )
        assert updateability == "READ_WRITE"
        await connection.execute(
            """
            DECLARE @probe TABLE (id INT PRIMARY KEY);
            INSERT INTO @probe VALUES (1);
            """
        )


@case("CONN-007")
def test_invalid_application_intent_fails_before_network_io(
    sql_auth_config: SqlAuthConfig,
) -> None:
    with pytest.raises(ValueError, match="Invalid application_intent"):
        Connection(
            server="host-that-must-not-be-resolved.invalid",
            port=1,
            database=sql_auth_config.database,
            username=sql_auth_config.owner_user,
            password=sql_auth_config.owner_password,
            application_intent="not-an-intent",
        )


@case("CONN-008")
def test_malformed_connection_string_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid connection string"):
        Connection("not a valid ADO connection string")


@case("CONN-009")
@pytest.mark.asyncio
async def test_closed_local_port_is_rejected() -> None:
    connection = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="fastmssql_closed_port",
        password="not-a-credential",
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=1,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
    )
    with pytest.raises(SqlConnectionError):
        await connection.connect()


@case("CONN-010")
@pytest.mark.asyncio
async def test_first_query_initializes_connection_lazily(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.is_connected() is False
    assert await scalar(connection, "SELECT 1") == 1
    assert await connection.is_connected() is True
    assert await connection.disconnect() is True


@case("CONN-011")
@pytest.mark.asyncio
async def test_explicit_connect_is_idempotent(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.connect() is True
    first_stats = await connection.pool_stats()
    assert await connection.connect() is True
    assert await connection.pool_stats() == first_stats
    assert await connection.disconnect() is True


@case("CONN-012")
@pytest.mark.asyncio
async def test_disconnect_before_and_after_connection(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.disconnect() is False
    assert await connection.connect() is True
    assert await connection.disconnect() is True
    assert await connection.disconnect() is False


@case("CONN-013")
@pytest.mark.asyncio
async def test_reconnect_after_disconnect(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.connect() is True
    assert await scalar(connection, "SELECT 1") == 1
    assert await connection.disconnect() is True
    assert await connection.connect() is True
    assert await scalar(connection, "SELECT 2") == 2
    assert await connection.disconnect() is True


@case("CONN-014")
@pytest.mark.asyncio
async def test_context_manager_normal_exit(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    async with connection:
        assert await connection.is_connected() is True
        assert await scalar(connection, "SELECT 1") == 1
    assert await connection.is_connected() is False


@case("CONN-015")
@pytest.mark.asyncio
async def test_context_manager_exceptional_exit(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    with pytest.raises(RuntimeError, match="intentional context failure"):
        async with connection:
            assert await connection.is_connected() is True
            raise RuntimeError("intentional context failure")
    assert await connection.is_connected() is False


@case("CONN-016")
@pytest.mark.asyncio
async def test_same_wrapper_supports_sequential_contexts(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    async with connection:
        assert await scalar(connection, "SELECT 1") == 1
    assert await connection.is_connected() is False
    async with connection:
        assert await scalar(connection, "SELECT 2") == 2
    assert await connection.is_connected() is False


@case("CONN-017")
@pytest.mark.asyncio
async def test_nested_context_resets_then_lazily_reconnects(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    async with connection:
        assert await connection.is_connected() is True
        async with connection:
            assert await scalar(connection, "SELECT 1") == 1
        assert await connection.is_connected() is False
        assert await scalar(connection, "SELECT 2") == 2
        assert await connection.is_connected() is True
    assert await connection.is_connected() is False


@case("CONN-018")
@pytest.mark.asyncio
async def test_is_connected_state_transitions(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.is_connected() is False
    assert await connection.connect() is True
    assert await connection.is_connected() is True
    assert await connection.disconnect() is True
    assert await connection.is_connected() is False


@case("CONN-019")
@pytest.mark.asyncio
async def test_pool_stats_keys_and_invariants(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _individual_connection(sql_auth_config)
    assert await connection.connect() is True
    stats = await connection.pool_stats()
    assert set(stats) == {
        "connected",
        "connections",
        "idle_connections",
        "active_connections",
        "max_size",
        "min_idle",
    }
    assert stats["connected"] is True
    assert 0 <= stats["idle_connections"] <= stats["connections"]
    assert stats["active_connections"] == (
        stats["connections"] - stats["idle_connections"]
    )
    assert stats["connections"] <= stats["max_size"]
    assert stats["max_size"] == 2
    assert stats["min_idle"] == 1
    assert await connection.disconnect() is True
