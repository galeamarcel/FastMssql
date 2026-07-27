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
- Git commit: `a9d5c2ab42de0f03051c771bb8942ee15dfe6e28`

## Matrix outcomes

| Status | Count |
|---|---:|
| PASS | 372 |
| FAIL | 0 |
| ERROR | 0 |
| SKIPPED | 0 |
| NOT RUN | 0 |

## Execution lanes

| Lane | Exit code | Tests | Failures | Errors | Skipped | Command |
|---|---:|---:|---:|---:|---:|---|
| async | 0 | 16 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/async-results.json uv run pytest tests/sql_auth_strict/test_async_strict.py --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/async.xml -vv |
| cargo-clippy | 0 | 0 | 0 | 0 | 0 | cargo clippy --all-targets -- -D warnings |
| cargo-fmt | 0 | 0 | 0 | 0 | 0 | cargo fmt --check |
| cargo-test | 0 | 0 | 0 | 0 | 0 | cargo test --locked |
| compose-up | 0 | 0 | 0 | 0 | 0 | docker compose --env-file /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.env.sql-auth.local -f docker-compose.sql-auth.yml up -d sqlserver |
| framework | 0 | 33 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/framework-results.json FASTMSSQL_FRAMEWORK_METRICS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/framework-metrics.json uv run pytest tests/sql_auth_strict/test_framework_integration.py --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/framework.xml -vv |
| load | 0 | 12 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/load-results.json FASTMSSQL_LOAD_METRICS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/load-metrics.json uv run pytest tests/sql_auth_strict/test_resilience_load.py -m load --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/load.xml -vv |
| maturin-develop | 0 | 0 | 0 | 0 | 0 | uv run maturin develop --release |
| provision | 0 | 0 | 0 | 0 | 0 | scripts/sql_auth/provision.sh |
| report | 0 | 0 | 0 | 0 | 0 | uv run python scripts/sql_auth/generate_report.py --spec docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md --strict-results /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/strict-results.json --artifact-dir /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth --matrix-output docs/SQL_AUTH_TEST_MATRIX.md --report-output docs/SQL_AUTH_TEST_REPORT.md --require-complete |
| resilience | 0 | 6 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/resilience-results.json uv run pytest tests/sql_auth_strict/test_resilience_load.py -m resilience --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/resilience.xml -vv |
| result-stream-load | 0 | 0 | 0 | 0 | 0 | scripts/sql_auth/run_result_stream_stress.sh |
| strict | 0 | 386 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/strict-results.json uv run pytest tests/sql_auth_strict/test_matrix_contract.py tests/sql_auth_strict/test_environment_auth.py tests/sql_auth_strict/test_connection.py tests/sql_auth_strict/test_pool.py tests/sql_auth_strict/test_pool_observability.py tests/sql_auth_strict/test_operation_metrics.py tests/sql_auth_strict/test_sql_features.py tests/sql_auth_strict/test_parameters_strict.py tests/sql_auth_strict/test_type_mapping_strict.py tests/sql_auth_strict/test_results_strict.py tests/sql_auth_strict/test_resultsets_streaming.py tests/sql_auth_strict/test_resultstream_lifecycle.py tests/sql_auth_strict/test_rpc_results.py tests/sql_auth_strict/test_batch_strict.py tests/sql_auth_strict/test_transactions_strict.py tests/sql_auth_strict/test_operation_timeouts.py tests/sql_auth_strict/test_lifecycle.py tests/sql_auth_strict/test_errors_tls.py -m not\ resilience\ and\ not\ load --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/strict.xml -vv |
| tiberius-clippy | 0 | 0 | 0 | 0 | 0 | cargo clippy --manifest-path vendor/tiberius/Cargo.toml --no-default-features --features chrono\,tds73\,rustls --all-targets -- -D warnings -A clippy::doc_lazy_continuation -A clippy::extra_unused_lifetimes -A clippy::large_enum_variant -A clippy::io_other_error -A clippy::needless_lifetimes -A clippy::legacy_numeric_constants -A clippy::cast_enum_truncation -A clippy::derivable_impls -A clippy::manual_div_ceil -A clippy::items_after_test_module |
| tiberius-fmt | 0 | 0 | 0 | 0 | 0 | cargo fmt --manifest-path vendor/tiberius/Cargo.toml --check |
| tiberius-lib | 0 | 0 | 0 | 0 | 0 | cargo test --manifest-path vendor/tiberius/Cargo.toml --no-default-features --features chrono\,tds73\,rustls --lib |
| tiberius-response-sql-auth | 0 | 0 | 0 | 0 | 0 | cargo test --manifest-path vendor/tiberius/Cargo.toml --no-default-features --features chrono\,tds73\,rustls --test response_events_sql_auth -- --test-threads=1 |
| tiberius-token-safety-sql-auth | 0 | 0 | 0 | 0 | 0 | cargo test --manifest-path vendor/tiberius/Cargo.toml --no-default-features --features chrono\,tds73\,rustls --test token_safety_sql_auth -- --test-threads=1 |
| original-local-regression | 0 | 1090 | 0 | 0 | 0 | uv run pytest -n 1 tests --ignore=tests/test_azure_auth_advanced.py --ignore=tests/test_azure_authentication.py --ignore=tests/test_azure_cli_path_validation.py --ignore=tests/test_transaction_azure_auth.py --ignore=tests/test_transaction_azure_auth_advanced.py --ignore=tests/sql_auth_strict --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.artifacts/sql-auth/upstream.xml -vv |
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
| `FRAME-009` | {"concurrent_seconds": 1.0253473329357803, "ratio": 0.253689459476586, "sequential_seconds": 4.04174195905216} |
| `FRAME-010` | {"ticker_count": 46} |
| `FRAME-011` | {"cancellation_seconds": 0.0001114171463996172, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "connections_closed_broken": 1, "connections_closed_idle_timeout": 0, "connections_closed_invalid": 0, "connections_closed_max_lifetime": 0, "connections_created": 2, "get_direct": 1, "get_started": 3, "get_timed_out": 0, "get_wait_time_seconds": 0.01474, "get_waited": 2, "idle_connections": 1, "max_size": 4, "min_idle": 0, "pending_gets": 0}} |
| `FRAME-015` | {"distinct_request_loops": true, "loop_ids": [4504086096, 4504080320]} |
| `FRAME-016` | {"concurrent": [0, 1, 2, 3], "concurrent_seconds": 1.0216725829523057, "sequential": [0, 1, 2, 3], "sequential_seconds": 4.050536209018901} |
| `FRAME-017` | {"elapsed_seconds": 2.0191517921630293, "execution_model": "WSGI worker-bound", "requests": 4} |
| `FRAME-021` | {"loop_id": 4504084272, "persistent_loop": true} |
| `FRAME-022` | {"elapsed_seconds": 8.029691541101784, "execution_model": "persistent ASGI loop around Flask/WSGI", "requests": 4} |
| `FRAME-023` | {"cancellation_seconds": 0.0002423750702291727, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "connections_closed_broken": 1, "connections_closed_idle_timeout": 0, "connections_closed_invalid": 0, "connections_closed_max_lifetime": 0, "connections_created": 2, "get_direct": 1, "get_started": 3, "get_timed_out": 0, "get_wait_time_seconds": 0.01429, "get_waited": 2, "idle_connections": 1, "max_size": 4, "min_idle": 0, "pending_gets": 0}, "recovery_bound_seconds": 3.5} |
| `OPMET-014` | {"elapsed_seconds": 0.046328875003382564, "event_loop_ticks": 153, "exact_query_delta": true, "maximum_connections": 8, "pool_max_size": 8, "request_count": 100, "worker_count": 20} |
| `OPMET-015` | {"distinct_request_loops": true, "execution_model": "WSGI per-request event loop", "query_count": 42, "worker_count": 10} |
| `OPMET-016` | {"event_loop_ticks": 2235, "execution_model": "persistent ASGI loop around Flask/WSGI", "persistent_loop": true, "query_count": 42, "worker_count": 10} |

## Load metrics

| Case | Metrics |
|---|---|
| `LOAD-001` | {"elapsed_seconds": 0.2196595419663936, "queries_per_second": 4552.499704988883} |
| `LOAD-002` | {"python_current_bytes": 1592592, "python_peak_bytes": 4793748, "rss_after_bytes": 113573888, "rss_before_bytes": 93945856} |
| `LOAD-003` | {"python_current_bytes": 49944, "python_peak_bytes": 63776, "samples_bytes": [49448, 49544, 49576, 49608, 49640, 49704, 49736, 49768, 49800, 49896]} |
| `LOAD-004` | {"elapsed_by_size": {"1": 0.009871916845440865, "100": 0.036055624950677156, "1000": 0.11641904199495912, "10000": 0.3437544999178499}} |
| `LOAD-005` | {"baseline_sessions": 0, "elapsed_seconds": 1.775990332942456, "final_sessions": 0} |
| `LOAD-006` | {"elapsed_seconds": 0.33821370801888406, "operation_count": 500} |
| `LOAD-008` | {"concurrency": 50, "distinct_session_count": 32, "elapsed_seconds": 1.5904365421738476, "transaction_count": 1000, "transactions_per_second": 628.7581890146812} |
| `LOAD-009` | {"elapsed_seconds": 0.16504566697403789, "peak_observed_sessions": 20, "pool_max_size": 20, "probe_count": 1000, "probes_per_second": 6058.929133579149, "task_concurrency": 100} |
| `OBS-009` | {"elapsed_seconds": 1.4214830840937793, "event_loop_ticks": 34844, "max_pending_gets": 100, "operation_count": 10000, "physical_connections": 20, "pool_max_size": 20, "queries_per_second": 7034.906086395799, "scrape_samples": 9025, "worker_count": 100} |
| `OPMET-011` | {"elapsed_seconds": 0.9201216250658035, "event_loop_ticks": 15680, "exact_outcomes": {"cancelled": 0, "errors": 0, "outcome_unknown": 0, "succeeded": 10000, "timed_out": 0}, "maximum_connections": 20, "maximum_in_flight": 100, "operation_count": 10000, "pool_max_size": 20, "queries_per_second": 10868.12843822124, "query_histogram": [0, 0, 11, 565, 3049, 4161, 8417, 9675, 9839, 9881, 9981, 10001, 10001, 10001, 10001, 10001, 10001], "scrape_samples": 2347, "worker_count": 100} |
| `PARAM-033` | {"completed": 1000, "elapsed_seconds": 0.6464430829510093, "failed": 0, "maximum_in_flight": 64, "operations_per_second": 1546.926599376708, "physical_sessions": 8, "pool_max_size": 8, "submitted": 1000, "succeeded": 1000} |

## Result-stream stress metrics

- Schema version: `1`
- Evidence status: `passed`
- Source SHA: `a9d5c2ab42de0f03051c771bb8942ee15dfe6e28`
- Configuration: `{"buffer_size": 8, "percentile_method": "nearest_rank", "pool_size": 8, "profiles": [{"concurrency": 64, "operations": 1000}], "queue_maxsize_factor": 2, "rss_growth_limit_bytes": 134217728, "worker_model": "long_lived"}`

| Profile | Status | Operations | Concurrency | Metrics |
|---|---|---:|---:|---|
| 1 | passed | 1000 | 64 | {"admitted_driver_latency_ns": {"max_ns": 47530500, "p50_ns": 17450708, "p95_ns": 30921833, "p99_ns": 33462375}, "application_name": "fastmssql_result_stress_89b0e5f6c035", "completed_id_count": 1000, "completed_id_max": 999, "completed_id_min": 0, "completed_id_sum": 499500, "completed_ids_sha256": "8db91b2ee25d579493dbc2ca66417cc945e215b5424349884013834d43df7ac4", "concurrency": 64, "duplicate_ids": [], "event_loop_ticker": {"count": 60, "max_scheduling_gap_seconds": 0.006223709089681506}, "failed": 0, "failure_types": {}, "max_concurrent_sql_spids": 8, "missing_ids": [], "operations": 1000, "operations_per_second": 3161.5642313269122, "pool": {"deltas": {"get_direct": 14, "get_started": 1000, "get_timed_out": 0, "get_wait_time_seconds": 15.482239, "get_waited": 986}, "final_active_connections": 0, "peak_active_connections": 8, "peak_pending_gets": 62}, "post_load_smoke": true, "python_process_cpu_seconds": 0.6092314640000002, "queue_maxsize": 128, "rss": {"baseline_bytes": 37617664, "final_bytes": 55361536, "growth_bytes": 17743872, "limit_bytes": 134217728, "peak_bytes": 55361536}, "sampler_iterations": 43, "scheduled_end_to_end_latency_ns": {"max_ns": 80681625, "p50_ns": 60788084, "p95_ns": 68300792, "p99_ns": 76557500}, "sql_session_cpu_time_ms_delta": 1, "status": "passed", "succeeded": 1000, "timed_out": 0, "total": 1000, "unique_sql_spid_count": 8, "unique_sql_spids": [60, 61, 62, 63, 64, 65, 66, 67], "violations": [], "wall_duration_seconds": 0.316299125, "worker_count": 64} |
