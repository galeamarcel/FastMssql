from __future__ import annotations

from collections.abc import AsyncIterator, Callable
import json
from pathlib import Path
from typing import Any

from fastmssql import Connection, Transaction
import pytest
import pytest_asyncio

from sql_auth_strict.cases import CASE_MARKER
from sql_auth_strict.config import SqlAuthConfig
from sql_auth_strict.helpers import CleanupRegistry
from sql_auth_strict.helpers import unique_sql_name as make_unique_sql_name


ROOT = Path(__file__).resolve().parents[2]
RESULTS_PATH = ROOT / ".artifacts/sql-auth/strict-results.json"
_RESULTS: dict[str, dict[str, Any]] = {}
_PASSWORDS: tuple[str, ...] = ()
_OUTCOME_PRIORITY = {"passed": 0, "skipped": 1, "failed": 2}


def redact_message(message: str, passwords: tuple[str, ...]) -> str:
    for password in passwords:
        if password:
            message = message.replace(password, "<redacted>")
    return message


def pytest_configure(config: pytest.Config) -> None:
    global _PASSWORDS

    config.addinivalue_line(
        "markers",
        f"{CASE_MARKER}(*case_ids): approved strict SQL-auth matrix IDs",
    )
    _RESULTS.clear()
    sql_auth_config = SqlAuthConfig.from_env(require_all=False)
    _PASSWORDS = tuple(
        password
        for password in (
            sql_auth_config.sa_password,
            sql_auth_config.owner_password,
            sql_auth_config.readonly_password,
            sql_auth_config.denied_password,
        )
        if password
    )


def _case_ids(item: pytest.Item) -> frozenset[str]:
    return frozenset(
        case_id
        for marker in item.iter_markers(name=CASE_MARKER)
        for case_id in marker.args
        if isinstance(case_id, str)
    )


def _report_message(report: pytest.TestReport) -> str:
    if not report.failed and not report.skipped:
        return ""
    longreprtext = getattr(report, "longreprtext", "")
    return redact_message(longreprtext or str(report.longrepr), _PASSWORDS)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()
    for case_id in _case_ids(item):
        record = _RESULTS.setdefault(
            case_id,
            {
                "outcome": "passed",
                "nodeid": item.nodeid,
                "duration_seconds": 0.0,
                "message": "",
            },
        )
        record["duration_seconds"] += report.duration
        if report.failed:
            next_outcome = "failed"
        elif report.skipped:
            next_outcome = "skipped"
        else:
            next_outcome = "passed"
        if _OUTCOME_PRIORITY[next_outcome] >= _OUTCOME_PRIORITY[
            record["outcome"]
        ]:
            record["outcome"] = next_outcome
        message = _report_message(report)
        if message:
            existing = record["message"]
            record["message"] = f"{existing}\n{message}".strip()


def pytest_sessionfinish(
    session: pytest.Session, exitstatus: int | pytest.ExitCode
) -> None:
    del session, exitstatus
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "cases": dict(sorted(_RESULTS.items())),
    }
    RESULTS_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="session")
def sql_auth_config() -> SqlAuthConfig:
    return SqlAuthConfig.from_env()


async def _connected(
    connection_string: str,
) -> AsyncIterator[Connection]:
    async with Connection(connection_string) as connection:
        yield connection


@pytest_asyncio.fixture
async def owner_connection(
    sql_auth_config: SqlAuthConfig,
) -> AsyncIterator[Connection]:
    async for connection in _connected(
        sql_auth_config.connection_string(
            sql_auth_config.owner_user,
            sql_auth_config.owner_password,
        )
    ):
        yield connection


@pytest_asyncio.fixture
async def readonly_connection(
    sql_auth_config: SqlAuthConfig,
) -> AsyncIterator[Connection]:
    async for connection in _connected(
        sql_auth_config.connection_string(
            sql_auth_config.readonly_user,
            sql_auth_config.readonly_password,
        )
    ):
        yield connection


@pytest_asyncio.fixture
async def denied_connection(
    sql_auth_config: SqlAuthConfig,
) -> AsyncIterator[Connection]:
    async for connection in _connected(
        sql_auth_config.connection_string(
            sql_auth_config.denied_user,
            sql_auth_config.denied_password,
        )
    ):
        yield connection


@pytest_asyncio.fixture
async def sa_connection(
    sql_auth_config: SqlAuthConfig,
) -> AsyncIterator[Connection]:
    async for connection in _connected(
        sql_auth_config.connection_string(
            sql_auth_config.sa_user,
            sql_auth_config.sa_password,
        )
    ):
        yield connection


@pytest.fixture
def transaction_factory(
    sql_auth_config: SqlAuthConfig,
) -> Callable[..., Transaction]:
    def factory(
        *,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        extra: str = "",
    ) -> Transaction:
        return Transaction(
            sql_auth_config.connection_string(
                user or sql_auth_config.owner_user,
                password or sql_auth_config.owner_password,
                database=database,
                extra=extra,
            )
        )

    return factory


@pytest.fixture
def unique_sql_name() -> Callable[[str], str]:
    return make_unique_sql_name


@pytest_asyncio.fixture
async def cleanup_registry(
    owner_connection: Connection,
) -> AsyncIterator[CleanupRegistry]:
    registry = CleanupRegistry(owner_connection)
    yield registry
    await registry.cleanup()
