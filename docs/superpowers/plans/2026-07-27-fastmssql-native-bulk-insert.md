# FastMssql Native TDS Bulk Insert Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` task by task, use
> `superpowers:test-driven-development` for every behavior change and use
> `superpowers:verification-before-completion` before every commit/status
> claim.

**Goal:** expose list-bounded `Connection.native_bulk_insert()` and
`Transaction.native_bulk_insert()` over the verified vendored-Tiberius
ordered-column bulk primitive, with exact target-guided conversion, atomic
connection settlement and rollback-only transaction failure semantics.

**Architecture:** a narrow vendored metadata bridge exposes the checked target
declarations. A dedicated FastMssql native-bulk module owns list chunking,
exact target conversion, TDS row construction and request finalization. The
connection form owns one pooled SQL transaction. The transaction form uses
the existing lease/state machine and adds an explicit rollback-only state.

**Tech stack:** Rust 2021, PyO3, pyo3-async-runtimes/Tokio, vendored Tiberius
0.12.3, bb8, SQL Server 2022 Docker with SQL authentication, pytest, maturin,
Cargo fmt/Clippy/test, Git worktrees and code-review-graph.

## Global constraints

- Exact documentation base:
  `4cfaa18a8d6938fb2911a4492fbc87093d9ef0d2`.
- Exact technical primitive ancestor:
  `52c04c35a27dd6a79ccb5f54b15d7a0413a8965b`.
- Branch topology:

  ```text
  docs/native-bulk-insert
    -> test/native-bulk-insert
    -> feat/native-bulk-insert
    -> docs/native-bulk-insert-status
  ```

- The feature branch must descend from the observed RED commit.
- Push only to `https://github.com/galeamarcel/FastMssql.git`.
- Keep the Rivendael push URL exactly `DISABLED`.
- Do not change compatibility `Connection.bulk_insert()` or
  `Client::bulk_insert(table)`.
- Do not accept iterator/async-iterator input in this slice.
- Keep the displayed package version `0.7.7`.
- Update `VERSION.md` for every repository commit.
- Do not publish a wheel/release, create a Tiberius fork or create any
  upstream PR.
- Never print `.env.sql-auth.local` or any credential value.
- Generated vendored `Cargo.lock` files are not committed.

---

## Task 1: publish the focused design and plan

**Branch:** `docs/native-bulk-insert`

**Files:**

- create
  `docs/superpowers/specs/2026-07-27-fastmssql-native-bulk-insert-design.md`;
- create
  `docs/superpowers/plans/2026-07-27-fastmssql-native-bulk-insert.md`;
- update `VERSION.md`.

- [ ] Confirm clean branch and exact ancestry.

  ```bash
  git status --short --branch
  git rev-parse HEAD
  git merge-base --is-ancestor \
    52c04c35a27dd6a79ccb5f54b15d7a0413a8965b HEAD
  git remote -v
  ```

- [ ] Self-review that the focused design:

  - keeps concrete list input only;
  - requires target-driven conversion;
  - defines empty-request finalization on pre-row conversion failure;
  - distinguishes reusable, rollback-only and broken outcomes;
  - preserves existing bulk metrics and compatibility APIs;
  - leaves iterable backpressure, execute-many and query-many open.

- [ ] Run:

  ```bash
  git diff --check
  ```

- [ ] Scan the two documents and `VERSION.md` for every non-empty local SQL
  password without printing the values.
- [ ] Commit:

  ```bash
  git add \
    docs/superpowers/specs/2026-07-27-fastmssql-native-bulk-insert-design.md \
    docs/superpowers/plans/2026-07-27-fastmssql-native-bulk-insert.md \
    VERSION.md
  git commit -m "docs: design native TDS bulk insert"
  ```

- [ ] Push only `docs/native-bulk-insert` and verify the exact remote SHA.

---

## Task 2: create the deterministic RED branch

**Branch:** `test/native-bulk-insert`

**Base:** exact Task 1 commit.

**Files:**

- modify
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`;
- modify `tests/sql_auth_strict/test_matrix_contract.py`;
- create `tests/test_native_bulk_contract.py`;
- create `tests/sql_auth_strict/test_native_bulk_strict.py`;
- extend `vendor/tiberius/src/client/bulk_columns_tests.rs`;
- if required, extend `vendor/tiberius/tests/response_api.rs`;
- update `scripts/sql_auth/run_all.sh` only if the new standalone target is
  not already selected by an existing lane;
- update `VERSION.md`.

### Step 2.1: register exact case IDs

- [ ] Add `BULK-006` through `BULK-012` exactly once to the canonical
  SQL-auth design.
- [ ] Change the canonical expected count from 377 to 384 everywhere in the
  matrix contract and report-generator fixtures.
- [ ] Attach each new ID to exactly one strict test.
- [ ] Require the matrix contract itself to pass on the RED branch.

### Step 2.2: add offline public-surface contracts

- [ ] Require raw extension methods:

  ```text
  Connection.native_bulk_insert
  Transaction.native_bulk_insert
  ```

- [ ] Require explicit wrapper methods and both stub declarations.
- [ ] Require:

  ```python
  await conn.native_bulk_insert(
      table,
      columns,
      rows,
      chunk_size=1000,
  ) -> int
  ```

- [ ] Require invalid chunk sizes to fail before touching a sentinel cell.
- [ ] Require an empty list to return zero with no pool/metric activity.
- [ ] Static source evidence must require:

  ```text
  bulk_insert_columns
  TokenRow::with_capacity / TokenRow::push
  request.send(...)
  request.finalize()
  ```

- [ ] The same static contract must reject building `INSERT ... VALUES` in
  the new native module.

### Step 2.3: add vendored RED contracts

- [ ] Require the public pure validator.
- [ ] Require `BulkLoadRequest::column_declarations()` to return exact
  declarations in requested order without identifiers or values.
- [ ] Require MONEY/SMALLMONEY declarations to remain visible to FastMssql so
  the wrapper can reject the unsupported exact-money boundary explicitly.

Expected RED: vendored tests fail to compile with missing validator and
missing `column_declarations`.

### Step 2.4: add SQL-auth cases

- [ ] `BULK-006`: raw/wrapper/stub surface plus zero-I/O empty behavior.
- [ ] `BULK-007`: reordered subset, omitted default, nullable target and exact
  row count.
- [ ] `BULK-008`: exact mixed target types:

  ```text
  BIT
  TINYINT / SMALLINT / INT / BIGINT
  REAL / FLOAT
  DECIMAL(19,4)
  VARCHAR / NVARCHAR
  VARBINARY
  UNIQUEIDENTIFIER
  DATE / TIME(3)
  DATETIME / SMALLDATETIME / DATETIME2(3) / DATETIMEOFFSET(3)
  XML
  typed NULLs
  ```

- [ ] `BULK-009`: identity, computed, rowversion, MONEY and SQL_VARIANT
  rejection; no panic; pool smoke after every rejection.
- [ ] `BULK-010`: `chunk_size=2`, later constraint/conversion failure and
  exact zero persisted rows after connection-form rollback.
- [ ] `BULK-011`: transaction success remains uncommitted until explicit
  settlement; post-wire failure rejects COMMIT/data, permits ROLLBACK and
  leaves zero rows.
- [ ] `BULK-012`: deterministic cancellation or operation timeout while the
  server is inside the native request; require request termination, physical
  replacement/capacity recovery and post-fault smoke.

Every test uses unique local objects and cleanup registration. No exception
is swallowed.

### Step 2.5: observe RED for the intended reason

- [ ] Build the unchanged native extension from the RED branch.
- [ ] Run:

  ```bash
  ../../.venv/bin/pytest \
    tests/test_native_bulk_contract.py \
    tests/sql_auth_strict/test_native_bulk_strict.py -q
  ```

Expected: missing `native_bulk_insert`, not fixture, import or connection
failure.

- [ ] Run vendored focused tests.

Expected: missing validator/declaration method.

- [ ] Run the matrix contract separately and require 26/26 PASS with 384
  canonical IDs.
- [ ] Commit only tests/spec/runner/version changes:

  ```bash
  git commit -m "test: require explicit native bulk insert"
  ```

- [ ] Push only `test/native-bulk-insert`, verify remote SHA and record the
  exact RED output.

---

## Task 3: implement the vendored metadata bridge

**Branch:** `feat/native-bulk-insert`

**Base:** exact observed RED commit.

**Primary files:**

- `vendor/tiberius/src/client.rs`;
- `vendor/tiberius/src/client/bulk_columns.rs`;
- `vendor/tiberius/src/tds/codec/bulk_load.rs`;
- vendored exports;
- `vendor/tiberius/FASTMSSQL_PATCH.md`;
- `VERSION.md`.

- [ ] Add a pure public validator that delegates to the existing private
  target parser.
- [ ] Make the checked declaration helper crate-visible only as required by
  the bulk request implementation.
- [ ] Add:

  ```rust
  pub fn column_declarations(&self) -> crate::Result<Vec<String>>
  ```

- [ ] Preserve declaration order and return type-only strings.
- [ ] Do not expose private `TypeInfo` or identifiers.
- [ ] Preserve `Client::bulk_insert(table)` source.
- [ ] Run vendored fmt, focused unit tests, full 168+ unit suite and direct
  SQL-auth regressions.

Expected: vendored RED contracts turn GREEN before FastMssql native tests do.

---

## Task 4: add exact target-guided conversion

**Primary files:**

- `src/parameter_conversion.rs`;
- its Rust unit tests;
- optional small shared error helper.

- [ ] Add one crate-private converter taking:

  ```rust
  (&Bound<PyAny>, &SqlParameterType, parameter_index)
  ```

- [ ] Raw values use the target type, never ordinary inferred wire width.
- [ ] A descriptor:

  - must be INPUT;
  - must not be expanded;
  - with explicit type must equal the canonical target type;
  - without explicit type is unwrapped and converted against the target.

- [ ] A `TypedNull` must match the target wire family.
- [ ] Add unit tests for exact integer widths, decimal/temporal scale,
  descriptor mismatch, typed-null mismatch and redacted errors.
- [ ] Do not alter ordinary query conversion unless a separately failing
  shared test proves the change is required.

---

## Task 5: implement the connection native-bulk engine

**Primary files:**

- create `src/native_bulk.rs`;
- modify `src/lib.rs`;
- modify `src/connection.rs`;
- reuse shared lifecycle/pool/deadline/error utilities.

### Step 5.1: synchronous boundary

- [ ] Validate table/columns with the vendored pure validator.
- [ ] Validate `chunk_size` in `1..=10_000`, rejecting bool.
- [ ] Capture `row_count` and an owned `Py<PyList>` without converting cells.
- [ ] Return zero before metrics/pool for empty input.

### Step 5.2: one-chunk conversion

- [ ] Validate the list length at each boundary.
- [ ] Start a bulk request and retrieve declarations.
- [ ] Parse declarations with the closed parser.
- [ ] Convert at most `chunk_size` rows to target-shaped owned values.
- [ ] On pre-send conversion failure, finalize the empty request.
- [ ] Attach global privacy-safe row/column/parameter context.

### Step 5.3: TDS send/finalize

- [ ] Build dynamic `TokenRow` values with exact column order.
- [ ] Send rows sequentially.
- [ ] Finalize exactly once.
- [ ] Require exact chunk count and checked cumulative count.
- [ ] Drop the current converted chunk before converting the next.
- [ ] Never build parameterized VALUES SQL.

### Step 5.4: atomic connection settlement

- [ ] Admit one `OperationName::BulkInsert`.
- [ ] Acquire one pooled lease and mark it unsafe before request I/O.
- [ ] Use one absolute operation deadline.
- [ ] Begin one SQL transaction across every chunk.
- [ ] Roll back reusable pre-COMMIT failures.
- [ ] Retire uncertain bulk streams instead of issuing SQL on an undrained
  protocol.
- [ ] Extract/reuse the existing deterministic COMMIT rejection versus
  `CommitOutcomeUnknown` classifier; do not duplicate weaker semantics.
- [ ] Restore pool reuse only after successful COMMIT/complete response.

---

## Task 6: implement transaction native bulk and rollback-only

**Primary files:**

- `src/transaction.rs`;
- `src/native_bulk.rs`;
- transaction Rust tests.

- [ ] Add `TransactionState::RollbackOnly`.
- [ ] Require Active before native bulk.
- [ ] Permit ROLLBACK from RollbackOnly.
- [ ] Reject COMMIT and every data operation from RollbackOnly with a stable
  privacy-safe error.
- [ ] Native success restores Active.
- [ ] Safe pre-row failure restores Active only after empty finalization.
- [ ] Post-wire reusable failure enters RollbackOnly.
- [ ] Uncertain/cancelled/timed-out/panicked failure retires the connection
  and enters Failed.
- [ ] Reuse the existing cancellation epoch and force-shutdown machinery.
- [ ] Add deterministic state-machine unit tests before running SQL-auth.

---

## Task 7: expose wrapper, stubs and truthful docs

**Files:**

- `src/connection.rs`;
- `src/transaction.rs`;
- `python/fastmssql/__init__.py`;
- `python/fastmssql/fastmssql.pyi`;
- `README.md`;
- `VERSION.md`.

- [ ] Add exact list-only signatures to raw classes.
- [ ] Add explicit async wrapper methods to both wrapper classes.
- [ ] Stub return type is `Coroutine[Any, Any, int]`.
- [ ] Document compatibility versus native semantics.
- [ ] State concrete-list-only input and exact unsupported types.
- [ ] Do not document iterable input until the next slice implements it.

---

## Task 8: make every RED contract GREEN

- [ ] Build/install the exact feature worktree:

  ```bash
  env \
    CARGO_TARGET_DIR=/private/tmp/fastmssql-native-bulk-target \
    UV_CACHE_DIR=/private/tmp/fastmssql-uv-cache \
    uv run maturin develop --release
  ```

- [ ] Verify wrapper and ABI3 extension paths resolve to the feature
  worktree.
- [ ] Run offline contracts.
- [ ] Source `../../.env.sql-auth.local` without printing it and run
  `BULK-006` through `BULK-012` on the approved Docker SQL Server.
- [ ] Run direct Tiberius SQL-auth tests.
- [ ] Run compatibility batch/bulk, parameter and transaction regressions.
- [ ] Run the matrix contract and require 384 unique IDs.

Do not weaken a test to accommodate an implementation defect. Use
systematic debugging for every unexpected failure.

---

## Task 9: concrete-list native-bulk stress

**Files:**

- add a bounded stress harness under `scripts/sql_auth/`;
- add a generated report only on the later status branch.

- [ ] Profiles:

  ```text
  1,000 rows
  10,000 rows
  99,999 rows (explicit opt-in)
  ```

- [ ] Record:

  - exact affected/persisted rows;
  - elapsed and throughput;
  - p50/p95/p99 chunk latency;
  - RSS baseline after input construction and maximum growth;
  - event-loop ticks and maximum stall;
  - maximum physical sessions/SPIDs;
  - errors/timeouts;
  - post-load smoke and teardown state.

- [ ] Require one pooled physical lease for a single native call and exact
  persisted count.
- [ ] Set explicit budgets from observed 1,000/10,000 baselines before
  accepting 99,999.
- [ ] Do not infer iterable backpressure from concrete-list results.

---

## Task 10: full feature verification and self-review

- [ ] Run root and vendored fmt.
- [ ] Run root Clippy with `-D warnings`.
- [ ] Run vendored Clippy with only the ten audited legacy allowances.
- [ ] Run root and vendored unit suites.
- [ ] Run all relevant SQL-auth targets serially.
- [ ] Run focused installed-wheel tests in an isolated environment.
- [ ] Run `git diff --check`.
- [ ] Scan changed files/artifacts for local credential values without
  printing them.
- [ ] Move any generated vendored `Cargo.lock` to a unique recoverable
  `/private/tmp` directory.
- [ ] Rebuild code-review-graph on the exact feature branch:

  ```bash
  env UV_CACHE_DIR=/private/tmp/fastmssql-uv-cache \
    uvx code-review-graph build
  ```

- [ ] Run `detect_changes`, affected flows and tests-for queries. Record
  vendored indexing limits honestly.
- [ ] Confirm all changed runtime paths have RED ancestry.
- [ ] Confirm compatibility bulk source/behavior is unchanged.
- [ ] Confirm origin/upstream push URLs again.

- [ ] Commit:

  ```bash
  git commit -m "feat: add native TDS bulk insert"
  ```

- [ ] Push only `feat/native-bulk-insert` and verify exact remote SHA.

---

## Task 11: update the live audit on a separate status branch

**Branch:** `docs/native-bulk-insert-status`

**Base:** exact feature commit.

**Files:**

- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`;
- `VERSION.md`;
- generated matrix/report only if complete evidence is actually regenerated;
- native-bulk stress report.

- [ ] Record exact design, plan, RED and feature SHAs.
- [ ] Record exact RED errors and every local/Docker/load count.
- [ ] Mark four of seven batch/bulk slices verified only if all mandatory
  gates pass.
- [ ] Leave iterable backpressure, `execute_many()` and `query_many()` open.
- [ ] Keep the last complete hosted/full matrix report tied to its actual
  ancestor unless a new complete run exists.
- [ ] Record unsupported MONEY/SQL_VARIANT/spatial/UDT/legacy LOB and
  byte-level LOB limits.
- [ ] Record no release/Tiberius-fork/upstream-PR claim.
- [ ] Run matrix contract, diff/credential checks and graph rebuild.
- [ ] Commit:

  ```bash
  git commit -m "docs: record native TDS bulk insert status"
  ```

- [ ] Push only the status branch and verify exact remote SHA.

## Final acceptance

This slice is `VERIFIED_FORK` only when:

1. the feature descends from the observed RED commit;
2. both public forms use actual TDS bulk requests;
3. raw values are converted from exact target metadata;
4. every request is finalized or the socket is retired;
5. connection form is atomic across chunks;
6. transaction form is settlement-neutral and rollback-only after post-wire
   failure;
7. cancellation/timeout recovery is proved on real SQL Server;
8. concrete-list memory/event-loop/load gates pass through the opt-in 99,999
   profile;
9. compatibility regressions remain green;
10. all publication remains exclusively on the user's fork.
