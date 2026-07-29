# FastMssql Bounded `execute_many()` Design

**Status:** approved by the repository owner's standing authorization for
future design/specification/plan files and inline implementations, subject to
self-review and the branch/TDD gates in this document.

**Base SHA:** `a36525834011db0f5707bfcac5bda195fd498fe5`

**Repository boundary:** every branch, commit, Docker artefact and hosted
check belongs only to `galeamarcel/FastMssql`. The
`Rivendael/FastMssql` remote remains fetch-only and its push URL must stay
exactly `DISABLED`.

## Goal

Add a production-safe `execute_many()` API that repeats one SQL statement
over bounded synchronous or asynchronous positional parameter sets without
ODBC, thread offload, eager whole-input conversion or one task per item.

The feature must provide:

- exact input-order execution;
- one checked total affected-row count;
- atomic-by-default Connection semantics;
- explicit chunk-commit partial-success semantics;
- ordinary settlement-neutral caller Transaction semantics with fail-closed
  security-context retirement;
- one absolute public deadline;
- one operation metric for the complete call;
- typed parameter conversion shared with `execute()`;
- deterministic cancellation, timeout and drop cleanup;
- privacy-safe, global failure and confirmed-commit metadata;
- real SQL Server, stress, installed-wheel and graph evidence.

This is the sixth of seven approved batch/bulk slices. It does not implement
bounded-concurrency `query_many()`.

## Scope boundaries

This slice does not add:

- heterogeneous SQL per item; `execute_batch()` remains that API;
- result-row streaming; `execute_many()` returns only affected rows;
- concurrent execution on one Transaction or one TDS session;
- server-side `sp_prepare`/prepared-handle caching;
- transparent retry before or after wire activity;
- named parameters;
- byte-level streaming inside one parameter or LOB;
- table-valued parameters, SQL_VARIANT, spatial, hierarchyid, CLR UDT or
  legacy LOB support;
- a release, published wheel, Tiberius fork or original-repository PR.

`query_many()` remains a separate later slice because it owns multiple pool
leases, output ordering and early-consumer cleanup rather than one sequential
transaction.

## Approaches considered

### Adopted: shared bounded Python coordinator over a private Rust sequence

Python owns `iter()`/`aiter()`, producer close, bounded chunk assembly and
exception chaining. A private Rust `_ExecuteManySequence` owns lifecycle
admission, the physical lease, transaction state, deadlines, metrics,
parameter conversion, SQL execution, settlement and connection disposition.

The existing native-bulk coordinator is extracted without semantic changes
into a focused shared bounded-sequence helper. Native bulk and execute-many
provide only their item normalizer, error labels and private sequence.

Advantages:

- the already-audited cancellation/reservation handoff is not copied and
  allowed to diverge;
- Python never decides whether a TDS connection can be reused;
- Rust never drives arbitrary Python iterator code while a TDS request is
  active;
- sync/async/list inputs share one observable contract;
- a later internal optimization does not change the public API.

### Rejected: loop over public `Transaction.execute()` in Python

This would restart the operation deadline per item, increment `execute`
metrics for internal work, create public begin/commit/rollback metrics, and
make `atomic=False` commit acknowledgement and cleanup ownership ambiguous.
It would also repeat validation and state transitions at the wrong boundary.

### Rejected: consume arbitrary Python iterators directly inside Rust

This couples GIL acquisition, `__next__`/`__anext__`, Python exception
semantics and TDS request ownership in one native state machine. Cancellation
would have more await boundaries where database progress could outlive Python
ownership. The reduced call overhead does not justify that correctness risk.

## Public API

### Wrapper `Connection`

```python
await connection.execute_many(
    sql,
    parameter_sets,
    *,
    atomic=True,
    chunk_size=1000,
)
```

Conceptual typing:

```python
type ParameterSet = list[object] | Parameters
type ParameterSetSource = (
    list[ParameterSet]
    | Iterable[ParameterSet]
    | AsyncIterable[ParameterSet]
)

async def execute_many(
    sql: str,
    parameter_sets: ParameterSetSource,
    *,
    atomic: bool = True,
    chunk_size: int = 1000,
) -> int: ...
```

### Wrapper `Transaction`

```python
await transaction.execute_many(
    sql,
    parameter_sets,
    *,
    chunk_size=1000,
)
```

The Transaction form deliberately has no `atomic` argument. It requires an
active caller-owned transaction and never commits or rolls it back.

### Raw extension

The raw PyO3 `Connection` and `Transaction` classes expose the same names for
concrete Python lists only:

```python
await raw_connection.execute_many(
    sql,
    parameter_sets_list,
    atomic=True,
    chunk_size=1000,
)

await raw_transaction.execute_many(
    sql,
    parameter_sets_list,
    chunk_size=1000,
)
```

Only the public Python wrappers widen the input to sync/async iterables.
Concrete lists use the raw Rust list adapter and do not pass through Python
iterator coordination.

## Validation order and input contract

Before acquiring or advancing caller producer code:

1. `sql` must extract as `str`;
2. `atomic` must be exactly `bool`, not an integer or arbitrary truthy object;
3. `chunk_size` must be an integer other than `bool` in `1..=10_000`;
4. the top-level source must not be `str`, bytes-like or a mapping;
5. one preferred protocol is acquired once: async when both async and sync
   are present.

Each parameter set must be a Python `list` or FastMssql `Parameters` object,
matching the existing `execute()` positional contract. Named `Parameters`
remain unsupported. `None`, tuples, mappings, strings and bytes-like values
are not silently reinterpreted.

Every set independently respects the current 2,098-user-parameter limit and
the closed `Parameter` descriptor conversion rules. Validation and conversion
never interpolate a value into `sql`.

The concrete-list adapter owns the outer list for the lifetime of the
awaitable, captures its initial length and rejects detectable resizing at
every chunk boundary. Bounded operation means values are intentionally
converted later; callers must not mutate the outer list, an unconsumed
parameter set or a yielded `Parameters` object until the awaitable completes.

Invalid public arguments consume zero producer items and perform no
lifecycle, pool, SQL or operation-metric activity.

## Empty input

A successfully empty list, sync iterable or async iterable returns `0`.

For `Connection`, empty input creates no pool, lease, SQL transaction or
operation metric. For an active `Transaction`, the private reservation is
released back to `Active` without SQL or metric activity. Normal exhaustion
does not trigger an additional `close()`/`aclose()` call; abnormal termination
closes the producer when it exposes the matching method.

## Backpressure

The shared coordinator:

- obtains the producer protocol exactly once;
- reserves the Rust sequence before the first pull;
- owns at most `chunk_size` parameter-set objects;
- activates the sequence after the first item;
- awaits the complete Rust `push()` for a chunk;
- drops the chunk;
- only then requests the next item.

It never calls `list(parameter_sets)`, creates a task per set, uses an
unbounded queue or retains completed chunks.

Synchronous `next()` runs on the Python event-loop thread. FastMssql checks
the absolute deadline around every synchronous pull and normalization
boundary, but cannot preempt arbitrary blocking Python code. A blocking
synchronous producer is outside the true-async contract and must be exposed
as an async iterable.

## SQL execution semantics

The same `sql` string is used for every set in input order. Every set is
converted through `convert_parameters_to_fast()` and executed sequentially
on one physical TDS session.

The engine preserves ordinary `execute()` behavior:

- parameterized statements use Tiberius `execute()` and fully consume the
  returned affected-count tokens;
- an empty parameter set plus scope-sensitive DDL uses the existing direct
  batch helper;
- no internal statement calls the public `execute()` method;
- no statement is retried;
- affected counts are summed with checked `u64` arithmetic and returned as a
  Python integer;
- overflow is a typed local error and follows the current transaction mode's
  rollback rules.

One chunk is a conversion/backpressure boundary in every mode. It is also a
commit boundary only for `Connection(atomic=False)`.

## Settlement modes

### `Connection`, `atomic=True`

- one lifecycle permit and one pooled physical lease cover the call;
- one SQL transaction begins before the first statement;
- all chunks execute inside that transaction;
- success commits once and returns the checked total;
- producer, conversion, SQL or count failure rolls back every chunk;
- COMMIT acknowledgement loss is `CommitOutcomeUnknown`;
- no rollback or retry follows an unknown COMMIT outcome.

### `Connection`, `atomic=False`

- one lifecycle permit and one pooled physical lease still cover the call;
- every non-empty chunk owns `BEGIN` → sequential execute → `COMMIT`;
- `push()` returns only after the chunk COMMIT is acknowledged;
- a later producer/conversion/SQL failure preserves earlier acknowledged
  chunk commits and rolls back only the current chunk;
- a partially filled chunk is not sent when the producer fails before
  `push()`;
- acknowledgement loss for a chunk COMMIT stops the call immediately with
  `CommitOutcomeUnknown`; no later chunk starts.

This is explicit partial-success behavior. It is not equivalent to
autocommit per parameter set.

### Active caller `Transaction`

- reservation occurs before the first producer pull;
- no Connection-owned `BEGIN`, `COMMIT` or `ROLLBACK` is issued;
- success returns the affected total and restores the caller state to
  `Active`;
- a producer failure before any statement restores `Active`;
- any error after a statement may have reached the wire makes the caller
  transaction rollback-only without settling it;
- COMMIT and new data operations are then rejected, while caller
  `rollback()`/`close()` remain the recovery paths.

Concurrent public operations cannot enter while the private state is
`ExecuteManyProducing`.

The normal-success rule has one existing fail-closed security exception. If
`requires_connection_retirement(sql)` classifies the statement, successful
protocol completion still returns the affected total and records a successful
operation, but the physical session is retired and the caller Transaction
becomes `Failed`. Later data operations and settlement are rejected, while
`close()` remains idempotent. This matches the ResultStream security policy
and prevents an impersonated session from returning to the pool. The affected
count is not a durability claim for the caller's still-uncommitted transaction,
which SQL Server rolls back when the transport closes.

## Error metadata

The original exception class and message remain primary. FastMssql does not
wrap a `SqlError`, `ConversionError`, `OperationTimeoutError`,
`CommitOutcomeUnknown`, producer exception or `CancelledError` in a generic
execute-many exception.

When an execute-many call fails, the primary exception receives:

```text
parameter_set_index: int
confirmed_committed_parameter_sets: int
partial_commit_possible: bool
```

The Python task owns producer and `CancelledError` objects, while Rust alone
knows which statement was active and which chunk COMMIT acknowledgements were
received. The private sequence therefore returns this privacy-safe snapshot
from every successful `abort("error" | "cancelled")` cleanup:

```text
active_parameter_set_index: int | None
confirmed_committed_parameter_sets: int
partial_commit_possible: bool
```

The execute-many adapter merges that authoritative Rust snapshot with its
Python-side next/pulled index before attaching the three public attributes.
When a statement was active, Rust's index wins. During producer wait or
normalization, the Python current index wins. An abort cleanup failure cannot
replace the primary exception; the adapter attaches conservative metadata
from its last confirmed Python/Rust progress and chains the cleanup failure.

Semantics:

- `parameter_set_index` is the zero-based set whose production, conversion or
  execution failed; while requesting the next producer item it is that next
  expected index;
- during cancellation/timeout in an active statement it is the exact active
  index retained by the Rust sequence;
- on `CommitOutcomeUnknown`, where no individual statement failure is known,
  it is the first set in the commit whose durability is unconfirmed: `0` for
  `atomic=True` and the current chunk's global start index for
  `atomic=False`;
- `confirmed_committed_parameter_sets` counts only parameter sets whose
  Connection-owned chunk COMMIT acknowledgement was received;
- it is always `0` for `atomic=True` and caller-owned Transaction failures;
- `partial_commit_possible` is true only for `atomic=False` when a prior
  commit was confirmed or the current chunk COMMIT outcome is unknown.

Existing metadata such as `parameter_index`, `sql_type`, `reason`,
`wire_sent`, `connection_discarded`, `retryable` and `outcome_unknown` is
preserved. `parameter_index` remains local to the failing parameter set;
`parameter_set_index` is global across chunks.

Driver-added messages and metadata never include `sql`, parameter values,
table names derived from the statement or producer representations. SQL
Server's own error text remains unchanged.

If rollback, retirement or producer close also fails, the original exception
remains primary and the first cleanup failure is attached as `__cause__`.
`BaseException`, including cancellation, is never swallowed.

## Deadline, cancellation and disposition

Sequence construction captures one operation deadline before producer
acquisition. The same absolute deadline covers:

- producer pulls;
- bounded normalization/conversion;
- lifecycle admission and pool acquisition;
- every statement;
- Connection-owned BEGIN/COMMIT;
- final settlement.

For a caller Transaction, its lifetime deadline participates and the earliest
absolute deadline wins. Rollback cleanup uses the existing independent
rollback timeout.

Cancellation rules:

- before any wire activity: release the reservation and reuse synchronized
  state;
- while a Connection-owned transaction is synchronized: rollback before
  release;
- while a request or commit is protocol-uncertain: retire the physical
  connection fail-closed;
- on caller Transaction after possible wire activity: mark rollback-only or
  fail the session when synchronization is unknown;
- stop all later producer pulls;
- await sequence abort/expire and producer close despite repeated task
  cancellation;
- re-raise the original cancellation only after terminal cleanup.

As with native bulk, Python assumes cleanup ownership before awaiting
`reserve()`. Rust abort/expire waits for a cancelled PyO3 operation to release
the private sequence lock. A Rust drop fallback supervises abandonment if
Python itself is destroyed before explicit cleanup.

## Operation metrics schema 2

This feature intentionally evolves the fixed operation snapshot from schema
version 1 to version 2.

The exact operation order becomes:

```text
connect
ping
query
simple_query
execute
execute_many
query_batch
execute_batch
bulk_insert
begin
commit
rollback
close
disconnect
```

Both public Connection and Transaction forms record exactly one
`execute_many` operation after the first input item. Empty inputs record
nothing. Internal statements and settlement do not increment `execute`,
`begin`, `commit` or `rollback`.

The one observer starts before conversion of the first item and finishes only
after commit/rollback/retirement has reached terminal state. It classifies
exactly one outcome: succeeded, errors, timed_out, cancelled or
outcome_unknown.

All Python stubs, operation-metric assertion helpers, stress validators,
README schema text and installed-wheel tests move to `Literal[2]` and the
14-key operation map. Historical evidence documents remain immutable.

## Components and files

### Shared Python bounded coordinator

Create `python/fastmssql/_bounded_sequence.py` with:

- one preferred sync/async protocol acquisition;
- absolute-deadline-aware pulls;
- one-chunk ownership;
- cleanup shielding and producer close;
- pre-await reservation ownership;
- feature callbacks for item normalization and failure metadata.

Refactor `python/fastmssql/_bulk_iterable.py` into a native-bulk adapter over
that helper without changing its public behavior or task names.

Create `python/fastmssql/_execute_many.py` for:

- top-level source rejection;
- execute-many parameter-set normalization;
- partial-commit metadata attachment;
- construction and driving of `_ExecuteManySequence`.

### Rust execution engine

Create `src/execute_many.rs` for:

- strict `atomic`/`chunk_size` validation;
- bounded concrete-list chunk conversion;
- global `parameter_set_index` attachment;
- ordinary execute/direct-batch parity;
- checked per-item/chunk/total affected counts;
- current-chunk transaction helpers and error metadata.

Create `src/execute_many_sequence.rs` for:

- progress and terminal state;
- Connection atomic/chunk-commit ownership;
- active Transaction reservation;
- absolute deadline and one observer;
- push/finish/abort/expire/drop cleanup, with `abort()` returning the terminal
  privacy-safe progress snapshot needed to annotate a Python-owned primary
  exception;
- the private PyO3 `_ExecuteManySequence`.

`src/connection.rs` and `src/transaction.rs` expose raw concrete-list
adapters plus private sequence factories. `src/transaction.rs` adds only the
new exclusive `ExecuteManyProducing` state and focused reservation methods;
it does not generalize or rename the already-verified native-bulk state.

### Public surface and packaging

Update:

- `python/fastmssql/__init__.py`;
- `python/fastmssql/__init__.pyi`;
- `python/fastmssql/fastmssql.pyi`;
- `src/lib.rs`;
- `README.md`;
- `VERSION.md`.

The wheel must contain both private Python helpers, both stubs, the ABI3
extension and `py.typed`.

## Deterministic offline contracts

The immutable RED branch requires:

- exact wrapper/raw/stub signatures and Transaction's missing `atomic`;
- concrete-list raw fast path and sync/async wrapper dispatch;
- strict validation before producer acquisition;
- async preference and one-time protocol acquisition;
- one-chunk backpressure with no task per item or unbounded queue;
- pre-pull Transaction reservation and exclusive state;
- exact global error and confirmed-commit metadata;
- atomic, chunk-commit and caller-transaction state-machine rules;
- absolute deadline reuse and mandatory cleanup shielding;
- metrics schema 2, exact 14-key order and no internal metric inflation;
- unchanged `execute_batch()` and native-bulk contracts;
- source/stub/wheel packaging invariants.

Fake-sequence coordinator tests must deterministically cover cancellation:

1. while waiting for an async producer;
2. after reservation becomes effective but before Python observes it;
3. while a Rust chunk push is pending;
4. during producer close;
5. with a secondary cleanup failure.

Rust unit tests cover state/progress/overflow and settlement decisions without
requiring SQL Server.

## Canonical SQL-auth cases

The canonical matrix grows from 396 to 407 unique cases:

- `EMANY-001`: wrapper/raw/stub surfaces, concrete-list fast path and exact
  signatures;
- `EMANY-002`: invalid atomic/chunk/source/set input performs zero producer,
  pool, SQL and metric activity;
- `EMANY-003`: empty list/sync/async input returns zero with no pool/SQL/
  metric and restores an active Transaction reservation;
- `EMANY-004`: list/sync/async producers preserve order, exact affected total,
  typed parameter conversion and TDS-gated one-chunk backpressure;
- `EMANY-005`: default atomic mode rolls back all earlier chunks after a late
  conversion, constraint or producer failure and reports the exact global
  set index;
- `EMANY-006`: `atomic=False` preserves acknowledged earlier chunks, rolls
  back the failing chunk and reports exact confirmed-commit metadata;
- `EMANY-007`: cancellation during producer wait or SQL stops pulls, reaches
  terminal cleanup, records one cancellation and leaks no session;
- `EMANY-008`: timeout during producer wait or SQL is typed, bounded,
  privacy-safe and leaves the pool reusable;
- `EMANY-009`: ordinary active-Transaction success is settlement-neutral;
  post-wire failure is rollback-only and only caller rollback settles it;
  successful security-context SQL returns its response but retires the
  physical session, fails the Transaction and recovers the pool on a distinct
  `connection_id`;
- `EMANY-010`: INSERT/UPDATE/DELETE/direct EXEC use typed sets and schema-2
  metrics record one `execute_many` with zero internal execute/settlement
  deltas;
- `EMANY-011`: lost atomic or chunk COMMIT acknowledgement produces
  `CommitOutcomeUnknown`, no rollback/retry and truthful partial-commit
  metadata.

Each case has one source test and one canonical runner lane. No skip, xfail or
swallowed exception satisfies a case.

## Stress profiles

Create `scripts/sql_auth/execute_many_stress.py` with required profiles:

```text
1,000 sets, sync producer,  atomic=True,  chunk 100
1,000 sets, async producer, atomic=True,  chunk 100
10,000 sets, sync producer,  atomic=True,  chunk 1,000
10,000 sets, async producer, atomic=True,  chunk 1,000
10,000 sets, sync producer,  atomic=False, chunk 1,000
99,999 sets, sync producer,  atomic=True,  chunk 1,000
99,999 sets, async producer, atomic=True,  chunk 1,000
```

The 99,999 profiles are explicit opt-in. Every profile records:

- exact pulls, executed sets, affected total and persisted rows;
- confirmed chunk commits for partial mode;
- maximum buffered parameter sets/cells;
- RSS baseline/peak/growth;
- event-loop ticks and maximum scheduling gap;
- physical connection identity and maximum SQL sessions;
- operation/pool metric deltas;
- wall duration and local throughput;
- failure type, post-load smoke and teardown sessions.

Initial hard gates:

```text
maximum buffered sets       <= chunk_size
RSS growth                  <= 64 MiB
maximum event-loop gap      <= 0.100 s
errors/timeouts             == 0
execute_many metric delta   == exactly one success
execute metric delta        == 0
post-load smoke             == PASS
remaining app sessions      == 0
```

These measurements characterize the driver and local host, not maximum SQL
Server capacity. A single oversized parameter value/LOB is not bounded by the
set-count gate.

## Build, wheel and graph gates

Before this slice can be `VERIFIED_FORK`:

- focused RED fails only for absent execute-many behavior;
- focused GREEN and `EMANY-001`–`EMANY-011` pass;
- all prior bulk, operation-metrics, transaction and timeout cases pass;
- the full deterministic SQL-auth, async, framework, resilience, load and
  original-local-regression lanes pass;
- FastMssql and vendored-Tiberius Rust tests pass;
- root/vendored `cargo fmt --check` and Clippy warning gates pass;
- Ruff, `compileall`, stub and matrix contracts pass;
- an isolated ABI3 wheel imports only from `site-packages` without
  `PYTHONPATH` and passes offline plus real execute-many tests;
- required and extended stress artefacts pass;
- code-review-graph is rebuilt on the exact feature SHA and impact/test gaps
  receive manual review;
- credential and generated-artefact scans pass;
- hosted Linux/macOS/Windows and RustSec status is reported exactly as PASS,
  FAIL or NOT RUN, never inferred from local evidence.

## Branch and commit lineage

```text
docs/execute-many-design
  -> test/execute-many
  -> feat/execute-many
  -> docs/execute-many-status
```

The RED commit must be an ancestor of the feature branch. History is not
rewritten. Design/plan, test, implementation and status commits remain
separate. Every push targets only
`https://github.com/galeamarcel/FastMssql.git`.

Planned commit roles:

```text
docs: design bounded execute many
docs: plan bounded execute many
test: require bounded parameterized execute many
refactor: share bounded producer coordination
feat: add execute many operation metrics
feat: add stateful execute many sequence
feat: expose bounded execute many
fix: close execute many full-gate regressions
docs: record execute many validation evidence
```

Corrective regressions discovered during execution receive their own
test-first commit and remain visible in history.

## Live-document update

Design and RED branches do not mark the feature complete. Only after every
exact gate passes may `docs/execute-many-status`:

- mark only `execute_many()` `VERIFIED_FORK`;
- update operation-metrics schema 2 as the current public contract;
- retain `query_many()` as the seventh open batch/bulk slice;
- record exact ancestry, counts, hashes, stress, wheel, graph and hosted
  status;
- keep the displayed package version and release state truthful.

## Residual risks

Even after this design passes:

- execution remains sequential on one TDS session by design;
- `atomic=False` intentionally permits acknowledged partial commits;
- one blocking sync producer can block the Python event loop;
- one oversized parameter/LOB can exceed chunk-level memory expectations;
- server plan reuse depends on SQL Server/Tiberius behavior and is not a
  prepared-statement guarantee;
- exact candidate CI must still run on Linux, macOS and Windows;
- `query_many()`, TVP, remaining enterprise types, named instances, TDS 8
  and server-process installed-wheel framework gates remain open;
- any original-repository proposal requires a fresh upstream rebase,
  reproduction, exact hosted evidence and explicit approval.
