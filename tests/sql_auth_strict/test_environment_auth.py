from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
import subprocess

from fastmssql import (
    Connection,
    PoolConfig,
    SqlConnectionError,
    SqlError,
    SslConfig,
    Transaction,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import (
    CleanupRegistry,
    quote_identifier,
    scalar,
)


ROOT = Path(__file__).resolve().parents[2]
AUTHENTICATION_ERRORS = (SqlError, SqlConnectionError)
pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def _failure_pool() -> PoolConfig:
    return PoolConfig(
        max_size=1,
        min_idle=1,
        connection_timeout_secs=1,
        retry_connection=False,
    )


async def _create_access_table(
    owner_connection: Connection,
    cleanup_registry: CleanupRegistry,
    unique_sql_name: Callable[[str], str],
) -> str:
    table = quote_identifier(unique_sql_name("sql_auth_access"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, payload NVARCHAR(50))"
    )
    await owner_connection.execute(
        f"INSERT INTO {table} (id, payload) VALUES (1, N'visible')"
    )
    return table


@case("ENV-001")
def test_dedicated_container_identity() -> None:
    identity = subprocess.run(
        [
            "docker",
            "inspect",
            "fastmssql-sql-auth-dev",
            "--format",
            "{{.Name}}|{{.Config.Image}}|{{.State.Status}}|"
            "{{.State.Health.Status}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert identity.stdout.strip() == (
        "/fastmssql-sql-auth-dev|"
        "mcr.microsoft.com/mssql/server:2022-latest|running|healthy"
    )
    port = subprocess.run(
        ["docker", "port", "fastmssql-sql-auth-dev", "1433/tcp"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert port.stdout.strip() == "127.0.0.1:14334"


@case("ENV-002", "ENV-003")
@pytest.mark.asyncio
async def test_server_reports_developer_edition_and_version(
    owner_connection: Connection,
) -> None:
    result = await owner_connection.query(
        """
        SELECT
          CAST(SERVERPROPERTY('Edition') AS NVARCHAR(128)) AS edition,
          CAST(SERVERPROPERTY('ProductVersion') AS NVARCHAR(128)) AS version,
          compatibility_level
        FROM sys.databases
        WHERE name = DB_NAME()
        """
    )
    row = result.fetchone()
    assert row is not None
    assert "Developer" in row["edition"]
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", row["version"])
    assert row["compatibility_level"] >= 160


@case("ENV-004")
@pytest.mark.asyncio
async def test_owner_login_authenticates(
    owner_connection: Connection, sql_auth_config: SqlAuthConfig
) -> None:
    assert (
        await scalar(
            owner_connection,
            "SELECT CAST(SUSER_SNAME() AS NVARCHAR(128))",
        )
        == sql_auth_config.owner_user
    )


@case("ENV-005")
@pytest.mark.asyncio
async def test_exercised_connection_uses_sql_authentication(
    owner_connection: Connection, sql_auth_config: SqlAuthConfig
) -> None:
    authentication_scheme = await scalar(
        owner_connection,
        "SELECT CAST(CONNECTIONPROPERTY('auth_scheme') AS NVARCHAR(32))",
    )
    assert authentication_scheme == "SQL"
    connection_string = sql_auth_config.connection_string(
        sql_auth_config.owner_user,
        sql_auth_config.owner_password,
    )
    assert "Integrated Security" not in connection_string
    assert "Trusted_Connection" not in connection_string
    assert "Authentication=" not in connection_string


@case("ENV-006")
@pytest.mark.asyncio
async def test_provisioning_is_idempotent_and_exact(
    owner_connection: Connection,
) -> None:
    for _ in range(2):
        subprocess.run(
            [str(ROOT / "scripts/sql_auth/provision.sh")],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    database_ids = (
        await owner_connection.query(
            """
            SELECT
              DB_ID(N'fastmssql_validation') AS validation_id,
              DB_ID(N'fastmssql_upstream_regression') AS upstream_id
            """
        )
    ).fetchone()
    assert database_ids is not None
    assert database_ids["validation_id"] is not None
    assert database_ids["upstream_id"] is not None

    users = (
        await owner_connection.query(
            """
            SELECT name
            FROM sys.database_principals
            WHERE name IN (
              N'fastmssql_owner',
              N'fastmssql_readonly',
              N'fastmssql_denied'
            )
            """
        )
    ).rows()
    assert {row["name"] for row in users} == {
        "fastmssql_owner",
        "fastmssql_readonly",
        "fastmssql_denied",
    }

    roles = (
        await owner_connection.query(
            """
            SELECT role_principal.name AS role_name,
                   member_principal.name AS member_name
            FROM sys.database_role_members AS membership
            JOIN sys.database_principals AS role_principal
              ON role_principal.principal_id = membership.role_principal_id
            JOIN sys.database_principals AS member_principal
              ON member_principal.principal_id = membership.member_principal_id
            WHERE member_principal.name IN (
              N'fastmssql_owner',
              N'fastmssql_readonly',
              N'fastmssql_denied'
            )
            """
        )
    ).rows()
    assert {
        (row["role_name"], row["member_name"]) for row in roles
    } == {
        ("db_owner", "fastmssql_owner"),
        ("db_datareader", "fastmssql_readonly"),
    }

    denied_permissions = (
        await owner_connection.query(
            """
            SELECT principal.name AS principal_name,
                   permission.permission_name
            FROM sys.database_permissions AS permission
            JOIN sys.database_principals AS principal
              ON principal.principal_id = permission.grantee_principal_id
            WHERE permission.state_desc = N'DENY'
              AND principal.name IN (
                N'fastmssql_readonly',
                N'fastmssql_denied'
              )
            """
        )
    ).rows()
    actual = {
        (row["principal_name"], row["permission_name"])
        for row in denied_permissions
    }
    write_permissions = {"CREATE TABLE", "DELETE", "INSERT", "UPDATE"}
    assert {
        permission
        for principal, permission in actual
        if principal == "fastmssql_readonly"
    } == write_permissions
    assert {
        permission
        for principal, permission in actual
        if principal == "fastmssql_denied"
    } == write_permissions | {"SELECT"}


@case("ENV-007")
def test_credentials_are_absent_from_tracked_files_and_logs(
    sql_auth_config: SqlAuthConfig,
) -> None:
    passwords = (
        sql_auth_config.sa_password,
        sql_auth_config.owner_password,
        sql_auth_config.readonly_password,
        sql_auth_config.denied_password,
    )
    tracked_scan = subprocess.run(
        [
            "git",
            "grep",
            "--quiet",
            "--fixed-strings",
            "-f",
            "-",
            "--",
        ],
        cwd=ROOT,
        input="\n".join(passwords) + "\n",
        capture_output=True,
        text=True,
    )
    assert tracked_scan.returncode == 1

    logs = subprocess.run(
        ["docker", "logs", "fastmssql-sql-auth-dev"],
        check=True,
        capture_output=True,
        text=True,
    )
    captured = logs.stdout + logs.stderr
    for password in passwords:
        assert password not in captured

    artifact_text = "".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / ".artifacts/sql-auth").glob("*.json"))
    )
    for password in passwords:
        assert password not in artifact_text


@case("AUTH-001")
@pytest.mark.asyncio
async def test_owner_authenticates_with_connection_string(
    sql_auth_config: SqlAuthConfig,
) -> None:
    async with Connection(
        sql_auth_config.connection_string(
            sql_auth_config.owner_user,
            sql_auth_config.owner_password,
        )
    ) as connection:
        assert (
            await scalar(
                connection, "SELECT CAST(SUSER_SNAME() AS NVARCHAR(128))"
            )
            == sql_auth_config.owner_user
        )


@case("AUTH-002")
@pytest.mark.asyncio
async def test_owner_authenticates_with_individual_parameters(
    sql_auth_config: SqlAuthConfig,
) -> None:
    async with Connection(
        server=sql_auth_config.host,
        port=sql_auth_config.port,
        database=sql_auth_config.database,
        username=sql_auth_config.owner_user,
        password=sql_auth_config.owner_password,
        ssl_config=SslConfig.development(),
    ) as connection:
        assert (
            await scalar(
                connection, "SELECT CAST(SUSER_SNAME() AS NVARCHAR(128))"
            )
            == sql_auth_config.owner_user
        )


@case("AUTH-003")
@pytest.mark.asyncio
async def test_password_with_punctuation_and_delimiters(
    sa_connection: Connection,
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    login_name = unique_sql_name("sql_auth_special_login")
    identifier = quote_identifier(login_name)
    special_password = "Fast;Mssql=2026!@#$%^&*()-_+[]{}:,./?"
    await sa_connection.execute(
        f"CREATE LOGIN {identifier} "
        f"WITH PASSWORD = N'{special_password}', CHECK_POLICY = OFF"
    )
    try:
        await sa_connection.execute(
            f"CREATE USER {identifier} FOR LOGIN {identifier}"
        )
        async with Transaction(
            server=sql_auth_config.host,
            port=sql_auth_config.port,
            database=sql_auth_config.database,
            username=login_name,
            password=special_password,
            ssl_config=SslConfig.development(),
        ) as connection:
            assert (
                await scalar(
                    connection,
                    "SELECT CAST(SUSER_SNAME() AS NVARCHAR(128))",
                )
                == login_name
            )
    finally:
        await sa_connection.execute(f"DROP USER IF EXISTS {identifier}")
        await sa_connection.execute(f"DROP LOGIN {identifier}")


@case("AUTH-004")
@pytest.mark.asyncio
async def test_invalid_username_is_rejected(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = Connection(
        sql_auth_config.connection_string(
            "fastmssql_user_does_not_exist",
            sql_auth_config.owner_password,
        ),
        pool_config=_failure_pool(),
    )
    with pytest.raises(AUTHENTICATION_ERRORS) as captured:
        await connection.connect()
    assert sql_auth_config.owner_password not in str(captured.value)


@case("AUTH-005")
@pytest.mark.asyncio
async def test_invalid_password_is_rejected(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = Connection(
        sql_auth_config.connection_string(
            sql_auth_config.owner_user,
            sql_auth_config.owner_password + "-wrong",
        ),
        pool_config=_failure_pool(),
    )
    with pytest.raises(AUTHENTICATION_ERRORS) as captured:
        await connection.connect()
    assert sql_auth_config.owner_password not in str(captured.value)


@case("AUTH-006", "AUTH-007")
def test_missing_sql_authentication_inputs_are_rejected(
    sql_auth_config: SqlAuthConfig,
) -> None:
    with pytest.raises(ValueError, match="password is required"):
        Connection(
            server=sql_auth_config.host,
            port=sql_auth_config.port,
            database=sql_auth_config.database,
            username=sql_auth_config.owner_user,
        )
    with pytest.raises(
        ValueError, match="username/password or azure_credential"
    ):
        Connection(
            server=sql_auth_config.host,
            port=sql_auth_config.port,
            database=sql_auth_config.database,
        )


@case("AUTH-008")
@pytest.mark.asyncio
async def test_nonexistent_database_is_rejected(
    sql_auth_config: SqlAuthConfig,
    unique_sql_name: Callable[[str], str],
) -> None:
    connection = Connection(
        sql_auth_config.connection_string(
            sql_auth_config.owner_user,
            sql_auth_config.owner_password,
            database=unique_sql_name("missing_database"),
        ),
        pool_config=_failure_pool(),
    )
    with pytest.raises(AUTHENTICATION_ERRORS):
        await connection.connect()


@case("AUTH-009")
@pytest.mark.asyncio
async def test_readonly_login_can_select(
    owner_connection: Connection,
    readonly_connection: Connection,
    cleanup_registry: CleanupRegistry,
    unique_sql_name: Callable[[str], str],
) -> None:
    table = await _create_access_table(
        owner_connection, cleanup_registry, unique_sql_name
    )
    result = await readonly_connection.query(
        f"SELECT id, payload FROM {table}"
    )
    row = result.fetchone()
    assert row is not None
    assert (row["id"], row["payload"]) == (1, "visible")


@case("AUTH-010")
@pytest.mark.asyncio
async def test_readonly_login_cannot_write_or_create(
    owner_connection: Connection,
    readonly_connection: Connection,
    cleanup_registry: CleanupRegistry,
    unique_sql_name: Callable[[str], str],
) -> None:
    table = await _create_access_table(
        owner_connection, cleanup_registry, unique_sql_name
    )
    blocked_table = quote_identifier(
        unique_sql_name("sql_auth_readonly_blocked")
    )
    cleanup_registry.add(f"DROP TABLE IF EXISTS {blocked_table}")
    statements = (
        (f"INSERT INTO {table} VALUES (2, N'blocked')", 229),
        (f"UPDATE {table} SET payload = N'blocked' WHERE id = 1", 229),
        (f"DELETE FROM {table} WHERE id = 1", 229),
        (f"CREATE TABLE {blocked_table} (id INT)", 262),
    )
    for statement, expected_code in statements:
        with pytest.raises(SqlError) as captured:
            await readonly_connection.execute(statement)
        assert captured.value.code == expected_code


@case("AUTH-011")
@pytest.mark.asyncio
async def test_denied_login_receives_stable_permission_error(
    owner_connection: Connection,
    denied_connection: Connection,
    cleanup_registry: CleanupRegistry,
    unique_sql_name: Callable[[str], str],
) -> None:
    table = await _create_access_table(
        owner_connection, cleanup_registry, unique_sql_name
    )
    with pytest.raises(SqlError) as captured:
        await denied_connection.query(f"SELECT id FROM {table}")
    assert captured.value.code == 229


@case("AUTH-012")
@pytest.mark.asyncio
async def test_server_identifies_intended_sql_principal(
    owner_connection: Connection,
    sql_auth_config: SqlAuthConfig,
) -> None:
    row = (
        await owner_connection.query(
            """
            SELECT
              CAST(SUSER_SNAME() AS NVARCHAR(128)) AS session_login,
              CAST(ORIGINAL_LOGIN() AS NVARCHAR(128)) AS original_login,
              CAST(USER_NAME() AS NVARCHAR(128)) AS database_user
            """
        )
    ).fetchone()
    assert row is not None
    assert {
        row["session_login"],
        row["original_login"],
        row["database_user"],
    } == {sql_auth_config.owner_user}


@case("AUTH-013")
@pytest.mark.asyncio
async def test_connection_and_errors_do_not_disclose_passwords(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = Connection(
        sql_auth_config.connection_string(
            sql_auth_config.owner_user,
            sql_auth_config.owner_password + "-invalid",
        ),
        pool_config=_failure_pool(),
    )
    with pytest.raises(AUTHENTICATION_ERRORS) as captured:
        await connection.connect()
    rendered = "\n".join(
        (repr(sql_auth_config), repr(connection), repr(captured.value))
    )
    for password in (
        sql_auth_config.sa_password,
        sql_auth_config.owner_password,
        sql_auth_config.readonly_password,
        sql_auth_config.denied_password,
    ):
        assert password not in rendered
