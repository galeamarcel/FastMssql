# FastMssql SQL-auth validation report

## Scope

- Authentication under test: SQL Server username/password only.
- Azure authentication and Windows authentication are explicitly excluded.
- SQL Server target: isolated Developer Edition container.
- ARM64 hosts may execute the `linux/amd64` image under emulation; timings are diagnostic rather than marketing benchmarks.

## Environment

- Python: `3.13.14`
- Platform: `macOS-26.5-arm64-arm-64bit-Mach-O`
- Machine: `arm64`
- Git commit: `375124ae61a2cec80aec41f1f175d0d2ffb7b767`

## Matrix outcomes

| Status | Count |
|---|---:|
| PASS | 24 |
| FAIL | 0 |
| ERROR | 0 |
| SKIPPED | 0 |
| NOT RUN | 226 |

## Execution lanes

| Lane | Exit code | Tests | Failures | Errors | Skipped | Command |
|---|---:|---:|---:|---:|---:|---|
| framework | NOT RUN | 26 | 0 | 0 | 0 |  |

## Failures and errors

None recorded.

## Framework execution models

- **FastAPI/native ASGI:** true-async end-to-end only when `FRAME-005` through `FRAME-013` pass.
- **Flask/WSGI:** functional async-view compatibility; each request remains worker-bound.
- **Flask via WsgiToAsgi:** persistent event-loop compatibility; the application remains adapted WSGI, not native ASGI.

### Fixed exclusions

- Production deployment tuning for Gunicorn, uWSGI, Hypercorn, or Uvicorn.
- WebSockets.
- Framework authentication, authorization, serialization, or ORM behavior.
- Quart, gevent, eventlet, or non-`asyncio` event loops.
- Multi-process pool sharing; each process must own its own pool.
- Windows or Azure SQL authentication.

### Framework metrics

| Case | Metrics |
|---|---|
| `FRAME-001` | {"versions": {"asgi-lifespan": "2.1.0", "asgiref": "3.12.1", "fastapi": "0.139.2", "flask": "3.1.3", "httpx": "0.28.1"}} |
| `FRAME-003` | {"principals": {"FastAPI/native ASGI": "fastmssql_owner", "Flask/WSGI": "fastmssql_owner", "Flask/WsgiToAsgi": "fastmssql_owner"}} |
| `FRAME-009` | {"concurrent_seconds": 1.0163382079917938, "ratio": 0.2513468506839292, "sequential_seconds": 4.043568499968387} |
| `FRAME-010` | {"ticker_count": 49} |
| `FRAME-011` | {"cancellation_seconds": 0.00011558295227587223, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "idle_connections": 1, "max_size": 4, "min_idle": 0}} |
| `FRAME-015` | {"distinct_request_loops": true, "loop_ids": [4470494944, 4470494032]} |
| `FRAME-016` | {"concurrent": [0, 1, 2, 3], "concurrent_seconds": 1.020415332983248, "sequential": [0, 1, 2, 3], "sequential_seconds": 4.034650332992896} |
| `FRAME-017` | {"elapsed_seconds": 2.037175750010647, "execution_model": "WSGI worker-bound", "requests": 4} |
| `FRAME-021` | {"loop_id": 4470503456, "persistent_loop": true} |
| `FRAME-022` | {"elapsed_seconds": 8.027434792020358, "execution_model": "persistent ASGI loop around Flask/WSGI", "requests": 4} |
| `FRAME-023` | {"cancellation_seconds": 0.0002506669843569398, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "idle_connections": 1, "max_size": 4, "min_idle": 0}, "recovery_bound_seconds": 3.5} |
