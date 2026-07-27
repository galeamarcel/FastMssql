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
- Git commit: `7a881c57ef2bbd08477271d2b58d7d5aaefd5b99`

## Matrix outcomes

| Status | Count |
|---|---:|
| PASS | 346 |
| FAIL | 0 |
| ERROR | 0 |
| SKIPPED | 0 |
| NOT RUN | 0 |

## Execution lanes

| Lane | Exit code | Tests | Failures | Errors | Skipped | Command |
|---|---:|---:|---:|---:|---:|---|
| async | 0 | 16 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/async-results.json uv run pytest tests/sql_auth_strict/test_async_strict.py --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/async.xml -vv |
| cargo-clippy | 0 | 0 | 0 | 0 | 0 | cargo clippy --all-targets -- -D warnings |
| cargo-fmt | 0 | 0 | 0 | 0 | 0 | cargo fmt --check |
| cargo-test | 0 | 0 | 0 | 0 | 0 | cargo test --locked |
| compose-up | 0 | 0 | 0 | 0 | 0 | docker compose --env-file /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.env.sql-auth.local -f docker-compose.sql-auth.yml up -d sqlserver |
| framework | 0 | 33 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/framework-results.json FASTMSSQL_FRAMEWORK_METRICS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/framework-metrics.json uv run pytest tests/sql_auth_strict/test_framework_integration.py --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/framework.xml -vv |
| load | 0 | 12 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/load-results.json FASTMSSQL_LOAD_METRICS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/load-metrics.json uv run pytest tests/sql_auth_strict/test_resilience_load.py -m load --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/load.xml -vv |
| maturin-develop | 0 | 0 | 0 | 0 | 0 | uv run maturin develop --release |
| provision | 0 | 0 | 0 | 0 | 0 | scripts/sql_auth/provision.sh |
| resilience | 0 | 6 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/resilience-results.json uv run pytest tests/sql_auth_strict/test_resilience_load.py -m resilience --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/resilience.xml -vv |
| strict | 0 | 346 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/strict-results.json uv run pytest tests/sql_auth_strict/test_matrix_contract.py tests/sql_auth_strict/test_environment_auth.py tests/sql_auth_strict/test_connection.py tests/sql_auth_strict/test_pool.py tests/sql_auth_strict/test_pool_observability.py tests/sql_auth_strict/test_operation_metrics.py tests/sql_auth_strict/test_sql_features.py tests/sql_auth_strict/test_parameters_strict.py tests/sql_auth_strict/test_type_mapping_strict.py tests/sql_auth_strict/test_results_strict.py tests/sql_auth_strict/test_batch_strict.py tests/sql_auth_strict/test_transactions_strict.py tests/sql_auth_strict/test_operation_timeouts.py tests/sql_auth_strict/test_lifecycle.py tests/sql_auth_strict/test_errors_tls.py -m not\ resilience\ and\ not\ load --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/strict.xml -vv |
| original-local-regression | 0 | 1072 | 0 | 0 | 0 | uv run pytest -n 1 tests --ignore=tests/test_azure_auth_advanced.py --ignore=tests/test_azure_authentication.py --ignore=tests/test_azure_cli_path_validation.py --ignore=tests/test_transaction_azure_auth.py --ignore=tests/test_transaction_azure_auth_advanced.py --ignore=tests/sql_auth_strict --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-typed-parameter-merge/.artifacts/sql-auth/upstream.xml -vv |
| uv-sync | 0 | 0 | 0 | 0 | 0 | uv sync --locked --all-extras --dev |

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
| `FRAME-009` | {"concurrent_seconds": 1.031535041052848, "ratio": 0.25511822292040015, "sequential_seconds": 4.043360874988139} |
| `FRAME-010` | {"ticker_count": 47} |
| `FRAME-011` | {"cancellation_seconds": 0.0008882500696927309, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "connections_closed_broken": 1, "connections_closed_idle_timeout": 0, "connections_closed_invalid": 0, "connections_closed_max_lifetime": 0, "connections_created": 2, "get_direct": 1, "get_started": 3, "get_timed_out": 0, "get_wait_time_seconds": 0.015444, "get_waited": 2, "idle_connections": 1, "max_size": 4, "min_idle": 0, "pending_gets": 0}} |
| `FRAME-015` | {"distinct_request_loops": true, "loop_ids": [4516013344, 4516004832]} |
| `FRAME-016` | {"concurrent": [0, 1, 2, 3], "concurrent_seconds": 1.023595874896273, "sequential": [0, 1, 2, 3], "sequential_seconds": 4.036736333044246} |
| `FRAME-017` | {"elapsed_seconds": 2.030772167025134, "execution_model": "WSGI worker-bound", "requests": 4} |
| `FRAME-021` | {"loop_id": 4516016384, "persistent_loop": true} |
| `FRAME-022` | {"elapsed_seconds": 8.048948290990666, "execution_model": "persistent ASGI loop around Flask/WSGI", "requests": 4} |
| `FRAME-023` | {"cancellation_seconds": 0.0004372501280158758, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "connections_closed_broken": 1, "connections_closed_idle_timeout": 0, "connections_closed_invalid": 0, "connections_closed_max_lifetime": 0, "connections_created": 2, "get_direct": 1, "get_started": 3, "get_timed_out": 0, "get_wait_time_seconds": 0.018256, "get_waited": 2, "idle_connections": 1, "max_size": 4, "min_idle": 0, "pending_gets": 0}, "recovery_bound_seconds": 3.5} |
| `OPMET-014` | {"elapsed_seconds": 0.04633416701108217, "event_loop_ticks": 289, "exact_query_delta": true, "maximum_connections": 8, "pool_max_size": 8, "request_count": 100, "worker_count": 20} |
| `OPMET-015` | {"distinct_request_loops": true, "execution_model": "WSGI per-request event loop", "query_count": 42, "worker_count": 10} |
| `OPMET-016` | {"event_loop_ticks": 2224, "execution_model": "persistent ASGI loop around Flask/WSGI", "persistent_loop": true, "query_count": 42, "worker_count": 10} |

## Load metrics

| Case | Metrics |
|---|---|
| `LOAD-001` | {"elapsed_seconds": 0.19886925001628697, "queries_per_second": 5028.429482778771} |
| `LOAD-002` | {"python_current_bytes": 1592592, "python_peak_bytes": 4793748, "rss_after_bytes": 113164288, "rss_before_bytes": 91226112} |
| `LOAD-003` | {"python_current_bytes": 49944, "python_peak_bytes": 63776, "samples_bytes": [49448, 49544, 49576, 49608, 49640, 49704, 49736, 49768, 49800, 49896]} |
| `LOAD-004` | {"elapsed_by_size": {"1": 0.010255916975438595, "100": 0.009003499988466501, "1000": 0.11331849987618625, "10000": 0.3484480830375105}} |
| `LOAD-005` | {"baseline_sessions": 0, "elapsed_seconds": 1.7356055409181863, "final_sessions": 0} |
| `LOAD-006` | {"elapsed_seconds": 0.3201211669947952, "operation_count": 500} |
| `LOAD-008` | {"concurrency": 50, "distinct_session_count": 38, "elapsed_seconds": 1.5323576249647886, "transaction_count": 1000, "transactions_per_second": 652.5891761219765} |
| `LOAD-009` | {"elapsed_seconds": 0.163226000033319, "peak_observed_sessions": 20, "pool_max_size": 20, "probe_count": 1000, "probes_per_second": 6126.4749475933495, "task_concurrency": 100} |
| `OBS-009` | {"elapsed_seconds": 1.3436920829117298, "event_loop_ticks": 34709, "max_pending_gets": 100, "operation_count": 10000, "physical_connections": 20, "pool_max_size": 20, "queries_per_second": 7442.181231231473, "scrape_samples": 8956, "worker_count": 100} |
| `OPMET-011` | {"elapsed_seconds": 0.8913687909953296, "event_loop_ticks": 16392, "exact_outcomes": {"cancelled": 0, "errors": 0, "outcome_unknown": 0, "succeeded": 10000, "timed_out": 0}, "maximum_connections": 20, "maximum_in_flight": 100, "operation_count": 10000, "pool_max_size": 20, "queries_per_second": 11218.701059562221, "query_histogram": [0, 0, 21, 602, 3066, 4192, 8522, 9724, 9840, 9881, 9981, 10001, 10001, 10001, 10001, 10001, 10001], "scrape_samples": 2496, "worker_count": 100} |
| `PARAM-033` | {"completed": 1000, "elapsed_seconds": 0.623699292074889, "failed": 0, "maximum_in_flight": 64, "operations_per_second": 1603.3367565213266, "physical_sessions": 8, "pool_max_size": 8, "submitted": 1000, "succeeded": 1000} |
