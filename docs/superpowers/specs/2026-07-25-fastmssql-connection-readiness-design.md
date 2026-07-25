# FastMssql Connection Readiness Design

**Status:** Approved by Marcel Galea on 2026-07-25

**Source baseline:** `test/sql-auth-validation` at `13c0925`

**Target repository:** `https://github.com/galeamarcel/FastMssql.git`

## Problem

`Connection.connect()` currently proves only that a bb8 pool object was
allocated. When `PoolConfig(min_idle=0)` is used, pool construction performs
no TCP, TLS, login or SQL Server database access. Both explicit `connect()` and
`Connection.__aenter__()` can therefore report success before a physical
connection exists.

The behavior was reproduced against a closed local endpoint:

```text
pool.max_size                         1
pool.min_idle                         0
endpoint                              127.0.0.1:1
await connection.connect()            True
await connection.is_connected()       True
pool_stats()["connections"]           0
first query                           SqlConnectionError
```

The same boundary was reproduced against the real SQL-auth Docker server:

```text
explicit connect, entry connections   0
async context, entry connections      0
first query result                    1
connections after first query         1
```

This is unsafe for application startup and readiness decisions. A FastAPI
lifespan or an ASGI-adapted Flask startup can complete even though SQL Server
is unreachable, moving the failure into the first user request.

`is_connected()` compounds the ambiguity because its current documentation
describes the pool as "active and ready", while the implementation checks only
whether `Arc<RwLock<Option<ConnectionPool>>>` contains a pool handle.

## Goals

1. Make explicit `connect()` validate real SQL Server access by default.
2. Preserve intentional lazy pool initialization through an explicit opt-out.
3. Add a public `ping()` operation for live readiness checks.
4. Make async context entry use the same strict default as `connect()`.
5. Keep `is_connected()` as a cheap pool-lifecycle check and document it
   precisely.
6. Bound the complete readiness probe, including checkout and SQL response
   consumption.
7. Retire a connection if cancellation or timeout abandons a partial readiness
   response.
8. Preserve query-without-connect lazy initialization.
9. Verify the contract through real SQL-auth, closed-endpoint, fault,
   framework and bounded-load tests.

## Non-goals

This candidate does not implement:

- general connect/acquire/query/transaction/rollback timeout configuration;
- a new timeout exception taxonomy;
- graceful shutdown or `Open -> Closing -> Closed` lifecycle states;
- serialization of concurrent `connect()` and `disconnect()` calls;
- TDS `ATTENTION` or same-socket cancellation recovery;
- expanded bb8 statistics or OpenTelemetry;
- automatic background health checks;
- transparent retry of queries, writes or transactions;
- a Tiberius fork or upstream publication.

Those remain separate audit candidates so connection readiness can be reviewed
and reverted independently.

## Approved public contract

### `Connection.connect`

The Python-visible signature becomes:

```python
async def connect(self, validate: bool = True) -> bool: ...
```

`validate=True` is the default and means:

1. initialize or reuse the shared bb8 pool;
2. acquire one lease through the normal pool path;
3. execute a fixed `SELECT 1`;
4. consume the complete response;
5. return `True` only after all four steps succeed.

Every explicit call with `validate=True` performs the probe. This means a
second call is idempotent with respect to pool identity, but it is an actual
liveness check and can fail if the pool is saturated or SQL Server is no
longer reachable.

`validate=False` means:

1. initialize or reuse the pool object;
2. perform no forced checkout or SQL round-trip;
3. return `True` after pool initialization.

This is the explicit compatibility and cold-start optimization path. With
`min_idle=0`, `validate=False` may leave
`pool_stats()["connections"] == 0`. It must never be documented as readiness.

Calling `query`, `simple_query`, `execute`, batch methods or
`Connection.transaction()` without first calling `connect()` remains
supported. The first operation continues to initialize the pool lazily.

### `Connection.ping`

The new Python-visible signature is:

```python
async def ping(self) -> bool: ...
```

`ping()`:

1. initializes the pool if it does not exist;
2. uses the shared pool rather than a dedicated bypass connection;
3. executes the same fixed `SELECT 1`;
4. consumes the full result;
5. returns exactly `True` on success;
6. propagates the existing typed FastMssql error on failure.

`ping()` does not return `False` for SQL, TLS, authentication, protocol,
checkout or timeout failures. Returning `False` would discard the error class
and connection context needed by startup logs and health diagnostics.

Using the pool is intentional. A readiness probe must demonstrate that the
application can obtain and use a lease under its configured resource budget,
not merely that a separate socket can reach SQL Server.

### Async context manager

`Connection.__aenter__()` uses the strict readiness path:

```python
async with connection:
    ...
```

The body is entered only after a real `SELECT 1` round-trip succeeds. There is
no lazy opt-out on context entry. Callers that intentionally want lazy pool
allocation can use `await connection.connect(validate=False)` without a
context manager.

`__aexit__()` retains its current immediate pool-drop behavior in this
candidate. Graceful shutdown and active-lease deadlines are a later lifecycle
design.

### `Connection.is_connected`

The runtime behavior remains unchanged:

```python
async def is_connected(self) -> bool: ...
```

It answers only:

> Is a pool handle currently installed in this `Connection` object?

It does not perform network I/O, acquire a lease, validate an idle socket or
prove current SQL Server readiness. Runtime docstrings, both stub surfaces and
README examples must stop describing it as a readiness check.

Applications use:

```python
if await database.is_connected():
    ...  # pool lifecycle information only

await database.ping()  # actual readiness; raises on failure
```

## Internal architecture

### One readiness primitive

`src/connection.rs` gains one private async primitive used by `connect()`,
`ping()` and `__aenter__()`. Conceptually:

```rust
async fn validate_pool_readiness(pool: &ConnectionPool) -> PyResult<()> {
    // acquire, SELECT 1, fully consume, classify result, finish guard
}
```

There must not be three independent implementations. A common primitive keeps
deadline, connection disposition, panic containment and error mapping
identical across every public entry point.

### Shared-pool checkout

The primitive acquires through the existing `ConnectionPool`. Pool checkout
errors use the common `map_pool_checkout_error()` translation so
`Connection.query()`, pooled transactions and readiness do not expose
different messages for the same bb8 `RunError`.

A `PooledOperationGuard` is created before the first TDS await. The guard is
completed only after the fixed query response is consumed fully.

### Fixed probe statement

The statement is a driver constant:

```sql
SELECT 1
```

It accepts no caller SQL or parameters, changes no session state and contains
no sensitive data. It is executed with `simple_query` and drained through
`into_results()` so success cannot be reported after receiving only response
metadata.

The connection becomes `NeedsReset` after a successful probe, matching every
other completed pooled operation. The existing TDS `RESETCONNECTION` path
cleans the session before its next cross-lease use.

### One bounded readiness budget

bb8 0.9.1 applies `connection_timeout` to `Pool::get()`, including queue wait,
connection creation and checkout validation. It does not bound TDS work after
the lease has been returned.

The readiness primitive wraps the complete checkout-plus-query future with:

```rust
tokio::time::timeout(pool.config().connection_timeout, readiness_future)
```

The public budget is therefore the existing effective
`PoolConfig.connection_timeout_secs`, including bb8's 30-second default when
the Python option is `None`.

This is a single total deadline, not a checkout deadline followed by a second
full query deadline. A saturated pool and a server that accepts a connection
but stalls the probe are both bounded by the same duration.

Tokio cancels a wrapped future when the timeout elapses by dropping it:
[Tokio `timeout`](https://docs.rs/tokio/latest/tokio/time/fn.timeout.html).
If the drop happens after a lease was acquired, `PooledOperationGuard::drop`
marks the physical connection unusable. bb8 then closes it rather than
returning a partial TDS response to another borrower.

### Error behavior

The primitive preserves existing error classes:

- pool saturation -> `SqlConnectionError`;
- TCP or non-TLS I/O -> `SqlConnectionError`;
- TLS/certificate -> `TlsError`;
- SQL Server login/database error -> `SqlError`;
- protocol desynchronization -> `ProtocolError`;
- driver panic -> contained Python error through `catch_driver_panic`;
- outer readiness deadline -> `SqlConnectionError` whose message identifies
  the readiness timeout and configured duration.

No branch catches and converts these failures to `False`. No error is accepted
as an alternative successful outcome.

The timeout-specific exception and stable timeout attributes belong to the
later general timeout candidate. This design does not introduce a partial
taxonomy that would have to be replaced immediately.

### Cancellation

Python cancellation preserves `asyncio.CancelledError`.

If cancellation happens:

- before checkout, no connection was borrowed;
- after checkout but before complete response consumption, the incomplete
  `PooledOperationGuard` marks the lease broken;
- after full consumption and guard completion, the connection can return
  through the normal reset path.

There is no retry of the probe inside FastMssql. A later caller may invoke
`ping()` again and receive a newly created connection after the broken one is
retired.

### Concurrent calls

Concurrent `connect(validate=True)`, `ping()` and lazy data operations share
the same pool and obey `pool.max_size`. They may run independent readiness
round-trips on separate leases.

If the pool is saturated for the complete configured deadline,
`connect(validate=True)` and `ping()` fail with the same pool-timeout
classification as a normal operation. This is desirable readiness behavior:
an application unable to acquire any lease within its own budget is not ready
for another request.

A concurrent `disconnect()` can currently remove the owner-visible pool while
an operation still holds a cloned bb8 handle. The in-flight operation can
finish on that clone. Eliminating this race requires the later lifecycle state
machine and is not hidden inside this candidate.

## Framework contract

### FastAPI / ASGI

The recommended lifespan becomes:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.connect()  # strict by default
    app.state.database = database
    try:
        yield
    finally:
        await database.disconnect()
```

An unreachable SQL Server prevents lifespan startup. A readiness endpoint
invokes `await database.ping()` and maps the typed failure according to
application policy; it must not use `is_connected()` as a database health
signal.

### Flask

Flask under WSGI retains its documented event-loop limitations. Explicit
application/test startup invokes strict `connect()` on the persistent owner
loop. Flask behind an ASGI adapter follows the same persistent-lifespan
contract as FastAPI.

The driver does not create a second pool per request and does not hide startup
errors inside an HTTP 200 readiness response.

## Deterministic RED contracts

The test branch is `test/connection-readiness`, created from the committed
design baseline.

### CONN-020 — strict connect rejects a lazy false positive

1. Construct a pool with `max_size=1`, `min_idle=0`,
   `connection_timeout_secs=1`, `retry_connection=False`.
2. Point it at a closed local TCP port.
3. Call `connect()` without arguments.
4. Require a typed `SqlConnectionError`.
5. Assert no successful readiness result is accepted.

On the baseline, this returns `True` and is RED.

### CONN-021 — explicit lazy opt-out remains available

1. Use the same closed endpoint and pool settings.
2. Call `connect(validate=False)`.
3. Require `True`, `is_connected() == True` and zero physical connections.
4. Call `ping()`.
5. Require a typed connection failure.
6. Disconnect and require the pool lifecycle to return to false.

On the baseline, the `validate` argument and `ping()` do not exist.

### CONN-022 — strict connect creates a real SQL-auth session

1. Start with a real SQL-auth connection using `min_idle=0`.
2. Use a unique `application_name`.
3. Prove the observer sees no matching SQL session.
4. Call default `connect()`.
5. Require `True` and at least one physical/idle pool connection.
6. Prove an observer sees an authenticated session with the expected login,
   database and application name before any application query is issued.
7. Disconnect and prove the session disappears within a bounded deadline.

On the baseline, step 5 observes zero connections and step 6 sees no session.

### CONN-023 — async context validates before body entry

1. Construct `Connection` with `min_idle=0` and a closed endpoint.
2. Set a local `body_entered=False` marker.
3. Enter `async with connection` and set the marker only inside the body.
4. Require a typed connection error.
5. Require `body_entered is False`.

On the baseline, the body is entered and is RED.

### CONN-024 — ping retires a dead physical connection

1. Create a real one-connection SQL-auth pool with
   `test_on_check_out=False`.
2. Strict-connect and record both `session_id` and
   `sys.dm_exec_connections.connection_id`.
3. Kill the session through the observer and wait for server-side removal.
4. Call `ping()` and require a typed failure from the dead socket.
5. Prove the broken connection is no longer reusable.
6. Call `ping()` again and require `True`.
7. Record the replacement identity and require a different `connection_id`.
8. Disconnect and require zero matching sessions.

The first failed `ping()` is not retried transparently. Recovery is explicit
and occurs on the second call.

### FRAME-025 — FastAPI lifespan fails before serving

1. Build the real FastAPI lifespan around a `min_idle=0` connection to a
   closed endpoint.
2. Start the ASGI lifespan through the real test client.
3. Require startup to raise the typed FastMssql connection error.
4. Prove no route handler ran and no pool/session remains.

### FRAME-026 — adapted Flask persistent startup fails before serving

1. Build the existing Flask-through-ASGI lane with a persistent owner loop.
2. Configure the shared `min_idle=0` connection to a closed endpoint.
3. Invoke its explicit startup path.
4. Require the typed FastMssql connection error before any request is served.
5. Prove no handler ran and shutdown remains idempotent.

### LOAD-009 — readiness storm remains pool-bounded

1. Create one shared SQL-auth `Connection` with `pool.max_size=20` and
   `min_idle=0`.
2. Run 1,000 `ping()` calls with task concurrency 100.
3. Require exactly 1,000 `True` results and zero exceptions.
4. Sample client pool state and observer SQL sessions during the run.
5. Require no more than 20 physical/application sessions.
6. Run a post-load application query.
7. Disconnect and require zero matching sessions.

This load gate tests the driver and pool behavior, not SQL Server capacity.

## Matrix accounting

The strict specification grows from 277 to 285 exact case IDs:

```text
CONN-020
CONN-021
CONN-022
CONN-023
CONN-024
FRAME-025
FRAME-026
LOAD-009
```

`tests/sql_auth_strict/test_matrix_contract.py`, report generation and every
exact-total assertion must change together. There are no skipped, xfailed or
alternative successful outcomes.

## Branch and commit discipline

All work remains on the fork:

```text
docs/connection-readiness-design
  -> specification and implementation plan only

test/connection-readiness
  -> RED tests and matrix update only

fix/connection-readiness
  -> minimal production implementation

docs/connection-readiness-status
  -> live audit and upstream candidate evidence
```

The RED branch is committed and pushed before production code is written. The
fix branch starts from the committed RED branch. Documentation status starts
only from the exact verified cumulative merge.

No branch is pushed to `Rivendael/FastMssql`. The `upstream` push URL remains
`DISABLED`, and no upstream PR is created without a separate explicit
approval.

## Verification gates

The fix is not complete until all of the following are fresh and green:

```text
CONN-020–CONN-024                    PASS
FRAME-025–FRAME-026                  PASS
LOAD-009                             PASS
strict specification IDs            285/285
complete strict SQL-auth suite       zero failures
applicable upstream suite            zero failures
Rust unit tests                      zero failures
cargo fmt                            PASS
Clippy -D warnings                   PASS
Ruff / compileall                    PASS
RustSec                              zero findings
readiness storm                      bounded at pool.max_size
post-load query                      PASS
remaining application sessions      0
origin                               galeamarcel/FastMssql
upstream push                        DISABLED
upstream PR                          not created
```

The real Docker SQL Server uses SQL authentication. Every failure path has a
bounded deadline and unconditional cleanup.

## Follow-up sequence

After this isolated candidate is verified and documented, the next P1 designs
remain:

1. separate public connect/acquire/query/transaction/rollback timeouts and
   stable timeout errors;
2. `Open -> Closing -> Closed` lifecycle with graceful shutdown deadline and
   active-lease accounting;
3. full bb8 statistics, timeout/retirement counters and privacy-safe tracing;
4. optional TDS `ATTENTION` only for same-session reuse after safe
   `DONE_ATTN` drainage.
