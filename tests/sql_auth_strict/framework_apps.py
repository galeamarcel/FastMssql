from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time

from fastmssql import Connection, PoolConfig, SslConfig, Transaction

from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import IDENTIFIER, scalar


@dataclass
class FrameworkState:
    config: SqlAuthConfig
    application_name: str
    connection: Connection
    loops: list[asyncio.AbstractEventLoop] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        config: SqlAuthConfig,
        *,
        application_name: str,
        max_size: int = 4,
        min_idle: int = 0,
    ) -> FrameworkState:
        if not IDENTIFIER.fullmatch(application_name):
            raise ValueError(
                f"unsafe framework application name {application_name!r}"
            )
        connection = Connection(
            server=config.host,
            port=config.port,
            database=config.database,
            username=config.owner_user,
            password=config.owner_password,
            application_name=application_name,
            ssl_config=SslConfig.development(),
            pool_config=PoolConfig(
                max_size=max_size,
                min_idle=min_idle,
                max_lifetime_secs=None,
                idle_timeout_secs=None,
                connection_timeout_secs=3,
                retry_connection=False,
            ),
        )
        return cls(config, application_name, connection)

    def transaction(self) -> Transaction:
        return Transaction(
            self.config.connection_string(
                self.config.owner_user,
                self.config.owner_password,
                extra=f"Application Name={self.application_name}",
            )
        )


async def session_count(observer: Connection, application_name: str) -> int:
    return await scalar(
        observer,
        """
        SELECT COUNT(*)
        FROM sys.dm_exec_sessions
        WHERE program_name = @P1
          AND session_id <> @@SPID
        """,
        [application_name],
    )


async def sql_request_count(observer: Connection, token: str) -> int:
    return await scalar(
        observer,
        """
        SELECT COUNT(*)
        FROM sys.dm_exec_requests AS request
        CROSS APPLY sys.dm_exec_sql_text(request.sql_handle) AS sql_text
        WHERE request.session_id <> @@SPID
          AND sql_text.text LIKE @P1
        """,
        [f"%{token}%"],
    )


async def wait_for_pool_active(
    connection: Connection,
    *,
    expected: int,
    timeout: float = 3.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stats = await connection.pool_stats()
        if stats["active_connections"] == expected:
            return stats
        await asyncio.sleep(0.02)
    stats = await connection.pool_stats()
    raise AssertionError(
        f"expected {expected} active connection(s), observed {stats}"
    )


async def wait_for_session_count(
    observer: Connection,
    application_name: str,
    *,
    expected: int,
    timeout: float = 4.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await session_count(observer, application_name) == expected:
            return
        await asyncio.sleep(0.05)
    observed = await session_count(observer, application_name)
    raise AssertionError(
        f"expected {expected} SQL session(s) for "
        f"{application_name!r}, observed {observed}"
    )


async def wait_for_sql_request(
    observer: Connection,
    token: str,
    *,
    present: bool,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        observed = await sql_request_count(observer, token)
        if (observed > 0) is present:
            return
        await asyncio.sleep(0.02)
    observed = await sql_request_count(observer, token)
    raise AssertionError(
        f"expected SQL request {token!r} present={present}, "
        f"observed count={observed}"
    )
