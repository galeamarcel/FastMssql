# FastMssql Bounded Compatibility Bulk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Use
> `superpowers:systematic-debugging` for unexpected results,
> `superpowers:test-driven-development` for the behavior change, and
> `superpowers:verification-before-completion` before each commit. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** make compatibility `bulk_insert()` defer Python-cell conversion
until its awaitable runs, retain at most one SQL VALUES chunk of converted
driver values, preserve full rollback, and return zero for empty input without
pool or metric activity.

**Architecture:** keep an owned `Py<PyList>` across awaits and convert one
bounded range under a short `Python::attach` section. Preflight the first
chunk before pool acquisition, then reuse that owned chunk after `BEGIN`;
convert each later chunk only after the previous request is fully drained.

**Tech Stack:** Rust 2024, PyO3 0.29, Tokio, Tiberius 0.12.3,
Python 3.11+ asyncio, pytest, psutil, maturin, and Docker SQL Server 2022 with
SQL authentication.

## Global Constraints

- Work and push only to `https://github.com/galeamarcel/FastMssql.git`.
- Keep `Rivendael/FastMssql` fetch-only with push URL exactly `DISABLED`.
- Use project-local ignored worktrees and separate RED/fix branches.
- Base `fix/bulk-bounded-buffering` on the observed RED commit.
- Update `VERSION.md` on both branches without changing displayed version
  `0.7.7`.
- Never catch-and-ignore an exception or turn a required failure into a skip.
- Preserve compatibility SQL `INSERT ... VALUES` semantics and one atomic
  transaction for non-empty input.
- Do not retry after a write may have reached SQL Server.
- Build the extension from the current worktree before Python verification.
- Use Docker SQL Server with SQL authentication for SQL claims.
- Do not commit credentials, generated Cargo lockfiles, build outputs or
  stress artifacts.
- Rebuild and review code-review-graph after implementation changes.

---

## File Structure

- `tests/test_bulk_bounded_buffering.py`: deterministic offline timing and
  empty-input contracts.
- `tests/sql_auth_strict/test_batch_strict.py`: real SQL Server proof that a
  later conversion failure occurs after a prior chunk reached the server and
  still rolls the transaction back.
- `tests/sql_auth_strict/test_matrix_contract.py`: exact canonical case count
  changes from 372 to 374 when the two new cases are registered.
- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`:
  canonical registration for `BULK-001` and `BULK-002`.
- `scripts/sql_auth/bulk_buffering_probe.py`: opt-in RSS and event-loop-stall
  evidence after the Python list already exists.
- `src/batch.rs`: one-chunk Python conversion and compatibility execution.
- `tests/test_batch_parameter_validation.py`: wording correction for
  validation that now runs inside the awaitable but before first wire I/O.
- `VERSION.md`: RED and fix evidence with no package-version change.

### Task 1: Create the RED worktree

**Files:** none.

**Interfaces:**

- Consumes: committed `docs/batch-bulk-design`.
- Produces: branch `test/bulk-bounded-buffering` in
  `.worktrees/test-bulk-bounded-buffering`.

- [ ] **Step 1: Verify the documentation gate**

```bash
git status --short --branch
git rev-parse HEAD
git remote -v
```

Expected: clean `docs/batch-bulk-design`, pushed fork SHA equals local SHA,
`origin` is `galeamarcel/FastMssql`, and upstream push is `DISABLED`.

- [ ] **Step 2: Create the isolated RED branch**

```bash
git worktree add \
  .worktrees/test-bulk-bounded-buffering \
  -b test/bulk-bounded-buffering \
  docs/batch-bulk-design
```

- [ ] **Step 3: Verify ancestry and ignore coverage**

```bash
git -C .worktrees/test-bulk-bounded-buffering merge-base --is-ancestor \
  docs/batch-bulk-design test/bulk-bounded-buffering
git check-ignore .worktrees/test-bulk-bounded-buffering
```

Expected: both commands exit zero.

### Task 2: Add deterministic offline RED contracts

**Files:**

- Create: `tests/test_bulk_bounded_buffering.py`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes:
  `Connection.bulk_insert(table: str, columns: list[str], rows: list[list[Any]])`.
- Produces:
  `test_bulk_insert_does_not_convert_cells_during_method_creation` and
  `test_empty_bulk_insert_is_zero_io_and_zero_metric`.

- [ ] **Step 1: Write the conversion probe and timing test**

```python
from __future__ import annotations

import asyncio

from fastmssql import (
    Connection,
    OperationMetricsConfig,
    PoolConfig,
    SslConfig,
    TimeoutConfig,
)
import pytest


class _IndexProbe:
    def __init__(self) -> None:
        self.conversions = 0

    def __index__(self) -> int:
        self.conversions += 1
        return 7


def _offline_connection() -> Connection:
    return Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="bulk_probe",
        password="not-used",
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
        timeout_config=TimeoutConfig(
            connect_timeout_secs=1.0,
            acquire_timeout_secs=1.0,
        ),
        operation_metrics_config=OperationMetricsConfig(enabled=True),
    )


@pytest.mark.asyncio
async def test_bulk_insert_does_not_convert_cells_during_method_creation() -> None:
    connection = _offline_connection()
    probe = _IndexProbe()

    awaitable = connection.bulk_insert("dbo.target", ["value"], [[probe]])
    try:
        assert probe.conversions == 0
    finally:
        assert awaitable.cancel()
        with pytest.raises(asyncio.CancelledError):
            await awaitable
```

- [ ] **Step 2: Run the timing test and observe the intended RED**

```bash
.venv/bin/pytest \
  tests/test_bulk_bounded_buffering.py::test_bulk_insert_does_not_convert_cells_during_method_creation \
  -q
```

Expected: FAIL because `probe.conversions` is `1` before the awaitable is
returned. A connection/import failure is not an acceptable RED reason.

- [ ] **Step 3: Add the empty-input contract**

```python
@pytest.mark.asyncio
async def test_empty_bulk_insert_is_zero_io_and_zero_metric() -> None:
    connection = _offline_connection()

    assert await connection.bulk_insert("dbo.target", ["value"], []) == 0
    assert await connection.is_connected() is False
    snapshot = await connection.operation_stats()
    assert snapshot["operations"]["bulk_insert"]["started"] == 0
    assert snapshot["operations"]["bulk_insert"]["completed"] == 0
```

- [ ] **Step 4: Run the empty-input test and observe the intended RED**

```bash
.venv/bin/pytest \
  tests/test_bulk_bounded_buffering.py::test_empty_bulk_insert_is_zero_io_and_zero_metric \
  -q
```

Expected: FAIL with the unreachable endpoint because the unchanged method
initializes/checks out the pool for an empty list.

- [ ] **Step 5: Record the RED contract in `VERSION.md`**

Add a `Compatibility bulk bounded-buffering RED coverage` subsection stating
that the branch adds tests only, both failures are observed on the unchanged
implementation, and the displayed/package version remains `0.7.7`.

### Task 3: Add the real SQL Server late-conversion RED

**Files:**

- Modify: `tests/sql_auth_strict/test_batch_strict.py`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: `_wait_for_request`, `owner_connection`, `sa_connection`,
  `unique_sql_name`, and `CleanupRegistry`.
- Produces:
  - `BULK-001`: a second-chunk conversion failure follows visible first-chunk
    wire activity and rolls back all rows.
  - `BULK-002`: empty input performs no connection admission or metrics.

- [ ] **Step 1: Register the exact cases in the canonical SQL-auth spec**

```markdown
- `BULK-001`: late second-chunk conversion failure occurs after first-chunk
  server activity and rolls back the full compatibility bulk transaction.
- `BULK-002`: empty compatibility bulk returns zero without pool admission or
  operation-metric activity.
```

Update every exact matrix-contract expectation in
`tests/sql_auth_strict/test_matrix_contract.py` from `372` to `374`, including
the test name, generated row count, missing-evidence diagnostic and report
summary.

- [ ] **Step 2: Write `BULK-001`**

```python
@case("BULK-001")
@pytest.mark.asyncio
async def test_bulk_late_conversion_failure_rolls_back_sent_chunk(
    owner_connection: Connection,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    cleanup_registry: CleanupRegistry,
) -> None:
    raw_table = unique_sql_name("strict_bulk_late_conversion")
    raw_trigger = unique_sql_name("strict_bulk_late_conversion_trigger")
    table = quote_identifier(raw_table)
    trigger = quote_identifier(raw_trigger)
    cleanup_registry.add(f"DROP TRIGGER IF EXISTS {trigger}")
    cleanup_registry.add(f"DROP TABLE IF EXISTS {table}")
    await owner_connection.execute(
        f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)"
    )
    await owner_connection.simple_query(
        f"""
        CREATE TRIGGER {trigger}
        ON {table}
        AFTER INSERT
        AS
        BEGIN
            SET NOCOUNT ON;
            IF EXISTS (SELECT 1 FROM inserted WHERE id = 0)
                WAITFOR DELAY '00:00:01';
        END
        """
    )
    rows: list[list[object]] = [[index, index] for index in range(1000)]
    rows.append([1000, object()])

    awaitable = owner_connection.bulk_insert(
        raw_table,
        ["id", "value"],
        rows,
    )
    task = asyncio.ensure_future(awaitable)
    try:
        await _wait_for_request(
            sa_connection,
            raw_table,
            present=True,
            timeout=5.0,
        )
        with pytest.raises(ValueError, match="Unsupported type"):
            await task
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif not task.cancelled():
            task.exception()

    assert await scalar(owner_connection, f"SELECT COUNT(*) FROM {table}") == 0
```

- [ ] **Step 3: Write `BULK-002` using the offline configured connection**

```python
@case("BULK-002")
@pytest.mark.asyncio
async def test_empty_bulk_has_no_pool_or_metric_activity() -> None:
    connection = Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="bulk_probe",
        password="not-used",
        ssl_config=SslConfig.development(),
        pool_config=PoolConfig(
            max_size=1,
            min_idle=0,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
        timeout_config=TimeoutConfig(
            connect_timeout_secs=1.0,
            acquire_timeout_secs=1.0,
        ),
        operation_metrics_config=OperationMetricsConfig(enabled=True),
    )
    assert await connection.bulk_insert("dbo.target", ["value"], []) == 0
    assert await connection.is_connected() is False
    snapshot = await connection.operation_stats()
    assert snapshot["operations"]["bulk_insert"]["started"] == 0
```

- [ ] **Step 4: Build/install the unchanged RED worktree**

```bash
env \
  CARGO_TARGET_DIR=/private/tmp/fastmssql-bulk-buffering-red-target \
  UV_CACHE_DIR=/private/tmp/fastmssql-bulk-buffering-red-uv-cache \
  PYO3_PYTHON=../../.venv/bin/python \
  ../../.venv/bin/maturin develop --release
```

- [ ] **Step 5: Run only the new strict cases**

```bash
set -a
source ../../.env.sql-auth.local
set +a
../../.venv/bin/pytest \
  tests/sql_auth_strict/test_batch_strict.py \
  -k 'bulk_late_conversion_failure or empty_bulk_has_no_pool' \
  -q
```

Expected: both cases fail for the two identified implementation defects.
Collection failure, missing environment, wrong extension origin or unavailable
SQL Server is not acceptable evidence.

### Task 4: Add an opt-in bounded-resource probe

**Files:**

- Create: `scripts/sql_auth/bulk_buffering_probe.py`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: the established `FASTMSSQL_SQL_AUTH_HOST`, `PORT`, `DATABASE`,
  `OWNER_USER`, and `OWNER_PASSWORD` environment fields.
- Produces: one JSON object with `row_count`, `payload_bytes`,
  `rss_baseline_bytes`, `rss_peak_bytes`, `rss_growth_bytes`,
  `max_event_loop_stall_seconds`, `elapsed_seconds`, `affected_rows`, and
  `post_smoke_value`, plus a `violations` list that controls the exit status.

- [ ] **Step 1: Implement the CLI contract**

```python
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import gc
import json
import os
import time
from uuid import uuid4

from fastmssql import Connection, PoolConfig, SslConfig
import psutil


@dataclass(frozen=True, repr=False)
class SqlAuthSettings:
    host: str
    port: int
    database: str
    username: str
    password: str

    @classmethod
    def from_env(cls) -> SqlAuthSettings:
        password = os.getenv("FASTMSSQL_SQL_AUTH_OWNER_PASSWORD", "")
        if not password:
            raise RuntimeError(
                "missing required environment setting FASTMSSQL_SQL_AUTH_OWNER_PASSWORD"
            )
        return cls(
            host=os.getenv("FASTMSSQL_SQL_AUTH_HOST", "127.0.0.1"),
            port=int(os.getenv("FASTMSSQL_SQL_AUTH_PORT", "14334")),
            database=os.getenv(
                "FASTMSSQL_SQL_AUTH_DATABASE",
                "fastmssql_validation",
            ),
            username=os.getenv(
                "FASTMSSQL_SQL_AUTH_OWNER_USER",
                "fastmssql_owner",
            ),
            password=password,
        )

    def connection(self, *, application_name: str) -> Connection:
        return Connection(
            server=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            password=self.password,
            application_name=application_name,
            ssl_config=SslConfig.development(),
            pool_config=PoolConfig(
                max_size=1,
                min_idle=1,
                max_lifetime_secs=None,
                idle_timeout_secs=None,
                connection_timeout_secs=5,
                retry_connection=False,
            ),
        )


def quote_identifier_part(value: str) -> str:
    return f"[{value.replace(']', ']]')}]"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--rows",
        type=int,
        choices=(1_000, 10_000, 99_999),
        required=True,
    )
    parser.add_argument("--payload-bytes", type=int, default=1024)
    parser.add_argument(
        "--rss-growth-limit-bytes",
        type=int,
        default=67_108_864,
    )
    parser.add_argument(
        "--event-loop-stall-limit-seconds",
        type=float,
        default=0.100,
    )
    args = parser.parse_args()
    if args.payload_bytes <= 0:
        parser.error("--payload-bytes must be positive")
    if args.rss_growth_limit_bytes < 0:
        parser.error("--rss-growth-limit-bytes must be non-negative")
    if args.event_loop_stall_limit_seconds <= 0:
        parser.error("--event-loop-stall-limit-seconds must be positive")
    return args


async def run_probe(args: argparse.Namespace) -> int:
    settings = SqlAuthSettings.from_env()
    application_name = f"fastmssql-bulk-buffer-{uuid4().hex}"
    connection = settings.connection(application_name=application_name)
    raw_table = f"fastmssql_bulk_buffer_{uuid4().hex}"
    qualified_table = f"dbo.{raw_table}"
    sql_table = f"[dbo].{quote_identifier_part(raw_table)}"
    process = psutil.Process()

    async with connection:
        await connection.execute(
            f"""
            CREATE TABLE {sql_table} (
                id INT PRIMARY KEY,
                payload VARCHAR(MAX) NOT NULL
            )
            """
        )
        try:
            payload = "x" * args.payload_bytes
            rows: list[list[object]] = [
                [row_index, payload] for row_index in range(args.rows)
            ]
            gc.collect()
            rss_baseline = process.memory_info().rss
            rss_peak = rss_baseline
            max_stall = 0.0
            stop_sampling = asyncio.Event()
            loop = asyncio.get_running_loop()

            async def sample_resources() -> None:
                nonlocal rss_peak, max_stall
                previous = loop.time()
                while not stop_sampling.is_set():
                    await asyncio.sleep(0.001)
                    current = loop.time()
                    max_stall = max(max_stall, current - previous - 0.001)
                    previous = current
                    rss_peak = max(rss_peak, process.memory_info().rss)

            sampler = asyncio.create_task(sample_resources())
            await asyncio.sleep(0)
            started = time.perf_counter()
            try:
                affected = await connection.bulk_insert(
                    qualified_table,
                    ["id", "payload"],
                    rows,
                )
            finally:
                stop_sampling.set()
                await sampler
            elapsed = time.perf_counter() - started
            rss_peak = max(rss_peak, process.memory_info().rss)
            persisted = (
                await connection.query(
                    f"SELECT COUNT_BIG(*) AS row_count FROM {sql_table}"
                )
            ).fetchone()["row_count"]
            post_smoke_value = (
                await connection.query("SELECT CAST(1 AS INT) AS value")
            ).fetchone()["value"]
        finally:
            await connection.execute(f"DROP TABLE IF EXISTS {sql_table}")

    rss_growth = max(0, rss_peak - rss_baseline)
    violations: list[str] = []
    if affected != args.rows or persisted != args.rows:
        violations.append("row_count_mismatch")
    if post_smoke_value != 1:
        violations.append("post_smoke_failed")
    if rss_growth > args.rss_growth_limit_bytes:
        violations.append("rss_growth_exceeded")
    if max_stall > args.event_loop_stall_limit_seconds:
        violations.append("event_loop_stall_exceeded")

    result = {
        "affected_rows": affected,
        "elapsed_seconds": elapsed,
        "max_event_loop_stall_seconds": max_stall,
        "payload_bytes": args.payload_bytes,
        "persisted_rows": persisted,
        "post_smoke_value": post_smoke_value,
        "row_count": args.rows,
        "rss_baseline_bytes": rss_baseline,
        "rss_growth_bytes": rss_growth,
        "rss_peak_bytes": rss_peak,
        "violations": violations,
    }
    print(json.dumps(result, sort_keys=True))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_probe(parse_args())))
```

- [ ] **Step 2: Run the unchanged implementation for diagnostic RED evidence**

```bash
set -a
source ../../.env.sql-auth.local
set +a
../../.venv/bin/python scripts/sql_auth/bulk_buffering_probe.py \
  --rows 10000 \
  --payload-bytes 1024 \
  --rss-growth-limit-bytes 67108864 \
  --event-loop-stall-limit-seconds 0.100
```

Expected: the JSON or nonzero result records the eager conversion behavior.
This probe supplements, but does not replace, the deterministic RED tests.

### Task 5: Verify and commit the RED branch

**Files:** all RED files above.

**Interfaces:**

- Produces: an evidence-only commit with no production-code delta.

- [ ] **Step 1: Prove production code is unchanged**

```bash
git diff --name-only docs/batch-bulk-design...HEAD
git diff -- src python/fastmssql
```

Expected: no `src/` or `python/fastmssql/` production changes.

- [ ] **Step 2: Run hygiene checks**

```bash
git diff --check
rg -n 'T[B]D|TO[D]O|implement la[t]er|fill in detai[l]s|Similar to Tas[k]' \
  tests/test_bulk_bounded_buffering.py \
  tests/sql_auth_strict/test_batch_strict.py \
  tests/sql_auth_strict/test_matrix_contract.py \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  scripts/sql_auth/bulk_buffering_probe.py \
  VERSION.md
```

Expected: no output.

- [ ] **Step 3: Commit and push only the RED branch**

```bash
git add \
  tests/test_bulk_bounded_buffering.py \
  tests/sql_auth_strict/test_batch_strict.py \
  tests/sql_auth_strict/test_matrix_contract.py \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  scripts/sql_auth/bulk_buffering_probe.py \
  VERSION.md
git commit -m "test: require bounded compatibility bulk input"
git push -u origin test/bulk-bounded-buffering
```

### Task 6: Create the implementation branch

**Files:** none.

**Interfaces:**

- Consumes: exact RED commit from Task 5.
- Produces: branch `fix/bulk-bounded-buffering` in
  `.worktrees/fix-bulk-bounded-buffering`.

- [ ] **Step 1: Create the fix worktree from RED**

```bash
git worktree add \
  .worktrees/fix-bulk-bounded-buffering \
  -b fix/bulk-bounded-buffering \
  test/bulk-bounded-buffering
```

- [ ] **Step 2: Prove exact RED ancestry**

```bash
git -C .worktrees/fix-bulk-bounded-buffering merge-base --is-ancestor \
  test/bulk-bounded-buffering fix/bulk-bounded-buffering
```

Expected: exit zero.

### Task 7: Implement one-chunk conversion

**Files:**

- Modify: `src/batch.rs`
- Modify: `tests/test_batch_parameter_validation.py`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes:
  `bulk_rows_per_batch`, `fix_bulk_null_types`,
  `python_to_fast_parameter`, `rollback_after_failure`, and the existing
  `OperationName::BulkInsert` lifecycle/metric path.
- Produces:

```rust
fn convert_bulk_chunk(
    data_rows: &Py<PyList>,
    start: usize,
    rows_per_batch: usize,
    col_count: usize,
) -> PyResult<Vec<FastParameter>>;
```

- [ ] **Step 1: Add the focused chunk converter**

```rust
fn convert_bulk_chunk(
    data_rows: &Py<PyList>,
    start: usize,
    rows_per_batch: usize,
    col_count: usize,
) -> PyResult<Vec<FastParameter>> {
    Python::attach(|py| {
        let rows = data_rows.bind(py);
        let end = start.saturating_add(rows_per_batch).min(rows.len());
        let mut chunk = Vec::with_capacity((end - start) * col_count);
        for row_index in start..end {
            let row = rows.get_item(row_index)?;
            let row = row.cast::<PyList>()?;
            if row.len() != col_count {
                return Err(PyValueError::new_err(format!(
                    "Row has {} values but {} columns specified",
                    row.len(),
                    col_count
                )));
            }
            for value in row.iter() {
                chunk.push(python_to_fast_parameter(&value)?);
            }
        }
        fix_bulk_null_types(&mut chunk, col_count);
        Ok(chunk)
    })
}
```

- [ ] **Step 2: Replace eager outer-chunk construction**

At the PyO3 boundary retain only:

```rust
let row_count = data_rows.len();
let data_rows = data_rows.clone().unbind();
```

Keep identifier validation synchronous. For `row_count == 0`, return a
ready awaitable containing `0u64` before `observe_operation`, lifecycle
admission or pool initialization.

- [ ] **Step 3: Preflight and reuse the first chunk**

Inside the async operation, call:

```rust
let deadline =
    deadline_from(TimeoutPhase::Operation, timeout_config.operation_timeout);
let mut start = 0usize;
let mut chunk = convert_bulk_chunk(
    &data_rows,
    start,
    rows_per_batch,
    col_count,
)?;
```

Do this before pool initialization so first-chunk shape/conversion errors
remain local and zero-I/O. After checkout and `BEGIN TRANSACTION`, execute
that same chunk without converting it again. Reuse this `deadline` for the
complete BEGIN/chunk/COMMIT sequence so preflight time reduces, rather than
resets, the existing operation budget.

- [ ] **Step 4: Convert later chunks only after the previous drain**

After each successful `conn.execute(...).await`:

```rust
start += row_count_in_batch;
if start >= row_count {
    break;
}
chunk = convert_bulk_chunk(
    &data_rows,
    start,
    rows_per_batch,
    col_count,
)?;
```

Build parameter references only for the current `chunk`. Use
`checked_add()` for `total_affected`; overflow returns a local typed Python
error and follows the existing rollback path.

- [ ] **Step 5: Correct the validation-test explanation**

Change the `TestBatchItemStructureValidation` docstring to state that bulk row
checks run inside the returned awaitable before first wire I/O, while
`parse_batch_items()` remains synchronous.

- [ ] **Step 6: Record the fix in `VERSION.md`**

State the exact bounded ownership, deferred conversion, empty zero-I/O
behavior, rollback preservation and verified gates. Do not modify package
metadata or displayed version `0.7.7`.

### Task 8: Prove RED becomes GREEN

**Files:** no new files.

**Interfaces:**

- Produces: focused local and SQL-auth evidence from the fix worktree.

- [ ] **Step 1: Run Rust format and focused unit tests**

```bash
cargo fmt --check
cargo test --locked bulk_chunking_respects_row_constructor_and_parameter_limits
```

- [ ] **Step 2: Build/install this exact worktree**

```bash
env \
  CARGO_TARGET_DIR=/private/tmp/fastmssql-bulk-buffering-fix-target \
  UV_CACHE_DIR=/private/tmp/fastmssql-bulk-buffering-fix-uv-cache \
  PYO3_PYTHON=../../.venv/bin/python \
  ../../.venv/bin/maturin develop --release
```

- [ ] **Step 3: Run focused offline and SQL-auth tests**

```bash
../../.venv/bin/pytest tests/test_bulk_bounded_buffering.py -q
set -a
source ../../.env.sql-auth.local
set +a
../../.venv/bin/pytest \
  tests/sql_auth_strict/test_batch_strict.py \
  -k 'bulk_late_conversion_failure or empty_bulk_has_no_pool' \
  -q
```

Expected: all new tests pass without skips.

- [ ] **Step 4: Run the complete compatibility-bulk regression**

```bash
../../.venv/bin/pytest tests/test_batch_parameter_validation.py -q
../../.venv/bin/pytest tests/sql_auth_strict/test_batch_strict.py -q
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
```

Expected: all pass, no warnings and no skipped required SQL-auth cases.

- [ ] **Step 5: Run bounded-resource profiles**

```bash
../../.venv/bin/python scripts/sql_auth/bulk_buffering_probe.py \
  --rows 1000 \
  --payload-bytes 1024 \
  --rss-growth-limit-bytes 67108864 \
  --event-loop-stall-limit-seconds 0.100
../../.venv/bin/python scripts/sql_auth/bulk_buffering_probe.py \
  --rows 10000 \
  --payload-bytes 1024 \
  --rss-growth-limit-bytes 67108864 \
  --event-loop-stall-limit-seconds 0.100
```

Run `--rows 99999` only under the owner's standing opt-in stress approval.
Every run must report exact JSON, zero errors, the configured RSS/stall bounds
and a successful post-load smoke query.

### Task 9: Review, commit and publish the fix

**Files:** all fix-branch files.

**Interfaces:**

- Produces: reviewed `fix/bulk-bounded-buffering` fork branch.

- [ ] **Step 1: Rebuild and inspect code-review-graph**

```bash
uvx code-review-graph build
```

Use `detect_changes` against the RED SHA, `get_affected_flows`, and
`tests_for` on `src/batch.rs::bulk_insert`. Resolve every high-risk finding or
record a concrete residual risk in the design/audit.

- [ ] **Step 2: Run final hygiene and ancestry checks**

```bash
git diff --check
git merge-base --is-ancestor \
  test/bulk-bounded-buffering fix/bulk-bounded-buffering
git status --short
```

- [ ] **Step 3: Commit the implementation**

```bash
git add \
  src/batch.rs \
  tests/test_batch_parameter_validation.py \
  VERSION.md
git commit -m "fix: bound compatibility bulk conversion"
```

Include any test-only correction in the same commit only when it explains or
verifies this implementation; do not alter RED assertions.

- [ ] **Step 4: Push only the fork branch and verify parity**

```bash
git push -u origin fix/bulk-bounded-buffering
git rev-parse HEAD
git ls-remote --heads origin refs/heads/fix/bulk-bounded-buffering
```

Expected: local and remote SHAs are identical. Do not update the live audit to
claim the overall `feat/batch-bulk` item complete; only this verified slice may
be recorded after cumulative integration.
