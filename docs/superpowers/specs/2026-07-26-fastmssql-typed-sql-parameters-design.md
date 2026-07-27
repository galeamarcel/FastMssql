# FastMssql Enterprise Typed SQL Parameters Design

**Status:** approved for inline execution by Marcel Galea's standing
authorization; specification, implementation plan and every implementation
branch require self-review.

**Date:** 26 July 2026

**Source baseline:** `test/sql-auth-validation` at
`fa0ffa38be32669a838d469132e5e02b8cb5234c`

**Repository boundary:** every design, reproduction, test, fix, feature,
evidence and status branch is created and published only in
`https://github.com/galeamarcel/FastMssql.git`. The original repository is
fetch-only and its push URL remains exactly `DISABLED`. The local Tiberius
source may be changed inside this FastMssql fork, but no Tiberius fork,
publication or external pull request is authorized by this design.

## Objective

Replace the current decorative parameter hints with an enterprise input
parameter contract whose Python value, declared SQL type and TDS metadata
remain coherent from the Python API through `sp_executesql`.

The completed subsystem must:

1. bind Python `bool` as SQL `BIT`, not `BIGINT`;
2. support exact Python `Decimal`, `uuid.UUID`, naive `time`, naive
   `datetime`, and timezone-aware `datetime` values;
3. preserve the offset of aware datetimes as `DATETIMEOFFSET`;
4. make `Parameter(value, sql_type)` control the effective SQL declaration
   and TDS metadata, including typed `None`;
5. carry `direction`, `precision`, `scale`, `length`, and expansion metadata
   without leaking the value through `repr`;
6. reject invalid declarations and incompatible values before sending an RPC;
7. propagate a declared type to every element of an expanded parameter;
8. apply the same conversion path to direct connections, pooled connections,
   transactions, batch execution, and raw positional lists;
9. remain true async and safe under concurrent use;
10. prove effective types with `SQL_VARIANT_PROPERTY`, not only successful
    `CAST` results.

## Measured baseline

A read-only diagnostic against the dedicated SQL-auth Docker container
executed:

```sql
SELECT
  SQL_VARIANT_PROPERTY(@P1, 'BaseType'),
  SQL_VARIANT_PROPERTY(@P1, 'Precision'),
  SQL_VARIANT_PROPERTY(@P1, 'Scale'),
  SQL_VARIANT_PROPERTY(@P1, 'MaxLength')
```

The unmodified cumulative baseline produced:

| Python input | Effective SQL type |
|---|---|
| `True` | `bigint`, precision 19, length 8 |
| `7` | `bigint`, precision 19, length 8 |
| `Parameter(7, "INT")` | `bigint`, precision 19, length 8 |
| `"abc"` | `nvarchar`, max length 8000 bytes |
| `Parameter("abc", "VARCHAR(10)")` | `nvarchar`, max length 8000 bytes |
| aware `datetime(..., +02:00)` | `datetime2(7)`, offset discarded |

The current source explains every observation:

- `Parameters.to_list()` returns only values and discards descriptors;
- `PyInt` is tested before `PyBool`, although Python `bool` subclasses `int`;
- aware datetime extraction occurs after naive datetime extraction and then
  explicitly calls `naive_local()`;
- `Decimal`, `UUID`, and `time` have no input variants;
- Tiberius derives the `sp_executesql` declaration exclusively from
  `ColumnData::type_name()`;
- `Parameter.__repr__()` embeds `repr(value)`.

## Approaches considered

### Approach A — closed typed descriptor plus typed Tiberius RPC, selected

FastMssql parses the public SQL declaration into a closed enum and converts
the Python value into a compatible owned Rust value. The vendored Tiberius
copy accepts an optional structured parameter type through `ToSql`, generates
the canonical `sp_executesql` declaration, and encodes matching TDS
`TYPE_INFO`.

Advantages:

- no arbitrary user text is appended to the RPC declaration;
- query plans see the intended stable type, precision, scale and length;
- typed nulls use the same path as non-null values;
- ANSI values can use the negotiated session collation;
- direct SQL, transactions and batches share one conversion implementation;
- unit tests can verify raw TDS type metadata while SQL-auth tests verify the
  effective server type.

### Approach B — rewrite SQL around `CAST` or `DECLARE`

Rejected. Rewriting would require parsing arbitrary T-SQL, would alter query
text and plan-cache keys, could mishandle comments or nested expressions, and
would create a second SQL-injection boundary.

### Approach C — improve only Python value inference

Rejected. It would fix `bool`, temporal values, `Decimal`, and UUID but leave
`Parameter.sql_type`, typed `None`, lengths, precision, scale, and plan
stability broken.

## Public Python descriptor

The compatible constructor grows keyword-only metadata:

```python
Parameter(
    value,
    sql_type=None,
    *,
    direction="INPUT",
    precision=None,
    scale=None,
    length=None,
    expanded=None,
)
```

The descriptor exposes read-only properties:

```text
value
sql_type
direction
precision
scale
length
expanded
is_expanded    # compatibility alias
```

`sql_type` is the canonical effective declaration, for example
`INT`, `DECIMAL(19,4)`, `NVARCHAR(40)`, `VARCHAR(MAX)`, `TIME(3)`, or
`DATETIMEOFFSET(7)`. Canonicalization intentionally replaces the old behavior
of preserving an arbitrary string which was never applied.

`precision`, `scale`, and `length` may be supplied either in the declaration
or as keyword metadata. Redundant values are allowed only when identical.
Conflicting metadata is rejected during construction.

`length` accepts a positive integer or the case-insensitive string `"MAX"`.
`expanded=None` retains automatic iterable detection. `expanded=True`
requires an expandable iterable. `expanded=False` rejects an expandable
value until a future scalar collection or table-valued-parameter API exists.

`direction` is canonicalized to:

```text
INPUT
OUTPUT
INPUT_OUTPUT
RETURN_VALUE
```

The descriptor preserves every direction for the future stored-procedure
result API. The current `query`, `execute`, transaction and batch APIs accept
only `INPUT`; attempting to execute another direction raises a deterministic
local error. Output values and return status remain part of the separate
result-set/stored-procedure feature and are not falsely advertised here.

`Parameters.add()` and `Parameters.set()` gain the same keyword-only
metadata. Passing a `Parameter` inside either `Parameters(...)` or an ordinary
positional list preserves its descriptor. `Parameters.to_list()` remains a
compatibility inspection method returning raw values, but wire conversion no
longer calls it.

## Safe SQL type grammar

Only the following closed set is accepted:

| Family | Accepted declarations |
|---|---|
| Boolean | `BIT` |
| Integer | `TINYINT`, `SMALLINT`, `INT`, `BIGINT` |
| Floating point | `REAL`, `FLOAT`, `FLOAT(n)` for `1 <= n <= 53` |
| Exact numeric | `DECIMAL(p,s)`, `NUMERIC(p,s)` with `1 <= p <= 38`, `0 <= s <= p` |
| ANSI text | `CHAR(n)`, `VARCHAR(n)`, `VARCHAR(MAX)` |
| Unicode text | `NCHAR(n)`, `NVARCHAR(n)`, `NVARCHAR(MAX)` |
| Binary | `BINARY(n)`, `VARBINARY(n)`, `VARBINARY(MAX)` |
| Identity | `UNIQUEIDENTIFIER` |
| Temporal | `DATE`, `TIME(s)`, `DATETIME`, `SMALLDATETIME`, `DATETIME2(s)`, `DATETIMEOFFSET(s)` |
| XML | `XML` |

Temporal scale is from 0 through 7. Fixed character and binary types require
an explicit length. ANSI and binary limited lengths are at most 8,000;
Unicode limited lengths are at most 4,000 characters. `MAX` is valid only for
variable-length types.

Compatibility defaults are explicit and stable:

```text
FLOAT       -> FLOAT(53)
VARCHAR     -> VARCHAR(8000)
NVARCHAR    -> NVARCHAR(4000)
VARBINARY   -> VARBINARY(8000)
TIME        -> TIME(7)
DATETIME2   -> DATETIME2(7)
DATETIMEOFFSET -> DATETIMEOFFSET(7)
```

`DECIMAL` and `NUMERIC` require precision and scale because silently applying
SQL Server's `(18,0)` default would destroy fractional data. `CHAR`, `NCHAR`,
and `BINARY` require length for the same reason.

The parser accepts case and surrounding whitespace but no schema names,
brackets, quoted identifiers, collation clauses, comments, semicolons, nested
parentheses, expressions, user-defined aliases, or arbitrary suffixes. It
constructs an enum; the original text never reaches `@params`.

`MONEY`, `SMALLMONEY`, legacy LOB types, `SQL_VARIANT`, spatial types,
hierarchyid, CLR UDTs, and table-valued parameters are rejected explicitly.
The current Tiberius money representation passes through `f64`, so claiming
exact typed money input would violate the precision contract. Exact monetary
applications must use `DECIMAL(19,4)` until a separate fixed-point money
feature is implemented.

## Inferred Python input mapping

Raw values without a `Parameter` descriptor use this stable mapping:

| Python value | SQL/TDS type |
|---|---|
| `None` | legacy untyped `TINYINT NULL` |
| `bool` | `BIT` |
| `int` | `BIGINT`, with signed 64-bit validation |
| `float` | `FLOAT(53)`, finite values only |
| `Decimal` | exact minimal `NUMERIC(p,s)` |
| `str` | `NVARCHAR(4000)` or `NVARCHAR(MAX)` |
| `bytes`, `bytearray`, `memoryview` | `VARBINARY(8000)` or `VARBINARY(MAX)` |
| `date` | `DATE` |
| naive `time` | `TIME(7)` |
| naive `datetime` | `DATETIME2(7)` |
| aware `datetime` | `DATETIMEOFFSET(7)` |
| `uuid.UUID` | `UNIQUEIDENTIFIER` |

Raw Python integers intentionally remain `BIGINT`. Choosing a different
integer type from the runtime magnitude would fragment the plan cache as
values cross boundaries. Applications that require `INT`, `SMALLINT`, or
`TINYINT` use an explicit descriptor.

Raw strings intentionally remain Unicode. ANSI encoding depends on the
negotiated SQL Server collation; applications requesting ANSI semantics use
an explicit `VARCHAR` or `CHAR` descriptor.

Legacy `TypedNull` remains supported. `Parameter(None, sql_type=...)` is the
preferred precise API because it can also preserve length, precision and
scale.

## Exact decimal conversion

Python `Decimal.as_tuple()` is the authoritative input. Conversion does not
pass through `float` or locale-sensitive text.

The converter:

1. rejects NaN, sNaN and infinities;
2. constructs a checked signed `i128` coefficient;
3. expands positive exponents exactly;
4. supports scale 0 through 38 and up to 38 total digits;
5. derives raw precision as `max(coefficient_digits, scale, 1)`;
6. for an explicit `(p,s)`, rescales with SQL Server-compatible half-away-
   from-zero rounding;
7. checks the rounded coefficient against `10^p`;
8. never calls a Tiberius path that can panic because scale metadata differs.

The vendored `Numeric` implementation must accept scale 38, compute precision
correctly for values such as `1E-38`, and avoid the current `1 + scale`
overstatement for sub-unit values. Decode and encode tests cover
`DECIMAL(38,38)`, zero, positive and negative boundaries.

SQL-to-Python numeric values remain Python `Decimal` with their scale
preserved.

## Temporal conversion

Aware datetime detection occurs before naive datetime extraction.

For `DATETIMEOFFSET`:

- `utcoffset()` must be non-null, whole-minute and between `-14:00` and
  `+14:00`;
- the UTC instant and original offset are both encoded;
- local and UTC date ranges are validated;
- scale reduction uses integer arithmetic and SQL Server-compatible rounding;
- day rollover is handled explicitly rather than truncated.

Naive `time` and `datetime` map to scale 7 by default. Aware Python `time`
objects are rejected because SQL Server `TIME` carries no offset.

Explicit `TIME`, `DATETIME2`, and `DATETIMEOFFSET` scales from 0 through 7
use exact integer rescaling. `DATETIME` uses its 1/300-second resolution and
range beginning at 1753-01-01. `SMALLDATETIME` uses its documented minute
boundary—`29.998` seconds rounds down and `29.999` seconds rounds up—and its
1900-01-01 through 2079-06-06 range. Overflow after rounding is a local
conversion error.

## UUID symmetry

Input accepts a real Python `uuid.UUID` for inferred parameters. An explicit
`UNIQUEIDENTIFIER` accepts `uuid.UUID` and a canonical parseable UUID string.
Invalid strings are rejected locally.

SQL `UNIQUEIDENTIFIER` output becomes a Python `uuid.UUID`, replacing the
current asymmetric string output. The UUID class is cached under the GIL in
the same manner as `decimal.Decimal`.

## ANSI, Unicode and binary lengths

The Tiberius connection context records the latest `SQLCOLLATION` environment
change from login or database changes. Explicit `CHAR` and `VARCHAR` values
use that collation's encoding. An unavailable or unsupported code page
returns a conversion/protocol error; it never panics or performs a lossy
replacement.

Length checks use encoded bytes for ANSI types, UTF-16 code units for Unicode
types, and bytes for binary types. Supplementary Unicode characters therefore
consume two NVARCHAR characters, matching SQL Server. Limited values that do
not fit are rejected before the RPC is sent; they are never silently
truncated.

`MAX` uses PLP encoding. Existing raw-size transitions remain compatible:
4,000 Unicode characters and 8,000 binary bytes stay limited, while larger
values use `MAX`.

## Vendored Tiberius extension

The vendored crate gains a public, validated `SqlParameterType` enum and a
defaulted `ToSql::sql_parameter_type()` method:

```rust
pub trait ToSql: Send + Sync {
    fn to_sql(&self) -> ColumnData<'_>;

    fn sql_parameter_type(&self) -> Option<SqlParameterType> {
        None
    }
}
```

Existing Tiberius callers remain source-compatible because the method has a
default. FastMssql's `FastParameter` supplies `Some(type)` only for explicit
descriptors or inference variants requiring corrected metadata.

The RPC path carries:

```text
value: ColumnData
declaration: canonical enum-derived text
type_info: enum-derived TDS TypeInfo
```

`RpcParam` encodes explicit `TYPE_INFO` before encoding the compatible value
against that metadata. Inferred callers continue to use the existing
`ColumnData::type_name()` and self-describing encoding.
For `DATE`, `DATEN` `TYPE_INFO` is exactly the one-byte type identifier; the
three-byte payload length is emitted once as part of the value rather than
being duplicated in the metadata.

The context stores both the current session collation and the initial LOGIN7
collation. It updates the current value on
`TokenEnvChange::SqlCollation`. Because `RESETCONNECTION` restores the login
environment before processing the request carrying that flag, pool reset
restores the initial collation locally before deriving that request's
parameter `TYPE_INFO`; a later server `ENVCHANGE` remains authoritative.
Every LOGIN7 advertises TDS 7.4 `UTF8_SUPPORT`; the corresponding
`FEATUREEXTACK` is decoded without panics and retained in context. A collation
with `fUTF8` uses UTF-8 rather than its legacy LCID code page, and explicit
ANSI parameters fail locally if SQL Server did not acknowledge UTF-8 support.

All enum variants validate their range before declaration or encoding.
No public constructor accepts a raw declaration string. The local patch is
documented in `vendor/tiberius/FASTMSSQL_PATCH.md`.

## FastMssql conversion architecture

`FastParameter` becomes an owned descriptor containing:

```text
value: FastParameterValue
sql_type: Option<SqlParameterType>
```

`FastParameterValue` owns strings, bytes, exact numeric values, UUIDs and
temporal values, so no Python object or GIL-bound reference crosses an
`await`.

`convert_parameters_to_fast()`:

1. reads `Parameters.positional` directly rather than calling `to_list()`;
2. rejects named parameters with the existing explicit contract;
3. recognizes `Parameter` objects inside either `Parameters` or a plain list;
4. validates direction before execution;
5. expands only the descriptor's value;
6. applies the same parsed type to each expanded child;
7. retains the 2,098-user-parameter ceiling during bounded generator
   expansion;
8. reports the zero-based parameter index and safe reason without value
   content.

The owned Rust vector is created while holding the GIL. Existing async
operations then borrow only Rust values. No global mutable conversion state,
blocking I/O, Python callback, or lock is introduced on the async path.

Bulk insert continues to accept raw row values rather than `Parameter`
descriptors. It gains the corrected inferred scalar types and updates
all-null column repair for every new `FastParameterValue` variant. Explicit
bulk column metadata remains a separate native bulk-copy design.

## Errors and privacy

Invalid descriptor syntax or contradictory metadata raises `ValueError`
during `Parameter` construction.

Value/type incompatibility, overflow, precision loss, unavailable encoding,
non-finite floats, unsupported Python values, and non-input execution raise
`ConversionError` with safe structured attributes where applicable:

```text
message
parameter_index
sql_type
reason
retryable = False
wire_sent = False
connection_discarded = False
outcome_unknown = False
```

Structured parameter conversion errors are completed before the encoded RPC
payload is handed to the transport. They therefore leave the TDS stream and
an active transaction synchronized; the connection can continue without
silently replaying the failed operation.
Batch preflight preserves the original exception class and these attributes,
adds the zero-based `batch_index`, and prefixes only the safe exception
message with `Batch item N parameter validation failed`.

Messages may contain the canonical closed SQL declaration and Python type
name. They never contain `repr(value)`, string/binary contents, decimal
digits, UUID content, passwords, SQL-auth credentials, or arbitrary original
type text.

`Parameter.__repr__()` becomes metadata-only:

```text
Parameter(sql_type='INT', direction='INPUT', expanded=False, value=<redacted>)
```

For an inferred type, `sql_type=None`. Iterable contents, scalar values and
custom `__repr__` output are never evaluated by `Parameter.__repr__()`.
`Parameters.__repr__()` continues to expose counts only.

## Test design

Existing limitation tests become desired contracts:

- `PARAM-003` verifies raw `bool` is `BIT` through
  `SQL_VARIANT_PROPERTY`;
- `PARAM-006` round-trips exact `Decimal` values, including scale 38;
- `PARAM-012` proves naive `DATETIME2(7)` and aware
  `DATETIMEOFFSET(7)` with offset retention;
- `PARAM-013` round-trips Python `time`;
- `PARAM-014` round-trips Python `UUID`;
- `PARAM-015` proves `Parameter.sql_type` changes the effective type.

The SQL-auth specification gains:

- `PARAM-025`: every supported explicit scalar declaration and typed null
  reports the intended base type, `DATE` remains byte-aligned, an empty XML
  value leaves a following RPC parameter aligned, and the exact
  `SMALLDATETIME` rounding boundary is preserved;
- `PARAM-026`: precision, scale, max length, rounding and overflow;
- `PARAM-027`: legacy and `_UTF8` ANSI collation, Unicode code-unit and binary
  length behavior; the `_UTF8` assertion connects with the temporary database
  in LOGIN7 instead of relying on `USE` state that a pooled checkout might not
  preserve, and a one-session contamination/reset assertion proves that the
  first reset RPC uses the initial login collation;
- `PARAM-028`: safe parser rejects malformed, injected and unsupported
  declarations before network I/O;
- `PARAM-029`: typed iterable expansion preserves the type for every child;
- `PARAM-030`: descriptor fields, direction rejection and `Parameters`
  compatibility;
- `PARAM-031`: metadata-only `repr` and conversion error redaction;
- `PARAM-032`: connection, transaction and batch paths use the same typed
  conversion;
- `PARAM-033`: concurrent typed operations preserve values and effective
  types without exceeding configured pool/session bounds.

`TYPE-004`, `TYPE-010`, `TYPE-012`, and `TYPE-013` are extended for symmetric
round trips. Tiberius unit tests validate declaration text, enum ranges,
collation context updates, exact `DATE` and scaled temporal RPC `TYPE_INFO`,
numeric scale 38, and temporal boundary encoding.

The RED evidence for each subproblem is recorded on its own test branch
before its corresponding fix or feature branch.

## Branch topology

The subsystem is implemented as a sequence. Every arrow means strict Git
ancestry from the previous reviewed branch:

```text
docs/typed-sql-parameters-design
  -> test/parameter-repr-redaction
     -> fix/parameter-repr-redaction
        -> test/bool-parameter-wire-type
           -> fix/bool-parameter-wire-type
              -> test/tiberius-numeric-scale-38
                 -> fix/tiberius-numeric-scale-38
                    -> test/decimal-parameter-input
                       -> feat/decimal-parameter-input
                          -> test/time-parameter-input
                             -> feat/time-parameter-input
                                -> test/datetimeoffset-parameter-preservation
                                   -> fix/datetimeoffset-parameter-preservation
                                      -> test/uuid-parameter-symmetry
                                         -> feat/uuid-parameter-symmetry
                                            -> test/typed-parameter-descriptor
                                               -> feat/typed-parameter-descriptor
                                                  -> test/sql-auth-validation
```

Each test branch changes only tests/spec contract required to demonstrate its
RED state. Each fix/feature branch changes only the implementation and
documentation directly needed for its focused contract. Intermediate
branches may leave later, not-yet-implemented tests absent; every branch must
remain buildable and its pre-existing gates green.

The final cumulative merge is technical and history-preserving. A separate
status branch updates the live audit only after the exact merged SHA passes
all required gates.

## Acceptance gates

Each subproblem requires:

```text
focused unit/contract test before implementation       RED
focused test after implementation                     PASS
pre-existing Rust tests                               PASS
affected Python contract tests                        PASS
git diff --check                                      PASS
fmt and Clippy -D warnings                            PASS
privacy sentinel scan                                 PASS
```

The final exact cumulative merge requires:

```text
cargo test --locked                                   PASS
cargo fmt --check                                     PASS
cargo clippy --all-targets --all-features -- -D warnings
strict SQL-auth suite                                 PASS, zero skips
async/framework/resilience/load lanes                 PASS, zero skips
original-local-regression lane                        PASS
matrix contract                                       PASS
typed concurrent load of at least 1,000 operations   PASS
complete scripts/sql_auth/run_all.sh                  PASS
wheel build and isolated install contracts            PASS
Linux/macOS/Windows hosted raw Cargo/wheel gate        PASS
RustSec hosted gate                                   PASS
secret/privacy scans                                  PASS
fork branch SHA parity                                PASS
upstream push URL                                     DISABLED
```

Observed counts from the final run are authoritative. Documentation must not
predeclare forecast counts as evidence.

## Live-audit update

Only after final verification, a dedicated
`docs/typed-sql-parameters-status` branch updates
`docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` with:

- original measured wire-type evidence;
- root causes;
- every test/fix/feature branch and commit SHA;
- supported declaration table and deliberate exclusions;
- RED/GREEN evidence;
- exact SQL-auth, regression, load, Rust and hosted counts;
- exact Docker load metrics;
- hosted GitHub Actions links;
- remaining result-set/output-parameter and native-bulk work.

The audit must distinguish resolved input-parameter work from still-open
stored-procedure output parameters, multiple result sets, streaming,
table-valued parameters, money input, and native bulk metadata.

## Non-goals

This design does not:

- implement stored-procedure OUT/INOUT values or return status transport;
- implement multiple result sets or a true async `ResultStream`;
- implement table-valued parameters or native bulk-copy column descriptors;
- make named Python parameters executable;
- support arbitrary SQL aliases, user-defined types, collations in the type
  string, spatial types, hierarchyid, CLR UDTs, or SQL_VARIANT;
- claim exact MONEY/SMALLMONEY input through the current `f64` vendor path;
- infer a narrower integer type from each runtime magnitude;
- change pool size, transaction leasing, operation timeouts or lifecycle
  semantics;
- add a sync ODBC fallback;
- bump package version or publish a wheel/release;
- publish a Tiberius fork;
- write, push, open a pull request or publish anything in the original
  FastMssql repository.

## Self-review record

The design was checked against `parameter_conversion.rs`,
`py_parameters.rs`, every connection/transaction/batch call site, the Python
stubs, strict parameter/type tests, Tiberius `Client::rpc_perform_query`,
`RpcParam`, `ColumnData`, `TypeInfo`, `Numeric`, temporal encoders, collation
environment tokens, and the current live audit.

Corrections made during self-review:

1. A value-only fix was rejected because it would leave `sql_type`
   decorative.
2. SQL rewriting was rejected because it creates parsing, injection and
   plan-cache hazards.
3. Raw integers remain BIGINT to avoid value-dependent plan types; explicit
   descriptors provide narrower integers.
4. ANSI metadata uses negotiated collation rather than a hard-coded code
   page or Unicode-to-ANSI lossy conversion.
5. `DECIMAL` without `(p,s)` is rejected to prevent accidental `(18,0)`
   rounding.
6. Scale 38 is included because SQL Server supports it and the current
   vendored `Numeric` implementation does not.
7. Temporal rescaling uses integer rounding and rollover rather than the
   current floating-point truncation path.
8. UUID output changes to `uuid.UUID` so input/output behavior is actually
   symmetric.
9. Non-input directions are carried but explicitly rejected until the result
   subsystem can return their values.
10. Typed metadata is propagated through plain lists and expansions, not only
    through `Parameters`.
11. Repr and errors never invoke or embed arbitrary value representations.
12. The work is split into independent RED and fix/feature branch pairs so
    the history identifies each resolved audit problem.
13. MONEY/SMALLMONEY are explicitly excluded rather than falsely presented
    as exact while Tiberius decodes/encodes them through floating point.
14. Final evidence requires real MSSQL and concurrent load, while hosted
    operating-system gates remain database-independent.
15. Local RPC encoding failure leaves the connection's response state
    unchanged and is classified as reusable, so deterministic bad input does
    not poison an active transaction.

The specification contains no placeholder, swallowed exception, unbounded
input path, arbitrary declaration concatenation, or authorization for an
original-repository write.
