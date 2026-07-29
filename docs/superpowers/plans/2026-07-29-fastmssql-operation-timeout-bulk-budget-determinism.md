# FastMssql Operation-Timeout Bulk-Budget Determinism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve the exact cross-chunk absolute-deadline proof in
`TIME-006` while giving bounded first-chunk conversion and connection setup a
deterministic margin.

**Architecture:** A dedicated test branch adds a bounded opt-in runner and
records the unchanged intermittent failure. A descendant fix branch changes
only the real-MSSQL test's trigger/deadline geometry from 160/250 ms to
500/850 ms. The fix must retain exactly two observed requests, typed
fail-closed timeout metadata, atomic rollback and the five-chunk
reset-per-chunk detector. The verified test-only correction is fast-forwarded
into the execute-many candidate before its complete source, SQL-auth, stress,
wheel, graph and fork-only gates are restarted.

**Tech Stack:** Python 3.12/3.13, pytest, pytest-asyncio, PyO3 0.27.2, Rust
1.94.0, Tiberius, Docker, Microsoft SQL Server 2022, Bash, uv, maturin, Git
and GitHub Actions.

## Global constraints

- Work and push only in `https://github.com/galeamarcel/FastMssql.git`.
- Keep the original repository fetch-only and its push URL exactly
  `DISABLED`.
- Base the design branch on exact execute-many candidate
  `8df2abbb8076365455ed3691c1349b6cfcffdac6`.
- Preserve ancestry:
  `docs/operation-timeout-bulk-budget-determinism-design` →
  `test/operation-timeout-bulk-budget-determinism` →
  `fix/operation-timeout-bulk-budget-determinism` →
  `feat/execute-many`.
- Keep the reproduction commit as an ancestor of the fix.
- Change no file under `src/`, `python/` or `vendor/` for this correction.
- Keep `TIME-006` on Docker SQL Server with SQL authentication; do not add a
  SQLite, mock-driver or swallowed-exception substitute.
- Preserve 4,001 input rows, one column, five 1,000-row compatibility chunks,
  the independent DMV sampler and the exact-two request assertion.
- Preserve timeout metadata:
  `phase=operation`, `operation=bulk_insert`, `retryable=false`,
  `discarded=true`, `outcome_unknown=true`.
- Preserve the zero-row rollback proof.
- Use 500 ms per trigger execution, one 850 ms absolute operation budget and
  an elapsed guard of 0.70 through less than 1.50 seconds.
- Keep the repeated gate opt-in and outside `scripts/sql_auth/run_all.sh`.
- Accept `FASTMSSQL_TIME006_STRESS_ITERATIONS` only as an integer from 1
  through 100; use 20 by default.
- Update `VERSION.md` for every branch commit without changing displayed
  package version `0.7.7` or release metadata.
- Do not commit `.env.sql-auth.local`, runner artifacts, wheels, generated
  vendored lockfiles/targets, passwords or connection strings.
- Do not publish a wheel, release, upstream PR or original-repository branch.
- Execute inline under Marcel Galea's standing approval and self-review every
  commit.

---

## Task 1: publish the reviewed design and executable plan

**Branch:** `docs/operation-timeout-bulk-budget-determinism-design`

**Files:**

- Create:
  `docs/superpowers/specs/2026-07-29-fastmssql-operation-timeout-bulk-budget-determinism-design.md`
- Create:
  `docs/superpowers/plans/2026-07-29-fastmssql-operation-timeout-bulk-budget-determinism.md`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: exact source baseline
  `8df2abbb8076365455ed3691c1349b6cfcffdac6`, complete-runner failure
  `423 passed, 1 failed`, and diagnostic failure state
  `elapsed=0.25245849997736514`, `requests=1`.
- Produces: immutable reviewed requirements for the descendant test and fix
  branches.

- [ ] **Step 1.1: prove the branch parent and repository boundary**

  Run:

  ```bash
  git merge-base --is-ancestor \
    8df2abbb8076365455ed3691c1349b6cfcffdac6 HEAD
  git remote get-url origin
  git remote get-url --push origin
  git remote get-url --push upstream
  git status --short --branch
  ```

  Require both origin URLs to name `galeamarcel/FastMssql`, upstream push to
  print `DISABLED`, and no unrelated change.

- [ ] **Step 1.2: self-review both documents**

  Run:

  ```bash
  rg -n \
    'T[B]D|TO[D]O|implement la[t]er|fill in detai[l]s|Similar to Tas[k]' \
    docs/superpowers/specs/2026-07-29-fastmssql-operation-timeout-bulk-budget-determinism-design.md \
    docs/superpowers/plans/2026-07-29-fastmssql-operation-timeout-bulk-budget-determinism.md
  git diff --check
  ```

  Require no placeholder and a clean whitespace check. Read every changed
  line and confirm the test geometry preserves exact-two semantics.

- [ ] **Step 1.3: commit and publish only the design branch**

  Commit the specification and plan as documentation-only changes, then run:

  ```bash
  git push -u origin \
    docs/operation-timeout-bulk-budget-determinism-design
  git ls-remote --heads origin \
    docs/operation-timeout-bulk-budget-determinism-design
  git remote get-url --push upstream
  ```

  Require local/fork SHA equality and `DISABLED`.

---

## Task 2: add and observe the unchanged TIME-006 reproduction

**Branch:** `test/operation-timeout-bulk-budget-determinism`

**Base:** exact Task 1 head.

**Files:**

- Create:
  `scripts/test_operation_timeout_bulk_budget_determinism.sh`
- Modify: `VERSION.md`
- Exercise unchanged:
  `tests/sql_auth_strict/test_operation_timeouts.py::test_batch_and_bulk_share_one_absolute_operation_budget`

**Interfaces:**

- Consumes: `.env.sql-auth.local`, the locked uv environment, the exact local
  FastMssql extension and running `fastmssql-sql-auth-dev` container.
- Produces: a bounded opt-in runner that exits non-zero at the first real
  focused-test failure and zero only after every requested iteration passes.

- [ ] **Step 2.1: create the test branch from the exact design head**

  Run:

  ```bash
  git switch -c test/operation-timeout-bulk-budget-determinism
  git rev-parse HEAD
  git status --short --branch
  ```

  Require the Task 1 plan commit and a clean worktree.

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
    || (( iterations < 1 || iterations > 100 )); then
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

  Then run:

  ```bash
  chmod +x scripts/test_operation_timeout_bulk_budget_determinism.sh
  bash -n scripts/test_operation_timeout_bulk_budget_determinism.sh
  ```

  Require valid shell syntax and executable mode.

- [ ] **Step 2.3: prove input validation is bounded**

  Run the script with iteration values `0`, `101` and `invalid`. Require exit
  code `2`, the same explicit range message and no pytest invocation.

- [ ] **Step 2.4: rebuild the exact unchanged extension and observe RED**

  Run:

  ```bash
  uv sync --locked --all-extras --dev
  maturin develop --release
  FASTMSSQL_TIME006_STRESS_ITERATIONS=20 \
    scripts/test_operation_timeout_bulk_budget_determinism.sh
  ```

  The unchanged source is expected to fail at least one iteration with the
  exact-one-versus-two request assertion. If one 20-iteration batch happens
  to pass, run up to four more bounded batches. Record exact iterations and
  results honestly. The already captured full-runner and diagnostic failures
  remain valid reproduction evidence even if a later batch happens to pass.

- [ ] **Step 2.5: review, commit and push the reproduction**

  Run:

  ```bash
  git diff -- src python vendor
  git diff --check
  bash -n scripts/test_operation_timeout_bulk_budget_determinism.sh
  git status --short
  ```

  Require no runtime diff and only the script plus `VERSION.md`.

  Commit:

  ```bash
  git add \
    scripts/test_operation_timeout_bulk_budget_determinism.sh \
    VERSION.md
  git commit -m "test: reproduce bulk timeout budget race"
  git push -u origin test/operation-timeout-bulk-budget-determinism
  ```

  Require publication only on the fork.

---

## Task 3: stabilize the exact-two absolute-budget proof

**Branch:** `fix/operation-timeout-bulk-budget-determinism`

**Base:** exact Task 2 test commit.

**Files:**

- Modify: `tests/sql_auth_strict/test_operation_timeouts.py`
- Modify: `VERSION.md`
- Test:
  `scripts/test_operation_timeout_bulk_budget_determinism.sh`

**Interfaces:**

- Consumes: the unchanged RED test and bounded repetition runner.
- Produces: the same `TIME-006` semantic contract with deterministic
  500/850 ms timing geometry.

- [ ] **Step 3.1: create the fix branch from the exact RED commit**

  Run:

  ```bash
  git switch -c fix/operation-timeout-bulk-budget-determinism
  git merge-base --is-ancestor \
    test/operation-timeout-bulk-budget-determinism HEAD
  git status --short --branch
  ```

- [ ] **Step 3.2: make only the approved test changes**

  In the bulk half of `TIME-006`, change:

  ```python
  WAITFOR DELAY '00:00:00.160'
  operation_timeout_secs=0.25
  assert 0.18 <= elapsed < 0.75
  ```

  to:

  ```python
  WAITFOR DELAY '00:00:00.500'
  operation_timeout_secs=0.85
  assert 0.70 <= elapsed < 1.50
  ```

  Update the comment to state that:

  - bounded conversion and connection setup share the first 350 ms margin;
  - the first 500 ms request completes;
  - the second starts;
  - two complete waits need at least one second and exceed the shared 850 ms
    deadline;
  - a per-chunk reset would still allow all five requests.

  Do not change the batch half, sampler, rows, exact-two assertion, timeout
  metadata or rollback assertion.

- [ ] **Step 3.3: run the focused GREEN gates**

  Run:

  ```bash
  uv run pytest -q \
    tests/sql_auth_strict/test_operation_timeouts.py::test_batch_and_bulk_share_one_absolute_operation_budget
  FASTMSSQL_TIME006_STRESS_ITERATIONS=20 \
    scripts/test_operation_timeout_bulk_budget_determinism.sh
  uv run pytest -q tests/sql_auth_strict/test_operation_timeouts.py
  uv run ruff check \
    tests/sql_auth_strict/test_operation_timeouts.py
  ```

  Require focused PASS, exactly `20/20` repeated PASS, the complete timeout
  file PASS and Ruff PASS.

- [ ] **Step 3.4: prove the change is test-only and self-review it**

  Run:

  ```bash
  git diff -- src python vendor
  git diff --check
  git diff -- \
    tests/sql_auth_strict/test_operation_timeouts.py \
    VERSION.md
  ```

  Require no production diff, no tolerance around exact two, no secret, no
  generated artifact and no unrelated path.

- [ ] **Step 3.5: commit and publish only the fix branch**

  Commit:

  ```bash
  git add \
    tests/sql_auth_strict/test_operation_timeouts.py \
    VERSION.md
  git commit -m "fix: stabilize bulk timeout budget proof"
  git push -u origin fix/operation-timeout-bulk-budget-determinism
  ```

  Verify test-branch ancestry, local/fork SHA equality and upstream push
  `DISABLED`.

---

## Task 4: integrate the correction and restart execute-many gates

**Branch:** `feat/execute-many`

**Base:** exact Task 3 fix commit through fast-forward only.

- [ ] **Step 4.1: fast-forward without a merge commit**

  Run:

  ```bash
  git switch feat/execute-many
  git merge --ff-only fix/operation-timeout-bulk-budget-determinism
  git merge-base --is-ancestor \
    test/operation-timeout-bulk-budget-determinism HEAD
  git diff 8df2abbb8076365455ed3691c1349b6cfcffdac6 -- \
    src python vendor
  ```

  Require no production diff from the pre-correction execute-many candidate.

- [ ] **Step 4.2: rerun the complete canonical runner**

  Run:

  ```bash
  scripts/sql_auth/run_all.sh
  ```

  Require every exitcode zero and record exact counts for:

  - root and vendored Rust gates;
  - Tiberius token, bulk and response SQL-auth;
  - ResultStream stress;
  - strict, true-async and framework lanes;
  - resilience, load and original-local-regression lanes;
  - canonical matrix/report generation.

  Copy any generated evidence outside the repository before restoring
  historical generated reports. Do not commit generated vendored targets or
  lockfiles.

- [ ] **Step 4.3: rerun execute-many stress through 99,999**

  Run the approved execute-many stress runner in sync and async modes at:

  ```text
  1,000 sets / chunk 100
  10,000 sets / chunk 1,000
  99,999 sets / chunk 1,000
  ```

  Require exact affected/persisted counts, bounded buffer and RSS, event-loop
  responsiveness, exact success metrics, maximum one candidate session,
  post-load smoke PASS and zero candidate sessions after teardown.

- [ ] **Step 4.4: build and test the isolated ABI3 wheel**

  Build from the exact clean candidate into a fresh temporary directory,
  record filename and SHA-256, install into a fresh environment, unset
  `PYTHONPATH`, prove import from isolated `site-packages`, run:

  - execute-many offline coordinator and API contracts;
  - `EMANY-001` through `EMANY-011` on Docker SQL Server;
  - representative Connection/Transaction list and iterable operations;
  - native-bulk, query and ResultStream smoke;
  - dependency import checks and `pip check`.

  Do not publish or copy the wheel into the repository.

---

## Task 5: graph, source, evidence and fork-only completion

**Branch:** `feat/execute-many`

- [ ] **Step 5.1: rebuild and review the exact graph**

  Run:

  ```bash
  uvx code-review-graph build
  ```

  Use `get_minimal_context`, `detect_changes`, `get_affected_flows` and
  `tests_for`. Confirm the graph SHA equals candidate HEAD. Review
  `TIME-006`, execute-many Connection/Transaction paths, bounded coordinator,
  state settlement, lifecycle, metrics, timeout/cancellation and wrapper/stub
  coverage.

- [ ] **Step 5.2: perform direct final checks**

  Read every changed hunk after graph review. Run:

  ```bash
  git diff --check
  git status --short --branch
  git remote get-url origin
  git remote get-url --push origin
  git remote get-url --push upstream
  ```

  Scan tracked changes for credential values, URLs containing credentials,
  local absolute paths, connection strings, generated artifacts and
  accidental original-repository push targets.

- [ ] **Step 5.3: record evidence and push only the fork**

  Update `VERSION.md` with exact observed results, commit:

  ```bash
  git add VERSION.md
  git commit -m "docs: record execute many validation evidence"
  git push -u origin feat/execute-many
  ```

  Require local/fork equality and upstream push `DISABLED`. Read public
  GitHub Actions only for the fork. Record exact candidate status as PASS,
  FAIL or NOT RUN; do not infer a pass from an ancestor and do not trigger
  release publication.

- [ ] **Step 5.4: update the live audit on its own branch**

  Create `docs/execute-many-status` from exact verified feature HEAD. Update:

  - `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`;
  - `docs/EXECUTE_MANY_VALIDATION_REPORT.md`;
  - `VERSION.md`;
  - the upstream PR roadmap only if evidence changes grouping.

  Mark only execute-many slice 6/7 as `VERIFIED_FORK`. Keep bounded
  `query_many()` open as slice 7/7, and keep enterprise features 20 and 21
  open. Push only the status branch on the fork.

## Completion criteria

This plan is complete only when:

1. the design, test and fix branches exist on the fork with exact ancestry;
2. the unchanged test's intermittent one-request failure is recorded;
3. the fix changes test timing and documentation only;
4. `TIME-006` passes 20 consecutive real-MSSQL repetitions with exact two
   requests;
5. every execute-many source, complete-runner, 99,999-set, isolated-wheel and
   graph gate passes on one exact clean candidate;
6. the fork branch equals local HEAD and upstream push remains `DISABLED`;
7. the live audit records observed evidence and leaves `query_many()` open.

No completion claim may be based on an ancestor, stale extension, partial
runner, skipped required case, swallowed exception or unpublished local-only
commit.
