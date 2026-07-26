from __future__ import annotations

import asyncio
from pathlib import Path

import fastmssql


ROOT = Path(__file__).resolve().parents[1]
CORE_STUB = ROOT / "python/fastmssql/fastmssql.pyi"
WRAPPER_STUB = ROOT / "python/fastmssql/__init__.pyi"
README = ROOT / "README.md"
POOL_STATS_KEYS = {
    "connected",
    "connections",
    "idle_connections",
    "active_connections",
    "max_size",
    "min_idle",
    "get_started",
    "get_direct",
    "get_waited",
    "get_timed_out",
    "pending_gets",
    "get_wait_time_seconds",
    "connections_created",
    "connections_closed_broken",
    "connections_closed_invalid",
    "connections_closed_max_lifetime",
    "connections_closed_idle_timeout",
}
INTEGER_METRICS = POOL_STATS_KEYS - {
    "connected",
    "min_idle",
    "get_wait_time_seconds",
}


async def _disconnected_stats() -> dict[str, object]:
    connection = fastmssql.Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="pool_observability_contract",
        password="not-used",
        ssl_config=fastmssql.SslConfig.development(),
        pool_config=fastmssql.PoolConfig(
            max_size=2,
            min_idle=1,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
    )
    return await connection.pool_stats()


def test_disconnected_pool_stats_exact_schema_types_and_zero_epoch() -> None:
    stats = asyncio.run(_disconnected_stats())
    assert set(stats) == POOL_STATS_KEYS
    assert type(stats["connected"]) is bool
    assert stats["connected"] is False
    assert type(stats["max_size"]) is int
    assert stats["max_size"] == 2
    assert type(stats["min_idle"]) is int
    assert stats["min_idle"] == 1
    assert type(stats["get_wait_time_seconds"]) is float
    assert all(type(stats[key]) is int for key in INTEGER_METRICS)
    assert all(
        stats[key] == 0
        for key in POOL_STATS_KEYS - {"connected", "max_size", "min_idle"}
    )


def test_stubs_wrapper_and_readme_publish_the_same_contract() -> None:
    annotation = "Coroutine[Any, Any, Dict[str, int | float | bool | None]]"
    for stub in (CORE_STUB, WRAPPER_STUB):
        text = stub.read_text(encoding="utf-8")
        assert annotation in text
        assert all(f"- {key} " in text for key in POOL_STATS_KEYS)
    wrapper = (ROOT / "python/fastmssql/__init__.py").read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    for key in POOL_STATS_KEYS:
        assert key in wrapper
        assert key in readme
    assert "current pool" in readme.lower()
    assert "reset" in readme.lower()
