# FastMssql Native Bulk Iterable Backpressure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` task by task, use
> `superpowers:test-driven-development` for every behavior change and use
> `superpowers:verification-before-completion` before every commit or status
> claim. Every checkbox is an execution gate, not a retrospective summary.

**Goal:** widen the public `Connection.native_bulk_insert()` and
`Transaction.native_bulk_insert()` wrappers to bounded synchronous and
asynchronous row producers while preserving the verified concrete-list raw
fast path, one absolute deadline, one operation metric, one transaction and
global diagnostics across every chunk.

**Architecture:** Python owns producer protocol selection, one-row-at-a-time
normalization, bounded chunk construction and cancellation-safe producer
cleanup. A private PyO3 `_NativeBulkSequence` owns the cross-chunk Rust
transaction, lifecycle admission, deadline, observer, row offset and
settlement. The existing raw list methods remain unchanged public primitives.

**Tech stack:** Python 3.11–3.14, `asyncio`, Rust 2021, PyO3 and
`pyo3-async-runtimes` 0.29, Tokio, vendored Tiberius 0.12.3, bb8, SQL Server
2022 Docker with SQL authentication, pytest/pytest-asyncio, Ruff, maturin,
Cargo fmt/Clippy/test, Git worktrees and code-review-graph.

## Global constraints

- Exact focused-design commit:
  `0afe5c93542e93b4f42764e8fcb4f5ce6c58e47f`.
- Branch topology:

  ```text
  docs/bulk-iterable-backpressure-design
    -> test/bulk-iterable-backpressure
    -> feat/bulk-iterable-backpressure
    -> docs/bulk-iterable-backpressure-status
  ```

- The test branch starts from the exact design-and-plan commit.
- The feature branch must descend from the commit where focused RED was
  actually observed.
- All worktrees live under the project-local ignored `.worktrees/` directory.
- Push only to `https://github.com/galeamarcel/FastMssql.git`.
- Keep the `Rivendael/FastMssql` push URL exactly `DISABLED`.
- Do not create or update a pull request against the original repository.
- Keep the displayed package version `0.7.7`; this slice is part of the
  unreleased `0.8.0` candidate history.
- Update `VERSION.md` in every documentation, test, feature and status commit.
- Do not change the compatibility `bulk_insert()`, `execute_batch()` or
  `query_batch()` APIs.
- Do not implement `execute_many()`, `query_many()`, byte-level LOB streaming
  or new SQL type families in this slice.
- Preserve the concrete-list raw extension call and its list-only stubs.
- Never call `list(rows)` for a non-list producer, prefetch a row, create a
  per-row task or use an unbounded queue.
- Never swallow `BaseException`; `CancelledError`, `KeyboardInterrupt` and
  `SystemExit` remain primary.
- Never print or commit SQL-auth passwords, connection URLs, local `.env`
  contents, stress JSON, wheels, build caches or generated vendored lockfiles.
- A local pass is not a hosted pass. Fork GitHub Actions status is recorded
  independently as PASS, FAIL or NOT RUN.

---

## Task 1: publish the focused design and executable plan

**Branch:** `docs/bulk-iterable-backpressure-design`

**Files:**

- retain
  `docs/superpowers/specs/2026-07-29-fastmssql-bulk-iterable-backpressure-design.md`;
- create
  `docs/superpowers/plans/2026-07-29-fastmssql-bulk-iterable-backpressure.md`;
- update `VERSION.md`.

- [ ] Confirm the design commit is the exact parent and the worktree is clean
  before the plan edit:

  ```bash
  git rev-parse HEAD
  git status --short --branch
  git remote -v
  ```

- [ ] Self-review the plan against the focused design and current source:

  - validation precedes producer advancement;
  - async protocol wins for a dual-protocol object;
  - caller `Transaction` is reserved before the first pull;
  - a successful empty producer performs no SQL and starts no metric;
  - the first observed row activates one lifecycle operation and one observer;
  - no producer pull occurs while a Rust `push()` is pending;
  - one deadline covers producer wait, pool/TDS work and Connection COMMIT;
  - Connection failures roll back all chunks;
  - caller-transaction post-wire failures become rollback-only;
  - global row/parameter indices cross chunk boundaries;
  - cleanup preserves the original exception;
  - list input remains on the existing raw method.

- [ ] Rebuild code-review-graph and require a docs-only change with no affected
  runtime flow:

  ```bash
  uvx code-review-graph build
  ```

- [ ] Run:

  ```bash
  git diff --check
  ```

- [ ] Scan the staged files for every non-empty local credential value without
  printing any value.

- [ ] Commit exactly the plan and its `VERSION.md` entry:

  ```bash
  git add \
    docs/superpowers/plans/2026-07-29-fastmssql-bulk-iterable-backpressure.md \
    VERSION.md
  git commit -m "docs: plan bounded native bulk iterable input"
  ```

- [ ] Push only `docs/bulk-iterable-backpressure-design` and verify the exact
  remote SHA equals local HEAD.

---

## Task 2: create deterministic offline RED contracts

**Branch:** `test/bulk-iterable-backpressure`

**Base:** exact Task 1 commit.

**Worktree:**
`.worktrees/test-bulk-iterable-backpressure`

**Files:**

- create `tests/test_native_bulk_iterable_contract.py`;
- create `tests/test_native_bulk_iterable_coordinator.py`;
- modify `tests/test_native_bulk_contract.py` only for shared surface checks
  that genuinely belong to both input paths;
- update `VERSION.md`.

### Step 2.1: lock the public/raw surface split

- [ ] Assert both public wrapper stubs use:

  ```python
  from collections.abc import AsyncIterable, Iterable, Sequence
  from typing import Any

  BulkRow = Sequence[Any]
  BulkRows = (
      list[list[Any]]
      | Iterable[BulkRow]
      | AsyncIterable[BulkRow]
  )
  ```

- [ ] Require the public wrapper signatures:

  ```python
  async def native_bulk_insert(
      self,
      table: str,
      columns: list[str],
      rows: BulkRows,
      *,
      chunk_size: int = 1000,
  ) -> int: ...
  ```

- [ ] Require both raw-extension stub signatures to remain:

  ```python
  rows: list[list[Any]]
  ```

- [ ] Use a raw sentinel and prove a concrete `list` invokes
  `raw.native_bulk_insert(...)` directly without importing or constructing the
  iterable coordinator.

### Step 2.2: specify the private coordinator protocol

- [ ] Fake raw owners expose exactly one synchronous constructor:

  ```python
  sequence = raw._native_bulk_sequence(
      table,
      columns,
      chunk_size=chunk_size,
  )
  ```

- [ ] The fake sequence records these awaited calls:

  ```text
  reserve()
  activate()
  push(list[list[Any]])
  finish() -> int
  abort("error" | "cancelled")
  expire() -> raises OperationTimeoutError
  remaining_timeout() -> float | None
  ```

- [ ] Assert invalid `chunk_size`, invalid identifiers, mappings and
  string/binary top-level values cause zero `next/anext` calls, zero
  reservation and zero push calls.

- [ ] Assert `iter()` or `aiter()` is acquired exactly once. A producer with
  both protocols must record async acquisition and zero sync acquisition.

- [ ] Assert `Transaction.reserve()` finishes before the first
  `next()`/`anext()` call.

- [ ] Assert empty sync and async producers call `finish()` after
  `reserve()`, never call `activate()` or `push()`, and return zero.

- [ ] Assert tuple rows become fresh lists while list rows retain the list row
  shape expected by the raw sequence.

- [ ] Reject every row that is not a non-string `Sequence`; retain the
  original normalization exception as the primary error.

### Step 2.3: prove bounded pull behavior

- [ ] Use a fake `push()` blocked by an `asyncio.Event`.
- [ ] With `chunk_size=3`, assert exactly three producer pulls occur before
  the first push blocks.
- [ ] Yield to the loop repeatedly while the push remains blocked and assert
  the pull count remains three.
- [ ] Release the push and require the next pull only after the await
  completes.
- [ ] Repeat the same contract for sync and async producers and for a final
  partial chunk.
- [ ] Assert only one `push` exists at a time and no task is created per row.

### Step 2.4: prove abnormal cleanup

- [ ] Cover producer failure:

  ```python
  class ProducerFailure(BaseException):
      pass
  ```

  The exact object must be re-raised; it may not be stringified or wrapped.

- [ ] Cover cancellation during `anext()` and during `push()`. Producer pulls
  must stop immediately and `abort("cancelled")` plus `aclose()` must reach a
  terminal state under retained, shielded cleanup tasks.

- [ ] Cover cleanup failure and assert:

  ```python
  raised is original
  raised.__cause__ is cleanup_error
  ```

- [ ] Cover producer deadline expiry and require the typed error created by
  `sequence.expire()`, not the built-in `asyncio.TimeoutError`.

- [ ] Static AST checks reject `list(rows)`, `Queue`, task-per-row loops and
  an unreferenced `asyncio.shield(coroutine)` call in
  `python/fastmssql/_bulk_iterable.py`.

### Step 2.5: observe offline RED

- [ ] Rebuild the unchanged extension from the exact test worktree.
- [ ] Run:

  ```bash
  ../../.venv/bin/pytest \
    tests/test_native_bulk_iterable_contract.py \
    tests/test_native_bulk_iterable_coordinator.py \
    tests/test_native_bulk_contract.py -q
  ```

  Expected RED: the public wrapper still delegates generator input to a raw
  method requiring `list`, the private coordinator/module does not exist and
  public stubs remain list-only. Fixture/import/environment failures are not
  acceptable RED evidence.

---

## Task 3: add canonical SQL-auth and stress RED contracts

**Branch:** continue `test/bulk-iterable-backpressure`

**Files:**

- modify
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`;
- create
  `tests/sql_auth_strict/test_native_bulk_iterable_strict.py`;
- modify `tests/sql_auth_strict/test_matrix_contract.py`;
- create `scripts/sql_auth/native_bulk_iterable_stress.py`;
- create `tests/test_native_bulk_iterable_stress_contract.py`;
- update `VERSION.md`.

### Step 3.1: register the matrix exactly

- [ ] Add `BULK-013` through `BULK-021` exactly once to the canonical
  SQL-auth design, using the wording in the focused design.
- [ ] Change the approved unique-case count from 387 to 396.
- [ ] Attach each new ID to exactly one strict test source.
- [ ] Run the matrix contract and require it to pass even on the RED branch:

  ```bash
  ../../.venv/bin/pytest tests/sql_auth_strict/test_matrix_contract.py -q
  ```

### Step 3.2: encode the real SQL Server cases

- [ ] `BULK-013`: public sync/async producer surface, raw list-only surface
  and unchanged concrete-list fast path.
- [ ] `BULK-014`: invalid validation paths pull zero rows and create no pool,
  SQL session or `bulk_insert` operation metric.
- [ ] `BULK-015`: empty Connection producer remains disconnected with zero
  metrics; empty active Transaction remains active and settlement-neutral.
- [ ] `BULK-016`: a gated synchronous generator crosses at least three
  chunks, persists exact ordered values and never advances while its current
  chunk is held in the TDS path.
- [ ] `BULK-017`: repeat `BULK-016` with an async generator.
- [ ] `BULK-018`: fail a later producer and a later conversion at known global
  row indices; require the original exception/global metadata, `close` or
  `aclose`, zero committed Connection rows and a reusable pool.
- [ ] `BULK-019`: caller Transaction success remains invisible outside until
  COMMIT; post-wire producer failure rejects further data and COMMIT, permits
  ROLLBACK and persists zero rows.
- [ ] `BULK-020`: cancel once while awaiting producer input and once while
  SQL Server holds the request; require no later pull, one cancelled metric,
  bounded cleanup, DMV disappearance and a post-fault smoke query.
- [ ] `BULK-021`: expire once in `anext()` and once in TDS activity; require
  `OperationTimeoutError(operation="bulk_insert")`, privacy-safe metadata,
  bounded wall time, no candidate application session and pool recovery.

- [ ] Every SQL object and application name is unique. Register cleanup before
  the first operation that can fail. Cleanup errors are chained or aggregated,
  never swallowed.

### Step 3.3: create the dedicated stress contract

- [ ] The parser accepts exact `ROWS:CHUNK_SIZE` profiles between 1 and
  99,999 rows and 1 and 10,000 rows per chunk.
- [ ] `99_999` requires `--allow-extended`.
- [ ] Run both `sync` and `async` producer modes for each selected profile.
- [ ] Generate rows lazily. A tracked `str` payload subclass records the
  number of live yielded cells so the peak buffered-row assertion is
  independent of total row count.
- [ ] Emit atomic, privacy-safe JSON containing:

  ```text
  source_sha
  worktree_dirty
  input_model
  row_count
  chunk_size
  affected_rows
  persisted summary
  producer_pulls
  maximum_buffered_rows
  RSS baseline/peak/growth
  event-loop ticks/maximum gap
  physical identities/maximum SQL sessions
  operation and pool metric deltas
  elapsed time/throughput
  post-load smoke
  teardown session count
  violations
  ```

- [ ] The runner exits non-zero for any violation:

  ```text
  maximum_buffered_rows > chunk_size
  rss_growth_bytes > 67_108_864
  maximum_event_loop_gap_seconds > 0.100
  affected/persisted/pull count mismatch
  error or timeout
  failed smoke
  non-zero teardown sessions
  ```

- [ ] Contract tests parse the source AST and parser behavior without
  requiring SQL Server.

### Step 3.4: observe real RED and commit it

- [ ] Start the approved Docker SQL Server and prove readiness:

  ```bash
  docker compose \
    --env-file ../../.env.sql-auth.local \
    -f docker-compose.sql-auth.yml up -d sqlserver
  docker compose -f docker-compose.sql-auth.yml ps
  ```

- [ ] Load `.env.sql-auth.local` without printing it and run the new strict
  tests from the exact rebuilt test worktree:

  ```bash
  ../../.venv/bin/pytest \
    tests/sql_auth_strict/test_native_bulk_iterable_strict.py -q
  ```

  Expected RED: generator input reaches the existing list-only raw PyO3
  method. Docker, authentication, fixture and cleanup failures are not
  acceptable evidence.

- [ ] Record the exact focused offline and SQL-auth RED outputs outside the
  repository under `/private/tmp`, named with the full RED SHA.

- [ ] Run `git diff --check`, Ruff on new Python files and the complete
  test-only self-review.

- [ ] Commit only canonical spec/tests/stress harness/version changes:

  ```bash
  git add \
    docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
    tests/test_native_bulk_iterable_contract.py \
    tests/test_native_bulk_iterable_coordinator.py \
    tests/test_native_bulk_iterable_stress_contract.py \
    tests/sql_auth_strict/test_native_bulk_iterable_strict.py \
    tests/sql_auth_strict/test_matrix_contract.py \
    scripts/sql_auth/native_bulk_iterable_stress.py \
    VERSION.md
  git commit -m "test: require bounded bulk iterable input"
  ```

- [ ] Push only `test/bulk-iterable-backpressure`; verify its remote SHA and
  keep the branch immutable as the observed RED ancestor.

---

## Task 4: make native bulk chunks accept a global row base

**Branch:** `feat/bulk-iterable-backpressure`

**Base:** exact observed RED commit.

**Worktree:**
`.worktrees/feat-bulk-iterable-backpressure`

**Files:**

- modify `src/native_bulk.rs`;
- add focused Rust unit tests in `src/native_bulk.rs`;
- update `VERSION.md`.

**Consumes:** the verified concrete-list engine and vendored ordered-column
bulk primitive.

**Produces:** a reusable validated target plus a one-chunk execution primitive
whose diagnostics use a caller-provided global row base.

### Step 4.1: add Rust unit RED before the refactor

- [ ] Add tests requiring:

  - identifier validation exactly once when preparing a target;
  - `row_index_base + local_row_index` with checked `usize` arithmetic;
  - global `parameter_index`;
  - overflow before TDS row submission;
  - list fast-path base zero;
  - exact chunk affected count.

- [ ] Run the focused Rust test and observe failure for the absent
  target/chunk API before editing production code.

### Step 4.2: split target validation from concrete rows

- [ ] Introduce the crate-private shape:

  ```rust
  #[derive(Clone)]
  pub(crate) struct NativeBulkTarget {
      table: String,
      columns: Vec<String>,
      column_count: usize,
      chunk_size: usize,
  }

  pub(crate) fn prepare_native_bulk_target(
      table: String,
      columns: Vec<String>,
      chunk_size: usize,
  ) -> PyResult<NativeBulkTarget>;

  pub(crate) fn prepare_native_bulk_chunk(
      target: NativeBulkTarget,
      rows: &Bound<'_, PyList>,
      row_index_base: usize,
  ) -> PyResult<PreparedNativeBulk>;
  ```

- [ ] Keep `prepare_native_bulk(...)` as the list-path adapter using
  `row_index_base = 0`.
- [ ] Add `row_index_base` to `PreparedNativeBulk`.
- [ ] Replace every local diagnostic row index with checked global addition;
  do not use saturating arithmetic for externally visible positions.
- [ ] Keep table/column/value text out of every error.

### Step 4.3: expose one-chunk execution internally

- [ ] Refactor without changing list behavior:

  ```rust
  pub(crate) async fn run_native_bulk_chunk(
      client: &mut TiberiusClient,
      input: &PreparedNativeBulk,
      any_prior_row_sent: bool,
  ) -> Result<u64, NativeBulkFailure>;
  ```

- [ ] `run_native_bulk_chunks()` remains the concrete-list loop and delegates
  to this primitive.
- [ ] A chunk is complete only after `request.finalize()` returns the exact
  expected count.
- [ ] A local conversion failure after request creation finalizes the empty
  request before returning.

- [ ] Run:

  ```bash
  cargo test --locked native_bulk
  cargo fmt --check
  ```

---

## Task 5: reserve caller transactions and implement the private sequence

**Branch:** continue `feat/bulk-iterable-backpressure`

**Files:**

- create `src/native_bulk_sequence.rs`;
- modify `src/transaction.rs`;
- modify `src/connection.rs`;
- modify `src/operation_metrics.rs`;
- modify `src/lib.rs`;
- modify `python/fastmssql/fastmssql.pyi` only for underscore-prefixed private
  methods/classes needed by the Python coordinator;
- add Rust state-machine/cancellation tests beside the owning modules;
- update `VERSION.md`.

**Consumes:** `NativeBulkTarget`, the one-chunk native engine, transaction
lease/state machinery, lifecycle permits, deadline helpers and operation
observers.

**Produces:** one private stateful PyO3 sequence for either Connection-owned
or caller-owned settlement.

### Step 5.1: write state-machine RED tests

- [ ] Require the exact reservation table:

  | Owner | Before reserve | Reserved producer wait | In-flight push | After successful finish |
  |---|---|---|---|---|
  | Connection | no transaction | no SQL/permit | private transaction executing | committed/released |
  | Transaction | `Active` | `BulkProducing` | `Executing` | `Active`, uncommitted |

- [ ] Require a successful empty caller Transaction to transition
  `Active -> BulkProducing -> Active` with no TDS.
- [ ] Require every public data/settlement command except cleanup to reject
  `BulkProducing`.
- [ ] Require pre-wire caller abort to restore `Active`; post-wire abort
  becomes `RollbackOnly`.
- [ ] Require Connection abort after any started private transaction to roll
  back, and require uncertain cleanup to retire the connection.
- [ ] Require an in-flight cancelled push to use the existing epoch guard and
  leave no reusable uncertain transport.
- [ ] Require a dropped non-terminal sequence to fail closed.
- [ ] Dropping a sequence that never reserved or activated anything remains a
  side-effect-free no-op.
- [ ] Observe focused Rust RED before adding the new states/type.

### Step 5.2: add explicit reservation semantics

- [ ] Add `TransactionState::BulkProducing`. It is not a TDS in-flight state,
  but all ordinary public commands reject it deterministically.
- [ ] Add crate-private methods that atomically:

  ```rust
  reserve_bulk_producer()
  activate_owned_bulk_lifecycle()
  execute_reserved_native_bulk_chunk(...)
  finish_reserved_bulk(...)
  abort_reserved_bulk(...)
  ```

- [ ] For caller ownership, reserve only after `iter/aiter` acquisition and
  before the first producer pull.
- [ ] For Connection ownership, first-row activation admits one
  `TransactionPermit` but does not initialize/acquire the pool. Teach
  connection acquisition to reuse that stored permit instead of admitting a
  second participant.
- [ ] Preserve a single lock order: sequence progress first, then transaction
  session. No other path may acquire them in reverse order.

### Step 5.3: preserve one absolute deadline and one observer

- [ ] Add:

  ```rust
  pub(crate) fn start_at(
      metrics: Option<Arc<OperationMetricsRegistry>>,
      operation: OperationName,
      started_at: tokio::time::Instant,
  ) -> Self;
  ```

  Keep `OperationObserver::start()` as the existing `Instant::now()` adapter.

- [ ] Capture at private-sequence construction:

  ```rust
  started_at: tokio::time::Instant
  operation_deadline: Option<Deadline>
  ```

- [ ] Never call `deadline_from` again for an internal push, hidden BEGIN,
  COMMIT or cleanup decision.
- [ ] Internal BEGIN/COMMIT/ROLLBACK must not create public transaction
  operation metrics.

### Step 5.4: implement `_NativeBulkSequence`

- [ ] Register, but do not re-export, this private class:

  ```rust
  #[pyclass(name = "_NativeBulkSequence")]
  pub(crate) struct PyNativeBulkSequence {
      inner: Arc<tokio::sync::Mutex<NativeBulkSequence>>,
  }
  ```

- [ ] Its Python-visible protocol is exact:

  ```text
  reserve() -> awaitable[None]
  activate() -> awaitable[None]
  push(rows: list[list[Any]]) -> awaitable[int]
  finish() -> awaitable[int]
  abort(outcome: "error" | "cancelled") -> awaitable[None]
  expire() -> awaitable[NoReturn]
  remaining_timeout() -> float | None
  ```

- [ ] Construction synchronously validates identifiers/chunk size, captures
  target/deadline/owner and performs no lifecycle, pool, SQL or metric work.
- [ ] `activate()` starts one observer from captured `started_at`; it is
  idempotent only for the same active sequence and rejects terminal use.
- [ ] `push()` accepts one non-empty concrete chunk, requires
  `len(rows) <= chunk_size`, uses stored global base, awaits complete TDS
  finalization and only then advances count/base.
- [ ] `finish()` settles Connection ownership once; caller ownership returns
  to `Active` without settlement.
- [ ] `abort()` preserves the supplied metric outcome while performing
  bounded cleanup.
- [ ] `abort()`/`expire()` wait for a just-cancelled private awaitable to
  release the sequence lock; normal producer calls still reject concurrency,
  and Python may not re-raise before observer/database cleanup is terminal.
- [ ] `abort()` or `expire()` from the reserved-but-not-activated state starts
  and completes exactly one observer from captured `started_at`, releases the
  caller Transaction reservation and performs no lifecycle, pool or SQL work.
- [ ] `expire()` creates the authoritative
  `OperationTimeoutError(operation="bulk_insert")`, cleans up and raises it.
- [ ] `remaining_timeout()` returns `None` or the non-negative remaining
  monotonic duration.

### Step 5.5: add private constructors on raw owners

- [ ] Add the same underscore-prefixed method to raw `Connection` and
  `Transaction`:

  ```python
  def _native_bulk_sequence(
      self,
      table: str,
      columns: list[str],
      *,
      chunk_size: int = 1000,
  ) -> _NativeBulkSequence: ...
  ```

- [ ] Keep the public raw `native_bulk_insert(... rows: list[list[Any]])`
  signature and behavior unchanged.

- [ ] Run focused Rust tests after every state transition, then:

  ```bash
  cargo fmt --check
  cargo clippy --all-targets -- -D warnings
  cargo test --locked
  ```

---

## Task 6: implement the bounded Python coordinator

**Branch:** continue `feat/bulk-iterable-backpressure`

**Files:**

- create `python/fastmssql/_bulk_iterable.py`;
- modify `python/fastmssql/__init__.py`;
- modify `python/fastmssql/__init__.pyi`;
- modify `python/fastmssql/fastmssql.pyi`;
- modify `README.md`;
- update `VERSION.md`.

**Consumes:** the private raw sequence from Task 5.

**Produces:** widened public wrappers with deterministic bounded producer
semantics.

### Step 6.1: implement one protocol adapter

- [ ] Keep public dispatch explicit:

  ```python
  if isinstance(rows, list):
      return await raw.native_bulk_insert(
          table,
          columns,
          rows,
          chunk_size=chunk_size,
      )
  return await native_bulk_insert_iterable(
      raw,
      table,
      columns,
      rows,
      chunk_size=chunk_size,
  )
  ```

- [ ] Reject mapping/string/binary top-level values locally.
- [ ] Prefer `__aiter__` over `__iter__`; acquire the selected protocol once.
- [ ] Construct/validate the raw sequence before producer advancement.
- [ ] Await `reserve()` before first `next/anext`.

### Step 6.2: normalize and push bounded chunks

- [ ] The core loop has this ordering:

  ```text
  pull one row
  activate on the first observed row
  normalize one non-string Sequence to list
  append to current chunk
  when len(chunk) == chunk_size:
      await sequence.push(chunk)
      drop chunk
  repeat
  push final partial chunk
  await sequence.finish()
  ```

- [ ] Use `sequence.remaining_timeout()` around every async pull.
- [ ] Check the remaining deadline after every sync pull and normalization
  boundary.
- [ ] Never advance the producer while `sequence.push()` is pending.

### Step 6.3: make cleanup cancellation-safe

- [ ] Centralize strong-referenced shielded cleanup:

  ```python
  async def _await_cleanup(awaitable, *, name: str):
      task = asyncio.create_task(awaitable, name=name)
      while not task.done():
          try:
              await asyncio.shield(task)
          except asyncio.CancelledError:
              continue
      return task.result()
  ```

  The coordinator retains and later re-raises the original cancellation;
  repeated cancellation requests during cleanup may not abandon the task.

- [ ] On an abnormal exit:

  1. stop pulling;
  2. call `abort("cancelled")` or `abort("error")`;
  3. call producer `aclose()` or `close()` when available;
  4. attach the first cleanup error as cause;
  5. re-raise the original `BaseException`.

- [ ] On async-pull expiry, call `sequence.expire()` so Rust supplies the
  typed timeout, then close the producer and keep cleanup evidence as cause.

### Step 6.4: document the boundary precisely

- [ ] README examples cover a sync generator and an async generator.
- [ ] Document:

  - `chunk_size` bounds memory by row count, not byte size;
  - a single row/LOB may still dominate memory;
  - sync `next()` runs on the event-loop thread and cannot be preempted;
  - blocking sources must be modeled as async iterables;
  - Connection owns atomic commit/rollback;
  - caller Transaction remains settlement-neutral;
  - raw extension remains list-only.

- [ ] Run focused offline tests and require GREEN:

  ```bash
  ../../.venv/bin/pytest \
    tests/test_native_bulk_iterable_contract.py \
    tests/test_native_bulk_iterable_coordinator.py \
    tests/test_native_bulk_contract.py \
    tests/test_native_bulk_iterable_stress_contract.py -q
  ../../.venv/bin/ruff check \
    python/fastmssql/_bulk_iterable.py \
    tests/test_native_bulk_iterable_contract.py \
    tests/test_native_bulk_iterable_coordinator.py \
    tests/test_native_bulk_iterable_stress_contract.py \
    scripts/sql_auth/native_bulk_iterable_stress.py
  ```

---

## Task 7: drive canonical SQL-auth cases GREEN

**Branch:** continue `feat/bulk-iterable-backpressure`

**Files:** production and test files from Tasks 3–6; make only the smallest
behavioral corrections proved necessary by a failing contract.

### Step 7.1: rebuild the exact feature worktree

- [ ] Use isolated temporary Cargo/uv caches and release mode:

  ```bash
  CARGO_TARGET_DIR=/private/tmp/fastmssql-bulk-iterable-target \
  UV_CACHE_DIR=/private/tmp/fastmssql-bulk-iterable-uv-cache \
    uv sync --locked --all-extras --dev
  CARGO_TARGET_DIR=/private/tmp/fastmssql-bulk-iterable-target \
  UV_CACHE_DIR=/private/tmp/fastmssql-bulk-iterable-uv-cache \
    uv run maturin develop --release
  ```

- [ ] Prove the loaded `fastmssql` package and extension originate from this
  feature worktree/build, not another editable checkout.

### Step 7.2: run the new cases on Docker SQL Server

- [ ] Require all `BULK-013`–`BULK-021` cases to pass with SQL authentication:

  ```bash
  ../../.venv/bin/pytest \
    tests/sql_auth_strict/test_native_bulk_iterable_strict.py -q
  ```

- [ ] Run existing `BULK-006`–`BULK-012` cases unchanged:

  ```bash
  ../../.venv/bin/pytest \
    tests/sql_auth_strict/test_native_bulk_strict.py -q
  ```

- [ ] Inspect after success:

  - operation metrics contain one complete bulk outcome;
  - pool capacity is restored;
  - caller transaction state matches the case;
  - no test application session remains;
  - no SQL object from the cases remains.

### Step 7.3: debug failures systematically

- [ ] For every failure, record the earliest violated invariant before
  editing.
- [ ] Add or tighten the smallest deterministic regression test, observe it
  fail, then implement the fix.
- [ ] If a correction changes the approved public behavior or private
  protocol, update focused design, plan and test together on an explicit
  corrective test branch before merging its history into the feature branch.
- [ ] Never relax an atomicity, cancellation, timeout, privacy, buffer or
  cleanup assertion to obtain GREEN.

---

## Task 8: run bounded real-SQL stress through 99,999 rows

**Branch:** clean exact `feat/bulk-iterable-backpressure` candidate.

**Artifacts:** write only under `/private/tmp`, with filenames containing the
full candidate SHA.

### Step 8.1: mandatory profiles

- [ ] Run:

  ```bash
  CANDIDATE_SHA="$(git rev-parse HEAD)"
  ../../.venv/bin/python \
    scripts/sql_auth/native_bulk_iterable_stress.py \
    --profiles 1_000:100,10_000:1_000 \
    --modes sync,async \
    --metrics-output \
      "/private/tmp/native-bulk-iterable-${CANDIDATE_SHA}.json"
  ```

- [ ] Require for every profile:

  ```text
  affected rows              == requested rows
  persisted rows             == requested rows
  producer pulls             == requested rows
  maximum buffered rows      <= chunk_size
  RSS growth                 <= 64 MiB
  maximum event-loop gap     <= 0.100 s
  maximum candidate sessions == 1
  terminal bulk metrics      == exactly one success
  post-load smoke            == true
  teardown sessions          == 0
  violations                 == []
  ```

### Step 8.2: approved extended profiles

- [ ] Run both producer modes with explicit authorization:

  ```bash
  CANDIDATE_SHA="$(git rev-parse HEAD)"
  ../../.venv/bin/python \
    scripts/sql_auth/native_bulk_iterable_stress.py \
    --profiles 99_999:1_000 \
    --modes sync,async \
    --allow-extended \
    --metrics-output \
      "/private/tmp/native-bulk-iterable-99999-${CANDIDATE_SHA}.json"
  ```

- [ ] Repeat the same hard gates. Report observed local-host throughput and
  memory as characterization, not a universal SQL Server capacity claim.

- [ ] After each run, query DMV state from the independent observer and
  require zero candidate sessions.

---

## Task 9: run complete source, SQL-auth and wheel gates

**Branch:** clean exact feature candidate SHA.

### Step 9.1: Rust quality and unit gates

- [ ] Run:

  ```bash
  cargo fmt --check
  cargo clippy --all-targets -- -D warnings
  cargo test --locked
  cargo fmt --manifest-path vendor/tiberius/Cargo.toml --check
  cargo test \
    --manifest-path vendor/tiberius/Cargo.toml \
    --no-default-features \
    --features chrono,tds73,rustls \
    --lib
  ```

- [ ] Run the canonical `tiberius-clippy` lane from
  `scripts/sql_auth/run_all.sh`; it owns the audited
  `chrono,tds73,rustls` feature set and legacy-lint allowlist. Do not replace
  that lane with a different ad-hoc vendored Clippy profile.

### Step 9.2: Python and strict suites

- [ ] Run the full deterministic SQL-auth suite, true-async suite, framework
  suite, resilience/load lanes and original local regressions through their
  existing canonical runners.
- [ ] Run all repository tests selected by the existing lockfile environment.
- [ ] Run Ruff and every stub/matrix/report contract.
- [ ] Record exact pass/fail/skip/deselection counts. A skipped, deselected or
  stale-build result is not a pass.

### Step 9.3: build and test an isolated ABI3 wheel

- [ ] Build from the exact clean candidate SHA into `/private/tmp`.
- [ ] Record wheel filename and SHA-256; do not publish it.
- [ ] Install into a fresh isolated environment outside the repository.
- [ ] Remove `PYTHONPATH` and prove import originates from isolated
  `site-packages`.
- [ ] Run:

  - offline iterable coordinator/public-stub contracts;
  - `BULK-013`–`BULK-021` against Docker SQL Server;
  - representative list native-bulk, transaction, query and ResultStream
    regressions;
  - runtime dependency/import checks.

### Step 9.4: graph and source self-review

- [ ] Rebuild:

  ```bash
  uvx code-review-graph build
  ```

- [ ] Use `detect_changes`, `get_affected_flows` and `tests_for` to review:

  - `Connection.native_bulk_insert`;
  - `Transaction.native_bulk_insert`;
  - transaction state/settlement;
  - lifecycle shutdown;
  - operation metrics;
  - timeout and cancellation;
  - native conversion diagnostics;
  - wrapper/stub/package import paths.

- [ ] Read every changed hunk after graph review.
- [ ] Run `git diff --check`.
- [ ] Scan tracked changes for credential values, connection strings,
  generated artifacts and accidental original-repository URLs.
- [ ] Require a clean worktree before the technical commit.

### Step 9.5: commit and push only the fork

- [ ] Commit the cohesive feature:

  ```bash
  git commit -m "feat: stream bulk iterable input with backpressure"
  ```

- [ ] Verify the feature commit descends from the exact RED commit.
- [ ] Push only `feat/bulk-iterable-backpressure`.
- [ ] Verify local and `origin` SHA equality and confirm `upstream` push
  remains `DISABLED`.
- [ ] Read public GitHub Actions for the fork only. Record exact candidate
  Linux/macOS/Windows and RustSec status; do not trigger publication or infer
  success from an ancestor.

---

## Task 10: update the live audit from verified evidence

**Branch:** `docs/bulk-iterable-backpressure-status`

**Base:** exact verified Task 9 feature commit.

**Files:**

- modify `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`;
- create
  `docs/NATIVE_BULK_ITERABLE_BACKPRESSURE_VALIDATION_REPORT.md`;
- update
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
  only if the verified slice changes PR grouping;
- update `VERSION.md`.

- [ ] Mark only native-bulk iterable backpressure `VERIFIED_FORK`.
- [ ] Keep `execute_many()` and `query_many()` visibly open.
- [ ] Record:

  - design, plan, RED, feature and status SHAs;
  - exact ancestry;
  - intended RED failure;
  - focused and full local counts;
  - SQL-auth atomicity/state/timeout/cancellation evidence;
  - sync/async 1,000, 10,000 and 99,999 stress metrics;
  - isolated wheel filename/hash/import evidence;
  - graph metadata and reviewed impact;
  - fork hosted status;
  - residual single-row/LOB and blocking-sync-producer limits.

- [ ] Do not copy credentials, raw connection strings or privacy-sensitive
  exception values into the report.
- [ ] Self-review the report against raw `/private/tmp` evidence and exact
  Git output.
- [ ] Rebuild graph, run `git diff --check`, scan credentials and commit:

  ```bash
  git commit -m "docs: record bulk iterable validation evidence"
  ```

- [ ] Push only `docs/bulk-iterable-backpressure-status` and verify its exact
  remote SHA.

---

## Final acceptance checklist

- [ ] Concrete lists still take the unchanged raw fast path.
- [ ] Public wrappers accept sync and async row producers; raw methods remain
  list-only.
- [ ] Invalid input advances the producer zero times.
- [ ] Caller Transaction is reserved before the first producer pull.
- [ ] Empty producers perform no SQL and start no bulk metric.
- [ ] At most `chunk_size` rows are retained and no pull overlaps a push.
- [ ] One Connection lease/transaction/deadline/metric spans all chunks.
- [ ] Connection failures roll back every earlier chunk.
- [ ] Caller Transaction success is settlement-neutral; post-wire failure is
  rollback-only.
- [ ] Global diagnostic indices remain exact across chunks.
- [ ] Producer errors, cancellation and timeout stop pulls and complete
  shielded cleanup without losing the primary exception.
- [ ] Docker SQL-auth cases `BULK-013`–`BULK-021` pass.
- [ ] Sync/async 1,000, 10,000 and 99,999 stress profiles pass all hard gates.
- [ ] Root/vendor Rust, Python, strict, framework, resilience, load and wheel
  gates pass with exact recorded counts.
- [ ] Code-review-graph and final manual diff review find no untested affected
  flow or credential/artifact leak.
- [ ] The live audit distinguishes local, wheel and hosted evidence.
- [ ] Every pushed commit exists only on Marcel Galea's fork; original-repo
  push and PR creation did not occur.
