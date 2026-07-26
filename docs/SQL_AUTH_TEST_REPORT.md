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
- Git commit: `ecce84d3b1be65e591879f8dc2443a9058ddb581`

## Matrix outcomes

| Status | Count |
|---|---:|
| PASS | 337 |
| FAIL | 0 |
| ERROR | 0 |
| SKIPPED | 0 |
| NOT RUN | 0 |

## Execution lanes

| Lane | Exit code | Tests | Failures | Errors | Skipped | Command |
|---|---:|---:|---:|---:|---:|---|
| async | 0 | 16 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/async-results.json uv run pytest tests/sql_auth_strict/test_async_strict.py --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/async.xml -vv |
| cargo-clippy | 0 | 0 | 0 | 0 | 0 | cargo clippy --all-targets -- -D warnings |
| cargo-fmt | 0 | 0 | 0 | 0 | 0 | cargo fmt --check |
| cargo-test | 0 | 0 | 0 | 0 | 0 | cargo test --locked |
| compose-up | 0 | 0 | 0 | 0 | 0 | docker compose --env-file /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.env.sql-auth.local -f docker-compose.sql-auth.yml up -d sqlserver |
| framework | 0 | 33 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/framework-results.json FASTMSSQL_FRAMEWORK_METRICS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/framework-metrics.json uv run pytest tests/sql_auth_strict/test_framework_integration.py --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/framework.xml -vv |
| load | 0 | 11 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/load-results.json FASTMSSQL_LOAD_METRICS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/load-metrics.json uv run pytest tests/sql_auth_strict/test_resilience_load.py -m load --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/load.xml -vv |
| maturin-develop | 0 | 0 | 0 | 0 | 0 | uv run maturin develop --release |
| provision | 0 | 0 | 0 | 0 | 0 | scripts/sql_auth/provision.sh |
| report | 0 | 0 | 0 | 0 | 0 | uv run python scripts/sql_auth/generate_report.py --spec docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md --strict-results /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/strict-results.json --artifact-dir /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth --matrix-output docs/SQL_AUTH_TEST_MATRIX.md --report-output docs/SQL_AUTH_TEST_REPORT.md --require-complete |
| resilience | 0 | 6 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/resilience-results.json uv run pytest tests/sql_auth_strict/test_resilience_load.py -m resilience --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/resilience.xml -vv |
| strict | 0 | 340 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/strict-results.json uv run pytest tests/sql_auth_strict/test_matrix_contract.py tests/sql_auth_strict/test_environment_auth.py tests/sql_auth_strict/test_connection.py tests/sql_auth_strict/test_pool.py tests/sql_auth_strict/test_pool_observability.py tests/sql_auth_strict/test_operation_metrics.py tests/sql_auth_strict/test_sql_features.py tests/sql_auth_strict/test_parameters_strict.py tests/sql_auth_strict/test_type_mapping_strict.py tests/sql_auth_strict/test_results_strict.py tests/sql_auth_strict/test_batch_strict.py tests/sql_auth_strict/test_transactions_strict.py tests/sql_auth_strict/test_operation_timeouts.py tests/sql_auth_strict/test_lifecycle.py tests/sql_auth_strict/test_errors_tls.py -m not\ resilience\ and\ not\ load --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/strict.xml -vv |
| original-local-regression | 0 | 930 | 0 | 0 | 0 | uv run pytest -n 1 tests --ignore=tests/test_azure_auth_advanced.py --ignore=tests/test_azure_authentication.py --ignore=tests/test_azure_cli_path_validation.py --ignore=tests/test_transaction_azure_auth.py --ignore=tests/test_transaction_azure_auth_advanced.py --ignore=tests/sql_auth_strict --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/verify-lifecycle-shutdown-waiter-test-race/.artifacts/sql-auth/upstream.xml -vv |
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
| `FRAME-009` | {"concurrent_seconds": 1.022949250182137, "ratio": 0.2540401208043612, "sequential_seconds": 4.026723207905889} |
| `FRAME-010` | {"ticker_count": 48} |
| `FRAME-011` | {"cancellation_seconds": 0.0008023330010473728, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "connections_closed_broken": 1, "connections_closed_idle_timeout": 0, "connections_closed_invalid": 0, "connections_closed_max_lifetime": 0, "connections_created": 2, "get_direct": 1, "get_started": 3, "get_timed_out": 0, "get_wait_time_seconds": 0.01762, "get_waited": 2, "idle_connections": 1, "max_size": 4, "min_idle": 0, "pending_gets": 0}} |
| `FRAME-015` | {"distinct_request_loops": true, "loop_ids": [4452967712, 4452959200]} |
| `FRAME-016` | {"concurrent": [0, 1, 2, 3], "concurrent_seconds": 1.018911249935627, "sequential": [0, 1, 2, 3], "sequential_seconds": 4.036234959028661} |
| `FRAME-017` | {"elapsed_seconds": 2.026888750027865, "execution_model": "WSGI worker-bound", "requests": 4} |
| `FRAME-021` | {"loop_id": 4452970752, "persistent_loop": true} |
| `FRAME-022` | {"elapsed_seconds": 8.03520516701974, "execution_model": "persistent ASGI loop around Flask/WSGI", "requests": 4} |
| `FRAME-023` | {"cancellation_seconds": 0.00025191716849803925, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "connections_closed_broken": 1, "connections_closed_idle_timeout": 0, "connections_closed_invalid": 0, "connections_closed_max_lifetime": 0, "connections_created": 2, "get_direct": 1, "get_started": 3, "get_timed_out": 0, "get_wait_time_seconds": 0.01878, "get_waited": 2, "idle_connections": 1, "max_size": 4, "min_idle": 0, "pending_gets": 0}, "recovery_bound_seconds": 3.5} |
| `OPMET-014` | {"elapsed_seconds": 0.044844166142866015, "event_loop_ticks": 276, "exact_query_delta": true, "maximum_connections": 8, "pool_max_size": 8, "request_count": 100, "worker_count": 20} |
| `OPMET-015` | {"distinct_request_loops": true, "execution_model": "WSGI per-request event loop", "query_count": 42, "worker_count": 10} |
| `OPMET-016` | {"event_loop_ticks": 2241, "execution_model": "persistent ASGI loop around Flask/WSGI", "persistent_loop": true, "query_count": 42, "worker_count": 10} |

## Load metrics

| Case | Metrics |
|---|---|
| `LOAD-001` | {"elapsed_seconds": 0.22135325009003282, "queries_per_second": 4517.665765437199} |
| `LOAD-002` | {"python_current_bytes": 1592592, "python_peak_bytes": 4793748, "rss_after_bytes": 109608960, "rss_before_bytes": 89161728} |
| `LOAD-003` | {"python_current_bytes": 49944, "python_peak_bytes": 63776, "samples_bytes": [49448, 49544, 49576, 49608, 49640, 49704, 49736, 49768, 49800, 49896]} |
| `LOAD-004` | {"elapsed_by_size": {"1": 0.014914250001311302, "100": 0.009578125085681677, "1000": 0.12090158299542964, "10000": 0.37640645797364414}} |
| `LOAD-005` | {"baseline_sessions": 0, "elapsed_seconds": 1.8446663750801235, "final_sessions": 0} |
| `LOAD-006` | {"elapsed_seconds": 0.3566599579062313, "operation_count": 500} |
| `LOAD-008` | {"concurrency": 50, "distinct_session_count": 34, "elapsed_seconds": 1.6228463749866933, "transaction_count": 1000, "transactions_per_second": 616.201271675022} |
| `LOAD-009` | {"elapsed_seconds": 0.16610883292742074, "peak_observed_sessions": 20, "pool_max_size": 20, "probe_count": 1000, "probes_per_second": 6020.149454887435, "task_concurrency": 100} |
| `OBS-009` | {"elapsed_seconds": 1.444130124989897, "event_loop_ticks": 30452, "max_pending_gets": 100, "operation_count": 10000, "physical_connections": 20, "pool_max_size": 20, "queries_per_second": 6924.583752499422, "scrape_samples": 7676, "worker_count": 100} |
| `OPMET-011` | {"elapsed_seconds": 1.047053915914148, "event_loop_ticks": 18011, "exact_outcomes": {"cancelled": 0, "errors": 0, "outcome_unknown": 0, "succeeded": 10000, "timed_out": 0}, "maximum_connections": 20, "maximum_in_flight": 100, "operation_count": 10000, "pool_max_size": 20, "queries_per_second": 9550.606561906921, "query_histogram": [0, 0, 18, 307, 2517, 3365, 7844, 9550, 9839, 9881, 9981, 10001, 10001, 10001, 10001, 10001, 10001], "scrape_samples": 2649, "worker_count": 100} |
