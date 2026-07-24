# FastMssql strict SQL-auth test matrix

Missing evidence is reported as `NOT RUN`; it is never promoted to a pass.

| Case | Status | Test node | Seconds | Evidence | Requirement |
|---|---|---|---:|---|---|
| `ENV-001` | NOT RUN |  |  |  | container name, image, port, and health state are exact. |
| `ENV-002` | NOT RUN |  |  |  | `SERVERPROPERTY('Edition')` reports Developer Edition. |
| `ENV-003` | NOT RUN |  |  |  | SQL Server version/build and compatibility level are recorded. |
| `ENV-004` | NOT RUN |  |  |  | SQL authentication succeeds for the owner login. |
| `ENV-005` | NOT RUN |  |  |  | Windows/Azure credentials are absent from the exercised paths. |
| `ENV-006` | NOT RUN |  |  |  | databases, users, roles, and permissions are idempotently created. |
| `ENV-007` | NOT RUN |  |  |  | credentials are absent from tracked files and captured logs. |
| `AUTH-001` | NOT RUN |  |  |  | valid owner username/password via connection string. |
| `AUTH-002` | NOT RUN |  |  |  | valid owner username/password via individual parameters. |
| `AUTH-003` | NOT RUN |  |  |  | password containing supported punctuation and delimiters. |
| `AUTH-004` | NOT RUN |  |  |  | invalid username. |
| `AUTH-005` | NOT RUN |  |  |  | invalid password. |
| `AUTH-006` | NOT RUN |  |  |  | missing password with individual parameters. |
| `AUTH-007` | NOT RUN |  |  |  | missing authentication method. |
| `AUTH-008` | NOT RUN |  |  |  | nonexistent database. |
| `AUTH-009` | NOT RUN |  |  |  | readonly login can select. |
| `AUTH-010` | NOT RUN |  |  |  | readonly login cannot insert, update, delete, or create. |
| `AUTH-011` | NOT RUN |  |  |  | denied login receives a stable permission error. |
| `AUTH-012` | NOT RUN |  |  |  | `SUSER_SNAME()`, `ORIGINAL_LOGIN()`, and `USER_NAME()` identify |
| `AUTH-013` | NOT RUN |  |  |  | connection and error representations do not disclose passwords. |
| `CONN-001` | NOT RUN |  |  |  | connection-string parsing with host and port. |
| `CONN-002` | NOT RUN |  |  |  | individual server/database/user/password/port parameters. |
| `CONN-003` | NOT RUN |  |  |  | connection string precedence when extra individual arguments are |
| `CONN-004` | NOT RUN |  |  |  | application name is visible in `APP_NAME()`. |
| `CONN-005` | NOT RUN |  |  |  | valid ReadWrite application intent. |
| `CONN-006` | NOT RUN |  |  |  | ReadOnly intent behavior on a standalone server is documented. |
| `CONN-007` | NOT RUN |  |  |  | invalid application intent fails before network I/O. |
| `CONN-008` | NOT RUN |  |  |  | malformed connection string. |
| `CONN-009` | NOT RUN |  |  |  | unreachable host and closed port. |
| `CONN-010` | NOT RUN |  |  |  | lazy first connection. |
| `CONN-011` | NOT RUN |  |  |  | explicit `connect()` and repeated `connect()`. |
| `CONN-012` | NOT RUN |  |  |  | `disconnect()` before and after connection. |
| `CONN-013` | NOT RUN |  |  |  | reconnect after disconnect. |
| `CONN-014` | NOT RUN |  |  |  | async context manager normal exit. |
| `CONN-015` | NOT RUN |  |  |  | async context manager exceptional exit. |
| `CONN-016` | NOT RUN |  |  |  | sequential reuse of the same wrapper. |
| `CONN-017` | NOT RUN |  |  |  | nested/reentrant context behavior is deterministic. |
| `CONN-018` | NOT RUN |  |  |  | `is_connected()` state transitions. |
| `CONN-019` | NOT RUN |  |  |  | `pool_stats()` keys and arithmetic invariants. |
| `POOL-001` | NOT RUN |  |  |  | default configuration values match runtime behavior. |
| `POOL-002` | NOT RUN |  |  |  | all preset configurations. |
| `POOL-003` | NOT RUN |  |  |  | adaptive configuration boundary inputs. |
| `POOL-004` | NOT RUN |  |  |  | invalid sizes and timeout values. |
| `POOL-005` | NOT RUN |  |  |  | minimum idle warmup. |
| `POOL-006` | NOT RUN |  |  |  | concurrent lazy initialization creates one shared pool. |
| `POOL-007` | NOT RUN |  |  |  | connection reuse is observable through server session IDs. |
| `POOL-008` | NOT RUN |  |  |  | parallel acquisition up to `max_size`. |
| `POOL-009` | NOT RUN |  |  |  | saturation produces pool timeout. |
| `POOL-010` | NOT RUN |  |  |  | resources return after task completion. |
| `POOL-011` | NOT RUN |  |  |  | resources return after query error. |
| `POOL-012` | NOT RUN |  |  |  | resources return after task cancellation. |
| `POOL-013` | NOT RUN |  |  |  | idle timeout retires eligible connections. |
| `POOL-014` | NOT RUN |  |  |  | max lifetime retires eligible connections. |
| `POOL-015` | NOT RUN |  |  |  | checkout validation behavior. |
| `POOL-016` | NOT RUN |  |  |  | broken connection is not returned as healthy. |
| `POOL-017` | NOT RUN |  |  |  | rapid connect/disconnect does not leak sessions. |
| `SQL-001` | NOT RUN |  |  |  | parameterized single-row SELECT. |
| `SQL-002` | NOT RUN |  |  |  | empty result. |
| `SQL-003` | NOT RUN |  |  |  | ordered multirow result. |
| `SQL-004` | NOT RUN |  |  |  | large result set. |
| `SQL-005` | NOT RUN |  |  |  | `simple_query()` raw statement. |
| `SQL-006` | NOT RUN |  |  |  | INSERT row count and persisted state. |
| `SQL-007` | NOT RUN |  |  |  | UPDATE row count and persisted state. |
| `SQL-008` | NOT RUN |  |  |  | DELETE row count and persisted state. |
| `SQL-009` | NOT RUN |  |  |  | zero-row DML count. |
| `SQL-010` | NOT RUN |  |  |  | DDL create/alter/drop. |
| `SQL-011` | NOT RUN |  |  |  | CTE and recursive CTE. |
| `SQL-012` | NOT RUN |  |  |  | joins, grouping, window functions, and subqueries. |
| `SQL-013` | NOT RUN |  |  |  | `OUTPUT` clause. |
| `SQL-014` | NOT RUN |  |  |  | MERGE behavior and row count. |
| `SQL-015` | NOT RUN |  |  |  | view creation/query/drop. |
| `SQL-016` | NOT RUN |  |  |  | scalar and table-valued function execution. |
| `SQL-017` | NOT RUN |  |  |  | stored procedure with input parameters and result rows. |
| `SQL-018` | NOT RUN |  |  |  | stored procedure with return status. |
| `SQL-019` | NOT RUN |  |  |  | trigger side effects. |
| `SQL-020` | NOT RUN |  |  |  | identity, sequence, default, and computed columns. |
| `SQL-021` | NOT RUN |  |  |  | local temporary table behavior on `Connection` is documented. |
| `SQL-022` | NOT RUN |  |  |  | local temporary table persists on `Transaction`. |
| `SQL-023` | NOT RUN |  |  |  | multiple result-set behavior is explicitly asserted. |
| `SQL-024` | NOT RUN |  |  |  | session-level `SET` state behavior through the pool is documented. |
| `SQL-025` | NOT RUN |  |  |  | comments, multiline SQL, and trailing semicolons. |
| `PARAM-001` | NOT RUN |  |  |  | `None` with inferable SQL type. |
| `PARAM-002` | NOT RUN |  |  |  | every `TypedNull` variant. |
| `PARAM-003` | NOT RUN |  |  |  | bool. |
| `PARAM-004` | NOT RUN |  |  |  | signed integer boundaries and overflow. |
| `PARAM-005` | NOT RUN |  |  |  | finite float, signed zero, infinity, and NaN behavior. |
| `PARAM-006` | NOT RUN |  |  |  | `Decimal` signs, precision, scale, and SQL maximum precision. |
| `PARAM-007` | NOT RUN |  |  |  | ASCII and Unicode strings. |
| `PARAM-008` | NOT RUN |  |  |  | emoji, supplementary-plane characters, combining characters, |
| `PARAM-009` | NOT RUN |  |  |  | empty and maximum-length strings. |
| `PARAM-010` | NOT RUN |  |  |  | bytes, bytearray, memoryview, empty binary, and large binary. |
| `PARAM-011` | NOT RUN |  |  |  | `date`. |
| `PARAM-012` | NOT RUN |  |  |  | naive and timezone-aware `datetime`. |
| `PARAM-013` | NOT RUN |  |  |  | `time`. |
| `PARAM-014` | NOT RUN |  |  |  | UUID. |
| `PARAM-015` | NOT RUN |  |  |  | `Parameter` and `Parameters` positional APIs. |
| `PARAM-016` | NOT RUN |  |  |  | `Parameters` named construction semantics. |
| `PARAM-017` | NOT RUN |  |  |  | list/tuple/set expansion for `IN`. |
| `PARAM-018` | NOT RUN |  |  |  | empty iterable expansion. |
| `PARAM-019` | NOT RUN |  |  |  | nested and unsupported objects. |
| `PARAM-020` | NOT RUN |  |  |  | placeholder count mismatch. |
| `PARAM-021` | NOT RUN |  |  |  | parameter ordering and repeated placeholders. |
| `PARAM-022` | NOT RUN |  |  |  | 2,100-parameter SQL Server boundary. |
| `PARAM-023` | NOT RUN |  |  |  | parameterized SQL-injection payload remains data. |
| `PARAM-024` | NOT RUN |  |  |  | conversion error class and message are stable and redacted. |
| `TYPE-001` | NOT RUN |  |  |  | TINYINT, SMALLINT, INT, and BIGINT including NULL/boundaries. |
| `TYPE-002` | NOT RUN |  |  |  | BIT maps to bool, not int. |
| `TYPE-003` | NOT RUN |  |  |  | REAL and FLOAT including NULL and extreme finite values. |
| `TYPE-004` | NOT RUN |  |  |  | DECIMAL and NUMERIC preserve exact sign/precision/scale. |
| `TYPE-005` | NOT RUN |  |  |  | MONEY and SMALLMONEY preserve four decimal places. |
| `TYPE-006` | NOT RUN |  |  |  | CHAR, VARCHAR, VARCHAR(MAX), TEXT, and collation behavior. |
| `TYPE-007` | NOT RUN |  |  |  | NCHAR, NVARCHAR, NVARCHAR(MAX), and NTEXT Unicode behavior. |
| `TYPE-008` | NOT RUN |  |  |  | BINARY, VARBINARY, VARBINARY(MAX), IMAGE, and ROWVERSION. |
| `TYPE-009` | NOT RUN |  |  |  | DATE. |
| `TYPE-010` | NOT RUN |  |  |  | TIME at supported precisions. |
| `TYPE-011` | NOT RUN |  |  |  | SMALLDATETIME, DATETIME, and DATETIME2 precisions. |
| `TYPE-012` | NOT RUN |  |  |  | DATETIMEOFFSET retains the instant and timezone offset. |
| `TYPE-013` | NOT RUN |  |  |  | UNIQUEIDENTIFIER. |
| `TYPE-014` | NOT RUN |  |  |  | XML. |
| `TYPE-015` | NOT RUN |  |  |  | nullable columns and mixed NULL/non-NULL rows. |
| `TYPE-016` | NOT RUN |  |  |  | duplicate, empty, mixed-case, and non-ASCII column names. |
| `TYPE-017` | NOT RUN |  |  |  | unsupported SQL_VARIANT/spatial/hierarchyid/UDT behavior is |
| `RESULT-001` | NOT RUN |  |  |  | row access by valid name and index. |
| `RESULT-002` | NOT RUN |  |  |  | negative and out-of-range row indices. |
| `RESULT-003` | NOT RUN |  |  |  | missing column behavior. |
| `RESULT-004` | NOT RUN |  |  |  | `columns()`, `values()`, `to_dict()`, and `len()`. |
| `RESULT-005` | NOT RUN |  |  |  | repr/str are safe and stable enough for diagnostics. |
| `RESULT-006` | NOT RUN |  |  |  | stream length, emptiness, row presence, and columns. |
| `RESULT-007` | NOT RUN |  |  |  | iteration order and exhaustion. |
| `RESULT-008` | NOT RUN |  |  |  | indexed access, negative index, and cache behavior. |
| `RESULT-009` | NOT RUN |  |  |  | slices and invalid slice steps. |
| `RESULT-010` | NOT RUN |  |  |  | `fetchone`, `fetchmany`, `fetchall`, and aliases. |
| `RESULT-011` | NOT RUN |  |  |  | mixed fetch methods update position consistently. |
| `RESULT-012` | NOT RUN |  |  |  | reset restores position. |
| `RESULT-013` | NOT RUN |  |  |  | empty result methods. |
| `RESULT-014` | NOT RUN |  |  |  | documented sync versus async iterator behavior matches runtime. |
| `RESULT-015` | NOT RUN |  |  |  | lazy-conversion and memory claims are measured, not inferred. |
| `BATCH-001` | NOT RUN |  |  |  | empty/single/multiple query batches. |
| `BATCH-002` | NOT RUN |  |  |  | parameterized mixed query batches. |
| `BATCH-003` | NOT RUN |  |  |  | result ordering and independent result objects. |
| `BATCH-004` | NOT RUN |  |  |  | query-batch midstream SQL error. |
| `BATCH-005` | NOT RUN |  |  |  | empty/single/multiple command batches. |
| `BATCH-006` | NOT RUN |  |  |  | row-count list order. |
| `BATCH-007` | NOT RUN |  |  |  | full atomic rollback on command-batch failure. |
| `BATCH-008` | NOT RUN |  |  |  | malformed batch item shapes. |
| `BATCH-009` | NOT RUN |  |  |  | basic bulk insert and persisted data. |
| `BATCH-010` | NOT RUN |  |  |  | empty data behavior. |
| `BATCH-011` | NOT RUN |  |  |  | mixed types and typed NULLs. |
| `BATCH-012` | NOT RUN |  |  |  | parameter-limit chunk boundary. |
| `BATCH-013` | NOT RUN |  |  |  | multiple chunks. |
| `BATCH-014` | NOT RUN |  |  |  | wide table. |
| `BATCH-015` | NOT RUN |  |  |  | quoted schema/table/column identifiers and reserved words. |
| `BATCH-016` | NOT RUN |  |  |  | malformed and malicious identifier input. |
| `BATCH-017` | NOT RUN |  |  |  | row-width mismatch. |
| `BATCH-018` | NOT RUN |  |  |  | constraint failure atomicity. |
| `BATCH-019` | NOT RUN |  |  |  | identity/default/computed/trigger interactions. |
| `BATCH-020` | NOT RUN |  |  |  | batch and bulk cancellation cleanup. |
| `TX-001` | NOT RUN |  |  |  | dedicated session ID remains constant. |
| `TX-002` | NOT RUN |  |  |  | explicit begin/commit persists data. |
| `TX-003` | NOT RUN |  |  |  | explicit begin/rollback discards data. |
| `TX-004` | NOT RUN |  |  |  | context manager auto-begin/commit. |
| `TX-005` | NOT RUN |  |  |  | context manager exception rollback and propagation. |
| `TX-006` | NOT RUN |  |  |  | manual commit/rollback within context. |
| `TX-007` | NOT RUN |  |  |  | repeated begin/commit/rollback state errors. |
| `TX-008` | NOT RUN |  |  |  | close and reuse behavior. |
| `TX-009` | NOT RUN |  |  |  | sequential reuse of the transaction object. |
| `TX-010` | NOT RUN |  |  |  | query/simple-query/execute forwarding. |
| `TX-011` | NOT RUN |  |  |  | query-batch and execute-batch forwarding. |
| `TX-012` | NOT RUN |  |  |  | local temporary table and session state. |
| `TX-013` | NOT RUN |  |  |  | DDL rollback. |
| `TX-014` | NOT RUN |  |  |  | savepoint behavior through raw SQL. |
| `TX-015` | NOT RUN |  |  |  | read-uncommitted/read-committed visibility. |
| `TX-016` | NOT RUN |  |  |  | blocking lock and release. |
| `TX-017` | NOT RUN |  |  |  | deterministic deadlock victim error. |
| `TX-018` | NOT RUN |  |  |  | cancellation leaves transaction state explicit and recoverable. |
| `TX-019` | NOT RUN |  |  |  | concurrent method calls serialize safely on the dedicated client. |
| `ASYNC-001` | NOT RUN |  |  |  | event-loop ticker progresses during `WAITFOR`. |
| `ASYNC-002` | NOT RUN |  |  |  | pooled `WAITFOR` queries overlap in wall-clock time. |
| `ASYNC-003` | NOT RUN |  |  |  | concurrent lane is at least twice as fast as a measured |
| `ASYNC-004` | NOT RUN |  |  |  | a Python thread progresses while Rust waits on SQL I/O. |
| `ASYNC-005` | NOT RUN |  |  |  | concurrent successful and failing tasks remain isolated. |
| `ASYNC-006` | NOT RUN |  |  |  | same `Connection` object is safe across concurrent tasks. |
| `ASYNC-007` | NOT RUN |  |  |  | multiple `Connection` wrappers operate concurrently. |
| `ASYNC-008` | NOT RUN |  |  |  | `asyncio.wait_for` cancellation occurs within a bounded time. |
| `ASYNC-009` | NOT RUN |  |  |  | connection/pool remains usable after cancellation. |
| `ASYNC-010` | NOT RUN |  |  |  | cancellation storm does not leak pool capacity. |
| `ASYNC-011` | NOT RUN |  |  |  | task cancellation during pool acquisition returns capacity. |
| `ASYNC-012` | NOT RUN |  |  |  | transaction operations serialize instead of corrupting state. |
| `ASYNC-013` | NOT RUN |  |  |  | concurrent result conversion does not starve the event loop |
| `ERR-001` | NOT RUN |  |  |  | syntax error. |
| `ERR-002` | NOT RUN |  |  |  | missing object. |
| `ERR-003` | NOT RUN |  |  |  | duplicate key. |
| `ERR-004` | NOT RUN |  |  |  | foreign-key/check/not-null violation. |
| `ERR-005` | NOT RUN |  |  |  | truncation and conversion failure. |
| `ERR-006` | NOT RUN |  |  |  | arithmetic failure. |
| `ERR-007` | NOT RUN |  |  |  | deadlock victim. |
| `ERR-008` | NOT RUN |  |  |  | permission denial. |
| `ERR-009` | NOT RUN |  |  |  | SQL error exposes code/message/state. |
| `ERR-010` | NOT RUN |  |  |  | connection error exposes safe host/port information. |
| `ERR-011` | NOT RUN |  |  |  | TLS failure uses the TLS error class when distinguishable. |
| `ERR-012` | NOT RUN |  |  |  | protocol/conversion error classes are reachable and meaningful. |
| `ERR-013` | NOT RUN |  |  |  | error does not poison the pool. |
| `ERR-014` | NOT RUN |  |  |  | batch error preserves the original SQL error. |
| `ERR-015` | NOT RUN |  |  |  | credentials and tokens are absent from every error string. |
| `TLS-001` | NOT RUN |  |  |  | required encryption plus trusted development certificate. |
| `TLS-002` | NOT RUN |  |  |  | connection-string `Encrypt`/`TrustServerCertificate`. |
| `TLS-003` | NOT RUN |  |  |  | individual-parameter `SslConfig.development()`. |
| `TLS-004` | NOT RUN |  |  |  | disabled/login-only behavior matches server policy. |
| `TLS-005` | NOT RUN |  |  |  | untrusted certificate failure. |
| `TLS-006` | NOT RUN |  |  |  | invalid CA path/content/extension. |
| `TLS-007` | NOT RUN |  |  |  | mutually exclusive trust options. |
| `TLS-008` | NOT RUN |  |  |  | TLS settings do not change the SQL-auth principal. |
| `RES-001` | NOT RUN |  |  |  | dedicated container pause produces bounded query failure/timeout. |
| `RES-002` | NOT RUN |  |  |  | unpause permits a new connection. |
| `RES-003` | NOT RUN |  |  |  | container restart invalidates old sessions predictably. |
| `RES-004` | NOT RUN |  |  |  | existing pool rejects broken connections. |
| `RES-005` | NOT RUN |  |  |  | reconnect/recreated pool works after readiness returns. |
| `RES-006` | NOT RUN |  |  |  | in-flight transaction is not falsely reported committed. |
| `RES-007` | NOT RUN |  |  |  | no test touches a non-FastMssql Docker container. |
| `LOAD-001` | NOT RUN |  |  |  | repeated short queries under controlled concurrency. |
| `LOAD-002` | NOT RUN |  |  |  | large result memory remains within a recorded bound. |
| `LOAD-003` | NOT RUN |  |  |  | repeated result conversion does not grow memory monotonically. |
| `LOAD-004` | NOT RUN |  |  |  | bulk insert throughput and correctness at increasing sizes. |
| `LOAD-005` | NOT RUN |  |  |  | rapid lifecycle operations do not grow SQL sessions. |
| `LOAD-006` | NOT RUN |  |  |  | mixed query/execute/transaction workload. |
| `LOAD-007` | NOT RUN |  |  |  | post-load smoke query proves recovery. |
| `FRAME-001` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_framework_dependencies_are_development_only_and_locked | 0.002434 | framework-results.json | framework dependencies are development-only and their locked |
| `FRAME-002` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_importing_fastmssql_does_not_import_frameworks | 0.021508 | framework-results.json | importing FastMssql does not import FastAPI, Flask, HTTPX, or |
| `FRAME-003` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_every_framework_mode_uses_owner_sql_auth | 0.029191 | framework-results.json | every framework lane authenticates as `fastmssql_owner` using |
| `FRAME-004` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_framework_outputs_never_disclose_credentials | 0.032840 | framework-results.json | credentials are absent from HTTP responses, exception strings, |
| `FRAME-005` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_lifespan_connects_and_disconnects_shared_pool | 0.035023 | framework-results.json | FastAPI lifespan connects one shared pool and disconnects it on |
| `FRAME-006` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_parameterized_read_write_routes | 0.038527 | framework-results.json | FastAPI parameterized read/write routes return correct HTTP and |
| `FRAME-007` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_request_transaction_commits | 0.032462 | framework-results.json | FastAPI request-scoped transaction commits on success. |
| `FRAME-008` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_request_transaction_rolls_back | 0.030690 | framework-results.json | FastAPI request-scoped transaction rolls back on failure. |
| `FRAME-009` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_concurrent_requests_beat_sequential_baseline | 5.084978 | framework-results.json | FastAPI concurrent `WAITFOR` requests beat a measured sequential |
| `FRAME-010` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_event_loop_ticks_during_sql_wait | 1.058833 | framework-results.json | the Python event loop continues ticking during FastAPI SQL waits. |
| `FRAME-011` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_request_cancellation_recovers_immediately | 0.077993 | framework-results.json | cancelling a FastAPI request does not leak pool capacity or |
| `FRAME-012` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_sql_error_preserves_type_and_code | 0.023841 | framework-results.json | a FastMssql SQL error propagates through FastAPI with its class |
| `FRAME-013` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_fastapi_500_response_and_logs_redact_credentials | 0.009172 | framework-results.json | the normal FastAPI 500 response and logs do not disclose SQL |
| `FRAME-014` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_async_wsgi_view_executes_real_query | 0.014247 | framework-results.json | Flask executes a real parameterized FastMssql query from an |
| `FRAME-015` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_shared_pool_crosses_distinct_request_loops | 0.011630 | framework-results.json | one shared FastMssql connection remains correct across |
| `FRAME-016` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_one_async_view_overlaps_database_operations | 5.059020 | framework-results.json | concurrent FastMssql operations inside one Flask async view |
| `FRAME-017` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_wsgi_worker_requests_are_correct_and_measured | 2.039591 | framework-results.json | concurrent Flask WSGI worker requests return correct independent |
| `FRAME-018` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_wsgi_error_is_typed_redacted_and_pool_recovers | 0.022730 | framework-results.json | Flask WSGI SQL failures remain typed, redact credentials, and |
| `FRAME-019` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_flask_wsgi_explicit_shutdown_removes_app_sessions | 0.029579 | framework-results.json | explicit Flask WSGI test shutdown disconnects the shared pool |
| `FRAME-020` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_executes_real_sql_auth_query | 0.011287 | framework-results.json | Flask wrapped by `WsgiToAsgi` executes real FastMssql SQL-auth |
| `FRAME-021` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_reuses_persistent_asgi_loop | 0.013275 | framework-results.json | sequential adapted Flask requests reuse the persistent ASGI |
| `FRAME-022` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_concurrent_requests_are_correct_and_measured | 8.030018 | framework-results.json | concurrent adapted Flask requests all complete correctly and |
| `FRAME-023` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_cancellation_has_bounded_recovery | 0.048121 | framework-results.json | cancelling an adapted Flask request has a bounded outcome and |
| `FRAME-024` | PASS | tests/sql_auth_strict/test_framework_integration.py::test_adapted_flask_shutdown_removes_app_sessions | 0.022663 | framework-results.json | adapted Flask startup and shutdown leave the FastMssql pool |
