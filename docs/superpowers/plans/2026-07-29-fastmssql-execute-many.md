# FastMssql Bounded `execute_many()` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` task by task, use
> `superpowers:systematic-debugging` for every unexpected failure, use
> `superpowers:test-driven-development` for every behavior change and use
> `superpowers:verification-before-completion` before every commit or status
> claim. Every checkbox is an execution gate, not a retrospective summary.

**Goal:** add a production-safe `execute_many()` that repeats one SQL
statement over bounded list, synchronous-iterable or asynchronous-iterable
parameter sets with atomic-by-default Connection semantics, explicit
per-chunk partial commits, settlement-neutral caller Transaction semantics
with fail-closed security-context retirement, one deadline, one metric and
deterministic cleanup.

**Architecture:** concrete Python lists use bounded raw PyO3 adapters.
Arbitrary producers use a shared Python bounded-sequence coordinator over a
private Rust `_ExecuteManySequence`. Python owns producer protocol and close
semantics; Rust owns parameter conversion, lifecycle admission, the physical
lease, transaction state, SQL execution, deadline, operation metric,
settlement and connection disposition.

**Tech stack:** Python 3.11–3.14, `asyncio`, Rust 2021, PyO3 and
`pyo3-async-runtimes` 0.29, Tokio, vendored Tiberius 0.12.3, bb8, SQL Server
2022 Docker with SQL authentication, pytest/pytest-asyncio, Ruff, maturin,
Cargo fmt/Clippy/test, Git worktrees and code-review-graph.

## Global constraints

- Exact focused-design commit:
  `d3b89acdb31672daf589d7fa52101825d0690d91`.
- Exact design base:
  `a36525834011db0f5707bfcac5bda195fd498fe5`.
- Branch topology:

  ```text
  docs/execute-many-design
    -> test/execute-many
    -> feat/execute-many
    -> docs/execute-many-status
  ```

- The RED branch starts from the exact design-and-plan commit.
- The feature branch must descend from the commit where focused and real SQL
  RED were observed.
- Reuse the existing isolated project-local worktree or create another under
  the ignored `.worktrees/` directory; never use `/tmp` for repository feature
  work.
- Push only to `https://github.com/galeamarcel/FastMssql.git`.
- Keep the `Rivendael/FastMssql` push URL exactly `DISABLED`.
- Do not create or update a pull request against the original repository.
- Keep the displayed package version `0.7.7`; this slice remains part of the
  unreleased `0.8.0` candidate history.
- Update `VERSION.md` in every documentation, test, feature, corrective and
  status commit.
- Do not change public `execute()`, `execute_batch()`, compatibility bulk,
  native bulk, `query_batch()` or result-stream behavior.
- Do not implement `query_many()`, prepared-handle caching, named parameters,
  TVPs, new SQL type families or byte-level LOB streaming.
- Never call `list(parameter_sets)` for a non-list source, prefetch beyond one
  chunk, create a task per set or use an unbounded queue.
- Never use one public `execute()` call per set. Internal work must not restart
  deadlines or inflate public operation/settlement metrics.
- Never swallow `BaseException`; cancellation and process-level exceptions
  remain primary.
- Never retry a statement or a lost commit acknowledgement.
- Never print or commit SQL-auth passwords, connection URLs, local `.env`
  contents, stress JSON, wheels, build caches or generated vendored
  lockfiles.
- A local pass is not a hosted pass. Fork GitHub Actions status is recorded
  independently as PASS, FAIL or NOT RUN.

---

## Task 1: publish the focused design and executable plan

**Branch:** `docs/execute-many-design`

**Files:**

- retain
  `docs/superpowers/specs/2026-07-29-fastmssql-execute-many-design.md`;
- create `docs/superpowers/plans/2026-07-29-fastmssql-execute-many.md`;
- update `VERSION.md`.

- [ ] Confirm the exact design commit is the parent, the worktree contains
  only the plan edit and the remotes are safe:

  ```bash
  git rev-parse HEAD
  git rev-parse HEAD^
  git status --short --branch
  git remote -v
  git config --get remote.upstream.pushurl
  ```

- [ ] Self-review this plan against the focused design and current source:

  - invalid public arguments advance no producer;
  - async protocol wins for a dual-protocol object;
  - caller `Transaction` is reserved before the first pull;
  - empty input restores reservation and performs no pool/SQL/metric work;
  - normal exhaustion is not followed by an extra producer close;
  - abnormal termination closes the producer without masking the primary
    exception;
  - concrete lists use the bounded raw adapter;
  - non-list sources own at most `chunk_size` sets;
  - no next producer pull occurs while Rust `push()` is pending;
  - the same SQL executes sequentially in input order on one TDS session;
  - one absolute deadline covers producer wait, pool/TDS work and settlement;
  - `atomic=True` rolls back all chunks after a known failure;
  - `atomic=False` preserves only acknowledged earlier chunk commits;
  - active caller Transaction success is settlement-neutral and post-wire
    error is rollback-only;
  - commit acknowledgement loss is never rolled back or retried;
  - global set index and confirmed-commit evidence remain truthful;
  - schema 2 records exactly one `execute_many` and no internal metric
    inflation.

- [ ] Review the complete plan for unresolved placeholder markers or
  non-executable instructions and require no match.

- [ ] Run:

  ```bash
  git diff --check
  .venv/bin/pytest tests/sql_auth_strict/test_matrix_contract.py -q
  ```

- [ ] Scan staged files for credentials without printing any credential
  value.

- [ ] Commit exactly the plan and its `VERSION.md` entry:

  ```bash
  git add \
    docs/superpowers/plans/2026-07-29-fastmssql-execute-many.md \
    VERSION.md
  git commit -m "docs: plan bounded execute many"
  ```

- [ ] Rebuild code-review-graph on the exact plan commit and require
  branch/SHA freshness plus zero changed runtime entities or affected flows:

  ```bash
  uvx code-review-graph build
  ```

- [ ] Push only `docs/execute-many-design`; verify the exact remote SHA equals
  local HEAD and `upstream` push remains `DISABLED`.

---

## Task 2: create deterministic offline RED contracts

**Branch:** `test/execute-many`

**Base:** exact Task 1 commit.

**Files:**

- create `tests/test_execute_many_contract.py`;
- create `tests/test_execute_many_coordinator.py`;
- create `tests/test_execute_many_stress_contract.py`;
- modify `tests/test_operation_metrics_contract.py`;
- modify `tests/test_transaction_flags.py` only for the new exclusive state;
- update `VERSION.md`.

### Step 2.1: lock the public/raw API

- [ ] Require wrapper aliases:

  ```python
  from collections.abc import AsyncIterable, Iterable
  from typing import Any, TypeAlias

  ParameterSet: TypeAlias = list[Any] | Parameters
  ParameterSetSource: TypeAlias = (
      list[ParameterSet]
      | Iterable[ParameterSet]
      | AsyncIterable[ParameterSet]
  )
  ```

- [ ] Require exact public signatures:

  ```python
  async def Connection.execute_many(
      self,
      sql: str,
      parameter_sets: ParameterSetSource,
      *,
      atomic: bool = True,
      chunk_size: int = 1000,
  ) -> int: ...

  async def Transaction.execute_many(
      self,
      sql: str,
      parameter_sets: ParameterSetSource,
      *,
      chunk_size: int = 1000,
  ) -> int: ...
  ```

- [ ] Require raw-extension stubs to accept concrete lists only.
- [ ] Assert Transaction rejects an `atomic` keyword rather than silently
  ignoring it.
- [ ] Use raw sentinels to prove concrete lists call the matching raw
  `execute_many` directly and never instantiate the iterable coordinator.
- [ ] Prove `execute`, `execute_batch`, `bulk_insert` and
  `native_bulk_insert` signatures remain byte-for-byte unchanged.

### Step 2.2: specify strict validation and empty behavior

- [ ] Require `atomic` to be exactly `bool`.
- [ ] Require `chunk_size` to be an integer other than `bool` in
  `1..=10_000`.
- [ ] Reject top-level mappings, text and bytes-like sources before
  `iter()`/`aiter()`.
- [ ] Require each yielded set to be exactly a Python list or a positional
  FastMssql `Parameters`; reject tuple, mapping, text, bytes-like and `None`.
- [ ] Require named `Parameters` and sets over 2,098 user parameters to use
  the existing typed conversion errors.
- [ ] Prove invalid top-level arguments cause zero producer calls, zero raw
  calls and zero sequence reservation.
- [ ] Prove empty list/sync/async inputs return `0`; fake sequence history must
  show reservation release with zero activation, push or metric.
- [ ] Prove normal exhaustion does not invoke an additional
  `close()`/`aclose()`.
- [ ] For list input, prove captured outer length is checked at chunk
  boundaries and a detected resize is typed rather than panicking.

### Step 2.3: specify the private coordinator protocol

- [ ] Fake raw owners expose one synchronous constructor:

  ```python
  sequence = raw._execute_many_sequence(
      sql,
      atomic=atomic,       # Connection only
      chunk_size=chunk_size,
  )
  ```

- [ ] Fake sequences record awaited calls:

  ```text
  reserve()
  activate()
  push(list[ParameterSet])
  finish() -> int
  abort("error" | "cancelled") -> {
      active_parameter_set_index: int | None,
      confirmed_committed_parameter_sets: int,
      partial_commit_possible: bool,
  }
  expire()
  remaining_timeout() -> float | None
  ```

- [ ] Assert `iter()` or `aiter()` is acquired exactly once. A dual-protocol
  producer must record async acquisition and zero sync acquisition.
- [ ] Assert Transaction reservation finishes before the first producer pull.
- [ ] Assert the first item activates the sequence exactly once and each full
  or normally exhausted partial chunk is pushed once.
- [ ] With `chunk_size=2` and five items, require push sizes `[2, 2, 1]`, exact
  order, maximum retained sets `2` and no sixth pull.
- [ ] Block fake `push()` and prove producer pull count does not advance until
  it completes.
- [ ] Reject any source inspection, task-per-set scheduling, unbounded queue or
  whole-input materialization.

### Step 2.4: specify timeout, cancellation and cleanup

- [ ] Use one fake monotonic deadline token and prove every
  `anext()`, `push()`, `finish()`, `abort()` and `expire()` derives from the
  same remaining budget.
- [ ] Cover cancellation:

  1. while waiting in `anext()`;
  2. after reservation is effective but before Python observes it;
  3. while a Rust chunk push is pending;
  4. during producer close;
  5. while cleanup itself raises.

- [ ] Cover timeout during sync normalization, `anext()`, pending push and
  settlement.
- [ ] Require abnormal sync/async producer close to be shielded to terminal
  completion.
- [ ] Require the original producer, conversion, SQL, timeout or cancellation
  exception to remain primary; attach only the first cleanup error as
  `__cause__`.
- [ ] Require every error to expose:

  ```text
  parameter_set_index
  confirmed_committed_parameter_sets
  partial_commit_possible
  ```

- [ ] When Python owns the primary producer/cancellation exception, merge the
  adapter's current index with the authoritative Rust `abort()` progress
  snapshot; Rust's active statement index wins and confirmed commits are
  never inferred from an unacknowledged push.
- [ ] For commit acknowledgement loss, require `parameter_set_index` to be
  the first set in the unconfirmed commit, not a fictitious failed statement.
- [ ] Assert messages/metadata never contain SQL text, values or producer
  representations.

### Step 2.5: lock schema-2 operation metrics

- [ ] Require `schema_version == 2`.
- [ ] Require this exact 14-key order:

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

- [ ] Require Connection and Transaction success/error/timeout/cancellation/
  outcome-unknown paths to finish exactly one `execute_many` observer.
- [ ] Require empty and invalid calls to record no observer.
- [ ] Require internal statement and settlement work to leave `execute`,
  `begin`, `commit` and `rollback` deltas at zero.

### Step 2.6: observe offline RED

- [ ] Run existing bulk/coordinator/metric tests first and require GREEN:

  ```bash
  .venv/bin/pytest \
    tests/test_native_bulk_iterable_contract.py \
    tests/test_native_bulk_iterable_coordinator.py -q
  ```

- [ ] Run new tests plus the deliberately changed schema contract:

  ```bash
  .venv/bin/pytest \
    tests/test_execute_many_contract.py \
    tests/test_execute_many_coordinator.py \
    tests/test_execute_many_stress_contract.py \
    tests/test_operation_metrics_contract.py -q
  ```

- [ ] Record failures as missing `execute_many`, missing schema 2 or missing
  private sequence behavior. A collection/import/environment failure is not
  acceptable RED evidence.

---

## Task 3: add canonical SQL-auth and stress RED contracts

**Branch:** continue `test/execute-many`.

**Files:**

- modify
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`;
- modify `tests/sql_auth_strict/cases.py`;
- create `tests/sql_auth_strict/test_execute_many_strict.py`;
- modify `tests/sql_auth_strict/test_operation_metrics.py`;
- modify `tests/sql_auth_strict/operation_metrics_assertions.py`;
- modify `tests/sql_auth_strict/test_matrix_contract.py`;
- modify `scripts/sql_auth/run_all.sh`;
- create `scripts/sql_auth/execute_many_stress.py`;
- update `VERSION.md`.

### Step 3.1: register the matrix exactly

- [ ] Add `EMANY-001` through `EMANY-011` exactly once to the canonical
  SQL-auth specification and case registry.
- [ ] Change the required unique case count from 396 to 407.
- [ ] Attach every ID to exactly one source test and one canonical runner
  lane.
- [ ] Keep historical generated matrix/report evidence unchanged on RED.
- [ ] Run the 26 matrix contracts and require all of them to pass.

### Step 3.2: encode the real SQL Server cases

- [ ] `EMANY-001`: wrapper/raw/stub signatures and concrete-list fast path.
- [ ] `EMANY-002`: invalid atomic/chunk/source/set causes zero producer, pool,
  SQL and metric activity.
- [ ] `EMANY-003`: empty list/sync/async returns zero, creates no pool/SQL/
  metric and restores active Transaction reservation.
- [ ] `EMANY-004`: list/sync/async inputs preserve exact order, affected total,
  typed values and TDS-gated one-chunk pulls.
- [ ] `EMANY-005`: default atomic mode rolls back earlier chunks after late
  conversion, constraint and producer failures; assert global set index.
- [ ] `EMANY-006`: `atomic=False` preserves acknowledged earlier chunks,
  rolls back the failing chunk and reports exact confirmed commits.
- [ ] `EMANY-007`: cancellation during producer wait and SQL stops pulls,
  records one cancellation and leaks no session.
- [ ] `EMANY-008`: timeout during producer wait and SQL is typed, bounded,
  privacy-safe and leaves the pool reusable.
- [ ] `EMANY-009`: ordinary active-Transaction success is
  settlement-neutral; post-wire error is rollback-only and only caller
  rollback settles it. Successfully consumed security-context SQL returns
  its response but retires the physical connection, fails the Transaction
  and proves pool recovery on a distinct `connection_id`.
- [ ] `EMANY-010`: parameterized INSERT/UPDATE/DELETE and direct stored
  procedure execution return exact totals with one schema-2 metric and zero
  internal metric deltas.
- [ ] `EMANY-011`: fault-proxy loss of atomic/chunk COMMIT acknowledgement is
  `CommitOutcomeUnknown`, causes no retry/rollback and reports truthful
  unconfirmed-chunk metadata.

- [ ] Use unique schema names/application names and deterministic DMV
  observers.
- [ ] Assert no test application session or SQL fixture remains after every
  case.
- [ ] Do not catch-and-ignore an unavailable API or failed cleanup.

### Step 3.3: create the bounded stress contract

- [ ] Require profiles:

  ```text
  1,000 sync  atomic=True  chunk=100
  1,000 async atomic=True  chunk=100
  10,000 sync  atomic=True  chunk=1,000
  10,000 async atomic=True  chunk=1,000
  10,000 sync  atomic=False chunk=1,000
  99,999 sync  atomic=True  chunk=1,000
  99,999 async atomic=True  chunk=1,000
  ```

- [ ] Make 99,999 opt-in through an explicit `--allow-extended` flag.
- [ ] Require exact pulls/executed/affected/persisted counts, maximum buffered
  sets/cells, RSS, event-loop gap, physical identity/session count,
  operation/pool deltas, confirmed commits, smoke and teardown evidence.
- [ ] Enforce:

  ```text
  buffered sets             <= chunk_size
  RSS growth                <= 64 MiB
  maximum loop gap          <= 0.100 s
  errors/timeouts           == 0
  execute_many success      == 1
  execute delta             == 0
  post-load smoke           == true
  teardown app sessions     == 0
  ```

- [ ] Write JSON only to an explicit caller-supplied path outside the
  repository.

### Step 3.4: observe real RED and commit it

- [ ] Start/verify the authorized Docker SQL Server and build the exact RED
  worktree.
- [ ] Run all pre-existing batch/native-bulk/transaction SQL-auth cases and
  require GREEN.
- [ ] Run the changed schema-2 metric contracts separately and require only
  the exact version/key RED while the runtime still exposes schema 1.
- [ ] Run `test_execute_many_strict.py`; require a deterministic absent-API or
  missing-behavior failure, not authentication, stale wheel or environment
  failure.
- [ ] Run the smallest stress profile; require the same intended RED.
- [ ] Add the RED outcome to `VERSION.md`.
- [ ] Run `git diff --check`, Ruff on new Python tests/scripts and a
  credential scan.
- [ ] Commit all immutable RED requirements:

  ```bash
  git commit -m "test: require bounded parameterized execute many"
  ```

- [ ] Verify this RED commit descends from the exact plan commit.
- [ ] Push only `test/execute-many` and verify fork remote/local SHA equality.

---

## Task 4: extract the shared bounded producer coordinator

**Branch:** create `feat/execute-many` from the exact RED commit.

**Files:**

- create `python/fastmssql/_bounded_sequence.py`;
- modify `python/fastmssql/_bulk_iterable.py`;
- update focused characterization tests if a missing invariant is found;
- update `VERSION.md`.

### Step 4.1: characterize the existing native-bulk adapter

- [ ] Run its complete coordinator/contract/real-SQL cases before editing.
- [ ] If extraction needs a previously implicit behavior, add the smallest
  native-bulk characterization test and observe it fail for the missing
  seam—not for changed public behavior.
- [ ] Record current task names, protocol preference, close behavior,
  cancellation shielding, deadline calls and exception chaining.

### Step 4.2: extract without semantic change

- [ ] Move only reusable mechanics:

  - one preferred producer protocol acquisition;
  - one-item normalization callback;
  - bounded chunk assembly;
  - reservation-before-pull ownership;
  - deadline-aware sync/async pulls;
  - pending-push gating;
  - shielded abort/expire/producer-close cleanup;
  - original-exception preservation.

- [ ] Keep feature-specific names, metadata and raw constructor calls in
  `_bulk_iterable.py`.
- [ ] Preserve the concrete-list native-bulk fast path and every existing task
  name/public traceback contract.
- [ ] Do not introduce one generic state enum in Rust.

### Step 4.3: verify and commit the refactor

- [ ] Run all native-bulk offline tests, `BULK-013`–`BULK-021`, Ruff and
  `compileall`.
- [ ] Require no change in SQL rows, metrics, state, cancellation or timeout
  evidence.
- [ ] Update `VERSION.md`.
- [ ] Commit:

  ```bash
  git commit -m "refactor: share bounded producer coordination"
  ```

---

## Task 5: add operation-metrics schema 2

**Branch:** continue `feat/execute-many`.

**Files:**

- modify `src/deadline.rs`;
- modify `src/operation_metrics.rs`;
- modify `python/fastmssql/__init__.pyi`;
- modify `python/fastmssql/fastmssql.pyi`;
- modify `tests/test_operation_metrics_contract.py`;
- modify `tests/sql_auth_strict/operation_metrics_assertions.py`;
- modify relevant metric/stress validators;
- modify `README.md`;
- update `VERSION.md`.

### Step 5.1: make the metric RED exact

- [ ] Run only schema/order/enum/serialization tests and retain their exact
  missing-key/version failure.
- [ ] Add a Rust unit assertion for 14 operations and stable enum-to-index
  mapping if the RED test does not already reach that boundary.

### Step 5.2: implement one stable schema migration

- [ ] Insert `OperationName::ExecuteMany` between `Execute` and `QueryBatch`.
- [ ] Change all fixed operation arrays and maps from 13 to 14 entries.
- [ ] Change only current runtime/stub/docs schema declarations from `1` to
  `2`; do not rewrite historical evidence.
- [ ] Preserve existing key order and counters for all other operations.
- [ ] Reject duplicate, omitted or reordered operation names in tests.

### Step 5.3: verify and commit

- [ ] Run root Rust metric tests, offline metric contracts, strict operation
  metric cases and stress-contract validators.
- [ ] Confirm no public operation emits `execute_many` yet; this commit adds
  schema capacity only.
- [ ] Update `VERSION.md`.
- [ ] Commit:

  ```bash
  git commit -m "feat: add execute many operation metrics"
  ```

---

## Task 6: implement the Rust execute-many engine and state machine

**Branch:** continue `feat/execute-many`.

**Files:**

- create `src/execute_many.rs`;
- create `src/execute_many_sequence.rs`;
- modify `src/transaction.rs`;
- modify `src/connection.rs`;
- modify `src/lib.rs`;
- update `VERSION.md`.

### Step 6.1: implement local validation and checked progress

- [ ] Add Rust unit RED for:

  - strict bool/chunk bounds;
  - empty list zero activation;
  - captured list length and resize detection;
  - global parameter-set index across chunks;
  - per-set 2,098-parameter enforcement;
  - checked per-item/chunk/total affected-count overflow;
  - privacy-safe error metadata.

- [ ] Reuse `convert_parameters_to_fast()` and the existing positional
  `Parameters` rules; do not add a second value converter.
- [ ] Preserve direct-batch handling for empty-parameter scope-sensitive DDL.
- [ ] Fully consume affected-count tokens before the next set.

### Step 6.2: encode settlement decisions independently

- [ ] Add pure/state unit RED for:

  ```text
  Connection atomic=True:
    one BEGIN, all pushes, one COMMIT
    known failure -> one rollback
    commit unknown -> discard, no rollback/retry

  Connection atomic=False:
    one BEGIN/COMMIT per non-empty pushed chunk
    earlier acknowledged chunks remain committed
    failing current chunk rolls back
    current commit unknown -> stop/discard, no rollback/retry

  active Transaction:
    no driver-owned settlement
    pre-wire producer failure -> Active
    post-wire failure -> RollbackOnly
    caller rollback/close remains available
  ```

- [ ] Require partial-buffer producer failure to send no partial chunk.
- [ ] Require `confirmed_committed_parameter_sets` to advance only after an
  acknowledged chunk commit.
- [ ] Require `partial_commit_possible` only for truthful partial mode.

### Step 6.3: add explicit Transaction reservation

- [ ] Add only `ExecuteManyProducing`; do not rename/generalize
  `BulkProducing`.
- [ ] Reserve before producer pull and reject concurrent query/execute/
  stream/bulk/commit/second-execute-many operations.
- [ ] Restore `Active` on empty input and producer failure before any
  statement reaches the wire.
- [ ] After possible wire activity, transition to the existing rollback-only
  state and preserve caller recovery methods.
- [ ] Prove a dropped, timed-out or cancelled sequence cannot strand the
  reservation.

### Step 6.4: own one deadline, observer and physical lease

- [ ] Capture one absolute deadline at sequence construction before producer
  acquisition.
- [ ] Do not start lifecycle/pool/metric activity for invalid or empty input.
- [ ] On first activation, own one lifecycle permit, one lease and one
  `execute_many` observer until terminal settlement/disposition.
- [ ] Run TDS awaits through the existing deadline helpers.
- [ ] Finish exactly one terminal metric classification.
- [ ] Suppress public `execute`, `begin`, `commit` and `rollback` metrics for
  internal work.

### Step 6.5: implement `_ExecuteManySequence`

- [ ] Implement explicit states for reserved, active, pushing, finishing,
  terminal and rollback-only handoff.
- [ ] Expose synchronous construction and async:

  ```text
  reserve
  activate
  push
  finish
  abort
  expire
  remaining_timeout
  ```

- [ ] Retain exact active/global indices across cancellation and timeout.
- [ ] Make successful `abort()` return the terminal
  `active_parameter_set_index`, `confirmed_committed_parameter_sets` and
  `partial_commit_possible` snapshot so Python can annotate its own primary
  exception without guessing.
- [ ] On dropped live sequences, schedule bounded terminal cleanup on the
  runtime and fail closed if cleanup cannot be scheduled.
- [ ] Retire/discard any connection with unread response data, timed-out TDS,
  cancellation, rollback failure or unknown commit outcome.
- [ ] Never return pool capacity before disposition is terminal.

### Step 6.6: add raw list adapters and private factories

- [ ] `Connection.execute_many(list, atomic, chunk_size)` chunks the captured
  list through the same engine.
- [ ] `Transaction.execute_many(list, chunk_size)` uses the same reservation
  and never settles caller state.
- [ ] `_execute_many_sequence(...)` factories are private and used only by
  the Python iterable adapter.
- [ ] Register the private class in `src/lib.rs` without exposing it as a
  documented public API.

### Step 6.7: verify and commit the native layer

- [ ] Run focused Rust unit tests after every smallest implementation step.
- [ ] Run:

  ```bash
  cargo fmt --check
  cargo clippy --all-targets -- -D warnings
  cargo test --locked
  ```

- [ ] Build the extension and run raw-list execute-many RED tests.
- [ ] Update `VERSION.md`.
- [ ] Commit:

  ```bash
  git commit -m "feat: add stateful execute many sequence"
  ```

---

## Task 7: expose bounded iterable `execute_many()`

**Branch:** continue `feat/execute-many`.

**Files:**

- create `python/fastmssql/_execute_many.py`;
- modify `python/fastmssql/__init__.py`;
- modify `python/fastmssql/__init__.pyi`;
- modify `python/fastmssql/fastmssql.pyi`;
- modify `README.md`;
- update `VERSION.md`.

### Step 7.1: implement wrapper dispatch

- [ ] Validate public keywords and top-level source before protocol
  acquisition.
- [ ] Dispatch concrete lists directly to raw methods.
- [ ] Route other sources through `_execute_many.py` and the shared bounded
  coordinator.
- [ ] Prefer async protocol for dual-protocol sources.
- [ ] Preserve exact positional set rules; do not coerce tuple/mapping/`None`.
- [ ] Attach producer-level global/confirmed-commit metadata without replacing
  the original exception class.

### Step 7.2: preserve bounded ownership and cleanup

- [ ] Reserve Transaction before the first producer pull.
- [ ] Activate only after first input.
- [ ] Await each `push()` before requesting another set.
- [ ] Drop completed chunks and retain no completed parameter objects.
- [ ] Do not close normally exhausted producers a second time.
- [ ] On abnormal termination, run abort/expire and producer close under
  cancellation shielding to terminal completion.
- [ ] Preserve the primary exception and first cleanup failure cause.

### Step 7.3: document the public contract

- [ ] Document:

  - one statement and positional sets;
  - list/sync/async inputs;
  - default `atomic=True`;
  - `atomic=False` chunk commit and partial-success metadata;
  - Transaction's missing `atomic` argument;
  - sequential one-session semantics;
  - chunk range and memory boundary;
  - sync-producer event-loop limitation;
  - one deadline, no retry and unknown commit outcome;
  - parameter-set mutation prohibition until completion;
  - schema-2 operation metrics.

- [ ] Keep claims limited to implemented SQL types and verified behavior.

### Step 7.4: drive offline contracts GREEN and commit

- [ ] Run all execute-many contract/coordinator tests.
- [ ] Run unchanged native-bulk coordinator tests to prove extraction parity.
- [ ] Run Ruff, `compileall`, stub and packaging contracts.
- [ ] Run Rust fmt/Clippy/tests again after wrapper registration.
- [ ] Update `VERSION.md`.
- [ ] Commit:

  ```bash
  git commit -m "feat: expose bounded execute many"
  ```

---

## Task 8: drive canonical SQL-auth cases GREEN

**Branch:** continue `feat/execute-many`.

### Step 8.1: rebuild the exact feature worktree

- [ ] Use isolated temporary Cargo/uv caches and release mode:

  ```bash
  CARGO_TARGET_DIR=/private/tmp/fastmssql-execute-many-target \
  UV_CACHE_DIR=/private/tmp/fastmssql-execute-many-uv-cache \
    uv sync --locked --all-extras --dev
  CARGO_TARGET_DIR=/private/tmp/fastmssql-execute-many-target \
  UV_CACHE_DIR=/private/tmp/fastmssql-execute-many-uv-cache \
    uv run maturin develop --release
  ```

- [ ] Prove loaded Python package and extension originate from this exact
  feature worktree/build.

### Step 8.2: run new real-SQL cases

- [ ] Run:

  ```bash
  ../../.venv/bin/pytest \
    tests/sql_auth_strict/test_execute_many_strict.py -q
  ```

- [ ] Inspect after each case:

  - exact rows and affected count;
  - exact commit/rollback boundary;
  - operation metric deltas;
  - pool capacity and transaction state;
  - independent DMV session count;
  - fixture teardown.

- [ ] Run existing parameter, batch, native-bulk, transaction, timeout and
  operation-metric strict suites unchanged.

### Step 8.3: debug every failure systematically

- [ ] Record the earliest violated invariant before editing.
- [ ] Add/tighten the smallest deterministic regression and observe RED.
- [ ] Apply the smallest correction; do not relax atomicity, timeout,
  cancellation, privacy, buffer or cleanup assertions.
- [ ] Give each independently discovered bug its own test-first corrective
  commit and `VERSION.md` entry.
- [ ] If public behavior must change, update design/plan/test explicitly
  before implementation.

---

## Task 9: run bounded real-SQL stress through 99,999 sets

**Branch:** clean exact `feat/execute-many` candidate.

**Artifacts:** write only under `/private/tmp`, with filenames containing the
full candidate SHA.

### Step 9.1: mandatory profiles

- [ ] Run 1,000 and 10,000 sync/async atomic profiles plus 10,000
  `atomic=False`:

  ```bash
  CANDIDATE_SHA="$(git rev-parse HEAD)"
  ../../.venv/bin/python \
    scripts/sql_auth/execute_many_stress.py \
    --profiles 1_000:100,10_000:1_000 \
    --modes sync,async \
    --include-partial-profile \
    --metrics-output \
      "/private/tmp/execute-many-${CANDIDATE_SHA}.json"
  ```

- [ ] Require:

  ```text
  pulls/executed/affected/persisted == requested sets
  maximum buffered sets           <= chunk_size
  RSS growth                      <= 64 MiB
  maximum event-loop gap          <= 0.100 s
  maximum candidate sessions      == 1
  execute_many terminal metric    == exactly one success
  execute metric delta            == 0
  post-load smoke                 == true
  teardown sessions               == 0
  violations                      == []
  ```

### Step 9.2: approved extended profiles

- [ ] Run both producer modes:

  ```bash
  CANDIDATE_SHA="$(git rev-parse HEAD)"
  ../../.venv/bin/python \
    scripts/sql_auth/execute_many_stress.py \
    --profiles 99_999:1_000 \
    --modes sync,async \
    --allow-extended \
    --metrics-output \
      "/private/tmp/execute-many-99999-${CANDIDATE_SHA}.json"
  ```

- [ ] Repeat the same hard gates and require zero candidate sessions through
  an independent DMV observer after teardown.
- [ ] Report throughput/memory as local driver characterization, not a
  universal SQL Server capacity claim.

---

## Task 10: run complete source, SQL-auth and wheel gates

**Branch:** clean exact feature candidate SHA.

### Step 10.1: Rust quality and unit gates

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

- [ ] Run the canonical vendored-Tiberius Clippy lane from
  `scripts/sql_auth/run_all.sh`; do not substitute an unaudited feature set.

### Step 10.2: Python and SQL-auth gates

- [ ] Run the full deterministic SQL-auth matrix and require 407/407.
- [ ] Run strict, true-async, framework, resilience, load and
  original-local-regression lanes through `scripts/sql_auth/run_all.sh`.
- [ ] Run all repository tests selected by the locked environment.
- [ ] Run Ruff, `compileall`, stub, matrix, report and stress contracts.
- [ ] Record exact pass/fail/skip/deselection counts; a skip, deselection or
  stale build is not a pass.

### Step 10.3: build and test an isolated ABI3 wheel

- [ ] Build from the exact clean candidate SHA into a fresh
  `/private/tmp` directory.
- [ ] Record wheel filename and SHA-256; do not publish it.
- [ ] Install into a fresh isolated environment outside the repository.
- [ ] Remove `PYTHONPATH` and prove import originates from isolated
  `site-packages`.
- [ ] Run:

  - execute-many offline coordinator/public-stub contracts;
  - `EMANY-001`–`EMANY-011` against Docker SQL Server;
  - representative list/iterable Connection and Transaction operations;
  - existing native-bulk, query and ResultStream smoke cases;
  - runtime dependency/import checks and `pip check`.

### Step 10.4: graph and source self-review

- [ ] Rebuild:

  ```bash
  uvx code-review-graph build
  ```

- [ ] Use `detect_changes`, `get_affected_flows` and `tests_for` to review:

  - Connection and Transaction `execute_many`;
  - shared bounded coordinator and native-bulk parity;
  - transaction state/settlement;
  - caller-Transaction security retirement and ResultStream parity;
  - lifecycle shutdown and pool disposition;
  - operation metrics schema/indexing;
  - timeout/cancellation/drop paths;
  - conversion/direct-batch diagnostics;
  - wrappers, stubs and wheel package paths.

- [ ] Read every changed hunk after graph review.
- [ ] Run `git diff --check`.
- [ ] Scan tracked changes for credential values, connection strings,
  generated artifacts and accidental original-repository push targets.
- [ ] Require no unrelated or untracked repository changes; only the reviewed
  evidence update may remain for the final technical evidence commit.

### Step 10.5: commit evidence and push only the fork

- [ ] Add a documentation-only evidence entry without modifying generated
  historical reports.
- [ ] Commit:

  ```bash
  git commit -m "docs: record execute many validation evidence"
  ```

- [ ] Verify the candidate descends from the exact RED commit.
- [ ] Push only `feat/execute-many`.
- [ ] Verify local and `origin` SHA equality; require `upstream` push
  `DISABLED`.
- [ ] Read public GitHub Actions for the fork only. Record exact candidate
  Linux/macOS/Windows and RustSec status as PASS, FAIL or NOT RUN; do not
  trigger publication or infer a pass from an ancestor.

---

## Task 11: update the live audit from verified evidence

**Branch:** `docs/execute-many-status`

**Base:** exact verified Task 10 feature commit.

**Files:**

- modify `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`;
- create `docs/EXECUTE_MANY_VALIDATION_REPORT.md`;
- modify
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
  only if verified evidence changes PR grouping;
- update `VERSION.md`.

- [ ] Mark only `execute_many()` as `VERIFIED_FORK`.
- [ ] Keep bounded-concurrency `query_many()` visibly open.
- [ ] Record:

  - design, plan, RED, feature and status SHAs;
  - exact ancestry and branch boundaries;
  - intended RED failure;
  - focused and full local counts;
  - SQL-auth atomicity/partial-commit/transaction/timeout/cancellation
    evidence;
  - schema-2 metric evidence;
  - sync/async 1,000, 10,000 and 99,999 stress metrics;
  - isolated wheel filename/hash/import evidence;
  - graph metadata and manually reviewed impact;
  - exact fork hosted status;
  - remaining risks and next slice.

- [ ] Do not rewrite prior generated evidence or claim a package release.
- [ ] Run docs/matrix contracts, `git diff --check`, credential scan and graph
  rebuild.
- [ ] Commit:

  ```bash
  git commit -m "docs: record execute many validation status"
  ```

- [ ] Push only `docs/execute-many-status`; verify exact remote SHA and
  `upstream` push `DISABLED`.

---

## Final acceptance checklist

- [ ] Design and plan commits are separate and exact.
- [ ] RED is deterministic, immutable and an ancestor of the feature.
- [ ] All repository modifications have truthful `VERSION.md` entries.
- [ ] Public/raw/stub signatures match the focused design.
- [ ] Concrete list, sync iterable and async iterable inputs pass.
- [ ] Validation and empty input perform zero forbidden work.
- [ ] Maximum retained sets never exceed `chunk_size`.
- [ ] Execution order and checked affected total are exact.
- [ ] `atomic=True` rollback and commit-unknown behavior are exact.
- [ ] `atomic=False` confirmed partial commits and metadata are exact.
- [ ] Caller Transaction settlement/rollback-only behavior is exact.
- [ ] Cancellation, timeout, drop and producer-close paths leak no session.
- [ ] Every failure preserves typed privacy-safe metadata.
- [ ] Metrics schema 2 has exactly 14 ordered keys.
- [ ] One non-empty call records exactly one `execute_many` metric and no
  internal metric inflation.
- [ ] `EMANY-001`–`EMANY-011` and 407/407 canonical cases pass.
- [ ] Sync/async stress passes at 1,000, 10,000 and 99,999 sets.
- [ ] RSS, event-loop, session, smoke and teardown gates pass.
- [ ] Existing batch/native-bulk/query/result-stream behavior remains green.
- [ ] Root and vendored Rust fmt/Clippy/tests pass.
- [ ] Isolated ABI3 wheel passes without source-tree imports.
- [ ] Code-review-graph is exact to the feature SHA and impact is reviewed.
- [ ] Credential/generated-artifact scans are clean.
- [ ] Fork-only remote SHA is exact and upstream push remains disabled.
- [ ] Hosted candidate status is reported truthfully.
- [ ] Live audit keeps `query_many()` and all unrelated enterprise gaps open.
