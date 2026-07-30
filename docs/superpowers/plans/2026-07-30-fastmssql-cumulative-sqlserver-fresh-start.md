# FastMssql cumulative SQL Server fresh-start implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the canonical cumulative SQL-auth gate start from a fresh SQL
Server process without deleting its persistent databases.

**Architecture:** Extend the executable full-runner sandbox contract with a
recording Docker boundary and a provision marker. Then add only
`--force-recreate` to the existing Compose invocation so the process is
recreated while the named volume remains mounted.

**Tech Stack:** Bash, Docker Compose, pytest, Ruff, Python 3.11 ABI3 package
contracts, SQL Server 2022 Developer, git, code-review-graph.

## Global Constraints

- Work only in the existing project-local linked worktree.
- Keep the displayed package version at `0.7.7`; update `VERSION.md` for
  every repository modification.
- Preserve the named `fastmssql_sql_auth_data` volume; never run
  `docker compose down -v`, `docker volume rm`, or an equivalent deletion.
- Keep `fastmssql-sql-auth-dev` as the only allowed Docker target.
- Do not add a retry or alter SQL Server memory/resource-governor settings.
- Use separate documentation, RED, fix and cumulative-merge branches.
- Push branches only to `https://github.com/galeamarcel/FastMssql.git`;
  `upstream` must retain push URL `DISABLED`.
- Do not record secrets, `.env.sql-auth.local`, wheels, caches or generated
  SQL-auth artifacts.
- A cumulative success claim requires the full parent Task 12 gate on one
  exact clean SHA; focused success is not cumulative success.

---

### Task 1: Commit the executable RED contract

**Branch:** `test/cumulative-sqlserver-fresh-start`

**Base:** final commit of
`docs/cumulative-sqlserver-fresh-start-design`

**Files:**

- Modify: `tests/sql_auth_strict/test_matrix_contract.py:170`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: executable `scripts/sql_auth/run_all.sh` and its existing
  `record compose-up` / `record provision` ordering.
- Produces: a sandbox runner helper returning
  `tuple[subprocess.CompletedProcess[str], Path, Path]`, where the final path
  contains ordered Docker/provision events.

- [ ] **Step 1: create the RED branch from the exact design commit**

Run:

```bash
git status --short --branch
git switch -c test/cumulative-sqlserver-fresh-start
git rev-parse HEAD
git remote get-url --push upstream
```

Expected: clean status before the switch, base equals the design commit, and
the upstream push URL is `DISABLED`.

- [ ] **Step 2: extract the existing sandbox setup into one helper**

Replace the setup portion of
`test_full_runner_uses_original_local_regression_display_name` with:

```python
def _run_full_runner_sandbox(
    tmp_path: Path,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    sandbox = tmp_path / "repo"
    scripts = sandbox / "scripts" / "sql_auth"
    scripts.mkdir(parents=True)
    runner = scripts / "run_all.sh"
    shutil.copy2(ROOT / "scripts/sql_auth/run_all.sh", runner)
    sequence_path = sandbox / "runner-sequence.tsv"

    (sandbox / ".env.sql-auth.local").write_text(
        "\n".join(
            (
                "FASTMSSQL_SQL_AUTH_CONTAINER=fastmssql-sql-auth-dev",
                "FASTMSSQL_SQL_AUTH_HOST=127.0.0.1",
                "FASTMSSQL_SQL_AUTH_PORT=14333",
                "FASTMSSQL_SQL_AUTH_OWNER_USER=test_owner",
                "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD=test_only_password",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    _write_executable(
        scripts / "provision.sh",
        """#!/usr/bin/env bash
set -eu
printf 'provision\\n' >>"${FASTMSSQL_TEST_SEQUENCE_PATH:?}"
""",
    )
    for name in (
        "run_result_stream_stress.sh",
        "run_query_many_stress.sh",
    ):
        _write_executable(
            scripts / name,
            "#!/usr/bin/env bash\nexit 0\n",
        )

    fake_bin = sandbox / "fake-bin"
    fake_bin.mkdir()
    _write_fake_uv(fake_bin / "uv")
    _write_executable(
        fake_bin / "cargo",
        "#!/usr/bin/env bash\nexit 0\n",
    )
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
set -eu
printf 'docker' >>"${FASTMSSQL_TEST_SEQUENCE_PATH:?}"
printf '\\t%s' "$@" >>"${FASTMSSQL_TEST_SEQUENCE_PATH:?}"
printf '\\n' >>"${FASTMSSQL_TEST_SEQUENCE_PATH:?}"
""",
    )

    environment = os.environ.copy()
    environment["PATH"] = (
        f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}"
    )
    environment["FASTMSSQL_TEST_PYTHON_EXECUTABLE"] = sys.executable
    environment["FASTMSSQL_TEST_PYTHON_HOME"] = sys.base_prefix
    environment["FASTMSSQL_TEST_SEQUENCE_PATH"] = str(sequence_path)
    completed = subprocess.run(
        [str(runner)],
        cwd=sandbox,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    return completed, sandbox, sequence_path
```

Keep the existing display-name assertions in their original test by calling:

```python
completed, sandbox, _ = _run_full_runner_sandbox(tmp_path)
```

The helper executes the real runner. It does not inspect its source or assert
on a Python mock.

- [ ] **Step 3: add one behavior-focused fresh-process contract**

Add:

```python
def test_full_runner_recreates_sqlserver_before_provision(
    tmp_path: Path,
) -> None:
    completed, sandbox, sequence_path = _run_full_runner_sandbox(tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert sequence_path.read_text(encoding="utf-8").splitlines() == [
        "\t".join(
            (
                "docker",
                "compose",
                "--env-file",
                str(sandbox / ".env.sql-auth.local"),
                "-f",
                "docker-compose.sql-auth.yml",
                "up",
                "-d",
                "--force-recreate",
                "sqlserver",
            )
        ),
        "provision",
    ]
```

Mutation check: removing or moving `--force-recreate`, adding another Docker
call, or provisioning first must fail this test.

- [ ] **Step 4: run the exact contract and observe RED**

Run:

```bash
.venv/bin/pytest \
  tests/sql_auth_strict/test_matrix_contract.py::test_full_runner_recreates_sqlserver_before_provision \
  -q
```

Expected: one assertion failure showing the actual Docker argument vector
lacks `--force-recreate`; no collection, fixture, import or sandbox error is
acceptable.

- [ ] **Step 5: prove the pre-existing sandbox behavior remains green**

Run:

```bash
.venv/bin/pytest \
  tests/sql_auth_strict/test_matrix_contract.py::test_full_runner_uses_original_local_regression_display_name \
  -q
```

Expected: `1 passed`.

- [ ] **Step 6: record exact RED evidence and verify the test-only diff**

Add a `Cumulative SQL Server fresh-process gate RED` entry to `VERSION.md`
with the exact test count and failure reason. Then run:

```bash
.venv/bin/ruff check tests/sql_auth_strict/test_matrix_contract.py
.venv/bin/python -m compileall -q \
  tests/sql_auth_strict/test_matrix_contract.py
git diff --check
git diff -- \
  tests/sql_auth_strict/test_matrix_contract.py VERSION.md
```

Expected: Ruff, compileall and diff check exit zero; the diff contains no
production file.

- [ ] **Step 7: commit and publish only the RED branch**

Run:

```bash
git add tests/sql_auth_strict/test_matrix_contract.py VERSION.md
git diff --cached --check
git commit -m "test: require fresh SQL Server cumulative gate"
git push -u origin test/cumulative-sqlserver-fresh-start
git ls-remote origin refs/heads/test/cumulative-sqlserver-fresh-start
git remote get-url --push upstream
```

Expected: the remote SHA equals local HEAD and upstream remains `DISABLED`.

---

### Task 2: Implement the minimal runner correction

**Branch:** `fix/cumulative-sqlserver-fresh-start`

**Base:** RED commit from Task 1

**Files:**

- Modify: `scripts/sql_auth/run_all.sh:119`
- Modify: `VERSION.md`
- Test unchanged:
  `tests/sql_auth_strict/test_matrix_contract.py`

**Interfaces:**

- Consumes: the RED event sequence fixed in Task 1.
- Produces: a Compose invocation that creates or force-recreates only the
  `sqlserver` service while retaining the named volume.

- [ ] **Step 1: create the fix branch from the exact RED commit**

Run:

```bash
git status --short --branch
git switch -c fix/cumulative-sqlserver-fresh-start
git merge-base --is-ancestor \
  test/cumulative-sqlserver-fresh-start HEAD
```

Expected: clean status and ancestor check exit zero.

- [ ] **Step 2: add the one required Compose option**

Change only the Compose lane to:

```bash
record compose-up \
  docker compose --env-file "${env_file}" \
  -f docker-compose.sql-auth.yml up -d --force-recreate sqlserver
```

Do not add volume removal, memory settings, retries, sleeps or another Docker
command.

- [ ] **Step 3: run the unchanged RED contract and observe GREEN**

Run:

```bash
.venv/bin/pytest \
  tests/sql_auth_strict/test_matrix_contract.py::test_full_runner_recreates_sqlserver_before_provision \
  -q
```

Expected: `1 passed`.

- [ ] **Step 4: run the complete matrix-contract regression**

Run:

```bash
.venv/bin/pytest tests/sql_auth_strict/test_matrix_contract.py -q
```

Expected: all collected tests pass with zero failures, errors, skips or
warnings.

- [ ] **Step 5: record GREEN evidence and run focused static gates**

Add a `Cumulative SQL Server fresh-process gate fix` entry to `VERSION.md`
with exact RED/GREEN counts. Run:

```bash
.venv/bin/ruff check tests/sql_auth_strict/test_matrix_contract.py
.venv/bin/python -m compileall -q \
  tests/sql_auth_strict/test_matrix_contract.py
bash -n scripts/sql_auth/run_all.sh
git diff --check
```

Expected: every command exits zero.

- [ ] **Step 6: verify scope, privacy and branch topology**

Run:

```bash
git diff --stat test/cumulative-sqlserver-fresh-start...HEAD
git diff --check test/cumulative-sqlserver-fresh-start...HEAD
git diff test/cumulative-sqlserver-fresh-start...HEAD -- \
  scripts/sql_auth/run_all.sh VERSION.md
git merge-base --is-ancestor \
  test/cumulative-sqlserver-fresh-start HEAD
git remote get-url --push upstream
```

Scan the diff for credential URLs, access tokens, private keys, tracked
`.env`, wheel, target/cache and generated report artifacts. Expected: only
the runner and version entry changed on the fix branch, no secret/artifact is
present, the ancestry check exits zero, and upstream is `DISABLED`.

- [ ] **Step 7: rebuild and review the exact knowledge graph**

Run:

```bash
uvx code-review-graph build
```

Use `get_minimal_context`, `detect_changes`, `get_affected_flows` and
`query_graph` with `tests_for` for the fresh-process contract. Require
`head_matches_build=true`; reconcile shell-script graph limitations with the
executable pytest evidence.

- [ ] **Step 8: commit and publish only the fix branch**

Run:

```bash
git add scripts/sql_auth/run_all.sh VERSION.md
git diff --cached --check
git commit -m "fix: recreate SQL Server for cumulative gate"
git push -u origin fix/cumulative-sqlserver-fresh-start
git ls-remote origin refs/heads/fix/cumulative-sqlserver-fresh-start
git remote get-url --push upstream
```

Expected: the remote SHA equals local HEAD and upstream remains `DISABLED`.

---

### Task 3: Merge the correction and resume the cumulative gate

**Branch:** `verify/batch-bulk-merge`

**Base:** cumulative candidate `b997ab3`

**Files:**

- No intentional source change during the history-only merge.
- Generated evidence remains ignored until parent Task 13 deliberately
  regenerates tracked reports.

**Interfaces:**

- Consumes: exact fix branch with RED ancestry.
- Produces: the next exact cumulative SHA for parent batch/bulk Task 12.

- [ ] **Step 1: merge the complete correction history**

Run:

```bash
git status --short --branch
git switch verify/batch-bulk-merge
git merge --no-ff fix/cumulative-sqlserver-fresh-start \
  -m "merge: preserve fresh SQL Server cumulative gate"
git merge-base --is-ancestor \
  test/cumulative-sqlserver-fresh-start HEAD
git merge-base --is-ancestor \
  fix/cumulative-sqlserver-fresh-start HEAD
```

Expected: clean pre-merge status and both ancestry checks exit zero.

- [ ] **Step 2: verify real recreation before the full runner**

Run the exact Compose lane and provision:

```bash
docker compose --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml \
  up -d --force-recreate sqlserver
scripts/sql_auth/provision.sh
```

Capture the container start timestamp before and after if an existing
container was present. Query
`sys.dm_exec_query_resource_semaphores` and
`sys.dm_os_process_memory`; require no low-memory flag and zero waiters before
the test.

- [ ] **Step 3: rerun the exact previously timed-out test**

Run:

```bash
set -a
source .env.sql-auth.local
set +a
.venv/bin/pytest \
  tests/sql_auth_strict/test_native_bulk_strict.py::test_native_bulk_target_guided_mixed_types_and_typed_nulls \
  -q
```

Expected: `1 passed`, unchanged from the RED/fix branches.

- [ ] **Step 4: execute the complete parent cumulative runner**

Run:

```bash
scripts/sql_auth/run_all.sh
```

Expected: every required lane exits zero, the strict suite includes
`BULK-008`, and `.artifacts/sql-auth/compose-up.command` contains the exact
`--force-recreate` invocation.

- [ ] **Step 5: validate exact evidence before continuing parent Task 12**

Require:

- every generated `.exitcode` file equals `0`;
- query-many stress reports the exact cumulative source SHA and
  `worktree_dirty=false`;
- `git status --short` contains only the tracked reports intentionally
  generated by the final report lane;
- no secret appears in logs, JSON, XML, command captures or tracked diffs.

If any mandatory lane fails, stop the completion claim, preserve its exact
artifact and return to systematic debugging. If all pass, continue the
parent plan with cumulative 99,999 stress, fault gates, installed ABI3 wheel,
RustSec, hosted exact-SHA truth and the final graph review.
