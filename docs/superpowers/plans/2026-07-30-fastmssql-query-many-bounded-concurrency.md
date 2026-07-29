# FastMssql Bounded-Concurrency `query_many()` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lazy, fixed-worker, pool-bounded `Connection.query_many()`
async iterator with deterministic ordering, backpressure, failure propagation
and terminal cleanup over the existing independently observed raw query path.

**Architecture:** A public Python `QueryManyIterator` owns one producer task,
a fixed worker set and one shared capacity-token window bounded by
`min(concurrency, pool.max_size)`. Each worker calls the existing raw
`Connection.query()` so Rust remains the sole owner of SQL lifecycle,
timeouts, typed conversion, metrics and physical-connection disposition.

**Tech Stack:** Python 3.11+, `asyncio`, the existing PyO3/Rust extension,
bb8/Tiberius, pytest/pytest-asyncio, Docker SQL Server 2022 with SQL
authentication, Ruff, maturin ABI3 wheels and code-review-graph.

## Global Constraints

- Work only in `galeamarcel/FastMssql`; keep
  `Rivendael/FastMssql` fetch-only with push URL exactly `DISABLED`.
- Start RED work from the exact committed plan on `docs/query-many-design`.
- Preserve real RED ancestry:
  `test/query-many-bounded-concurrency` must be an ancestor of
  `feat/query-many-bounded-concurrency`.
- Update `VERSION.md` in every repository-modifying commit without changing
  package metadata or displayed version `0.7.7`.
- Use `apply_patch` for text edits and preserve unrelated user changes.
- Add no dependency and no new aggregate operation metric.
- Keep operation-metrics schema exactly 2; every started child remains one
  existing `query` operation.
- Add no public or raw `Transaction.query_many()` and no raw PyO3
  `Connection.query_many()`.
- Validate `concurrency` as an integer other than `bool` in `1..=10_000` and
  `ordered` as exactly `bool`.
- Accept only concrete lists, sync iterables or async iterables of positional
  Python lists or `Parameters`; preserve the 2,098-user-parameter limit.
- Prefer the async protocol exactly once when a producer advertises both.
- Never materialize the complete source, create one task per input item, use
  an unbounded queue or retain more than the effective concurrency window.
- A capacity token is acquired before producer advancement and is released
  only after yield or terminal discard.
- Preserve underlying exceptions and tracebacks. Add `query_index` only to a
  producer, parameter-set or child-query failure, never to consumer
  cancellation or explicit close.
- Await terminal cleanup on exhaustion, failure, consumer cancellation,
  explicit `aclose()` and async-context exit. Treat drop cleanup as
  best-effort only.
- Use the Docker SQL-auth database for every SQL behavior test; add no SQLite
  persistence substitute.
- Required stress covers 1,000 and 10,000 operations. The explicitly approved
  extended gate covers 99,999 operations.
- Do not publish a release, wheel, Tiberius fork or original-repository PR.

---

## File responsibility map

### New runtime files

- `python/fastmssql/_parameter_sets.py` — one shared positional
  parameter-set shape/count validator used by execute-many and query-many.
- `python/fastmssql/_query_many.py` — producer cursor, capacity token,
  work/completion records, private `_QueryManyState` fixed-worker supervisor
  and public `QueryManyIterator` facade.

### Modified runtime and typing files

- `python/fastmssql/_execute_many.py` — import shared validation without
  changing execute-many behavior.
- `python/fastmssql/__init__.py` — export `QueryManyIterator` and add the
  regular `Connection.query_many()` factory method.
- `python/fastmssql/__init__.pyi` — publish exact iterator and method typing.
- `python/fastmssql/fastmssql.pyi` — deliberately retain the absence of raw
  query-many surfaces; static tests guard this.

### New focused tests

- `tests/test_query_many_contract.py` — AST/public surface, validation,
  source protocol and no-raw/no-Transaction contracts.
- `tests/test_query_many_coordinator.py` — deterministic fake-query barriers
  for fixed workers, ordering, one-window backpressure and cleanup.
- `tests/test_query_many_stress_contract.py` — static and unit contract for
  bounded stress CLI, hard gates, artifact privacy and cleanup.
- `tests/sql_auth_strict/test_query_many_strict.py` — `QMANY-001` through
  `QMANY-012` on real SQL Server.
- `tests/sql_auth_strict/test_resilience_load.py` — `QMANY-013` required
  1,000-operation load case in the canonical load lane.
- `tests/sql_auth_strict/conftest.py` — admit `QMANY-013` into the canonical
  load-metric recorder without broadening unrelated case namespaces.

### New and modified validation infrastructure

- `scripts/sql_auth/query_many_stress.py` — sync/async producer stress without
  harness task fan-out.
- `scripts/sql_auth/run_query_many_stress.sh` — required profiles and
  explicit extended-profile switch.
- `scripts/sql_auth/run_all.sh` — required query-many stress lane and focused
  strict test registration.
- `scripts/sql_auth/generate_report.py` — privacy-safe structured
  query-many-stress summary.
- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
  — canonical `QMANY-001`–`QMANY-013` requirements.
- `tests/sql_auth_strict/test_matrix_contract.py` — case total 407→420 and
  runner/stress wiring assertions.
- `.github/workflows/rust-unit-tests.yml` — installed-wheel offline
  query-many contracts on Linux, macOS and Windows.
- `README.md` — truthful usage and early-exit guidance.
- `VERSION.md` — live change history for every commit.

---

### Task 1: Commit the absent public API as RED

**Branch:** `test/query-many-bounded-concurrency`

**Files:**

- Create: `tests/test_query_many_contract.py`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: current wrapper `fastmssql.Connection`, public wrapper stub and
  raw extension stub.
- Produces: exact required names and signatures for
  `QueryManyIterator` and `Connection.query_many()`.

- [ ] **Step 1: verify linked-worktree and fork boundaries**

Run:

```bash
git status --short --branch
git rev-parse --git-dir
git rev-parse --git-common-dir
git remote -v
git remote get-url --push upstream
```

Expected: current branch is `docs/query-many-design`, worktree is linked,
status is clean, `origin` is `galeamarcel/FastMssql`, and upstream push is
`DISABLED`.

- [ ] **Step 2: create the RED branch from the exact plan commit**

Run:

```bash
git switch -c test/query-many-bounded-concurrency
```

Record the exact parent SHA in the new `VERSION.md` RED entry.

- [ ] **Step 3: write the static public-surface RED**

Add AST helpers matching the style in
`tests/test_execute_many_contract.py`. Require:

```python
QUERY_MANY_POSITIONAL = ("self", "sql", "parameter_sets")
QUERY_MANY_KEYWORD_ONLY = ("concurrency", "ordered")

def test_wrapper_declares_regular_query_many_factory() -> None:
    tree = _tree(WRAPPER)
    method = _method(
        _class(tree, "Connection"),
        "query_many",
        asynchronous=False,
    )
    assert tuple(arg.arg for arg in method.args.args) == QUERY_MANY_POSITIONAL
    assert tuple(arg.arg for arg in method.args.kwonlyargs) == (
        QUERY_MANY_KEYWORD_ONLY
    )

def test_transaction_and_raw_stubs_do_not_publish_query_many() -> None:
    wrapper_tree = _tree(WRAPPER_STUB)
    raw_tree = _tree(RAW_STUB)
    assert "query_many" not in _method_names(_class(wrapper_tree, "Transaction"))
    assert "query_many" not in _method_names(_class(raw_tree, "Transaction"))
    assert "query_many" not in _method_names(_class(raw_tree, "Connection"))
```

Require the wrapper stub to define:

```python
class QueryManyIterator(AsyncIterator[QueryStream]):
    def __aiter__(self) -> QueryManyIterator: ...
    async def __anext__(self) -> QueryStream: ...
    async def aclose(self) -> None: ...
    async def __aenter__(self) -> QueryManyIterator: ...
    async def __aexit__(
        self,
        exc_type: Any,
        exc: BaseException | None,
        traceback: Any,
    ) -> bool: ...
```

and:

```python
def query_many(
    self,
    sql: str,
    parameter_sets: ParameterSetSource,
    *,
    concurrency: int = 10,
    ordered: bool = True,
) -> QueryManyIterator: ...
```

The ellipsis tokens in these two blocks are the literal body syntax used by
the repository's `.pyi` files; they do not denote omitted plan work.

- [ ] **Step 4: write runtime validation RED cases**

Use a fake raw owner whose `pool_stats()` and `query()` raise
`AssertionError` if called. Assert the factory rejects, synchronously:

```python
INVALID_CONCURRENCY = (True, False, 0, -1, 10_001, 1.5, "2", None)
INVALID_ORDERED = (0, 1, "yes", None)
INVALID_SOURCES = ("rows", b"rows", bytearray(b"rows"), memoryview(b"rows"), {})
```

Also require:

```python
def test_invalid_arguments_do_not_acquire_or_pull_producer() -> None:
    source = CountingSyncSource([[1]])
    with pytest.raises((TypeError, ValueError)):
        connection.query_many("SELECT @P1", source, concurrency=True)
    assert source.iter_calls == 0
    assert source.pull_calls == 0
    assert raw.pool_stats_calls == 0
    assert raw.query_calls == []
```

Test that a dual-protocol source acquires only `__aiter__()` once, that a
concrete list is retained by identity, and that resizing a captured concrete
list fails at the next pull with `query_index` for that boundary. Add a source
whose `__iter__()` raises a sentinel `BaseException`; require the synchronous
factory call to preserve that same exception object and to perform zero pool
or query work.

- [ ] **Step 5: run the public-surface RED**

Run:

```bash
.venv/bin/pytest tests/test_query_many_contract.py -q
```

Expected: fail only because `QueryManyIterator`,
`Connection.query_many()` and `_query_many.py` do not exist. Existing
execute-many tests must remain collectable.

- [ ] **Step 6: update version history, verify and commit**

Add a `Query-many absent-API RED` entry to `VERSION.md` containing the exact
base SHA, branch, expected failure reason and the statement that no runtime
behavior changed.

Run:

```bash
git diff --check
.venv/bin/ruff check tests/test_query_many_contract.py
git add tests/test_query_many_contract.py VERSION.md
git diff --cached --check
git commit -m "test: require query many public contract"
```

---

### Task 2: Commit deterministic coordinator and cleanup contracts as RED

**Files:**

- Create: `tests/test_query_many_coordinator.py`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: the public factory required by Task 1.
- Produces: observable fixed-worker, capacity-window, ordering and cleanup
  contracts without depending on SQL timing.

- [ ] **Step 1: create controlled fake raw query operations**

Define a fake owner with SQL-free pool stats and explicit per-index gates:

```python
@dataclass
class QueryGate:
    started: asyncio.Event
    release: asyncio.Event
    result: object | None = None
    error: BaseException | None = None

class FakeRawConnection:
    def __init__(self, *, max_size: int, gates: dict[int, QueryGate]) -> None:
        self.max_size = max_size
        self.gates = gates
        self.pool_stats_calls = 0
        self.query_calls: list[int] = []
        self.active_queries = 0
        self.maximum_active_queries = 0

    async def pool_stats(self) -> dict[str, int]:
        self.pool_stats_calls += 1
        return {"max_size": self.max_size}

    async def query(self, sql: str, params: object) -> object:
        index = int(params[0])
        gate = self.gates[index]
        self.query_calls.append(index)
        self.active_queries += 1
        self.maximum_active_queries = max(
            self.maximum_active_queries,
            self.active_queries,
        )
        gate.started.set()
        try:
            await gate.release.wait()
            if gate.error is not None:
                raise gate.error
            return gate.result
        finally:
            self.active_queries -= 1
```

Use opaque sentinel results rather than emulating `QueryStream`; the
coordinator must return raw results unchanged.

- [ ] **Step 2: require one end-to-end capacity window**

With requested concurrency 5, pool max 3 and a slow consumer, require:

```python
assert raw.maximum_active_queries == 3
assert source.maximum_accepted_not_yielded == 3
assert source.pull_calls <= yielded_count + 3 + 1
assert len(
    [
        task
        for task in asyncio.all_tasks()
        if task.get_name().startswith("fastmssql-query-many-worker-")
    ]
) == 3
```

The `+1` is only the normal exhaustion probe after capacity becomes
available; no input item beyond the three-token window may be retained.

- [ ] **Step 3: require ordered and completion-order delivery**

Release gates in order `2, 0, 1`.

```python
ordered = connection.query_many(
    "SELECT @P1",
    [[0], [1], [2]],
    concurrency=3,
    ordered=True,
)
assert await collect(ordered) == [result_0, result_1, result_2]

unordered = connection.query_many(
    "SELECT @P1",
    [[0], [1], [2]],
    concurrency=3,
    ordered=False,
)
assert await collect(unordered) == [result_2, result_0, result_1]
```

Use events to wait until all three calls have started before releasing any
gate; do not use sleeps to infer ordering.

- [ ] **Step 4: require first-error supervision**

Make index 2 fail while indices 0 and 1 remain blocked. Assert:

```python
with pytest.raises(QueryFailure) as caught:
    await iterator.__anext__()
assert caught.value is failure
assert caught.value.query_index == 2
assert raw.active_queries == 0
assert source.close_calls == 1
assert await named_query_many_tasks() == []
```

Add separate producer and parameter-set failures. Producer failure receives
the next assignable `query_index`; a parameter conversion failure preserves
its existing `parameter_index` and adds only the outer `query_index`.
No error text may contain a source or parameter representation.

Make `pool_stats()` first raise a sentinel and then return malformed or
non-positive `max_size` values. These startup failures preserve the original
exception where applicable, carry no fabricated `query_index`, perform zero
source pulls and zero query calls, close an already acquired producer once,
and leave no named task. Assert that the traceback still contains the
original producer/worker/startup origin instead of a replacement wrapper
exception.

Make abnormal producer close raise `CleanupFailure`. Require the original
query/producer failure to remain primary with that cleanup error as
`__cause__`. With no existing primary, require explicit `aclose()` to raise
the cleanup failure. With a body exception inside `async with`, require the
body exception to remain primary and cleanup to be chained.

- [ ] **Step 5: require every deterministic close path**

Cover:

- full exhaustion: producer close count remains zero;
- `await iterator.aclose()` before start and after start: idempotent and one
  abnormal producer close;
- `async with` plus early `break`: all active queries are settled before
  `__aexit__()` returns;
- cancellation of an active `__anext__()`: cleanup completes and the original
  `CancelledError` is re-raised without `query_index`;
- repeated cancellation during cleanup: the same cleanup task reaches
  completion;
- concurrent `__anext__()` calls: the second receives `RuntimeError`;
- second event loop use: `RuntimeError`;
- dropped active iterator: loop exception handler receives no
  “Task exception was never retrieved” record.

Use `weakref`, `gc.collect()`, events and a captured loop exception handler
for the drop case. The deterministic acceptance path remains explicit
`aclose()`/context exit. The async producer records an `anext_active` flag and
asserts from its `aclose()` method that close is never invoked until the
cancelled `anext()` call has quiesced.

Add a fake configured `operation_timeout` shorter than an intentionally
blocked producer pull. Require no library timeout while producer code is
waiting; wrap the canonical `async with` in `asyncio.timeout()` and require
that caller cancellation to drive deterministic cleanup.

- [ ] **Step 6: run and commit the coordinator RED**

Run:

```bash
.venv/bin/pytest tests/test_query_many_coordinator.py -q
```

Expected: fail because the query-many coordinator is absent, while the test
module itself collects cleanly.

Append exact RED counts and expected failure to `VERSION.md`, then run:

```bash
.venv/bin/ruff check tests/test_query_many_coordinator.py
git diff --check
git add tests/test_query_many_coordinator.py VERSION.md
git commit -m "test: require bounded query many coordination"
```

---

### Task 3: Commit SQL-auth, matrix and stress requirements as RED

**Files:**

- Create: `tests/sql_auth_strict/test_query_many_strict.py`
- Create: `tests/test_query_many_stress_contract.py`
- Create: `scripts/sql_auth/query_many_stress.py`
- Create: `scripts/sql_auth/run_query_many_stress.sh`
- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify: `tests/sql_auth_strict/test_resilience_load.py`
- Modify: `tests/sql_auth_strict/conftest.py`
- Modify: `scripts/sql_auth/run_all.sh`
- Modify: `scripts/sql_auth/generate_report.py`
- Modify: `.github/workflows/rust-unit-tests.yml`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: `QMANY-001`–`QMANY-013` from the focused design.
- Produces: one canonical occurrence of each case ID, required stress
  execution and cross-platform installed-wheel contract coverage.

- [ ] **Step 1: add the 13 canonical case descriptions**

Insert a `QMANY — independent pool-bounded queries` section after `EMANY`.
Copy the exact meanings from the focused design, with one backtick occurrence
of each ID `QMANY-001` through `QMANY-013`.

Update all exact matrix-count assertions and fixture strings from 407 to 420:

```python
def test_approved_spec_contains_420_unique_case_ids() -> None:
    ids = spec_case_ids(APPROVED_SPEC)
    assert len(ids) == 420
```

Do not replace unrelated historical 407 evidence in audit/report documents.

- [ ] **Step 2: map `QMANY-001`–`QMANY-012` to real-SQL tests**

Use these exact test ownership groups and fixture signatures so each ID
occurs once:

| Function | Case IDs | Exact fixtures |
|---|---|---|
| `test_query_many_surface_validation_and_empty` | `QMANY-001`, `QMANY-002`, `QMANY-003` | `sql_auth_config`, `sa_connection`, `unique_sql_name` |
| `test_query_many_parameter_sources_and_typed_results` | `QMANY-004` | `owner_connection`, `unique_sql_name`, `cleanup_registry` |
| `test_query_many_pool_window_and_ordering` | `QMANY-005`, `QMANY-006`, `QMANY-007` | `sql_auth_config`, `sa_connection`, `transaction_factory`, `unique_sql_name`, `cleanup_registry` |
| `test_query_many_failure_and_consumer_cleanup` | `QMANY-008`, `QMANY-009` | `sql_auth_config`, `sa_connection`, `transaction_factory`, `unique_sql_name`, `cleanup_registry` |
| `test_query_many_faults_and_lifecycle` | `QMANY-010`, `QMANY-011` | `sql_auth_config`, `sa_connection`, `transaction_factory`, `unique_sql_name`, `cleanup_registry` |
| `test_query_many_metrics_and_security_retirement` | `QMANY-012` | `sql_auth_config`, `sa_connection`, `unique_sql_name` |

These are existing fixtures; do not invent an `observer_connection` fixture.
Every candidate connection created inside a test receives a unique
`application_name`. DMV bounds and teardown assertions use `sa_connection`
to count only sessions for that name, so the observer fixture's own session
cannot be mistaken for a leak.

`QMANY-004` must exercise the repeated query surface, not merely scalar
echoes: cover a parameterized join, a parameterized CTE, an empty result and
an `EXEC <procedure> @P1` invocation in addition to typed list, sync and async
parameter sources. Register every created table and procedure with
`cleanup_registry`.

For deterministic real-SQL completion order, create a three-row gate table.
Open one blocker transaction per row, update its row to hold an X lock, start
three repeated parameterized queries using:

```sql
SELECT gate_id, @@SPID AS spid
FROM <quoted_gate_table> WITH (UPDLOCK, ROWLOCK)
WHERE gate_id = @P1
```

Wait through DMVs until all requests have an `LCK_M_%` wait, then roll back
the blockers in order `2, 0, 1`. Assert input-order delivery for
`ordered=True`, completion-order delivery for `ordered=False`, distinct
concurrent SPIDs where the pool permits, and zero candidate
`application_name` sessions after teardown.

- [ ] **Step 3: map `QMANY-013` to the canonical load lane**

Add one test to `tests/sql_auth_strict/test_resilience_load.py`:

```python
@case("QMANY-013")
@pytest.mark.load
@pytest.mark.asyncio
async def test_query_many_thousand_operation_required_profile(
    sql_auth_config: SqlAuthConfig,
    record_load_metric,
) -> None:
    operation_count = 1_000
    connection = _connection(sql_auth_config, max_size=10, min_idle=10)
    yielded = 0
    try:
        async with connection.query_many(
            "SELECT @P1 AS value, @@SPID AS spid",
            ([value] for value in range(operation_count)),
            concurrency=10,
            ordered=True,
        ) as results:
            async for result in results:
                row = result.fetchone()
                assert row["value"] == yielded
                yielded += 1
        assert yielded == operation_count
        stats = await connection.pool_stats()
        assert stats["active_connections"] == 0
        assert stats["connections"] <= 10
        record_load_metric("QMANY-013", operations=yielded)
    finally:
        await connection.disconnect()
```

Extend `record_load_metric` in `tests/sql_auth_strict/conftest.py` to admit
exactly `QMANY-013` alongside the existing load-owned exceptional IDs.
Do not allow the whole `QMANY-*` namespace because `QMANY-001`–`QMANY-012`
belong to the strict functional lane.

- [ ] **Step 4: create the bounded stress contract before its runtime passes**

Use CLI profiles encoded as:

```text
operations:source:ordered:requested_concurrency:pool_max
```

Defaults:

```text
1_000:sync:true:10:10
1_000:async:false:25:8
10_000:sync:false:50:16
10_000:async:true:100:16
```

Extended profiles:

```text
99_999:sync:true:200:32
99_999:async:false:500:32
```

`tests/test_query_many_stress_contract.py` must reject zero, 100,000,
duplicate profiles, invalid source/order, requested concurrency above 10,000
and pool max below one. It must AST-reject:

- `asyncio.gather()` over an input-sized comprehension;
- `asyncio.create_task()` inside the operation producer;
- `list(parameter_sets)` or `list(results)`;
- unbounded `asyncio.Queue()`;
- `repr(error)` or `str(error)` in the artifact.

Hard-gate mutations must detect each violation:

```text
missing_or_duplicate_ids
ordered_output_mismatch
accepted_window_exceeded
active_query_bound_exceeded
pool_bound_exceeded
query_metric_mismatch
unexpected_query_many_metric
schema_version_mismatch
rss_growth_exceeded
event_loop_gap_exceeded
post_load_smoke_failed
teardown_session_leak
unhandled_task_exception
```

- [ ] **Step 5: wire the required stress lane and hosted wheel contract**

Make the shell runner executable and have it pass SQL-auth environment
variables without printing them. Add before strict tests:

```bash
chmod +x scripts/sql_auth/run_query_many_stress.sh
record query-many-load scripts/sql_auth/run_query_many_stress.sh
```

Add `tests/sql_auth_strict/test_query_many_strict.py` to
`strict_functional`.

Teach `generate_report.py` to render source SHA, status, profile count,
maximum active queries/sessions/window, RSS and event-loop gap from
`query-many-stress.json`, using the existing redaction helper.

Add to the installed-wheel pytest command on all three hosted operating
systems:

```text
tests/test_query_many_contract.py
tests/test_query_many_coordinator.py
tests/test_query_many_stress_contract.py
```

- [ ] **Step 6: prove matrix completeness and the intended runtime RED**

Run:

```bash
.venv/bin/pytest tests/sql_auth_strict/test_matrix_contract.py -q
.venv/bin/pytest tests/test_query_many_stress_contract.py -q
.venv/bin/pytest tests/sql_auth_strict/test_query_many_strict.py -q
```

Expected:

- matrix and stress structural contracts pass;
- SQL-auth tests fail at the absent `Connection.query_many()` surface, not
  through skip, swallowed exception or environment fallback.

- [ ] **Step 7: commit and publish the complete RED branch**

Append exact matrix/structural counts and SQL RED evidence to `VERSION.md`.

Run:

```bash
.venv/bin/ruff check \
  tests/test_query_many_contract.py \
  tests/test_query_many_coordinator.py \
  tests/test_query_many_stress_contract.py \
  tests/sql_auth_strict/test_query_many_strict.py \
  tests/sql_auth_strict/test_resilience_load.py \
  scripts/sql_auth/query_many_stress.py
git diff --check
git add \
  .github/workflows/rust-unit-tests.yml \
  VERSION.md \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  scripts/sql_auth/generate_report.py \
  scripts/sql_auth/query_many_stress.py \
  scripts/sql_auth/run_all.sh \
  scripts/sql_auth/run_query_many_stress.sh \
  tests/sql_auth_strict/conftest.py \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/sql_auth_strict/test_query_many_strict.py \
  tests/sql_auth_strict/test_resilience_load.py \
  tests/test_query_many_stress_contract.py
git commit -m "test: require bounded query many concurrency"
git push -u origin test/query-many-bounded-concurrency
```

Verify with `git merge-base --is-ancestor` that all three RED commits belong
to the public test branch.

---

### Task 4: Extract shared parameter-set validation without semantic drift

**Branch:** `feat/query-many-bounded-concurrency`

**Files:**

- Create: `python/fastmssql/_parameter_sets.py`
- Modify: `python/fastmssql/_execute_many.py`
- Modify: `tests/test_execute_many_coordinator.py`
- Modify: `VERSION.md`

**Interfaces:**

- Produces:

```python
INVALID_PRODUCER_TYPES: tuple[type, ...]
MAX_USER_QUERY_PARAMETERS: int

def preflight_parameter_set(
    parameter_set: object,
    *,
    operation: str,
) -> object: ...
```

The ellipsis in the type tuple and the function body above is literal Python
typing/stub notation. The complete runtime body is fixed in Step 3.

- Consumes: `Parameters` from the raw extension.

- [ ] **Step 1: branch from the complete RED ancestry**

Run:

```bash
git switch -c feat/query-many-bounded-concurrency
git merge-base --is-ancestor \
  test/query-many-bounded-concurrency \
  feat/query-many-bounded-concurrency
```

Expected: ancestry command exits zero.

- [ ] **Step 2: add characterization for exact execute-many messages**

Extend existing execute-many coordinator tests to assert:

```python
with pytest.raises(
    TypeError,
    match="each execute_many parameter set must be a list or Parameters object",
):
    await _run(raw, [(1,)], chunk_size=1)
```

Also pin the named-parameter and 2,098-limit messages before moving code.

- [ ] **Step 3: extract the helper**

Move only the existing constants and validation behavior:

```python
def preflight_parameter_set(
    parameter_set: object,
    *,
    operation: str,
) -> object:
    if isinstance(parameter_set, list):
        count = list.__len__(parameter_set)
    elif isinstance(parameter_set, Parameters):
        count = len(parameter_set)
        if count > MAX_USER_QUERY_PARAMETERS:
            raise parameter_count_error(count)
        if len(parameter_set.named):
            raise ValueError(
                "Named parameters are not supported by the SQL Server wire "
                "protocol. Use positional parameters instead. "
                f"Found {len(parameter_set.named)} named parameter(s)"
            )
    else:
        raise TypeError(
            f"each {operation} parameter set must be a list or Parameters object"
        )
    if count > MAX_USER_QUERY_PARAMETERS:
        raise parameter_count_error(count)
    return parameter_set
```

Update `_execute_many.py` to call:

```python
preflight_item=lambda item: preflight_parameter_set(
    item,
    operation="execute_many",
)
```

- [ ] **Step 4: prove semantic preservation**

Run:

```bash
.venv/bin/pytest \
  tests/test_execute_many_contract.py \
  tests/test_execute_many_coordinator.py \
  tests/sql_auth_strict/test_execute_many_strict.py -q
.venv/bin/ruff check \
  python/fastmssql/_parameter_sets.py \
  python/fastmssql/_execute_many.py \
  tests/test_execute_many_coordinator.py
```

Expected: every execute-many test passes unchanged.

- [ ] **Step 5: update VERSION and commit**

Record the characterization and extraction results, then:

```bash
git add \
  VERSION.md \
  python/fastmssql/_parameter_sets.py \
  python/fastmssql/_execute_many.py \
  tests/test_execute_many_coordinator.py
git diff --cached --check
git commit -m "refactor: share parameter set validation"
```

---

### Task 5: Implement the validated public facade

**Files:**

- Create: `python/fastmssql/_query_many.py`
- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/__init__.pyi`

**Interfaces:**

- Produces:

```python
MAX_QUERY_MANY_CONCURRENCY = 10_000

def create_query_many_iterator(
    raw_connection: object,
    sql: object,
    parameter_sets: object,
    *,
    concurrency: object,
    ordered: object,
) -> QueryManyIterator: ...
```

The ellipsis above is literal interface notation; Steps 1–3 provide the
complete runtime behavior.

- Produces the public `QueryManyIterator` methods fixed by Task 1 and a
  private `_QueryManyState`; background tasks must retain the state but never
  the facade.

- [ ] **Step 1: implement scalar validation and one producer acquisition**

Use explicit type checks:

```python
if not isinstance(sql, str):
    raise TypeError("sql must be a string")
if isinstance(concurrency, bool) or not isinstance(concurrency, int):
    raise TypeError("concurrency must be an integer between 1 and 10000")
if not 1 <= concurrency <= MAX_QUERY_MANY_CONCURRENCY:
    raise ValueError("concurrency must be between 1 and 10,000")
if type(ordered) is not bool:
    raise TypeError("ordered must be exactly bool")
if isinstance(parameter_sets, INVALID_PRODUCER_TYPES):
    raise TypeError(PRODUCER_ERROR)
```

For a concrete list, create `_CapturedListProducer` using
`list.__len__`/`list.__getitem__` and compare the captured length before
every pull. For other sources, call `_acquire_producer()` once, preferring
async.

- [ ] **Step 2: add the regular wrapper factory**

In `Connection`:

```python
def query_many(
    self,
    sql,
    parameter_sets,
    *,
    concurrency=10,
    ordered=True,
):
    return create_query_many_iterator(
        self._conn,
        sql,
        parameter_sets,
        concurrency=concurrency,
        ordered=ordered,
    )
```

Export `QueryManyIterator` from the package. Do not add the method to either
Transaction class or raw stub.

- [ ] **Step 3: implement the facade/state ownership boundary**

`QueryManyIterator` contains one `_QueryManyState` and no background task
captures the facade. The facade implements identity iteration, delegates
`__anext__`, `aclose` and context methods to the state, and its destructor can
only call the state's synchronous drop request.

Create asyncio locks/queues only on first async use. Task 6 supplies the
complete `ensure_started()` and `next_result()` state behavior before this
runtime is committed.

- [ ] **Step 4: run the now-green non-consuming facade subset**

Run:

```bash
.venv/bin/pytest \
  tests/test_query_many_contract.py \
  tests/test_query_many_coordinator.py \
  -k "surface or validation or protocol_acquisition" -q
```

Expected: selected construction and synchronous validation tests pass;
consuming/worker tests remain RED.

- [ ] **Step 5: keep the facade changes together with the bounded engine**

Run:

```bash
.venv/bin/ruff check \
  python/fastmssql/_query_many.py \
  python/fastmssql/__init__.py \
  tests/test_query_many_contract.py
git diff --check
```

Do not commit an intentionally non-consuming facade. Continue immediately
into Task 6 and commit the public surface only with a working bounded engine.

---

### Task 6: Implement fixed workers, empty input and one capacity window

**Files:**

- Modify: `python/fastmssql/_query_many.py`
- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/__init__.pyi`
- Modify: `VERSION.md`

**Interfaces:**

- Every `self` in the internal producer/worker snippets below denotes
  `_QueryManyState`, never the public `QueryManyIterator` facade. Background
  tasks must therefore remain unable to retain the facade.

- Produces internal records:

```python
@dataclass(slots=True)
class _WorkItem:
    index: int
    parameters: object
    capacity: _CapacityToken

@dataclass(slots=True)
class _CompletedQuery:
    index: int
    result: object
    capacity: _CapacityToken
```

- Produces fixed task names
  `fastmssql-query-many-producer` and
  `fastmssql-query-many-worker-{index}`.

- [ ] **Step 1: bind lazily and derive effective concurrency**

`_QueryManyState.ensure_started()` awaits raw `pool_stats()`, validates that
`max_size` is a positive integer other than `bool`, and computes:

```python
self._effective_concurrency = min(
    self._requested_concurrency,
    max_size,
)
```

Initialize queues, semaphore and exactly that many workers only once. An
empty source reaches `StopAsyncIteration`, creates no pool/query metric,
does not close a normally exhausted producer and remains exhausted on later
`__anext__()` calls.

- [ ] **Step 2: implement an idempotent capacity token**

```python
class _CapacityToken:
    __slots__ = ("_semaphore", "_released")

    def __init__(self, semaphore: asyncio.Semaphore) -> None:
        self._semaphore = semaphore
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._semaphore.release()
```

The producer owns the token until queue submission, a worker owns it while
querying, and a completion owns it until yield/discard.

- [ ] **Step 3: implement one producer task**

For each next index:

1. await the capacity semaphore;
2. pull exactly one source item;
3. release immediately on normal exhaustion;
4. preflight with `operation="query_many"`;
5. enqueue one `_WorkItem`;
6. increment the index only after successful acceptance.

On normal exhaustion, enqueue one private end sentinel per fixed worker.
Never close a normally exhausted source.

- [ ] **Step 4: implement the fixed worker loop**

```python
async def _worker(self, worker_index: int) -> None:
    while True:
        item = await self._work_queue.get()
        if item is _END:
            await self._completed_queue.put(_WORKER_DONE)
            return
        transferred = False
        try:
            preflight_parameter_set(
                item.parameters,
                operation="query_many",
            )
            result = await self._raw_connection.query(
                self._sql,
                item.parameters,
            )
            await self._completed_queue.put(
                _CompletedQuery(item.index, result, item.capacity)
            )
            transferred = True
        except BaseException as error:
            self._register_failure(item.index, error)
            return
        finally:
            if not transferred:
                item.capacity.release()
```

Start exactly `effective_concurrency` workers once and no task per item.
Add a minimal single-assignment `_register_failure()` and normal-exhaustion
join in this task so the module is complete and every task exception is
observed; Task 7 extends the same primitives with shielded abnormal cleanup
rather than replacing task ownership.

- [ ] **Step 5: implement ordered and completion-order consumption**

For unordered mode, yield each `_CompletedQuery` directly. For ordered mode,
store by index and yield only `self._next_ordered_index`.

Immediately before returning a result:

```python
completed.capacity.release()
return completed.result
```

Check a registered failure both before blocking on the completion queue and
after every queue wake, so a failure takes precedence over an unyielded
queued success.

- [ ] **Step 6: prove the empty/bounded/order subset**

Run:

```bash
.venv/bin/pytest \
  tests/test_query_many_coordinator.py \
  -k "empty or capacity or fixed_worker or ordered or completion_order or slow_consumer" \
  -q
```

Expected: all selected tests pass with maximum active queries and retained
items equal to the effective bound.

- [ ] **Step 7: update VERSION and commit**

Record task counts and bound evidence, then:

```bash
.venv/bin/ruff check python/fastmssql/_query_many.py
git diff --check
git add \
  VERSION.md \
  python/fastmssql/_query_many.py \
  python/fastmssql/__init__.py \
  python/fastmssql/__init__.pyi
git commit -m "feat: add bounded query many coordination"
```

---

### Task 7: Implement first-error and terminal cleanup supervision

**Files:**

- Modify: `python/fastmssql/_query_many.py`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: `_await_cleanup()` and `_close_producer()` from
  `_bounded_sequence.py`.
- Produces: one single-assignment primary failure and one idempotent cleanup
  task per iterator.

- [ ] **Step 1: implement single-assignment failure registration**

`_register_failure(index, error)` must:

- set `query_index` best-effort on item/producer errors;
- retain the same error object;
- ignore later candidates as primary;
- atomically set closing state;
- cancel producer and sibling workers, excluding the current task;
- wake a blocked consumer with a private sentinel when the bounded completion
  queue has room;
- start one supervised cleanup task.

Do not set `query_index` from `__anext__()` cancellation or `aclose()`.

- [ ] **Step 2: implement quiescent cleanup ordering**

The cleanup coroutine must:

1. cancel producer and workers;
2. gather all of them with `return_exceptions=True`;
3. observe and retain only the first cleanup failure;
4. call producer `aclose()`/`close()` only after no `anext()` remains active;
5. drain work/completion queues and ordered pending results;
6. release every remaining capacity token idempotently;
7. mark the iterator terminal.

Use `_await_cleanup()` so repeated caller cancellation cannot stop this
sequence.

- [ ] **Step 3: implement public close and context semantics**

Required behavior:

```python
def __aiter__(self) -> QueryManyIterator:
    return self

async def aclose(self) -> None:
    await self._close_without_primary()

async def __aenter__(self) -> QueryManyIterator:
    self._bind_loop()
    return self

async def __aexit__(self, exc_type, exc, traceback) -> bool:
    await self._close_preserving_body_exception(exc)
    return False
```

`__anext__()` catches its own consumer `CancelledError`, awaits terminal
cleanup, chains a cleanup failure if necessary, and re-raises the same
cancellation object.

- [ ] **Step 4: implement supervised best-effort drop**

Producer, worker and cleanup tasks must be state methods or module
coroutines; they must not close over or retain the `QueryManyIterator`
facade. Store the bound loop on the state after first async use.
`QueryManyIterator.__del__()` may only:

- return if never started or already terminal;
- call `state.request_drop_cleanup()` through
  `loop.call_soon_threadsafe()` when the loop is running;
- synchronously request cancellation and schedule the existing cleanup
  supervisor;
- consume its terminal exception through a done callback.

It must not call `asyncio.run()`, block, claim awaited cleanup or create a
coroutine after loop closure.

Startup failure before worker creation follows the same abnormal producer
close path and completes synchronously through the supervised cleanup state;
it must not depend on a worker to wake the consumer.

- [ ] **Step 5: run every offline query-many contract**

Run:

```bash
.venv/bin/pytest \
  tests/test_query_many_contract.py \
  tests/test_query_many_coordinator.py \
  tests/test_query_many_stress_contract.py -q
.venv/bin/pytest \
  tests/test_execute_many_contract.py \
  tests/test_execute_many_coordinator.py \
  tests/test_operation_metrics_contract.py -q
```

Expected: all focused offline contracts and execute-many regressions pass;
there are no pending-task or never-retrieved warnings.

- [ ] **Step 6: update VERSION and commit**

Record exact counts, then:

```bash
.venv/bin/ruff check python/fastmssql tests/test_query_many_*.py
.venv/bin/python -m compileall -q python/fastmssql
git diff --check
git add VERSION.md python/fastmssql/_query_many.py
git commit -m "feat: supervise query many terminal cleanup"
```

---

### Task 8: Make all real SQL Server query-many contracts green

**Files:**

- Modify only when evidence requires:
  `python/fastmssql/_query_many.py`,
  `python/fastmssql/_parameter_sets.py`,
  `python/fastmssql/__init__.py`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: existing Docker SQL-auth fixtures and the raw query path.
- Produces: passing `QMANY-001`–`QMANY-012` evidence without a new Rust SQL
  state machine.

- [ ] **Step 1: start and inspect the dedicated SQL Server**

Run:

```bash
docker compose \
  --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml up -d sqlserver
scripts/sql_auth/provision.sh
docker ps --filter name=fastmssql-sql-auth-dev
```

The provision script validates the dedicated container name and required
SQL-auth secrets before applying `scripts/sql_auth/provision.sql`. Do not
point disruptive tests at any other container.

- [ ] **Step 2: run validation/empty/typed-result cases**

Run:

```bash
.venv/bin/pytest \
  tests/sql_auth_strict/test_query_many_strict.py \
  -k "surface_validation_and_empty or parameter_sources_and_typed_results" \
  -q
```

Require no pool creation for empty input, typed `Parameter`/`Parameters`
round trips and exact first-result `QueryStream` values for list, sync and
async sources.

- [ ] **Step 3: run deterministic pool/order cases**

Run:

```bash
.venv/bin/pytest \
  tests/sql_auth_strict/test_query_many_strict.py \
  -k "pool_window_and_ordering" -q
```

Verify DMV-observed sessions and requests never exceed
`min(concurrency, pool.max_size)`, and release blocker transactions in the
test's explicit `2,0,1` order.

- [ ] **Step 4: run failure, cancellation and fault cases**

Run:

```bash
.venv/bin/pytest \
  tests/sql_auth_strict/test_query_many_strict.py \
  -k "failure_and_consumer_cleanup or faults_and_lifecycle" -q
```

Cover producer error, local conversion error, SQL `THROW`, operation timeout,
consumer cancellation, context early exit, `KILL SPID`, graceful disconnect
and force shutdown. After every case, require zero named application sessions
and a successful smoke query.

Add pool saturation with one separately held lease and `pool.max_size=1`.
The query-many worker must retain its zero-based item association when the
existing acquire timeout terminates it, without starting or retrying SQL.

- [ ] **Step 5: run metrics and security retirement**

Run:

```bash
.venv/bin/pytest \
  tests/sql_auth_strict/test_query_many_strict.py \
  -k "metrics_and_security_retirement" -q
```

Require:

```text
schema_version == 2
query.started == child queries actually started
query.completed == succeeded + errors + timed_out + cancelled
"query_many" not in operations
```

Run successful security-context SQL through the repeated statement, prove
the result was returned, and prove later work uses a different physical
`connection_id`.

- [ ] **Step 6: treat independent defects as separate branch pairs**

If a failure is in pre-existing raw FastMssql/Tiberius behavior rather than
the coordinator, stop editing the feature branch. Create:

```text
test/<focused-defect-name>
  -> fix/<focused-defect-name>
```

Commit deterministic reproduction first, fix second, verify independently,
push only those fork branches, then merge ancestry into the feature. Do not
fold an unrelated repair into query-many.

- [ ] **Step 7: record exact SQL evidence**

If coordinator changes were required, add them with exact test counts and
session/recovery observations to `VERSION.md`, then:

```bash
git add VERSION.md python/fastmssql
git diff --cached --check
git commit -m "feat: harden query many sql lifecycle"
```

If no source change was required, do not create an empty evidence commit;
retain command output for the later validation report.

---

### Task 9: Run required and extended bounded stress

**Files:**

- Modify only on contract-proven defect:
  `scripts/sql_auth/query_many_stress.py`,
  `scripts/sql_auth/run_query_many_stress.sh`,
  `python/fastmssql/_query_many.py`
- Modify: `VERSION.md` when a tracked correction is committed.

**Interfaces:**

- Produces privacy-safe JSON with exact source SHA, worktree cleanliness,
  configuration, per-profile gates and overall status.

- [ ] **Step 1: make structural stress contracts green**

Run:

```bash
.venv/bin/pytest tests/test_query_many_stress_contract.py -q
```

Expected: CLI, bounded AST, hard-gate mutations, atomic artifact and cleanup
tests all pass.

- [ ] **Step 2: run the four required profiles**

Run through the repository SQL-auth environment:

```bash
scripts/sql_auth/run_query_many_stress.sh
```

Expected profiles:

```text
1_000:sync:true:10:10
1_000:async:false:25:8
10_000:sync:false:50:16
10_000:async:true:100:16
```

The harness iterates with:

```python
async with connection.query_many(
    SQL,
    producer,
    concurrency=profile.requested_concurrency,
    ordered=profile.ordered,
) as results:
    async for result in results:
        validate_and_release(result)
```

It must not create operation-sized task lists.

- [ ] **Step 3: run the explicitly approved 99,999 profiles**

Run:

```bash
FASTMSSQL_QUERY_MANY_ALLOW_EXTENDED=1 \
FASTMSSQL_QUERY_MANY_PROFILES='99_999:sync:true:200:32,99_999:async:false:500:32' \
scripts/sql_auth/run_query_many_stress.sh
```

Require exact pulls/yields, no duplicate/missing IDs, accepted window and
active queries no greater than 32, pool/session maximum no greater than 32,
schema 2, exact child query metrics, RSS growth at most 128 MiB, event-loop
gap at most 100 ms, smoke success and zero teardown sessions.

- [ ] **Step 4: inspect the generated artifact without exposing secrets**

Use a parser that prints only:

```text
source_sha
status
profile_count
operations
maximum_accepted_window
maximum_active_queries
maximum_sql_sessions
maximum_rss_growth_bytes
maximum_event_loop_gap_seconds
teardown_sessions
```

Do not print environment variables, connection strings, SQL parameter values
or exception text.

- [ ] **Step 5: correct only measured failures**

For each hard-gate failure, first add or strengthen the deterministic focused
test that reproduces the violated invariant. Then patch the smallest runtime
or harness surface, rerun that test and repeat the exact failed profile.
Update `VERSION.md` and commit a correction with a specific message.

---

### Task 10: Document usage and verify framework compatibility

**Files:**

- Modify: `README.md`
- Modify: `tests/sql_auth_strict/framework_apps.py`
- Modify: `tests/sql_auth_strict/test_framework_integration.py`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: public `Connection.query_many()`.
- Produces: truthful FastAPI/ASGI, Flask/WSGI functional compatibility and
  Flask-via-ASGI persistent-loop smoke without claiming Task 21's full
  process-server matrix.

- [ ] **Step 1: document canonical full and early-stop usage**

Add:

```python
async with connection.query_many(
    "SELECT @P1 AS customer_id",
    ([customer_id] for customer_id in customer_ids),
    concurrency=20,
    ordered=True,
) as results:
    async for result in results:
        consume(result.fetchone())
```

State explicitly:

- each child uses a separate pool lease and existing query metric;
- effective concurrency is capped by pool max;
- `QueryStream` buffers one first result set;
- bare `break` does not synchronously close a general async iterator;
- early-stop callers use `async with` or `await iterator.aclose()`;
- Transaction intentionally has no concurrent query-many surface.

- [ ] **Step 2: add framework smoke helpers**

Add one shared helper to `framework_apps.py`:

```python
async def query_many_payload(connection: Connection) -> list[int]:
    values: list[int] = []
    async with connection.query_many(
        "SELECT @P1 AS value",
        [[1], [2], [3]],
        concurrency=3,
        ordered=True,
    ) as results:
        async for result in results:
            values.append(int(result.fetchone()["value"]))
    return values
```

Expose it through the existing FastAPI, Flask async-under-WSGI and
WsgiToAsgi test applications. Assert `[1, 2, 3]`, no console/server exception
and zero active pool leases after each request.

- [ ] **Step 3: run framework tests**

Run:

```bash
.venv/bin/pytest \
  tests/sql_auth_strict/test_framework_integration.py -q
```

Report Flask/WSGI as functionally compatible with per-request loop limits;
do not reinterpret it as persistent-loop true concurrency. The WsgiToAsgi
case proves the approved persistent-loop adapter path.

- [ ] **Step 4: update VERSION and commit**

Run:

```bash
.venv/bin/ruff check \
  tests/sql_auth_strict/framework_apps.py \
  tests/sql_auth_strict/test_framework_integration.py
git diff --check
git add \
  README.md \
  VERSION.md \
  tests/sql_auth_strict/framework_apps.py \
  tests/sql_auth_strict/test_framework_integration.py
git commit -m "docs: document bounded query many usage"
```

---

### Task 11: Verify an installed ABI3 wheel outside the source tree

**Files:**

- Do not track wheel, virtual environment, cache or generated evidence.
- Modify runtime/tests only after a new RED reproduction if the installed
  artifact differs from source-tree behavior.

**Interfaces:**

- Produces exact wheel filename, SHA-256 and isolated import/test evidence for
  the later report.

- [ ] **Step 1: require a clean exact source SHA**

Run:

```bash
git status --short
git rev-parse HEAD
```

Expected: no tracked or untracked change and one exact feature SHA.

- [ ] **Step 2: build the wheel in workspace-local ignored artifacts**

Before building, resolve the four feature-owned paths below against the
current worktree. Require every resolved path to begin with the exact
worktree-local `.artifacts/sql-auth/query-many-` prefix, then remove only a
stale instance of those four paths. After `maturin build`, require exactly
one `.whl` in the wheel directory before expanding the install/hash glob.

Run:

```bash
CARGO_TARGET_DIR=.artifacts/sql-auth/query-many-wheel-target \
UV_CACHE_DIR=.artifacts/sql-auth/query-many-wheel-cache \
.venv/bin/maturin build --release --locked \
  --out .artifacts/sql-auth/query-many-wheel
uv venv --python 3.13 .artifacts/sql-auth/query-many-wheel-venv
uv pip install \
  --python .artifacts/sql-auth/query-many-wheel-venv/bin/python \
  pytest==9.1.1 \
  pytest-asyncio==1.4.0 \
  pytest-timeout==2.4.0 \
  python-dotenv==1.2.2 \
  psutil==7.2.2 \
  'fastapi>=0.139.2' \
  'flask[async]>=3.1.3' \
  'httpx>=0.28.1' \
  'asgiref>=3.12.1' \
  'asgi-lifespan>=2.1.0' \
  .artifacts/sql-auth/query-many-wheel/*.whl
shasum -a 256 .artifacts/sql-auth/query-many-wheel/*.whl
```

All paths are project-local, ignored and scoped to this feature. Record:

```text
wheel filename
sha256
source SHA
Python ABI/platform tag
```

- [ ] **Step 3: install into a fresh external environment**

The previous step created the environment and installed only the wheel plus
the explicit test/framework dependencies. Unset `PYTHONPATH` and verify:

```bash
env -u PYTHONPATH \
  .artifacts/sql-auth/query-many-wheel-venv/bin/python -c \
  'from pathlib import Path; import fastmssql; module_path = Path(fastmssql.__file__).resolve(); assert "site-packages" in module_path.parts; assert "FastMssql/python" not in str(module_path); print(module_path)'
```

The printed path is non-secret evidence and must reside under
`.artifacts/sql-auth/query-many-wheel-venv`.

- [ ] **Step 4: run installed offline and real-SQL contracts**

Run:

```bash
env -u PYTHONPATH \
  .artifacts/sql-auth/query-many-wheel-venv/bin/python \
  -m pytest --noconftest \
  tests/test_query_many_contract.py \
  tests/test_query_many_coordinator.py \
  tests/test_query_many_stress_contract.py -q
set -a
source .env.sql-auth.local
set +a
env -u PYTHONPATH \
  .artifacts/sql-auth/query-many-wheel-venv/bin/python -m pytest \
  tests/sql_auth_strict/test_query_many_strict.py -q
env -u PYTHONPATH \
  .artifacts/sql-auth/query-many-wheel-venv/bin/python -m pytest \
  tests/sql_auth_strict/test_framework_integration.py \
  -k query_many -q
uv pip check \
  --python .artifacts/sql-auth/query-many-wheel-venv/bin/python
```

Required result: all pass from installed `site-packages`; no source-tree
fallback appears in `sys.path` or module paths.

- [ ] **Step 5: discard untracked build products safely**

Resolve and assert that each removal target begins with the current
worktree's exact `.artifacts/sql-auth/query-many-` prefix. The parent
`.artifacts/sql-auth/` directory is already ignored by the repository.
Remove only:

```text
.artifacts/sql-auth/query-many-wheel
.artifacts/sql-auth/query-many-wheel-cache
.artifacts/sql-auth/query-many-wheel-target
.artifacts/sql-auth/query-many-wheel-venv
```

Then confirm `git status --short` remains clean.

---

### Task 12: Focused feature gate, graph review and fork-only publication

**Files:**

- Modify only to repair evidence-backed focused failures.
- Update: `VERSION.md` for every correction commit.

**Interfaces:**

- Produces one clean exact feature SHA ready for parent Task 12 cumulative
  verification.

- [ ] **Step 1: run the complete focused Python gate fresh**

Run:

```bash
.venv/bin/pytest \
  tests/test_query_many_contract.py \
  tests/test_query_many_coordinator.py \
  tests/test_query_many_stress_contract.py \
  tests/test_execute_many_contract.py \
  tests/test_execute_many_coordinator.py \
  tests/test_execute_many_stress_contract.py \
  tests/test_operation_metrics_contract.py \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/sql_auth_strict/test_query_many_strict.py \
  tests/sql_auth_strict/test_framework_integration.py -q
```

Read the complete output and record exact pass/fail/skip counts. No swallowed
exception or intentional skip is acceptable for a required query-many case.

- [ ] **Step 2: run formatting, syntax and unchanged Rust gates**

Run:

```bash
.venv/bin/ruff check python/fastmssql tests scripts/sql_auth/query_many_stress.py
.venv/bin/python -m compileall -q python/fastmssql tests scripts/sql_auth
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo clippy \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --all-targets -- \
  -D warnings \
  -A clippy::doc_lazy_continuation \
  -A clippy::extra_unused_lifetimes \
  -A clippy::large_enum_variant \
  -A clippy::io_other_error \
  -A clippy::needless_lifetimes \
  -A clippy::legacy_numeric_constants \
  -A clippy::cast_enum_truncation \
  -A clippy::derivable_impls \
  -A clippy::manual_div_ceil \
  -A clippy::items_after_test_module
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls --lib
scripts/security/audit_dependencies.sh
git diff --check
```

Although the adopted implementation is Python-only, Rust gates prove the raw
query/disposition foundation was not regressed.

- [ ] **Step 3: rebuild and review the knowledge graph**

Run:

```bash
uvx code-review-graph build
```

Then use graph tools:

1. `get_minimal_context`;
2. `detect_changes` against the plan base;
3. `get_affected_flows`;
4. `query_graph` with `tests_for` for
   `Connection.query_many`, `QueryManyIterator.__anext__` and
   `QueryManyIterator.aclose`.

Verify graph branch/SHA match HEAD. Reconcile dynamic wrapper gaps against
offline, SQL-auth and installed-wheel evidence rather than silently ignoring
them.

- [ ] **Step 4: run privacy, artifact and branch checks**

Require:

- no tracked `.env`, wheel, target/cache or stress JSON artifact;
- no token/private-key/password pattern in the diff;
- all new Markdown links resolve;
- `VERSION.md` describes every commit;
- upstream push URL remains `DISABLED`;
- `git merge-base --is-ancestor
  test/query-many-bounded-concurrency
  feat/query-many-bounded-concurrency` exits zero.

- [ ] **Step 5: self-review the feature diff**

Inspect:

```bash
git diff --stat docs/query-many-design...HEAD
git diff --check docs/query-many-design...HEAD
git log --oneline --decorate docs/query-many-design..HEAD
```

Check requirement-by-requirement against the focused design:

- fixed worker count;
- one total capacity window;
- exact ordering;
- truthful early exit;
- first-error cleanup;
- no fabricated cancellation index;
- no raw/Transaction surface;
- schema 2 unchanged;
- per-query metrics;
- SQL fault recovery;
- required/extended stress;
- installed wheel.

- [ ] **Step 6: commit any final evidence-backed correction**

If verification changed tracked files, update `VERSION.md`, rerun the exact
failed gate and commit with a specific correction message. Do not create an
empty “verification” commit.

- [ ] **Step 7: publish only the feature branch and verify the remote SHA**

Run:

```bash
git push -u origin feat/query-many-bounded-concurrency
git ls-remote origin refs/heads/feat/query-many-bounded-concurrency
git remote get-url --push upstream
```

Expected: remote SHA equals local HEAD; upstream push remains `DISABLED`.
Read public GitHub Actions/check-runs for that exact SHA. Record zero runs as
`NOT RUN`, never as inferred PASS.

---

### Task 13: Hand off to the parent batch/bulk cumulative gate

**Next branches:**

```text
verify/batch-bulk-merge
  -> docs/batch-bulk-status
```

**Files:**

- Parent Task 12 creates cumulative evidence from the exact technical merge.
- Parent Task 13 updates
  `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`,
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`,
  `docs/SQL_AUTH_TEST_MATRIX.md`, `docs/SQL_AUTH_TEST_REPORT.md` and
  `VERSION.md`, and creates
  `docs/SQL_AUTH_BATCH_BULK_STRESS_REPORT.md` as required by the parent
  batch/bulk plan.

**Interfaces:**

- Consumes: the exact verified feature SHA and every RED/fix ancestry edge.
- Produces: live-audit closure of feature 19/21 only when all mandatory gates
  pass.

- [ ] **Step 1: preserve history-only ancestry**

Create the cumulative verification branch from the latest technical
candidate and merge branch histories without replacing tested trees through
cherry-pick-only topology.

- [ ] **Step 2: run the parent full gate**

Execute the complete `scripts/sql_auth/run_all.sh`, both Rust suites,
format/Clippy, RustSec, required and extended stress, installed wheel,
Linux/macOS/Windows hosted truth and graph review on one exact SHA.

- [ ] **Step 3: update the live audit only from exact evidence**

Mark `query_many()` and the seven-part batch/bulk feature `VERIFIED_FORK`
only if every required case/gate passes. Otherwise keep the item open and
record the exact failing command and residual risk.

- [ ] **Step 4: keep later enterprise work visible**

After feature 19 closes, continue separately with:

```text
20. fix/named-instance
21. test/production-framework-matrix
```

Do not claim the whole library enterprise production-ready until those audit
items and the explicitly retained P2 scope have their own verified
dispositions.
