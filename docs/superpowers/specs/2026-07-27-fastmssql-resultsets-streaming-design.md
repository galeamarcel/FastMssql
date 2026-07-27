# FastMssql Enterprise Result Sets, Streaming and RPC Results Design

**Status:** approved for inline execution by Marcel Galea's standing
authorization; specification, implementation plan and every implementation
branch require self-review.

**Date:** 27 July 2026

**Source baseline:** `test/sql-auth-validation` at
`88ac9c00d3d80edbb84377fc1f812070b5cf289b`

**Repository boundary:** every design, reproduction, test, fix, feature,
evidence and status branch is created and published only in
`https://github.com/galeamarcel/FastMssql.git`. The original Rivendael
repository is fetch-only and its push URL remains exactly `DISABLED`. The
vendored Tiberius source may be changed inside this FastMssql fork, but no
Tiberius fork, publication or external pull request is authorized by this
design.

## Objective

Replace the current first-result, fully buffered compatibility path with an
additive enterprise API that preserves the complete SQL Server response and
supports true asynchronous, bounded-memory consumption.

The completed subsystem must:

1. preserve every result set in wire order, including empty result sets;
2. make column metadata available before the first row and when a result set
   has zero rows;
3. expose a Python asynchronous iterator whose row payload is bounded by
   explicit backpressure;
4. preserve DONE row counts only when SQL Server marks the count valid;
5. preserve SQL Server informational messages separately from rows and
   errors;
6. expose stored-procedure return status and OUTPUT/INPUT_OUTPUT values;
7. keep the physical pool lease and lifecycle permit until the complete
   response reaches a terminal state;
8. return a normally exhausted connection to the pool through the existing
   reset path;
9. retire the physical connection on early response abandonment,
   cancellation, conversion failure or protocol uncertainty;
10. integrate with pooled and transactional execution without allowing a
    second operation on the same in-flight TDS response;
11. keep the existing buffered `query()` and `QueryStream` behavior
    compatible while documenting it honestly;
12. prove the contract with real Docker SQL Server, SQL authentication,
    deterministic fault cases, installed-wheel tests and bounded load.

This design closes audit item `feat/resultsets-streaming` through several
independently reviewable RED/feature pairs. It does not declare the entire
library production-ready.

## Measured baseline

The current implementation has four distinct limitations:

- `Connection::execute_query_async_gil_free()` and its transaction
  equivalent call `tiberius::QueryStream::into_first_result()`;
- `into_first_result()` first collects the complete response with
  `into_results()`, then discards result sets after index zero;
- `PyQueryStream` stores `Vec<Option<Row>>` and offers synchronous
  `__iter__`, indexing, `len()` and `reset()`;
- non-INPUT parameter directions are rejected locally before execution.

The focused SQL-auth suite on the exact baseline passed 15/15. Two of those
passing cases intentionally encode current limitations:

- `RESULT-014` requires the object to be synchronous and not async iterable;
- `RESULT-015` measures growth after the complete result has already been
  buffered and then converted.

A read-only reproduction against the healthy
`fastmssql-sql-auth-dev` container produced:

```text
multiple_visible_rows= [{'first_value': 11}]
empty_len= 0
empty_columns= ValueError: No column information available
has_aiter= False
has_anext= False
output_direction_reason= unsupported_direction
active_after_buffered_query= 0
```

The two-statement batch returned only the row from its first result set. An
empty typed SELECT lost its column metadata. The result object implemented no
async iteration. An OUTPUT descriptor was rejected. The active pool count was
already zero because the entire server response had been consumed before
Python received the object.

The first attempted baseline run stopped before SQL Server because the
ignored local extension binary predated `OperationMetricsConfig`. The binary
timestamp and missing exported symbol identified the environmental mismatch.
Rebuilding the extension from the exact source baseline made the same 15-test
suite pass. This setup correction is not classified as a result-streaming
runtime defect.

## Protocol and binding evidence

The design is based on current primary sources and the exact vendored code:

- the PyO3 async-iterator protocol permits `__aiter__` to return self and
  `__anext__` to return an awaitable;
- `pyo3-async-runtimes::tokio::future_into_py` converts a `'static` Rust
  future into the Python awaitable;
- Tiberius documents that its `QueryStream` must be polled empty before the
  client begins another request;
- Tiberius currently exposes only `QueryItem::Metadata` and
  `QueryItem::Row`, while its internal token stream already decodes DONE,
  INFO, RETURNSTATUS and RETURNVALUE;
- MS-TDS defines a procedure name as `US_VARCHAR` in an RPC request and the
  `fByRefValue` parameter flag for output parameters;
- MS-TDS defines DONE_COUNT as the validity bit for `DoneRowCount`;
- MS-TDS requires RETURNSTATUS for RPC execution and one RETURNVALUE token
  for each output parameter.

Primary references:

- [PyO3 asynchronous iterator protocol](https://pyo3.rs/main/class/protocols.html)
- [pyo3-async-runtimes](https://github.com/PyO3/pyo3-async-runtimes)
- [Tiberius QueryStream](https://github.com/prisma/tiberius/blob/main/src/tds/stream/query.rs)
- [MS-TDS RPC Request](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/619c43b6-9495-4a58-9e49-a4950db245b3)
- [MS-TDS DONE](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/3c06f110-98bd-4d5b-b836-b1ba66452cb7)
- [MS-TDS RETURNSTATUS](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/c719f199-e71b-4187-90b9-94f78bd1870e)
- [MS-TDS RETURNVALUE](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/7091f6f6-b83d-4ed2-afeb-ba5013dfb18f)

## Approaches considered

### Approach A — owned producer plus bounded channel, selected

A Tokio producer task owns an `OwnedPooledConnection`, the lifecycle permit
and the Tiberius response stream. It sends owned response events through one
bounded `tokio::sync::mpsc` channel. Python-side async iterators consume the
channel and convert one raw row at a time under the GIL.

Advantages:

- no self-referential Rust object and no `unsafe`;
- the Tiberius stream and borrowed client remain in one ordinary async stack;
- channel capacity provides deterministic row backpressure;
- the producer can select between wire progress, lifecycle force and consumer
  cancellation;
- dropping the receiver unblocks a producer waiting on a full channel;
- a physical connection has one owner until EOF or fail-closed retirement.

### Approach B — store Tiberius QueryStream directly in a pyclass

Rejected. `tiberius::QueryStream<'a>` borrows the mutable client which would
also need to live in the same Python object. That is a self-referential
lifetime. Solving it through leaked allocations, transmute, pinned
self-references or an unsafe helper crate would make cancellation and pool
ownership harder to prove.

### Approach C — SQL Server cursor or application pagination

Rejected as the default result API. Server cursors and rewritten paginated
queries change transaction duration, locking, execution plans and supported
SQL shapes. They also do not preserve DONE, INFO, output and return-status
tokens as one original response. Applications may build pagination
separately, but it is not a replacement for TDS backpressure.

## Compatibility boundary and public API

The implementation is additive for the 0.8.0 candidate.

Existing calls remain source-compatible:

```python
buffered = await connection.query(sql, params)
raw_buffered = await connection.simple_query(sql)
```

They continue returning the existing buffered `QueryStream` and continue
selecting the first result set. Their documentation and stubs must stop
claiming async iteration or bounded memory. The class remains available for
indexing, `len()`, `reset()` and synchronous compatibility.

The enterprise response APIs are:

```python
response = await connection.stream(
    sql,
    params=None,
    *,
    buffer_size=64,
)

response = await connection.batch(
    sql,
    *,
    buffer_size=64,
)

response = await connection.callproc(
    procedure,
    params=None,
    *,
    buffer_size=64,
)
```

The same methods are available on a begun `Transaction`.

`stream()` uses the parameterized RPC path and accepts the same positional
parameter forms as `query()`. `batch()` sends one raw SQL batch and accepts no
parameters. `callproc()` sends one direct named RPC rather than constructing
an `EXEC` SQL string.

`query()`, `execute()`, `stream()` and the batch APIs continue accepting only
`INPUT` directions. `callproc()` is the only API in this subsystem that
consumes `OUTPUT`, `INPUT_OUTPUT` or `RETURN_VALUE` descriptors.

`buffer_size` is a plain integer from 1 through 1,024. Python booleans,
floats, negative values, zero and values above the limit are rejected locally
before pool checkout. The default of 64 is fixed and documented.

No existing method silently changes its return type in this subsystem.

## Python result model

### ResultStream

`ResultStream` is an async context manager and an async iterator of
`ResultSet` objects:

```python
response = await connection.stream(sql, params, buffer_size=32)

async with response:
    async for result_set in response:
        print(result_set.index, result_set.columns)
        async for row in result_set:
            process(row)

summary = response.summary
```

The outer iterator yields result sets strictly in wire order. A result set is
yielded as soon as its metadata arrives, including when no row follows.

Only one result set may be active at a time. Calling outer `__anext__` before
the current set reaches EOF or is explicitly discarded raises a stable
`RuntimeError`; it never races two consumers against one TDS response.

Public terminal operations:

```text
await response.aclose()       abort the complete response
await response.finish()       consume/discard through normal EOF
response.closed               terminal local state
response.complete             true only after normal protocol EOF
response.summary              available only after normal EOF
```

`aclose()` is idempotent and waits for producer acknowledgement that the
lease was released or retired. It never reports completion merely because a
cancel signal was queued.

`finish()` drains rows without materializing them, preserving result
metadata, DONE records, messages, return status and output values. It is the
explicit way to obtain a complete summary when the caller does not need every
row.

Accessing `summary` before normal EOF raises `RuntimeError`. An aborted
response has no partial summary masquerading as complete.

### ResultSet

Each `ResultSet` has immutable metadata:

```text
index
columns          tuple[ColumnMetadata, ...]
column_names     tuple[str, ...]
```

`ColumnMetadata` contains:

```text
ordinal
name
type_name
nullable
precision
scale
length
```

Fields not represented by the received TDS metadata are `None`; the driver
does not guess. Metadata is constructed from COLMETADATA, not from the first
row.

`ResultSet` implements:

```text
__aiter__
__anext__
await aclose()     discard this set through its boundary, retain response
closed
```

`ResultSet.aclose()` means “skip remaining rows in this set”, not “abort the
whole response”. It consumes through the next result-set boundary under the
same absolute operation deadline. `ResultStream.aclose()` aborts the complete
response and retires the connection.

There is no `len`, synchronous iterator, index, slice, reset or replay on
`ResultSet`. Those operations require buffering and remain only on the legacy
`QueryStream`.

### ResultSummary

After normal EOF, `ResultStream.summary` is an immutable
`ResultSummary` containing:

```text
result_set_count
done                  tuple[DoneResult, ...]
messages              tuple[SqlMessage, ...]
return_status         int | None
output_parameters     mapping[str | int, object]
```

The `output_parameters` property returns a fresh dictionary snapshot on each
access, so mutating it cannot change the internally immutable summary.

`DoneResult` preserves:

```text
kind                  DONE | DONEPROC | DONEINPROC
rows_affected         int | None
more_results          bool
in_transaction        bool
attention_acknowledged bool
```

`rows_affected` is `None` unless DONE_COUNT is set. A valid zero remains
integer zero and is not confused with an absent count.

`SqlMessage` preserves number, state, severity, message, server, procedure and
line for the caller. Messages are returned as data and are never logged
automatically.

The initial implementation enforces fixed safety limits of 4,096 DONE
records, 1,024 informational messages and 1,024 result sets per response.
Exceeding a limit raises `ProtocolError` with the stable safe reason
`response_limit_exceeded` and retires the connection. These limits bound
non-row response metadata without adding a second configuration object. They
may become configurable only with measured evidence.

## Producer and backpressure architecture

The producer owns:

```text
OwnedPooledConnection or owned Transaction session guard
lifecycle OperationPermit
operation deadline
Tiberius ResponseStream
bounded Sender<ResponseEvent>
cancellation receiver
terminal acknowledgement sender
```

The Python `ResultStream` owns:

```text
bounded Receiver<ResponseEvent>
cancellation sender
terminal acknowledgement receiver
single-consumer state
current ResultSet state
terminal summary or terminal error
```

The channel holds both control events and rows. At most `buffer_size` pending
events exist, so a fast SQL Server cannot cause an unbounded `Vec<Row>`.
Tiberius decodes only the next token requested by the producer. Python
conversion occurs only after a row leaves the bounded channel.

The producer state machine is:

```text
Created
  -> Running
     -> Completed       complete protocol EOF; lease NeedsReset
     -> Failed          typed SQL/protocol/I/O error disposition
     -> Cancelled       Python cancellation; lease Broken
     -> Abandoned       receiver/drop/early close; lease Broken
     -> ForcedClosed    lifecycle force; lease Broken
```

The consumer state machine is:

```text
OpenNoSet
  -> ActiveSet
     -> OpenNoSet       set EOF or set discard
  -> Complete           normal response EOF and immutable summary
  -> Closed             explicit full-response close
  -> Failed             stable terminal exception
```

All state transitions are one-way and terminal notification is exactly once.

## Pool ownership, reset and cancellation

The pool path acquires `bb8::PooledConnection<'static, ...>` through the
existing owned checkout helper. Before sending the request it calls the same
checkout preparation and fail-closed `begin_operation()` used by current
operations.

On complete protocol EOF:

1. the producer validates the terminal response state;
2. the connection becomes `NeedsReset`;
3. the operation metric completes successfully;
4. the lifecycle permit is released;
5. dropping the owned lease returns it to bb8;
6. the next checkout uses the existing RESETCONNECTION path.

On early `ResultStream.aclose()`, dropped receiver, producer-task
cancellation, row-conversion failure, SQL/protocol uncertainty or lifecycle
force:

1. the response stream is dropped;
2. the managed connection remains `Broken`;
3. bb8 retires the physical connection;
4. no partially consumed TDS stream is returned to another borrower;
5. terminal acknowledgement is sent only after owned resources are dropped.

The first implementation deliberately does not try to reuse an early-closed
socket. Tiberius exposes no public ATTENTION request and same-socket reuse
would require ATTENTION, DONE_ATTN validation and complete drain. Physical
retirement is the deterministic fail-closed behavior.

Dropping a Python object cannot await. `Drop` therefore sends best-effort
cancellation; the producer also detects receiver closure. Tests wait on pool
statistics and SQL Server DMVs to prove eventual retirement. Explicit
`async with` or `aclose()` remains the deterministic application contract.

Cancelling one pending Python `__anext__` await does not dequeue or lose an
event. The receiver operation is cancellation-safe and remains usable if the
caller keeps the response object. Dropping the object after task cancellation
triggers complete response cancellation and retirement.

## Deadlines and lifecycle

Pool acquisition remains governed by `acquire_timeout`.

The existing absolute `operation_timeout` begins before the request is sent
and covers the complete response through protocol EOF, including time spent
blocked by consumer backpressure. This preserves the established absolute
deadline policy and prevents an abandoned-but-referenced stream from holding
a session forever. Applications streaming intentionally long responses must
configure an appropriate finite operation timeout.

Every live response retains its lifecycle operation permit. Graceful
`disconnect()` waits for response completion or explicit close. At grace
expiry the registered stream participant cancels the producer, retires the
connection and reports the existing typed lifecycle timeout. A cancelled
disconnect waiter does not cancel the shared shutdown supervisor.

The stream does not start a fresh timeout per row and does not hide the
application's backpressure time.

## Transaction semantics

A transaction response holds the owned transaction-session mutex guard for
the complete response. No query, execute, commit or rollback can interleave
with that response.

On normal EOF or `ResultSet.aclose()` through a valid boundary, the
transaction returns to its prior begun state.

On complete response abandonment, Python task cancellation, forced shutdown,
conversion failure or uncertain TDS state:

- the physical transaction connection is retired;
- the transaction transitions to `Failed`;
- SQL Server rolls back the open transaction when the transport closes;
- later data operations and commit are rejected;
- explicit close remains idempotent.

The direct compatibility `Transaction(...)` path follows the same stream
state machine but remains outside the shared pool as already documented.

## Vendored Tiberius response events

The vendored crate gains a public response-event path without changing the
existing `QueryStream<Item = QueryItem>` contract.

The new owned event enum preserves:

```text
Metadata(ResultMetadata)
Row(Row)
Done(ResponseDone)
Info(ResponseInfo)
ReturnStatus(i32)
ReturnValue(ResponseReturnValue)
```

`ResponseDone` exposes the DONE variant and status bits through safe getters.
It returns an optional row count based on DONE_COUNT rather than exposing the
initialized numeric field unconditionally.

`ResponseInfo` owns every INFO field. It does not implement a value-bearing
`repr` beyond safe structural metadata.

`ResponseReturnValue` owns ordinal, name, status, type metadata and
`ColumnData<'static>`. FastMssql converts it through the same Python scalar
mapping used for row values.

New Tiberius entry points return `ResponseStream` for:

```text
parameterized SQL
raw SQL batch
named RPC procedure
```

Existing `Client::query()` and `Client::simple_query()` remain compatible
adapters that filter response events into `QueryItem`.

The currently unimplemented named `RpcProcIdValue::Name` encoder is completed
using the MS-TDS `US_VARCHAR` form. It validates the UTF-16 code-unit length
before writing and never reaches an unimplemented branch or panics on
application input.

RPC parameters carry:

```text
name
value
explicit TypeInfo when declared
ByRefValue for OUTPUT and INPUT_OUTPUT
safe parameter index/declaration metadata for encoding errors
```

`DefaultValue` is never set by this subsystem. A procedure argument omitted
from `Parameters` is not encoded at all.

No raw SQL is generated to invoke a procedure.

## Stored procedures, OUT values and return status

`callproc()` accepts either positional parameters or named parameters in a
`Parameters` object. Mixing positional and named parameters is rejected.
Named keys may be supplied with or without a leading `@`; the canonical
output mapping omits the leading `@`. Duplicate canonical names are rejected.

The procedure name is sent as the RPC ProcName field, not interpolated into
SQL. It must contain one through three non-empty multipart identifier
components, each at most 128 UTF-16 code units and without NUL/control
characters. It is not logged.

Direction rules are:

| Direction | Input value | Explicit SQL type | Wire behavior |
|---|---|---|---|
| `INPUT` | required, nullable allowed | optional | by value |
| `OUTPUT` | must be `None` | required | typed NULL, ByRefValue |
| `INPUT_OUTPUT` | required, nullable allowed | required | value, ByRefValue |
| `RETURN_VALUE` | must be `None` | must be `INT` or omitted | local status slot, not an RPC parameter |

Expanded parameters are rejected for `callproc()`. Table-valued parameters
remain a separate feature.

At most one `RETURN_VALUE` descriptor is accepted. SQL Server's
RETURNSTATUS token is always captured even when no descriptor was supplied;
the descriptor controls only whether an aligned entry is also present in
`output_parameters`.

RETURNVALUE tokens are matched by original parameter ordinal and validated
name, never by arrival order. MS-TDS permits large output values to be moved
after smaller values, so arrival-order matching would be incorrect.

Output conversion supports the scalar grammar already implemented for typed
input. MONEY/SMALLMONEY, TVP, SQL_VARIANT, spatial, hierarchyid, CLR UDT and
legacy LOB output remain explicitly unsupported until their own exact
conversion designs.

## Row conversion and metadata

COLMETADATA creates one shared `Arc<ColumnInfo>` before any row is consumed.
Every `FastRow` in that result set reuses it.

Raw Tiberius `Row` values cross the bounded channel, but Python objects do
not. `ResultSet.__anext__` converts exactly one row under the GIL and returns
one `FastRow`.

If conversion of a received row fails:

- the consumer receives the typed conversion/protocol error;
- the complete response is cancelled;
- the producer retires the physical connection;
- subsequent iteration returns the same terminal classification without
  exposing row contents.

Duplicate SQL column names retain ordinal access. The existing name map keeps
its current deterministic last-name behavior until a separate row-name
ambiguity contract changes it.

## Errors and privacy

New local misuse errors are deterministic and occur before network I/O:

- invalid `buffer_size`;
- mixed named and positional procedure parameters;
- invalid direction/value combinations;
- missing explicit output type;
- expanded procedure parameter;
- duplicate canonical parameter name;
- concurrent result-set consumers;
- accessing `summary` before normal EOF.

Wire, SQL Server and lifecycle failures continue using the existing typed
FastMssql exception hierarchy. Timeout, lifecycle and protocol failures that
retire a stream record `connection_discarded=True` through their existing
typed metadata. Explicit `aclose()` is successful control flow rather than a
fabricated query error. No response is retried automatically.

No exception, `repr`, metric, warning, report or terminal acknowledgement may
contain:

- SQL text;
- parameter or output values;
- row payloads;
- connection strings;
- usernames or passwords;
- driver-authored metric labels or logs derived from procedure names.

SQL Server-originated errors and caller-owned `SqlMessage` fields can contain
server-supplied object, server or procedure names and message text. FastMssql
returns those fields only to the caller and never logs them automatically.

`ResultStream`, `ResultSet`, `ResultSummary`, `ColumnMetadata`,
`DoneResult`, and `SqlMessage` reprs contain only type, counts, indices and
terminal state.

## Operation metrics

The fixed operation-metrics schema remains version 1 in this subsystem:

- `stream()` contributes to `query`;
- `batch()` contributes to `query_batch`;
- `callproc()` contributes to `query`.

Duration spans acquisition-independent operation admission through protocol
EOF or terminal close. A response abandoned before EOF records one
`cancelled` outcome. A SQL/protocol error records one `errors` outcome.
Operation-timeout and lifecycle-force outcomes retain their existing typed
categories. No SQL- or procedure-derived metric label is added.

Adding separately named stream/RPC metric series would require a distinct
schema-version design and is not bundled here.

## Test design

### Existing compatibility cases

`RESULT-001` through `RESULT-015` remain green for legacy `QueryStream`.
Their descriptions are corrected to call it buffered and synchronous.
`RESULT-014` no longer acts as evidence about the new `ResultStream`.

### Tiberius response-event cases

- `TIB-RESULT-001`: metadata and rows preserve multiple result indices.
- `TIB-RESULT-002`: empty metadata survives without a row.
- `TIB-RESULT-003`: DONE kind, DONE_COUNT and zero count are distinct.
- `TIB-RESULT-004`: INFO fields are preserved.
- `TIB-RESULT-005`: RETURNSTATUS is decoded as signed 32-bit.
- `TIB-RESULT-006`: RETURNVALUE preserves ordinal, name, type and value.
- `TIB-RESULT-007`: named RPC uses exact US_VARCHAR encoding.
- `TIB-RESULT-008`: ByRefValue is set only for output-capable directions.
- `TIB-RESULT-009`: existing QueryStream adapters retain their old contract.

### SQL-auth result-stream cases

- `RESULT-016`: three result sets are yielded in order.
- `RESULT-017`: an empty middle result set retains complete metadata.
- `RESULT-018`: outer and inner async iteration are genuinely awaitable and
  reject synchronous iteration.
- `RESULT-019`: a large slow-consumer response remains within the configured
  channel bound and a measured RSS ceiling.
- `RESULT-020`: normal EOF returns a resettable lease and permits a smoke
  query.
- `RESULT-021`: skipping one result set reaches the next boundary without
  materializing skipped rows.
- `RESULT-022`: complete `aclose()` retires the physical SPID and recovers
  the pool.
- `RESULT-023`: dropped and garbage-collected responses eventually retire the
  physical SPID.
- `RESULT-024`: cancellation during `__anext__`, conversion failure and a
  midstream SQL error cannot poison a later checkout.
- `RESULT-025`: concurrent consumers and invalid buffer sizes fail locally
  and privacy-safely.
- `RESULT-026`: graceful disconnect waits; forced disconnect retires the
  stream within the lifecycle budget.
- `RESULT-027`: transaction EOF preserves the transaction, while abandonment
  fails and retires it.
- `RESULT-028`: DONE records, valid zero counts and INFO messages remain
  separate from rows.
- `RESULT-029`: 1,000 concurrent bounded streams respect `pool.max_size`,
  complete exactly once and leave a post-load smoke query green.
- `RESULT-030`: the installed wheel exposes the same async protocol and real
  SQL-auth behavior.

The load gate starts at 1,000 streams with pool size 8 and concurrency 64.
Larger gates are increased only after the focused contract is stable; the
objective is driver backpressure and lease correctness, not SQL Server
saturation.

### Stored-procedure cases

- `RPC-001`: direct named procedure with no parameters returns exact status.
- `RPC-002`: INPUT, OUTPUT and INPUT_OUTPUT integer values round-trip.
- `RPC-003`: Unicode, binary, decimal, UUID and temporal outputs preserve
  their Python types.
- `RPC-004`: multiple and empty result sets precede output summary correctly.
- `RPC-005`: output values reordered by large-object rules still match their
  original names/ordinals.
- `RPC-006`: a valid zero return status differs from absent status.
- `RPC-007`: SQL error, cancellation and early close retire or reset the
  connection according to terminal certainty.
- `RPC-008`: named and positional parameter modes, direction rules and
  procedure-name limits are deterministic and injection-safe.
- `RPC-009`: pooled and transactional call paths share one implementation.
- `RPC-010`: concurrent procedure calls preserve outputs without exceeding
  pool/session bounds.

All SQL objects use per-test unique names and are dropped through the cleanup
registry. No exception handler swallows an unexpected failure.

## Branch topology

Every arrow means strict Git ancestry from the previous reviewed branch:

```text
docs/resultsets-streaming-design
  -> test/tiberius-response-events
     -> feat/tiberius-response-events
        -> test/resultsets-streaming
           -> feat/resultsets-streaming
              -> test/resultstream-lifecycle
                 -> feat/resultstream-lifecycle
                    -> test/rpc-output-results
                       -> feat/rpc-output-results
                          -> verify/resultsets-streaming-merge
                             -> test/sql-auth-validation
```

Responsibilities:

- `test/tiberius-response-events` records raw protocol RED tests only;
- `feat/tiberius-response-events` changes only the vendored response/RPC
  event surface and its patch documentation;
- `test/resultsets-streaming` records Python and real-MSSQL RED contracts;
- `feat/resultsets-streaming` implements the additive bounded result API;
- `test/resultstream-lifecycle` records early-close, cancellation,
  transaction and shutdown RED contracts;
- `feat/resultstream-lifecycle` completes fail-closed ownership semantics;
- `test/rpc-output-results` records procedure OUT/return RED contracts;
- `feat/rpc-output-results` implements direct RPC and output conversion;
- any newly discovered defect receives its own `test/...` then `fix/...`
  branch inserted before technical verification;
- `verify/resultsets-streaming-merge` merges reviewed histories and contains
  only merge/evidence changes;
- a later dedicated status branch updates the live audit after exact merged
  verification.

No intermediate branch is presented as a production candidate. In
particular, the basic stream feature is not integrated into the cumulative
branch until the lifecycle and fail-closed retirement branch is also green.

No implementation branch is pushed to or based for publication on the
original Rivendael repository.

## TDD and acceptance gates

Every focused problem requires:

```text
new focused test on its test branch                     RED
same focused test after implementation                 PASS
all pre-existing tests for that layer                  PASS
git diff --check                                       PASS
privacy sentinel scan                                 PASS
branch ancestry and fork remote check                 PASS
```

Every Rust implementation branch also requires:

```text
cargo fmt --check                                      PASS
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked                                    PASS
vendored Tiberius tests                                PASS
```

Every Python/MSSQL implementation branch also requires:

```text
focused real SQL-auth cases                            PASS, zero skips
legacy RESULT-001..RESULT-015                         PASS
affected transaction/pool/timeout/lifecycle cases     PASS
stub/runtime export contract                           PASS
```

The exact final technical merge requires:

```text
complete scripts/sql_auth/run_all.sh                   PASS
strict matrix                                          PASS, zero skips
async/framework/resilience/load lanes                  PASS, zero skips
original-local-regression lane                         PASS
1,000-stream bounded load and post-load smoke          PASS
RSS/backpressure evidence                              PASS
physical SPID retirement evidence                      PASS
wheel build and isolated install                       PASS
installed-wheel real SQL-auth cases                    PASS
raw Cargo and wheel on Linux/macOS/Windows             PASS hosted
RustSec                                                PASS hosted
secret/privacy scans                                   PASS
local/fork SHA parity                                  PASS
original-repository push URL                           DISABLED
```

Observed counts and memory/session metrics from the final exact SHA are
authoritative. The design does not forecast them as completed evidence.

## Live-audit update

Only after exact merged verification, a dedicated status branch updates
`docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` with:

- original first-result/buffered reproduction;
- the selected producer/channel architecture;
- every RED, feature and fix branch plus exact SHA;
- supported APIs and deliberate compatibility behavior;
- result-set, metadata, DONE, INFO, OUT and return-status evidence;
- bounded channel, RSS, pool and physical-SPID metrics;
- transaction, timeout and lifecycle outcomes;
- exact local Docker, wheel and hosted gate results;
- remaining TVP, MONEY, SQL_VARIANT, native bulk and framework-matrix work.

The audit checkbox for bounded streaming is marked complete only after the
memory and early-close gates pass. The checkbox for multiple/empty
resultsets and output parameters is marked complete only after RPC cases pass.

## Non-goals

This design does not:

- change existing `query()` into a breaking async stream;
- provide synchronous iteration over the new `ResultStream` or `ResultSet`;
- buffer a live result merely to preserve indexing, length or reset;
- reuse an early-closed physical connection without ATTENTION/DONE_ATTN;
- implement MARS or concurrent commands on one physical session;
- implement TVP, MONEY/SMALLMONEY fixed-point, SQL_VARIANT, spatial,
  hierarchyid, CLR UDT or legacy LOB conversion;
- implement native bulk copy or bounded input bulk iteration;
- add dynamic SQL/procedure labels to metrics;
- add an ODBC fallback;
- bump package metadata, publish a wheel/release or claim overall enterprise
  production readiness;
- publish a Tiberius fork or external Tiberius pull request;
- write, push, open a pull request or publish anything in the original
  FastMssql repository.

## Self-review record

The design was checked against the live audit, `connection.rs`,
`transaction.rs`, `pool_manager.rs`, `helpers.rs`, `types.rs`,
`parameter_conversion.rs`, `py_parameters.rs`, both Python stub layers,
strict result/parameter/timeout/lifecycle tests, the vendored Tiberius client,
query stream, token decoder, RPC request encoder and Microsoft MS-TDS token
contracts.

Corrections made during self-review:

1. Replacing `QueryStream` in place was rejected because it would silently
   break indexing, length, reset and synchronous consumers.
2. A direct pyclass-held Tiberius stream was rejected because its client
   lifetime is self-referential.
3. The pool lease and lifecycle permit last through response EOF, not merely
   through request submission.
4. Early close retires the socket instead of pretending a dropped Tiberius
   stream is reusable.
5. Empty result-set metadata is built from COLMETADATA rather than the first
   row.
6. The API separates skipping one set from abandoning the entire response.
7. DONE row count uses DONE_COUNT, so absent and valid zero are distinct.
8. INFO tokens are caller-owned data and are not written to logs.
9. Output parameters are matched by ordinal/name rather than arrival order
   because MS-TDS may reorder large outputs.
10. Stored procedures use direct RPC ProcName encoding rather than generated
    `EXEC` SQL.
11. RETURNSTATUS is captured independently of an optional RETURN_VALUE
    descriptor.
12. The operation timeout remains one absolute response deadline; it does not
    restart per row.
13. Transaction abandonment fails the transaction and closes its transport
    instead of allowing commit on uncertain protocol state.
14. Row payload is bounded by the channel, while fixed caps also bound
    result-set, DONE and INFO metadata.
15. Existing operation-metric schema is preserved by mapping the new APIs to
    stable existing operation names.
16. Real MSSQL, RSS, DMV, installed-wheel and hosted OS gates are all required;
    unit tests alone cannot prove bounded production behavior.
17. The work is decomposed into protocol, streaming, lifecycle and RPC
    RED/feature pairs rather than one opaque patch.
18. Remaining type, bulk and framework work stays explicit and does not become
    an implied production-ready claim.

The specification contains no placeholder, swallowed exception, unbounded
row buffer, arbitrary SQL procedure invocation or authorization for an
original-repository write.
