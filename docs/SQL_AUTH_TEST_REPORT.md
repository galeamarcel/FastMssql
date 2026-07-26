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
- Git commit: `fd79495c2d6f811377ec5b5f35948e18019ac89d`

## Matrix outcomes

| Status | Count |
|---|---:|
| PASS | 311 |
| FAIL | 0 |
| ERROR | 0 |
| SKIPPED | 0 |
| NOT RUN | 0 |

## Execution lanes

| Lane | Exit code | Tests | Failures | Errors | Skipped | Command |
|---|---:|---:|---:|---:|---:|---|
| async | 0 | 16 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/async-results.json uv run pytest tests/sql_auth_strict/test_async_strict.py --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/async.xml -vv |
| cargo-clippy | 0 | 0 | 0 | 0 | 0 | cargo clippy --all-targets -- -D warnings |
| cargo-fmt | 0 | 0 | 0 | 0 | 0 | cargo fmt --check |
| cargo-test | 0 | 0 | 0 | 0 | 0 | cargo test --locked |
| compose-up | 0 | 0 | 0 | 0 | 0 | docker compose --env-file /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.env.sql-auth.local -f docker-compose.sql-auth.yml up -d sqlserver |
| framework | 0 | 30 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/framework-results.json FASTMSSQL_FRAMEWORK_METRICS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/framework-metrics.json uv run pytest tests/sql_auth_strict/test_framework_integration.py --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/framework.xml -vv |
| load | 0 | 9 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/load-results.json FASTMSSQL_LOAD_METRICS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/load-metrics.json uv run pytest tests/sql_auth_strict/test_resilience_load.py -m load --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/load.xml -vv |
| maturin-develop | 0 | 0 | 0 | 0 | 0 | uv run maturin develop --release |
| provision | 0 | 0 | 0 | 0 | 0 | scripts/sql_auth/provision.sh |
| report | 0 | 0 | 0 | 0 | 0 | uv run python scripts/sql_auth/generate_report.py --spec docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md --strict-results /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/strict-results.json --artifact-dir /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth --matrix-output docs/SQL_AUTH_TEST_MATRIX.md --report-output docs/SQL_AUTH_TEST_REPORT.md --require-complete |
| resilience | 0 | 6 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/resilience-results.json uv run pytest tests/sql_auth_strict/test_resilience_load.py -m resilience --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/resilience.xml -vv |
| strict | 0 | 321 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/strict-results.json uv run pytest tests/sql_auth_strict/test_matrix_contract.py tests/sql_auth_strict/test_environment_auth.py tests/sql_auth_strict/test_connection.py tests/sql_auth_strict/test_pool.py tests/sql_auth_strict/test_sql_features.py tests/sql_auth_strict/test_parameters_strict.py tests/sql_auth_strict/test_type_mapping_strict.py tests/sql_auth_strict/test_results_strict.py tests/sql_auth_strict/test_batch_strict.py tests/sql_auth_strict/test_transactions_strict.py tests/sql_auth_strict/test_operation_timeouts.py tests/sql_auth_strict/test_lifecycle.py tests/sql_auth_strict/test_errors_tls.py -m not\ resilience\ and\ not\ load --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/strict.xml -vv |
| upstream | 0 | 921 | 0 | 0 | 0 | uv run pytest -n 1 tests --ignore=tests/test_azure_auth_advanced.py --ignore=tests/test_azure_authentication.py --ignore=tests/test_azure_cli_path_validation.py --ignore=tests/test_transaction_azure_auth.py --ignore=tests/test_transaction_azure_auth_advanced.py --ignore=tests/sql_auth_strict --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-lifecycle-state/.artifacts/sql-auth/upstream.xml -vv |
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
| `FRAME-009` | {"concurrent_seconds": 1.0286072089802474, "ratio": 0.25445149144769125, "sequential_seconds": 4.042449125088751} |
| `FRAME-010` | {"ticker_count": 46} |
| `FRAME-011` | {"cancellation_seconds": 0.00026249978691339493, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "idle_connections": 1, "max_size": 4, "min_idle": 0}} |
| `FRAME-015` | {"distinct_request_loops": true, "loop_ids": [4439858688, 4439854128]} |
| `FRAME-016` | {"concurrent": [0, 1, 2, 3], "concurrent_seconds": 1.0270461670588702, "sequential": [0, 1, 2, 3], "sequential_seconds": 4.041564583079889} |
| `FRAME-017` | {"elapsed_seconds": 2.0235166661441326, "execution_model": "WSGI worker-bound", "requests": 4} |
| `FRAME-021` | {"loop_id": 4439858992, "persistent_loop": true} |
| `FRAME-022` | {"elapsed_seconds": 8.031086208065972, "execution_model": "persistent ASGI loop around Flask/WSGI", "requests": 4} |
| `FRAME-023` | {"cancellation_seconds": 0.00039016595110297203, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "idle_connections": 1, "max_size": 4, "min_idle": 0}, "recovery_bound_seconds": 3.5} |

## Load metrics

| Case | Metrics |
|---|---|
| `LOAD-001` | {"elapsed_seconds": 0.19579566596075892, "queries_per_second": 5107.3653499583515} |
| `LOAD-002` | {"python_current_bytes": 1592592, "python_peak_bytes": 4793748, "rss_after_bytes": 86818816, "rss_before_bytes": 63586304} |
| `LOAD-003` | {"python_current_bytes": 49944, "python_peak_bytes": 63776, "samples_bytes": [49448, 49544, 49576, 49608, 49640, 49704, 49736, 49768, 49800, 49896]} |
| `LOAD-004` | {"elapsed_by_size": {"1": 0.009478166932240129, "100": 0.00822816719301045, "1000": 0.1129645830951631, "10000": 0.3579395420383662}} |
| `LOAD-005` | {"baseline_sessions": 0, "elapsed_seconds": 1.6893554581329226, "final_sessions": 0} |
| `LOAD-006` | {"elapsed_seconds": 0.32752295793034136, "operation_count": 500} |
| `LOAD-008` | {"concurrency": 50, "distinct_session_count": 36, "elapsed_seconds": 1.3957842090167105, "transaction_count": 1000, "transactions_per_second": 716.4431246177165} |
| `LOAD-009` | {"elapsed_seconds": 0.1438788331579417, "peak_observed_sessions": 20, "pool_max_size": 20, "probe_count": 1000, "probes_per_second": 6950.29267371288, "task_concurrency": 100} |
