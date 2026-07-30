# FastMssql SQL-auth validation report

## Scope

- Authentication under test: SQL Server username/password only.
- Azure authentication and Windows authentication are explicitly excluded.
- SQL Server target: isolated Developer Edition container.
- ARM64 hosts may execute the `linux/amd64` image under emulation; timings are diagnostic rather than marketing benchmarks.

## Environment

- Python: `3.12.10`
- Platform: `macOS-26.5-arm64-arm-64bit`
- Machine: `arm64`
- Git commit: `306b44d1aafce6b0dc763bfe179784de5bfd6f06`

## Matrix outcomes

| Status | Count |
|---|---:|
| PASS | 420 |
| FAIL | 0 |
| ERROR | 0 |
| SKIPPED | 0 |
| NOT RUN | 0 |

## Execution lanes

| Lane | Exit code | Tests | Failures | Errors | Skipped | Command |
|---|---:|---:|---:|---:|---:|---|
| async | 0 | 16 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/async-results.json uv run pytest tests/sql_auth_strict/test_async_strict.py --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/async.xml -vv |
| cargo-clippy | 0 | 0 | 0 | 0 | 0 | cargo clippy --all-targets -- -D warnings |
| cargo-fmt | 0 | 0 | 0 | 0 | 0 | cargo fmt --check |
| cargo-test | 0 | 0 | 0 | 0 | 0 | env PYO3_PYTHON=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.venv/bin/python3 PYTHONHOME=/Users/marcelgalea/.local/share/uv/python/cpython-3.12-macos-aarch64-none cargo test --locked |
| compose-up | 0 | 0 | 0 | 0 | 0 | docker compose --env-file /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.env.sql-auth.local -f docker-compose.sql-auth.yml up -d --force-recreate sqlserver |
| framework | 0 | 36 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/framework-results.json FASTMSSQL_FRAMEWORK_METRICS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/framework-metrics.json uv run pytest tests/sql_auth_strict/test_framework_integration.py --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/framework.xml -vv |
| load | 0 | 13 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/load-results.json FASTMSSQL_LOAD_METRICS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/load-metrics.json uv run pytest tests/sql_auth_strict/test_resilience_load.py -m load --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/load.xml -vv |
| maturin-develop | 0 | 0 | 0 | 0 | 0 | uv run maturin develop --release |
| provision | 0 | 0 | 0 | 0 | 0 | scripts/sql_auth/provision.sh |
| query-many-load | 0 | 0 | 0 | 0 | 0 | scripts/sql_auth/run_query_many_stress.sh |
| report | 0 | 0 | 0 | 0 | 0 | uv run python scripts/sql_auth/generate_report.py --spec docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md --strict-results /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/strict-results.json --artifact-dir /Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth --matrix-output docs/SQL_AUTH_TEST_MATRIX.md --report-output docs/SQL_AUTH_TEST_REPORT.md --require-complete |
| resilience | 0 | 6 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/resilience-results.json uv run pytest tests/sql_auth_strict/test_resilience_load.py -m resilience --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/resilience.xml -vv |
| result-stream-load | 0 | 0 | 0 | 0 | 0 | scripts/sql_auth/run_result_stream_stress.sh |
| strict | 0 | 434 | 0 | 0 | 0 | env FASTMSSQL_SQL_AUTH_RESULTS_PATH=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/strict-results.json uv run pytest tests/sql_auth_strict/test_matrix_contract.py tests/sql_auth_strict/test_environment_auth.py tests/sql_auth_strict/test_connection.py tests/sql_auth_strict/test_pool.py tests/sql_auth_strict/test_pool_observability.py tests/sql_auth_strict/test_operation_metrics.py tests/sql_auth_strict/test_sql_features.py tests/sql_auth_strict/test_parameters_strict.py tests/sql_auth_strict/test_type_mapping_strict.py tests/sql_auth_strict/test_results_strict.py tests/sql_auth_strict/test_resultsets_streaming.py tests/sql_auth_strict/test_resultstream_lifecycle.py tests/sql_auth_strict/test_rpc_results.py tests/sql_auth_strict/test_batch_strict.py tests/sql_auth_strict/test_native_bulk_strict.py tests/sql_auth_strict/test_native_bulk_iterable_strict.py tests/sql_auth_strict/test_execute_many_strict.py tests/sql_auth_strict/test_query_many_strict.py tests/sql_auth_strict/test_transactions_strict.py tests/sql_auth_strict/test_operation_timeouts.py tests/sql_auth_strict/test_lifecycle.py tests/sql_auth_strict/test_errors_tls.py -m not\ resilience\ and\ not\ load --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/strict.xml -vv |
| tiberius-bulk-column-subset-sql-auth | 0 | 0 | 0 | 0 | 0 | cargo test --manifest-path vendor/tiberius/Cargo.toml --no-default-features --features chrono\,tds73\,rustls --test bulk_column_subset_sql_auth -- --test-threads=1 |
| tiberius-clippy | 0 | 0 | 0 | 0 | 0 | cargo clippy --manifest-path vendor/tiberius/Cargo.toml --no-default-features --features chrono\,tds73\,rustls --all-targets -- -D warnings -A clippy::doc_lazy_continuation -A clippy::extra_unused_lifetimes -A clippy::large_enum_variant -A clippy::io_other_error -A clippy::needless_lifetimes -A clippy::legacy_numeric_constants -A clippy::cast_enum_truncation -A clippy::derivable_impls -A clippy::manual_div_ceil -A clippy::items_after_test_module |
| tiberius-fmt | 0 | 0 | 0 | 0 | 0 | cargo fmt --manifest-path vendor/tiberius/Cargo.toml --check |
| tiberius-lib | 0 | 0 | 0 | 0 | 0 | cargo test --manifest-path vendor/tiberius/Cargo.toml --no-default-features --features chrono\,tds73\,rustls --lib |
| tiberius-response-sql-auth | 0 | 0 | 0 | 0 | 0 | cargo test --manifest-path vendor/tiberius/Cargo.toml --no-default-features --features chrono\,tds73\,rustls --test response_events_sql_auth -- --test-threads=1 |
| tiberius-token-safety-sql-auth | 0 | 0 | 0 | 0 | 0 | cargo test --manifest-path vendor/tiberius/Cargo.toml --no-default-features --features chrono\,tds73\,rustls --test token_safety_sql_auth -- --test-threads=1 |
| original-local-regression | 0 | 1253 | 0 | 0 | 0 | uv run pytest -n 1 tests --ignore=tests/test_azure_auth_advanced.py --ignore=tests/test_azure_authentication.py --ignore=tests/test_azure_cli_path_validation.py --ignore=tests/test_transaction_azure_auth.py --ignore=tests/test_transaction_azure_auth_advanced.py --ignore=tests/sql_auth_strict --junitxml=/Users/marcelgalea/Developer/fast_mssql_testdev/FastMssql/.worktrees/feat-bulk-iterable-backpressure/.artifacts/sql-auth/upstream.xml -vv |
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
| `FRAME-009` | {"concurrent_seconds": 1.0105731249786913, "ratio": 0.25087796021672526, "sequential_seconds": 4.028146291151643} |
| `FRAME-010` | {"ticker_count": 47} |
| `FRAME-011` | {"cancellation_seconds": 0.00010920804925262928, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "connections_closed_broken": 1, "connections_closed_idle_timeout": 0, "connections_closed_invalid": 0, "connections_closed_max_lifetime": 0, "connections_created": 2, "get_direct": 1, "get_started": 3, "get_timed_out": 0, "get_wait_time_seconds": 0.017519, "get_waited": 2, "idle_connections": 1, "max_size": 4, "min_idle": 0, "pending_gets": 0}} |
| `FRAME-015` | {"distinct_request_loops": true, "loop_ids": [4517047584, 4517038704]} |
| `FRAME-016` | {"concurrent": [0, 1, 2, 3], "concurrent_seconds": 1.0357431669253856, "sequential": [0, 1, 2, 3], "sequential_seconds": 4.024752541910857} |
| `FRAME-017` | {"elapsed_seconds": 2.0245672091841698, "execution_model": "WSGI worker-bound", "requests": 4} |
| `FRAME-021` | {"loop_id": 4516743600, "persistent_loop": true} |
| `FRAME-022` | {"elapsed_seconds": 8.026647791964933, "execution_model": "persistent ASGI loop around Flask/WSGI", "requests": 4} |
| `FRAME-023` | {"cancellation_seconds": 0.0003025829792022705, "pool_after": {"active_connections": 0, "connected": true, "connections": 1, "connections_closed_broken": 1, "connections_closed_idle_timeout": 0, "connections_closed_invalid": 0, "connections_closed_max_lifetime": 0, "connections_created": 2, "get_direct": 1, "get_started": 3, "get_timed_out": 0, "get_wait_time_seconds": 0.017923, "get_waited": 2, "idle_connections": 1, "max_size": 4, "min_idle": 0, "pending_gets": 0}, "recovery_bound_seconds": 3.5} |
| `OPMET-014` | {"elapsed_seconds": 0.04957208293490112, "event_loop_ticks": 274, "exact_query_delta": true, "maximum_connections": 8, "pool_max_size": 8, "request_count": 100, "worker_count": 20} |
| `OPMET-015` | {"distinct_request_loops": true, "execution_model": "WSGI per-request event loop", "query_count": 42, "worker_count": 10} |
| `OPMET-016` | {"event_loop_ticks": 2359, "execution_model": "persistent ASGI loop around Flask/WSGI", "persistent_loop": true, "query_count": 42, "worker_count": 10} |

## Load metrics

| Case | Metrics |
|---|---|
| `LOAD-001` | {"elapsed_seconds": 0.20975133287720382, "queries_per_second": 4767.550157049238} |
| `LOAD-002` | {"python_current_bytes": 1592600, "python_peak_bytes": 4793756, "rss_after_bytes": 114360320, "rss_before_bytes": 93323264} |
| `LOAD-003` | {"python_current_bytes": 49952, "python_peak_bytes": 63784, "samples_bytes": [49456, 49552, 49584, 49616, 49648, 49712, 49744, 49776, 49808, 49904]} |
| `LOAD-004` | {"elapsed_by_size": {"1": 0.010783832985907793, "100": 0.00906670792028308, "1000": 0.11774504208005965, "10000": 0.34373395796865225}} |
| `LOAD-005` | {"baseline_sessions": 0, "elapsed_seconds": 1.8039351250045002, "final_sessions": 0} |
| `LOAD-006` | {"elapsed_seconds": 0.3703504998702556, "operation_count": 500} |
| `LOAD-008` | {"concurrency": 50, "distinct_session_count": 32, "elapsed_seconds": 1.4868270829319954, "transaction_count": 1000, "transactions_per_second": 672.573167034339} |
| `LOAD-009` | {"elapsed_seconds": 0.16072616702876985, "peak_observed_sessions": 20, "pool_max_size": 20, "probe_count": 1000, "probes_per_second": 6221.762258668191, "task_concurrency": 100} |
| `OBS-009` | {"elapsed_seconds": 1.463510625064373, "event_loop_ticks": 40795, "max_pending_gets": 100, "operation_count": 10000, "physical_connections": 20, "pool_max_size": 20, "queries_per_second": 6832.885138473215, "scrape_samples": 10801, "worker_count": 100} |
| `OPMET-011` | {"elapsed_seconds": 1.4478888330049813, "event_loop_ticks": 30493, "exact_outcomes": {"cancelled": 0, "errors": 0, "outcome_unknown": 0, "succeeded": 10000, "timed_out": 0}, "maximum_connections": 20, "maximum_in_flight": 100, "operation_count": 10000, "pool_max_size": 20, "queries_per_second": 6906.607587576854, "query_histogram": [0, 0, 0, 10, 1637, 2359, 2797, 9223, 9817, 9880, 9981, 10001, 10001, 10001, 10001, 10001, 10001], "scrape_samples": 4467, "worker_count": 100} |
| `PARAM-033` | {"completed": 1000, "elapsed_seconds": 0.6871684589423239, "failed": 0, "maximum_in_flight": 64, "operations_per_second": 1455.2472352109703, "physical_sessions": 8, "pool_max_size": 8, "submitted": 1000, "succeeded": 1000} |
| `QMANY-013` | {"operations": 1000} |

## Result-stream stress metrics

- Schema version: `1`
- Evidence status: `passed`
- Source SHA: `306b44d1aafce6b0dc763bfe179784de5bfd6f06`
- Configuration: `{"buffer_size": 8, "percentile_method": "nearest_rank", "pool_size": 8, "profiles": [{"concurrency": 64, "operations": 1000}], "queue_maxsize_factor": 2, "rss_growth_limit_bytes": 134217728, "worker_model": "long_lived"}`

| Profile | Status | Operations | Concurrency | Metrics |
|---|---|---:|---:|---|
| 1 | passed | 1000 | 64 | {"admitted_driver_latency_ns": {"max_ns": 38943834, "p50_ns": 20572333, "p95_ns": 23238208, "p99_ns": 33550458}, "application_name": "fastmssql_result_stress_693f49cf1515", "completed_id_count": 1000, "completed_id_max": 999, "completed_id_min": 0, "completed_id_sum": 499500, "completed_ids_sha256": "8db91b2ee25d579493dbc2ca66417cc945e215b5424349884013834d43df7ac4", "concurrency": 64, "duplicate_ids": [], "event_loop_ticker": {"count": 65, "max_scheduling_gap_seconds": 0.006083708023652434}, "failed": 0, "failure_types": {}, "max_concurrent_sql_spids": 8, "missing_ids": [], "operations": 1000, "operations_per_second": 3033.036209523658, "pool": {"deltas": {"get_direct": 11, "get_started": 1000, "get_timed_out": 0, "get_wait_time_seconds": 17.650076, "get_waited": 989}, "final_active_connections": 0, "peak_active_connections": 8, "peak_pending_gets": 63}, "post_load_smoke": true, "python_process_cpu_seconds": 0.7074617089999999, "queue_maxsize": 128, "rss": {"baseline_bytes": 39895040, "final_bytes": 54951936, "growth_bytes": 15056896, "limit_bytes": 134217728, "peak_bytes": 54951936}, "sampler_iterations": 42, "scheduled_end_to_end_latency_ns": {"max_ns": 80099792, "p50_ns": 61675208, "p95_ns": 64749417, "p99_ns": 73622416}, "sql_session_cpu_time_ms_delta": 1, "status": "passed", "succeeded": 1000, "timed_out": 0, "total": 1000, "unique_sql_spid_count": 8, "unique_sql_spids": [68, 69, 70, 71, 72, 73, 74, 75], "violations": [], "wall_duration_seconds": 0.329702625, "worker_count": 64} |

## Query-many stress metrics

- Schema version: `1`
- Evidence status: `passed`
- Source SHA: `306b44d1aafce6b0dc763bfe179784de5bfd6f06`
- Profile count: `4`
- Maximum active queries: `16`
- Maximum SQL sessions: `16`
- Maximum accepted window: `16`
- Maximum RSS growth bytes: `15319040`
- Maximum event-loop gap seconds: `0.007362916832789779`

| Profile | Status | Operations | Source | Ordered | Requested concurrency | Pool max | Active queries | SQL sessions | Accepted window | RSS growth bytes | Event-loop gap seconds |
|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | passed | 1000 | sync | yes | 10 | 10 | 10 | 10 | 10 | 11239424 | 0.005959667032584548 |
| 2 | passed | 1000 | async | no | 25 | 8 | 8 | 8 | 8 | 2801664 | 0.005423165857791901 |
| 3 | passed | 10000 | sync | no | 50 | 16 | 16 | 16 | 16 | 15319040 | 0.006542999995872378 |
| 4 | passed | 10000 | async | yes | 100 | 16 | 16 | 16 | 16 | 3653632 | 0.007362916832789779 |
