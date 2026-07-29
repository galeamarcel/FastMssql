# FastMssql `execute_many()` Security-Retirement Characterization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. Use
> `superpowers:systematic-debugging` for any unexpected result and
> `superpowers:verification-before-completion` before every commit or status
> claim.

**Goal:** lock the existing fail-closed behavior for
`Transaction.execute_many()` when successful SQL changes the SQL Server
security context, without changing runtime or public API behavior.

**Architecture:** extend the existing canonical `EMANY-009` SQL-auth case
because it already owns caller-Transaction settlement semantics. The test
uses a size-one pool, records the physical `connection_id`, verifies the
successful execute-many metric and failed Transaction state, then proves
that the pool creates a different usable physical session.

**Tech Stack:** Python 3.11–3.14, `asyncio`, pytest/pytest-asyncio, FastMssql
PyO3 ABI3 extension, bb8, vendored Tiberius 0.12.3, SQL Server 2022 Docker
with SQL authentication, Ruff, Git worktrees and code-review-graph.

## Global Constraints

- Exact approved design commit:
  `96bcbc23344782a2cf4e7bdf312925905a489dc2`.
- Branch topology:

  ```text
  docs/execute-many-security-retirement-design
    -> test/execute-many-security-retirement
    -> feat/execute-many
  ```

- This is a deterministic characterization of existing runtime behavior.
  Do not create an artificial failing assertion or change production code to
  manufacture a RED phase.
- If the real SQL-auth characterization contradicts the design, preserve the
  exact reproduction on the test branch, diagnose it systematically, and
  create `fix/execute-many-security-retirement` from that test commit before
  changing runtime.
- Extend `EMANY-009`; do not add a twelfth `EMANY` ID or change the canonical
  total of 407 cases.
- Use `connection_id`, not SPID, as physical-session identity because SQL
  Server may reuse a SPID.
- Keep the displayed package version `0.7.7`; update `VERSION.md` in every
  repository commit.
- Push only to `https://github.com/galeamarcel/FastMssql.git`; the
  `Rivendael/FastMssql` push URL must remain exactly `DISABLED`.
- Never print or commit passwords, SQL-auth URLs, `.env` content, generated
  reports, stress JSON, wheels or build caches.

---

## Task 1: add the deterministic `EMANY-009` characterization

**Branch:** `test/execute-many-security-retirement`, created from the clean
commit containing this plan.

**Files:**

- Modify: `tests/sql_auth_strict/test_execute_many_strict.py`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes:
  `Connection.transaction() -> Transaction`,
  `Transaction.execute_many(sql, parameter_sets, *, chunk_size=1000)`,
  `Transaction.is_connected() -> bool`,
  `Connection.operation_stats() -> dict[str, Any]`,
  `operation_delta(...)`, `zero_outcomes(...)`, `scalar(...)` and
  `_wait_for_zero_sessions(...)`.
- Produces:
  one expanded canonical test,
  `test_execute_many_transaction_is_neutral_then_rollback_only`, that owns
  ordinary settlement neutrality, synchronized rollback-only recovery and
  successful security-context retirement.

- [ ] **Step 1.1: create the test-only branch from the clean plan commit**

  Run:

  ```bash
  git status --short
  git switch -c test/execute-many-security-retirement
  git merge-base --is-ancestor \
    96bcbc23344782a2cf4e7bdf312925905a489dc2 HEAD
  git config --get remote.upstream.pushurl
  ```

  Require no worktree changes before the branch switch, ancestry exit code
  zero and upstream push URL `DISABLED`.

- [ ] **Step 1.2: add one physical-identity helper**

  Add beside `_row_count`:

  ```python
  async def _physical_connection_id(connection: Any) -> str:
      return str(
          await scalar(
              connection,
              """
              SELECT CONVERT(NVARCHAR(36), connection_id)
              FROM sys.dm_exec_connections
              WHERE session_id = @@SPID
              """,
          )
      )
  ```

- [ ] **Step 1.3: extend the existing test inputs**

  Add the SQL-auth configuration and independent DMV observer to the
  existing `EMANY-009` function:

  ```python
  async def test_execute_many_transaction_is_neutral_then_rollback_only(
      sql_auth_config: SqlAuthConfig,
      owner_connection: Connection,
      sa_connection: Connection,
      unique_sql_name: Callable[[str], str],
      cleanup_registry: CleanupRegistry,
  ) -> None:
  ```

- [ ] **Step 1.4: append the security-retirement scenario**

  Keep the existing ordinary-success and rollback-only assertions unchanged.
  After their final persisted-row assertion, add:

  ```python
  application_name = unique_sql_name("strict_emany_transaction_retirement")
  connection = _isolated_connection(
      sql_auth_config,
      application_name=application_name,
      metrics=True,
  )
  retired = connection.transaction()
  replacement: Transaction | None = None
  try:
      await retired.begin()
      retired_connection_id = await _physical_connection_id(retired)
      before = await connection.operation_stats()

      assert await retired.execute_many(
          "EXECUTE AS USER = 'dbo'",
          [[]],
      ) == 0

      after = await connection.operation_stats()
      delta = operation_delta(before, after, "execute_many")
      assert delta["started"] == delta["completed"] == 1
      assert {key: delta[key] for key in OUTCOME_KEYS} == zero_outcomes(
          succeeded=1
      )
      for operation in ("execute", "begin", "commit", "rollback"):
          assert operation_delta(before, after, operation)["started"] == 0

      assert retired.is_connected() is False
      with pytest.raises(
          RuntimeError,
          match="state is indeterminate; call close",
      ):
          await retired.query("SELECT 1")
      with pytest.raises(
          RuntimeError,
          match="state is indeterminate; call close",
      ):
          await retired.commit()
      with pytest.raises(
          RuntimeError,
          match="state is indeterminate; call close",
      ):
          await retired.rollback()

      await retired.close()
      await retired.close()

      replacement = connection.transaction()
      await replacement.begin()
      replacement_connection_id = await _physical_connection_id(replacement)
      assert replacement_connection_id != retired_connection_id
      assert await scalar(replacement, "SELECT 1") == 1
      await replacement.rollback()
  finally:
      if replacement is not None:
          await replacement.close()
      await retired.close()
      await connection.disconnect()
      await _wait_for_zero_sessions(sa_connection, application_name)
  ```

  This verifies the response, metric, failed state, idempotent close,
  physical retirement, pool replacement, smoke query and teardown without
  relying on timing or SPID reuse.

- [ ] **Step 1.5: record the test-only change in `VERSION.md`**

  Add an unreleased-candidate entry stating that `EMANY-009` now
  characterizes successful security-context retirement, distinct physical
  replacement, successful operation metrics and zero leaked sessions. State
  explicitly that no runtime, API, package version or release metadata
  changed.

- [ ] **Step 1.6: run the focused real-SQL characterization**

  Confirm the dedicated container is healthy, then run:

  ```bash
  set -a
  source ../../.env.sql-auth.local
  set +a
  .venv/bin/pytest \
    tests/sql_auth_strict/test_execute_many_strict.py::test_execute_many_transaction_is_neutral_then_rollback_only \
    -q
  ```

  Expected: one PASS. A runtime assertion failure is product evidence, not
  permission to weaken the test. An unavailable Docker/localhost path is an
  environment failure and must be rerun with the approved access.

- [ ] **Step 1.7: run the complete affected test set**

  With the same SQL-auth environment, run:

  ```bash
  .venv/bin/pytest tests/sql_auth_strict/test_execute_many_strict.py -q
  .venv/bin/pytest tests/sql_auth_strict/test_matrix_contract.py -q
  .venv/bin/pytest \
    tests/test_execute_many_contract.py \
    tests/test_execute_many_coordinator.py \
    tests/test_execute_many_stress_contract.py \
    -q
  .venv/bin/ruff check \
    tests/sql_auth_strict/test_execute_many_strict.py
  ```

  Require all 11 `EMANY` cases, all 27 matrix contracts and all focused
  offline contracts to pass without skip or swallowed exception.

- [ ] **Step 1.8: review, commit and push only the test branch**

  Run:

  ```bash
  git diff --check
  .venv/bin/pytest \
    tests/sql_auth_strict/test_environment_auth.py::test_credentials_are_absent_from_tracked_files_and_logs \
    -q
  uvx code-review-graph build
  git status --short
  git diff -- tests/sql_auth_strict/test_execute_many_strict.py VERSION.md
  ```

  Confirm that only the two intended files changed, inspect every hunk,
  review the dirty-tree graph context, and require the credential test to
  scan tracked files, Docker logs and JSON artifacts without printing secret
  values. Then commit and rebuild the graph on the exact commit:

  ```bash
  git add tests/sql_auth_strict/test_execute_many_strict.py VERSION.md
  git commit -m "test: lock execute many security retirement"
  uvx code-review-graph build
  git push --set-upstream origin test/execute-many-security-retirement
  ```

  Require `head_matches_build=true`, local and fork SHA equality and upstream
  push URL `DISABLED`.

---

## Task 2: integrate the characterization into the feature candidate

**Branch:** `feat/execute-many`

**Files:**

- Integrate: the exact `test/execute-many-security-retirement` commit
- Modify: `VERSION.md` only if new verification evidence is recorded

**Interfaces:**

- Consumes: the passing `EMANY-009` characterization from Task 1.
- Produces: a feature candidate whose ancestry contains the approved design,
  executable plan and exact characterization without a merge commit or
  runtime-tree change.

- [ ] **Step 2.1: fast-forward the feature branch**

  Run from a clean worktree:

  ```bash
  git switch feat/execute-many
  git merge --ff-only test/execute-many-security-retirement
  git merge-base --is-ancestor test/execute-many-security-retirement HEAD
  ```

  Require a fast-forward only. Do not rebase, squash or create a merge
  commit.

- [ ] **Step 2.2: re-run exact source and SQL-auth gates**

  Run:

  ```bash
  cargo fmt --check
  cargo clippy --all-targets -- -D warnings
  cargo test --locked
  .venv/bin/ruff check python tests scripts
  .venv/bin/python -m compileall -q python tests scripts
  scripts/sql_auth/run_all.sh
  ```

  Require the root Rust suite, vendored Tiberius lanes, 407/407 canonical
  cases, strict, true-async, framework, resilience, load and
  original-local-regression lanes to pass. Record exact counts from fresh
  logs; do not infer them from the earlier `5b307e7` run. Copy the fresh
  `.artifacts/sql-auth` directory outside the repository, inspect the tracked
  file list, then restore only the two generated historical outputs that the
  runner rewrites:

  ```bash
  candidate_sha="$(git rev-parse HEAD)"
  evidence_dir="/private/tmp/fastmssql-execute-many-gates-${candidate_sha}"
  cp -R .artifacts/sql-auth "${evidence_dir}"
  git diff --name-only
  git restore --source=HEAD -- \
    docs/SQL_AUTH_TEST_MATRIX.md \
    docs/SQL_AUTH_TEST_REPORT.md
  ```

  Do not restore any unexpected path; investigate it first.

- [ ] **Step 2.3: re-run exact stress profiles**

  Run:

  ```bash
  candidate_sha="$(git rev-parse HEAD)"
  .venv/bin/python scripts/sql_auth/execute_many_stress.py \
    --profiles 1_000:100,10_000:1_000 \
    --modes sync,async \
    --include-partial-profile \
    --metrics-output \
    "/private/tmp/execute-many-${candidate_sha}.json"
  .venv/bin/python scripts/sql_auth/execute_many_stress.py \
    --profiles 99_999:1_000 \
    --modes sync,async \
    --allow-extended \
    --metrics-output \
    "/private/tmp/execute-many-99999-${candidate_sha}.json"
  ```

  Require:

  - exact affected and persisted counts;
  - no timeout or failure;
  - maximum one physical SQL session;
  - bounded producer buffering;
  - event-loop stall below the existing hard gate;
  - one successful `execute_many` metric;
  - post-load smoke PASS;
  - zero application sessions after teardown.

  Store privacy-safe JSON outside the repository under `/private/tmp`, keyed
  by the exact candidate SHA.

- [ ] **Step 2.4: rebuild and test an isolated ABI3 wheel**

  From the exact clean candidate, run:

  ```bash
  candidate_sha="$(git rev-parse HEAD)"
  wheel_root="$(
    mktemp -d \
      "/private/tmp/fastmssql-execute-many-wheel-${candidate_sha}.XXXXXX"
  )"
  CARGO_TARGET_DIR="${wheel_root}/cargo-target" \
    UV_CACHE_DIR="${wheel_root}/uv-cache" \
    uv run maturin build --release --out "${wheel_root}/wheels"
  wheel_path="$(
    find "${wheel_root}/wheels" -maxdepth 1 -type f -name '*.whl' -print -quit
  )"
  test -n "${wheel_path}"
  shasum -a 256 "${wheel_path}"
  UV_CACHE_DIR="${wheel_root}/uv-cache" \
    uv venv --python 3.12 "${wheel_root}/venv"
  UV_CACHE_DIR="${wheel_root}/uv-cache" \
    uv pip install \
      --python "${wheel_root}/venv/bin/python" \
      "${wheel_path}" \
      pytest==9.1.1 \
      pytest-asyncio==1.4.0 \
      pytest-timeout==2.4.0 \
      python-dotenv==1.2.2 \
      psutil==7.2.2
  ```

  Prove isolated import and package metadata:

  ```bash
  WHEEL_ROOT="${wheel_root}" \
    env -u PYTHONPATH "${wheel_root}/venv/bin/python" -c \
    'from importlib.metadata import version
import os
from pathlib import Path
import fastmssql
root = Path(os.environ["WHEEL_ROOT"]).resolve()
module = Path(fastmssql.__file__).resolve()
assert root in module.parents, (root, module)
assert version("fastmssql") == "0.7.7"
print(module)'
  ```

  Run the installed-wheel contracts with `PYTHONPATH` removed:

  ```bash
  env -u PYTHONPATH "${wheel_root}/venv/bin/python" -m pytest \
    --rootdir "${wheel_root}" \
    --import-mode=importlib \
    tests/test_execute_many_contract.py \
    tests/test_execute_many_coordinator.py \
    tests/test_execute_many_stress_contract.py \
    -q
  env -u PYTHONPATH "${wheel_root}/venv/bin/python" -m pytest \
    --rootdir "${wheel_root}" \
    --import-mode=importlib \
    tests/sql_auth_strict/test_execute_many_strict.py \
    -q
  env -u PYTHONPATH "${wheel_root}/venv/bin/python" -m pytest \
    --rootdir "${wheel_root}" \
    --import-mode=importlib \
    tests/sql_auth_strict/test_native_bulk_strict.py::test_native_bulk_ordered_subset_defaults_nulls_and_count \
    tests/sql_auth_strict/test_sql_features.py::test_parameterized_single_row_select \
    tests/sql_auth_strict/test_resultstream_lifecycle.py::test_complete_aclose_retires_spid_before_pool_recovery \
    -q
  uv pip check --python "${wheel_root}/venv/bin/python"
  ```

  Require every test and dependency check to pass. Record the wheel filename,
  SHA-256 and isolated import path, but do not publish or copy the wheel into
  the repository.

- [ ] **Step 2.5: finish Task 10.4/10.5 of the parent plan**

  Rebuild code-review-graph on the exact candidate, use `detect_changes`,
  `get_affected_flows` and `tests_for`, read every changed hunk, run
  `git diff --check`, scan credentials/generated artifacts and confirm a
  clean worktree. Record the fresh evidence in `VERSION.md`, commit:

  ```bash
  git add VERSION.md
  git commit -m "docs: record execute many validation evidence"
  git push origin feat/execute-many
  ```

  Verify exact local/fork SHA equality, `upstream` push `DISABLED`, and read
  GitHub Actions only for the fork. Record hosted status as PASS, FAIL or
  NOT RUN; do not infer it from an ancestor or create an upstream PR.

---

## Task 3: update the production-readiness live document

**Branch:** `docs/execute-many-status`, created from the exact verified
feature commit.

**Files:**

- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Create: `docs/EXECUTE_MANY_VALIDATION_REPORT.md`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: exact source, SQL-auth, stress, wheel, graph, credential and fork
  evidence from Task 2.
- Produces: an auditable `VERIFIED_FORK` status for batch/bulk slice 6 of 7,
  while leaving `query_many()` explicitly open.

- [ ] **Step 3.1: write only claims supported by exact evidence**

  Record branch/commit lineage, focused and full test counts, stress profiles,
  wheel filename/SHA-256/import origin, graph freshness, credential scan and
  fork Actions status. Explain that `EMANY-009` preserves ordinary
  settlement neutrality while security-context SQL retires the session
  fail-closed.

- [ ] **Step 3.2: preserve remaining scope**

  Change the batch/bulk summary from five to six verified slices only after
  all Task 2 gates pass. Keep bounded-concurrency `query_many()` as slice 7
  and keep named-instance/framework-matrix work as roadmap items 20 and 21.

- [ ] **Step 3.3: verify, commit and publish only the status branch**

  Run the matrix/document contracts, `git diff --check`, credential scan and
  code-review-graph rebuild. Inspect every diff, commit:

  ```bash
  git add \
    docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
    docs/EXECUTE_MANY_VALIDATION_REPORT.md \
    VERSION.md
  git commit -m "docs: record execute many production evidence"
  git push --set-upstream origin docs/execute-many-status
  ```

  Require local/fork SHA equality and upstream push URL `DISABLED`.

## Final Acceptance Checklist

- [ ] The approved design, plan and characterization are in feature ancestry.
- [ ] `EMANY-009` proves ordinary neutrality, rollback-only recovery and
  successful security-context retirement on real Docker SQL Server.
- [ ] A different `connection_id` and a successful replacement query prove
  physical retirement and pool recovery.
- [ ] Metrics classify the consumed statement as exactly one success and do
  not count internal settlement.
- [ ] No SQL-auth application session, credential or generated artifact
  leaks.
- [ ] Full source, SQL-auth, stress and isolated-wheel gates pass on the exact
  feature candidate.
- [ ] The fork contains all three branches; the original repository contains
  no push or PR.
- [ ] The live audit marks only six of seven batch/bulk slices verified and
  keeps `query_many()` open.
