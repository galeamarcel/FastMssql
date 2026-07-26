# FastMssql Pool Observability Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking. This session executes inline;
> delegated subagents are not permitted by the active repository instructions.

**Goal:** Expose the complete low-cardinality statistics already maintained by
bb8 through the existing `Connection.pool_stats()` API, with deterministic
real-SQL proof for checkout pressure, acquisition timeouts, every connection
retirement category, concurrent scraping, lifecycle reset and privacy.

**Architecture:** Capture bb8's current-pool `State`, copy its gauges and
counters into a private scalar snapshot, reconcile independently loaded
checkout atomics so the public pending-count invariant cannot underflow, and
build the existing Python dictionary from that snapshot. No operation path,
pool policy, callback, exporter, global state or dependency changes.

**Tech stack:** Rust 2024, PyO3 0.29, pyo3-async-runtimes 0.29, Tokio 1.52,
bb8 0.9.1, vendored Tiberius, Python 3.11+, pytest/pytest-asyncio, Docker SQL
Server with SQL authentication, GitHub Actions Linux/macOS/Windows ABI3 wheel
contracts.

## Global constraints

- Source baseline is `test/sql-auth-validation` at
  `8bf2a448df9a46128aebc7da8b9ed60b3bb249ea`.
- Design authority is
  `docs/superpowers/specs/2026-07-26-fastmssql-pool-observability-design.md`.
- All branches, commits, pushes, workflow runs and artifacts target only
  `https://github.com/galeamarcel/FastMssql.git`.
- `upstream` must remain fetch-only with push URL exactly `DISABLED`.
- No upstream branch, pull request, package publication or dependency fork is
  authorized.
- Use project-local `.worktrees/`; preserve all existing worktrees and dirty
  user state.
- Keep documentation, RED tests, GREEN implementation and final status on
  distinct branches.
- RED assertions may not be deleted, weakened, skipped or converted to expected
  failures in GREEN.
- Use the approved Docker SQL Server with SQL authentication for SQL behavior;
  never substitute SQLite or a mocked DB API.
- Never print, copy, add or commit `.env.sql-auth.local`, credentials,
  connection strings or privacy sentinels.
- `Connection.pool_stats()` remains the only public API entry point. It gains
  eleven fixed keys and returns exactly seventeen keys.
- Existing six keys and their semantics remain unchanged.
- Counters belong to the current concrete pool epoch and reset to zero before
  connection, after disconnect and after a subsequent new pool is created.
- bb8 loads statistics from independent `Relaxed` atomics. Never call
  subtraction-based `Statistics::pending_gets()` directly.
- Reconcile checkout counters with saturating addition and safe subtraction:

  ```text
  completed = get_direct + get_waited + get_timed_out
  exported_started = max(bb8_started, completed)
  pending = exported_started - completed
  ```

- Normal SQL operations receive no new lock, allocation, Python callback,
  metric update or exporter work.
- Pool reads remain diagnostic and lifecycle-independent; they perform no SQL,
  pool creation or retry.
- Direct `execute_batch()` sockets and direct `Transaction(...)` sockets remain
  outside `pool_stats()`.
- Existing `POOL-013` and `POOL-014` reaper scenarios are extended in place;
  do not add duplicate 30-second reaper sleeps.
- Run graph-first review before source editing and rebuild the graph after
  implementation.
- The repository has no `VERSION.md`; handoff records this fact without
  creating an unrelated version file.

---

## Exact public schema

Every implementation and test uses this one canonical key set:

```python
POOL_STATS_KEYS = {
    "connected",
    "connections",
    "idle_connections",
    "active_connections",
    "max_size",
    "min_idle",
    "get_started",
    "get_direct",
    "get_waited",
    "get_timed_out",
    "pending_gets",
    "get_wait_time_seconds",
    "connections_created",
    "connections_closed_broken",
    "connections_closed_invalid",
    "connections_closed_max_lifetime",
    "connections_closed_idle_timeout",
}
```

The value contract is:

```python
{
    "connected": bool,
    "connections": int,
    "idle_connections": int,
    "active_connections": int,
    "max_size": int,
    "min_idle": int | None,
    "get_started": int,
    "get_direct": int,
    "get_waited": int,
    "get_timed_out": int,
    "pending_gets": int,
    "get_wait_time_seconds": float,
    "connections_created": int,
    "connections_closed_broken": int,
    "connections_closed_invalid": int,
    "connections_closed_max_lifetime": int,
    "connections_closed_idle_timeout": int,
}
```

Both public stubs therefore return:

```python
Coroutine[Any, Any, Dict[str, int | float | bool | None]]
```

## File map and ownership boundaries

### New files

- `tests/test_pool_observability_contract.py` — no-network runtime, stub,
  wrapper and README contract that also runs against an installed wheel.
- `tests/sql_auth_strict/test_pool_observability.py` — real SQL-auth cases
  `OBS-001..004`, `OBS-006` and `OBS-010`.

### Existing implementation/API files

- `src/connection.rs` — private snapshot/reconciliation helpers and the eleven
  new dictionary entries.
- `python/fastmssql/__init__.py` — complete wrapper docstring.
- `python/fastmssql/fastmssql.pyi` — complete core return type and key docs.
- `python/fastmssql/__init__.pyi` — complete wrapper return type and key docs.
- `README.md` — current-pool epoch semantics, invariant and example.

### Existing tests/orchestration files

- `tests/sql_auth_strict/test_connection.py` — strengthen `CONN-019` to the
  exact seventeen-key schema and attach `OBS-001`.
- `tests/sql_auth_strict/test_pool.py` — common metric invariants; extend
  cancellation, lifetime and idle retirement as `OBS-005`, `OBS-007` and
  `OBS-008`.
- `tests/sql_auth_strict/test_resilience_load.py` — `OBS-009`, 10,000 bounded
  parameterized queries with concurrent scraping.
- `tests/sql_auth_strict/conftest.py` — allow the load-metrics recorder's
  otherwise `LOAD-*`-only namespace to accept exactly `OBS-009`.
- `tests/sql_auth_strict/test_matrix_contract.py` — authoritative count
  `311 -> 321`.
- `tests/test_pyo3_build_contract.py` — hosted installed-wheel command includes
  the new contract.
- `scripts/sql_auth/run_all.sh` — strict functional lane includes the focused
  observability file.
- `.github/workflows/rust-unit-tests.yml` — Linux/macOS/Windows installed-wheel
  contract includes the new file.

### Existing specifications and final status files

- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
  — exact `OBS-001..010` registry.
- `docs/SQL_AUTH_TEST_MATRIX.md` — regenerate only from the exact technical
  merge.
- `docs/SQL_AUTH_TEST_REPORT.md` — regenerate only from the exact technical
  merge.
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` — final measured status,
  limitations and next candidate only after verification.
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md` — future
  independently reviewable PR candidate; no PR creation.

---

### Task 1: Preserve the approved design and create the RED worktree

**Files:**

- Verify:
  `docs/superpowers/specs/2026-07-26-fastmssql-pool-observability-design.md`
- Verify:
  `docs/superpowers/plans/2026-07-26-fastmssql-pool-observability.md`

**Produces:** a clean, pushed `docs/observability-metrics-design` branch and
isolated `test/observability-metrics` worktree.

- [ ] **Step 1: Verify branch, remotes, baseline and design diff**

Run:

```bash
git status --short --branch
git remote -v
git rev-parse HEAD
git merge-base --is-ancestor \
  8bf2a448df9a46128aebc7da8b9ed60b3bb249ea HEAD
git diff --check
```

Expected:

```text
branch docs/observability-metrics-design
origin fetch/push galeamarcel/FastMssql
upstream fetch Rivendael/FastMssql
upstream push DISABLED
baseline is an ancestor
no whitespace errors
```

- [ ] **Step 2: Self-review the specification and plan**

Check every public key, ID, branch name and count with:

```bash
rg -n \
  "OBS-00[1-9]|OBS-010|seventeen|321|Relaxed|pending_gets|DISABLED" \
  docs/superpowers/specs/2026-07-26-fastmssql-pool-observability-design.md \
  docs/superpowers/plans/2026-07-26-fastmssql-pool-observability.md
```

Expected:

- exactly ten unique OBS IDs are defined;
- exact schema has seventeen names;
- no direct OpenTelemetry/callback implementation leaked into scope;
- no placeholder, unresolved option or upstream write exists.

- [ ] **Step 3: Commit and push documentation only to the fork**

Run:

```bash
git add \
  docs/superpowers/specs/2026-07-26-fastmssql-pool-observability-design.md \
  docs/superpowers/plans/2026-07-26-fastmssql-pool-observability.md
git commit -m "docs: plan pool observability metrics"
git push -u origin docs/observability-metrics-design
```

Expected: only the fork branch advances.

- [ ] **Step 4: Create the RED branch/worktree**

From the main repository worktree:

```bash
git worktree add .worktrees/test-observability-metrics \
  -b test/observability-metrics docs/observability-metrics-design
```

- [ ] **Step 5: Link the ignored SQL-auth environment safely**

Inside the RED worktree:

```bash
test -f ../../.env.sql-auth.local
test ! -e .env.sql-auth.local
ln -s ../../.env.sql-auth.local .env.sql-auth.local
git check-ignore .env.sql-auth.local
```

Do not display the file.

- [ ] **Step 6: Verify the inherited baseline**

Run:

```bash
git status --short --branch
cargo test --locked
```

Expected: clean RED worktree and all inherited Rust tests pass.

---

### Task 2: Add the RED public, matrix and hosted-wheel contracts

**Files:**

- Create: `tests/test_pool_observability_contract.py`
- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify: `tests/test_pyo3_build_contract.py`
- Modify: `.github/workflows/rust-unit-tests.yml`
- Modify: `scripts/sql_auth/run_all.sh`

**Produces:** an exact no-network RED API contract, ten registered SQL-auth IDs
and cross-platform installed-wheel enforcement.

- [ ] **Step 1: Add the authoritative OBS registry**

Add this section to the SQL-auth specification after POOL:

```markdown
### OBS — pool observability

- `OBS-001`: exact public key/type schema, zero disconnected snapshot and all
  arithmetic invariants.
- `OBS-002`: direct checkout and physical creation counters increase after
  successful SQL and remain bounded by `max_size`.
- `OBS-003`: pool saturation exposes a server-gated pending checkout, then a
  waited completion and positive accumulated wait time.
- `OBS-004`: acquire timeout increments `get_timed_out` exactly once and pool
  recovery remains successful.
- `OBS-005`: cancelled pooled work increments
  `connections_closed_broken` and replaces the physical session.
- `OBS-006`: a killed idle session with checkout validation enabled increments
  `connections_closed_invalid` before a healthy replacement is returned.
- `OBS-007`: maximum-lifetime retirement increments only its exact close
  category.
- `OBS-008`: idle reaping increments only its exact close category.
- `OBS-009`: concurrent scraping during 10,000 bounded SQL operations
  preserves invariants, event-loop progress and post-load health.
- `OBS-010`: SQL, parameters, application/database/login identifiers and
  credentials are absent from pool statistics and evidence.
```

Change every authoritative matrix/report fixture count from 311 to 321,
including function names, expected error text and report rows. Do not change
unrelated historic evidence prose.

- [ ] **Step 2: Create the no-network installed-wheel contract**

Create `tests/test_pool_observability_contract.py` with:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import fastmssql


ROOT = Path(__file__).resolve().parents[1]
CORE_STUB = ROOT / "python/fastmssql/fastmssql.pyi"
WRAPPER_STUB = ROOT / "python/fastmssql/__init__.pyi"
README = ROOT / "README.md"
POOL_STATS_KEYS = {
    "connected",
    "connections",
    "idle_connections",
    "active_connections",
    "max_size",
    "min_idle",
    "get_started",
    "get_direct",
    "get_waited",
    "get_timed_out",
    "pending_gets",
    "get_wait_time_seconds",
    "connections_created",
    "connections_closed_broken",
    "connections_closed_invalid",
    "connections_closed_max_lifetime",
    "connections_closed_idle_timeout",
}
INTEGER_METRICS = POOL_STATS_KEYS - {
    "connected",
    "min_idle",
    "get_wait_time_seconds",
}


async def _disconnected_stats() -> dict[str, object]:
    connection = fastmssql.Connection(
        server="127.0.0.1",
        port=1,
        database="master",
        username="pool_observability_contract",
        password="not-used",
        ssl_config=fastmssql.SslConfig.development(),
        pool_config=fastmssql.PoolConfig(
            max_size=2,
            min_idle=1,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=1,
            retry_connection=False,
        ),
    )
    return await connection.pool_stats()


def test_disconnected_pool_stats_exact_schema_types_and_zero_epoch() -> None:
    stats = asyncio.run(_disconnected_stats())
    assert set(stats) == POOL_STATS_KEYS
    assert stats["connected"] is False
    assert stats["max_size"] == 2
    assert stats["min_idle"] == 1
    assert type(stats["get_wait_time_seconds"]) is float
    assert all(type(stats[key]) is int for key in INTEGER_METRICS)
    assert all(
        stats[key] == 0
        for key in POOL_STATS_KEYS - {"connected", "max_size", "min_idle"}
    )


def test_stubs_wrapper_and_readme_publish_the_same_contract() -> None:
    annotation = (
        "Coroutine[Any, Any, Dict[str, int | float | bool | None]]"
    )
    for stub in (CORE_STUB, WRAPPER_STUB):
        text = stub.read_text(encoding="utf-8")
        assert annotation in text
        assert all(f"- {key} " in text for key in POOL_STATS_KEYS)
    wrapper = (
        ROOT / "python/fastmssql/__init__.py"
    ).read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    for key in POOL_STATS_KEYS:
        assert key in wrapper
        assert key in readme
    assert "current pool" in readme.lower()
    assert "reset" in readme.lower()
```

The first test must fail on the six-key baseline. Do not weaken it into subset
checks.

- [ ] **Step 3: Wire the contract into every hosted OS**

Append `tests/test_pool_observability_contract.py` to the installed contract
pytest command in `.github/workflows/rust-unit-tests.yml`, and update the
normalized exact command asserted by `tests/test_pyo3_build_contract.py`.

The resulting command must be:

```text
tests/test_pool_config_default_contract.py
tests/test_timeout_config_contract.py
tests/test_lifecycle_contract.py
tests/test_pool_observability_contract.py -q
```

- [ ] **Step 4: Wire the focused SQL-auth test file**

Add:

```bash
tests/sql_auth_strict/test_pool_observability.py
```

to `strict_functional` immediately after `test_pool.py`. The load case remains
in `test_resilience_load.py`, which is already selected by the load lane.

- [ ] **Step 5: Run the static contracts**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/test_pyo3_build_contract.py -q
```

Expected: static wiring and the 321-ID specification contract pass once all
OBS source decorators have been added in Tasks 3–5. It may temporarily report
the not-yet-added source IDs before those tasks; that is not the behavioral RED
proof.

---

### Task 3: Add RED direct, pending and timeout SQL-auth cases

**Files:**

- Create: `tests/sql_auth_strict/test_pool_observability.py`
- Modify: `tests/sql_auth_strict/test_connection.py`

**Produces:** `OBS-001..004`.

- [ ] **Step 1: Add one shared exact invariant helper**

In the focused file define:

```python
import math


def assert_pool_metrics(stats: dict[str, object], *, connected: bool) -> None:
    assert set(stats) == POOL_STATS_KEYS
    assert stats["connected"] is connected
    assert stats["active_connections"] == (
        stats["connections"] - stats["idle_connections"]
    )
    assert 0 <= stats["idle_connections"] <= stats["connections"]
    assert stats["connections"] <= stats["max_size"]
    assert stats["get_started"] == (
        stats["get_direct"]
        + stats["get_waited"]
        + stats["get_timed_out"]
        + stats["pending_gets"]
    )
    assert all(
        stats[key] >= 0
        for key in POOL_STATS_KEYS
        - {"connected", "min_idle", "get_wait_time_seconds"}
    )
    assert math.isfinite(stats["get_wait_time_seconds"])
    assert stats["get_wait_time_seconds"] >= 0.0
```

Add bounded polling helpers for:

- an exact metric value;
- an active request visible in `sys.dm_exec_requests`;
- session disappearance.

All helpers use `time.monotonic()` deadlines and short `asyncio.sleep()`
polling. A fixed scheduler sleep is never accepted as proof of a pending
checkout or server request.

- [ ] **Step 2: Strengthen `CONN-019` and attach `OBS-001`**

Change only the decorator and exact key assertion:

```python
import math

@case("CONN-019", "OBS-001")
```

Preserve every existing assertion and add:

```python
assert stats["get_started"] == (
    stats["get_direct"]
    + stats["get_waited"]
    + stats["get_timed_out"]
    + stats["pending_gets"]
)
for key in {
    "get_started",
    "get_direct",
    "get_waited",
    "get_timed_out",
    "pending_gets",
    "connections_created",
    "connections_closed_broken",
    "connections_closed_invalid",
    "connections_closed_max_lifetime",
    "connections_closed_idle_timeout",
}:
    assert type(stats[key]) is int
    assert stats[key] >= 0
assert type(stats["get_wait_time_seconds"]) is float
assert math.isfinite(stats["get_wait_time_seconds"])
assert stats["get_wait_time_seconds"] >= 0.0
```

Also check:

1. the no-pool snapshot has seventeen keys and zero new counters;
2. the connected snapshot satisfies every invariant;
3. after `disconnect()`, all new counters return to zero;
4. reconnect starts a fresh epoch and succeeds.

- [ ] **Step 3: Add `OBS-002` direct-checkout accounting**

Use `max_size=1`, `min_idle=1`, no lifetime/idle reaper, checkout validation
disabled. Strictly connect first and capture a baseline. Then:

```python
before = await connection.pool_stats()
assert await scalar(connection, "SELECT @P1", [42]) == 42
after = await connection.pool_stats()
assert after["get_started"] - before["get_started"] == 1
assert after["get_direct"] - before["get_direct"] == 1
assert after["get_waited"] == before["get_waited"]
assert after["get_timed_out"] == before["get_timed_out"]
assert after["pending_gets"] == 0
assert after["connections_created"] == before["connections_created"]
```

The warmup/validation checkout is intentionally outside the measured delta.

- [ ] **Step 4: Add `OBS-003` server-gated pending/waited accounting**

Warm one physical session and record its SPID. Capture a baseline, start:

```sql
WAITFOR DELAY '00:00:00.750'; SELECT 1
```

Wait until that SPID appears in `sys.dm_exec_requests`. Start a second
`SELECT @P1`, then poll until `pending_gets == 1`. Assert the live arithmetic
invariant. After both tasks finish:

```python
assert after["get_started"] - before["get_started"] == 2
assert after["get_direct"] - before["get_direct"] == 1
assert after["get_waited"] - before["get_waited"] == 1
assert after["get_timed_out"] == before["get_timed_out"]
assert after["pending_gets"] == 0
assert (
    after["get_wait_time_seconds"]
    > before["get_wait_time_seconds"]
)
```

- [ ] **Step 5: Add `OBS-004` exact acquire timeout and recovery**

Use `max_size=1`, one warm idle connection and:

```python
TimeoutConfig(
    connect_timeout_secs=2.0,
    acquire_timeout_secs=0.25,
    operation_timeout_secs=3.0,
    transaction_timeout_secs=3.0,
    rollback_timeout_secs=2.0,
)
```

Hold the lease with a server-visible one-second WAITFOR. Assert the competing
query raises `OperationTimeoutError` with `phase == "acquire"` and preserves
the existing retryable/discard/outcome metadata. Then assert:

```python
assert after_timeout["get_timed_out"] - before["get_timed_out"] == 1
assert after_timeout["pending_gets"] == 0
assert await holder == 1
assert await scalar(connection, "SELECT 2") == 2
assert recovered["get_timed_out"] == after_timeout["get_timed_out"]
```

No exception is swallowed in cleanup.

---

### Task 4: Add RED retirement-category and privacy cases

**Files:**

- Modify: `tests/sql_auth_strict/test_pool.py`
- Modify: `tests/sql_auth_strict/test_pool_observability.py`

**Produces:** `OBS-005..008` and `OBS-010`.

- [ ] **Step 1: Extend cancellation retirement as `OBS-005`**

Attach the ID to the existing cancellation case:

```python
@case("POOL-012", "OBS-005")
```

Record the first `@@SPID`, physical `connection_id` and counter baseline.
Cancel only after the WAITFOR request is visible on SQL Server. Await the
`CancelledError`, poll until:

```python
after_cancel["connections_closed_broken"] \
    == before["connections_closed_broken"] + 1
```

Then prove a new query succeeds on a different physical `connection_id` and
that no other retirement-event counter changed.

- [ ] **Step 2: Add killed-idle validation as `OBS-006`**

Create a one-connection pool with `test_on_check_out=True`, obtain its SPID and
physical ID, let it return idle, and issue `KILL {killed_spid}` through the
privileged fixture. Wait until the server session is absent, then execute
`SELECT @@SPID`.

Assert:

```python
assert replacement_connection_id != killed_connection_id
assert after["connections_closed_invalid"] \
    == before["connections_closed_invalid"] + 1
assert after["connections_created"] \
    == before["connections_created"] + 1
assert after["connections_closed_broken"] \
    == before["connections_closed_broken"] + 1
```

Checkout validation, not a later application error, must discover the killed
idle transport. Do not require a different `@@SPID`: SQL Server can
immediately reuse the smallint session ID after `KILL`. The
`sys.dm_exec_connections.connection_id` UUID is the physical-connection
identity required by this case.

This intentionally proves overlapping bb8 retirement categories. bb8 records
`connections_closed_invalid` when `is_valid()` returns an error, then invokes
`has_broken()` while dropping the invalid lease. FastMssql marks validation
unsafe before its first await for cancellation safety, so the killed transport
also records `connections_closed_broken`. Do not make the categories exclusive
or clear the broken disposition on an error path.

- [ ] **Step 3: Extend maximum-lifetime retirement as `OBS-007`**

Attach:

```python
@case("POOL-014", "OBS-007")
```

Keep the existing session/connection-ID replacement proof. Add baseline/final
counter assertions:

```python
assert after["connections_closed_max_lifetime"] \
    == before["connections_closed_max_lifetime"] + 1
assert after["connections_closed_idle_timeout"] \
    == before["connections_closed_idle_timeout"]
assert after["connections_closed_broken"] \
    == before["connections_closed_broken"]
assert after["connections_closed_invalid"] \
    == before["connections_closed_invalid"]
```

- [ ] **Step 4: Extend idle reaping as `OBS-008`**

Attach:

```python
@case("POOL-013", "OBS-008")
```

Reuse its existing bounded 35-second reaper wait. Assert exactly one increment
of `connections_closed_idle_timeout` and no increment of the other three
retirement categories.

- [ ] **Step 5: Add fixed-schema privacy as `OBS-010`**

Run a parameterized query whose SQL comment, parameter and application name
each contain a unique sentinel. Inspect only:

```python
stats
repr(stats)
tuple(stats)
tuple(stats.values())
```

Check the sentinels plus configured host/database/login/password are absent.
For real credentials, avoid pytest assertion rewriting that could echo a
secret:

```python
if sql_auth_config.owner_password in repr(stats):
    raise AssertionError("credential leaked into pool statistics")
```

Values must remain only exact `bool`, `int`, `float` or `None`; keys must equal
the canonical fixed set. Final evidence scanning in Task 10 proves generated
artifacts do not contain the explicit sentinels.

---

### Task 5: Add the RED 10,000-operation concurrent-scrape load case

**Files:**

- Modify: `tests/sql_auth_strict/test_resilience_load.py`
- Modify: `tests/sql_auth_strict/conftest.py`

**Produces:** `OBS-009`.

- [ ] **Step 1: Permit exactly one observability ID in load evidence**

The existing recorder intentionally rejects non-`LOAD-*` keys. Preserve that
boundary while allowing this one approved cross-category load case:

```python
if not (case_id.startswith("LOAD-") or case_id == "OBS-009"):
    raise ValueError(f"not a load case ID: {case_id}")
```

Do not permit a general `OBS-*` prefix and do not invent `LOAD-010`; the
strict matrix remains 321 IDs.

- [ ] **Step 2: Add a load-only case with bounded task count**

Add:

```python
@case("OBS-009")
@pytest.mark.load
@pytest.mark.asyncio
@pytest.mark.timeout(90)
async def test_pool_metrics_remain_consistent_during_ten_thousand_queries(
    sql_auth_config: SqlAuthConfig,
    sa_connection: Connection,
    unique_sql_name: Callable[[str], str],
    record_load_metric,
) -> None:
    operation_count = 10_000
    worker_count = 100
    max_size = 20
```

Use 100 workers, each executing its deterministic slice sequentially:

```python
async def worker(worker_id: int) -> list[int]:
    values = []
    for value in range(worker_id, operation_count, worker_count):
        values.append(
            await scalar(connection, "SELECT @P1 AS value", [value])
        )
    return values
```

This bounds Python task count at 100 rather than allocating 10,000 concurrent
tasks.

- [ ] **Step 3: Scrape and tick concurrently**

Start two bounded background tasks:

```python
async def scrape() -> tuple[int, int]:
    samples = 0
    max_pending = 0
    while not stop.is_set():
        stats = await connection.pool_stats()
        assert_pool_metric_invariants(stats)
        samples += 1
        max_pending = max(max_pending, stats["pending_gets"])
        await asyncio.sleep(0)
    return samples, max_pending

async def tick() -> int:
    ticks = 0
    while not stop.is_set():
        ticks += 1
        await asyncio.sleep(0)
    return ticks
```

Await all workers with a 75-second bound. In `finally`, set the stop event and
await both observers so assertion failures cannot be lost.

- [ ] **Step 4: Assert exact accounting and health**

Flatten and sort worker results, then assert:

```python
assert values == list(range(operation_count))
assert final["get_started"] - before["get_started"] == operation_count
assert (
    final["get_direct"] - before["get_direct"]
    + final["get_waited"] - before["get_waited"]
) == operation_count
assert final["get_timed_out"] == before["get_timed_out"]
assert final["pending_gets"] == 0
assert final["connections"] <= max_size
assert final["active_connections"] == 0
assert samples > 0
assert ticks > 10
assert await scalar(connection, "SELECT 321") == 321
```

Record elapsed time, queries/second, samples, ticks, maximum pending, physical
connections and counter deltas under metric ID `OBS-009`.

Disconnect in `finally`, poll `sys.dm_exec_sessions` by unique application
name, and require zero leaked sessions.

- [ ] **Step 5: Complete source-registry coverage**

Run:

```bash
uv run pytest tests/sql_auth_strict/test_matrix_contract.py -q
```

Expected: exactly 321 unique specification IDs and exactly one source
registration for every ID. Parameterized nodes may produce multiple runtime
results for an existing ID, but no duplicate source decorator is added.

---

### Task 6: Prove RED, self-review tests and create the GREEN branch

**Files:** all RED files from Tasks 2–5.

**Produces:** pushed `test/observability-metrics` whose failures are caused only
by the missing adapter keys, followed by isolated `feat/observability-metrics`.

- [ ] **Step 1: Build the unchanged baseline extension**

Run:

```bash
uv sync --locked --all-extras --dev
uv run maturin develop --release
```

- [ ] **Step 2: Prove the no-network contract is RED**

Run:

```bash
uv run pytest tests/test_pool_observability_contract.py -q
```

Expected: failure shows the eleven declared keys are absent. It must not fail
because of import, syntax, plugin or environment setup.

- [ ] **Step 3: Prove focused real-SQL behavior is RED**

Start/provision only the approved container, then run:

```bash
set -a
source .env.sql-auth.local
set +a
docker compose --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml up -d sqlserver
scripts/sql_auth/provision.sh
uv run pytest \
  tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_pool.py \
  tests/sql_auth_strict/test_pool_observability.py \
  tests/sql_auth_strict/test_resilience_load.py \
  -m "not resilience" -vv
```

Expected:

- existing non-observability cases remain green;
- OBS assertions fail on missing keys;
- no OBS case passes because an unrelated SQL operation failed;
- cleanup produces no hidden error or leaked session.

- [ ] **Step 4: Test self-review**

Inspect the staged diff for:

```text
exact 17-key equality, never subset-only
all 10 OBS IDs present exactly once in source
no skipped/xfail tests
server-visible gates before cancellation/saturation
bounded monotonic polling
timeout metadata still asserted
all exception tasks awaited
all connections disconnected in finally
no credential in failure text
10,000 operations but only 100 worker tasks
existing POOL/CONN assertions preserved
hosted wheel contract updated on all OSes
```

Run:

```bash
git diff --check
git diff --stat
git status --short
```

- [ ] **Step 5: Commit RED tests in reviewable slices**

Run:

```bash
git add \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  tests/test_pool_observability_contract.py \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/test_pyo3_build_contract.py \
  scripts/sql_auth/run_all.sh \
  .github/workflows/rust-unit-tests.yml
git commit -m "test: define pool observability contract"

git add \
  tests/sql_auth_strict/conftest.py \
  tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_pool.py \
  tests/sql_auth_strict/test_pool_observability.py \
  tests/sql_auth_strict/test_resilience_load.py
git commit -m "test: reproduce missing pool observability metrics"

git push -u origin test/observability-metrics
```

- [ ] **Step 6: Create the GREEN worktree from the exact RED head**

From the main repository:

```bash
git worktree add .worktrees/feat-observability-metrics \
  -b feat/observability-metrics test/observability-metrics
```

Link and verify the ignored SQL-auth environment exactly as in Task 1.

---

### Task 7: Implement the smallest safe Rust adapter

**Files:**

- Modify: `src/connection.rs`

**Produces:** eleven new dictionary entries with no SQL hot-path changes.

- [ ] **Step 1: Run graph-first impact analysis**

Run:

```bash
uvx code-review-graph build
uvx code-review-graph status
```

Inspect callers and tests for `PyConnection::pool_stats`,
`ConnectionPool::state`, `disconnect`, lazy reconnect and Python wrapper
forwarding. Confirm the method is not used for SQL admission and no other
dictionary constructor exists.

- [ ] **Step 2: Add and test the pure checkout reconciliation helper**

Add near the top of `src/connection.rs`:

```rust
fn reconcile_checkout_counts(
    get_started: u64,
    get_direct: u64,
    get_waited: u64,
    get_timed_out: u64,
) -> (u64, u64) {
    let completed = get_direct
        .saturating_add(get_waited)
        .saturating_add(get_timed_out);
    let reconciled_started = get_started.max(completed);
    (reconciled_started, reconciled_started - completed)
}
```

Add local Rust unit tests:

```rust
#[test]
fn checkout_counts_preserve_normal_pending_work() {
    assert_eq!(reconcile_checkout_counts(10, 4, 3, 1), (10, 2));
}

#[test]
fn checkout_counts_reconcile_transient_relaxed_load_order() {
    assert_eq!(reconcile_checkout_counts(2, 2, 1, 0), (3, 0));
}

#[test]
fn checkout_counts_do_not_overflow_completed_sum() {
    assert_eq!(
        reconcile_checkout_counts(u64::MAX, u64::MAX, 1, 1),
        (u64::MAX, 0)
    );
}
```

Run the focused Rust test before changing `pool_stats()`. It should pass
because this helper itself is new behavior used to make GREEN safe.

- [ ] **Step 3: Add a private scalar snapshot**

Add:

```rust
#[derive(Debug, Default)]
struct PoolStatisticsSnapshot {
    connected: bool,
    connections: u32,
    idle_connections: u32,
    get_started: u64,
    get_direct: u64,
    get_waited: u64,
    get_timed_out: u64,
    pending_gets: u64,
    get_wait_time_seconds: f64,
    connections_created: u64,
    connections_closed_broken: u64,
    connections_closed_invalid: u64,
    connections_closed_max_lifetime: u64,
    connections_closed_idle_timeout: u64,
}

impl PoolStatisticsSnapshot {
    fn from_state(state: bb8::State) -> Self {
        let statistics = state.statistics;
        let (get_started, pending_gets) = reconcile_checkout_counts(
            statistics.get_started,
            statistics.get_direct,
            statistics.get_waited,
            statistics.get_timed_out,
        );
        Self {
            connected: true,
            connections: state.connections,
            idle_connections: state.idle_connections,
            get_started,
            get_direct: statistics.get_direct,
            get_waited: statistics.get_waited,
            get_timed_out: statistics.get_timed_out,
            pending_gets,
            get_wait_time_seconds: statistics.get_wait_time.as_secs_f64(),
            connections_created: statistics.connections_created,
            connections_closed_broken:
                statistics.connections_closed_broken,
            connections_closed_invalid:
                statistics.connections_closed_invalid,
            connections_closed_max_lifetime:
                statistics.connections_closed_max_lifetime,
            connections_closed_idle_timeout:
                statistics.connections_closed_idle_timeout,
        }
    }
}
```

`Default` is the precise disconnected zero epoch. Do not store SQL/config
identifiers or pool handles in the snapshot.

- [ ] **Step 4: Replace the three-value tuple with one snapshot**

Inside `pool_stats()`:

```rust
let snapshot = {
    let pool_guard = pool.read().await;
    pool_guard
        .as_ref()
        .map(|pool_ref| PoolStatisticsSnapshot::from_state(pool_ref.state()))
        .unwrap_or_default()
};
```

Build all seventeen dictionary entries from `snapshot`. Preserve:

```rust
"active_connections" =
    snapshot.connections.saturating_sub(snapshot.idle_connections)
```

Add the eleven new keys in canonical order. Use only the already captured
scalar snapshot after releasing the Tokio read guard.

- [ ] **Step 5: Run focused Rust quality gates**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
```

Expected: all inherited and three new Rust tests pass with zero warnings.

---

### Task 8: Publish the Python/docs contract and turn RED to GREEN

**Files:**

- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `python/fastmssql/__init__.pyi`
- Modify: `README.md`

- [ ] **Step 1: Update both stub layers**

Change both return annotations to:

```python
Coroutine[Any, Any, Dict[str, int | float | bool | None]]
```

List all seventeen keys and their exact types. State that wait time is
cumulative seconds and counters reset with a new pool epoch.

- [ ] **Step 2: Update the wrapper docstring**

Keep forwarding behavior unchanged. Document all gauges/counters and state
that no SQL or implicit connect occurs.

- [ ] **Step 3: Add the README operational contract**

Extend the quick-start example to show at least:

```python
stats["pending_gets"]
stats["get_wait_time_seconds"]
stats["connections_closed_broken"]
```

Add concise prose for:

- current-pool epoch/reset semantics;
- arithmetic invariant;
- cumulative, not average, wait time;
- retirement-event categories, including their documented overlap;
- pull-only behavior and privacy;
- direct-socket exclusions.

- [ ] **Step 4: Build and prove the no-network contract GREEN**

Run:

```bash
uv run maturin develop --release
uv run pytest tests/test_pool_observability_contract.py -q
uv run pytest tests/test_pyo3_build_contract.py -q
```

Expected: exact schema, types, zero epoch, stubs, wrapper, README and hosted
command all pass.

- [ ] **Step 5: Run focused real-SQL GREEN**

Run:

```bash
uv run pytest \
  tests/sql_auth_strict/test_connection.py \
  tests/sql_auth_strict/test_pool_observability.py -vv
uv run pytest \
  tests/sql_auth_strict/test_pool.py \
  -k \
  "resources_return_after_task_cancellation or idle_timeout_retires_connection or max_lifetime_retires_connection" \
  -vv
uv run pytest \
  tests/sql_auth_strict/test_resilience_load.py \
  -k pool_metrics_remain_consistent_during_ten_thousand_queries \
  -vv
```

Expected: `OBS-001..010` pass, including exact retirement categories,
concurrent invariant checks and zero leaked sessions.

- [ ] **Step 6: Commit the minimal GREEN change**

Run:

```bash
git diff --check
git add \
  src/connection.rs \
  python/fastmssql/__init__.py \
  python/fastmssql/fastmssql.pyi \
  python/fastmssql/__init__.pyi \
  README.md
git commit -m "feat: expose pool observability metrics"
git push -u origin feat/observability-metrics
```

Tests remain in the inherited RED commits and are not amended into GREEN.

---

### Task 9: Perform the complete local enterprise verification

**Files produced by commands:**

- `.artifacts/sql-auth/*` — ignored raw evidence.
- `docs/SQL_AUTH_TEST_MATRIX.md` and `docs/SQL_AUTH_TEST_REPORT.md` — generated
  locally but not committed on the feature branch.

- [ ] **Step 1: Run source-quality and security gates**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
uv run ruff check .
uv run python -m compileall -q python tests
scripts/security/audit_dependencies.sh
```

Expected: zero failures, warnings or denied dependency findings.

- [ ] **Step 2: Run the complete SQL-auth harness**

Run:

```bash
scripts/sql_auth/run_all.sh
```

Expected every required lane passes:

```text
strict: 321/321 registered cases with complete evidence
async true-async
FastAPI/native ASGI
Flask async under WSGI
Flask via persistent WsgiToAsgi loop
resilience
load including OBS-009
applicable upstream regression
report completeness
```

- [ ] **Step 3: Run all approved transaction stress profiles**

Run:

```bash
FASTMSSQL_TRANSACTION_STRESS_PROFILES="10_000:100,99_999:100,99_999:200" \
FASTMSSQL_TRANSACTION_STRESS_STRATEGY="persistent" \
FASTMSSQL_TRANSACTION_STRESS_POOL_SIZE="100" \
scripts/sql_auth/run_transaction_stress.sh
```

Expected: each profile has exact commit/rollback totals, bounded physical
sessions, event-loop progress, zero failed transactions, zero leaked sessions
and a healthy post-run query. Record throughput as driver evidence, not as a
universal SQL Server benchmark.

- [ ] **Step 4: Build and verify an isolated ABI3 wheel**

Run:

```bash
uv run maturin build --release --out dist
wheel_path="$(find dist -maxdepth 1 -name 'fastmssql-*.whl' -print -quit)"
test -n "${wheel_path}"
wheel_root="$(mktemp -d)"
python3 -m venv "${wheel_root}/venv"
"${wheel_root}/venv/bin/pip" install pytest==9.1.1 "${wheel_path}"
"${wheel_root}/venv/bin/python" -m pytest --noconftest \
  tests/test_pool_config_default_contract.py \
  tests/test_timeout_config_contract.py \
  tests/test_lifecycle_contract.py \
  tests/test_pool_observability_contract.py -q
```

- [ ] **Step 5: Scan evidence for privacy sentinels without printing secrets**

Use a fixed non-credential sentinel introduced by `OBS-010`:

```bash
if rg -l "FASTMSSQL_OBS_PRIVACY_SENTINEL_2026" \
  .artifacts/sql-auth docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md; then
  echo "privacy sentinel leaked into evidence" >&2
  exit 1
fi
```

Use the existing redaction contract for environment credentials. Never put an
actual password on a command line or in expected output.

- [ ] **Step 6: Rebuild graph and self-review the implementation**

Run:

```bash
uvx code-review-graph build
uvx code-review-graph status
git diff --check docs/observability-metrics-design...HEAD
git status --short --branch
git log --oneline --decorate docs/observability-metrics-design..HEAD
```

Review:

```text
no SQL hot-path instrumentation
no Python callback/exporter/global provider
no pool lock held while attaching Python
no call to bb8 Statistics::pending_gets()
no unchecked checkout-counter subtraction
exact 17-key schema in Rust/wrapper/stubs/docs/tests
disconnected/reconnected zero epoch
all retirement events map exactly to bb8 fields and are not treated as an
exclusive partition
no direct-socket misreporting
no labels, SQL, parameters or identifiers
no test weakening/skip/xfail
no dependency or lockfile change
no upstream write target
```

- [ ] **Step 7: Restore only generated feature-worktree reports**

The full runner changes the two tracked generated reports. After recording the
successful results and hashes, restore only those two files to the feature
HEAD so the technical branch remains implementation-only:

```bash
git restore --source=HEAD -- \
  docs/SQL_AUTH_TEST_MATRIX.md docs/SQL_AUTH_TEST_REPORT.md
git status --short --branch
```

This is safe only in the isolated feature worktree after confirming those are
the sole uncommitted tracked changes created by the runner. If any unrelated
change exists, stop and preserve it.

---

### Task 10: Run hosted Linux/macOS/Windows and security gates

**Produces:** hosted runs tied to the exact feature SHA.

- [ ] **Step 1: Push the final feature SHA**

Run:

```bash
git push origin feat/observability-metrics
feature_sha="$(git rev-parse HEAD)"
git ls-remote origin refs/heads/feat/observability-metrics
```

The remote SHA must equal `feature_sha`.

- [ ] **Step 2: Dispatch hosted Rust/wheel contracts**

Run:

```bash
gh workflow run rust-unit-tests.yml \
  --repo galeamarcel/FastMssql \
  --ref feat/observability-metrics
```

Wait for the run and inspect every job:

```bash
gh run list \
  --repo galeamarcel/FastMssql \
  --workflow rust-unit-tests.yml \
  --branch feat/observability-metrics \
  --limit 1
rust_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow rust-unit-tests.yml \
    --branch feat/observability-metrics \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
test -n "${rust_run_id}"
gh run watch "${rust_run_id}" \
  --repo galeamarcel/FastMssql --exit-status
gh run view "${rust_run_id}" --repo galeamarcel/FastMssql
```

Expected on Ubuntu, macOS and Windows:

- raw Cargo build;
- all Rust tests;
- ABI3 wheel build/install;
- all four installed Python contract files, including exact pool metrics.

- [ ] **Step 3: Dispatch dependency security**

Run:

```bash
gh workflow run dependency-security.yml \
  --repo galeamarcel/FastMssql \
  --ref feat/observability-metrics
security_run_id="$(
  gh run list \
    --repo galeamarcel/FastMssql \
    --workflow dependency-security.yml \
    --branch feat/observability-metrics \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId'
)"
test -n "${security_run_id}"
gh run watch "${security_run_id}" \
  --repo galeamarcel/FastMssql --exit-status
```

Require zero vulnerabilities and zero warnings under the workflow's strict
policy.

- [ ] **Step 4: Confirm hosted SHA and repository boundary**

Verify each run's `headSha` equals `feature_sha`. Then:

```bash
git remote get-url --push origin
git remote get-url --push upstream
gh pr list \
  --repo Rivendael/FastMssql \
  --head galeamarcel:feat/observability-metrics \
  --state all \
  --json number,url,state
```

Expected:

```text
origin push: galeamarcel/FastMssql
upstream push: DISABLED
upstream PR list: []
```

---

### Task 11: Integrate the exact technical candidate and reverify it

**Produces:** one exact technical merge in `test/sql-auth-validation`, verified
from a detached clean worktree before any status prose is added.

- [ ] **Step 1: Record exact technical ancestry**

Record:

```bash
design_sha="$(git rev-parse origin/docs/observability-metrics-design)"
red_sha="$(git rev-parse origin/test/observability-metrics)"
feature_sha="$(git rev-parse origin/feat/observability-metrics)"
git merge-base --is-ancestor "${design_sha}" "${red_sha}"
git merge-base --is-ancestor "${red_sha}" "${feature_sha}"
```

- [ ] **Step 2: Merge only into the cumulative fork branch**

In the clean main/cumulative worktree:

```bash
git switch test/sql-auth-validation
git pull --ff-only origin test/sql-auth-validation
git merge --no-ff feat/observability-metrics \
  -m "merge: add pool observability metrics"
technical_merge_sha="$(git rev-parse HEAD)"
git push origin test/sql-auth-validation
```

Do not merge or push anything to `upstream/master`.

- [ ] **Step 3: Create a detached exact-verification worktree**

Run:

```bash
git worktree add --detach \
  .worktrees/verify-observability-metrics "${technical_merge_sha}"
```

Link the ignored SQL-auth environment and verify:

```bash
git status --short
git rev-parse HEAD
```

- [ ] **Step 4: Re-run the exact-merge gates**

At minimum run:

```bash
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test --locked
uv run maturin develop --release
uv run pytest tests/test_pool_observability_contract.py -q
scripts/sql_auth/run_all.sh
```

Require the same 321-case complete matrix and every regression lane. The
reports generated here are the authoritative inputs for the status branch.

- [ ] **Step 5: Re-run exact-merge stress**

Run all three approved stress profiles again if any difference exists between
feature and cumulative ancestry beyond a clean merge. Otherwise verify the
technical merge contains no additional source change with:

```bash
git diff --exit-code "${feature_sha}" "${technical_merge_sha}" -- \
  src python tests scripts .github README.md Cargo.toml Cargo.lock pyproject.toml
```

Even when the tree is identical, the exact-merge SQL-auth harness in Step 4 is
mandatory.

---

### Task 12: Record live audit status and finish the cumulative fork

**Files:**

- Regenerate: `docs/SQL_AUTH_TEST_MATRIX.md`
- Regenerate: `docs/SQL_AUTH_TEST_REPORT.md`
- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`

**Produces:** pushed `docs/observability-metrics-status`, documentation-only
merge into `test/sql-auth-validation`, fork parity and explicit remaining
limitations.

- [ ] **Step 1: Create the status branch from the exact technical merge**

Run:

```bash
git worktree add .worktrees/docs-observability-metrics-status \
  -b docs/observability-metrics-status "${technical_merge_sha}"
```

Copy no raw artifacts or secrets. Generate matrix/report in this worktree by
running the exact harness or by using the exact-verification artifact set only
after verifying its SHA metadata.

- [ ] **Step 2: Update the live production-readiness audit**

Record:

- design, RED, feature and exact technical merge SHAs;
- exact local Rust, contract, 321-case SQL-auth and upstream counts;
- `OBS-001..010` results;
- 10,000 concurrent-scrape throughput, sample count, event-loop ticks, maximum
  pending and session-leak result;
- all three transaction stress profiles;
- ABI3 installed-wheel result;
- hosted Linux/macOS/Windows run IDs and exact SHA;
- dependency-security run ID/result;
- current scope limitations:
  current-pool epoch only, no direct-socket stats, no operation
  duration/outcome metrics, no tracing/exporter, hosted CI has no real MSSQL;
- next independent candidate: operation-duration/outcome observability.

Use `VERIFIED_FORK`, not an upstream-release or universal performance claim.

- [ ] **Step 3: Add the future upstream-sized candidate**

Add a roadmap entry containing:

```text
Pool observability metrics
dependency-free
existing pool_stats additive schema migration
bb8 counters only
RED and real SQL-auth evidence
Linux/macOS/Windows installed-wheel evidence
privacy and performance boundaries
no upstream PR opened
```

Do not combine it with future operation histograms or OpenTelemetry.

- [ ] **Step 4: Self-review status and generated evidence**

Run:

```bash
git diff --check
rg -n "NOT RUN|FAIL|ERROR|missing evidence" \
  docs/SQL_AUTH_TEST_MATRIX.md docs/SQL_AUTH_TEST_REPORT.md
rg -n "321|OBS-001|OBS-010|current pool|OpenTelemetry|hosted" \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
```

Expected: no failed/missing row, no inflated hosted-MSSQL claim and no secret
or sentinel.

- [ ] **Step 5: Commit and push status only to the fork**

Run:

```bash
git add \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git commit -m "docs: record verified pool observability metrics"
git push -u origin docs/observability-metrics-status
```

- [ ] **Step 6: Merge the documentation status into cumulative**

In the clean cumulative worktree:

```bash
git switch test/sql-auth-validation
git merge --no-ff docs/observability-metrics-status \
  -m "merge: record pool observability verification"
git push origin test/sql-auth-validation
```

- [ ] **Step 7: Final fork/upstream safety and parity audit**

Run:

```bash
git status --short --branch
git rev-list --left-right --count \
  test/sql-auth-validation...origin/test/sql-auth-validation
git remote get-url --push origin
git remote get-url --push upstream
git ls-remote upstream refs/heads/master
gh pr list \
  --repo Rivendael/FastMssql \
  --head galeamarcel:feat/observability-metrics \
  --state all \
  --json number,url,state
test ! -e VERSION.md
```

Expected:

```text
clean cumulative branch
fork parity 0 0
origin is the user fork
upstream push is DISABLED
upstream master remains read-only/unmodified by this work
no upstream PR
no VERSION.md exists
```

---

## Final self-review record required at execution time

Before declaring this candidate verified, append measured facts to the live
audit and confirm all of the following:

1. The schema has exactly seventeen stable keys in Rust, wrapper, both stubs,
   README, no-network contract and SQL-auth tests.
2. Existing six-key values and assertions remain semantically unchanged.
3. The adapter uses bb8's already-maintained statistics; it adds no
   driver-owned counter and no normal-operation overhead.
4. Checkout reconciliation cannot underflow or overflow while reading
   independent relaxed atomics.
5. `pending_gets` is observed while a server-gated waiter is actually live.
6. Acquire timeout increments exactly once and preserves typed error metadata.
7. Broken, invalid, max-lifetime and idle-timeout retirements increment only
   their correct reason categories.
8. Ten thousand parameterized operations run through a bounded 100-task/20
   connection topology while scrape invariants are continuously checked.
9. The event loop advances, post-load SQL succeeds and no application session
   leaks.
10. Disconnection and reconnection visibly reset the metric epoch.
11. SQL, parameters, identifiers, credentials and privacy sentinels are absent
    from the snapshot and generated evidence.
12. Both 99,999-transaction profiles remain green after the adapter change.
13. Raw Cargo, Rust tests, ABI3 wheel install and all contracts pass on hosted
    Linux, macOS and Windows at the exact feature SHA.
14. Real SQL-auth is correctly described as local Docker evidence, not hosted
    MSSQL evidence.
15. No dependency, lockfile, package version, release, upstream push or
    upstream PR was introduced.

Only after all fifteen checks pass may the audit mark the candidate
`VERIFIED_FORK`.
