# FastMssql Compatibility-Bulk Timeout Phase-Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the approved separation between compatibility-bulk acquire
and operation timeouts, while preserving one absolute operation deadline
across `BEGIN`, every SQL chunk and `COMMIT`.

**Architecture:** A dedicated RED branch saturates a size-one pool longer
than the operation timeout but shorter than the acquire timeout, then proves
that the unchanged runtime has already expired its operation deadline when
checkout finishes. A descendant fix moves only compatibility
`bulk_insert()` deadline creation to immediately after checkout. The same
real-MSSQL test then requires exactly two requests under a 500/850 ms
trigger/deadline geometry, 20 consecutive repetitions, full compatibility
bulk coverage and every execute-many candidate gate.

**Tech Stack:** Python 3.12/3.13, pytest, pytest-asyncio, PyO3 0.27.2, Rust
1.94.0, Tiberius, Docker, Microsoft SQL Server 2022, Bash, uv, maturin, Git
and GitHub Actions.

## Global constraints

- Work and push only in `https://github.com/galeamarcel/FastMssql.git`.
- Keep the original repository fetch-only and its push URL exactly
  `DISABLED`.
- Base the corrected design on execute-many candidate
  `8df2abbb8076365455ed3691c1349b6cfcffdac6`.
- Preserve ancestry:
  `docs/operation-timeout-bulk-budget-determinism-design` →
  `test/compatibility-bulk-operation-deadline-boundary` →
  `fix/compatibility-bulk-operation-deadline-boundary` →
  `feat/execute-many`.
- Keep the RED commit as an ancestor of the fix.
- Restrict runtime changes to placement of the existing compatibility
  `bulk_insert()` operation deadline in `src/batch.rs`.
- Keep first-chunk conversion before pool initialization/acquisition so its
  errors remain zero-I/O and zero-lease.
- Start the operation deadline after `PooledOperationGuard` construction and
  before the first `BEGIN TRANSACTION` await.
- Reuse that same deadline across `BEGIN`, every generated compatibility
  chunk, later-chunk conversion and `COMMIT`.
- Do not change native bulk iterable or execute-many deadline semantics.
- Keep `TIME-006` on Docker SQL Server with SQL authentication.
- Preserve 4,001 rows, one column, five 1,000-row compatibility chunks,
  independent DMV observation, exact-two requests, typed timeout metadata and
  zero-row rollback.
- Use pool size `1`, acquire timeout `2.0`, operation timeout `0.85`, trigger
  delay `0.50` and controlled pending-acquire hold `0.95` seconds.
- Prove checkout wait through `pool_stats()["pending_gets"] == 1`; do not
  substitute scheduler yields.
- Keep the repeated gate opt-in and outside `scripts/sql_auth/run_all.sh`.
- Accept `FASTMSSQL_TIME006_STRESS_ITERATIONS` only as an integer from 1
  through 100; use 20 by default.
- Update `VERSION.md` for every repository commit without changing displayed
  `0.7.7` or release metadata.
- Do not commit credentials, `.env.sql-auth.local`, runner artifacts, wheels,
  generated reports, vendored lockfiles/targets or local absolute paths.
- Do not publish a wheel, release, upstream PR or original-repository branch.
- Execute inline under Marcel Galea's standing approval and self-review each
  commit.

---

## Task 1: publish the corrected design and plan

**Branch:** `docs/operation-timeout-bulk-budget-determinism-design`

**Files:**

- Correct:
  `docs/superpowers/specs/2026-07-29-fastmssql-operation-timeout-bulk-budget-determinism-design.md`
- Correct:
  `docs/superpowers/plans/2026-07-29-fastmssql-operation-timeout-bulk-budget-determinism.md`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: initial intermittent evidence, foundational post-checkout timeout
  contract, bounded-buffering commit `adb6637` and the conflicting later
  bounded-buffering instruction.
- Produces: reviewed requirements for a runtime phase-boundary RED/fix rather
  than the superseded test-only timing adjustment.

- [ ] **Step 1.1: record the corrected diagnosis**

  Require the design to state:

  - the first hypothesis was incomplete;
  - the foundational compatibility contract starts operation timing after
    acquire;
  - `adb6637` moved the deadline before first conversion and acquire;
  - native bulk iterable and execute-many retain their explicitly broader
    later sequence contracts;
  - no test-only widening is accepted as the complete fix.

- [ ] **Step 1.2: self-review the exact documents**

  Run:

  ```bash
  rg -n \
    'T[B]D|TO[D]O|implement la[t]er|fill in detai[l]s|Similar to Tas[k]' \
    docs/superpowers/specs/2026-07-29-fastmssql-operation-timeout-bulk-budget-determinism-design.md \
    docs/superpowers/plans/2026-07-29-fastmssql-operation-timeout-bulk-budget-determinism.md
  git diff --check
  ```

  Read every changed line. Require explicit conflict resolution, deterministic
  RED, one-line runtime movement and full acceptance gates.

- [ ] **Step 1.3: commit the correction without rewriting history**

  Commit the corrected design/plan plus `VERSION.md` as a descendant of the
  original documentation commits:

  ```bash
  git add \
    docs/superpowers/specs/2026-07-29-fastmssql-operation-timeout-bulk-budget-determinism-design.md \
    docs/superpowers/plans/2026-07-29-fastmssql-operation-timeout-bulk-budget-determinism.md \
    VERSION.md
  git commit -m "docs: correct bulk timeout phase-boundary design"
  ```

- [ ] **Step 1.4: fast-forward and republish only the docs branch**

  Point the local docs branch at the correction commit, push the fast-forward
  to origin and require local/fork SHA equality. Verify upstream push remains
  `DISABLED`.

---

## Task 2: add the deterministic real-MSSQL RED

**Branch:** `test/compatibility-bulk-operation-deadline-boundary`

**Base:** exact corrected Task 1 head.

**Files:**

- Modify: `tests/sql_auth_strict/test_operation_timeouts.py`
- Create:
  `scripts/test_operation_timeout_bulk_budget_determinism.sh`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: the unchanged compatibility runtime, pool observability
  `pending_gets`, Docker SQL Server and existing `TIME-006`.
- Produces: a deterministic contract that fails because acquire wait consumed
  the operation deadline, plus a bounded repeated-test runner.

- [ ] **Step 2.1: create/rename the test branch at the corrected design head**

  Run:

  ```bash
  git branch --show-current
  git rev-parse HEAD
  git status --short --branch
  ```

  Require corrected Task 1 head, branch
  `test/compatibility-bulk-operation-deadline-boundary` and no unrelated
  tracked change.

- [ ] **Step 2.2: add the bounded opt-in runner**

  Create `scripts/test_operation_timeout_bulk_budget_determinism.sh` with:

  ```bash
  #!/usr/bin/env bash
  set -euo pipefail

  readonly project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  readonly env_file="${project_root}/.env.sql-auth.local"
  readonly iterations="${FASTMSSQL_TIME006_STRESS_ITERATIONS:-20}"
  readonly node_id="tests/sql_auth_strict/test_operation_timeouts.py::test_batch_and_bulk_share_one_absolute_operation_budget"

  if [[ ! "${iterations}" =~ ^[0-9]+$ ]] \
    || ((iterations < 1 || iterations > 100)); then
    printf '%s\n' \
      'FASTMSSQL_TIME006_STRESS_ITERATIONS must be an integer from 1 through 100' \
      >&2
    exit 2
  fi

  if [[ ! -f "${env_file}" ]]; then
    printf 'missing %s\n' "${env_file}" >&2
    exit 2
  fi

  cd "${project_root}"
  set -a
  # shellcheck disable=SC1090
  source "${env_file}"
  set +a

  readonly results_path="$(
    mktemp "${TMPDIR:-/tmp}/fastmssql-time006-results.XXXXXX"
  )"
  trap 'rm -f "${results_path}"' EXIT

  for ((iteration = 1; iteration <= iterations; iteration++)); do
    if output="$(
      FASTMSSQL_SQL_AUTH_RESULTS_PATH="${results_path}" \
        uv run pytest -q "${node_id}" 2>&1
    )"; then
      printf '[TIME-006-stress] %d/%d passed\n' \
        "${iteration}" "${iterations}"
    else
      printf '%s\n' "${output}" >&2
      printf '[TIME-006-stress] failed at iteration %d/%d\n' \
        "${iteration}" "${iterations}" >&2
      exit 1
    fi
  done
  ```

  Set executable mode, run `bash -n`, and require invalid iteration values
  `0`, `101` and `invalid` to exit `2` before loading SQL-auth configuration.

- [ ] **Step 2.3: extend only the bulk half of TIME-006**

  Keep the existing table, trigger, sampler, timeout metadata and rollback
  assertions. Change the bulk trigger to:

  ```sql
  WAITFOR DELAY '00:00:00.500';
  ```

  Configure:

  ```python
  pool_config=bounded_pool(max_size=1)
  TimeoutConfig(
      acquire_timeout_secs=2.0,
      operation_timeout_secs=0.85,
  )
  ```

  Before starting bulk, begin a holder Transaction on
  `bulk_connection`. Schedule its PyO3 `Future` with
  `asyncio.ensure_future()` and then wait:

  ```python
  async def bulk_is_waiting_for_checkout() -> bool:
      return (
          await bulk_connection.pool_stats()
      )["pending_gets"] == 1

  await wait_until(bulk_is_waiting_for_checkout)
  await asyncio.sleep(0.95)
  assert bulk_task.done() is False
  await holder.rollback()
  await holder.close()
  operation_phase_started = time.monotonic()
  ```

  Await the bulk task inside `pytest.raises(error_type)`, calculate elapsed
  from `operation_phase_started`, and require:

  ```python
  assert 0.70 <= operation_phase_elapsed < 1.50
  ```

  Cleanup must cancel/await an unfinished task, close the holder
  idempotently, stop/await the sampler and disconnect. Suppress only the
  explicit `asyncio.CancelledError` from test cleanup.

- [ ] **Step 2.4: rebuild unchanged runtime and observe deterministic RED**

  Run:

  ```bash
  uv sync --locked --all-extras --dev
  maturin develop --release
  uv run pytest -q \
    tests/sql_auth_strict/test_operation_timeouts.py::test_batch_and_bulk_share_one_absolute_operation_budget
  ```

  Expected on unchanged `src/batch.rs`: FAIL after checkout because the
  operation deadline expired during the 0.95 second acquire wait. Require
  zero observed bulk requests rather than the expected two. Do not change
  runtime on this branch.

- [ ] **Step 2.5: self-review, commit and push RED only to the fork**

  Run:

  ```bash
  git diff -- src/batch.rs python vendor
  git diff --check
  bash -n scripts/test_operation_timeout_bulk_budget_determinism.sh
  git status --short
  ```

  Require no runtime diff. Commit:

  ```bash
  git add \
    tests/sql_auth_strict/test_operation_timeouts.py \
    scripts/test_operation_timeout_bulk_budget_determinism.sh \
    VERSION.md
  git commit -m "test: expose compatibility bulk timeout phase regression"
  git push -u origin \
    test/compatibility-bulk-operation-deadline-boundary
  ```

---

## Task 3: restore the compatibility operation boundary

**Branch:** `fix/compatibility-bulk-operation-deadline-boundary`

**Base:** exact Task 2 RED commit.

**Files:**

- Modify: `src/batch.rs`
- Modify: `VERSION.md`
- Test:
  `tests/sql_auth_strict/test_operation_timeouts.py`

**Interfaces:**

- Consumes: first bounded chunk, acquired `PooledOperationGuard` and existing
  `run_until` bulk future.
- Produces: a fresh operation deadline immediately after checkout, reused
  across every SQL and later-conversion step.

- [ ] **Step 3.1: create the fix branch from exact RED**

  Run:

  ```bash
  git switch -c fix/compatibility-bulk-operation-deadline-boundary
  git merge-base --is-ancestor \
    test/compatibility-bulk-operation-deadline-boundary HEAD
  git status --short --branch
  ```

- [ ] **Step 3.2: move only the existing deadline**

  In `src/batch.rs::bulk_insert`, remove deadline creation before
  `convert_bulk_chunk`. Reinsert the identical expression immediately after:

  ```rust
  let mut conn = PooledOperationGuard::new(pooled);
  ```

  and before:

  ```rust
  let mut transaction_started = false;
  let operation = run_until(...);
  ```

  Do not move first conversion, create a second deadline, change errors,
  change pool timeout behavior or touch native bulk/execute-many.

- [ ] **Step 3.3: run focused GREEN and repetition gates**

  Rebuild the exact extension, then run:

  ```bash
  uv run pytest -q \
    tests/sql_auth_strict/test_operation_timeouts.py::test_batch_and_bulk_share_one_absolute_operation_budget
  FASTMSSQL_TIME006_STRESS_ITERATIONS=20 \
    scripts/test_operation_timeout_bulk_budget_determinism.sh
  uv run pytest -q tests/sql_auth_strict/test_operation_timeouts.py
  uv run pytest -q \
    tests/sql_auth_strict/test_batch_strict.py \
    tests/test_batch_operations.py \
    tests/test_batch_operations_advanced.py \
    tests/test_bulk_bounded_buffering.py \
    tests/test_bulk_row_descriptor_conversion.py
  ```

  Require focused PASS, exactly 20/20 repeated PASS, complete timeout PASS and
  compatibility bulk PASS.

- [ ] **Step 3.4: run source quality gates**

  Run:

  ```bash
  cargo fmt --check
  cargo clippy --all-targets -- -D warnings
  cargo test --locked
  uv run ruff check \
    tests/sql_auth_strict/test_operation_timeouts.py
  git diff --check
  ```

- [ ] **Step 3.5: graph and direct self-review**

  Rebuild code-review-graph. Use `detect_changes`,
  `get_affected_flows` and `tests_for` for `src/batch.rs::bulk_insert`.
  Read every changed hunk and confirm:

  - first conversion remains pre-acquire;
  - acquire retains its own timeout;
  - operation deadline starts post-checkout;
  - later conversion and all SQL share that deadline;
  - no other API changed.

- [ ] **Step 3.6: commit and push fix only to the fork**

  Commit:

  ```bash
  git add src/batch.rs VERSION.md
  git commit -m "fix: restore compatibility bulk timeout phase boundary"
  git push -u origin \
    fix/compatibility-bulk-operation-deadline-boundary
  ```

  Require test ancestry, local/fork equality and upstream push `DISABLED`.

---

## Task 4: fast-forward the correction into execute-many

**Branch:** `feat/execute-many`

- [ ] **Step 4.1: integrate without a merge commit**

  Run:

  ```bash
  git switch feat/execute-many
  git merge --ff-only \
    fix/compatibility-bulk-operation-deadline-boundary
  git merge-base --is-ancestor \
    test/compatibility-bulk-operation-deadline-boundary HEAD
  ```

  Require the single reviewed runtime movement plus test/docs ancestry.

- [ ] **Step 4.2: rerun the complete canonical Docker runner**

  Run:

  ```bash
  scripts/sql_auth/run_all.sh
  ```

  Require all root/vendored Rust, Tiberius SQL-auth, ResultStream stress,
  strict, true-async, framework, resilience, load,
  original-local-regression and canonical matrix/report exitcodes zero.
  Record exact counts.

  Back up generated evidence outside the repository, restore historical
  generated reports and remove generated vendored artifacts recoverably.

- [ ] **Step 4.3: rerun execute-many stress**

  Run sync and async execute-many profiles:

  ```text
  1,000 sets / chunk 100
  10,000 sets / chunk 1,000
  99,999 sets / chunk 1,000
  ```

  Require exact counts, bounded buffer/RSS, event-loop responsiveness,
  exact operation metrics, at most one candidate session, smoke PASS and zero
  candidate sessions after teardown.

- [ ] **Step 4.4: rebuild and test the isolated ABI3 wheel**

  Build from exact clean HEAD into a fresh temporary directory, record
  filename/SHA-256, install in a fresh environment, unset `PYTHONPATH`, prove
  import from isolated `site-packages`, run execute-many offline and
  `EMANY-001` through `EMANY-011`, representative compatibility/native bulk,
  query and ResultStream smoke, then `pip check`. Do not publish the wheel.

---

## Task 5: final evidence, fork push and live audit

- [ ] **Step 5.1: rebuild exact graph and review source**

  Rebuild code-review-graph at candidate HEAD. Use
  `get_minimal_context`, `detect_changes`, `get_affected_flows` and
  `tests_for`; then read every changed hunk.

- [ ] **Step 5.2: scan repository boundaries**

  Run `git diff --check`, require a clean worktree and scan tracked changes
  for passwords, connection strings, local paths, generated artifacts and
  original-repository push targets. Confirm both origin URLs name the fork
  and upstream push is `DISABLED`.

- [ ] **Step 5.3: record exact evidence and push feat/execute-many**

  Update `VERSION.md` with observed results, commit:

  ```bash
  git add VERSION.md
  git commit -m "docs: record execute many validation evidence"
  git push -u origin feat/execute-many
  ```

  Require local/fork equality. Read public GitHub Actions only for the fork
  and record exact candidate status as PASS, FAIL or NOT RUN.

- [ ] **Step 5.4: update the live audit separately**

  Create `docs/execute-many-status` from exact verified feature HEAD. Update:

  - `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`;
  - `docs/EXECUTE_MANY_VALIDATION_REPORT.md`;
  - `VERSION.md`;
  - upstream PR roadmap only if verified evidence changes grouping.

  Record the compatibility-bulk phase correction as a separate discovered
  bug. Mark only execute-many slice 6/7 `VERIFIED_FORK`; keep `query_many()`
  and enterprise features 20/21 open.

## Completion criteria

This plan is complete only when:

1. corrected design history is published without rewriting the initial
   hypothesis;
2. the unchanged runtime fails the saturated-pool `TIME-006` deterministically;
3. the fix moves only compatibility bulk deadline creation post-checkout;
4. exact-two requests pass 20 consecutive real-MSSQL repetitions;
5. acquire/operation phases, first-chunk zero-I/O and later-chunk absolute
   budgeting are all preserved;
6. every execute-many runner, 99,999-set, isolated-wheel and graph gate passes
   on one exact candidate;
7. local and fork SHAs match and upstream push remains `DISABLED`;
8. the live audit records the discovered runtime regression separately and
   leaves `query_many()` open.

No completion claim may rely on an ancestor, stale extension, partial runner,
required skip/deselection, swallowed exception or unpublished local-only
commit.
