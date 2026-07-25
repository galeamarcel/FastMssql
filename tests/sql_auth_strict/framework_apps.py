from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import logging
import time

from asgiref.wsgi import WsgiToAsgi
import fastmssql
from fastmssql import Connection, PoolConfig, SslConfig, Transaction
from fastapi import FastAPI
from flask import Flask, jsonify, request
from pydantic import BaseModel
from starlette.responses import JSONResponse, PlainTextResponse

from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import IDENTIFIER, scalar


LOGGER = logging.getLogger("fastmssql.framework")


class ItemPayload(BaseModel):
    value: str


class IntentionalRollback(RuntimeError):
    pass


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
        pool_config: PoolConfig | None = None,
        timeout_config=None,
    ) -> FrameworkState:
        if not IDENTIFIER.fullmatch(application_name):
            raise ValueError(
                f"unsafe framework application name {application_name!r}"
            )
        connection_kwargs = {}
        if timeout_config is not None:
            connection_kwargs["timeout_config"] = timeout_config
        connection = Connection(
            server=config.host,
            port=config.port,
            database=config.database,
            username=config.owner_user,
            password=config.owner_password,
            application_name=application_name,
            ssl_config=SslConfig.development(),
            pool_config=(
                pool_config
                if pool_config is not None
                else PoolConfig(
                    max_size=max_size,
                    min_idle=min_idle,
                    max_lifetime_secs=None,
                    idle_timeout_secs=None,
                    connection_timeout_secs=3,
                    retry_connection=False,
                )
            ),
            **connection_kwargs,
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


def _timeout_payload(error: BaseException) -> dict[str, object]:
    timeout_type = getattr(fastmssql, "OperationTimeoutError", None)
    if timeout_type is None or not isinstance(error, timeout_type):
        raise error
    return {
        "type": type(error).__name__,
        "phase": error.phase,
        "operation": error.operation,
        "retryable": error.retryable,
        "connection_discarded": error.connection_discarded,
        "outcome_unknown": error.outcome_unknown,
    }


async def _timeout_route_value(
    state: FrameworkState,
    value: int,
    *,
    wait: bool,
) -> dict[str, object]:
    delay = "WAITFOR DELAY '00:00:00.250';" if wait else ""
    result = await state.connection.query(
        f"""
        /* {state.application_name}_timeout_route */
        {delay}
        SELECT
            @P1 AS value,
            CONVERT(NVARCHAR(36), connection_id) AS connection_id
        FROM sys.dm_exec_connections
        WHERE session_id = @@SPID
        """,
        [value],
    )
    row = result.fetchone()
    assert row is not None
    return {
        "value": int(row["value"]),
        "connection_id": str(row["connection_id"]),
    }


def create_fastapi_app(
    state: FrameworkState,
    table_sql: str,
    *,
    safe_errors: bool = False,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await state.connection.connect()
            app.state.fastmssql = state
            yield
        finally:
            await state.connection.disconnect()

    app = FastAPI(lifespan=lifespan)
    delays = {
        "none": "",
        "short": "WAITFOR DELAY '00:00:01';",
        "long": "WAITFOR DELAY '00:00:05';",
    }

    @app.get("/principal")
    async def principal():
        return {
            "principal": await scalar(
                state.connection,
                "SELECT CAST(SUSER_SNAME() AS NVARCHAR(128))",
            )
        }

    @app.get("/wait/{value}")
    async def wait_value(value: int, profile: str = "short"):
        prefix = delays.get(profile)
        if prefix is None:
            raise ValueError(f"invalid delay profile {profile!r}")
        returned = await scalar(
            state.connection,
            f"/* {state.application_name} */ {prefix} SELECT @P1",
            [value],
        )
        return {"value": returned}

    @app.get("/timeout/{value}")
    async def timeout_value(value: int, profile: str = "immediate"):
        if profile not in {"immediate", "wait"}:
            raise ValueError(f"invalid timeout profile {profile!r}")
        try:
            return await _timeout_route_value(
                state,
                value,
                wait=profile == "wait",
            )
        except fastmssql.SqlConnectionError as error:
            return JSONResponse(
                _timeout_payload(error),
                status_code=504,
            )

    @app.post("/items/{item_id}", status_code=201)
    async def write_item(item_id: int, payload: ItemPayload):
        affected = await state.connection.execute(
            f"INSERT INTO {table_sql} (id, value) VALUES (@P1, @P2)",
            [item_id, payload.value],
        )
        return {"affected": affected}

    @app.get("/items/{item_id}")
    async def read_item(item_id: int):
        row = (
            await state.connection.query(
                f"SELECT id, value FROM {table_sql} WHERE id = @P1",
                [item_id],
            )
        ).fetchone()
        return {"id": row["id"], "value": row["value"]}

    @app.post("/transaction/{item_id}", status_code=201)
    async def transaction_item(item_id: int, fail: bool = False):
        async with state.transaction() as transaction:
            await transaction.execute(
                f"INSERT INTO {table_sql} (id, value) VALUES (@P1, @P2)",
                [item_id, "transaction"],
            )
            if fail:
                raise IntentionalRollback("intentional rollback")
        return {"committed": True}

    @app.get("/sql-error")
    async def sql_error():
        await state.connection.query(
            "SELECT * FROM dbo.strict_framework_missing_table"
        )
        raise AssertionError("unreachable after missing-table query")

    if safe_errors:

        @app.exception_handler(Exception)
        async def safe_500(request, error):
            del request
            if hasattr(error, "code") and hasattr(error, "state"):
                LOGGER.error(
                    "FastMssql request failed: type=%s code=%s state=%s",
                    type(error).__name__,
                    error.code,
                    error.state,
                )
            return PlainTextResponse(
                "Internal Server Error",
                status_code=500,
            )

    return app


def create_flask_app(state: FrameworkState) -> Flask:
    app = Flask(__name__)
    delays = {
        "none": "",
        "short": "WAITFOR DELAY '00:00:01';",
        "long": "WAITFOR DELAY '00:00:02';",
    }

    async def delayed(value: int, profile: str = "short") -> int:
        prefix = delays[profile]
        return await scalar(
            state.connection,
            f"/* {state.application_name} */ {prefix} SELECT @P1",
            [value],
        )

    @app.get("/principal")
    async def principal():
        value = await scalar(
            state.connection,
            "SELECT CAST(SUSER_SNAME() AS NVARCHAR(128))",
        )
        return jsonify(principal=value)

    @app.get("/value/<int:value>")
    async def value(value: int):
        return jsonify(value=await delayed(value, "none"))

    @app.get("/loop")
    async def loop():
        running = asyncio.get_running_loop()
        state.loops.append(running)
        return jsonify(
            loop_id=id(running),
            sql_value=await delayed(15, "none"),
        )

    @app.get("/gather")
    async def gather():
        sequential_started = time.monotonic()
        sequential = [await delayed(value) for value in range(4)]
        sequential_elapsed = time.monotonic() - sequential_started
        concurrent_started = time.monotonic()
        concurrent = await asyncio.gather(
            *(delayed(value) for value in range(4))
        )
        concurrent_elapsed = time.monotonic() - concurrent_started
        return jsonify(
            sequential=sequential,
            concurrent=concurrent,
            sequential_seconds=sequential_elapsed,
            concurrent_seconds=concurrent_elapsed,
        )

    @app.get("/wait/<int:value>")
    async def wait(value: int):
        return jsonify(value=await delayed(value, "long"))

    @app.get("/timeout/<int:value>")
    async def timeout_value(value: int):
        profile = request.args.get("profile", "immediate")
        if profile not in {"immediate", "wait"}:
            raise ValueError(f"invalid timeout profile {profile!r}")
        try:
            return jsonify(
                await _timeout_route_value(
                    state,
                    value,
                    wait=profile == "wait",
                )
            )
        except fastmssql.SqlConnectionError as error:
            return jsonify(_timeout_payload(error)), 504

    @app.get("/sql-error")
    async def sql_error():
        await state.connection.query(
            "SELECT * FROM dbo.strict_framework_missing_table"
        )
        raise AssertionError("unreachable after missing-table query")

    return app


def create_adapted_flask_app(state: FrameworkState) -> WsgiToAsgi:
    return WsgiToAsgi(create_flask_app(state))


@asynccontextmanager
async def adapted_flask_lifespan(state: FrameworkState):
    try:
        await state.connection.connect()
        yield create_adapted_flask_app(state)
    finally:
        await state.connection.disconnect()
