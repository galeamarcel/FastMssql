# FastMssql SQL Authentication Validation Design

**Status:** approved in conversation on 2026-07-24
**Repository baseline:** `Rivendael/FastMssql` at `e45f301f46128e7114c27097b608a4b2d7f429cf`
**Working branch:** `test/sql-auth-validation`

## 1. Goal and completion boundary

This work will produce a reproducible, strict integration test system for
FastMssql against a new SQL Server Developer Edition container using SQL Server
username/password authentication only.

“All possible interactions” is bounded to:

- every public FastMssql API that can be exercised with SQL authentication;
- every SQL Server input/output type implemented by FastMssql;
- documented connection, pool, query, result, batch, bulk, transaction, TLS,
  error, concurrency, cancellation, recovery, and resource-limit behavior;
- representative SQL Server language features that exercise distinct driver
  behavior;
- negative cases, boundary values, state transitions, and concurrent use.

It does not mean enumerating every semantically equivalent SQL statement.
Windows Authentication and all Azure authentication modes are excluded. SQL
Server features that require infrastructure absent from a single local
container, such as Always On availability groups, failover clusters, Azure SQL
routing, and distributed transactions, are also excluded and must be listed as
environmental exclusions in the final report.

Completion requires both a strict new test suite and an evidence-based run of
the applicable upstream regression suite. A test count alone is not evidence:
the current upstream suite contains 978 test functions, 506 broad
`except Exception` handlers across 34 files, and 40 skip/xfail references.

## 2. Chosen approach

Use a layered SQL-auth validation suite alongside the existing upstream tests.

1. Preserve the upstream suite as a regression signal.
2. Add a new strict suite whose assertions cannot silently accept failures.
3. Run both suites against an isolated SQL Server Developer instance.
4. Classify every failure as test defect, environment limitation, unsupported
   behavior, documentation mismatch, or FastMssql defect.
5. Reproduce each FastMssql defect with a minimal failing regression test.
6. Implement fixes on dedicated `fix/sql-auth-<slug>` branches.
7. Publish a fork or pull request only after explicit user approval.

The upstream repository remains untouched on GitHub. Local commits stay on
feature/fix branches until publication is approved.

## 3. Environment architecture

### 3.1 Dedicated SQL Server

Create a Compose definition dedicated to this repository:

- container: `fastmssql-sql-auth-dev`;
- image: `mcr.microsoft.com/mssql/server:2022-latest`;
- edition: `MSSQL_PID=Developer`;
- host port: `14334`;
- container port: `1433`;
- platform: `linux/amd64`;
- persistent named volume used only by this test environment;
- health check using the image's `sqlcmd` client;
- `ACCEPT_EULA=Y`;
- strong credentials read from an ignored local environment file.

The Docker engine runs on ARM64 while the SQL Server image is AMD64. Microsoft
documents emulation as unsupported. The functional results are useful for
FastMssql development, but the final report must record this platform risk and
must not represent it as a Microsoft-supported production configuration.

The new container and port must not modify, stop, or reuse any existing local
SQL Server container.

### 3.2 Databases and SQL logins

Provision these isolated databases:

- `fastmssql_validation` for the strict suite;
- `fastmssql_upstream_regression` for applicable upstream tests.

Provision SQL-auth logins with distinct responsibilities:

- `fastmssql_owner`: normal test application login with ownership of the
  validation databases;
- `fastmssql_readonly`: SELECT-only login used to prove permission boundaries;
- `fastmssql_denied`: login with deliberately restricted permissions for
  negative authorization cases;
- `sa`: provisioning and environment verification only.

All normal driver tests use `fastmssql_owner`, not `sa`. Passwords and complete
connection strings are never committed or printed in reports.

### 3.3 Reproducible orchestration

The repository will contain:

- a Compose file;
- a sanitized environment example;
- an ignored local environment file;
- a provisioning SQL script;
- a wait/readiness script;
- commands for start, provision, verify, test, restart, and stop;
- exact environment metadata capture for the report.

The provisioning script must be idempotent. Re-running it must repair missing
logins/databases/users without deleting unrelated Docker resources.

## 4. Test suite architecture

The strict suite lives under `tests/sql_auth_strict/` and runs serially by
default. Concurrency is created deliberately inside tests rather than through
pytest-xdist. Tests use unique object names and deterministic cleanup fixtures.

The suite is separated into these responsibilities:

- `conftest.py`: validated environment configuration and database fixtures;
- `helpers/names.py`: collision-free SQL identifier generation;
- `helpers/cleanup.py`: tracked object cleanup that preserves the primary
  failure;
- `helpers/timing.py`: monotonic timing and event-loop responsiveness probes;
- `helpers/assertions.py`: exact SQL error and pool-state assertions;
- focused test modules matching the matrix in section 6.

Strict-suite rules:

- no `except Exception` or bare `except`;
- no conditional assertion that accepts contradictory outcomes;
- no runtime `pytest.skip` for behavior claimed as supported;
- specific exception classes and SQL error numbers where stable;
- every expected skip must be declared in the matrix with a fixed reason;
- test cleanup cannot turn a failing assertion into a pass;
- tests must assert database state after write, rollback, and recovery behavior;
- credentials must be redacted from assertion messages and reports.

## 5. Data flow and execution lanes

### 5.1 Baseline lane

1. Build the Rust extension in the locked Python environment.
2. Run Rust formatting, linting, and unit tests.
3. Verify container edition, version, authentication mode, databases, and
   principals.
4. Run a minimal FastMssql SQL-auth smoke test.

### 5.2 Strict functional lane

Run the complete strict matrix against `fastmssql_validation`. This lane is the
primary correctness gate and must have zero unexpected skips.

### 5.3 Upstream regression lane

Run all upstream tests that are compatible with SQL authentication. Exclude
Azure-authentication modules explicitly rather than relying on their internal
skip behavior. Capture pass, fail, skip, and error counts. Broad exception
handlers in upstream tests are reported as confidence limitations.

### 5.4 Async and stress lane

Run deterministic event-loop, concurrency, saturation, and bounded stress
tests separately so timing evidence is not polluted by functional test setup.
Performance measurements are reported with thresholds chosen in advance.

### 5.5 Resilience lane

Use only `fastmssql-sql-auth-dev` for disruptive tests. Restart or pause that
container, verify expected failures, resume it, and verify recovery. No other
container may be targeted.

## 6. Required test matrix

Every identifier below must appear in the final matrix with a result and
evidence reference.

### ENV — environment and provisioning

- `ENV-001`: container name, image, port, and health state are exact.
- `ENV-002`: `SERVERPROPERTY('Edition')` reports Developer Edition.
- `ENV-003`: SQL Server version/build and compatibility level are recorded.
- `ENV-004`: SQL authentication succeeds for the owner login.
- `ENV-005`: Windows/Azure credentials are absent from the exercised paths.
- `ENV-006`: databases, users, roles, and permissions are idempotently created.
- `ENV-007`: credentials are absent from tracked files and captured logs.

### AUTH — SQL authentication and authorization

- `AUTH-001`: valid owner username/password via connection string.
- `AUTH-002`: valid owner username/password via individual parameters.
- `AUTH-003`: password containing supported punctuation and delimiters.
- `AUTH-004`: invalid username.
- `AUTH-005`: invalid password.
- `AUTH-006`: missing password with individual parameters.
- `AUTH-007`: missing authentication method.
- `AUTH-008`: nonexistent database.
- `AUTH-009`: readonly login can select.
- `AUTH-010`: readonly login cannot insert, update, delete, or create.
- `AUTH-011`: denied login receives a stable permission error.
- `AUTH-012`: `SUSER_SNAME()`, `ORIGINAL_LOGIN()`, and `USER_NAME()` identify
  the intended SQL principal.
- `AUTH-013`: connection and error representations do not disclose passwords.

### CONN — connection construction and lifecycle

- `CONN-001`: connection-string parsing with host and port.
- `CONN-002`: individual server/database/user/password/port parameters.
- `CONN-003`: connection string precedence when extra individual arguments are
  supplied.
- `CONN-004`: application name is visible in `APP_NAME()`.
- `CONN-005`: valid ReadWrite application intent.
- `CONN-006`: ReadOnly intent behavior on a standalone server is documented.
- `CONN-007`: invalid application intent fails before network I/O.
- `CONN-008`: malformed connection string.
- `CONN-009`: unreachable host and closed port.
- `CONN-010`: lazy first connection.
- `CONN-011`: explicit `connect()` and repeated `connect()`.
- `CONN-012`: `disconnect()` before and after connection.
- `CONN-013`: reconnect after disconnect.
- `CONN-014`: async context manager normal exit.
- `CONN-015`: async context manager exceptional exit.
- `CONN-016`: sequential reuse of the same wrapper.
- `CONN-017`: nested/reentrant context behavior is deterministic.
- `CONN-018`: `is_connected()` state transitions.
- `CONN-019`: `pool_stats()` keys and arithmetic invariants.
- `CONN-020`: default `connect()` rejects lazy pool allocation as readiness
  when the SQL Server endpoint is unreachable.
- `CONN-021`: `connect(validate=False)` preserves explicit lazy allocation and
  `ping()` still detects the unreachable endpoint.
- `CONN-022`: default `connect()` creates an authenticated SQL session before
  any application query when `min_idle=0`.
- `CONN-023`: async connection context entry validates SQL Server before
  entering the body.
- `CONN-024`: a failed `ping()` on a killed physical connection retires it and
  a second explicit `ping()` recovers on a different `connection_id`.

### POOL — pooling behavior

- `POOL-001`: default configuration values match runtime behavior.
- `POOL-002`: all preset configurations.
- `POOL-003`: adaptive configuration boundary inputs.
- `POOL-004`: invalid sizes and timeout values.
- `POOL-005`: minimum idle warmup.
- `POOL-006`: concurrent lazy initialization creates one shared pool.
- `POOL-007`: connection reuse is observable through server session IDs.
- `POOL-008`: parallel acquisition up to `max_size`.
- `POOL-009`: saturation produces pool timeout.
- `POOL-010`: resources return after task completion.
- `POOL-011`: resources return after query error.
- `POOL-012`: resources return after task cancellation.
- `POOL-013`: idle timeout retires eligible connections.
- `POOL-014`: max lifetime retires eligible connections.
- `POOL-015`: checkout validation behavior.
- `POOL-016`: broken connection is not returned as healthy.
- `POOL-017`: rapid connect/disconnect does not leak sessions.
- `POOL-018`: checkout reset rolls back a leaked local transaction without
  replacing the physical SQL Server session.
- `POOL-019`: a nonfatal SQL error still causes complete session-state reset
  before the physical connection is reused.
- `POOL-020`: checkout validation resets the prior lease before its health
  probe and preserves the healthy physical SQL Server session.
- `POOL-021`: a session that executes database-user impersonation is retired
  before another pool lease can observe that security context.
- `POOL-022`: a batch that changes the database principal before raising a
  nonfatal SQL error still retires the physical session.
- `POOL-023`: scope-bound database-user impersonation inside dynamic SQL
  reverts before the operation completes and does not unnecessarily retire the
  physical session.

### OBS — pool observability

- `OBS-001`: exact public key/type schema, zero disconnected snapshot and all
  arithmetic invariants.
- `OBS-002`: direct checkout and physical creation counters increase after
  successful SQL and remain bounded by `max_size`.
- `OBS-003`: pool saturation exposes a server-gated pending checkout, then a
  waited completion and positive accumulated wait time.
- `OBS-004`: acquire timeout increments `get_timed_out` exactly once and pool
  recovery remains successful.
- `OBS-005`: cancelled pooled work increments
  `connections_closed_broken` and replaces the physical session.
- `OBS-006`: a killed idle session with checkout validation enabled increments
  `connections_closed_invalid` before a healthy replacement is returned.
- `OBS-007`: maximum-lifetime retirement increments only its exact close
  category.
- `OBS-008`: idle reaping increments only its exact close category.
- `OBS-009`: concurrent scraping during 10,000 bounded SQL operations
  preserves invariants, event-loop progress and post-load health.
- `OBS-010`: SQL, parameters, application/database/login identifiers and
  credentials are absent from pool statistics and evidence.

### OPMET — operation duration and outcome metrics

- `OPMET-001`: exported configuration API, exact bool/default/repr behavior,
  constructor compatibility and isolated-copy semantics.
- `OPMET-002`: disabled exact schema remains zero across success, error and
  cancellation; returned snapshots are mutation-isolated.
- `OPMET-003`: successful connect/query/simple-query/execute calls publish
  exact counts, durations and cumulative buckets.
- `OPMET-004`: returned SQL/lifecycle errors increment only `errors`, while
  synchronous pre-future validation is excluded.
- `OPMET-005`: a pool acquisition deadline increments only `timed_out` and
  preserves recovery.
- `OPMET-006`: operation and graceful-shutdown deadline errors increment only
  `timed_out` for their public operations and preserve lifecycle recovery.
- `OPMET-007`: server-confirmed cancellation increments only `cancelled`,
  safely retires the transport and preserves recovery.
- `OPMET-008`: pooled transaction operations aggregate into their owner and
  standalone direct transactions do not.
- `OPMET-009`: connect, ping, context, disconnect and reconnect keep one
  connection-lifetime registry without internal-operation double counting.
- `OPMET-010`: query batch, dedicated-socket execute batch and bulk insert each
  publish one whole-call metric with exact effects.
- `OPMET-011`: 10,000 bounded concurrent operations plus continuous scraping
  preserve every invariant, event-loop progress and session cleanup.
- `OPMET-012`: statistics and evidence contain no SQL, parameters,
  identifiers, credentials, errors or arbitrary labels.
- `OPMET-013`: deterministic COMMIT acknowledgement loss publishes exactly one
  `outcome_unknown` and preserves the typed error/retirement contract.
- `OPMET-014`: FastAPI/native ASGI concurrency publishes exact operation
  deltas without blocking event-loop progress.
- `OPMET-015`: Flask async under WSGI publishes exact deltas while preserving
  its distinct per-request event-loop limitation.
- `OPMET-016`: Flask through WsgiToAsgi publishes exact concurrent deltas on a
  persistent event loop.

### SQL — query and command execution

- `SQL-001`: parameterized single-row SELECT.
- `SQL-002`: empty result.
- `SQL-003`: ordered multirow result.
- `SQL-004`: large result set.
- `SQL-005`: `simple_query()` raw statement.
- `SQL-006`: INSERT row count and persisted state.
- `SQL-007`: UPDATE row count and persisted state.
- `SQL-008`: DELETE row count and persisted state.
- `SQL-009`: zero-row DML count.
- `SQL-010`: DDL create/alter/drop.
- `SQL-011`: CTE and recursive CTE.
- `SQL-012`: joins, grouping, window functions, and subqueries.
- `SQL-013`: `OUTPUT` clause.
- `SQL-014`: MERGE behavior and row count.
- `SQL-015`: view creation/query/drop.
- `SQL-016`: scalar and table-valued function execution.
- `SQL-017`: stored procedure with input parameters and result rows.
- `SQL-018`: stored procedure with return status.
- `SQL-019`: trigger side effects.
- `SQL-020`: identity, sequence, default, and computed columns.
- `SQL-021`: local temporary tables are isolated between pooled `Connection`
  leases while the physical SQL Server session is reused.
- `SQL-022`: local temporary table persists on `Transaction`.
- `SQL-023`: multiple result-set behavior is explicitly asserted.
- `SQL-024`: database context, `SET` options, isolation level,
  `CONTEXT_INFO`, and read-only `SESSION_CONTEXT` values are reset to the login
  baseline between pooled `Connection` leases for both Batch and RPC requests.
- `SQL-025`: comments, multiline SQL, and trailing semicolons.

### PARAM — Python-to-SQL parameters

- `PARAM-001`: `None` with inferable SQL type.
- `PARAM-002`: every `TypedNull` variant.
- `PARAM-003`: bool.
- `PARAM-004`: signed integer boundaries and overflow.
- `PARAM-005`: finite float, signed zero, infinity, and NaN behavior.
- `PARAM-006`: `Decimal` signs, precision, scale, and SQL maximum precision.
- `PARAM-007`: ASCII and Unicode strings.
- `PARAM-008`: emoji, supplementary-plane characters, combining characters,
  embedded NUL, tabs, and newlines.
- `PARAM-009`: empty and maximum-length strings.
- `PARAM-010`: bytes, bytearray, memoryview, empty binary, and large binary.
- `PARAM-011`: `date`.
- `PARAM-012`: naive and timezone-aware `datetime`.
- `PARAM-013`: `time`.
- `PARAM-014`: UUID.
- `PARAM-015`: `Parameter` and `Parameters` positional APIs.
- `PARAM-016`: `Parameters` named construction semantics.
- `PARAM-017`: list/tuple/set expansion for `IN`.
- `PARAM-018`: empty iterable expansion.
- `PARAM-019`: nested and unsupported objects.
- `PARAM-020`: placeholder count mismatch.
- `PARAM-021`: parameter ordering and repeated placeholders.
- `PARAM-022`: 2,100-parameter SQL Server boundary.
- `PARAM-023`: parameterized SQL-injection payload remains data.
- `PARAM-024`: conversion error class and message are stable and redacted.
- `PARAM-025`: every supported explicit scalar declaration and typed null
  reports the intended effective SQL Server type; `DATE` metadata, empty XML
  followed by another RPC parameter, and the `SMALLDATETIME` 29.998/29.999
  rounding boundary remain byte-aligned and exact.
- `PARAM-026`: explicit precision, scale and maximum length are preserved,
  with deterministic rounding and local overflow rejection.
- `PARAM-027`: legacy and `_UTF8` ANSI collations, Unicode UTF-16 code-unit
  lengths and binary byte lengths are enforced without truncation; a
  supplementary character occupies four bytes in `VARCHAR` under `_UTF8`,
  and pool reset restores the initial LOGIN7 database collation before
  deriving the first reset request's parameter metadata.
- `PARAM-028`: the closed SQL type parser rejects malformed, injected and
  unsupported declarations before network I/O.
- `PARAM-029`: typed iterable expansion preserves the declared type for every
  expanded child.
- `PARAM-030`: descriptor fields, non-input direction rejection, plain-list
  descriptors and `Parameters` compatibility are deterministic.
- `PARAM-031`: `Parameter.__repr__` is metadata-only, never invokes the
  wrapped value's `__repr__`, and redacts scalar and expanded values.
- `PARAM-032`: connection, transaction and batch paths use the same typed
  parameter conversion.
- `PARAM-033`: 1,000 concurrent typed operations preserve values and
  effective types without exceeding configured pool or SQL Server session
  bounds.

### TYPE — SQL-to-Python values

- `TYPE-001`: TINYINT, SMALLINT, INT, and BIGINT including NULL/boundaries.
- `TYPE-002`: BIT maps to bool, not int.
- `TYPE-003`: REAL and FLOAT including NULL and extreme finite values.
- `TYPE-004`: DECIMAL and NUMERIC preserve exact sign/precision/scale.
- `TYPE-005`: MONEY and SMALLMONEY preserve four decimal places.
- `TYPE-006`: CHAR, VARCHAR, VARCHAR(MAX), TEXT, and collation behavior.
- `TYPE-007`: NCHAR, NVARCHAR, NVARCHAR(MAX), and NTEXT Unicode behavior.
- `TYPE-008`: BINARY, VARBINARY, VARBINARY(MAX), IMAGE, and ROWVERSION.
- `TYPE-009`: DATE.
- `TYPE-010`: TIME at supported precisions.
- `TYPE-011`: SMALLDATETIME, DATETIME, and DATETIME2 precisions.
- `TYPE-012`: DATETIMEOFFSET retains the instant and timezone offset.
- `TYPE-013`: UNIQUEIDENTIFIER.
- `TYPE-014`: XML.
- `TYPE-015`: nullable columns and mixed NULL/non-NULL rows.
- `TYPE-016`: duplicate, empty, mixed-case, and non-ASCII column names.
- `TYPE-017`: unsupported SQL_VARIANT/spatial/hierarchyid/UDT behavior is
  explicit rather than silently converted to a wrong value.

### RESULT — `FastRow`, buffered `QueryStream`, and bounded `ResultStream`

- `RESULT-001`: row access by valid name and index.
- `RESULT-002`: negative and out-of-range row indices.
- `RESULT-003`: missing column behavior.
- `RESULT-004`: `columns()`, `values()`, `to_dict()`, and `len()`.
- `RESULT-005`: repr/str are safe and stable enough for diagnostics.
- `RESULT-006`: stream length, emptiness, row presence, and columns.
- `RESULT-007`: iteration order and exhaustion.
- `RESULT-008`: indexed access, negative index, and cache behavior.
- `RESULT-009`: slices and invalid slice steps.
- `RESULT-010`: `fetchone`, `fetchmany`, `fetchall`, and aliases.
- `RESULT-011`: mixed fetch methods update position consistently.
- `RESULT-012`: reset restores position.
- `RESULT-013`: empty result methods.
- `RESULT-014`: documented sync versus async iterator behavior matches runtime.
- `RESULT-015`: lazy-conversion and memory claims are measured, not inferred.
- `RESULT-016`: three result sets are streamed in exact wire order.
- `RESULT-017`: an empty middle result set retains exact declared metadata,
  including Unicode code-unit lengths, byte lengths, MAX, precision, and
  temporal scale.
- `RESULT-018`: outer `ResultStream` and inner `ResultSet` iteration are
  genuinely asynchronous; normal and early context exits preserve their
  distinct completion contracts.
- `RESULT-019`: a fixed-payload slow consumer proves the configured event
  bound and records baseline, peak, final, and ceiling RSS without claiming
  byte-chunked LOB streaming.
- `RESULT-020`: ordinary EOF returns a resettable size-one pool lease, while
  successfully drained security-context SQL returns its results and retires
  the physical session before the next smoke query.
- `RESULT-021`: `ResultSet.aclose()` skips only the active set, preserves an
  empty following set, and reaches the terminal summary.
- `RESULT-022`: complete `ResultStream.aclose()` waits for physical-session
  retirement, returns the pool's active count to zero, and permits a clean
  checkout on a different connection identity.
- `RESULT-023`: dropping either an open `ResultStream` or its active
  `ResultSet` cancels the complete response, eventually retires its physical
  session, and leaves the pool usable.
- `RESULT-024`: cancellation of a pending outer or inner `__anext__` does not
  lose the unconsumed event; queued nonfatal SQL errors reset and reuse the
  synchronized session, while post-wire conversion uncertainty retires it
  with stable terminal classification.
- `RESULT-025`: concurrent consumers and invalid buffer sizes fail locally,
  deterministically, and without exposing SQL text.
- `RESULT-026`: graceful disconnect waits for a live response to finish,
  while an expired shutdown budget force-cancels the producer, retires its
  session, and reports exact typed lifecycle counts.
- `RESULT-027`: pooled and direct transaction streams own the session for the
  complete response; normal EOF and per-set close preserve the transaction,
  while response retirement, close, or drop fails the transaction and rolls
  back its open SQL transaction.
- `RESULT-028`: DONE records, valid zero row counts, and informational
  messages remain distinct from result rows.
- `RESULT-029`: fresh SHA-bound evidence proves 1,000 exactly-once bounded
  streams at concurrency 64 and pool size 8, with latency, pool, RSS,
  process-CPU, SQL-session-CPU, event-loop, and post-load smoke invariants.
- `RESULT-031`: queued metadata and rows retain their lease until successful
  consumer acknowledgement; a full queue cannot block timeout release, and
  conversion failure after server EOF retires rather than reuses the
  connection.

### RPC — direct stored procedures, output values, and return status

- `RPC-001`: a direct named procedure with no parameters preserves its exact
  signed nonzero return status without constructing an `EXEC` SQL string.
- `RPC-002`: positional INPUT, OUTPUT, and INPUT_OUTPUT integers round-trip,
  and output mappings are fresh snapshots keyed by original descriptor index.
- `RPC-003`: Unicode, binary, decimal, UUID, date, time, DATETIME2, and
  DATETIMEOFFSET outputs preserve exact Python values and types.
- `RPC-004`: multiple and empty result sets remain in wire order before the
  terminal output mapping and return status become available.
- `RPC-005`: reordered NVARCHAR(MAX) and VARBINARY(MAX) output tokens match
  their original validated names and positional ordinals, never arrival order.
- `RPC-006`: return status zero remains an integer distinct from an absent
  status, and an explicit RETURN_VALUE descriptor receives the same value.
- `RPC-007`: nonfatal SQL errors, cancelled receives, and complete early close
  each apply their exact reset-or-retire connection disposition.
- `RPC-008`: positional/named modes, direction rules, parameter count, and
  procedure/parameter identifier grammars are deterministic, injection-safe,
  value-free, and validated before pool checkout.
- `RPC-009`: pooled connection, pooled transaction, and direct compatibility
  transaction paths preserve identical result/output/status behavior.
- `RPC-010`: concurrent direct RPC calls preserve independent outputs without
  exceeding the configured physical pool/session bound.
- `RPC-011`: a recycled physical session returns to READ COMMITTED before a
  named RPC; punctuation-bearing procedure input is rejected before checkout.

### BATCH — query batch, execute batch, and bulk insert

- `BATCH-001`: empty/single/multiple query batches.
- `BATCH-002`: parameterized mixed query batches.
- `BATCH-003`: result ordering and independent result objects.
- `BATCH-004`: query-batch midstream SQL error.
- `BATCH-005`: empty/single/multiple command batches.
- `BATCH-006`: row-count list order.
- `BATCH-007`: full atomic rollback on command-batch failure.
- `BATCH-008`: malformed batch item shapes.
- `BATCH-009`: basic bulk insert and persisted data.
- `BATCH-010`: empty data behavior.
- `BATCH-011`: mixed types and typed NULLs.
- `BATCH-012`: parameter-limit chunk boundary.
- `BATCH-013`: multiple chunks.
- `BATCH-014`: wide table.
- `BATCH-015`: quoted schema/table/column identifiers and reserved words.
- `BATCH-016`: malformed and malicious identifier input.
- `BATCH-017`: row-width mismatch.
- `BATCH-018`: constraint failure atomicity.
- `BATCH-019`: identity/default/computed/trigger interactions.
- `BATCH-020`: batch and bulk cancellation cleanup.

Enterprise compatibility-bulk buffering:

- `BULK-001`: late second-chunk conversion failure occurs after first-chunk
  server activity and rolls back the full compatibility bulk transaction.
- `BULK-002`: empty compatibility bulk returns zero without pool admission or
  operation-metric activity.

### TX — dedicated transactions

- `TX-001`: dedicated session ID remains constant.
- `TX-002`: explicit begin/commit persists data.
- `TX-003`: explicit begin/rollback discards data.
- `TX-004`: context manager auto-begin/commit.
- `TX-005`: context manager exception rollback and propagation.
- `TX-006`: manual commit/rollback within context.
- `TX-007`: repeated begin/commit/rollback state errors.
- `TX-008`: close and reuse behavior.
- `TX-009`: sequential reuse of the transaction object.
- `TX-010`: query/simple-query/execute forwarding.
- `TX-011`: query-batch and execute-batch forwarding.
- `TX-012`: local temporary table and session state.
- `TX-013`: DDL rollback.
- `TX-014`: savepoint behavior through raw SQL.
- `TX-015`: read-uncommitted/read-committed visibility.
- `TX-016`: blocking lock and release.
- `TX-017`: deterministic deadlock victim error.
- `TX-018`: cancellation leaves transaction state explicit and recoverable.
- `TX-019`: concurrent method calls serialize safely on the dedicated client.
- `TX-020`: concurrent `begin()` calls have exactly one atomic winner in both
  the public wrapper and the Rust transaction core.
- `TX-021`: concurrent `commit()`/`rollback()` settlement has exactly one
  atomic winner and the persisted result matches that winner.
- `TX-022`: `Connection.transaction()` reserves exactly one session from the
  owning connection pool and keeps the same SPID for the active transaction.
- `TX-023`: concurrent pooled transactions obey `pool.max_size`; a waiter is
  unblocked as soon as `COMMIT` or `ROLLBACK` releases a lease.
- `TX-024`: ordinary pooled operations and pooled transactions share the same
  capacity budget instead of opening independent physical connections.
- `TX-025`: a transaction lease is reset before cross-lease reuse, including
  local temporary objects, `SESSION_CONTEXT`, and isolation level.
- `TX-026`: cancellation makes an active transaction lease fail-closed; close
  retires the uncertain socket and a waiter recovers on a new physical
  `connection_id`, even if SQL Server reuses the numeric SPID.
- `TX-027`: `CommitOutcomeUnknown` is public and independent from
  `SqlConnectionError` and `SqlError`.
- `TX-028`: a pooled COMMIT applied by SQL Server with its response withheld
  raises `CommitOutcomeUnknown`, is non-retryable, retires the physical socket,
  and releases the pool waiter.
- `TX-029`: a direct compatibility transaction has the same typed unknown
  outcome and closes its physical socket.
- `TX-030`: automatic context-manager COMMIT propagates
  `CommitOutcomeUnknown` without calling rollback or retrying.
- `TX-031`: SQL Server error 3902 at severity 16 remains `SqlError`.
- `TX-032`: cancelling a pooled transaction operation retires its unsafe
  physical connection, terminates the request and releases a pool waiter
  without requiring explicit `close()`.
- `TX-033`: cancelling a direct transaction operation closes its SQL session
  and rolls back its uncommitted work without requiring explicit `close()`.
- `TX-034`: cancelling a COMMIT after SQL Server has made it durable retires
  the pooled lease and releases a waiter without claiming rollback or retrying
  the settlement operation.

### ASYNC — true asynchronous behavior

- `ASYNC-001`: event-loop ticker progresses during `WAITFOR`.
- `ASYNC-002`: pooled `WAITFOR` queries overlap in wall-clock time.
- `ASYNC-003`: concurrent lane is at least twice as fast as a measured
  sequential baseline for the same workload.
- `ASYNC-004`: a Python thread progresses while Rust waits on SQL I/O.
- `ASYNC-005`: concurrent successful and failing tasks remain isolated.
- `ASYNC-006`: same `Connection` object is safe across concurrent tasks.
- `ASYNC-007`: multiple `Connection` wrappers operate concurrently.
- `ASYNC-008`: `asyncio.wait_for` cancellation occurs within a bounded time.
- `ASYNC-009`: connection/pool remains usable after cancellation.
- `ASYNC-010`: cancellation storm does not leak pool capacity.
- `ASYNC-011`: task cancellation during pool acquisition returns capacity.
- `ASYNC-012`: transaction operations serialize instead of corrupting state.
- `ASYNC-013`: concurrent result conversion does not starve the event loop
  beyond the documented eager-conversion behavior.

The timing gate uses both an absolute and relative condition. Five one-second
`WAITFOR` operations with a pool of five must complete in less than 2.5 seconds
and at least twice as fast as the sequential baseline measured in the same run.
The event-loop ticker must record progress throughout the wait. Raw performance
numbers are recorded because AMD64 emulation can add host-specific variance.

### ERR — errors and security

- `ERR-001`: syntax error.
- `ERR-002`: missing object.
- `ERR-003`: duplicate key.
- `ERR-004`: foreign-key/check/not-null violation.
- `ERR-005`: truncation and conversion failure.
- `ERR-006`: arithmetic failure.
- `ERR-007`: deadlock victim.
- `ERR-008`: permission denial.
- `ERR-009`: SQL error exposes code/message/state.
- `ERR-010`: connection error exposes safe host/port information.
- `ERR-011`: TLS failure uses the TLS error class when distinguishable.
- `ERR-012`: protocol/conversion error classes are reachable and meaningful.
- `ERR-013`: error does not poison the pool.
- `ERR-014`: batch error preserves the original SQL error.
- `ERR-015`: credentials and tokens are absent from every error string.

### TLS — encryption with SQL authentication

- `TLS-001`: required encryption plus trusted development certificate.
- `TLS-002`: connection-string `Encrypt`/`TrustServerCertificate`.
- `TLS-003`: individual-parameter `SslConfig.development()`.
- `TLS-004`: disabled/login-only behavior matches server policy.
- `TLS-005`: untrusted certificate failure.
- `TLS-006`: invalid CA path/content/extension.
- `TLS-007`: mutually exclusive trust options.
- `TLS-008`: TLS settings do not change the SQL-auth principal.
- `TLS-009`: omitting `Encrypt` from a connection string defaults to required
  full-session encryption.
- `TLS-010`: `ssl_config` remains effective when authentication and endpoint
  settings are supplied through a connection string.
- `TLS-011`: TLS settings cannot be split between a connection string and
  `ssl_config`; mixed sources fail before network I/O.
- `TLS-012`: conflicting trust-all and custom-CA connection-string options
  raise `ValueError`, never a Rust panic exposed through PyO3.
- `TLS-013`: login-only and plaintext modes remain available only through an
  explicit encryption opt-out.

### RES — fault and recovery

- `RES-001`: dedicated container pause produces bounded query failure/timeout.
- `RES-002`: unpause permits a new connection.
- `RES-003`: container restart invalidates old sessions predictably.
- `RES-004`: existing pool rejects broken connections.
- `RES-005`: reconnect/recreated pool works after readiness returns.
- `RES-006`: in-flight transaction is not falsely reported committed.
- `RES-007`: no test touches a non-FastMssql Docker container.

### LOAD — bounded load and resources

- `LOAD-001`: repeated short queries under controlled concurrency.
- `LOAD-002`: large result memory remains within a recorded bound.
- `LOAD-003`: repeated result conversion does not grow memory monotonically.
- `LOAD-004`: bulk insert throughput and correctness at increasing sizes.
- `LOAD-005`: rapid lifecycle operations do not grow SQL sessions.
- `LOAD-006`: mixed query/execute/transaction workload.
- `LOAD-007`: post-load smoke query proves recovery.
- `LOAD-008`: concurrent write transactions use independent SQL sessions,
  overlap in time, and preserve exact commit/rollback state.
- `LOAD-009`: 1,000 readiness probes at task concurrency 100 remain bounded by
  `pool.max_size=20`, preserve a post-load query, and leave zero application
  sessions after disconnect.

The required `LOAD-008` gate runs 1,000 transactions at concurrency 50. An
explicit, opt-in transaction stress harness additionally runs 10,000 and
99,999 total transactions at bounded concurrency. The extended harness is not
part of the default runner because it is an endurance diagnostic whose timing
depends on host and container resources; it must still preserve exact database
state, leave no application sessions, keep the event loop responsive, and emit
machine-readable metrics.

### FRAME — framework integration

- `FRAME-001`: framework dependencies are development-only and their locked
  versions are recorded.
- `FRAME-002`: importing FastMssql does not import FastAPI, Flask, HTTPX, or
  asgiref.
- `FRAME-003`: every framework lane authenticates as `fastmssql_owner` using
  SQL authentication.
- `FRAME-004`: credentials are absent from HTTP responses, exception strings,
  and captured framework logs.
- `FRAME-005`: FastAPI lifespan connects one shared pool and disconnects it on
  shutdown.
- `FRAME-006`: FastAPI parameterized read/write routes return correct HTTP and
  persisted database results.
- `FRAME-007`: FastAPI request-scoped transaction commits on success.
- `FRAME-008`: FastAPI request-scoped transaction rolls back on failure.
- `FRAME-009`: FastAPI concurrent `WAITFOR` requests beat a measured sequential
  baseline with the configured pool size.
- `FRAME-010`: the Python event loop continues ticking during FastAPI SQL waits.
- `FRAME-011`: cancelling a FastAPI request does not leak pool capacity or
  poison the next query.
- `FRAME-012`: a FastMssql SQL error propagates through FastAPI with its class
  and code intact when application exceptions are enabled.
- `FRAME-013`: the normal FastAPI 500 response and logs do not disclose SQL
  credentials.
- `FRAME-014`: Flask executes a real parameterized FastMssql query from an
  `async def` WSGI view.
- `FRAME-015`: one shared FastMssql connection remains correct across
  sequential Flask requests with different per-request event loops.
- `FRAME-016`: concurrent FastMssql operations inside one Flask async view
  overlap and return independent results.
- `FRAME-017`: concurrent Flask WSGI worker requests return correct independent
  SQL results; worker-bound timing is recorded without an ASGI claim.
- `FRAME-018`: Flask WSGI SQL failures remain typed, redact credentials, and
  leave the shared pool usable.
- `FRAME-019`: explicit Flask WSGI test shutdown disconnects the shared pool
  and leaves no test-owned active SQL sessions.
- `FRAME-020`: Flask wrapped by `WsgiToAsgi` executes real FastMssql SQL-auth
  queries.
- `FRAME-021`: sequential adapted Flask requests reuse the persistent ASGI
  event loop.
- `FRAME-022`: concurrent adapted Flask requests all complete correctly and
  their measured timing is reported without native-ASGI equivalence.
- `FRAME-023`: cancelling an adapted Flask request has a bounded outcome and
  the FastMssql pool remains usable afterward.
- `FRAME-024`: adapted Flask startup and shutdown leave the FastMssql pool
  disconnected and no test-owned active SQL sessions.
- `FRAME-025`: FastAPI lifespan rejects an unreachable SQL Server before
  serving and cleans its failed-startup pool.
- `FRAME-026`: persistent-loop Flask-through-ASGI startup rejects an
  unreachable SQL Server before serving and remains safe to shut down.

Performance results are diagnostic unless a correctness/resource invariant is
violated. No marketing claim is declared proven from a single emulated host.

### TIME — operation timeout and deadline safety

- `TIME-001`: typed configuration, compatibility fallback, precedence,
  exports, and clone isolation.
- `TIME-002`: an unanswered TDS pre-login handshake expires in the physical-
  connect phase.
- `TIME-003`: saturated pool checkout expires in the acquire phase without
  starting application SQL.
- `TIME-004`: a timed-out query retires its physical session and the bounded
  pool recovers.
- `TIME-005`: a timed-out parameterized write is submitted once and reconciled
  by business key.
- `TIME-006`: batch and bulk work consume one absolute operation budget rather
  than one budget per item.
- `TIME-007`: idle transaction-lifetime expiry retires the lease and rolls back
  the uncommitted write.
- `TIME-008`: the earlier operation or transaction deadline wins for in-flight
  transaction work.
- `TIME-009`: COMMIT timeout preserves unknown-outcome precedence and rollback
  timeout remains visible.
- `TIME-010`: FastAPI and Flask-through-ASGI preserve typed errors and recover
  after 1,000 bounded operations.

### LIFE — connection lifecycle and graceful shutdown

- `LIFE-001`: public config/state/error exports, defaults, signatures, stubs,
  copy isolation and portable validation.
- `LIFE-002`: initial Open, pool-independent state and graceful transitions.
- `LIFE-003`: disconnect waits for admitted SQL and leaves zero sessions.
- `LIFE-004`: Closing rejects new SQL and Closed permits a new generation.
- `LIFE-005`: concurrent/cancelled shutdown waiters share one supervisor.
- `LIFE-006`: admitted pool waiters remain inside the drain barrier.
- `LIFE-007`: an active pooled transaction may commit while Closing.
- `LIFE-008`: rollback/close release the transaction permit exactly once.
- `LIFE-009`: grace expiry force-retires a query and allows recovery.
- `LIFE-010`: forced writes/batches are uncertain and never retried.
- `LIFE-011`: force rolls back and revokes an idle pooled transaction.
- `LIFE-012`: forced unconfirmed COMMIT remains CommitOutcomeUnknown.
- `LIFE-013`: direct execute_batch and nested contexts use one lifecycle.
- `LIFE-014`: 100 generations and 2,000 operations leave no stale work.
- `LIFE-015`: ASGI lifecycle is persistent; Flask/WSGI remains per-loop.
- `LIFE-016`: cancelling an in-flight transaction close retires its lease,
  releases the lifecycle barrier exactly once and permits graceful shutdown.

## 7. Error handling and defect workflow

When a strict test fails:

1. preserve the exact command, SQL error attributes, timing, and environment;
2. reduce the failure to a minimal SQL-auth reproduction;
3. verify the reproduction fails for the intended reason;
4. classify it before changing library code;
5. create `fix/sql-auth-<slug>` from the relevant baseline;
6. use a red-green regression cycle;
7. rerun the focused test, strict category, full strict suite, applicable
   upstream suite, Rust checks, and build;
8. record the result and residual risk;
9. request approval before pushing the fork branch or opening a PR.

Environment limitations and unsupported SQL types remain visible in the report;
they are not converted into passing tests.

## 8. Reports and evidence

Create:

- `docs/SQL_AUTH_TEST_MATRIX.md`: every matrix ID, test path, status, and
  evidence;
- `docs/SQL_AUTH_TEST_REPORT.md`: environment, commands, totals, timing,
  failures, defects, fixes, exclusions, and risks;
- machine-readable JUnit XML for each pytest lane;
- captured Rust/Python/tool versions and SQL Server edition/build;
- Docker container health and non-secret configuration evidence.

The report distinguishes:

- strict suite results;
- applicable upstream regression results;
- upstream tests excluded because they exercise Azure authentication;
- environmental exclusions;
- discovered library defects;
- fixes verified locally but not published;
- fork/PR publication state.

## 9. Acceptance criteria

The work is complete only when:

- the dedicated container is running and reports Developer Edition;
- SQL username/password authentication is proven for normal and restricted
  logins;
- the strict matrix has a result for every listed identifier;
- the strict suite has zero unexpected skips and no swallowed failures;
- applicable upstream SQL-auth tests have been executed and honestly reported;
- async overlap, event-loop progress, cancellation, and post-cancellation reuse
  have direct timing/state evidence;
- transaction, pool, batch, bulk, type, error, TLS, and recovery behavior have
  database-state assertions;
- all discovered defects have minimal reproductions;
- any implemented fix has fresh red-green and full regression evidence;
- secrets are absent from git and reports;
- the final audit identifies every exclusion and residual risk;
- no fork, push, or pull request was made without explicit approval.

## 10. Versioning decision

Design and test-only changes do not change the published FastMssql version
`0.7.7`. The upstream repository has no `VERSION.md`; this branch will not
invent an unrelated release-history convention for documentation-only changes.
If a library fix is implemented, its dedicated fix plan must update the version
sources and release notes required by the repository/local instructions.
