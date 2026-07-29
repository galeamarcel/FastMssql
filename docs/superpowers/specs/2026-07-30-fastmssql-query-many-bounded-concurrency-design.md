# FastMssql Bounded-Concurrency `query_many()` Design

**Status:** approved by the repository owner's standing authorization for
future design/specification/plan files and inline implementations, subject to
self-review and the branch/TDD gates in this document.

**Base SHA:** `7b5ee080ab57ff1d777b1d607590141870f1b9bd`

**Repository boundary:** every branch, commit, Docker artefact and hosted
check belongs only to `galeamarcel/FastMssql`. The
`Rivendael/FastMssql` remote remains fetch-only and its push URL must stay
exactly `DISABLED`.

## Goal

Add a production-safe `Connection.query_many()` API that runs one
parameterized SQL statement over independent parameter sets with true SQL
concurrency supplied by multiple physical leases from the existing pool.

The feature must provide:

- a lazy async-iterator result surface rather than an eager result list;
- fixed, pool-bounded worker concurrency;
- one end-to-end capacity window across input, active queries and completed
  driver-owned results;
- input-order or completion-order delivery;
- exact reuse of the existing query lifecycle, timeout, connection
  disposition, typed-parameter and operation-metric contracts;
- deterministic terminal cleanup after errors, cancellation, explicit close
  and async-context exit;
- privacy-safe zero-based query indices on failures;
- real SQL Server, fault, load, installed-wheel and graph evidence through
  99,999 operations.

This is the seventh and final functional slice of the approved batch/bulk
program. It does not make one TDS session concurrent and does not add
`Transaction.query_many()`.

## Verified baseline

The focused design is based on the exact tree at the base SHA:

- raw `Connection.query()` converts positional parameters through the closed
  typed-parameter path, admits one lifecycle operation, acquires one pool
  lease, applies the existing acquire/operation deadlines, buffers the first
  result set and records one `query` operation metric;
- cancellation or dropping an incomplete Rust query future drops
  `PooledOperationGuard`, which marks that physical connection unusable;
- successful security-context-changing SQL is already detected and retires
  its physical session;
- `Connection.pool_stats()` performs no SQL, does not create a pool, and
  exposes the configured `max_size` even before connection;
- `QueryStream` is the compatibility object for an already buffered first
  result set and no longer owns the pool lease when returned;
- `Transaction.query()` owns one transaction session whose TDS requests are
  intentionally sequential;
- `_bounded_sequence.py` already provides producer protocol selection,
  abnormal producer close and cancellation-shielded cleanup;
- `_execute_many.py` already contains the positional parameter-set shape,
  named-parameter and 2,098-parameter validations that `query_many()` must
  share rather than duplicate.

The current operation-metrics schema is version 2. It has an existing `query`
entry and no `query_many` entry.

## Approaches considered

### Adopted: fixed Python coordinator over raw `Connection.query()`

A focused Python coordinator owns the sync/async producer, a fixed worker
set, bounded queues, output ordering and terminal supervision. Every worker
calls the existing raw `Connection.query()` independently.

Advantages:

- the verified Rust query path remains the single owner of SQL lifecycle,
  deadlines, typed conversion, metrics and connection disposition;
- each unit of SQL concurrency obtains its own ordinary pool lease;
- Python can drive arbitrary sync and async producers without Rust retaining
  Python iterator state across TDS awaits;
- cancellation of a child query keeps the existing fail-closed
  `PooledOperationGuard` behavior;
- no aggregate metric or duplicate SQL state machine is invented;
- the initial implementation is additive and does not require a Rust runtime
  change.

This design does not prohibit a later internal native optimization, but such
an optimization must preserve the public and observable contract below.

### Rejected: Rust-native query-many coordinator

A Rust coordinator could reduce Python task/queue overhead, but it would have
to own Python `iter()`/`aiter()` calls, GIL boundaries, output ordering,
consumer abandonment and TDS cancellation in one new native state machine.
It would duplicate already verified `Connection.query()` behavior and create
more cancellation boundaries where database progress could outlive Python
ownership. That complexity is not justified for the first correct API.

### Rejected: eager materialization or one task per parameter set

`list(parameter_sets)`, `asyncio.gather()` over the complete source and a task
per item are incompatible with the 99,999-operation target. They make input,
task and result memory proportional to total input and cannot provide
producer backpressure. A semaphore around an already-created task per item
does not fix task-memory growth and is also rejected.

## Public API

### Connection surface

`query_many()` is a regular method that validates and returns an async
iterator; callers do not await the method itself:

```python
results = connection.query_many(
    sql,
    parameter_sets,
    concurrency=10,
    ordered=True,
)

async for result in results:
    ...
```

Conceptual typing:

```python
ParameterSet = list[object] | Parameters
ParameterSetSource = (
    list[ParameterSet]
    | Iterable[ParameterSet]
    | AsyncIterable[ParameterSet]
)

def query_many(
    sql: str,
    parameter_sets: ParameterSetSource,
    *,
    concurrency: int = 10,
    ordered: bool = True,
) -> QueryManyIterator: ...
```

Each yielded value is the existing buffered-first-result `QueryStream`.
Callers needing every result set must start independent `stream()` calls;
`query_many()` does not silently change compatibility query semantics.

### Iterator surface and deterministic early exit

`QueryManyIterator` is a public, single-consumer, single-use asynchronous
iterator with:

```python
class QueryManyIterator(AsyncIterator[QueryStream]):
    def __aiter__(self) -> QueryManyIterator: ...
    async def __anext__(self) -> QueryStream: ...
    async def aclose(self) -> None: ...
    async def __aenter__(self) -> QueryManyIterator: ...
    async def __aexit__(self, exc_type, exc, traceback) -> bool: ...
```

The canonical form for a consumer that may stop early is:

```python
async with connection.query_many(sql, parameter_sets) as results:
    async for result in results:
        if no_more_results_are_needed(result):
            break
```

Python does not synchronously notify a general async iterator merely because
an `async for` body executes `break`. Therefore:

- full direct iteration cleans up automatically at exhaustion;
- cancellation while awaiting `__anext__()` cleans up before re-raising;
- `async with` awaits cleanup on early `break` or a body exception;
- without `async with`, an early-stopping caller must execute
  `await results.aclose()` in `finally`;
- dropping an active iterator triggers only a supervised best-effort
  fail-closed fallback and is not a deterministic resource-management API.

`aclose()` is idempotent. Concurrent `__anext__()` calls are rejected with a
`RuntimeError`; multiple consumers cannot divide one iterator. The iterator
binds to the event loop of its first asynchronous use and rejects later use
from another loop.

### Deliberately absent surfaces

- The public and raw `Transaction` classes do not expose `query_many()`.
  Concurrent commands cannot share one transaction session safely.
- The raw PyO3 `Connection` does not gain a second query-many state machine;
  the public wrapper coordinates existing raw `query()` calls.
- There is no eager `list[QueryStream]` convenience method.

## Validation order and producer contract

The synchronous `Connection.query_many()` call completes these checks before
any pool, lifecycle, SQL or metric activity:

1. `sql` must be a Python string;
2. `concurrency` must be an exact integer other than `bool` in
   `1..=10_000`;
3. `ordered` must be exactly `bool`;
4. the top-level source must not be a string, bytes-like object or mapping;
5. exactly one producer protocol is acquired, preferring async when a source
   advertises both async and sync iteration.

Producer protocol acquisition may execute caller `__iter__()` or
`__aiter__()` code, so it happens only after all scalar arguments and
top-level rejected types have passed.

Each pulled item must be exactly one Python `list` or FastMssql `Parameters`
object. `Parameters` must be positional, and every set independently respects
the current 2,098-user-parameter limit. Tuples, mappings, strings, bytes-like
objects, `None` and arbitrary truthy substitutes are rejected rather than
reinterpreted.

The parameter-set validation is extracted into
`python/fastmssql/_parameter_sets.py` and shared by `execute_many()` and
`query_many()`. The helper accepts the operation label needed for a precise
error message. The extraction must preserve every existing execute-many error
class, message and ordering.

For a concrete outer list, the iterator captures its initial length and
rejects detectable resizing at pull boundaries. A pulled parameter set may
wait in the bounded window before raw conversion. Callers therefore must not
mutate the outer source, an accepted list or `Parameters` object until that
item has completed or the iterator has terminated.

Failures from producer acquisition preserve the caller exception. Failures
from later `next()`/`anext()` calls terminate the iterator and identify the
zero-based index that would have been assigned to the next item.

## Empty input

An empty concrete list, sync iterable or async iterable produces no values
and reaches `StopAsyncIteration`.

It executes:

- zero SQL;
- zero query operations and zero query metrics;
- zero pool creation and zero leases;
- no normal-exhaustion `close()` or `aclose()` call on the producer.

A SQL-free `pool_stats()` read used to determine the capacity is permitted
internally and must not change those guarantees. Invalid arguments do not
advance the producer at all.

## Bounded concurrency and backpressure

### Effective concurrency

At first asynchronous use, the coordinator reads the configured pool maximum
through the SQL-free raw `pool_stats()` surface:

```text
effective_concurrency = min(requested_concurrency, pool.max_size)
```

The configured value is an upper bound, not a request to bypass the pool.
Other application work may consume pool capacity, so some workers can wait
under the existing acquire timeout. `query_many()` neither creates a private
pool nor reserves the full pool in advance.

### Fixed topology

The coordinator lives in `python/fastmssql/_query_many.py`; the public wrapper
and typing surface remain in `python/fastmssql/__init__.py` and
`python/fastmssql/__init__.pyi`.

One active iterator owns:

- one producer task;
- exactly `effective_concurrency` worker tasks;
- one bounded input queue;
- one bounded completion queue;
- one capacity semaphore with
  `effective_concurrency` permits;
- for ordered mode only, one index-keyed pending-result map.

It never creates a task per parameter set.

Before advancing the producer, the producer task acquires one capacity
permit. That permit follows the accepted item through:

```text
producer pull
  -> input queue
  -> one raw query
  -> completion queue / ordered pending map
  -> yielded or discarded terminally
```

The permit is released only when the library yields that result or discards
the item during terminal cleanup. If the producer reports normal exhaustion
without an item, the provisional permit is released immediately.

Both queues are independently bounded by `effective_concurrency`, but the
shared permit is the stronger invariant:

```text
accepted but not yet yielded/discarded items <= effective_concurrency
active raw query operations <= effective_concurrency
library-owned completed QueryStream objects <= effective_concurrency
physical pool sessions <= pool.max_size
```

This prevents an input queue, worker set and output queue from each retaining
a separate full window. A slow consumer backpressures completed workers and
ultimately the input producer.

### Worker behavior

Each worker:

1. receives one indexed, preflighted parameter set;
2. rechecks its shape immediately before raw conversion;
3. awaits `raw_connection.query(sql, parameter_set)`;
4. submits either the returned `QueryStream` or the original exception to the
   shared terminal supervisor;
5. receives another item only while the iterator remains active.

No worker retries SQL. Each successful or failed child query follows the
existing raw query deadline and disposition behavior.

## Ordering

For `ordered=True`, completions enter the bounded queue with their zero-based
input index. The consumer stores out-of-order successes in the pending map
and yields only the next expected index. The shared capacity permit ensures
that the completion queue plus pending map cannot grow beyond the effective
window.

For `ordered=False`, each success is yielded in observed completion order.
Indices remain internal unless an exception needs `query_index`.

If a terminal failure is registered, no additional queued or pending success
is yielded. Successfully yielded earlier values remain valid, but unyielded
results are discarded during cleanup. A later-index failure in ordered mode
does not wait indefinitely for earlier items before terminating.

## Failure and cancellation semantics

The first producer, validation, conversion, acquire, SQL, timeout or worker
failure registered by the coordinator becomes the primary terminal error.
Registration is single-assignment; later failures are cleanup observations,
not competing primary results.

Before the primary error is re-raised, the coordinator:

1. atomically stops new producer and worker work;
2. cancels the producer task and all outstanding workers;
3. awaits those tasks so no `anext()` call remains active;
4. relies on the existing dropped-query guard to retire incomplete physical
   connections;
5. closes the now-quiescent producer abnormally through `aclose()` or
   `close()` when
   available;
6. discards queued and pending results;
7. reconciles all capacity permits and consumes all task exceptions.

Mandatory cleanup runs in one supervised task and is awaited through
`asyncio.shield()` until complete, even if the consumer is cancelled again.
No task exception may be left for the event loop as “never retrieved”.

The underlying primary exception object and traceback are preserved.
FastMssql attaches:

```text
query_index: int
```

where the exception object permits attributes. It does not attach parameter
values, producer representations, SQL text or credentials. Existing
parameter-level metadata such as `parameter_index` remains unchanged.

If cleanup also fails, the primary error remains primary and the first
cleanup failure is chained as its cause. When `__aexit__()` receives a body
exception, that body exception remains primary; a cleanup failure is chained
without suppression. When explicit `aclose()` has no existing primary
exception, a cleanup failure is raised.

Consumer cancellation during `__anext__()` is the primary
`CancelledError`; cleanup completes before that cancellation is re-raised.
Explicit `aclose()` performs the same terminal cleanup without manufacturing
a `CancelledError`.

The best-effort drop fallback may synchronously request task cancellation and
schedule supervised cleanup only when its original loop is still running.
It must never claim that asynchronous cleanup was awaited.

## Lifecycle, deadlines and connection disposition

Every child raw query independently uses the existing:

- lifecycle admission generation;
- pool acquisition timeout;
- operation timeout;
- typed-parameter conversion;
- physical connection reset and checkout validation;
- timeout/cancellation/error disposition;
- security-context retirement.

There is no aggregate query-many operation timeout in this API. Time spent
waiting for a producer or a consumer is not incorrectly charged to an
individual SQL query. Applications needing one deadline around the complete
iteration use `asyncio.timeout()` around the canonical `async with`; its
cancellation enters the deterministic cleanup path.

If `disconnect()` moves the connection to `Closing`, already admitted child
queries follow existing drain semantics. Workers that try to admit a later
query are rejected by the lifecycle and terminate the iterator. Force
shutdown and `KILL SPID` must leave no leaked capacity or reusable uncertain
connection, and a subsequent connection/pool smoke query must recover.

Each child security-context-changing query preserves its own successful
result and metric but retires that physical session. Later items must acquire
clean replacement sessions as needed.

## Metrics and observability

There is intentionally no aggregate `query_many` operation key.

- every raw child query increments the existing `query` operation exactly
  once;
- producer waiting, queue waiting and iterator cleanup create no fabricated
  query metric;
- an empty source creates no query metric;
- the operation-metrics schema remains exactly version 2;
- `operation_stats()` gains no key and no cardinality;
- pool counters continue to describe ordinary independent lease activity.

The stress harness reports coordinator throughput and latency externally.
Those measurements are local test evidence, not a new runtime metrics API.

## Memory contract and non-claims

The coordinator bounds the number of library-owned items and completed
`QueryStream` objects. It cannot bound the bytes in one compatibility query
result because raw `query()` buffers its first result set before returning.
Therefore peak driver-owned result memory is conceptually:

```text
effective_concurrency * size of one buffered first result set
```

plus fixed task/queue overhead.

Callers with large individual result sets must use independently supervised
`stream()` calls and choose their own concurrency. Values already yielded to
and retained by caller code are also outside the iterator's ownership bound.
This feature is not advertised as byte-level or LOB streaming.

## Deterministic contract matrix

The SQL-auth matrix gains `QMANY-001` through `QMANY-013`:

| ID | Contract |
|---|---|
| `QMANY-001` | wrapper/stub surface, lazy iterator type, no raw or Transaction concurrent surface |
| `QMANY-002` | scalar/source validation order, one protocol acquisition, zero pull/pool/SQL/metric on invalid input |
| `QMANY-003` | empty list/sync/async sources with zero SQL, pool creation and query metrics |
| `QMANY-004` | list/sync/async typed positional parameters and exact `QueryStream` results |
| `QMANY-005` | requested concurrency, pool maximum, total accepted window, active query and session bounds |
| `QMANY-006` | deterministic input-order delivery with bounded reordering |
| `QMANY-007` | deterministic completion-order delivery without loss or duplication |
| `QMANY-008` | producer, validation, conversion and SQL first-error propagation with privacy-safe `query_index` |
| `QMANY-009` | full exhaustion, explicit close, async-context early break, consumer cancellation and supervised drop fallback |
| `QMANY-010` | operation timeout and `KILL SPID` retirement/recovery with no leaked task or session |
| `QMANY-011` | graceful/forced disconnect interaction and rejection of new work in `Closing` |
| `QMANY-012` | one existing query metric per started child, schema 2 unchanged, security-context retirement/replacement |
| `QMANY-013` | required and extended bounded stress, installed-wheel import and post-load recovery |

Offline coordinator tests use controlled futures and barriers rather than
timing guesses. They prove exact pull counts, task count, output order,
single-assignment failures, close counts and absence of unhandled task
exceptions.

Real SQL Server tests cover parameterized SELECTs, joins and CTEs, empty
results, stored procedures invoked through parameterized SQL, concurrent
SPIDs, deliberate SQL errors, timeout, cancellation, security-context SQL,
`KILL SPID`, disconnect and recovery. Existing CRUD, DDL, RPC and bulk suites
remain cumulative regression gates rather than being duplicated under every
query-many ID.

## Load profiles and hard gates

The dedicated stress tool drives the library iterator itself and never
creates one harness task per operation.

Required profiles:

| Operations | Source | Ordered | Requested concurrency | Pool max |
|---:|---|---:|---:|---:|
| 1,000 | sync | yes | 10 | 10 |
| 1,000 | async | no | 25 | 8 |
| 10,000 | sync | no | 50 | 16 |
| 10,000 | async | yes | 100 | 16 |

Extended explicitly approved profiles:

| Operations | Source | Ordered | Requested concurrency | Pool max |
|---:|---|---:|---:|---:|
| 99,999 | sync | yes | 200 | 32 |
| 99,999 | async | no | 500 | 32 |

Every profile must prove:

- exact producer pulls, yielded IDs and result values;
- exact ordered output or exact completion-order permutation as configured;
- no duplicate or missing operation;
- maximum accepted-but-unconsumed items and active raw queries no greater
  than effective concurrency;
- active pool connections and SQL application sessions no greater than pool
  maximum;
- existing `query.started` and terminal totals reconcile with the number of
  child queries actually started;
- no `query_many` metric and operation schema exactly 2;
- zero unexpected error, timeout or unhandled task exception;
- maximum event-loop scheduling gap no greater than 100 ms;
- RSS growth no greater than 128 MiB for the bounded one-row result profile;
- successful post-load smoke query;
- zero application sessions after deterministic teardown.

Throughput and p50/p95/p99 completion latency are reported for the local
environment but are not portable acceptance thresholds.

## Build, wheel and compatibility gates

The cumulative candidate must pass on one exact clean SHA:

1. focused offline and SQL-auth `QMANY` tests;
2. the generated mandatory SQL-auth matrix with no missing ID;
3. all strict SQL-auth, true-async, framework, resilience and load suites;
4. original upstream-compatible local regression tests;
5. FastMssql and vendored-Tiberius Rust tests, formatting and Clippy with
   warnings denied;
6. Ruff, compileall, stub checks and `git diff --check`;
7. RustSec with zero vulnerability and warning;
8. the required and extended query-many stress profiles;
9. an installed ABI3 wheel imported outside the source tree, with offline,
   SQL-auth and FastAPI/Flask smoke coverage;
10. Linux, macOS and Windows hosted gates when the exact branch SHA is
    eligible; absent runs remain reported as `NOT RUN`;
11. code-review-graph rebuild, change detection, affected flows and explicit
    reconciliation of dynamic Python/PyO3 coverage.

## Branch and commit topology

Design and implementation remain separately reviewable:

```text
docs/query-many-design
  -> test/query-many-bounded-concurrency
  -> feat/query-many-bounded-concurrency
  -> verify/batch-bulk-merge
  -> docs/batch-bulk-status
```

Expected commits:

```text
docs: design bounded query many
docs: plan bounded query many
test: require bounded query many concurrency
feat: add bounded query many
docs: record batch bulk validation evidence
docs: record batch bulk validation status
```

If RED or self-review reveals an independent pre-existing FastMssql defect,
its reproduction and remediation use a separate `test/...` then `fix/...`
pair before ancestry is merged into the feature. Nothing is pushed to the
original repository.

## Acceptance

This slice is complete only when:

- every focused contract is first committed RED against the exact design
  base and then passes on the feature branch;
- fixed workers and one shared capacity window are proven, not inferred from
  a semaphore around unbounded tasks;
- ordered and completion-order outputs are exact;
- errors, cancellation, explicit close and async-context early exit await
  terminal cleanup without swallowed exceptions;
- bare `async for ... break` is documented truthfully rather than claimed to
  synchronously close the iterator;
- per-query deadlines, metrics and fail-closed disposition remain those of
  the existing raw query path;
- no Transaction concurrent surface or aggregate metric is added;
- real SQL Server, fault, 99,999-operation stress, installed-wheel and graph
  evidence all pass on the recorded exact SHA;
- the live production-readiness audit records exact ancestry, commands,
  results, hosted truth and residual risks.

Passing this slice closes the seven-part batch/bulk feature. It does not by
itself close named-instance support, the production server/wheel framework
matrix, TDS 8, TVP, Always Encrypted or the other explicitly listed P2
capabilities.
