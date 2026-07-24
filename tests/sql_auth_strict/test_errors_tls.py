from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from fastmssql import (
    Connection,
    ConversionError,
    EncryptionLevel,
    PoolConfig,
    ProtocolError,
    SqlConnectionError,
    SqlError,
    SslConfig,
    TlsError,
    Transaction,
)
import pytest

from sql_auth_strict.cases import case
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry, quote_identifier, scalar


pytestmark = [pytest.mark.sql_auth_strict, pytest.mark.integration]


def _connection(
    config: SqlAuthConfig,
    ssl_config: SslConfig,
    *,
    user: str | None = None,
    password: str | None = None,
) -> Connection:
    return Connection(
        server=config.host,
        port=config.port,
        database=config.database,
        username=user or config.owner_user,
        password=password or config.owner_password,
        ssl_config=ssl_config,
        pool_config=PoolConfig(
            max_size=1,
            min_idle=1,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )


def _assert_sql_error(
    error: SqlError,
    expected_codes: set[int],
) -> None:
    assert type(error.code) is int
    assert error.code in expected_codes
    assert type(error.message) is str
    assert error.message
    assert type(error.state) is int
    assert 0 <= error.state <= 255
    assert str(error) == error.message


async def _session_security(connection: Connection) -> tuple[str, str]:
    row = (
        await connection.query(
            """
            SELECT
                CAST(SUSER_SNAME() AS NVARCHAR(128)) AS principal,
                CAST(encrypt_option AS NVARCHAR(10)) AS encrypt_option
            FROM sys.dm_exec_connections
            WHERE session_id = @@SPID
            """
        )
    ).fetchone()
    return row["principal"], row["encrypt_option"]


@case("ERR-001")
@pytest.mark.asyncio
async def test_syntax_error_taxonomy(owner_connection: Connection) -> None:
    with pytest.raises(SqlError) as captured:
        await owner_connection.query("SELEKT strict syntax failure")
    _assert_sql_error(captured.value, {102, 156})


@case("ERR-002")
@pytest.mark.asyncio
async def test_missing_object_error(owner_connection: Connection) -> None:
    with pytest.raises(SqlError) as captured:
        await owner_connection.query(
            "SELECT * FROM dbo.strict_object_that_does_not_exist"
        )
    _assert_sql_error(captured.value, {208})
    assert "strict_object_that_does_not_exist" in captured.value.message


@case("ERR-003")
@pytest.mark.asyncio
async def test_duplicate_key_error(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_error_duplicate"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    await owner_connection.execute(f"INSERT INTO {table} VALUES (1)")
    with pytest.raises(SqlError) as captured:
        await owner_connection.execute(f"INSERT INTO {table} VALUES (1)")
    _assert_sql_error(captured.value, {2601, 2627})


@case("ERR-004")
@pytest.mark.asyncio
async def test_foreign_key_check_and_not_null_errors(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    parent = quote_identifier(unique_sql_name("strict_error_parent"))
    child = quote_identifier(unique_sql_name("strict_error_child"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {parent}")
    cleanup_registry.add(f"DROP TABLE IF EXISTS {child}")
    await owner_connection.execute(f"CREATE TABLE {parent} (id INT PRIMARY KEY)")
    await owner_connection.execute(
        f"""
        CREATE TABLE {child} (
            id INT PRIMARY KEY,
            parent_id INT NOT NULL REFERENCES {parent}(id),
            checked_value INT NOT NULL CHECK (checked_value >= 0)
        )
        """
    )
    await owner_connection.execute(f"INSERT INTO {parent} VALUES (1)")

    with pytest.raises(SqlError) as foreign_key:
        await owner_connection.execute(
            f"INSERT INTO {child} VALUES (1, 99, 0)"
        )
    _assert_sql_error(foreign_key.value, {547})

    with pytest.raises(SqlError) as check:
        await owner_connection.execute(
            f"INSERT INTO {child} VALUES (2, 1, -1)"
        )
    _assert_sql_error(check.value, {547})

    with pytest.raises(SqlError) as not_null:
        await owner_connection.execute(
            f"INSERT INTO {child} VALUES (3, 1, NULL)"
        )
    _assert_sql_error(not_null.value, {515})


@case("ERR-005")
@pytest.mark.asyncio
async def test_truncation_and_conversion_errors(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_error_truncate"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value NVARCHAR(3))"
    )
    with pytest.raises(SqlError) as truncation:
        await owner_connection.execute(
            f"INSERT INTO {table} VALUES (1, @P1)", ["too-long"]
        )
    _assert_sql_error(truncation.value, {8152, 2628})

    with pytest.raises(SqlError) as conversion:
        await owner_connection.query("SELECT CAST(N'not-an-int' AS INT)")
    _assert_sql_error(conversion.value, {245})


@case("ERR-006")
@pytest.mark.asyncio
async def test_arithmetic_errors(owner_connection: Connection) -> None:
    with pytest.raises(SqlError) as divide_by_zero:
        await owner_connection.query("SELECT 1 / 0")
    _assert_sql_error(divide_by_zero.value, {8134})

    with pytest.raises(SqlError) as overflow:
        await owner_connection.query(
            "SELECT CAST(2147483648 AS INT)"
        )
    _assert_sql_error(overflow.value, {8115})


@case("ERR-007")
@pytest.mark.asyncio
async def test_deadlock_victim_error_taxonomy(
    owner_connection: Connection,
    transaction_factory: Callable,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_error_deadlock"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    await owner_connection.execute(
        f"INSERT INTO {table} VALUES (1, 0), (2, 0)"
    )
    first = transaction_factory()
    second = transaction_factory()
    try:
        await first.begin()
        await second.begin()
        await first.execute(f"UPDATE {table} SET value = 1 WHERE id = 1")
        await second.execute(f"UPDATE {table} SET value = 2 WHERE id = 2")
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                first.execute(f"UPDATE {table} SET value = 1 WHERE id = 2"),
                second.execute(f"UPDATE {table} SET value = 2 WHERE id = 1"),
                return_exceptions=True,
            ),
            timeout=10.0,
        )
        errors = [
            outcome
            for outcome in outcomes
            if isinstance(outcome, BaseException)
        ]
        assert len(errors) == 1
        assert isinstance(errors[0], SqlError)
        _assert_sql_error(errors[0], {1205})
    finally:
        await first.close()
        await second.close()


@case("ERR-008")
@pytest.mark.asyncio
async def test_permission_denial_errors(
    owner_connection: Connection,
    readonly_connection: Connection,
    denied_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_error_permission"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY)"
    )
    with pytest.raises(SqlError) as readonly_error:
        await readonly_connection.execute(f"INSERT INTO {table} VALUES (1)")
    _assert_sql_error(readonly_error.value, {229})

    with pytest.raises(SqlError) as denied_error:
        await denied_connection.query(f"SELECT * FROM {table}")
    _assert_sql_error(denied_error.value, {229})


@case("ERR-009")
@pytest.mark.asyncio
async def test_sql_error_exposes_code_message_and_state(
    owner_connection: Connection,
) -> None:
    with pytest.raises(SqlError) as captured:
        await owner_connection.query(
            "SELECT * FROM dbo.strict_error_attribute_probe"
        )
    _assert_sql_error(captured.value, {208})
    assert captured.value.args == (captured.value.message,)


@case("ERR-010")
@pytest.mark.asyncio
async def test_connection_error_exposes_safe_host_and_port() -> None:
    password = "NotARealCredential_2026!"
    connection = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="strict_closed_port",
        password=password,
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=1,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
    )
    with pytest.raises(SqlConnectionError) as pooled:
        await connection.connect()

    with pytest.raises(SqlConnectionError) as batch:
        await connection.execute_batch([("SELECT 1", None)])

    transaction = Transaction(
        server="127.0.0.1",
        port=1,
        database="master",
        username="strict_closed_port",
        password=password,
        ssl_config=SslConfig.development(),
    )
    try:
        with pytest.raises(SqlConnectionError) as dedicated:
            await transaction.begin()
    finally:
        await transaction.close()

    for captured in (pooled, batch, dedicated):
        text = str(captured.value)
        assert "127.0.0.1" in text
        assert "1" in text
        assert password not in text
        assert isinstance(captured.value.message, str)
        assert "127.0.0.1:1" in captured.value.message


@case("ERR-011", "TLS-005")
@pytest.mark.asyncio
async def test_untrusted_server_certificate_uses_tls_error(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(
        sql_auth_config,
        SslConfig(
            encryption_level=EncryptionLevel.REQUIRED,
            trust_server_certificate=False,
        ),
    )
    try:
        with pytest.raises(TlsError) as captured:
            await connection.connect()
        assert isinstance(captured.value.message, str)
        assert captured.value.message
    finally:
        await connection.disconnect()


@case("ERR-012")
def test_protocol_and_conversion_error_classes_are_meaningful() -> None:
    protocol = ProtocolError("strict protocol probe")
    conversion = ConversionError("strict conversion probe")
    assert isinstance(protocol, Exception)
    assert isinstance(conversion, Exception)
    assert type(protocol) is ProtocolError
    assert type(conversion) is ConversionError
    assert str(protocol) == "strict protocol probe"
    assert str(conversion) == "strict conversion probe"
    assert not isinstance(protocol, ConversionError)
    assert not isinstance(conversion, ProtocolError)


@case("ERR-013")
@pytest.mark.asyncio
async def test_sql_error_does_not_poison_pool(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, SslConfig.development())
    try:
        before = await scalar(connection, "SELECT @@SPID")
        with pytest.raises(SqlError):
            await connection.query(
                "SELECT * FROM dbo.strict_error_pool_missing"
            )
        after = await scalar(connection, "SELECT @@SPID")
        assert after == before
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["idle_connections"] == 1
    finally:
        await connection.disconnect()


@case("ERR-014")
@pytest.mark.asyncio
async def test_batch_error_preserves_original_sql_error(
    owner_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    table = quote_identifier(unique_sql_name("strict_error_batch"))
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY)")
    with pytest.raises(SqlError) as captured:
        await owner_connection.execute_batch(
            [
                (f"INSERT INTO {table} VALUES (1)", None),
                (f"INSERT INTO {table} VALUES (1)", None),
            ]
        )
    _assert_sql_error(captured.value, {2601, 2627})
    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0


@case("ERR-015")
@pytest.mark.asyncio
async def test_credentials_absent_from_error_strings(
    owner_connection: Connection,
    denied_connection: Connection,
    sql_auth_config: SqlAuthConfig,
) -> None:
    errors: list[BaseException] = []
    with pytest.raises(SqlError) as syntax:
        await owner_connection.query("SELEKT credential redaction probe")
    errors.append(syntax.value)

    with pytest.raises(SqlError) as permission:
        await denied_connection.query("SELECT * FROM sys.objects")
    errors.append(permission.value)

    wrong_password = f"{sql_auth_config.owner_password}_wrong"
    failed_login = _connection(
        sql_auth_config,
        SslConfig.development(),
        password=wrong_password,
    )
    try:
        with pytest.raises((SqlError, SqlConnectionError)) as login:
            await failed_login.connect()
        if isinstance(login.value, SqlError):
            assert login.value.code == 18456
        errors.append(login.value)
    finally:
        await failed_login.disconnect()

    secrets = {
        sql_auth_config.sa_password,
        sql_auth_config.owner_password,
        sql_auth_config.readonly_password,
        sql_auth_config.denied_password,
        wrong_password,
    }
    assert all(secrets)
    for error in errors:
        rendered = " | ".join(
            (
                str(error),
                repr(error),
                str(getattr(error, "message", "")),
            )
        )
        for secret in secrets:
            assert secret not in rendered


@case("TLS-001", "TLS-003")
@pytest.mark.asyncio
async def test_required_encryption_with_trusted_development_certificate(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection = _connection(sql_auth_config, SslConfig.development())
    try:
        principal, encrypt_option = await _session_security(connection)
        assert principal == sql_auth_config.owner_user
        assert encrypt_option == "TRUE"
    finally:
        await connection.disconnect()


@case("TLS-002")
@pytest.mark.asyncio
async def test_connection_string_encrypt_and_trust_settings(
    sql_auth_config: SqlAuthConfig,
) -> None:
    connection_string = (
        f"Server={sql_auth_config.host},{sql_auth_config.port};"
        f"Database={sql_auth_config.database};"
        f"User Id={sql_auth_config.owner_user};"
        f"Password={sql_auth_config.owner_password};"
        "Encrypt=True;TrustServerCertificate=True"
    )
    connection = Connection(
        connection_string,
        pool_config=PoolConfig(
            max_size=1,
            min_idle=1,
            connection_timeout_secs=2,
            retry_connection=False,
        ),
    )
    try:
        principal, encrypt_option = await _session_security(connection)
        assert principal == sql_auth_config.owner_user
        assert encrypt_option == "TRUE"
    finally:
        await connection.disconnect()


@case("TLS-004")
@pytest.mark.asyncio
async def test_login_only_and_disabled_match_server_policy(
    sql_auth_config: SqlAuthConfig,
) -> None:
    login_only_development = SslConfig(
        encryption_level=EncryptionLevel.LOGIN_ONLY,
        trust_server_certificate=True,
    )
    for ssl_config in (login_only_development, SslConfig.disabled()):
        connection = _connection(sql_auth_config, ssl_config)
        try:
            principal, encrypt_option = await _session_security(connection)
            assert principal == sql_auth_config.owner_user
            assert encrypt_option == "FALSE"
        finally:
            await connection.disconnect()


@case("TLS-006")
def test_invalid_ca_path_content_and_extension(tmp_path: Path) -> None:
    missing = tmp_path / "missing.pem"
    with pytest.raises(ValueError, match="cannot be opened"):
        SslConfig.with_ca_certificate(str(missing))

    invalid_content = tmp_path / "invalid.pem"
    invalid_content.write_text("not a certificate\n", encoding="utf-8")
    with pytest.raises(ValueError, match="valid PEM or DER"):
        SslConfig.with_ca_certificate(str(invalid_content))

    invalid_extension = tmp_path / "certificate.txt"
    invalid_extension.write_text(
        "-----BEGIN CERTIFICATE-----\ninvalid\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must have a"):
        SslConfig.with_ca_certificate(str(invalid_extension))


@case("TLS-007")
def test_trust_options_are_mutually_exclusive(tmp_path: Path) -> None:
    certificate = tmp_path / "exclusive.pem"
    certificate.write_text(
        "-----BEGIN CERTIFICATE-----\nplaceholder\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="mutually exclusive"):
        SslConfig(
            encryption_level=EncryptionLevel.REQUIRED,
            trust_server_certificate=True,
            ca_certificate_path=str(certificate),
        )


@case("TLS-008")
@pytest.mark.asyncio
async def test_tls_settings_do_not_change_sql_principal(
    sql_auth_config: SqlAuthConfig,
) -> None:
    login_only_development = SslConfig(
        encryption_level=EncryptionLevel.LOGIN_ONLY,
        trust_server_certificate=True,
    )
    configurations = [
        (SslConfig.development(), "TRUE"),
        (login_only_development, "FALSE"),
        (SslConfig.disabled(), "FALSE"),
    ]
    for ssl_config, expected_encryption in configurations:
        connection = _connection(sql_auth_config, ssl_config)
        try:
            principal, encrypt_option = await _session_security(connection)
            assert principal == sql_auth_config.owner_user
            assert encrypt_option == expected_encryption
        finally:
            await connection.disconnect()
