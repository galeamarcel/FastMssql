# FastMssql Connection Lifecycle and Graceful Shutdown Design

**Status:** Approved by Marcel Galea on 2026-07-26 through the standing
approval for future enterprise designs, specifications, plans and inline
implementations, subject to a recorded self-review before implementation

**Source baseline:** `test/sql-auth-validation` at
`0992f60a23db7e038cba484d9eadfbff6606d2ad`

**Target repository:** `https://github.com/galeamarcel/FastMssql.git`

**Publication boundary:** every branch, commit and hosted validation produced
by this candidate belongs only to Marcel Galea's fork. The original
`Rivendael/FastMssql` repository remains fetch-only with push URL `DISABLED`.
No upstream branch, push, pull request, package publication or dependency fork
is authorized by this design.

## Decision summary

FastMssql will add a connection-scoped lifecycle coordinator with three public
states:

```text
Open -> Closing -> Closed
  ^                    |
  +--------------------+
```

`Open` means that the connection object accepts new work. It does not mean
that a pool exists or that SQL Server is ready. `Closing` is an admission
barrier: work admitted before the transition may drain, settlement and cleanup
of an already active pooled transaction remain allowed, and all other new SQL
work is rejected before it can reach SQL Server. `Closed` means the shutdown
round has detached the owner-visible pool and ended its admission generation.

Compatibility requires `Closed -> Open`: the next explicit `connect()`,
context-manager entry or lazy SQL operation starts a new generation. This
preserves reconnect-after-disconnect and the existing nested-context behavior.
No operation can reopen a connection while the preceding generation is still
`Closing`.

One public `LifecycleConfig` supplies two independent positive finite budgets:

| Phase | Public setting | Default | Scope |
| --- | --- | ---: | --- |
| Graceful drain | `shutdown_timeout_secs` | `30.0` | wait for admitted operations and pooled transaction leases |
| Forced retirement | `force_timeout_secs` | `5.0` | cancel remaining work, retire transports and revoke pooled transaction leases |

`disconnect()` starts or joins one cancellation-safe shutdown supervisor. A
cancelled Python waiter cannot abandon that supervisor. If graceful drain
finishes within its budget, `disconnect()` returns its existing Boolean result.
If the graceful budget expires, FastMssql performs bounded forced retirement,
ends in `Closed`, and raises a typed `ShutdownTimeoutError` to every waiter.
It never reports a forced shutdown as graceful.

All ordinary connection operations acquire an admission permit before pool
initialization or network I/O. A pooled transaction holds a lifecycle session
permit for the complete lifetime of its checked-out physical lease, not merely
for each method call. Direct `Transaction(...)` instances own a separate
socket and are intentionally outside a `Connection` object's lifecycle.

The feature is fail-closed. Forced interruption drops the affected transport;
FastMssql does not retry SQL. If COMMIT completion cannot be confirmed,
`CommitOutcomeUnknown` remains the public terminal error and chains the
lifecycle error as its cause.

## Measured baseline defect

The source baseline stores the pool as:

```rust
Arc<RwLock<Option<bb8::Pool<_>>>>
```

`disconnect()` only replaces the `Option` with `None`. bb8 0.9.1 does not
provide a pool-close operation, and its owned leases retain their own
`PoolInner`. Dropping the owner-visible handle therefore cannot revoke a
checked-out lease or a pool clone already held by an admitted future.

The defect was reproduced against the approved Docker SQL Server with SQL
authentication and the strict suite's `SslConfig.development()` TLS policy:

```text
query                                 WAITFOR DELAY '00:00:02'; SELECT 42
delay before disconnect              0.2 seconds
disconnect return                    True
disconnect elapsed                   0.000 seconds
query result                         1 row
query total elapsed                  2.012 seconds
```

The first reproduction omitted the strict TLS configuration and failed during
certificate validation with `UnsupportedCertVersion`. That was a test setup
difference, not a FastMssql defect. Repeating with the exact strict fixture
isolated the lifecycle race above.

Current behavior permits these false shutdown claims:

- a pooled query can keep executing after `disconnect()` returns;
- an operation waiting for pool checkout can retain a pool clone;
- a pooled transaction can keep an owned lease and an open SQL transaction;
- `execute_batch()` can keep its direct socket because it bypasses the pool;
- cancellation of the Python task awaiting `disconnect()` can abandon any
  future inline wait implementation;
- a concurrent operation can recreate a pool unless admission and pool
  ownership are coordinated atomically.

## Goals

1. Make shutdown an explicit, race-free state machine.
2. Block new SQL admissions while one generation is closing.
3. Wait for every operation admitted before the barrier.
4. Count a pooled transaction for the full owned-lease lifetime.
5. Bound both graceful drain and forced retirement.
6. Make the shutdown supervisor independent of any one Python waiter.
7. Coalesce concurrent `disconnect()` calls onto one result.
8. Retire, rather than reuse, every transport interrupted by forced shutdown.
9. Preserve `CommitOutcomeUnknown` for unconfirmed COMMIT.
10. Preserve explicit and lazy reconnection after shutdown.
11. Expose stable, typed lifecycle configuration, state and errors.
12. Cover pooled, direct-batch, transaction, context-manager and framework
    paths with deterministic real SQL-auth tests.
13. Preserve all existing timeout, cancellation, pool, transaction, TLS,
    framework, load, packaging and security contracts.

## Non-goals

This candidate does not:

- add waiter limits, queue policies or a process-global connection budget;
- add pool histograms, reason counters, tracing or OpenTelemetry;
- add TDS `ATTENTION` or reuse a force-interrupted session;
- add query retry, transaction retry or idempotency classification;
- make `is_connected()` a readiness check;
- make `QueryStream` a live server-side stream;
- change result-set, stored-procedure OUT parameter or row-count semantics;
- change `PoolConfig` sizing or `TimeoutConfig` operation deadlines;
- manage independently constructed direct `Transaction(...)` objects;
- add a public immediate `terminate()` method;
- aggregate body and cleanup exceptions in Python context managers;
- make Flask `async def` under WSGI share one persistent event loop;
- bump the package version or publish a wheel;
- modify bb8, Tokio, Tiberius or PyO3;
- push or open a pull request against the original repository.

Pool backpressure, lifecycle metrics, privacy-safe tracing and TDS attention
remain separately designed enterprise candidates.

## Options considered

### Option A — State plus one active-call counter

Add an atomic state, increment a counter around each public connection future
and wait for zero in `disconnect()`.

This is the smallest patch, but it cannot correctly represent an idle pooled
transaction that still owns a lease. It also leaves shutdown tied to the
Python task awaiting it unless a second task architecture is added, and it
does not provide a safe way to revoke old-generation transactions after the
grace period. It is rejected as insufficient for enterprise shutdown.

### Option B — Dedicated generation-aware lifecycle coordinator

Add a coordinator shared by `ConnectionHandles`, batch paths and pooled
transactions. It owns the state transition, admission permits, pooled-session
registry, force signal and shared shutdown result. A runtime-owned supervisor
performs shutdown after the first caller starts it.

This option has one atomic admission boundary while preserving direct parallel
I/O through bb8. It can distinguish ordinary operations from long-lived
transaction sessions and can make shutdown cancellation-safe without putting
all SQL traffic behind one dispatcher.

**Selected.**

### Option C — Actor owns the pool and all SQL commands

Move pool ownership and every command behind one Tokio actor. Shutdown becomes
an actor message and is naturally serialized.

This gives strong ownership but rewrites the driver's hot paths, adds a queue
in front of bb8, complicates Python cancellation, and risks turning the actor
into the same serialization funnel this project is intended to avoid. It is
rejected for this isolated candidate.

## Public API

### `LifecycleConfig`

```python
LifecycleConfig(
    shutdown_timeout_secs: float = 30.0,
    force_timeout_secs: float = 5.0,
)
```

Both values:

- reject `None` and booleans;
- accept Python integers and floats;
- must be finite and greater than zero;
- must be at least one nanosecond;
- must not exceed `3_153_600_000` seconds, the existing portable 100-year
  deadline ceiling;
- must fit the current platform's monotonic clock.

The class exposes mutable Python properties and a stable `repr`, matching the
configuration style already used by `PoolConfig` and `TimeoutConfig`.
`Connection` stores an isolated effective copy. Its
`connection.lifecycle_config` property returns another isolated copy so later
mutation cannot alter an active connection unexpectedly.

The constructor gains one final optional argument:

```python
Connection(..., timeout_config=None, lifecycle_config=None)
```

Placing the argument last preserves all existing keyword behavior and the
current positional order.

### `ConnectionLifecycleState`

```python
ConnectionLifecycleState.OPEN
ConnectionLifecycleState.CLOSING
ConnectionLifecycleState.CLOSED
```

`str()` returns `Open`, `Closing` or `Closed`, and `repr()` uses the public
class name. `connection.lifecycle_state` is a synchronous, read-only property
backed by the coordinator's atomic state. Reading it performs no I/O and
cannot block on the async pool lock.

The initial state is `Open`, even though `is_connected()` is `False`.

### Typed errors

```python
class ConnectionLifecycleError(SqlConnectionError): ...
class ShutdownTimeoutError(ConnectionLifecycleError): ...
```

`ConnectionLifecycleError` covers two distinct fail-closed events:

1. a new SQL operation rejected at the `Closing` barrier;
2. an admitted old-generation operation interrupted during forced retirement.

Its stable attributes are:

```text
message
operation
phase                  "shutdown"
state                  "Closing" or "Closed"
generation
retryable
connection_discarded
outcome_unknown
forced
```

Admission rejection occurs before SQL, so `retryable=True`,
`connection_discarded=False`, `outcome_unknown=False` and `forced=False`.
FastMssql still performs no automatic retry. Forced interruption uses
`retryable=False`, `connection_discarded=True`, `forced=True`, and sets
`outcome_unknown` according to the operation's existing uncertainty policy.

`ShutdownTimeoutError` is returned only by shutdown waiters after graceful
drain exceeded its budget. In addition to the base attributes, it exposes:

```text
shutdown_timeout_seconds
force_timeout_seconds
active_operations_at_timeout
active_transactions_at_timeout
force_completed
```

The error is created after the bounded force phase. Its `state` is `Closed`.
`force_completed=False` is a visible invariant violation or unsupported
straggler, never a hidden success.

### `disconnect()` compatibility

The Python signature and return type remain:

```python
await connection.disconnect() -> bool
```

For a graceful shutdown, the Boolean means that the joined shutdown round
observed resources: an owner pool, an admitted operation, or an active pooled
transaction. Disconnecting an unused or already closed object returns
`False`. All concurrent waiters for one round receive the same Boolean or the
same structured timeout outcome.

After `ShutdownTimeoutError`, the connection is still `Closed`; forced
retirement is cleanup, not a request to keep the generation open.

## State and generation contract

### `Open`

- accepts ordinary SQL operations;
- permits lazy pool initialization;
- permits explicit `connect()` and `ping()`;
- may have no pool, an initializing pool or an established pool;
- does not imply readiness;
- belongs to exactly one monotonically increasing generation.

### `Closing`

- starts atomically with the first polled `disconnect()` future;
- rejects new connect, ping, query, execute, batch, bulk and transaction-begin
  work before pool acquisition or SQL;
- counts previously admitted pool waiters as active operations;
- allows commit, rollback and close on a pooled transaction already holding a
  permit from that generation;
- allows control-plane reads: `lifecycle_state`, `is_connected()`,
  `pool_stats()`, configuration properties;
- cannot be reopened by a lazy operation;
- is shared by every concurrent `disconnect()` waiter.

### `Closed`

- is reached only by the supervisor;
- has detached the owner-visible pool;
- has no active permit when graceful or forced cleanup completes normally;
- does not claim SQL readiness;
- permits a later SQL operation or explicit `connect()` to atomically create a
  fresh `Open` generation;
- never makes an old-generation transaction reusable.

State transition and permit admission use one async mutex-backed control
record. The public state is mirrored in an atomic byte only for nonblocking
introspection. The mutex, not the atomic mirror, is the source of transition
truth.

## Internal architecture

### Coordinator

One `Arc<ConnectionLifecycle>` is created with each `PyConnection` and cloned
into every operation handle.

Conceptually it owns:

```rust
struct LifecycleInner {
    state: InternalState,
    generation: u64,
    active_operations: usize,
    active_transactions: usize,
    participants: HashMap<u64, ForcedShutdownParticipant>,
    shutdown_round: Option<ShutdownRound>,
}
```

The concrete implementation may use a `Mutex`, `Notify`, `watch`, RAII guards
and boxed cleanup futures, but it must preserve these invariants:

1. admission and `Open -> Closing` are serialized by one source of truth;
2. every successful admission has one RAII decrement;
3. the zero-active notification cannot be lost;
4. participant IDs and permits belong to one generation;
5. a stale permit cannot decrement or close a newer generation;
6. the shutdown result is data, not a stored `PyErr`, so each Python waiter can
   construct an equivalent typed exception safely;
7. no lifecycle lock is held while awaiting SQL, pool checkout or Python
   attachment.

### Operation permits

These connection paths acquire one operation permit before calling
`ensure_pool_initialized_with_auth`:

- `query`;
- `simple_query`;
- `execute`;
- `connect`;
- `ping`;
- `__aenter__`;
- `query_batch`;
- `bulk_insert`;
- `execute_batch`, including its direct socket.

`pool_stats()`, `is_connected()`, state/configuration reads and
`Connection.transaction()` do not create SQL work and do not acquire an
operation permit.

Each admitted future listens for the force signal for its generation across
the complete pool-initialization, checkout and SQL-response path. A forced
branch drops any `PooledOperationGuard` incomplete so bb8 retires it. A direct
batch drops its socket. The permit is released only after result consumption
or fail-closed retirement.

External asyncio cancellation keeps its current behavior: dropping an
operation future retires an incomplete session. Lifecycle force cancellation
uses the same safe retirement rule but returns a typed lifecycle error when
the Python waiter is still present.

### Pooled transaction sessions

`Connection.transaction()` passes the lifecycle coordinator into
`Transaction::from_pool`.

The transaction acquires one session permit before obtaining its first owned
pool lease. The permit is stored with the Rust transaction session and remains
held while that lease exists, including idle time between method calls. It is
released on:

- confirmed commit;
- confirmed rollback;
- `close()`;
- operation failure that retires the connection;
- transaction lifetime expiry;
- Python/Rust object destruction;
- forced lifecycle retirement.

During `Closing`, an already permitted transaction may call only commit,
rollback or close. Begin and new data operations are rejected. During force,
the participant registry:

1. signals any in-flight transaction method;
2. locks each surviving session without holding the coordinator lock;
3. marks an incomplete connection unusable;
4. drops the owned lease or direct pooled transport;
5. moves the transaction to a terminal fail-closed state;
6. releases its lifecycle permit exactly once.

An old transaction object cannot acquire from a new connection generation.

Direct `Transaction(...)` does not receive a connection lifecycle coordinator
and preserves its existing ownership and timeout behavior.

### Cancellation-safe shutdown supervisor

The first `disconnect()` caller creates one `ShutdownRound` and spawns its
supervisor on the initialized Tokio runtime. The caller then waits on a shared
result channel. Later callers join the same channel.

The supervisor owns no Python object and continues if any or all Python
waiters are cancelled. It performs:

```text
1. transition Open -> Closing and snapshot whether resources exist
2. wait for operation + transaction counts to reach zero
3a. if graceful: detach pool, transition Closed, publish success
3b. on grace timeout:
      snapshot active counts
      publish force signal
      invoke registered transaction retirement
      wait at most force_timeout_secs for zero
      detach pool
      transition Closed
      publish structured timeout outcome
```

The pool is not detached at the start of `Closing`. A previously admitted
future may not yet have reached pool initialization and must still use the
same generation's pool. New operations cannot race in because admission is
already closed.

When force starts, pool initialization/check-out and SQL futures select the
force signal. After they unwind, detaching the final owner pool closes idle
sessions. The supervisor never waits while holding the pool write lock.

### Concurrent disconnect and reconnect

Concurrent disconnects never start competing timers or close different pools.
They share the first round's configured deadlines and outcome.

A reconnect cannot begin until the state is `Closed`. Once closed, the first
accepted explicit or lazy operation increments the generation and transitions
to `Open`. Results, permits, participant callbacks or force signals from an old
generation are ignored by the new one.

## Outcome safety

### Graceful completion

An operation already in flight completes normally. The shutdown waiter returns
only after its permit drops and the pool is detached. Therefore, after a
graceful `disconnect()` returns:

- no old-generation application operation can still be executing;
- no pooled transaction retains a lease;
- the owner pool is absent;
- candidate SQL sessions converge to zero;
- a later operation uses a new generation and, where required, a new session.

### Forced query, write, batch or bulk

The force signal cancels response consumption and retires the physical
connection. FastMssql cannot classify arbitrary SQL text as read-only:
`query()` and `simple_query()` can execute stored procedures or statements
with side effects. Their conservative uncertainty contract therefore matches
the existing operation-timeout policy.

Query, simple-query, execute, batch and bulk interruption raises
`ConnectionLifecycleError` with `outcome_unknown=True`. FastMssql does not
retry, compensate or claim rollback unless the SQL operation was inside a
transaction whose socket closure lets SQL Server roll it back. Connect,
acquire, readiness and ping failures that cannot have applied application SQL
use `outcome_unknown=False`.

### Forced transaction settlement

An interrupted rollback retires the socket and is fail-closed. An interrupted
COMMIT is special: `CommitOutcomeUnknown` remains the outer exception and the
forced `ConnectionLifecycleError` is its cause. No rollback or retry follows
an unconfirmed COMMIT.

## Python wrapper and context managers

The thin Python wrapper exports the new configuration, state and exception
types unchanged.

`Connection.__aexit__()` delegates to the same `disconnect()` supervisor. It
does not perform a second raw pool assignment. Normal and exceptional context
exit therefore use the same drain and force policy.

If both a body exception and a shutdown exception occur, Python's current
context-manager propagation behavior is preserved. Structured aggregation of
body and cleanup failures remains a separate candidate.

Nested contexts retain the current compatibility contract:

1. inner exit closes the current generation;
2. an outer-body query after inner exit lazily opens a new generation;
3. outer exit closes that new generation.

## Framework contract

### FastAPI and ASGI

One `Connection` is created for the application and explicitly connected in
the ASGI lifespan startup hook. Lifespan shutdown awaits `disconnect()`.
Requests already admitted may drain; requests reaching the driver after
`Closing` receive a typed lifecycle error. A cancelled lifespan waiter does
not abandon resource cleanup.

### Flask through an ASGI adapter

The persistent ASGI event loop uses the same application-scoped lifecycle as
FastAPI. True async concurrency and graceful shutdown are testable here.

### Flask `async def` under WSGI

Functional compatibility remains supported, but WSGI may create a per-request
event loop. The report must not claim that a single connection lifecycle or
pool is safely shared across unrelated transient loops. WSGI lifecycle tests
verify deterministic per-loop creation and cleanup, not true-async throughput.

## Strict SQL-auth cases

The approved strict specification gains sixteen unique IDs:

- `LIFE-001`: public config/state/error exports, defaults, signatures, stubs,
  copy isolation and portable validation.
- `LIFE-002`: initial `Open`, pool-independent state, graceful connect and
  disconnect transitions.
- `LIFE-003`: disconnect waits for an admitted `WAITFOR` query before
  returning and then leaves zero candidate sessions.
- `LIFE-004`: an operation started during `Closing` is rejected before SQL,
  while a post-`Closed` operation opens a new generation.
- `LIFE-005`: concurrent disconnect callers coalesce and cancellation of the
  initiating Python waiter cannot abandon shutdown.
- `LIFE-006`: an admitted operation waiting for pool checkout is counted and
  cannot escape the barrier.
- `LIFE-007`: an active pooled transaction may commit during graceful closing;
  shutdown waits for lease release.
- `LIFE-008`: rollback and `close()` during graceful closing release the
  pooled transaction permit exactly once.
- `LIFE-009`: grace expiry force-interrupts a read, retires its session, raises
  typed errors and permits clean generation recovery.
- `LIFE-010`: grace expiry force-interrupts a write/batch without retry and
  exposes `outcome_unknown=True`.
- `LIFE-011`: an idle pooled transaction is rolled back by forced retirement,
  loses its lease and cannot attach to the next generation.
- `LIFE-012`: a forced unconfirmed COMMIT preserves
  `CommitOutcomeUnknown(ConnectionLifecycleError)`.
- `LIFE-013`: `execute_batch()` direct sockets, async context exit and nested
  lazy reconnection obey the same lifecycle.
- `LIFE-014`: repeated query/reconnect/disconnect races across at least 100
  generations and 2,000 operations leave no old-generation work or SQL
  sessions.
- `LIFE-015`: FastAPI/ASGI and Flask-through-ASGI use persistent lifecycle
  shutdown; Flask/WSGI is reported as functional per-loop compatibility.
- `LIFE-016`: cancelling an in-flight `Transaction.close()` retires the
  transport, rolls back server-side and releases the generation-scoped
  transaction permit without a compensating second close.

The strict matrix grows from 295 to 311 unique case IDs. Existing IDs and
assertions are not renumbered or weakened.

## Deterministic test mechanics

Timing tests use server-side gates, `WAITFOR`, events and monotonic clocks with
wide bounds. They do not pass merely because a process watchdog fires.

The real SQL-auth fixture uses unique application names. An observer connection
queries:

- `sys.dm_exec_sessions`;
- `sys.dm_exec_requests`;
- `sys.dm_tran_session_transactions`;
- `sys.dm_tran_active_transactions` where required.

Every force test proves both client-side and server-side cleanup:

1. the expected typed exception and metadata;
2. no automatic retry through a side-effect counter;
3. retirement of the interrupted session ID;
4. zero candidate requests and sessions after bounded convergence;
5. a new-generation smoke query succeeds.

The graceful regression reproduces the measured baseline exactly: a two-second
query starts, shutdown begins after the server request is visible, and
`disconnect()` must remain pending until the query completes. A fixed sleep
without server visibility is not accepted as synchronization.

The generation stress lane uses bounded concurrency and at least 100
open/close rounds. The existing 99,999-transaction bounded-load evidence
remains a mandatory regression gate; this candidate does not multiply that
expensive lane by 100 lifecycle rounds.

## Required regression gates

Before the candidate can be marked remediated:

- focused pure/static lifecycle contracts pass;
- all sixteen `LIFE-*` cases pass against real Docker SQL Server with SQL
  authentication;
- all 311 strict specification IDs have passing evidence;
- every existing strict test remains green;
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
- raw Cargo/Rust/wheel contracts pass on Linux, macOS and Windows;
- hosted lifecycle SQL-auth tests run on an available self-hosted SQL Server
  runner, or their local-only infrastructure limitation is recorded without
  weakening the local gate;
- no credential appears in source, logs, artifacts or generated reports;
- all candidate requests, transactions and sessions are zero after teardown.

## Branch and commit discipline

The candidate uses independent branches/worktrees:

```text
docs/lifecycle-state-design
test/lifecycle-state
feat/lifecycle-state
docs/lifecycle-state-status
```

Required history:

1. approved design specification commit;
2. detailed implementation-plan commit;
3. deterministic RED test commit(s);
4. minimal GREEN implementation commit(s);
5. exact technical merge into `test/sql-auth-validation`;
6. generated evidence commit from the verified technical SHA;
7. live audit/roadmap status commit;
8. documentation-only merge into the cumulative fork.

RED tests are never rewritten or weakened in GREEN. Any defect first observed
on another operating system or under hosted execution receives a focused RED
commit before its fix.

Every push targets `origin`
`https://github.com/galeamarcel/FastMssql.git`. `upstream` remains fetch-only
with push URL `DISABLED`. A future upstream proposal requires a fresh branch
from the then-current upstream baseline, a minimal independent reproduction
and new explicit approval from Marcel Galea.

## Compatibility and migration

Existing callers that do not provide `LifecycleConfig` receive bounded
30-second graceful and 5-second force phases.

Observable intentional changes:

- `disconnect()` no longer returns while admitted work is still using the
  old generation;
- a query racing with an active `Closing` phase receives a typed error instead
  of nondeterministically using or recreating a pool;
- an over-deadline shutdown raises `ShutdownTimeoutError` after forced cleanup;
- context-manager exit uses the same bounded lifecycle.

Preserved behavior:

- construction performs no SQL I/O;
- initial `is_connected()` remains false;
- explicit connect remains idempotent while `Open`;
- repeated disconnect of an unused/closed object returns false;
- explicit reconnect after disconnect works;
- lazy reconnect after an inner context exit works;
- normal query/transaction concurrency remains bounded by bb8 rather than a
  new global actor;
- no SQL operation is retried.

Applications should size `shutdown_timeout_secs` to their longest accepted
request SLA plus cleanup margin. A shutdown timeout is an operational signal
that work exceeded the graceful budget; suppressing it would hide uncertain
outcomes.

## Rollback

The candidate is independently revertible. Reverting the GREEN implementation
restores immediate pool-handle removal while leaving RED reproduction and
design evidence available.

No schema or persisted-data migration is involved. Application rollback can
omit `LifecycleConfig`, but it cannot restore old immediate-disconnect
semantics while running the fixed driver.

## Security and privacy

Lifecycle errors and shutdown metadata contain operation names, state,
generation and counts only. They never contain SQL text, parameter values,
credentials, connection strings, database names or hostnames.

Forced shutdown retires transports instead of returning partially consumed TDS
state or an active transaction to the pool. This preserves the existing
fail-closed security boundary for session impersonation and transaction
cleanup.

## Self-review record

The design was reviewed against the source baseline, the exact bb8 0.9.1
implementation, the strict SQL-auth matrix and the prior PoolConfig,
connection-readiness, cancellation-retirement and timeout specifications.

Corrections made during self-review:

1. The initial state is `Open`, not `Closed`; lifecycle admission and SQL
   readiness are distinct.
2. `Closed -> Open` is explicit because existing strict tests require both
   explicit reconnect and nested lazy reconnect.
3. The pool is detached after drain, not at the start of `Closing`, because a
   previously admitted waiter may not yet have initialized it.
4. Pooled transactions hold a session permit for the lease lifetime; a
   method-only counter would falsely report an idle active transaction as
   drained.
5. Commit and rollback remain allowed during `Closing`; otherwise graceful
   transaction settlement would be impossible.
6. Force timeout is separate from graceful timeout so cleanup cannot consume
   an unbounded second budget.
7. `ShutdownTimeoutError` is raised even when force succeeds, preventing a
   forced shutdown from being mislabeled as graceful.
8. Forced COMMIT preserves `CommitOutcomeUnknown`; the lifecycle feature does
   not weaken transaction outcome safety.
9. Pool backpressure, observability and TDS attention were removed from scope
   to preserve a reviewable lifecycle boundary.
10. Direct `Transaction(...)` remains outside connection scope, while the
    direct socket used internally by `Connection.execute_batch()` is included.
11. A second audit found that cancelling `Transaction.close()` while its
    rollback response was pending dropped the local socket/lease but left the
    generation-scoped transaction permit inside `TransactionSession`. The
    close phase is therefore an epoch-checked in-flight state: cancellation
    must retire the transport, release the permit exactly once, mark the
    transaction failed and preserve Python `CancelledError`. A later
    `disconnect()` must drain gracefully instead of waiting for the grace
    deadline and entering force cleanup.

No unresolved contradiction or placeholder remains. The repository does not
contain a `VERSION.md`; therefore this documentation-only candidate cannot
update such a file. This absence must be reported with each handoff rather
than inventing a version ledger outside the repository's established
structure.

## Acceptance criteria

The candidate is complete only when:

1. `Open`, `Closing` and `Closed` have the exact admission semantics above;
2. all connection SQL paths hold generation-scoped operation permits;
3. pooled transactions hold a permit for the complete lease lifetime;
4. graceful disconnect waits for all old-generation work;
5. concurrent and cancelled disconnect waiters cannot abandon cleanup;
6. forced shutdown is bounded and retires every interrupted transport;
7. forced writes expose uncertainty and are never retried;
8. unconfirmed COMMIT remains `CommitOutcomeUnknown`;
9. old transactions cannot attach to a new generation;
10. explicit and lazy reconnect compatibility remains green;
11. cancelling an in-flight `Transaction.close()` releases its lifecycle
    permit without requiring a second explicit close;
12. public configuration, state, errors, signatures and stubs agree;
13. all real SQL-auth, framework, stress, upstream, Rust, wheel, quality,
    security and hosted cross-platform gates pass at exact recorded SHAs;
14. the live production-readiness audit and upstream PR roadmap contain
    measured evidence and remaining limitations;
15. all branches and pushes exist only on Marcel Galea's fork.
