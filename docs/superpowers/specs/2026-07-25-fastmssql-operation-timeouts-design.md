# FastMssql Operation Timeouts and Deadline Safety Design

**Status:** Approved by Marcel Galea on 2026-07-25 through the standing
approval for future enterprise designs, specifications, plans and inline
implementations

**Source baseline:** `test/sql-auth-validation` at
`2f6d0c53ebe4f3d5bc617606730f6aed0f143619`

**Target repository:** `https://github.com/galeamarcel/FastMssql.git`

**Publication boundary:** every branch, commit and hosted validation produced
by this candidate belongs only to Marcel Galea's fork. The original
`Rivendael/FastMssql` repository remains fetch-only with push URL `DISABLED`.
No upstream branch, push, pull request, package publication or dependency fork
is authorized by this design.

## Decision summary

FastMssql will add one typed `TimeoutConfig` and one internal absolute-deadline
mechanism for five independent phases:

| Phase | Public setting | Default | Scope |
| --- | --- | ---: | --- |
| Physical connection | `connect_timeout_secs` | `30.0` | credential acquisition, TCP, TLS/login and one routed reconnect |
| Pool acquisition | `acquire_timeout_secs` | `30.0` | queue wait, connection availability and checkout validation/reset |
| SQL operation | `operation_timeout_secs` | `None` | one complete query, command, ping, batch or bulk call |
| Transaction lifetime | `transaction_timeout_secs` | `None` | absolute lifetime beginning after confirmed `BEGIN` |
| Cleanup rollback | `rollback_timeout_secs` | `30.0` | explicit rollback and rollback performed by `close()` |

`None` means no FastMssql deadline only for the four optional fields:
`connect_timeout_secs`, `operation_timeout_secs`,
`transaction_timeout_secs` and `rollback_timeout_secs`.
`acquire_timeout_secs` is always a finite positive number because bb8 0.9.1
requires a nonzero connection-acquisition timeout.

The feature is fail-closed. If a deadline expires after a TDS request may have
started, the physical connection is retired and is never returned to the
pool. FastMssql does not retry SQL operations. A timeout while awaiting COMMIT
continues to raise `CommitOutcomeUnknown`, with the typed timeout preserved as
its cause.

The broader audit item is deliberately decomposed:

1. this candidate implements timeout configuration, error taxonomy and
   deadline safety;
2. `Open -> Closing -> Closed` graceful lifecycle remains a separate
   candidate;
3. pool metrics, reason counters and OpenTelemetry remain a separate
   observability candidate;
4. TDS `ATTENTION` plus `DONE_ATTN` draining remains a later same-socket
   optimization; transport retirement is the safe timeout fallback here.

## Problem

FastMssql currently has only one setting named
`PoolConfig.connection_timeout_secs`. It is passed to
`bb8::Builder::connection_timeout`, whose documented contract is the maximum
time returned by `Pool::get()` may wait. bb8 0.9.1 wraps the complete checkout
future, including queueing and optional connection validation. It is not a
distinct SQL query timeout.

The current source also reuses that duration around the `SELECT 1` readiness
operation. As a result, one value currently has two observable meanings:

```text
pool saturation / checkout wait       connection_timeout_secs
readiness query response              connection_timeout_secs
```

There is no public FastMssql deadline for:

- Azure credential acquisition;
- TCP connect;
- TLS/login or routed reconnect;
- ordinary `query`, `simple_query` or `execute`;
- query/execute batch;
- bulk insert;
- transaction lifetime;
- `BEGIN`, `COMMIT` or `ROLLBACK`;
- the best-effort rollback performed by `Transaction.close()`.

### Unbounded physical connection path

`AzureConnectionManager::connect()` awaits credential acquisition,
`TcpStream::connect()` and `tiberius::Client::connect()` without a Tokio
deadline. A peer can accept TCP and never complete TDS pre-login/login, leaving
that future pending indefinitely.

The default pool has `min_idle=3`. `bb8::Builder::build()` waits for its
configured minimum connections. Its `connection_timeout` bounds retry policy
and `Pool::get()`, but the `ManageConnection::connect()` future itself has no
independent timeout. Pool initialization can therefore wait on a physical
connection attempt that never yields a result.

### Unbounded SQL operation path

Connection operations acquire a `PooledOperationGuard`, issue TDS and consume
the first result set. Cancellation is now fail-closed: dropping the Rust
future retires the socket. There is, however, no native deadline and no typed
timeout error. Every application must wrap calls in `asyncio.timeout()` or
`asyncio.wait_for()` and independently choose a taxonomy.

External cancellation is a useful outer safety net but cannot:

- distinguish pool acquisition from query execution;
- bound internal pool warm-up or direct transaction connect;
- expose stable timeout metadata;
- preserve the timeout as the cause of `CommitOutcomeUnknown`;
- apply a separate cleanup budget after a transaction lifetime expires;
- keep one absolute budget across a multi-step batch or bulk call.

### Transaction deadline gap

The Rust transaction state machine tracks in-flight operations and retires a
cancelled connection. It does not store an absolute transaction deadline.
An active transaction can remain open indefinitely while holding one pool
lease. `close()` performs a rollback without a deadline, so application
shutdown can wait forever on an unresponsive peer.

### Ambiguous timeout error

A bb8 acquisition timeout currently becomes `SqlConnectionError` with a
rendered string. Callers must inspect text such as `"pool timeout"` and cannot
reliably determine:

- which phase expired;
- which configured budget applied;
- whether any SQL may have executed;
- whether a physical connection was discarded;
- whether retry is safe at the driver boundary.

## Goals

1. Expose one typed timeout policy shared by `Connection` and `Transaction`.
2. Give connect, acquire, operation, transaction and rollback independent
   budgets with precise start/end boundaries.
3. Preserve the legacy pool-acquisition duration when a caller does not pass
   `TimeoutConfig`.
4. Make every configured value visible through runtime introspection, PyO3
   signatures, the Python stub and README.
5. Use absolute deadlines so a multi-step operation cannot reset its budget at
   each internal await.
6. Retire every connection whose protocol state may be incomplete.
7. Preserve `CommitOutcomeUnknown` for any unconfirmed COMMIT and attach the
   typed timeout as its cause.
8. Add a stable, structured timeout exception without requiring message
   parsing.
9. Preserve external asyncio cancellation and its existing RAII cleanup.
10. Verify real SQL-auth behavior, server-side request/session termination,
    pool recovery and bounded concurrency.
11. Preserve all existing SQL, framework, load, upstream, Rust, packaging and
    security gates.

## Non-goals

This candidate does not:

- implement graceful shutdown or an `Open | Closing | Closed` lifecycle;
- wait for active leases during `disconnect()`;
- expose waiters, histograms, reason counters or OpenTelemetry;
- log SQL text, parameter values, credentials or connection strings;
- implement TDS `ATTENTION` or reuse a timed-out physical session;
- retry a query, command, batch, bulk operation, transaction or settlement;
- classify arbitrary SQL as idempotent;
- add per-call timeout keyword overrides;
- parse timeout values from ADO.NET connection strings;
- add a background task solely to expire idle transactions;
- change pool size, queue strategy, retry-connection policy or session reset;
- change streaming/result-set behavior;
- change direct `Transaction(...)` ownership semantics;
- aggregate Python context-manager cleanup exceptions; swallowed rollback
  errors in the wrapper receive a separate defect candidate;
- bump the package version or publish a wheel;
- modify bb8, Tokio, Tiberius or PyO3;
- create or publish anything against the original FastMssql repository.

These exclusions keep the timeout contract independently reviewable and avoid
mixing it with lifecycle and telemetry policy.

## Options considered

### Option A — Typed `TimeoutConfig` with centralized Rust deadlines

Add one configuration object, derive one effective policy per connection and
route every timeout through shared Rust helpers and a structured exception.

**Selected.** It gives one auditable policy, avoids duplicated validation,
supports direct and pooled transactions, preserves fail-closed disposal and
can later feed observability without changing the public semantics.

### Option B — Add `timeout_secs` to every method

Add optional keywords to `connect`, `ping`, every query/command method,
batch/bulk and every transaction method.

**Rejected for this candidate.** Per-call overrides are useful but create a
large signature surface and two-level precedence rules before the base
deadline semantics are proven. They can be added later without changing
`TimeoutConfig`.

### Option C — Require callers to use `asyncio.timeout()`

Document external cancellation as the only timeout mechanism.

**Rejected.** It cannot bound internal physical connection setup, distinguish
phases, attach structured metadata or guarantee a separate rollback cleanup
budget. It also duplicates policy across FastAPI/ASGI and Flask integrations.

## Approved public API

### `TimeoutConfig`

The compiled module and package root export:

```python
class TimeoutConfig:
    def __init__(
        self,
        connect_timeout_secs: float | None = 30.0,
        acquire_timeout_secs: float = 30.0,
        operation_timeout_secs: float | None = None,
        transaction_timeout_secs: float | None = None,
        rollback_timeout_secs: float | None = 30.0,
    ) -> None: ...
```

All fields are readable and writable with the same validation:

- every numeric value must be finite and strictly greater than zero;
- every numeric value must be at least one nanosecond and no greater than
  `3_153_600_000` seconds (100 × 365 days), inclusive, so validation is
  deterministic across Linux, macOS and Windows;
- booleans are rejected rather than interpreted as `0.0` or `1.0`;
- `NaN`, positive/negative infinity, zero and negative values raise
  `ValueError` before a pool or socket is created;
- a value above the portable 100-year ceiling raises `ValueError` even when a
  particular operating system could represent a larger monotonic instant;
- `acquire_timeout_secs=None` is rejected;
- optional `None` values mean that FastMssql does not install a deadline for
  that phase.

Subsecond positive floats are supported. Runtime getters and `repr` return
seconds without silently rounding to integer seconds.

The exact default representation is:

```python
TimeoutConfig(
    connect_timeout_secs=30.0,
    acquire_timeout_secs=30.0,
    operation_timeout_secs=None,
    transaction_timeout_secs=None,
    rollback_timeout_secs=30.0,
)
```

`inspect.signature(TimeoutConfig)` and `__text_signature__` expose those
concrete defaults and contain no `...`/`Ellipsis`.

### Connection construction

Both the Rust class and Python stub add the final keyword:

```python
Connection(..., timeout_config: TimeoutConfig | None = None)
```

The argument is keyword-compatible with existing construction and does not
change the positional meaning of any existing parameter.

When `timeout_config` is supplied, it is the authoritative source for all five
phases. Its `acquire_timeout_secs` is passed to the bb8 builder.

When it is omitted, FastMssql derives an effective compatibility policy:

```text
connect_timeout_secs       PoolConfig.connection_timeout_secs or 30.0
acquire_timeout_secs       PoolConfig.connection_timeout_secs or 30.0
operation_timeout_secs     None
transaction_timeout_secs   None
rollback_timeout_secs      30.0
```

This preserves every existing custom acquisition timeout. An explicit
`PoolConfig.connection_timeout_secs=None` already leaves bb8's 30-second
default in effect; the derived policy reports `30.0`, not an unbounded wait.

If both objects are provided, `TimeoutConfig.acquire_timeout_secs` wins. The
effective cloned pool configuration used internally is aligned to that value
so bb8, runtime behavior and introspection cannot disagree.

The connection exposes a read-only clone:

```python
connection.timeout_config -> TimeoutConfig
```

Mutating the original object after construction does not race with in-flight
operations and does not change the connection's policy.

### Transaction construction and inheritance

The direct constructor adds:

```python
Transaction(..., timeout_config: TimeoutConfig | None = None)
```

Omission uses `TimeoutConfig()` defaults.

`Connection.transaction()` clones the connection's effective timeout policy.
The transaction cannot observe later mutation of an external config object.
It exposes the same read-only `timeout_config` property.

## Timeout error contract

The compiled module and package root export:

```python
class OperationTimeoutError(SqlConnectionError):
    message: str
    operation: str
    phase: str
    timeout_seconds: float
    retryable: bool
    connection_discarded: bool
    outcome_unknown: bool
```

It subclasses `SqlConnectionError` so existing acquisition-timeout handlers
remain compatible. New code can catch `OperationTimeoutError` directly. The
message is human-readable, but every decision-relevant field is structured
and tested.

Stable `phase` values are:

```text
connect
acquire
operation
transaction
rollback
```

`operation` is the public call or settlement name, including:

```text
connect, ping, query, simple_query, execute, query_batch, execute_batch,
bulk_insert, begin, commit, rollback, close, transaction
```

The error policy is conservative:

| Situation | `retryable` | `connection_discarded` | `outcome_unknown` |
| --- | ---: | ---: | ---: |
| Physical connect expires before a usable session exists | `True` | `False` | `False` |
| Pool acquisition expires before a lease is returned | `True` | `False` | `False` |
| Read-only `ping` expires after request start | `False` | `True` | `False` |
| General SQL operation expires after request start | `False` | `True` | `True` |
| Transaction lifetime expires while idle | `False` | `True` | `False` |
| `BEGIN` expires | `False` | `True` | `False` |
| `ROLLBACK`/`close()` rollback expires | `False` | `True` | `False` |

`retryable=True` is guidance that no application SQL was started; FastMssql
still performs no new automatic retry. Existing
`PoolConfig.retry_connection` remains the only connection-creation retry
policy.

### COMMIT exception precedence

If the COMMIT request may have reached SQL Server but its complete response is
not consumed before either the operation or transaction deadline:

1. the socket/lease is retired;
2. the top-level exception is `CommitOutcomeUnknown`;
3. `error.operation == "commit"`;
4. `error.retryable is False`;
5. `error.connection_discarded is True`;
6. `error.__cause__` is `OperationTimeoutError`;
7. the cause has `phase == "operation"` or `"transaction"` and
   `outcome_unknown is True`.

FastMssql does not issue rollback, a second COMMIT or a retry. Applications
must reconcile through an idempotency/business key.

## Deadline boundaries

All active deadlines use `tokio::time::Instant` and
`tokio::time::timeout_at`. An absolute deadline is calculated once per public
operation. Internal awaits and chunks consume the same budget.

Tokio cancels an elapsed inner future by dropping it. Existing RAII guards
therefore remain the authoritative disposal mechanism: cancellation or timeout
cannot execute a success completion callback.

### Physical connect

The physical-connect deadline begins before Azure credential acquisition and
ends only after Tiberius returns an authenticated usable client. It includes:

1. credential cache/refresh;
2. address resolution performed by Tokio;
3. TCP connect and `TCP_NODELAY`;
4. TDS pre-login, TLS and login;
5. one server-routing reconnect, if Tiberius returns a routing target.

The same absolute deadline is reused across a routing reconnect. It is not
reset after the first server responds.

Each bb8 connection-creation attempt receives this bound. Existing
`retry_connection=True` may start another physical attempt while the overall
bb8 acquisition/retry window remains open; this candidate does not add or
remove those retries.

`connect(validate=True)` crosses sequential phases and therefore may consume
up to the applicable connect, acquire and operation budgets. The connect
setting bounds physical establishment, not the entire public method. No phase
silently borrows unused time from another phase.

### Pool acquisition

The acquisition deadline starts immediately before `Pool::get()` or
`Pool::get_owned()` and ends after:

- a lease becomes available;
- `test_on_check_out`, if enabled, completes;
- pending `RESETCONNECTION` validation completes.

It does not include the SQL operation that follows. bb8's builder
`connection_timeout` is set to the effective acquisition duration, so its
statistics and error path remain coherent.

If the caller times out while waiting, no lease is returned and no application
SQL starts. bb8's cancellation-safe checkout path remains responsible for any
connection it already wrapped.

### SQL operation

The operation deadline begins after acquisition and immediately before the
first request await. It ends only after FastMssql has fully consumed and
converted the response currently promised by that public API.

It covers:

- `ping`;
- `query` and `simple_query`;
- `execute`;
- `query_batch` and `execute_batch`;
- `bulk_insert`;
- transaction data operations;
- `BEGIN` and `COMMIT`.

For batch and bulk APIs, the deadline covers the entire public call. It is not
reset for each statement, chunk, RPC or response.

Because current `QueryStream` results are buffered before return, the deadline
covers the current full network read. It does not cover later Python iteration
over already-buffered rows.

### Transaction lifetime

The transaction lifetime deadline is created only after the complete
successful response to `BEGIN TRANSACTION` is consumed. It is stored with the
Rust transaction session and remains absolute until COMMIT, ROLLBACK or close.

For each data operation and COMMIT, the effective deadline is the earlier of:

- the operation deadline, if configured;
- the transaction lifetime deadline, if configured.

An idle transaction does not create a background timer. On the next public
operation or settlement, an already expired lifetime is detected before
starting more application SQL. The connection is retired, causing SQL Server
to roll back when the transport closes.

A lifetime expiry never prevents cleanup. Explicit ROLLBACK and `close()` use
the independent rollback budget even if the transaction lifetime has already
expired.

### Rollback and close

Explicit `rollback()` and the rollback issued by `close()` share the rollback
deadline. `close()`:

1. removes the connection/lease from the reusable transaction state;
2. attempts a complete rollback within the configured budget;
3. returns success only after the response is fully consumed;
4. on timeout or error, marks a pooled connection broken or drops the direct
   socket;
5. never returns a partial TDS stream to the pool.

Transport closure is the final rollback guarantee for an uncommitted SQL
Server transaction. A timeout is still surfaced; it is not swallowed.

## Internal architecture

### Configuration

`src/timeout_config.rs` owns:

- `PyTimeoutConfig`;
- validated optional/required durations;
- conversion from positive finite Python seconds to Rust `Duration`;
- the canonical explicit defaults;
- compatibility derivation from `PyPoolConfig`;
- cloning and representation.

`PyConnection` stores one effective `PyTimeoutConfig`. `SharedPoolSource` and
`TransactionHandles` carry a clone into pooled transactions. No operation
reads mutable Python state after construction.

### Deadline helpers

One small internal module owns:

- stable timeout phases/operation names;
- `Instant` deadline construction;
- the minimum of operation and transaction deadlines;
- conversion of elapsed deadlines to `OperationTimeoutError`;
- metadata flags.

The helper accepts futures; it does not own SQL, pool or transaction state.
Resource disposal remains with the existing guards.

### Pool manager

`AzureConnectionManager` stores the optional physical-connect duration. Its
complete connection future is wrapped in one deadline.

`establish_pool()` receives the effective timeout policy and configures bb8
with `acquire_timeout_secs`. `map_pool_checkout_error()` receives the public
operation name and maps `RunError::TimedOut` to a phase-`acquire`
`OperationTimeoutError`.

Physical-connect timeouts remain distinguishable from an exhausted checkout
deadline.

### Pooled operations

Acquisition and operation execution become two explicit phases:

```text
ensure pool
  -> acquire with acquire deadline
  -> mark/guard physical session
  -> execute and consume with operation deadline
  -> complete as NeedsReset, or retire as Broken
```

On timeout after acquisition, `PooledOperationGuard` is left incomplete. Its
drop path marks the connection unusable before bb8 sees it again.

### Transaction state

`TransactionSession` adds an optional lifetime deadline. The existing
epoch-checked cancellation guard remains active for every in-flight wire
operation.

Timeout handling follows the same state-machine rules as external
cancellation:

- remove/retire the connection;
- transition to `Failed`;
- disarm a cancellation guard only after deterministic timeout cleanup has
  taken ownership of the connection;
- never restore `Active` after an incomplete response.

COMMIT retains its existing special error mapping.

## Deterministic RED/GREEN verification

The SQL-auth registry adds exactly ten cases, `TIME-001` through `TIME-010`.
The registry grows from `285` to `295` unique IDs. No existing ID is removed,
renamed, skipped or weakened.

### `TIME-001` — configuration and compatibility

Pure/static contracts prove:

- exact constructor defaults and concrete signature;
- finite-positive validation and boolean rejection;
- optional `None` behavior;
- stub, package export and README consistency;
- legacy `PoolConfig.connection_timeout_secs` fallback;
- explicit `TimeoutConfig` acquisition precedence;
- connection and transaction clone isolation.

The baseline fails because `TimeoutConfig` and `OperationTimeoutError` do not
exist.

### `TIME-002` — physical handshake blackhole

A local async TCP fixture accepts a connection and deliberately never answers
TDS pre-login. No external network or sleep race is used.

The candidate must:

- raise `OperationTimeoutError` with phase/operation `connect`;
- finish within a bounded tolerance around the configured subsecond deadline;
- report `retryable=True`, `connection_discarded=False`,
  `outcome_unknown=False`;
- close the accepted socket and leave no pool/session handle.

The baseline requires an outer asyncio timeout and therefore fails the native
error contract.

### `TIME-003` — saturated pool acquisition

With `max_size=1`, one task holds the only lease. A second request uses a short
acquisition deadline and a longer operation deadline.

It must:

- fail specifically in phase `acquire`;
- start no SQL for the second request;
- preserve the holder;
- report safe application-level retry metadata;
- succeed after the holder returns the lease.

This replaces message parsing but preserves compatibility because
`OperationTimeoutError` subclasses `SqlConnectionError`.

### `TIME-004` — query timeout and retirement

A real SQL-auth query blocks through `WAITFOR`. A short operation deadline
must:

- raise the typed phase-`operation` error;
- retire the physical connection;
- terminate the server-side request/session;
- use a different `connection_id` on recovery;
- leave the pool bounded and usable;
- leave zero candidate sessions after disconnect.

### `TIME-005` — write outcome is not retried

A parameterized command uses a unique business key and a proxy that can hold
the final response. On timeout:

- `retryable=False`;
- `outcome_unknown=True`;
- the connection is discarded;
- FastMssql sends the command exactly once;
- the test reconciles the final row by key instead of assuming rollback or
  success.

No transparent retry is accepted.

### `TIME-006` — one absolute batch/bulk budget

A deterministic multi-step batch and a chunked bulk path consume a shared
operation budget. The test proves the deadline is not restarted for each
internal item/chunk, the connection is retired after expiry and no later item
is submitted by FastMssql.

Atomicity remains governed by each API's existing transaction contract; the
timeout feature does not invent rollback or retry.

### `TIME-007` — transaction lifetime

A pooled transaction begins, performs a parameterized write and remains idle
past its configured lifetime. The next operation must:

- detect expiry before sending more SQL;
- retire the lease;
- transition the transaction to failed;
- roll back the uncommitted write through transport closure;
- free the pool slot for a waiting transaction;
- leave zero server sessions after teardown.

### `TIME-008` — in-flight transaction operation

The earlier of operation and transaction deadlines wins during a blocking
transaction query/command. The exception reports the winning phase. The
epoch-checked guard retires the lease and a stale cleanup task cannot close a
later transaction.

### `TIME-009` — COMMIT and rollback precedence

The existing TCP proxy holds a COMMIT response after the durable write is
observable. Expiry must produce `CommitOutcomeUnknown` with a typed timeout
cause and no rollback/retry.

A separate rollback/close response hold must surface
`OperationTimeoutError(phase="rollback")`, discard the transport and leave the
business row rolled back.

### `TIME-010` — framework and concurrent recovery

Real FastAPI/ASGI and Flask-through-ASGI requests execute a mix of successful,
acquire-timeout and operation-timeout work through one persistent pool.

The test proves:

- the event loop heartbeat remains responsive;
- typed errors cross the Python wrapper unchanged;
- at least 1,000 operations with concurrency 100 remain bounded by
  `pool.max_size`;
- no timed-out physical connection is reused;
- a post-load smoke query succeeds;
- teardown leaves zero candidate sessions.

Flask `async def` under plain WSGI remains functional compatibility only; its
per-request event-loop limitation is reported, not misrepresented as
true-async load behavior.

## Required regression gates

Before the candidate can be marked remediated:

- focused pure/static timeout contracts pass;
- all ten `TIME-*` cases pass on real SQL Server SQL authentication;
- every existing strict ID remains present and passing;
- true-async, framework, resilience and load lanes pass;
- the existing 99,999-transaction bounded-load evidence remains green;
- the complete applicable upstream Python suite passes;
- Rust unit tests pass;
- Tiberius vendored unit/doctest gates remain green where applicable;
- a clean ABI3 wheel builds, installs and imports;
- `cargo fmt --check` passes;
- Clippy with `-D warnings` passes;
- Ruff and `compileall` pass;
- `cargo audit --deny warnings` reports zero findings;
- hosted raw Cargo/Rust/wheel contracts pass on Linux, macOS and Windows;
- no credential value appears in source, fixtures, artifacts or generated
  reports;
- all candidate sessions and requests are zero after teardown.

Timing assertions use monotonic clocks and wide bounded tolerances. A timeout
test never passes merely because the whole pytest process hit its watchdog.

## Branch and commit discipline

The candidate uses these independent branches/worktrees:

```text
docs/operation-timeouts-design
test/operation-timeouts
feat/operation-timeouts
docs/operation-timeouts-status
```

Required history:

1. design specification commit;
2. detailed implementation-plan commit;
3. deterministic RED test commit(s);
4. minimal GREEN implementation commit(s);
5. exact technical merge into `test/sql-auth-validation`;
6. generated evidence commit from the verified technical SHA;
7. live audit/roadmap status commit;
8. documentation-only merge into the cumulative fork.

RED tests are not rewritten or weakened in GREEN. A hosted-only defect receives
an additional RED commit before its fix, as with PoolConfig.

Every push targets `origin`
`https://github.com/galeamarcel/FastMssql.git`. `upstream` remains fetch-only
with push URL `DISABLED`. A future upstream candidate requires a new branch
from the then-current `upstream/master`, a fresh RED reproduction and a new
explicit approval from Marcel Galea.

## Acceptance criteria

The candidate is complete only when:

1. all five timeout phases have distinct documented budgets;
2. configuration validation is exact and introspectable;
3. legacy acquisition-timeout callers retain their effective value;
4. physical connect and pool acquisition are independently bounded;
5. SQL operation expiry always retires an uncertain TDS session;
6. transaction lifetime is absolute and cannot be reset by later operations;
7. rollback/close use an independent cleanup budget;
8. COMMIT timeout remains `CommitOutcomeUnknown` with the timeout as cause;
9. no SQL operation is retried automatically;
10. server requests/sessions terminate and pool recovery is measured;
11. all local and hosted gates pass at exact recorded SHAs;
12. the live production-readiness audit and future upstream roadmap are
    updated with measured evidence;
13. no branch, push or PR is created against the original repository.
