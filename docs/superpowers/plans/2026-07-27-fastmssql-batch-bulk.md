# FastMssql Enterprise Batch and Bulk Program Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this program task-by-task. Use
> `superpowers:systematic-debugging` for every unexpected failure,
> `superpowers:test-driven-development` for every behavior change, and
> `superpowers:verification-before-completion` before commits, merges or
> completion claims. Steps use checkbox (`- [ ]`) syntax for tracking in the
> executable slice plans.

**Goal:** implement the batch/bulk design in independent RED/implementation
branch pairs, prove it against SQL Server with SQL authentication, and update
the production-readiness audit without overstating incomplete slices.

**Architecture:** preserve compatibility `INSERT ... VALUES` behavior while
bounding its conversion memory, then add a checked Tiberius column-subset
primitive, explicit native TDS bulk, bounded producer coordination,
`execute_many`, and fixed-worker `query_many`. Each slice has a dedicated
executable plan and RED→implementation ancestry; this document coordinates
those slice plans and final integration gates.

**Tech Stack:** Rust 2024, PyO3 0.29, Tokio, vendored Tiberius 0.12.3,
Python 3.11+ asyncio, pytest, Docker SQL Server 2022 with SQL authentication,
maturin ABI3 wheels, and code-review-graph.

**Design:** `docs/superpowers/specs/2026-07-27-fastmssql-batch-bulk-design.md`

**Base SHA:** `0d50c480ac9d5eabd7024dc5c405cb7e5603d317`

## Global Constraints

- Work and push only to `https://github.com/galeamarcel/FastMssql.git`.
- Keep the original repository remote fetch-only with push URL `DISABLED`.
- Use one project-local `.worktrees/<branch>` worktree per branch.
- Every implementation branch descends from its focused RED branch.
- A RED commit changes tests/specification evidence and `VERSION.md`, not
  production behavior.
- Update `VERSION.md` on every repository modification; do not change the
  displayed `0.7.7` package version or publish a release.
- Do not weaken an assertion, catch-and-ignore an exception, or convert a
  required failure into a skip.
- Do not retry operations after a write may have reached SQL Server.
- Preserve exact transaction, lifecycle, timeout, pool and operation-metric
  semantics.
- Use SQL Server in `docker-compose.sql-auth.yml` with SQL authentication for
  every SQL behavior claim.
- Build/install the extension from the current worktree before treating a
  Python result as authoritative.
- Store generated evidence under `.artifacts/` or an explicit temporary path;
  never commit credentials.
- Run `git diff --check` and a privacy/placeholder scan before every commit.
- Rebuild code-review-graph after implementation changes and use
  `detect_changes`, `get_affected_flows` and `tests_for` for self-review.
- Update the live audit only after exact-SHA technical verification.

---

## Plan hierarchy

The design spans seven sequentially dependent subsystems. This program plan
locks their order and branch ancestry; each subsystem receives a separate
executable plan before its RED branch is created. The first executable plan
is:

- `docs/superpowers/plans/2026-07-27-fastmssql-bulk-bounded-buffering.md`

The later slice plans are added on their corresponding documentation gate,
after the preceding implementation provides the exact interfaces they
consume. This avoids guessing signatures that do not yet exist.

## Task 1: publish the design and plan

**Branch:** `docs/batch-bulk-design`

**Files:**

- add
  `docs/superpowers/specs/2026-07-27-fastmssql-batch-bulk-design.md`;
- add
  `docs/superpowers/plans/2026-07-27-fastmssql-batch-bulk.md`;
- update `VERSION.md`.

- [ ] Verify branch base, clean worktree, fork origin and disabled upstream
  push.
- [ ] Record baseline FastMssql Rust, Tiberius and real SQL-auth batch
  results.
- [ ] Scan every design/plan document for placeholders, contradictions,
  ambiguous completion criteria and accidental upstream authorization.
- [ ] Run `git diff --check`.
- [ ] Commit as `docs: design enterprise batch and bulk APIs`.
- [ ] Push only `docs/batch-bulk-design` and verify the remote SHA.

## Task 2: reproduce eager O(N) compatibility-bulk conversion

**Branch:** `test/bulk-bounded-buffering`

**Base:** final commit of `docs/batch-bulk-design`

**Files:**

- add `tests/test_bulk_bounded_buffering.py`;
- extend `tests/sql_auth_strict/test_batch_strict.py`;
- extend the SQL-auth design with `BULK-001` and `BULK-002`;
- update `VERSION.md`.

RED requirements:

1. Use a value with observable conversion and place it beyond the first SQL
   chunk.
2. Split method creation from awaiting. Assert method creation does not touch
   any row value.
3. Assert a late conversion failure rolls back rows already sent by earlier
   chunks.
4. Add an opt-in RSS/ticker probe whose baseline memory is measured only
   after the Python input list exists.
5. Observe the unchanged implementation fail for the eager-conversion reason,
   not because of missing MSSQL, import errors or an invalid test fixture.
6. Commit only RED/spec/version changes as
   `test: require bounded compatibility bulk input`.
7. Push only `test/bulk-bounded-buffering`.

## Task 3: implement bounded compatibility-bulk conversion

**Branch:** `fix/bulk-bounded-buffering`

**Base:** RED commit from Task 2

**Primary files:**

- `src/batch.rs`;
- focused tests if a fixture-only correction is required;
- `VERSION.md`.

Implementation:

1. Keep only an owned `Py<PyList>` handle across awaits.
2. Remove the outer `Vec<Vec<FastParameter>>`.
3. Convert at most `bulk_rows_per_batch(columns.len())` rows inside one
   short `Python::attach` section.
4. Validate row width and conversion while creating that one owned chunk.
5. Send/drain the chunk, drop it, then advance the input index.
6. Keep empty input as a zero-I/O result.
7. Keep all non-empty chunks in one existing transaction and one absolute
   deadline.
8. Preserve rollback and fail-closed cancellation/timeout behavior.
9. Verify focused RED→GREEN, the full batch strict file, Rust, fmt and Clippy.
10. Run the bounded RSS/ticker probe on real SQL Server.
11. Commit as `fix: bound compatibility bulk conversion`.
12. Push only `fix/bulk-bounded-buffering`.

## Task 4: reproduce bulk descriptor inconsistency

**Branch:** `test/bulk-row-descriptor-conversion`

**Base:** Task 3 implementation

**Files:**

- extend parameter/bulk unit and SQL-auth tests;
- register `BULK-003` through `BULK-005`;
- update `VERSION.md`.

RED requirements:

- `Parameter(..., sql_type=...)` in a bulk cell reaches the same validated
  conversion family as ordinary execution;
- `expanded=True` is rejected because one cell cannot create multiple target
  columns;
- errors expose row/column/parameter/type/reason metadata without value, SQL
  text, table or column names;
- typed NULL and exact numeric/temporal/UUID values round-trip on SQL Server.

Commit as `test: require typed bulk row conversion`, then push the RED branch.

## Task 5: implement shared bulk row conversion

**Branch:** `fix/bulk-row-descriptor-conversion`

**Base:** Task 4 RED

Implementation:

1. Extract one closed single-cell converter from the existing parameter
   descriptor path.
2. Reject non-input and expanded descriptors locally.
3. Add privacy-safe bulk position context without replacing the typed
   conversion error.
4. Use the converter from compatibility bulk without changing ordinary
   query behavior.
5. Verify focused tests, all parameter tests, batch strict, Rust/fmt/Clippy
   and real SQL Server.
6. Commit as `fix: share typed conversion with bulk rows`.
7. Push only the fix branch.

## Task 6: reproduce Tiberius column-subset and panic boundaries

**Branch:** `test/tiberius-bulk-column-subset`

**Base:** Task 5 implementation

**Files:**

- add vendored Tiberius unit tests;
- add a dedicated SQL-auth integration test under `vendor/tiberius/tests/`;
- update `vendor/tiberius/FASTMSSQL_PATCH.md` only with RED status wording;
- update `VERSION.md`.

RED requirements:

- requesting two columns from a wider table cannot use existing
  `Client::bulk_insert(table)`;
- exact requested order is required;
- `]`, schema qualification and reserved words cannot alter metadata SQL;
- identity/computed/rowversion and unsupported metadata fail with typed
  errors;
- SQL_VARIANT/UDT metadata cannot reach `todo!()`/`unreachable!()`.

Commit as `test: require safe Tiberius bulk column subsets`, then push.

## Task 7: implement the Tiberius column-subset primitive

**Branch:** `feat/tiberius-bulk-column-subset`

**Base:** Task 6 RED

**Primary files:**

- `vendor/tiberius/src/client.rs`;
- checked bulk metadata/declaration helpers;
- vendored tests and patch notice;
- `VERSION.md`.

Implementation:

1. Add a structured column-subset entry point without changing existing
   `bulk_insert(table)`.
2. Quote identifiers internally and reject null bytes/empty parts.
3. Fetch only requested metadata in order.
4. Reject count/order/writability mismatches.
5. Replace new-path panic branches with typed errors.
6. Preserve packet streaming and mandatory `finalize()`.
7. Verify vendored fmt/Clippy/unit/SQL-auth plus root Cargo tests.
8. Commit as `feat: add safe Tiberius bulk column subsets`.
9. Push only the feature branch.

## Task 8: reproduce and implement explicit native bulk for concrete chunks

**Branches:**

```text
test/native-bulk-insert
  -> feat/native-bulk-insert
```

**Base:** Task 7 implementation

RED branch:

- add raw extension/stub/wrapper contracts;
- add SQL-auth `BULK-006` through `BULK-012`;
- prove subset columns, actual TDS bulk path, exact row count, transaction
  atomicity, unsupported schema rejection and cancellation retirement;
- commit as `test: require explicit native bulk insert`.

Feature branch:

1. Add internal native-chunk helpers for pooled and transaction-owned
   sessions.
2. Convert only one concrete chunk.
3. Call the Tiberius subset primitive, `send()` each row and always require a
   successful `finalize()`.
4. Mark the connection unusable before the first request await and restore
   reuse only after complete response/settlement.
5. Add `Connection.native_bulk_insert` and
   `Transaction.native_bulk_insert` for concrete list input.
6. Preserve explicit transaction ownership on `Transaction`.
7. Update both stub layers and README truthfully.
8. Commit as `feat: add native TDS bulk insert`.
9. Push only the feature branch.

## Task 9: add iterable and async-iterable bulk backpressure

**Branches:**

```text
test/bulk-iterable-backpressure
  -> feat/bulk-iterable-backpressure
```

**Base:** Task 8 implementation

RED requirements:

- sync generator and async generator inputs are accepted;
- pull count never exceeds one in-flight chunk;
- invalid `chunk_size` consumes no input;
- producer failure rolls back atomic connection form;
- cancellation/early close stops producer advancement and releases or
  retires the session deterministically;
- 1,000/10,000/99,999 profiles record RSS, ticker, pool and SPID ceilings.

Implementation:

1. Add one private bounded iterator coordinator in the Python wrapper.
2. Use the native concrete-chunk Rust primitives.
3. Shield mandatory rollback/close cleanup and preserve `BaseException`.
4. Never create a task per row or materialize the complete input.
5. Keep one checked cumulative row count.
6. Update stubs and usage docs.
7. Commit RED as `test: require bounded bulk iterable input`.
8. Commit feature as `feat: stream bulk iterable input with backpressure`.
9. Push both branches only to the fork.

## Task 10: add `execute_many`

**Branches:**

```text
test/execute-many
  -> feat/execute-many
```

**Base:** Task 9 implementation

RED requirements:

- one statement, typed parameter sets, sync/async iterable;
- atomic default rollback after late failure;
- explicit partial-success evidence for `atomic=False`;
- stable zero-based failure index;
- no eager consumption and no retry;
- cancellation/timeout cleanup and exact total row count.
- operation-metrics schema version 2 has an exact `execute_many` key and
  internal items do not increment `execute`;
- `Transaction.execute_many(sql, parameter_sets, *, chunk_size=1000)` never
  commits or rolls back the caller's transaction and becomes rollback-only
  after an execution error.

Implementation:

1. Reuse the bounded coordinator and transaction-safe chunk primitives.
2. Add connection and transaction wrapper/raw/stub surfaces; only the
   connection form exposes `atomic`.
3. Keep heterogeneous `execute_batch` unchanged.
4. Upgrade the operation-metrics contract from schema version 1 to 2 and
   count one `execute_many` public operation.
5. Verify real SQL Server, load, wheel and compatibility.
6. Commit RED as `test: require bounded parameterized execute many`.
7. Commit feature as `feat: add bounded execute many`.
8. Push both fork branches.

## Task 11: add bounded-concurrency `query_many`

**Branches:**

```text
test/query-many-bounded-concurrency
  -> feat/query-many-bounded-concurrency
```

**Base:** Task 10 implementation

RED requirements:

- async iterator result surface;
- ordered and completion-order modes;
- configured concurrency and pool max are never exceeded;
- input and output backpressure are both bounded;
- first error and early consumer exit clean all workers;
- transaction objects do not advertise concurrent `query_many`.

Implementation:

1. Use a fixed worker set and bounded queue, never one task per input.
2. Run independent pooled `query()` operations.
3. Buffer at most the concurrency window for ordered mode.
4. Await worker cleanup in generator finalization.
5. Preserve underlying per-query metrics and document the absence of an
   aggregate metric until a schema-versioned change.
6. Commit RED as `test: require bounded query many concurrency`.
7. Commit feature as `feat: add bounded query many`.
8. Push both branches.

## Task 12: cumulative verification

**Branch:** `verify/batch-bulk-merge`

Create history-only merges that preserve every RED→implementation ancestry.
Then verify on one exact SHA:

1. `git diff --check`, format, Clippy and both Rust unit suites.
2. Full `scripts/sql_auth/run_all.sh`.
3. Dedicated batch/bulk stress:
   - 1,000 operations/rows;
   - 10,000 operations/rows;
   - opt-in 99,999 operations/rows;
   - bounded concurrency and pool sizes;
   - p50/p95/p99, errors, timeouts, RSS, event-loop ticks, SPIDs and smoke.
4. Faults: cancellation, timeout, `KILL SPID`, connection reset and shutdown.
5. Installed ABI3 wheel in an isolated environment.
6. RustSec with zero vulnerabilities and zero warnings.
7. Hosted Linux/macOS/Windows raw Cargo, wheel install and contracts.
8. code-review-graph rebuild, change detection, affected flows and missing
   test coverage.

Do not update the audit if any mandatory gate is incomplete.

## Task 13: live audit and report

**Branch:** `docs/batch-bulk-status`

**Files:**

- update `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`;
- regenerate `docs/SQL_AUTH_TEST_MATRIX.md`;
- regenerate `docs/SQL_AUTH_TEST_REPORT.md`;
- add `docs/SQL_AUTH_BATCH_BULK_STRESS_REPORT.md`;
- update the upstream PR roadmap only with independently reproducible
  candidates;
- update `VERSION.md`.

Record:

- exact branch/commit ancestry;
- exact local and hosted run identifiers;
- measured limits and residual risks;
- compatibility versus native semantics;
- unsupported metadata and byte-level LOB limitation;
- no publication/release/upstream PR claim.

Mark order item 19 complete only if every branch pair and final gate above is
proved on the same cumulative tree. Otherwise record partial verified slices
and leave `feat/batch-bulk` open.
