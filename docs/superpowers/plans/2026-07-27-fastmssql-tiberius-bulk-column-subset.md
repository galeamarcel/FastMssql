# FastMssql Vendored Tiberius Bulk Column Subset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe ordered-column-subset entry point to FastMssql's vendored
Tiberius bulk API without changing the existing `Client::bulk_insert(table)`
contract.

**Architecture:** A private pure helper owns the closed identifier grammar,
metadata validation and total SQL type declaration formatting. The public
client method owns only TDS request sequencing and returns the existing
`BulkLoadRequest`. Unit contracts exercise every helper branch; a dedicated
SQL-auth integration target proves server metadata, TDS row order, defaults,
NULLs and rejection behavior.

**Tech Stack:** Rust 2021 for vendored Tiberius 0.12.3, Tokio, TDS 7.3/7.4
metadata, SQL Server 2022 Docker with SQL authentication, Cargo fmt/Clippy/test,
Git worktrees and code-review-graph.

## Global Constraints

- Base the documentation branch on
  `151c69efb60a787f0f8bbee3beb73ae10a947dd4`.
- Use branch topology
  `docs/tiberius-bulk-column-subset` ->
  `test/tiberius-bulk-column-subset` ->
  `feat/tiberius-bulk-column-subset` ->
  `docs/tiberius-bulk-column-subset-status`.
- The implementation branch must descend from the observed RED commit.
- Push only to `https://github.com/galeamarcel/FastMssql.git`; the `upstream`
  push URL must remain exactly `DISABLED`.
- Preserve `Client::bulk_insert(table)` source and behavior.
- The new API is exactly
  `Client::bulk_insert_columns(&mut self, table: &str, columns: &[&str])`.
- Input names are raw identifiers. Table grammar is one through three
  non-empty dot-separated parts; every column is one literal identifier part.
- Each identifier part is non-empty, NUL-free and at most 128 UTF-16 code
  units. Closing brackets are doubled before bracket quoting.
- Validate all raw input before the first connection flush or request packet.
- Require one non-empty requested list that fits the TDS `u16` column count.
- Require one metadata result set, exact count, exact byte-for-byte name/order
  and no canonical duplicates.
- Require `Updateable`; reject identity, computed, CLR, sparse-column-set,
  encrypted, hidden, rowversion/non-updateable and unsupported metadata.
- New-path metadata declaration code must contain no panic, `unwrap`,
  `expect`, `todo!()` or `unreachable!()`.
- No SQL text, identifier, row value or credential is added to logs/tracing.
- Keep displayed package version `0.7.7`; update `VERSION.md` for every commit.
- Do not create a Tiberius fork, PR, release, wheel publication or upstream
  push.

---

### Task 1: Publish the focused design and executable plan

**Files:**
- Create:
  `docs/superpowers/specs/2026-07-27-fastmssql-tiberius-bulk-column-subset-design.md`
- Create:
  `docs/superpowers/plans/2026-07-27-fastmssql-tiberius-bulk-column-subset.md`
- Modify: `VERSION.md`

**Interfaces:**
- Consumes: approved umbrella design
  `docs/superpowers/specs/2026-07-27-fastmssql-batch-bulk-design.md`.
- Produces: exact API, identifier grammar, error taxonomy, type mapping,
  branch topology and gates used by every later task.

- [ ] **Step 1: Verify the graph and clean base**

Run:

```bash
git status --short --branch
git rev-parse HEAD
git remote get-url --push origin
git remote get-url --push upstream
```

Expected: clean branch at `151c69e...`, origin is the `galeamarcel` fork and
upstream is `DISABLED`.

- [ ] **Step 2: Self-review both documents**

Run:

```bash
rg -n 'T''BD|T''ODO|imple''ment later|fi''ll in|appropri''ate error|sim''ilar to' \
  docs/superpowers/specs/2026-07-27-fastmssql-tiberius-bulk-column-subset-design.md \
  docs/superpowers/plans/2026-07-27-fastmssql-tiberius-bulk-column-subset.md
git diff --check
```

Expected: `rg` has no matches and `git diff --check` is silent.

- [ ] **Step 3: Commit and push only the documentation branch**

```bash
git add \
  docs/superpowers/specs/2026-07-27-fastmssql-tiberius-bulk-column-subset-design.md \
  docs/superpowers/plans/2026-07-27-fastmssql-tiberius-bulk-column-subset.md \
  VERSION.md
git commit -m "docs: plan safe Tiberius bulk column subsets"
git push -u origin docs/tiberius-bulk-column-subset
```

Verify the remote ref equals local `HEAD` with `git ls-remote`.

### Task 2: Create the RED branch and pure helper contracts

**Files:**
- Create: `vendor/tiberius/src/client/bulk_columns_tests.rs`
- Modify: `vendor/tiberius/src/client.rs`
- Modify: `vendor/tiberius/FASTMSSQL_PATCH.md`
- Modify: `VERSION.md`

**Interfaces:**
- Consumes: private module name `client::bulk_columns`.
- Produces these test-required helper interfaces:

```rust
pub(super) struct BulkInsertColumns;

impl BulkInsertColumns {
    pub(super) fn new(table: &str, columns: &[&str]) -> crate::Result<Self>;
    pub(super) fn metadata_query(&self) -> String;
    pub(super) fn validate_metadata(
        &self,
        resultset_count: usize,
        columns: Option<Vec<MetaDataColumn<'static>>>,
    ) -> crate::Result<Vec<MetaDataColumn<'static>>>;
    pub(super) fn insert_query(
        &self,
        columns: &[MetaDataColumn<'_>],
    ) -> crate::Result<String>;
}

pub(super) fn checked_bulk_type_declaration(
    ty: &TypeInfo,
) -> crate::Result<String>;
```

- [ ] **Step 1: Create the isolated RED worktree**

From the repository root:

```bash
git worktree add \
  .worktrees/test-tiberius-bulk-column-subset \
  -b test/tiberius-bulk-column-subset \
  docs/tiberius-bulk-column-subset
```

Expected: the new branch starts at the documentation-plan commit and is clean.

- [ ] **Step 2: Add the test-only module declaration**

Add beside the existing client submodules:

```rust
#[cfg(test)]
mod bulk_columns_tests;
```

Do not add `mod bulk_columns;` on the RED branch.

- [ ] **Step 3: Add identifier and query tests**

In `bulk_columns_tests.rs`, construct `BulkInsertColumns` and assert:

```rust
#[test]
fn identifiers_are_closed_and_metadata_sql_preserves_order() {
    let target = BulkInsertColumns::new(
        "db]name.dbo.order",
        &["select", "amount]net", "literal.dot"],
    )
    .expect("valid raw identifiers must be accepted");

    assert_eq!(
        target.metadata_query(),
        "SELECT TOP (0) [select], [amount]]net], [literal.dot] \
FROM [db]]name].[dbo].[order]"
    );
}
```

Add table-driven invalid cases for:

```text
"", ".table", "schema.", "db..table", "a.b.c.d", "nul\0part",
129 UTF-16 code units, empty columns, empty/NUL/overlong column,
exact duplicate columns, and 65,536 requested columns.
```

Every case must match `Error::BulkInput(_)` and must not assert the complete
human-readable message.

- [ ] **Step 4: Add metadata order and writability tests**

Use this metadata constructor:

```rust
fn metadata_column(
    name: &str,
    flags: BitFlags<ColumnFlag>,
    ty: TypeInfo,
) -> MetaDataColumn<'static> {
    MetaDataColumn {
        base: BaseMetaDataColumn { flags, ty },
        col_name: Cow::Owned(name.to_owned()),
    }
}
```

Require:

- result-set count exactly one;
- metadata count exactly equals requested count;
- each metadata name exactly matches its requested position;
- duplicate canonical metadata names are rejected;
- `Updateable` is required;
- `Identity`, `Computed`, `FixedLenClrType`, `SparseColumnSet`, `Encrypted`
  and `Hidden` are rejected even with `Updateable`;
- `UpdateableUnknown` alone is rejected;
- `Nullable`, `CaseSensitive` and `Key` remain accepted.

Count/order mismatch must match `Error::Protocol(_)`. Flag failures must match
`Error::BulkInput(_)`.

- [ ] **Step 5: Add exhaustive declaration tests**

Table-drive every accepted declaration:

```text
Int1 tinyint             Bit bit
Int2 smallint            Int4 int
Datetime4 smalldatetime  Float4 real
Money money              Datetime datetime
Float8 float             Money4 smallmoney
Int8 bigint

Bitn(1) bit
Guid(16) uniqueidentifier
Intn(1|2|4|8) tinyint|smallint|int|bigint
Floatn(4|8) real|float
Money(4|8) smallmoney|money
Datetimen(4|8) smalldatetime|datetime
Daten(3) date
Timen(0|3|7) time(0|3|7)
Datetime2(0|3|7) datetime2(0|3|7)
DatetimeOffsetn(0|3|7) datetimeoffset(0|3|7)
BigVarBin(8|8000|0xffff) varbinary(8)|varbinary(8000)|varbinary(max)
BigBinary(8|8000) binary(8)|binary(8000)
BigVarChar(8|8000|0xffff) varchar(8)|varchar(8000)|varchar(max)
BigChar(8|8000) char(8)|char(8000)
NVarchar(20|8000|0xffff) nvarchar(10)|nvarchar(4000)|nvarchar(max)
NChar(20|8000) nchar(10)|nchar(4000)
Decimaln(38,38,size=17) decimal(38,38)
Numericn(19,4,size=9) numeric(19,4)
TypeInfo::Xml xml
```

Wrap rejected cases in `catch_unwind` and require `BulkInput`, not a panic:

```text
FixedLen Null; invalid Bitn/Guid/Intn/Floatn/Money/Datetimen/Daten widths;
temporal scale 8; zero/oversized/eccentric Unicode widths; MAX fixed types;
Text, NText, Image, Udt, SSVariant; misplaced Decimaln/Numericn/Xml;
precision 0 or 39; scale > precision; incorrect numeric storage size.
```

- [ ] **Step 6: Record RED patch and version status**

Describe the required method as not yet implemented in
`vendor/tiberius/FASTMSSQL_PATCH.md`. Add a `VERSION.md` section naming the
RED branch, desired API and expected compile failure. Do not claim runtime
support.

### Task 3: Add the RED real SQL-auth target and observe failure

**Files:**
- Create: `vendor/tiberius/tests/bulk_column_subset_sql_auth.rs`
- Modify: `scripts/sql_auth/run_all.sh`
- Modify: `VERSION.md`

**Interfaces:**
- Consumes: public method
  `Client::bulk_insert_columns(table, columns)`.
- Produces: five direct Tiberius SQL-auth contracts, named `TIB-BULK-001`
  through `TIB-BULK-005`.

- [ ] **Step 1: Add shared SQL-auth helpers**

Copy the repository's existing `required_environment`,
`connect_sql_auth(application_name)`, `drain_batch`, `cleanup_batch` and
`smoke_query` patterns. Add only this local identifier helper:

```rust
fn quote_identifier(value: &str) -> String {
    format!("[{}]", value.replace(']', "]]"))
}
```

Fixture names must be generated from `Uuid::new_v4().simple()` and must never
contain credentials.

- [ ] **Step 2: Add `TIB-BULK-001`**

Create a table with:

```sql
id INT IDENTITY PRIMARY KEY,
first_value INT NOT NULL,
second_value NVARCHAR(40) NOT NULL,
defaulted INT NOT NULL CONSTRAINT <unique-name> DEFAULT (41),
nullable_value INT NULL
```

Start:

```rust
let mut request = client
    .bulk_insert_columns(
        &format!("dbo.{raw_table}"),
        &["second_value", "first_value", "nullable_value"],
    )
    .await?;
request
    .send(("ordered", 7_i32, Option::<i32>::None).into_row())
    .await?;
let result = request.finalize().await?;
```

Require total row count 1 and query back `(7, "ordered", 41, NULL)`.

- [ ] **Step 3: Add `TIB-BULK-002`**

Create a target table and columns whose raw names include `]` and SQL reserved
words, plus a separate guard table. Pass raw names to
`bulk_insert_columns`. Require the inserted row to round-trip and the guard
table to remain present with its sentinel row.

- [ ] **Step 4: Add `TIB-BULK-003`**

Create one table containing identity, base, computed and rowversion columns.
Request each restricted column separately and require `Error::BulkInput`.
After every rejection, run `smoke_query` on the same client. Query the table
and require zero inserted rows.

- [ ] **Step 5: Add `TIB-BULK-004`**

Create a table with `SQL_VARIANT`. Install the same scoped panic-hook pattern
used by `token_safety_sql_auth.rs`, wrap the method future with
`AssertUnwindSafe(...).catch_unwind()`, and require:

```rust
!panic_hook_invoked
matches!(driver_error, Error::Protocol(_))
```

Drop the uncertain client and prove recovery with a new smoke client.

- [ ] **Step 6: Add `TIB-BULK-005`**

For each malformed identifier from the unit contract, call the public method,
require `Error::BulkInput` and run a smoke query on the same client. This
proves input validation occurs before any protocol transition.

- [ ] **Step 7: Register the SQL-auth target**

After the existing Tiberius token-safety lane in `run_all.sh`, add:

```bash
record tiberius-bulk-column-subset-sql-auth \
  cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test bulk_column_subset_sql_auth -- --test-threads=1
```

- [ ] **Step 8: Observe the correct RED**

Run:

```bash
env CARGO_TARGET_DIR=/private/tmp/fastmssql-tiberius-bulk-red-target \
  cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --lib
```

Expected: compilation fails because `client::bulk_columns` does not exist.

Then run the new integration target with the SQL-auth env loaded. Expected:
compilation fails because `Client::bulk_insert_columns` does not exist.

Do not accept a dependency, syntax or fixture failure as RED evidence.

- [ ] **Step 9: Commit and publish the RED branch**

```bash
git add \
  vendor/tiberius/src/client.rs \
  vendor/tiberius/src/client/bulk_columns_tests.rs \
  vendor/tiberius/tests/bulk_column_subset_sql_auth.rs \
  vendor/tiberius/FASTMSSQL_PATCH.md \
  scripts/sql_auth/run_all.sh \
  VERSION.md
git commit -m "test: require safe Tiberius bulk column subsets"
git push -u origin test/tiberius-bulk-column-subset
```

Verify remote and local SHAs are identical.

### Task 4: Implement the pure checked bulk-column helper

**Files:**
- Create: `vendor/tiberius/src/client/bulk_columns.rs`
- Modify: `vendor/tiberius/src/client.rs`

**Interfaces:**
- Consumes: the exact helper signatures from Task 2.
- Produces: owned, prevalidated metadata SQL; checked ordered metadata; safe
  `INSERT BULK` SQL.

- [ ] **Step 1: Create the feature worktree from RED**

```bash
git worktree add \
  .worktrees/feat-tiberius-bulk-column-subset \
  -b feat/tiberius-bulk-column-subset \
  test/tiberius-bulk-column-subset
```

Verify:

```bash
git merge-base --is-ancestor \
  test/tiberius-bulk-column-subset \
  feat/tiberius-bulk-column-subset
```

Expected: exit 0.

- [ ] **Step 2: Declare the production module**

Add to `client.rs`:

```rust
mod bulk_columns;
use bulk_columns::BulkInsertColumns;
```

Keep the `#[cfg(test)] mod bulk_columns_tests;` declaration.

- [ ] **Step 3: Implement the closed identifier grammar**

In `bulk_columns.rs`, add:

```rust
const MAX_IDENTIFIER_UTF16: usize = 128;
const MAX_TABLE_PARTS: usize = 3;

fn quote_identifier_part(
    value: &str,
    kind: &'static str,
    index: usize,
) -> crate::Result<String>;

fn quote_table(table: &str) -> crate::Result<String>;
```

Count UTF-16 units with `value.encode_utf16().count()`. Reject empty, NUL,
overlong and invalid table-part count with `Error::BulkInput`. Escape each `]`
by doubling it and wrap the result in `[...]`.

`BulkInsertColumns::new` must validate the entire input, reject exact
duplicates with `HashSet`, retain owned requested names, retain quoted columns
and build no SQL by raw interpolation.

- [ ] **Step 4: Implement metadata validation**

`validate_metadata` must:

1. require `resultset_count == 1`;
2. require `Some(columns)` and exact length;
3. zip requested and returned columns in order;
4. compare `col_name.as_ref()` byte-for-byte;
5. reject duplicate returned names with a `HashSet`;
6. return the original ordered vector.

Use `Error::Protocol` for phases 1–4 and `Error::Protocol` for canonical
duplicates because they contradict the server-result contract. Do not include
names in messages.

- [ ] **Step 5: Implement flag checks**

Before formatting a declaration:

```rust
if !flags.contains(ColumnFlag::Updateable) {
    return Err(Error::BulkInput("bulk column is not writable".into()));
}
for restricted in [
    ColumnFlag::Identity,
    ColumnFlag::Computed,
    ColumnFlag::FixedLenClrType,
    ColumnFlag::SparseColumnSet,
    ColumnFlag::Encrypted,
    ColumnFlag::Hidden,
] {
    if flags.contains(restricted) {
        return Err(Error::BulkInput(
            "bulk column carries a restricted metadata flag".into(),
        ));
    }
}
```

The implementation may include a zero-based metadata index in the message,
but never the name.

- [ ] **Step 6: Implement the total type formatter**

Match every `TypeInfo`, `FixedLenType` and `VarLenType` variant explicitly.
Use the exact accepted mapping from Task 2. Add private checked helpers for
finite byte length, Unicode byte length, temporal scale and numeric storage.

For numeric storage:

```rust
let expected_size = match precision {
    1..=9 => 5,
    10..=19 => 9,
    20..=28 => 13,
    29..=38 => 17,
    _ => return Err(bulk_input("invalid bulk numeric precision")),
};
```

Return a stable `BulkInput` classification for every invalid match. Do not use
a wildcard arm that can hide a newly added enum variant.

- [ ] **Step 7: Implement checked INSERT BULK SQL**

`insert_query` must call the flag validator and formatter for every column,
quote the server-returned name with the single-part identifier helper and
join declarations in order:

```text
INSERT BULK [schema].[table]
([second_value] nvarchar(40), [first_value] int)
```

No code path may call `format!("{column}")` or the existing
`MetaDataColumn::fmt`.

- [ ] **Step 8: Run the unit contracts**

Run the vendored library target. Expected: every helper contract passes and
the suite count increases above the 162-test baseline with zero failures.

### Task 5: Implement client request sequencing

**Files:**
- Modify: `vendor/tiberius/src/client.rs`

**Interfaces:**
- Consumes: `BulkInsertColumns` from Task 4.
- Produces:

```rust
pub async fn bulk_insert_columns<'a>(
    &'a mut self,
    table: &str,
    columns: &[&str],
) -> crate::Result<BulkLoadRequest<'a, S>>;
```

- [ ] **Step 1: Validate before the first await**

The first statement is:

```rust
let target = BulkInsertColumns::new(table, columns)?;
```

Only then call `self.connection.flush_stream().await?`.

- [ ] **Step 2: Fetch and drain one metadata response**

Send `target.metadata_query()` through `BatchRequest` and the existing reset
baseline helper. Fold the complete `TokenStream` into:

```rust
(resultset_count: usize, columns: Option<Vec<MetaDataColumn<'static>>>)
```

Increment with checked arithmetic. Clone metadata only for
`ReceivedToken::NewResultset`. Drain all other tokens.

- [ ] **Step 3: Validate and start INSERT BULK**

Call `target.validate_metadata(...)` and `target.insert_query(&columns)` before
sending the setup batch. Then:

1. flush the connection;
2. send the checked `INSERT BULK` batch;
3. call `TokenStream::flush_done().await?`;
4. return `BulkLoadRequest::new(&mut self.connection, columns)`.

Do not modify the existing `bulk_insert(table)` method.

- [ ] **Step 4: Run unit and source-contract tests**

Run:

```bash
cargo fmt --manifest-path vendor/tiberius/Cargo.toml --check
env CARGO_TARGET_DIR=/private/tmp/fastmssql-tiberius-bulk-feature-target \
  cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --lib
```

Expected: fmt clean and all vendored unit tests pass.

### Task 6: Prove real SQL Server behavior and regressions

**Files:**
- Modify: `vendor/tiberius/FASTMSSQL_PATCH.md`
- Modify: `VERSION.md`

**Interfaces:**
- Consumes: completed implementation.
- Produces: exact green evidence and final feature commit.

- [ ] **Step 1: Start and inspect the approved SQL Server**

Run:

```bash
docker compose --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml up -d sqlserver
docker inspect fastmssql-sql-auth-dev \
  --format '{{json .State.Health}}'
```

Expected: the named container is healthy and mapped to the configured
localhost port.

- [ ] **Step 2: Run new SQL-auth contracts**

Load `.env.sql-auth.local` without printing it, then run:

```bash
env CARGO_TARGET_DIR=/private/tmp/fastmssql-tiberius-bulk-feature-target \
  cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test bulk_column_subset_sql_auth -- --test-threads=1
```

Expected: `TIB-BULK-001` through `TIB-BULK-005` all pass.

- [ ] **Step 3: Run existing Tiberius regressions**

Run:

```bash
env CARGO_TARGET_DIR=/private/tmp/fastmssql-tiberius-bulk-feature-target \
  cargo test --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features --features chrono,tds73,rustls --test response_api

env CARGO_TARGET_DIR=/private/tmp/fastmssql-tiberius-bulk-feature-target \
  cargo test --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features --features chrono,tds73,rustls \
  --test response_events_sql_auth -- --test-threads=1

env CARGO_TARGET_DIR=/private/tmp/fastmssql-tiberius-bulk-feature-target \
  cargo test --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features --features chrono,tds73,rustls \
  --test token_safety_sql_auth -- --test-threads=1
```

Expected: all targets pass.

- [ ] **Step 4: Run quality gates**

Run vendored fmt and Clippy with `-D warnings` plus exactly the ten documented
legacy lint allowances from `scripts/sql_auth/run_all.sh`. Then run:

```bash
env CARGO_TARGET_DIR=/private/tmp/fastmssql-root-bulk-subset-target \
  cargo fmt --check
env CARGO_TARGET_DIR=/private/tmp/fastmssql-root-bulk-subset-target \
  cargo clippy --all-targets -- -D warnings
env CARGO_TARGET_DIR=/private/tmp/fastmssql-root-bulk-subset-target \
  cargo test --locked
```

Expected: zero failures and zero new warnings.

- [ ] **Step 5: Run FastMssql batch/parameter regressions**

Build the exact feature worktree extension with `maturin develop --release`.
Prove the imported Python wrapper and ABI3 extension resolve under the same
feature worktree, then run:

```bash
.venv/bin/pytest tests/sql_auth_strict/test_batch_strict.py -q
.venv/bin/pytest tests/sql_auth_strict/test_parameters_strict.py -q
.venv/bin/pytest tests/test_batch_parameter_validation.py -q
.venv/bin/pytest tests/test_bulk_bounded_buffering.py -q
.venv/bin/pytest tests/test_bulk_row_descriptor_conversion.py -q
```

Use the existing SQL-auth environment for DB-backed targets. Expected: all
predecessor tests pass; this slice does not add the Python native API.

- [ ] **Step 6: Record the implemented patch**

Change `FASTMSSQL_PATCH.md` from the RED requirement to a seventh narrow local
patch description. Record:

- additive subset method;
- closed identifier grammar;
- exact ordered metadata;
- total checked declarations;
- restricted/unsupported typed errors;
- mandatory finalize boundary;
- no Tiberius fork/publication.

Add a feature implementation section to `VERSION.md` with exact test counts
and no release claim.

- [ ] **Step 7: Self-review the feature diff**

Run:

```bash
git diff --check
rg -n 'todo!|unreachable!|unwrap\\(|expect\\(' \
  vendor/tiberius/src/client/bulk_columns.rs
rg -n 'format!\\(\"\\{\\}\"|format!\\(\".*table|format!\\(\".*column' \
  vendor/tiberius/src/client/bulk_columns.rs vendor/tiberius/src/client.rs
```

The first source scan has no matches. Review every formatting match from the
second scan and prove it consumes only pre-quoted or checked declarations.

Search the tracked diff for the exact configured username/password values
without printing those values. Expected: no matches.

- [ ] **Step 8: Rebuild and inspect code-review-graph**

Run:

```bash
uvx code-review-graph build
```

Then use `detect_changes`, `get_affected_flows` and `tests_for` for
`Client::bulk_insert_columns`. If the graph omits `vendor/`, record that
limitation and use direct source/test evidence rather than claiming graph
coverage.

- [ ] **Step 9: Commit and publish only the feature branch**

```bash
git add \
  vendor/tiberius/src/client.rs \
  vendor/tiberius/src/client/bulk_columns.rs \
  vendor/tiberius/FASTMSSQL_PATCH.md \
  VERSION.md
git commit -m "feat: add safe Tiberius bulk column subsets"
git push -u origin feat/tiberius-bulk-column-subset
```

The already tracked RED tests and runner remain inherited. Verify exact remote
SHA and confirm upstream push URL is still `DISABLED`.

### Task 7: Update the live production-readiness audit

**Files:**
- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify: `VERSION.md`

**Interfaces:**
- Consumes: exact RED/feature SHAs and all observed verification output.
- Produces: truthful live status for three completed batch/bulk slices and
  four still open.

- [ ] **Step 1: Create the status worktree**

```bash
git worktree add \
  .worktrees/docs-tiberius-bulk-column-subset-status \
  -b docs/tiberius-bulk-column-subset-status \
  feat/tiberius-bulk-column-subset
```

- [ ] **Step 2: Update only evidenced audit claims**

Record:

- design, RED and feature commit SHAs;
- exact observed RED compiler errors;
- exact unit, SQL-auth, fmt, Clippy, root-Cargo and FastMssql counts;
- supported/rejected metadata profile;
- the local graph's actual vendor coverage;
- fork-only branch publication;
- three of seven batch/bulk slices verified;
- four remaining slices: native TDS bulk, iterable backpressure,
  `execute_many()` and bounded-concurrency `query_many()`.

Keep the last complete hosted Linux/macOS/Windows, RustSec and generated matrix
evidence tied to its actual ancestor. Do not infer a hosted run for the new
commit.

- [ ] **Step 3: Update `VERSION.md` and self-review**

Add a status-only section. Run placeholder scan, credential-value scan,
`git diff --check`, audit consistency search and code-review-graph rebuild.

- [ ] **Step 4: Commit and push only status**

```bash
git add docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md VERSION.md
git commit -m "docs: record Tiberius bulk column subset status"
git push -u origin docs/tiberius-bulk-column-subset-status
```

Verify exact remote SHA, clean worktree, origin fork URL and upstream
`DISABLED`. Do not merge, create a PR or publish an artifact.
