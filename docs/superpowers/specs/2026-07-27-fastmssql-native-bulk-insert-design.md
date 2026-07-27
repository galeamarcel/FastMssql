# FastMssql Native TDS Bulk Insert Design

## Status and scope

This design closes the fourth of seven approved batch/bulk slices. It is based
on the exact cumulative fork status commit
`4cfaa18a8d6938fb2911a4492fbc87093d9ef0d2`, whose technical ancestor
`52c04c35a27dd6a79ccb5f54b15d7a0413a8965b` provides the safe ordered-column
vendored-Tiberius primitive.

This slice exposes native TDS bulk copy to Python for concrete list input:

- `Connection.native_bulk_insert(...)`;
- `Transaction.native_bulk_insert(...)`.

It does not add generator or async-generator input. Bounded iterable
coordination remains the next independent RED/feature pair. It also does not
add `execute_many()` or `query_many()`.

The compatibility `Connection.bulk_insert()` path remains parameterized
`INSERT ... VALUES` and is not changed or silently redirected.

## Confirmed current behavior

At the exact base:

- vendored Tiberius passes 168/168 unit tests;
- direct Tiberius bulk-column SQL-auth tests pass 5/5;
- `Client::bulk_insert_columns(table, columns)` validates a closed raw
  identifier grammar, retrieves exact ordered metadata, rejects restricted
  columns and returns `BulkLoadRequest`;
- `BulkLoadRequest::send()` encodes rows using the target TDS metadata and
  `finalize()` is mandatory;
- FastMssql exposes only compatibility `bulk_insert()`;
- ordinary raw Python integers are inferred as BIGINT, while native bulk
  encoding for an INT target requires an INT-shaped `ColumnData`;
- the bulk request does not expose its checked target declarations to the
  FastMssql crate;
- `TransactionState` has no rollback-only state;
- operation metrics already contain one `bulk_insert` family and do not need
  a schema change for this slice.

The native API therefore cannot be implemented safely by converting values
with the ordinary inference path and calling `send()`. Conversion must be
driven by the server metadata for each requested target column.

## Approaches considered

### Adopted: exact metadata-guided conversion and bounded concrete chunks

The vendored request exposes its already checked declarations. FastMssql
parses those internally generated declarations with its closed type parser,
converts each Python cell to the exact target wire type, sends at most one
concrete chunk at a time and finalizes every bulk request.

The connection form owns one SQL transaction across all chunks. The
transaction form uses the caller's active transaction and never settles it.

This preserves raw-value ergonomics without relying on implicit SQL casts
that do not exist in TDS bulk encoding.

### Rejected: ordinary inferred values

Sending ordinary `python_to_fast_parameter` output would make a Python `int`
BIGINT even for TINYINT, SMALLINT or INT targets. It also loses exact
temporal scale and decimal metadata. Depending on server-side casts here is
incorrect because bulk rows are encoded against target metadata before SQL
Server can apply normal expression conversion.

### Rejected: require every caller to wrap every cell in `Parameter`

This could make wire types explicit but would make the basic API impractical,
would still need target compatibility checks and would not satisfy the
approved raw-value contract.

### Rejected: replace compatibility `bulk_insert()`

Native bulk differs in trigger behavior, metadata restrictions, cancellation
boundaries and supported types. Replacing the compatibility method would be a
breaking semantic change.

### Deferred: iterable and async-iterable coordination

Accepting arbitrary producers requires a Python-side bounded coordinator,
cleanup shielding and producer backpressure. Implementing it here would merge
two approved RED/feature pairs and make cancellation evidence ambiguous.

## Public Python API

### Connection

```python
affected = await connection.native_bulk_insert(
    table,
    columns,
    rows,
    *,
    chunk_size=1000,
)
```

### Transaction

```python
await transaction.begin()
affected = await transaction.native_bulk_insert(
    table,
    columns,
    rows,
    *,
    chunk_size=1000,
)
await transaction.commit()
```

The transaction method requires `TransactionState::Active`. It never begins,
commits or rolls back the caller's transaction.

### Arguments and result

- `table: str` and `columns: list[str]` are raw identifiers under the exact
  vendored-Tiberius grammar.
- `columns` is non-empty.
- `rows` is a concrete Python list in this slice. Every row is a concrete
  Python list with exactly `len(columns)` cells.
- `chunk_size` is an integer in `1..=10_000`; booleans, zero, negative values
  and values above 10,000 are rejected before input consumption, pool
  admission or wire I/O.
- The default chunk size is 1,000.
- The result is a non-negative Python `int` produced from a checked `u64`
  cumulative affected-row count.
- Empty input returns `0` without pool admission, transaction creation,
  SQL/TDS activity or operation-metric activity.
- The driver never owns converted cells for more than one chunk.
- Callers must not resize the top-level rows list until the awaitable
  completes. A detected length change fails the operation.

The wrapper signatures are explicit even though `Connection.__getattr__`
could otherwise forward the raw extension method. This keeps runtime
documentation and stubs aligned and gives the next iterable slice one stable
surface to broaden.

## Identifier boundary

FastMssql calls an additive pure vendored-Tiberius validator synchronously
before creating the Rust future:

```rust
pub fn validate_bulk_insert_columns(
    table: &str,
    columns: &[&str],
) -> crate::Result<()>
```

The async client method repeats validation as the authoritative wire
boundary. Validation remains:

- one through three non-empty table parts;
- one literal part per column;
- no NUL;
- at most 128 UTF-16 code units per part;
- raw, not pre-quoted input;
- exact duplicate requested columns rejected;
- at most `u16::MAX` columns;
- independent bracket quoting with `]` doubled.

Error messages contain only structural kind/index context, never the raw
identifier.

## Target metadata bridge

`BulkLoadRequest` gains one read-only additive method:

```rust
pub fn column_declarations(&self) -> crate::Result<Vec<String>>
```

It returns declarations generated from the same checked metadata formatter
used to build `INSERT BULK`. The strings are driver-generated, not user SQL.
FastMssql parses them through `parse_sql_parameter_type`; arbitrary
declarations remain impossible.

This bridge is intentionally narrow:

- it exposes no mutable metadata;
- it exposes no table or column identifier;
- it does not change `send()` or `finalize()`;
- it does not make private TDS `TypeInfo` public;
- it does not alter `Client::bulk_insert(table)`.

Metadata representable by Tiberius but not by FastMssql's exact input type
model is rejected before the first row is sent. MONEY/SMALLMONEY remain
outside this slice because exact fixed-point input is still an open
capability. SQL_VARIANT, spatial, hierarchyid, CLR UDT and legacy LOB remain
unsupported.

## Exact cell conversion

For each target declaration, FastMssql converts the cell with the existing
closed typed conversion functions.

### Raw values

Raw Python values are converted as though the target declaration were an
explicit validated `Parameter` declaration:

- BIT -> `ColumnData::Bit`;
- TINYINT/SMALLINT/INT/BIGINT -> exact-width integer variants with range
  checks;
- REAL/FLOAT -> exact target floating representation with finite checks;
- DECIMAL/NUMERIC -> exact precision and scale;
- CHAR/VARCHAR/NCHAR/NVARCHAR -> target length semantics;
- BINARY/VARBINARY -> target length semantics;
- UNIQUEIDENTIFIER -> UUID;
- DATE/TIME/DATETIME/SMALLDATETIME/DATETIME2/DATETIMEOFFSET -> exact target
  family and scale;
- XML -> XML;
- `None` -> a target-shaped typed NULL.

### `Parameter` descriptors

A non-expanded INPUT descriptor is accepted only when its explicit type
matches the canonical target declaration. A descriptor without an explicit
type is unwrapped and converted against the target. Expanded, OUTPUT,
INPUT_OUTPUT, RETURN_VALUE and target-mismatched descriptors fail locally.

An explicit `TypedNull` must match the target wire family. Mismatches fail
instead of silently changing the caller's declared intent.

Every conversion error preserves:

```text
row_index
column_index
parameter_index
sql_type
reason
wire_sent
connection_discarded
```

Indices are global and zero-based. Errors never contain the Python value,
table, column, SQL text or credential.

## Chunk and bulk-request lifecycle

For each non-empty chunk:

1. start `Client::bulk_insert_columns(table, columns)`;
2. read and parse its checked target declarations;
3. convert the complete current chunk to exact owned `ColumnData`;
4. if conversion fails before any row is sent, finalize an empty request to
   restore the protocol before returning the conversion error;
5. build one dynamic `TokenRow` per row;
6. await `send()` in input order;
7. await `finalize()` exactly once;
8. require the returned count to equal the number of rows sent;
9. add it to the cumulative count with checked arithmetic;
10. drop the converted chunk before acquiring Python references for the next
    chunk.

If empty finalization itself fails, the conversion error remains primary and
the cleanup failure is its cause. The connection is retired.

After any `send()` begins, no transparent retry is allowed. A send/finalize
failure is conservatively post-wire even when the current packet may still
be buffered.

## Connection form atomicity

The connection method:

1. validates arguments synchronously;
2. returns zero early for empty rows;
3. admits exactly one `bulk_insert` operation;
4. acquires one pooled physical connection;
5. marks it unsafe before the first request await;
6. starts one SQL transaction;
7. executes all native chunks on that same lease and deadline;
8. commits once after all chunks finalize;
9. marks the connection reusable only after complete settlement.

Failure before COMMIT rolls back when the protocol is known reusable. If a
bulk request or cleanup is uncertain, the socket is retired and SQL Server
rolls back the open transaction when the transport closes.

A timeout before COMMIT is not a commit-outcome ambiguity. A lost response
after COMMIT is sent uses the existing `CommitOutcomeUnknown` classifier and
must not issue an automatic rollback or retry.

## Transaction form and rollback-only state

Native bulk on `Transaction` is legal only in `Active`.

Success restores `Active` and leaves settlement to the caller.

Failure disposition is explicit:

- local/pre-row failure plus successful empty finalization -> restore
  `Active`;
- failure after a row may have reached the bulk stream, with a fully consumed
  terminal response and reusable transport -> `RollbackOnly`;
- timeout, cancellation, panic, transport/protocol uncertainty or cleanup
  failure -> retire the connection and enter `Failed`.

`RollbackOnly`:

- rejects query, execute, stream, batch, callproc, native bulk and COMMIT;
- allows ROLLBACK;
- releases a pooled lease only after successful ROLLBACK or close;
- cannot be converted back to Active by another data operation.

The public error for a rejected operation states that rollback is required
without revealing SQL or values.

## Cancellation, timeout and forced shutdown

- One absolute operation deadline covers acquisition, BEGIN, all chunks and
  settlement.
- The transaction lifetime deadline remains an additional upper bound for
  the transaction form.
- Python cancellation while a bulk request is active leaves the guard armed;
  the physical connection is retired.
- Forced lifecycle shutdown follows the existing generation-aware force
  signal and retires the session.
- No cleanup path swallows `CancelledError` or another `BaseException`.
- The original failure remains primary; cleanup failure is attached as
  `__cause__`.

## Metrics

Compatibility and native bulk share the existing `bulk_insert` operation
family in this slice:

- one non-empty public call -> one started/completed/error observation;
- internal chunks do not create metric observations;
- empty input creates none;
- no operation-metrics schema change is made.

The live audit and documentation must distinguish compatibility VALUES bulk
from native TDS bulk even though the metric family is shared.

## RED contracts

The canonical SQL-auth specification gains exactly seven IDs:

- `BULK-006`: public raw/wrapper/stub surface and zero-I/O empty input;
- `BULK-007`: ordered subset, defaults, nullable values and exact returned
  count through the TDS bulk path;
- `BULK-008`: target-guided mixed scalar, exact temporal/numeric/UUID/XML and
  typed-NULL conversion;
- `BULK-009`: identity/computed/rowversion/MONEY/SQL_VARIANT rejection is
  typed, panic-free and followed by pool recovery;
- `BULK-010`: connection-form late multi-chunk failure rolls back every
  earlier native chunk;
- `BULK-011`: transaction success is settlement-neutral; post-wire failure
  becomes rollback-only and successful ROLLBACK removes all rows;
- `BULK-012`: cancellation/timeout retires the physical session, terminates
  the server request and recovers pool capacity.

Static evidence must prove the FastMssql path calls
`bulk_insert_columns`, `TokenRow::push`, `send()` and `finalize()`, and does
not build parameterized VALUES SQL.

## Verification gates

Mandatory local gates:

- focused RED then GREEN tests;
- canonical matrix contract updated from 377 to 384 unique IDs;
- vendored Tiberius fmt, Clippy and unit tests;
- direct Tiberius SQL-auth regressions;
- FastMssql fmt, Clippy and Rust unit tests;
- native extension build and exact-worktree import check;
- `BULK-006` through `BULK-012` on Docker SQL Server SQL-auth;
- compatibility batch/bulk and parameter regression suites;
- transaction state/cancellation/commit-ambiguity regressions;
- 1,000 and 10,000 concrete-list native-bulk profiles;
- opt-in 99,999-row native-bulk profile with exact persisted count, RSS,
  event-loop progress and post-load smoke;
- credential-value scan and artifact scan;
- installed-wheel focused gate;
- code-review-graph rebuild, impact review and explicit recording of any
  vendored-code indexing limitation.

Hosted Linux/macOS/Windows and RustSec evidence remains tied to the exact SHA
that actually ran. A local pass is not presented as a hosted pass.

## Branch topology

```text
docs/native-bulk-insert
  -> test/native-bulk-insert
  -> feat/native-bulk-insert
  -> docs/native-bulk-insert-status
```

The feature branch must descend from the observed RED commit. No history is
rewritten.

All pushes go only to `https://github.com/galeamarcel/FastMssql.git`.
The Rivendael remote remains fetch-only with push URL `DISABLED`.

## Non-claims

This slice does not claim:

- iterable or async-iterable input;
- byte-level streaming inside one oversized LOB cell;
- MONEY/SMALLMONEY exact fixed-point input;
- SQL_VARIANT, spatial, hierarchyid, CLR UDT or legacy LOB support;
- MARS or concurrent operations on one transaction session;
- `execute_many()` or `query_many()`;
- a release, wheel publication, Tiberius fork or upstream pull request.
