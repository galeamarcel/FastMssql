# FastMssql Vendored Tiberius Bulk Column Subset Design

## Status and scope

This design closes the first of the five batch/bulk slices that remain open
after the typed compatibility-bulk row conversion at
`deef315cc6be7b2c303040cca99a268aa201a28d`.

The slice adds one safe, additive vendored-Tiberius primitive for starting a
TDS bulk request against an ordered subset of table columns. It does not add
the Python `native_bulk_insert()` API, iterable coordination, `execute_many()`
or `query_many()`. Those remain separate RED/implementation branch pairs.

The public Tiberius compatibility method `Client::bulk_insert(table)` remains
unchanged.

## Confirmed current behavior

At the exact base commit `151c69efb60a787f0f8bbee3beb73ae10a947dd4`:

- the vendored Tiberius library passes 162/162 unit tests with
  `chrono,tds73,rustls`;
- `Client::bulk_insert(table)` executes `SELECT TOP 0 * FROM <table>`;
- it filters the returned metadata to every column carrying the
  `Updateable` flag;
- it formats those columns with `Display for MetaDataColumn`;
- it therefore cannot represent the ordered subset accepted by FastMssql's
  compatibility bulk API;
- the existing formatter contains `todo!()` and `unreachable!()` branches for
  unsupported or inconsistent metadata;
- `BulkLoadRequest` already streams encoded rows in bounded TDS packets and
  requires `finalize()` to consume the terminal server response.

## Approaches considered

### Adopted: quoted projection plus checked metadata declarations

Add a separate `Client::bulk_insert_columns(table, columns)` method. It parses
and quotes structured identifiers, projects only the requested columns,
validates the returned metadata in the same order and builds `INSERT BULK`
declarations through a total checked formatter.

This approach makes identifier safety, column order, writability and
unsupported metadata explicit without changing existing callers.

### Rejected: fetch every column and filter client-side

Fetching `SELECT TOP 0 *` and matching names locally avoids a column
projection, but it makes database-collation name matching and duplicate
resolution implicit. It also transfers metadata for columns the caller did
not request and does not prove that the server resolved each requested
identifier in the intended order.

### Rejected: accept raw `INSERT BULK` declarations or ordinals

Raw declarations expose an SQL-injection boundary and duplicate Tiberius's
TDS metadata model in every caller. Ordinals avoid identifier formatting but
do not match FastMssql's public column-name API and are fragile across schema
changes.

## Public Rust API

The vendored client gains this additive method:

```rust
pub async fn bulk_insert_columns<'a>(
    &'a mut self,
    table: &str,
    columns: &[&str],
) -> crate::Result<BulkLoadRequest<'a, S>>
```

The method copies and validates all identifier input before the first await,
so the returned request borrows only the client connection. The caller sends
rows in exactly the same order as `columns`.

`columns` must be non-empty and must fit the TDS `u16` column-count field.
Exact duplicate requested names are rejected before wire I/O. Server-returned
canonical duplicates are rejected after the metadata response is completely
drained.

## Identifier contract

Inputs are raw names, not pre-quoted SQL fragments.

### Table

- A table contains one, two or three dot-separated parts:
  `table`, `schema.table` or `database.schema.table`.
- Every part must be non-empty, contain no NUL and contain at most 128 UTF-16
  code units.
- A dot separates table qualification parts. A dot inside an individual table
  part is outside this API's grammar.
- Every part is independently bracket-quoted.
- A closing bracket is escaped as `]]`.
- Four-part linked-server targets, empty parts such as `db..table` and
  pre-quoted names are outside the contract and fail locally.

### Columns

- Every entry is one complete raw column name.
- A column must be non-empty, contain no NUL and contain at most 128 UTF-16
  code units.
- Dots in a column name are literal characters because a column is always one
  bracket-quoted part.
- Reserved words, whitespace, Unicode and closing brackets are valid and are
  neutralized by bracket quoting.

Invalid identifiers return `Error::BulkInput` before the connection is
flushed or any request packet is sent. Error messages identify only the
component kind and zero-based index; they do not echo identifier text.

## Request flow

The method performs these phases:

1. Validate the complete table and column input and construct owned quoted
   forms.
2. Flush any prior response exactly as existing client methods require.
3. Send one metadata batch:

   ```sql
   SELECT TOP (0) [requested_0], [requested_1]
   FROM [schema].[table]
   ```

4. Drain the complete response and require exactly one metadata result set.
5. Require the returned column count to equal the requested count.
6. Require each returned metadata name to equal the corresponding requested
   name byte-for-byte. This strict spelling rule is deterministic even on a
   case-insensitive database: callers must supply the canonical table-column
   spelling.
7. Reject duplicate canonical metadata names, non-writable columns,
   restricted flags and unsupported or inconsistent type metadata.
8. Build checked declarations using the server metadata and quote every
   returned column name again.
9. Send and drain the `INSERT BULK` setup batch.
10. Return `BulkLoadRequest` with metadata in exact requested order.

The reset-isolation prefix remains governed by the existing
`query_with_reset_baseline` path. No identifier, SQL text or metadata name is
added to tracing.

## Writability and flag rules

A selected column must contain `ColumnFlag::Updateable`.

The new path rejects these flags even if `Updateable` is also present:

- `Identity`;
- `Computed`;
- `FixedLenClrType`;
- `SparseColumnSet`;
- `Encrypted`;
- `Hidden`.

`UpdateableUnknown` does not satisfy writability. A SQL Server `rowversion`
column is rejected because its metadata is not updateable. `Nullable`,
`CaseSensitive` and `Key` do not prevent bulk input.

All these validation failures are `Error::BulkInput`. The response has already
been fully drained, so this local metadata rejection does not begin a bulk
request and leaves the client reusable.

## Total checked type declaration formatter

The new path does not call `Display for MetaDataColumn`. A private formatter
maps only validated metadata and returns `Error::BulkInput` for every other
case.

### Fixed-width metadata

| TDS metadata | `INSERT BULK` declaration |
| --- | --- |
| `Int1` | `tinyint` |
| `Bit` | `bit` |
| `Int2` | `smallint` |
| `Int4` | `int` |
| `Datetime4` | `smalldatetime` |
| `Float4` | `real` |
| `Money` | `money` |
| `Datetime` | `datetime` |
| `Float8` | `float` |
| `Money4` | `smallmoney` |
| `Int8` | `bigint` |

`FixedLenType::Null` is rejected.

### Variable-width metadata

- `Bitn(1)` becomes `bit`.
- `Guid(16)` becomes `uniqueidentifier`.
- `Intn(1|2|4|8)` becomes `tinyint|smallint|int|bigint`.
- `Floatn(4|8)` becomes `real|float`.
- `Money(4|8)` becomes `smallmoney|money`.
- `Datetimen(4|8)` becomes `smalldatetime|datetime`.
- `Daten(3)` becomes `date`.
- `Timen(scale)`, `Datetime2(scale)` and `DatetimeOffsetn(scale)` require
  `scale` in `0..=7` and retain that exact scale.
- `BigVarBin`, `BigVarChar` and `NVarchar` accept a finite legal length or the
  `0xffff` MAX sentinel.
- `BigBinary`, `BigChar` and `NChar` accept only finite legal lengths.
- Unicode TDS lengths are bytes, so finite `NVarchar` and `NChar` lengths must
  be positive, even and no greater than 8000; the SQL declaration uses half
  that byte length.
- ANSI and binary finite lengths must be positive and no greater than 8000.
- `Text`, `NText`, `Image`, `Udt`, `SSVariant`, and variable-context
  `Decimaln`, `Numericn` or `Xml` are rejected.

### Precision metadata and XML

`Decimaln` and `Numericn` precision metadata require:

- precision in `1..=38`;
- scale no greater than precision;
- exact TDS storage size: 5 bytes for precision 1–9, 9 for 10–19, 13 for
  20–28 and 17 for 29–38.

The declaration preserves `decimal` versus `numeric` and exact
`(precision, scale)`.

The dedicated `TypeInfo::Xml` representation becomes `xml`. Its existing
schema metadata remains in the TDS column token; schema names are never
formatted into SQL.

No match arm in this formatter may panic, assert, call `todo!()` or call
`unreachable!()`.

## Errors and connection disposition

- Input validation failures are `Error::BulkInput` before wire I/O.
- A missing, extra or reordered metadata result is a typed
  `Error::Protocol`; names are not echoed.
- Non-writable, restricted or unsupported metadata is `Error::BulkInput`
  after the metadata response is fully drained and before `INSERT BULK`.
- SQL Server errors and I/O errors retain their existing Tiberius variants.
- Unsupported `SQL_VARIANT` or UDT decoding remains a typed protocol error;
  it must never unwind the Rust worker.
- Once the `INSERT BULK` setup succeeds, the caller must either call
  `finalize()` or treat the physical connection as unusable. This slice does
  not add automatic cancellation recovery.

FastMssql's later native-bulk slice will conservatively retire the physical
connection after cancellation, timeout, panic or abandonment once a bulk
request may have started.

## Tests

### Vendored unit contracts

Private helper tests prove:

- table qualification and column quoting, including `]`, reserved words and a
  literal dot in a column;
- empty/NUL/overlong parts, empty lists, duplicate names and too many columns
  return `BulkInput`;
- the metadata SQL and `INSERT BULK` declarations contain only quoted,
  validated identifiers;
- metadata count, order and canonical duplicates are checked;
- identity, computed, rowversion-equivalent non-updateable and restricted
  flags are rejected;
- every supported type family has an exact declaration;
- invalid widths/scales/precision/storage and every unsupported type return a
  typed error without invoking a panic hook;
- the existing `bulk_insert(table)` API remains present.

### Real SQL-auth integration

`vendor/tiberius/tests/bulk_column_subset_sql_auth.rs` proves:

1. a reordered subset inserts into a wider table while omitted columns use
   defaults and a requested nullable column stores NULL;
2. schema/table/column names containing reserved words and `]` are handled as
   identifiers and cannot affect a separate guard table;
3. identity, computed and rowversion targets fail as `BulkInput`, send no row
   and leave the fully drained connection usable;
4. SQL_VARIANT metadata returns a typed error without a Rust panic;
5. malformed identifiers fail locally and the same connection remains usable.

The test runner uses the existing Docker SQL Server SQL-auth environment and
runs with one test thread to avoid shared panic-hook races.

### Regression and quality gates

- vendored Tiberius fmt;
- vendored Tiberius Clippy with warnings denied and only the repository's
  documented legacy lint allowances;
- vendored Tiberius library tests;
- existing response API, response SQL-auth and token-safety SQL-auth tests;
- new bulk-column-subset SQL-auth test;
- root Cargo fmt, Clippy and tests;
- FastMssql strict batch/bulk and parameter tests;
- credential-value scan;
- code-review-graph rebuild, change risk, affected flows and test mapping.

## Branch and evidence topology

```text
docs/tiberius-bulk-column-subset
  -> test/tiberius-bulk-column-subset
     -> feat/tiberius-bulk-column-subset
        -> docs/tiberius-bulk-column-subset-status
```

The RED branch adds only tests, the SQL-auth runner hook, patch-status
documentation and `VERSION.md`. The feature branch must descend from the
observed RED commit. The status branch records exact local/Docker evidence and
keeps hosted evidence tied to its actual ancestor.

Every branch is pushed only to
`https://github.com/galeamarcel/FastMssql.git`. The `upstream` push URL remains
`DISABLED`. No Tiberius fork, FastMssql release, wheel publication or PR is
created by this slice.

## Acceptance criteria

The slice is complete only when:

- the new method compiles without changing existing `bulk_insert(table)`;
- the desired API tests are observed RED on the unchanged implementation;
- all valid identifiers are bracket-quoted and invalid grammar is pre-wire;
- subset metadata and row encoding preserve exact caller order;
- identity, computed, rowversion and unsupported metadata fail with typed
  errors and no panic;
- the real SQL-auth subset/default/NULL/identifier cases pass;
- the complete regression and quality gates above pass;
- the live audit and `VERSION.md` identify exact commits and do not overstate
  later native-bulk work.

