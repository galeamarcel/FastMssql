# FastMssql Pool Observability Design

**Status:** approved for inline execution by Marcel Galea's standing
authorization; specification self-review is mandatory before implementation.

**Date:** 26 July 2026

**Source baseline:** `test/sql-auth-validation` at
`8bf2a448df9a46128aebc7da8b9ed60b3bb249ea`

**Repository boundary:** all design, RED, GREEN, evidence and status branches
are published only to `https://github.com/galeamarcel/FastMssql.git`.
`Rivendael/FastMssql` remains fetch-only and receives no branch, push or pull
request without a new explicit approval.

## Objective

Expose the complete pool statistics already maintained by bb8 0.9.1 through
FastMssql's existing `Connection.pool_stats()` API.

This is the first independently reviewable observability candidate. It covers
pool gauges, checkout pressure, wait time, timeouts, physical connection
creation and bb8's four retirement-event counters without adding a dependency,
callback, logging side effect or SQL hot-path allocation.

Operation-duration histograms, outcome counters, trace context and an
OpenTelemetry bridge are a second candidate. They are deliberately not mixed
into this pool-statistics change.

## Measured baseline gap

bb8 0.9.1 already records these values per pool instance:

```text
get_started
get_direct
get_waited
get_timed_out
get_wait_time
connections_created
connections_closed_broken
connections_closed_invalid
connections_closed_max_lifetime
connections_closed_idle_timeout
```

It also derives `pending_gets()` from the same statistics snapshot.

FastMssql currently calls `pool.state()` but returns only:

```text
connected
connections
idle_connections
active_connections
max_size
min_idle
```

The historical counters are discarded before the Python dictionary is built.
An application therefore cannot distinguish an idle pool from checkout
pressure, cannot measure accumulated wait time, and cannot tell whether
connections were replaced because they were broken, invalid, old or idle.

The missing data is not a limitation of SQL Server, Tiberius or bb8. It is an
adapter omission in `src/connection.rs::pool_stats`.

## Scope decomposition

Observability is too broad for one safe upstream-sized diff. It is split into
three layers:

1. **This candidate — pool metrics:** expose bb8's existing gauges/counters.
2. **Future operation metrics:** bounded duration histograms and typed outcome
   counters for every FastMssql operation.
3. **Future telemetry bridge:** optional OpenTelemetry/Prometheus integration
   consuming the stable metrics and operation-event contracts.

The split keeps the first change dependency-free and permits each later layer
to receive its own privacy, performance and compatibility review.

## Approaches considered

### Approach A — expose existing bb8 statistics, recommended

Extend `pool_stats()` with stable scalar keys mirroring bb8's counters. The
method remains pull-based and asynchronous only because it reads the existing
Tokio pool lock.

Advantages:

- no new dependency or global telemetry provider;
- no GIL attachment in the SQL hot path;
- no callback reentrancy or exporter backpressure;
- statistics are already collected by bb8, so normal operations gain no new
  instrumentation cost;
- the diff is small enough for an independent future upstream PR.

Trade-off: values are scoped to the current pool instance and reset when that
pool is dropped.

### Approach B — direct OpenTelemetry SDK integration

Add Rust or Python OpenTelemetry SDK/exporter dependencies and create spans
and instruments inside the driver.

Rejected for this candidate because it would couple the core wheel to global
provider ownership, sampling, exporter shutdown and rapidly evolving optional
dependencies. It also combines pool statistics, operation timing and trace
context in one large compatibility surface.

### Approach C — invoke a Python callback for each event

Call a user-supplied Python listener on checkout, completion and retirement.

Rejected because callback execution would attach the GIL in the hot path and
introduce blocking, reentrancy, exception and backpressure semantics into the
driver. A slow or failing observer must never affect SQL correctness.

## Public API

No constructor argument, class, method or dependency is added.

`await Connection.pool_stats()` keeps every existing key and adds these exact
keys:

```python
{
    # Existing gauges/configuration; semantics unchanged.
    "connected": bool,
    "connections": int,
    "idle_connections": int,
    "active_connections": int,
    "max_size": int,
    "min_idle": int | None,

    # New current-pool checkout counters.
    "get_started": int,
    "get_direct": int,
    "get_waited": int,
    "get_timed_out": int,
    "pending_gets": int,
    "get_wait_time_seconds": float,

    # New current-pool physical-connection counters.
    "connections_created": int,
    "connections_closed_broken": int,
    "connections_closed_invalid": int,
    "connections_closed_max_lifetime": int,
    "connections_closed_idle_timeout": int,
}
```

The Python wrapper forwards the extension dictionary unchanged. Both public
stub layers describe the complete value union as
`int | float | bool | None`.

The new names are a FastMssql compatibility contract even if the underlying
pool implementation changes later.

## Counter semantics

All new integer counters are monotonic within one concrete bb8 pool instance.

- `get_started`: every checkout started, before waiting. The exported snapshot
  is reconciled to at least the sum of the three completed-result counters
  because bb8 reads its independent relaxed atomics separately.
- `get_direct`: successful checkout without waiting for availability.
- `get_waited`: successful checkout that waited for availability.
- `get_timed_out`: checkout whose bb8 acquisition budget expired.
- `pending_gets`: reconciled started minus direct, waited and timed-out
  completions.
- `get_wait_time_seconds`: total time accumulated by waited or timed-out
  checkouts, converted from bb8's microsecond counter to seconds.
- `connections_created`: physical connections successfully added to the pool.
- `connections_closed_broken`: connections rejected by `has_broken`, including
  cancelled, forced, fatal-I/O/protocol and security-retirement paths.
- `connections_closed_invalid`: connections closed after checkout validation
  failed.
- `connections_closed_max_lifetime`: connections reaped or returned after the
  configured maximum lifetime.
- `connections_closed_idle_timeout`: connections reaped after the configured
  idle timeout.

The checkout counters measure every bb8 pool acquisition, not only
application query calls. `connect(validate=True)` and `ping()` perform a real
readiness checkout and therefore advance the counters. Repeating
`connect(validate=True)` reuses the same pool but deliberately performs and
counts a new readiness checkout; `connect(validate=False)` and `pool_stats()`
do not acquire a connection.

The four `connections_closed_*` counters are historical event categories, not
an exclusive partition of physical connection closures. In bb8 0.9.1,
checkout validation records `connections_closed_invalid` before it marks the
lease invalid. Dropping that lease still invokes the manager's `has_broken`;
if the transport is also unsafe, the same physical connection additionally
increments `connections_closed_broken`. Therefore consumers must not sum these
four values to derive a total number of uniquely closed connections.

`connections_closed_broken` intentionally exposes the safe bb8 reason
category, not SQL text or a high-cardinality application-specific cause.
Finer driver outcome reasons belong to the future operation-metrics candidate.

## Snapshot and lifecycle semantics

The dictionary is a snapshot of the currently installed pool:

- before pool creation, every new counter is zero;
- while connected, values come from one call to `pool.state()`;
- after `disconnect()` drops that pool, gauges and counters are again zero;
- a reconnect creates a new bb8 pool and starts a new counter epoch;
- `connected=False` means there is no current pool handle, exactly as before.

FastMssql does not silently aggregate values across pool generations.
Applications that need process-lifetime totals scrape/export counters before
shutdown and rely on the monitoring system's normal counter-reset handling.

The method is diagnostic and does not enter lifecycle SQL admission. It remains
callable during `Open`, `Closing` and `Closed` and does not create a pool,
perform I/O or delay graceful shutdown beyond the existing short pool read.

The snapshot is not a database transaction. Concurrent checkout/drop activity
can progress immediately before or after capture. bb8 constructs one
`State::statistics` value by loading independent `Relaxed` atomics, so the
loads are inexpensive but not one multi-counter atomic capture.

FastMssql therefore does not call bb8's subtraction-based
`Statistics::pending_gets()` directly. It derives:

```text
completed_gets = get_direct + get_waited + get_timed_out
exported_get_started = max(bb8_get_started, completed_gets)
pending_gets = exported_get_started - completed_gets
```

This reconciliation represents the logical fact that a completed checkout
must already have started, prevents an impossible negative/wrapped pending
value during a transient cross-atomic observation, and guarantees the public
arithmetic contract without adding locks or driver-owned counters. It does not
alter bb8 state.

## Required invariants

Every returned snapshot must satisfy:

```text
active_connections
    == connections - idle_connections

get_started
    == get_direct + get_waited + get_timed_out + pending_gets

0 <= idle_connections <= connections <= max_size
all counters >= 0
get_wait_time_seconds is finite and >= 0.0
```

`pending_gets` may be non-zero only while checkout futures are outstanding.
The tests use server-visible gates and polling; they do not assume a scheduler
sleep is proof that a waiter reached bb8.

## Error and cancellation behavior

`pool_stats()` performs no SQL and does not transform operation errors.

- acquire timeout continues to raise `OperationTimeoutError` in the operation
  task and increments `get_timed_out`;
- cancellation/fatal protocol paths continue to retire the lease and
  eventually increment `connections_closed_broken`;
- checkout validation failure continues to retry according to bb8 and
  increments `connections_closed_invalid`; when the failed transport is also
  marked unsafe by FastMssql's cancellation-safe manager, the same retirement
  also increments `connections_closed_broken`;
- reaper closures increment only their exact lifetime/idle categories;
- observer reads never suppress, replace or retry the underlying error.

Statistics are observed after bounded convergence where bb8 completes a drop
or reaper action. Tests do not require a counter to change before the owning
guard has actually returned to the pool.

## Security and privacy

The complete key set is fixed and low-cardinality. Values are only numeric,
boolean or `None`.

The snapshot must never contain:

- SQL text or normalized SQL;
- parameter values, counts or types;
- server, port, database, schema or table names;
- application name, username or authentication method;
- connection strings, access tokens, passwords or certificate paths;
- exception messages or arbitrary labels.

No metric is logged or exported automatically. Reading the dictionary is an
explicit application action.

## Performance model

Normal connect/query/transaction/batch paths are unchanged. bb8 already
updates its statistics with relaxed atomics.

The only added work is when the caller explicitly invokes `pool_stats()`:

1. read the existing pool handle;
2. call `pool.state()`;
3. reconcile the four checkout counters with bounded integer arithmetic;
4. convert one `Duration` to `f64` seconds;
5. allocate the returned Python dictionary.

There is no background task, timer, ring buffer, callback, histogram or
unbounded collection.

## Deterministic SQL-auth cases

The strict specification gains ten IDs:

- `OBS-001`: exact public key/type schema, zero disconnected snapshot and all
  arithmetic invariants.
- `OBS-002`: direct checkout and physical creation counters increase after
  successful SQL and remain bounded by `max_size`.
- `OBS-003`: pool saturation exposes a server-gated pending checkout, then a
  waited completion and positive accumulated wait time.
- `OBS-004`: acquire timeout increments `get_timed_out` exactly once and pool
  recovery remains successful.
- `OBS-005`: cancelled/fatally broken pooled work increments
  `connections_closed_broken` and replaces the physical session.
- `OBS-006`: a killed idle session with checkout validation enabled increments
  both the validation-event (`connections_closed_invalid`) and unsafe-transport
  (`connections_closed_broken`) counters before a healthy replacement is
  returned, demonstrating that retirement categories may overlap.
- `OBS-007`: the existing maximum-lifetime scenario increments only the
  max-lifetime close category.
- `OBS-008`: the existing idle-reaper scenario increments only the idle-timeout
  close category.
- `OBS-009`: concurrent scraping during at least 10,000 bounded SQL operations
  preserves every invariant, event-loop progress and a healthy post-load
  query.
- `OBS-010`: SQL, parameters, application/database/login sentinels and
  credentials are absent from keys, values, `repr()` and generated evidence.

The strict matrix grows from 311 to 321 unique case IDs. Existing IDs,
assertions and timing bounds are not weakened.

## RED/GREEN discipline

The RED branch adds the exact schema contract and real SQL-auth cases before
implementation. On the baseline:

- the six existing keys still pass;
- each new-key assertion fails because the adapter discards bb8 statistics;
- no RED test is accepted merely because an unrelated query fails;
- no expected failure is converted into skip or swallowed by cleanup.

GREEN reads `State.statistics` and adds only the declared keys. If a test
reveals a bb8 semantic mismatch, the design is corrected explicitly rather
than fabricating a counter in FastMssql.

## Required regression gates

Before the candidate can be marked `VERIFIED_FORK`:

- OBS-001–OBS-010 pass against the approved Docker SQL Server with SQL
  authentication;
- all 321 strict specification IDs have complete PASS evidence;
- the complete strict, true-async, framework, resilience, load and applicable
  upstream lanes pass;
- the 10,000 and both 99,999 transaction profiles remain green;
- `cargo fmt --check`, Clippy `-D warnings` and all Rust tests pass;
- Ruff and `compileall` pass;
- dependency audit has zero vulnerabilities and zero warnings;
- a clean ABI3 wheel installs and the pool-statistics contract passes from the
  installed artifact;
- raw Cargo, Rust, wheel and the installed contract pass on Linux, macOS and
  Windows;
- concurrent scrape evidence has zero leaked SQL sessions;
- generated reports contain no credential or sentinel value;
- no branch, push or PR targets the original repository.

Real SQL-auth remains local unless a hosted SQL Server runner exists. The
hosted limitation is recorded and never represented as an MSSQL hosted pass.

## Branch discipline

The candidate uses these independent branches/worktrees:

```text
docs/observability-metrics-design
test/observability-metrics
feat/observability-metrics
docs/observability-metrics-status
```

Required history:

1. design specification;
2. detailed implementation plan;
3. deterministic RED tests;
4. minimal GREEN adapter change;
5. exact technical merge into `test/sql-auth-validation`;
6. regenerated matrix/report from the exact merge;
7. audit and upstream-roadmap status;
8. documentation-only merge into the cumulative fork.

Every push targets `origin`. The upstream push URL remains `DISABLED`.

## Compatibility and migration

This is an additive value-level extension with one explicit schema migration:

- the method name, awaitability and six existing keys are unchanged;
- existing values retain their exact semantics and types;
- callers indexing known keys continue to work;
- callers asserting the previously exact six-key set must adopt the new exact
  seventeen-key schema;
- CONN-019 currently asserts the exact six-key set; GREEN replaces that
  assertion with the complete seventeen-key set and preserves every existing
  value/invariant assertion;
- CONN-011 continues to prove that repeated `connect()` reuses the same pool,
  but no longer compares historical counters for whole-dictionary equality:
  the default `validate=True` performs exactly one additional direct readiness
  checkout;
- no SQL, pool, timeout, lifecycle or transaction behavior changes;
- no application configuration or data migration is required.

The README states that counters are per current pool instance and reset after
disconnect/reconnect. It does not market them as process-lifetime totals.

## Rollback

Reverting the GREEN commit removes only the additional dictionary entries,
stub/docs contract and tests. It does not alter a database schema, wire
protocol, pool policy or dependency.

## Non-goals

This candidate does not add:

- operation duration/outcome metrics or percentile histograms;
- tracing spans, trace IDs, context propagation or OpenTelemetry packages;
- Prometheus endpoints or a monitoring server;
- callbacks, hooks, logs, SQL normalization or query fingerprints;
- direct-connection statistics for `execute_batch()` or direct
  `Transaction(...)`;
- aggregation across lifecycle generations or processes;
- reset/clear APIs for counters;
- retry, sampling, rate limiting or waiter backpressure;
- Tiberius changes, package version changes or release publication.

## Design self-review record

The design was reviewed against the exact bb8 0.9.1 source, FastMssql's
`pool_stats()` implementation, PoolConfig, timeout, cancellation, session
disposition and lifecycle contracts.

Corrections made during self-review:

1. The first draft proposed driver-owned atomics. They were removed because
   bb8 already records the required pool facts and duplicate counters could
   diverge under cancellation.
2. Cross-generation aggregation was removed because dropping an owner handle
   does not prove every external pool clone is physically closed; current-pool
   epochs are precise.
3. A Python callback was removed because observer latency and exceptions must
   not affect SQL futures.
4. `get_wait_time_seconds` is explicitly cumulative, not an average or a
   percentile.
5. Broken and invalid are kept as bb8's exact reason categories; this change
   does not invent unsupported sub-reasons.
6. Direct sockets are explicitly excluded so the public name `pool_stats`
   cannot be misread as whole-driver telemetry.
7. OBS-007/008 extend the existing lifetime/idle scenarios to avoid adding
   another long reaper sleep.
8. The schema allows `float` only for wait seconds and forbids labels or
   secret-bearing strings.
9. The current strict suite treats the six-key dictionary as exact.
   Compatibility language was corrected to identify the new exact key set as
   an intentional schema migration; the existing CONN-019 assertions are
   strengthened, not deleted or relaxed.
10. bb8's `State` is not a multi-counter atomic snapshot: its statistics are
    assembled from independent `Relaxed` loads. Direct use of
    `Statistics::pending_gets()` was removed from the implementation contract;
    completed counters are summed safely, exported `get_started` is reconciled
    upward when needed, and `pending_gets` is derived without underflow. The
    10,000-operation scrape test enforces the resulting invariant while writes
    are active.

No placeholder, unresolved API choice or hidden dependency remains.
