# FastMssql Enterprise Result Sets, Streaming and RPC Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:executing-plans` task-by-task, use
> `superpowers:test-driven-development` for every behavior change, use
> `superpowers:systematic-debugging` for every unexpected failure, and use
> `superpowers:verification-before-completion` before each integration claim.
> Execute inline; the active repository instructions do not permit delegated
> subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded true-async Python result API that preserves complete
SQL Server result sets, metadata, DONE/INFO records, stored-procedure return
status and OUTPUT values while keeping every pooled or transactional TDS
session fail-closed until protocol and Python conversion completion are known.

**Architecture:** The vendored Tiberius layer exposes an additive
`ResponseStream` of owned protocol events and direct named RPC requests.
FastMssql starts one Rust producer that owns the physical lease, lifecycle
permit, absolute deadline and response stream; it communicates with
single-consumer Python async iterators through bounded event and
acknowledgement channels. Normal protocol EOF plus successful conversion ACKs
returns the lease through RESETCONNECTION, while abandonment, cancellation,
conversion uncertainty and lifecycle force retire it.

**Tech Stack:** Rust 1.94.0, edition 2024, PyO3 0.29.0,
pyo3-async-runtimes 0.29.0, Tokio 1.52.3, bb8 0.9.1, vendored Tiberius 0.12.3,
Python 3.11+, pytest 9.1.1, pytest-asyncio 1.4.0, psutil 7.2.2, uv, maturin
1.14.1, Docker SQL Server 2022 with SQL authentication, GitHub Actions on
Linux/macOS/Windows.

## Global Constraints

- Source baseline is cumulative branch `test/sql-auth-validation` at
  `88ac9c00d3d80edbb84377fc1f812070b5cf289b`.
- Design authority is
  `docs/superpowers/specs/2026-07-27-fastmssql-resultsets-streaming-design.md`,
  initially committed at
  `4cb360aa39e2e63f3da04de21c2b1784b3cd32b0`; the bounded-ACK, active-set
  drop, metadata, tracing and direct-RPC reset corrections travel with this
  plan.
- Work, branches, commits, pushes, workflow runs and artifacts target only
  `https://github.com/galeamarcel/FastMssql.git`.
- `upstream` remains fetch-only and its push URL remains exactly `DISABLED`.
- Do not create, push, publish or open a pull request in
  `Rivendael/FastMssql` or in a separate Tiberius fork.
- Create each test, feature, fix, verification and status branch in its own
  project-local ignored `.worktrees/<hyphenated-branch-name>` worktree.
- Run every `git worktree add` command from the primary checkout root, so all
  worktrees remain direct children of its ignored `.worktrees` directory and
  the documented shared `../../.venv` path stays valid.
- Preserve strict ancestry and every reviewed commit; do not squash the
  problem-specific history.
- A RED branch changes only focused tests, their test specification, runner
  wiring and `VERSION.md`; it does not contain production behavior.
- A feature branch descends from its RED branch and contains the minimum
  implementation required for that focused contract.
- Any defect discovered during execution receives its own `test/...` then
  `fix/...` branch inserted before final verification.
- Keep `query()` and `simple_query()` source-compatible and returning the
  existing buffered, synchronous `QueryStream`.
- Add only `stream()`, `batch()` and `callproc()` as enterprise response APIs;
  do not silently change an existing return type.
- `buffer_size` accepts plain integers 1 through 1,024, rejects Python
  booleans and defaults to 64.
- The event channel and consumer-ACK channel are both bounded; no response
  path may collect all rows into a `Vec`.
- One absolute `operation_timeout` covers request send, protocol reads,
  backpressure, consumer acknowledgements and terminal cleanup.
- A lease becomes reusable only after protocol EOF and every queued metadata,
  row and output-summary conversion is acknowledged successful or discarded
  intentionally by `finish()`/`ResultSet.aclose()`.
- Full response close, active-result drop, response drop, conversion failure,
  task cancellation, protocol uncertainty and lifecycle force retire the
  physical connection.
- A fully drained nonfatal SQL Server error may follow the existing
  `NeedsReset` disposition; fatal SQL, I/O and protocol errors remain broken.
- A direct named RPC on a recycled lease first consumes the reset-bearing
  READ COMMITTED baseline batch inside the same operation deadline.
- Store no Python object, GIL-bound reference or borrowed Tiberius stream
  across an unsafe lifetime boundary. Do not add `unsafe`.
- Do not log SQL text, parameters, row/output values, procedure names, server
  message text, credentials or connection strings.
- Fixed per-response caps are 1,024 result sets, 1,024 INFO messages and
  4,096 DONE records. Overflow reason is exactly
  `response_limit_exceeded`.
- Procedure and named-parameter names use the exact conservative ASCII,
  non-quoted grammars from the design.
- `callproc()` accepts at most 2,100 encoded RPC arguments; RETURN_VALUE is a
  local status slot and does not consume a wire argument.
- Expanded descriptors and mixed positional/named descriptors are rejected
  for `callproc()` before checkout.
- Do not add MARS, server cursors, pagination rewriting, TVP, native bulk,
  MONEY/SMALLMONEY output, SQL_VARIANT, spatial, hierarchyid, CLR UDT or
  legacy LOB support, chunked cell reads or a channel byte budget in this
  subsystem.
- Keep operation-metrics schema version 1. Map `stream()` and `callproc()` to
  `query`, and `batch()` to `query_batch`.
- Update `VERSION.md` on every branch modification without changing package
  metadata or the displayed `0.7.7` version.
- Update the live production-readiness audit only after exact merged local,
  Docker, wheel and hosted verification.
- Before every real-MSSQL pytest command in a worktree, build that worktree's
  extension and load the shared SQL-auth environment without printing it:

  ```bash
  CARGO_TARGET_DIR=/private/tmp/fastmssql-cargo-target \
  UV_CACHE_DIR=/private/tmp/fastmssql-uv-cache \
  ../../.venv/bin/maturin develop --release
  set -a
  source ../../.env.sql-auth.local
  set +a
  ```

  The project-local worktrees intentionally have no private `.venv`; all
  focused commands use the root checkout's shared `../../.venv`.

- Every required test has zero skips, zero xfails, zero swallowed exceptions
  and zero retry-based passes.

---

## File and Interface Map

### Vendored Tiberius

- Create `vendor/tiberius/tests/token_safety_sql_auth.rs` for real
  TABNAME/COLINFO and unsupported TYPE_INFO reproductions.
- Modify `vendor/tiberius/src/tds/codec/token/token_type.rs`,
  `vendor/tiberius/src/tds/stream/token.rs` and
  `vendor/tiberius/src/tds/codec/type_info.rs` so recognized browse metadata
  is consumed and unsupported SQL_VARIANT/UDT metadata returns typed errors
  without panicking.
- Create `vendor/tiberius/src/tds/stream/response.rs` for public owned
  `ResponseEvent`, `ResponseStream`, response metadata, DONE, INFO and
  RETURNVALUE structures.
- Create `vendor/tiberius/tests/response_api.rs` for the database-independent
  public response/RPC API contract.
- Create `vendor/tiberius/tests/response_events_sql_auth.rs` for no-skip real
  SQL Server response-event and direct-RPC contracts.
- Modify `vendor/tiberius/src/tds/stream.rs` and
  `vendor/tiberius/src/lib.rs` to export the additive response API.
- Modify `vendor/tiberius/src/client.rs` to expose parameterized SQL, raw
  batch and direct named-RPC `ResponseStream` entry points, including the
  recycled-session isolation baseline.
- Modify `vendor/tiberius/src/client/connection.rs` so Debug/diagnostic paths
  report structure and buffer length without raw TDS bytes.
- Modify `vendor/tiberius/src/tds/stream/query.rs` so existing `QueryStream`
  filters `ResponseStream` without changing its public behavior.
- Modify `vendor/tiberius/src/tds/stream/token.rs` to decode signed
  RETURNSTATUS and emit structural tracing only.
- Modify `vendor/tiberius/src/tds/codec/rpc_request.rs` to encode ProcName as
  checked `US_VARCHAR`, checked B_VARCHAR parameter names and ByRef flags.
- Modify DONE/INFO/RETURNVALUE token files only for safe getters or internal
  conversion needed by `response.rs`.
- Modify `vendor/tiberius/FASTMSSQL_PATCH.md` to document the local response
  and named-RPC patch.

### FastMssql Rust

- Create `src/result_types.rs` for immutable Python `ColumnMetadata`,
  `DoneResult`, `SqlMessage` and `ResultSummary`, plus raw-summary conversion.
- Create `src/result_stream.rs` for bounded channels, producer/consumer state,
  `ResultStream`, `ResultSet`, pooled response ownership and terminal ACKs.
- Create `src/procedure.rs` for procedure-name validation, direction
  validation, canonical output slots and owned Tiberius RPC arguments.
- Modify `src/connection.rs` to add pooled `stream`, `batch` and `callproc`.
- Modify `src/transaction.rs` to add the same APIs and a
  `TransactionResponseLease` backed by `OwnedMutexGuard<TransactionSession>`.
- Modify `src/pool_manager.rs` to construct an owned
  `PooledOperationGuard<'static>` and preserve disposition transitions.
- Modify `src/operation_metrics.rs` to expose a movable operation observer
  whose terminal outcome is recorded by the producer rather than when the
  Python response object is created.
- Modify `src/parameter_conversion.rs`, `src/py_parameters.rs` and
  `src/type_mapping.rs` to share exact scalar conversion with procedure input
  and output paths while keeping query execution INPUT-only.
- Modify `src/types.rs` to construct shared `ColumnInfo` from received
  metadata instead of requiring a first row.
- Modify `src/lib.rs` to register and export every new Python class.

### Python API, tests and evidence

- Modify `python/fastmssql/__init__.py`,
  `python/fastmssql/__init__.pyi` and
  `python/fastmssql/fastmssql.pyi` for exact runtime/stub parity and honest
  legacy `QueryStream` documentation.
- Modify `README.md` to distinguish buffered compatibility APIs from bounded
  enterprise response APIs.
- Create `tests/test_result_stream_contract.py` for exports, stubs, immutable
  result types, async protocol and legacy-documentation contracts.
- Create `tests/test_tiberius_response_privacy_contract.py` for structural
  trace-source and value-redaction constraints.
- Create `tests/sql_auth_strict/test_resultsets_streaming.py` for
  `RESULT-016..021`, `RESULT-025`, `RESULT-028` and the RESULT-029 artifact
  validator. Task 12 reuses this file under the isolated installed wheel, but
  the source-side file does not itself claim `RESULT-030`.
- Create `tests/sql_auth_strict/test_resultstream_lifecycle.py` for
  `RESULT-022..024`, `RESULT-026..027` and `RESULT-031`.
- Create `tests/sql_auth_strict/test_rpc_results.py` for `RPC-001..011`.
- Create `scripts/sql_auth/result_stream_stress.py` and
  `scripts/sql_auth/run_result_stream_stress.sh` for bounded 1,000-operation
  load and optional profiles through 99,999.
- Modify the SQL-auth design, runner and report inputs so every new case is
  required and recorded.
- Modify `.github/workflows/rust-unit-tests.yml` and
  `tests/test_pyo3_build_contract.py` to run vendored response tests and the
  installed-wheel result-stream contract on Linux, macOS and Windows.

## Stable Internal Interfaces

The implementation must converge on these names and roles:

```rust
// vendor/tiberius/src/tds/stream/response.rs
pub enum ResponseEvent {
    Metadata(ResponseMetadata),
    Row(Row),
    Done(ResponseDone),
    Info(ResponseInfo),
    ReturnStatus(i32),
    ReturnValue(ResponseReturnValue),
}

pub struct ResponseColumn {
    name: String,
    column_type: ColumnType,
    type_name: String,
    nullable: Option<bool>,
    precision: Option<u8>,
    scale: Option<u8>,
    length: Option<ResponseLength>,
}

pub enum ResponseLength {
    Limited(usize),
    Max,
}

pub struct RpcParameter {
    name: String,
    value: ColumnData<'static>,
    parameter_type: Option<SqlParameterType>,
    by_ref: bool,
    parameter_index: usize,
}

impl RpcParameter {
    pub fn new(
        name: String,
        value: ColumnData<'static>,
        parameter_type: Option<SqlParameterType>,
        by_ref: bool,
        parameter_index: usize,
    ) -> Self;
}
```

Every public response structure exposes read-only getters for the fields
consumed by FastMssql; no value-bearing type derives or implements an
unredacted `Debug`.

```rust
// vendor/tiberius/src/client.rs
pub async fn response_query<'a, 'b>(
    &'a mut self,
    query: impl Into<Cow<'b, str>>,
    params: &'b [&'b dyn ToSql],
) -> crate::Result<ResponseStream<'a>>
where
    'a: 'b;

pub async fn response_batch<'a, 'b>(
    &'a mut self,
    query: impl Into<Cow<'b, str>>,
) -> crate::Result<ResponseStream<'a>>
where
    'a: 'b;

pub async fn response_rpc<'a>(
    &'a mut self,
    procedure: String,
    params: Vec<RpcParameter>,
) -> crate::Result<ResponseStream<'a>>;
```

```rust
// src/result_stream.rs
pub(crate) enum ResponseRequest {
    Query {
        sql: String,
        parameters: Vec<FastParameter>,
        retire_after_operation: bool,
    },
    Batch {
        sql: String,
        retire_after_operation: bool,
    },
    Procedure(ProcedureCall),
}

pub(crate) enum ConsumerAckKind {
    Converted,
    Discarded,
    ConversionFailed,
}

pub(crate) struct ConsumerAck {
    sequence: u64,
    kind: ConsumerAckKind,
}

#[derive(Clone)]
pub(crate) enum TerminalRelease {
    Pending,
    ReleasedSuccess,
    ReleasedFailure(Arc<StoredTerminalError>),
    ReleasedRetired,
}

#[pyclass(name = "ResultStream")]
pub struct PyResultStream;

#[pyclass(name = "ResultSet")]
pub struct PyResultSet;
```

`StoredTerminalError` owns a clone-safe, value-redacted representation of the
already-classified Python error and can materialize the same exception class
and safe metadata on repeated terminal calls. It never reconstructs an error
from SQL text, parameter values or log output.

```rust
// src/operation_metrics.rs
pub(crate) struct OperationObserver;

impl OperationObserver {
    pub(crate) fn start(
        metrics: Option<Arc<OperationMetricsRegistry>>,
        operation: OperationName,
    ) -> Self;
    pub(crate) fn finish_success(self);
    pub(crate) fn finish_error(self, error: &PyErr);
    pub(crate) fn finish_cancelled(self);
}
```

The public Python signatures are:

```python
async def stream(
    self,
    sql: str,
    params: list[Any] | Parameters | None = None,
    *,
    buffer_size: int = 64,
) -> ResultStream: ...

async def batch(
    self,
    sql: str,
    *,
    buffer_size: int = 64,
) -> ResultStream: ...

async def callproc(
    self,
    procedure: str,
    params: Parameters | None = None,
    *,
    buffer_size: int = 64,
) -> ResultStream: ...
```

`Connection` and `Transaction` expose all three signatures.

The async result protocols converge on:

```python
class ResultStream:
    def __aiter__(self) -> ResultStream: ...
    async def __anext__(self) -> ResultSet: ...
    async def __aenter__(self) -> ResultStream: ...
    async def __aexit__(self, exc_type, exc, traceback) -> None: ...
    async def aclose(self) -> None: ...
    async def finish(self) -> ResultSummary: ...
    @property
    def closed(self) -> bool: ...
    @property
    def complete(self) -> bool: ...
    @property
    def summary(self) -> ResultSummary: ...

class ResultSet:
    def __aiter__(self) -> ResultSet: ...
    async def __anext__(self) -> FastRow: ...
    async def aclose(self) -> None: ...
    @property
    def index(self) -> int: ...
    @property
    def columns(self) -> tuple[ColumnMetadata, ...]: ...
    @property
    def column_names(self) -> tuple[str, ...]: ...
    @property
    def closed(self) -> bool: ...
```

---

## Task 1: Commit the Reviewed Design Corrections and Executable Plan

**Branch:** `docs/resultsets-streaming-design`

**Files:**

- Modify:
  `docs/superpowers/specs/2026-07-27-fastmssql-resultsets-streaming-design.md`
- Create:
  `docs/superpowers/plans/2026-07-27-fastmssql-resultsets-streaming.md`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: reviewed design commit
  `4cb360aa39e2e63f3da04de21c2b1784b3cd32b0`.
- Produces: the single design authority and task sequence used by every later
  branch.

- [ ] **Step 1: Verify repository boundary and ancestry**

Run:

```bash
git rev-parse HEAD
git rev-parse HEAD^
git remote get-url origin
git remote get-url --push upstream
git status --short --branch
```

Expected: HEAD descends from `88ac9c0`, origin is
`galeamarcel/FastMssql`, upstream push is `DISABLED`, and only the design
amendment, this plan and `VERSION.md` are modified.

- [ ] **Step 2: Self-review design-to-plan coverage**

Run:

```bash
rg -n '^## |^### Task' \
  docs/superpowers/specs/2026-07-27-fastmssql-resultsets-streaming-design.md \
  docs/superpowers/plans/2026-07-27-fastmssql-resultsets-streaming.md
rg -n -i 'T[B]D|FIX[M]E|implement la[t]er|fill in detai[l]s|Similar to Tas[k]|lorem ips[u]m|insert text he[r]e' \
  docs/superpowers/specs/2026-07-27-fastmssql-resultsets-streaming-design.md \
  docs/superpowers/plans/2026-07-27-fastmssql-resultsets-streaming.md
git diff --check
```

Expected: no placeholder match, balanced Markdown fences, clean whitespace,
and explicit tasks for every protocol, result, lifecycle, RPC, load, wheel,
hosted and audit requirement. Literal `todo!`/`panic!` names remain expected
only where the plan records or forbids the reproduced Rust defect.

- [ ] **Step 3: Rebuild and review the knowledge graph**

Run:

```bash
uvx code-review-graph build
```

Then run graph `get_minimal_context`, `detect_changes` and
`get_affected_flows` for the three documentation files.

Expected: graph HEAD matches the branch; documentation changes affect no
runtime function or execution flow.

- [ ] **Step 4: Commit and publish only the design branch**

Run:

```bash
git add \
  VERSION.md \
  docs/superpowers/specs/2026-07-27-fastmssql-resultsets-streaming-design.md \
  docs/superpowers/plans/2026-07-27-fastmssql-resultsets-streaming.md
git diff --cached --check
git diff --cached --name-status
git commit -m "docs: plan bounded result streaming"
git push origin docs/resultsets-streaming-design
git ls-remote --heads origin refs/heads/docs/resultsets-streaming-design
git remote get-url --push upstream
```

Expected: only the three files are committed, local/fork SHA match, and
upstream remains `DISABLED`.

---

## Task 2: Record RED Vendored-Tiberius Token-Safety Reproductions

**Branch:** `test/tiberius-token-safety`

**Files:**

- Create: `vendor/tiberius/tests/token_safety_sql_auth.rs`
- Modify: `vendor/tiberius/src/tds/codec/token/token_type.rs` (tests only)
- Modify: `vendor/tiberius/src/tds/stream/token.rs` (tests only)
- Modify: `vendor/tiberius/src/tds/codec/type_info.rs` (tests only)
- Create: `tests/test_tiberius_token_safety_contract.py`
- Modify: `tests/test_pyo3_build_contract.py`
- Modify: `scripts/sql_auth/run_all.sh`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: current `TokenType`, `TokenStream`, `TypeInfo::decode()` and
  legacy `Client::query()`.
- Produces: RED `TIB-SAFE-001..003` contracts for TABNAME/COLINFO support and
  panic-free unsupported TYPE_INFO.

- [ ] **Step 1: Create the isolated RED worktree**

```bash
git worktree add \
  .worktrees/test-tiberius-token-safety \
  -b test/tiberius-token-safety \
  docs/resultsets-streaming-design
```

- [ ] **Step 2: Add real SQL-auth reproductions**

Create `vendor/tiberius/tests/token_safety_sql_auth.rs`. Read the existing
`FASTMSSQL_SQL_AUTH_*` variables without printing values and connect through
Tokio compatibility I/O. Implement:

```text
TIB-SAFE-001 -> tib_safe_001_for_browse_consumes_tabname_and_colinfo
TIB-SAFE-002 -> tib_safe_002_sql_variant_is_typed_error_without_panic
```

The first executes:

```sql
SELECT TOP (1) name FROM sys.objects FOR BROWSE
```

and requires one legacy `QueryStream` row plus a later smoke query on the same
client. The second executes:

```sql
SELECT CAST(1 AS SQL_VARIANT) AS value
```

and requires a deterministic safe unsupported/protocol error, no Rust panic
hook invocation, and a clean new-client smoke query. Observe the panic hook
with a process-global `AtomicBool` behind an RAII guard that always restores
the previous hook; keep the existing `--test-threads=1` lane so the hook
cannot race another integration case. Missing environment is a setup
failure; neither test conditionally returns or ignores.

- [ ] **Step 3: Add raw token-payload and TYPE_INFO panic regressions**

In the existing `#[cfg(test)]` modules of `token_type.rs` and `token.rs`,
require `TokenType::try_from(0xa4)` to produce `TableName` and exercise the
planned private USHORT-length payload reader with raw TABNAME/COLINFO
payloads. Assert exact declared-byte consumption, preservation of the first
following token byte and a typed I/O/protocol error for truncation. Name
these tests with the `tib_safe_003_` prefix. The unchanged RED baseline may
fail to compile because the enum variant/helper does not exist; do not add
runtime implementation on this branch.

In only the existing `#[cfg(test)]` module of `type_info.rs`, decode raw
SQL_VARIANT (`0x62`) and UDT (`0xf0`) type bytes under Tokio tests. Require
`Err(crate::Error::Protocol(_))` with safe type classifications. Do not catch
the panic merely to make RED pass: on the unchanged code the test must fail at
the current `todo!`. Name the tests:

```text
tib_safe_003_sql_variant_type_info_is_typed_error
tib_safe_003_udt_type_info_is_typed_error
```

- [ ] **Step 4: Add the static panic-safety contract (`TIB-SAFE-003`)**

`tests/test_tiberius_token_safety_contract.py` parses the selected vendored
sources and requires:

```text
TokenType::TableName = 0xA4
explicit TableName and ColInfo dispatch
no panic!/todo!/unimplemented!/unreachable! in TokenStream dispatch
no panic!/todo!/unimplemented!/unreachable! for Udt/SSVariant TYPE_INFO decode
```

Scope the checks to decoder/dispatch bodies so unrelated feature-gated
encoding or test assertions do not create false positives.

- [ ] **Step 5: Register local and hosted RED gates**

After compose/provision in `run_all.sh`, add a required
`tiberius-token-safety-sql-auth` lane:

```bash
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test token_safety_sql_auth -- --test-threads=1
```

Update `tests/test_pyo3_build_contract.py` to require a database-independent
vendored `cargo test ... --lib` command in all hosted OS jobs and to reject
the SQL-auth integration test there. The workflow itself remains unchanged
on RED.

- [ ] **Step 6: Record RED**

Load `.env.sql-auth.local`, then run:

```bash
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --lib tib_safe_003
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test token_safety_sql_auth -- --test-threads=1
../../.venv/bin/pytest \
  tests/test_tiberius_token_safety_contract.py \
  tests/test_pyo3_build_contract.py -q
```

Expected: FOR BROWSE reports invalid token `a4`, SQL_VARIANT reaches the
existing `todo!`, raw TYPE_INFO tests panic, and both static contracts fail
for the absent implementation/workflow command. No setup error or skip is
accepted.

- [ ] **Step 7: Self-review, commit and push RED**

Append a no-runtime-change `VERSION.md` entry, format-check the vendored
test sources and run `git diff --check`:

```bash
cargo fmt --manifest-path vendor/tiberius/Cargo.toml --check
git diff --check
```

Then:

```bash
git add \
  VERSION.md \
  scripts/sql_auth/run_all.sh \
  tests/test_pyo3_build_contract.py \
  tests/test_tiberius_token_safety_contract.py \
  vendor/tiberius/src/tds/codec/token/token_type.rs \
  vendor/tiberius/src/tds/codec/type_info.rs \
  vendor/tiberius/src/tds/stream/token.rs \
  vendor/tiberius/tests/token_safety_sql_auth.rs
git diff --cached --check
git commit -m "test: reproduce unsafe TDS token handling"
git push -u origin test/tiberius-token-safety
```

Expected: only tests, runner/static-contract wiring and version evidence are
committed.

---

## Task 3: Make Browse and Unsupported TYPE_INFO Handling Panic-Free

**Branch:** `fix/tiberius-token-safety`

**Files:**

- Modify: `vendor/tiberius/src/tds/codec/token/token_type.rs`
- Modify: `vendor/tiberius/src/tds/stream/token.rs`
- Modify: `vendor/tiberius/src/tds/codec/type_info.rs`
- Modify: `vendor/tiberius/FASTMSSQL_PATCH.md`
- Modify: `.github/workflows/rust-unit-tests.yml`
- Modify: `scripts/sql_auth/run_all.sh`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: Task 2 RED.
- Produces: structural TABNAME/COLINFO consumption and typed unsupported
  TYPE_INFO errors; it does not implement SQL_VARIANT/UDT value conversion.

- [ ] **Step 1: Create the fix worktree from RED**

```bash
git worktree add \
  .worktrees/fix-tiberius-token-safety \
  -b fix/tiberius-token-safety \
  test/tiberius-token-safety
```

- [ ] **Step 2: Consume browse metadata structurally**

Add `TokenType::TableName = 0xA4`. Implement the generic private
USHORT-length payload reader required by the RED unit tests. In
`TokenStream`, use it to decode TABNAME and COLINFO, consume exactly the
declared bytes and emit private structural `ReceivedToken::TableName` /
`ReceivedToken::ColInfo` variants. Record only token kind and byte count in
tracing. Existing `QueryStream` ignores both variants, so rows and metadata
remain source-compatible.

Reject truncated payloads through the existing I/O/protocol error path; do not
guess a length or retain base-table names.

- [ ] **Step 3: Replace unsupported TYPE_INFO panics**

In `TypeInfo::decode`, explicitly match UDT and SQL_VARIANT and return safe
typed protocol errors before any `todo!`/panic. Remove the wildcard panic from
token dispatch by handling every current `TokenType` explicitly. Do not add
row conversion for either type.

- [ ] **Step 4: Add local and hosted vendored-crate gates**

Add the exact database-independent command required by the RED static
contract to every Linux/macOS/Windows Cargo job after root `cargo test
--locked`. Keep real SQL-auth tests out of generic hosted runners.

Before Docker provisioning in `run_all.sh`, add required vendored
`tiberius-fmt`, `tiberius-clippy` and `tiberius-lib` lanes with the same
`chrono,tds73,rustls` feature set as the integration tests. Root Cargo gates
do not cover the path dependency's own unit tests, integration targets or
formatting, so both gate families remain required.

- [ ] **Step 5: Make focused tests green**

Load SQL-auth variables and run:

```bash
cargo fmt --check
cargo fmt --manifest-path vendor/tiberius/Cargo.toml --check
cargo clippy \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --all-targets -- -D warnings
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --lib
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test token_safety_sql_auth -- --test-threads=1
../../.venv/bin/pytest \
  tests/test_tiberius_token_safety_contract.py \
  tests/test_pyo3_build_contract.py -q
```

Expected: FOR BROWSE and same-client smoke pass; SQL_VARIANT/UDT are typed
errors with no Rust panic output; all static and vendored unit gates pass.

- [ ] **Step 6: Run root/legacy regressions**

Build the exact worktree extension, then run:

```bash
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked
../../.venv/bin/pytest tests/sql_auth_strict/test_results_strict.py -q
```

- [ ] **Step 7: Document, graph-review, commit and push**

Update `FASTMSSQL_PATCH.md` and `VERSION.md`, rebuild the graph and inspect
token/type impact and coverage. Then:

```bash
git diff --check
git add \
  VERSION.md \
  vendor/tiberius/FASTMSSQL_PATCH.md \
  vendor/tiberius/src/tds/codec/token/token_type.rs \
  vendor/tiberius/src/tds/codec/type_info.rs \
  vendor/tiberius/src/tds/stream/token.rs \
  scripts/sql_auth/run_all.sh \
  .github/workflows/rust-unit-tests.yml
git diff --cached --check
git commit -m "fix: make unsupported TDS tokens panic-free"
git push -u origin fix/tiberius-token-safety
```

---

## Task 4: Record RED Vendored-Tiberius Response and Privacy Contracts

**Branch:** `test/tiberius-response-events`

**Files:**

- Create: `vendor/tiberius/tests/response_api.rs`
- Create: `vendor/tiberius/tests/response_events_sql_auth.rs`
- Modify: `vendor/tiberius/src/tds/codec/rpc_request.rs` (tests only)
- Create: `tests/test_tiberius_response_privacy_contract.py`
- Modify: `scripts/sql_auth/run_all.sh`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: existing `ReceivedToken`, `QueryStream`, `Client` and RPC request
  encoder.
- Produces: RED contracts for `ResponseEvent`, `ResponseStream`,
  `RpcParameter`, named RPC encoding, signed status, DONE_COUNT, real
  SQL-auth token order and safe tracing.

- [ ] **Step 1: Create the isolated RED worktree**

From the primary checkout:

```bash
git worktree add \
  .worktrees/test-tiberius-response-events \
  -b test/tiberius-response-events \
  fix/tiberius-token-safety
```

- [ ] **Step 2: Add the database-independent public API contract**

In `vendor/tiberius/tests/response_api.rs`, import exactly:

```rust
use tiberius::{
    ResponseDoneKind, ResponseEvent, ResponseLength, ResponseStream,
    RpcParameter,
};
```

Statically require the exported stream/event types, public getters, safe
`Debug`, `Send` ownership and the public `RpcParameter` constructor. This
file must use no network, environment variable or conditional return, so it
can run honestly on Linux, macOS and Windows.

- [ ] **Step 3: Add exact named-RPC encoder unit contracts**

Extend only the existing `#[cfg(test)]` module in
`vendor/tiberius/src/tds/codec/rpc_request.rs`. Add:

```text
TIB-RESULT-007 -> tib_result_007_named_rpc_uses_us_varchar
TIB-RESULT-008 -> tib_result_008_by_ref_flag_is_output_only
```

Assert exact UTF-16 ProcName bytes, checked procedure/parameter length
overflow, and absence/presence of the `ByRefValue` bit. These additions are
test-only and do not change the runtime encoder on the RED branch.

- [ ] **Step 4: Add the real SQL-auth Tiberius contract**

`vendor/tiberius/tests/response_events_sql_auth.rs` reads the existing
`FASTMSSQL_SQL_AUTH_*` variables without printing values, connects with
Tokio compatibility I/O and creates UUID-derived fixtures with unconditional
cleanup. It implements:

```text
TIB-RESULT-001 -> tib_result_001_multiple_metadata_indices_and_rows
TIB-RESULT-002 -> tib_result_002_empty_metadata_is_an_event
TIB-RESULT-003 -> tib_result_003_done_count_distinguishes_absent_and_zero
TIB-RESULT-004 -> tib_result_004_info_fields_round_trip
TIB-RESULT-005 -> tib_result_005_return_status_is_signed
TIB-RESULT-006 -> tib_result_006_return_value_keeps_ordinal_name_type_and_value
TIB-RESULT-009 -> tib_result_009_query_stream_adapter_contract_is_unchanged
```

Assert exact event order, result indices, empty COLMETADATA,
`rows_affected=None` without DONE_COUNT, `rows_affected=Some(0)` with
DONE_COUNT, `-7i32` status, RETURNVALUE metadata and unchanged legacy
`QueryStream`. The empty-metadata case selects representative nullable
integer, float, MONEY/SMALLMONEY, UUID, character, Unicode, binary, decimal,
date/time/datetime2/datetimeoffset and XML declarations and asserts canonical
type names plus exact nullable/precision/scale/length fields. No case
conditionally returns or ignores missing configuration: absent credentials
are a setup failure.

- [ ] **Step 5: Add the privacy RED contract**

`tests/test_tiberius_response_privacy_contract.py` implements
`TIB-RESULT-010`. It reads the vendored source and asserts:

```python
FORBIDDEN_TRACE_PAYLOADS = (
    "?return_value",
    "?meta",
    "info.message",
    "err.message",
    "hex_dump",
    "dbg!",
    "procedure",
)
```

The test must scope `procedure` to tracing/event macro arguments rather than
rejecting the legitimate protocol field. It also requires manual safe
`Debug` implementations for `ResponseEvent`, `ResponseInfo` and
`ResponseReturnValue`, rejects derived value-bearing `Debug` on those types,
requires connection `Debug` to report only `buf_len`, and rejects raw
`SqlReadBytes::debug_buffer` output plus value-bearing COLMETADATA,
ENVCHANGE and LOGINACK traces.

- [ ] **Step 6: Register the required local lane and version entry**

Keep TIB-RESULT-001..010 in this subsystem design because the Python matrix
parser intentionally owns only one-segment pytest case IDs. In `run_all.sh`,
after Docker compose and provisioning, record a required
`tiberius-response-sql-auth` lane that runs exactly the real integration test
with `--test-threads=1`. Append one `VERSION.md` bullet stating that the RED
branch records protocol event and trace-privacy requirements and does not
change runtime behavior.

- [ ] **Step 7: Observe RED before implementation**

Run:

```bash
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --lib tib_result_
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test response_api
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test response_events_sql_auth -- --test-threads=1
../../.venv/bin/pytest tests/test_tiberius_response_privacy_contract.py -q
```

Load `.env.sql-auth.local` before the real integration command. Expected:
the named encoder panics at its current unimplemented branch, public and real
integration tests fail to compile because response symbols do not exist, and
the Python contract fails on existing value-bearing token trace calls. No
test is skipped.

- [ ] **Step 8: Self-review, commit and push the RED branch**

Run:

```bash
cargo fmt --manifest-path vendor/tiberius/Cargo.toml --check
git diff --check
git add \
  VERSION.md \
  scripts/sql_auth/run_all.sh \
  vendor/tiberius/src/tds/codec/rpc_request.rs \
  vendor/tiberius/tests/response_api.rs \
  vendor/tiberius/tests/response_events_sql_auth.rs \
  tests/test_tiberius_response_privacy_contract.py
git diff --cached --check
git commit -m "test: require complete TDS response events"
git push -u origin test/tiberius-response-events
```

Expected: only tests, test-runner/spec wiring and `VERSION.md` are committed;
RED remains reproducible and contains no runtime behavior change.

---

## Task 5: Implement Vendored-Tiberius Response Events and Direct RPC

**Branch:** `feat/tiberius-response-events`

**Files:**

- Create: `vendor/tiberius/src/tds/stream/response.rs`
- Modify: `vendor/tiberius/src/tds/stream.rs`
- Modify: `vendor/tiberius/src/lib.rs`
- Modify: `vendor/tiberius/src/client.rs`
- Modify: `vendor/tiberius/src/client/connection.rs`
- Modify: `vendor/tiberius/src/tds/stream/query.rs`
- Modify: `vendor/tiberius/src/tds/stream/token.rs`
- Modify: `vendor/tiberius/src/tds/codec/rpc_request.rs`
- Modify:
  `vendor/tiberius/src/tds/codec/token/token_done.rs`
- Modify:
  `vendor/tiberius/src/tds/codec/token/token_info.rs`
- Modify:
  `vendor/tiberius/src/tds/codec/token/token_return_value.rs`
- Modify: `vendor/tiberius/FASTMSSQL_PATCH.md`
- Modify: `.github/workflows/rust-unit-tests.yml`
- Modify: `tests/test_pyo3_build_contract.py`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: Task 4 RED tests.
- Produces: the stable Tiberius interfaces listed under “Stable Internal
  Interfaces”; existing `QueryStream` remains compatible.

- [ ] **Step 1: Create the feature worktree from RED**

```bash
git worktree add \
  .worktrees/feat-tiberius-response-events \
  -b feat/tiberius-response-events \
  test/tiberius-response-events
```

- [ ] **Step 2: Implement owned metadata and response event types**

Create `response.rs` with manual value-redacted `Debug` implementations.
Convert `TokenColMetaData` into `ResponseMetadata` once, preserving:

```rust
ResponseColumn {
    name,
    column_type,
    type_name,
    nullable,
    precision,
    scale,
    length,
}
```

Map `ColumnFlag::NullableUnknown` to `None`,
`ColumnFlag::Nullable` to `Some(true)`, otherwise `Some(false)`.
Convert NVARCHAR/NCHAR TDS byte lengths to declared UTF-16 code-unit
capacities, retain CHAR/VARCHAR and binary byte capacities, and map the PLP
sentinel to `ResponseLength::Max`. Map TIME/DATETIME2/DATETIMEOFFSET metadata
to fractional-seconds `scale` rather than `length`; preserve received
DECIMAL/NUMERIC precision and scale. Leave genuinely absent metadata as
`None`. Derive canonical SQL type names with a total match over every
`TypeInfo` accepted by the decoder; do not call `MetaDataColumn::fmt` or use a
wildcard `unreachable!`, because that legacy display path is incomplete for
valid MONEY and other metadata. Names are lowercase base types without
declaration suffixes; nullable variable-width integer, float, money and
datetime forms resolve from their received storage length. Unit-test the
total mapping in addition to the real TIB-RESULT-002 fixture.

- [ ] **Step 3: Implement `ResponseStream` token mapping**

`ResponseStream` owns:

```rust
token_stream: BoxStream<'a, crate::Result<ReceivedToken>>
columns: Option<Arc<Vec<Column>>>
result_set_index: Option<usize>
```

Its `Stream<Item = crate::Result<ResponseEvent>>` implementation:

1. increments result index only for COLMETADATA;
2. constructs each `Row` with the current shared columns and index;
3. maps DONE/DONEPROC/DONEINPROC to distinct `ResponseDoneKind`;
4. exposes row count only when DONE_COUNT is set;
5. maps INFO without logging message text;
6. maps RETURNSTATUS as signed `i32`;
7. maps RETURNVALUE without exposing value-bearing `Debug`;
8. preserves token order and propagates terminal driver errors.

An `ReceivedToken::Error` is not exposed as a successful response event and
does not make the adapter return before the existing token stream consumes
the server's trailing DONE state. Continue polling until the token stream
yields its stored terminal server error; this preserves the distinction
between a fully drained nonfatal SQL error and immediate protocol/I/O
uncertainty.

- [ ] **Step 4: Make legacy `QueryStream` an adapter**

Change `QueryStream` to wrap/filter `ResponseStream`. Its `poll_next` returns
only metadata and rows and ignores all other response events, retaining
`columns()`, `into_results()`, `into_first_result()`, `into_row()` and
`into_row_stream()` behavior.

- [ ] **Step 5: Add additive client entry points**

Implement `response_query`, `response_batch` and `response_rpc` with the
signatures fixed above. Existing `query` and `simple_query` call the first two
and wrap the result in `QueryStream`.

For `response_rpc`:

1. flush a previous response;
2. if reset is pending, send the direct batch
   `SET TRANSACTION ISOLATION LEVEL READ COMMITTED` with RESETCONNECTION and
   consume it completely;
3. derive exact type info from each optional `SqlParameterType`;
4. set `ByRefValue` only when `RpcParameter.by_ref` is true;
5. send `TokenRpcRequest::new(procedure, ...)`;
6. return a `ResponseStream` without pre-consuming response tokens.

- [ ] **Step 6: Encode names without panic**

In `rpc_request.rs`, encode ProcName as:

```text
u16 UTF-16 code-unit count
that many little-endian u16 code units
```

Use `u16::try_from(count)` before writing. Validate RPC parameter names with
`u8::try_from(utf16_count)` before writing. Return a typed encoding/protocol
error on overflow; do not use wrapping arithmetic, `unwrap`, `expect` or a
panic branch.

- [ ] **Step 7: Remove value-bearing token and connection diagnostics**

Replace COLMETADATA, row, INFO, ERROR, RETURNVALUE, ENVCHANGE and LOGINACK
payload traces with structural fields such as token kind, error number,
severity and row-column count. Change connection `Debug` from buffer hex to
`buf_len` and make `debug_buffer()` structural/no-payload. Do not emit message
text, metadata names, environment values, row/output values, procedure names,
raw TDS bytes or SQL.

- [ ] **Step 8: Wire database-independent vendored tests into hosted CI**

Retain the token-safety `--lib` command exactly once and add the public API
command on all three OS runners:

```bash
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --lib
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test response_api
```

Update `tests/test_pyo3_build_contract.py` to require both exact commands and
their position after raw Cargo tests. Explicitly reject
`response_events_sql_auth` in the generic hosted workflow. Keep checkout
credentials disabled and all existing bypass sentinels.

- [ ] **Step 9: Make focused RED tests green**

Run:

```bash
cargo fmt --check
cargo fmt --manifest-path vendor/tiberius/Cargo.toml --check
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --lib
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test response_api
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test token_safety_sql_auth -- --test-threads=1
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test response_events_sql_auth -- --test-threads=1
../../.venv/bin/pytest \
  tests/test_tiberius_response_privacy_contract.py \
  tests/test_pyo3_build_contract.py -q
```

Load `.env.sql-auth.local` before the real integration commands. Expected:
all TIB-SAFE and TIB-RESULT contracts, vendored unit tests and both static
contracts pass with no skipped SQL-auth case.

- [ ] **Step 10: Run Rust regressions**

Run:

```bash
cargo clippy --all-targets --all-features -- -D warnings
cargo clippy \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --all-targets -- -D warnings
cargo test --locked
```

Expected: zero warnings and zero failures.

- [ ] **Step 11: Document, graph-review, commit and push**

Document the response/RPC patch in `FASTMSSQL_PATCH.md`, append the exact
feature entry to `VERSION.md`, rebuild the graph, and run graph change/impact
analysis. Then:

```bash
git diff --check
git add \
  VERSION.md \
  vendor/tiberius \
  .github/workflows/rust-unit-tests.yml \
  tests/test_pyo3_build_contract.py
git diff --cached --check
git commit -m "feat: preserve complete TDS response events"
git push -u origin feat/tiberius-response-events
```

Expected: the feature commit descends from RED and no FastMssql Python API is
yet exposed.

---

## Task 6: Record RED Python Result-Stream and Bounded-Load Contracts

**Branch:** `test/resultsets-streaming`

**Files:**

- Create: `tests/test_result_stream_contract.py`
- Create: `tests/sql_auth_strict/test_resultsets_streaming.py`
- Create: `scripts/sql_auth/result_stream_stress.py`
- Create: `scripts/sql_auth/run_result_stream_stress.sh`
- Modify: `scripts/sql_auth/generate_report.py`
- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `scripts/sql_auth/run_all.sh`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify: `tests/test_pyo3_build_contract.py`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: Tiberius `ResponseStream`.
- Produces: RED public contracts for `ResultStream`, `ResultSet`,
  `ColumnMetadata`, `DoneResult`, `SqlMessage`, `ResultSummary`,
  `Connection.stream()` and `Connection.batch()`, including one SHA-bound
  RESULT-029 stress-evidence validator.

- [ ] **Step 1: Create the RED worktree**

```bash
git worktree add \
  .worktrees/test-resultsets-streaming \
  -b test/resultsets-streaming \
  feat/tiberius-response-events
```

- [ ] **Step 2: Add runtime/stub contract tests**

`tests/test_result_stream_contract.py` must require:

```python
PUBLIC_TYPES = (
    "ResultStream",
    "ResultSet",
    "ResultSummary",
    "ColumnMetadata",
    "DoneResult",
    "SqlMessage",
)
```

Assert every type is exported from the compiled module, wrapper package,
`__all__`, core stub and wrapper stub. Resolve both stubs and `py.typed`
relative to the imported `fastmssql.__file__`, not through a hard-coded
repository path; assert they share the imported package prefix. The same
contract therefore inspects tracked package files during an editable build
and the actual packaged copies in the isolated wheel. Assert `QueryStream`
documents and implements synchronous buffered behavior, while
`ResultStream`/`ResultSet` declare only async iteration. Parse both stubs with
`ast` and require the exact method signatures above and `buffer_size=64`.
Require `finish()` to return a `ResultSummary`, early async-context exit to
call full close, normal context exit to preserve completion, and synchronous
`iter()`/`next()` to remain absent.

- [ ] **Step 3: Add strict basic result cases**

Create real SQL-auth cases:

```text
RESULT-016 three result sets in exact order
RESULT-017 empty middle set with exact metadata
RESULT-018 genuine outer/inner async protocols
RESULT-019 slow consumer with bounded RSS
RESULT-020 normal EOF releases a resettable lease
RESULT-021 ResultSet.aclose skips only its set and preserves an empty next set
RESULT-025 concurrent-consumer and buffer validation
RESULT-028 DONE/zero-count/INFO separation
RESULT-029 validate the fresh required stress artifact and every invariant
```

Use `SET NOCOUNT OFF`, empty typed SELECTs, `PRINT`/low-severity RAISERROR,
DDL/DML row counts and post-response smoke queries. Check pool active count
while a response remains open and after normal EOF.
With a size-one pool, RESULT-020 proves ordinary normal EOF reuses the same
SPID through reset, while a successfully drained SQL batch classified by
`requires_connection_retirement` returns results but forces a different SPID
on the next checkout.
The empty-set metadata fixture includes NCHAR/NVARCHAR, CHAR/VARCHAR,
VARBINARY, MAX, DECIMAL and DATETIME2 columns and asserts the exact
code-unit/byte/precision/scale rules from the design.
The slow-consumer case records row count, exact payload bytes per row,
`buffer_size`, baseline/peak/final RSS and the resulting ceiling. It asserts
the event-count bound and never generalizes that measurement into chunked LOB
or arbitrary-row byte bounding.
The RESULT-029 pytest node does not rerun load. It requires the stress
artifact's Git SHA to equal `HEAD`, validates the exact required profile,
arithmetic/percentile/pool/RSS/CPU/ticker/smoke invariants and fails on a
missing, stale or failed artifact. This gives the central one-case/one-node
collector an exact owner for the external lane.

- [ ] **Step 4: Add the bounded load harness**

`result_stream_stress.py` accepts:

```text
--profiles OPERATIONS:CONCURRENCY[,OPERATIONS:CONCURRENCY]
--pool-size
--buffer-size
--rss-growth-limit-bytes
--metrics-output
--results-output
```

Validate operations 1..99,999, concurrency 1..500, pool size 1..500 and
buffer size 1..1,024. The required default profile is `1000:64`, pool size 8
and buffer size 8, with an RSS-growth limit of 134,217,728 bytes. Create
exactly `concurrency` long-lived worker coroutines and feed them through an
`asyncio.Queue(maxsize=2 * concurrency)`; never create one task per
operation. Record an operation's scheduled timestamp when its ID enters the
queue and its admitted timestamp after that worker acquires the concurrency
permit. Each operation streams one parameterized row containing its unique
input and `@@SPID`; workers consume every set/row, a sampler proves active
leases never exceed 8, an event-loop ticker proves scheduling progress, RSS
remains bounded and one final smoke query passes.

For every profile, write:

```text
total/succeeded/failed/timed_out and exact-once IDs
wall duration and operations/second
admitted driver latency p50/p95/p99/max
scheduled end-to-end latency p50/p95/p99/max
pool get_started/get_direct/get_waited/get_timed_out deltas
pool wait-time delta and peak pending_gets/active_connections
unique/max concurrent SQL SPIDs for the unique application name
baseline/peak/final RSS and Python process CPU seconds
SQL Server session cpu_time delta, reported separately
event-loop ticker count and maximum scheduling gap
post-load smoke result
```

Use `time.perf_counter_ns()` and an explicit nearest-rank percentile function;
do not add a statistics dependency or round before assertions. Measure
admitted latency only after the concurrency semaphore is acquired, and retain
scheduled latency separately so task-queue time is not mislabeled as driver
time. Snapshot `pool_stats()` before/after and during sampling. Sum
`sys.dm_exec_sessions.cpu_time` only for the run's unique application name;
label it SQL session CPU evidence, not whole-server saturation or a portable
benchmark.

This harness is the executable contract for `RESULT-029`.

The shell runner loads `.env.sql-auth.local` without printing secrets and
writes one fresh atomic
`.artifacts/sql-auth/result-stream-stress-metrics.json` containing schema
version, exact Git SHA, complete configuration and profiles. For exactly the
required `1000:64`, pool-size 8, buffer-size 8 gate it also always writes
`.artifacts/sql-auth/result-stream-load-results.json` in the existing matrix
result schema, with `RESULT-029` marked passed or failed and the real
duration/evidence path. A failure still writes a redacted FAIL record before
the process exits nonzero; stale evidence is removed before launch.

Allow explicit output overrides through
`FASTMSSQL_RESULT_STREAM_STRESS_METRICS_PATH` and
`FASTMSSQL_RESULT_STREAM_STRESS_RESULTS_PATH`. Accept larger profiles,
including `10000:128` and `99999:200`, only through
`FASTMSSQL_RESULT_STREAM_STRESS_PROFILES`; extended runs use distinct metric
paths and do not overwrite or manufacture the required RESULT-029 record.
An RSS override uses
`FASTMSSQL_RESULT_STREAM_STRESS_RSS_GROWTH_LIMIT_BYTES`; the effective value
is always written into the configuration and central validator evidence.

- [ ] **Step 5: Register exact SQL-auth cases and lanes**

Add RESULT-016..021, RESULT-025 and RESULT-028 to the central SQL-auth design.
Add `test_resultsets_streaming.py` to `strict_functional` and record the
stress runner as a separate required `result-stream-load` lane. Register
RESULT-029 in the same central design and make the report generator load the
fresh matrix result plus render the versioned result-stream profile metrics
in their own section. Extend `test_matrix_contract.py` with pass/fail,
redaction, stale-artifact and metric-rendering contracts without renaming
existing internal `upstream.*` artifacts. Run `result-stream-load` after
provisioning and before `strict`, so the RESULT-029 pytest evidence validator
never depends on a stale artifact. Keep RESULT-030 out of the ordinary
one-node central matrix: only Task 12's isolated installed-wheel lane may
claim it.

- [ ] **Step 6: Observe RED on the unchanged FastMssql API**

Build the exact worktree:

```bash
CARGO_TARGET_DIR=/private/tmp/fastmssql-cargo-target \
UV_CACHE_DIR=/private/tmp/fastmssql-uv-cache \
../../.venv/bin/maturin develop --release
```

Load the SQL-auth environment, then run:

```bash
../../.venv/bin/pytest tests/test_result_stream_contract.py -q
scripts/sql_auth/run_result_stream_stress.sh
../../.venv/bin/pytest tests/sql_auth_strict/test_resultsets_streaming.py -q
```

Expected: contracts fail because result types and methods are absent; no
failure is caused by missing credentials or an unhealthy container.

- [ ] **Step 7: Commit and push RED**

After `git diff --check` and a test-only self-review:

```bash
git add \
  VERSION.md \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  scripts/sql_auth/run_all.sh \
  scripts/sql_auth/generate_report.py \
  scripts/sql_auth/result_stream_stress.py \
  scripts/sql_auth/run_result_stream_stress.sh \
  tests/test_pyo3_build_contract.py \
  tests/test_result_stream_contract.py \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/sql_auth_strict/test_resultsets_streaming.py
git commit -m "test: require bounded async result streams"
git push -u origin test/resultsets-streaming
```

---

## Task 7: Implement Pooled Bounded Result Streaming

**Branch:** `feat/resultsets-streaming`

**Files:**

- Create: `src/result_types.rs`
- Create: `src/result_stream.rs`
- Modify: `src/connection.rs`
- Modify: `src/pool_manager.rs`
- Modify: `src/operation_metrics.rs`
- Modify: `src/types.rs`
- Modify: `src/lib.rs`
- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/__init__.pyi`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `README.md`
- Modify: `.github/workflows/rust-unit-tests.yml`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: Task 6 RED and vendored `ResponseStream`.
- Produces: pooled `Connection.stream()` and `Connection.batch()` plus all
  immutable result Python types.

- [ ] **Step 1: Create the feature worktree from RED**

```bash
git worktree add \
  .worktrees/feat-resultsets-streaming \
  -b feat/resultsets-streaming \
  test/resultsets-streaming
```

- [ ] **Step 2: Implement immutable public result types**

In `result_types.rs`, define frozen pyclasses with getters and safe reprs:

```text
ColumnMetadata  ordinal/name/type_name/nullable/precision/scale/length
DoneResult      kind/rows_affected/more_results/in_transaction/attention_acknowledged
SqlMessage      number/state/severity/message/server/procedure/line
ResultSummary   result_set_count/done/messages/return_status/output_parameters
```

Store internal tuples immutably. Return a fresh Python dictionary from
`output_parameters` on every access. Reprs contain type, counts, indices and
terminal state only.

- [ ] **Step 3: Build shared row metadata without a first row**

Add a `ColumnInfo` constructor that consumes `ResponseColumn` metadata.
`PyFastRow::from_tiberius_row` must reuse that exact `Arc<ColumnInfo>`.
Keep the existing first-row helper only for legacy `PyQueryStream`.

- [ ] **Step 4: Add movable metrics and owned pool guard support**

Expose `OperationObserver` with explicit success/error/cancel terminal
methods and cancellation-on-Drop. Reimplement `observe_operation` using the
observer so existing operations remain unchanged.

Add:

```rust
pub(crate) async fn acquire_owned_operation_guard(
    pool: &ConnectionPool,
    operation: OperationName,
    acquire_timeout: Duration,
) -> PyResult<PooledOperationGuard<'static>>;
```

It uses `acquire_owned_connection`, calls `prepare_for_checkout`, and keeps
the managed connection `Broken` from `begin_operation()` until the producer
finishes.

- [ ] **Step 5: Implement bounded producer channels**

`result_stream.rs` creates:

```text
mpsc::channel<ResponseEventEnvelope>(buffer_size)
mpsc::channel<ConsumerAck>(buffer_size)
watch channel for cancellation
watch channel for out-of-band terminal release
```

Every metadata, row and raw-summary envelope has one increasing `u64`
sequence. The producer uses `tokio::select!` so cancellation, receiver close,
ACKs, force and the absolute deadline remain observable even when the event
channel is full. An explicit outstanding-credit counter prevents sending the
next conversion envelope while `outstanding == buffer_size`; channel
capacity alone is not treated as the credit invariant. The producer therefore
never has more than `buffer_size` conversion events unacknowledged.

Run the complete producer body through the existing `catch_driver_panic`
boundary. A small outer supervisor owns terminal publication; it observes the
body result/panic only after the body future and its response/lease guards
have dropped. The supervisor retains `OperationObserver`, maps an unexpected
unwind to the existing value-free typed protocol error, records exactly one
outcome and publishes release exactly once. No lifecycle path aborts the
supervisor task; close/drop/force signal the body through its owned
cancellation channels.

At EOF, send the raw summary and wait for all ACKs. Then:

```text
all ACKs successful, normal SQL -> mark NeedsReset -> success metric -> release
all ACKs successful, retirement SQL -> keep Broken -> success metric -> retire
conversion failure  -> keep Broken -> error metric -> release permit/lease
full close/drop      -> keep Broken -> cancelled metric -> release permit/lease
driver error         -> apply existing disposition -> error metric -> release
timeout/force        -> keep Broken -> typed terminal error -> release
```

For a fully drained nonfatal server error whose existing disposition permits
reset, first close the event sender and wait for every outstanding
metadata/row ACK. Successful ACKs permit `NeedsReset`; conversion failure,
abandonment or deadline while waiting keeps the lease `Broken`. Immediate
protocol/I/O uncertainty remains broken and does not wait for conversion in
order to decide reuse.

Publish terminal release only after the response stream and owned resources
are dropped.

The cloneable, value-redacted terminal watch state is
`Pending | ReleasedSuccess | ReleasedFailure | ReleasedRetired`. It is set
exactly once and is not charged against event-channel capacity. On driver
error, timeout, force or cancellation, drop the event sender and resources
before publishing release, so a full event queue cannot block terminal
failure delivery. The consumer drains any already-queued envelopes in order;
event-channel EOF then resolves through the stored typed terminal state.

- [ ] **Step 6: Implement cancellation-safe Python iterators**

`ResultStream.__aiter__` and `ResultSet.__aiter__` return self.
`__anext__` uses a non-waiting single-consumer gate, then awaits `recv()`.
After a ready receive, metadata/row conversion, shared-state update and
`ConsumerAck::Converted` use no further await in that poll.

Both normal inner iteration and `ResultSet.aclose()` use the same
`pending_metadata: Option<ResponseEventEnvelope>` look-ahead slot.
Encountering metadata for the next set closes only the active set and leaves
that unacknowledged envelope for the next outer operation. Encountering the
terminal raw summary converts and ACKs it without another await, closes the
active set, then awaits the out-of-band terminal notification before
returning public EOF and exposing `response.summary`. Both
`ReleasedSuccess` and `ReleasedRetired` are successful here; the latter
confirms policy-driven physical retirement. `ReleasedFailure` re-raises its
stored typed error. Cancellation during that post-ACK release wait leaves a
retryable terminal state; a later `__anext__`, `finish()` or context exit
resumes the same wait.

Rules:

```text
outer next with an active set -> RuntimeError
second concurrent consumer    -> RuntimeError
set EOF                       -> StopAsyncIteration
response EOF + summary ACK    -> StopAsyncIteration
sync iter()/next()             -> TypeError by absent protocol
```

`ResultSet.aclose()` drains and ACKs rows belonging to its set as
`Discarded`. If it receives the next metadata to discover the boundary, it
stores exactly that one envelope in a private pending slot without
acknowledging it; the outer iterator consumes it next. A terminal summary is
converted and ACKed instead. `ResultStream.finish()` drains every event,
ACKs discarded metadata/rows and converts/ACKs the terminal summary.
`ResultStream.aclose()` closes the receiver, sends cancellation and waits for
terminal acknowledgement.

`finish()` may take ownership from a non-busy active set, marks that set
closed, drains normally and returns the immutable summary. It rejects a
simultaneous consumer, returns the existing summary when repeated after
completion, raises a stable local error after abort and re-raises the stored
typed error after failure. `__aexit__()` is a no-op after completion and
otherwise awaits full `aclose()`.

- [ ] **Step 7: Add pooled request startup**

Implement a plain-integer `BufferSize` extractor that rejects bool, non-int
and out-of-range values before parameter conversion/pool checkout.

`Connection.stream()`:

1. validates buffer size;
2. converts INPUT parameters under the GIL;
3. starts `OperationObserver(query)`;
4. admits the lifecycle operation;
5. ensures the pool and acquires an owned operation guard;
6. spawns one producer on the PyO3 Tokio runtime;
7. returns `ResultStream`.

`Connection.batch()` follows the same path without parameters and maps
metrics to `query_batch`. Both preserve
`requires_connection_retirement(sql)`.

- [ ] **Step 8: Correct Python exports, stubs and documentation**

Register all six new classes in `src/lib.rs`; re-export them from both Python
layers. Add exact methods/stubs. Rewrite every legacy `QueryStream`,
`query()` and `simple_query()` claim that says async or memory-efficient:
they are buffered-first-result compatibility APIs. Add one README enterprise
example using nested `async for` and `async with`.

- [ ] **Step 9: Make focused contracts green**

Build the exact worktree, load SQL-auth variables and run:

```bash
../../.venv/bin/pytest tests/test_result_stream_contract.py -q
scripts/sql_auth/run_result_stream_stress.sh
../../.venv/bin/pytest tests/sql_auth_strict/test_resultsets_streaming.py -q
```

Expected: all registered cases pass, 1,000 operations complete exactly once,
active leases never exceed 8, RSS stays under the recorded ceiling and the
post-load smoke query passes.

- [ ] **Step 10: Run compatibility and Rust checks**

Run:

```bash
../../.venv/bin/pytest tests/sql_auth_strict/test_results_strict.py -q
../../.venv/bin/pytest \
  tests/test_row_and_results.py \
  tests/test_simple_query.py \
  tests/test_for_iteration.py \
  tests/test_lazy_conversion.py -q
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked
```

Expected: legacy RESULT-001..015 and buffered Python behavior remain green.

- [ ] **Step 11: Add installed-wheel contract to hosted CI**

Extend the installed contract pytest list in
`rust-unit-tests.yml` with `tests/test_result_stream_contract.py`, and update
its static workflow test. Do not attempt SQL Server integration on generic
hosted runners.

- [ ] **Step 12: Graph-review, commit and push**

Rebuild the graph, inspect `detect_changes`, `get_affected_flows` and missing
tests for connection/result/metrics nodes. Then:

```bash
git diff --check
git add \
  VERSION.md README.md \
  src \
  python/fastmssql \
  .github/workflows/rust-unit-tests.yml
git commit -m "feat: add bounded async result streams"
git push -u origin feat/resultsets-streaming
```

Do not integrate this intermediate branch into the cumulative branch before
the lifecycle branch is green.

---

## Task 8: Record RED Result-Stream Lifecycle and Conversion-ACK Contracts

**Branch:** `test/resultstream-lifecycle`

**Files:**

- Create: `tests/sql_auth_strict/test_resultstream_lifecycle.py`
- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `scripts/sql_auth/run_all.sh`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: pooled basic `ResultStream`.
- Produces: RED cases RESULT-022..024, RESULT-026..027 and RESULT-031 for
  physical retirement, cancellation safety, conversion ACKs, lifecycle force
  and transaction ownership.

- [ ] **Step 1: Create RED worktree**

```bash
git worktree add \
  .worktrees/test-resultstream-lifecycle \
  -b test/resultstream-lifecycle \
  feat/resultsets-streaming
```

- [ ] **Step 2: Test full early close and object drops**

Use unique application names and DMVs to capture `@@SPID`/`connection_id`.
Assert `RESULT-022` and `RESULT-023`:

```text
ResultStream.aclose() waits until the original SPID disappears
dropped ResultStream eventually removes its SPID
dropped active ResultSet cancels the complete response
pool active count returns to zero
one later smoke query succeeds on a different synchronized connection
```

No fixed sleep is evidence; use bounded polling helpers.

- [ ] **Step 3: Test cancellation and terminal errors**

For `RESULT-024`, cancel a pending outer and inner `__anext__`, retain the
object and prove the same event can still be consumed. Separately test
cancellation followed by object drop. Use a large exact MONEY conversion
failure after wire read-ahead and a multi-statement midstream SQL error.
Assert typed classifications and exact connection-discard metadata. With a
size-one pool, pause before consuming a row queued ahead of a severity-16
server error and prove the connection remains active. After that row converts
and ACKs, the stored error must retain `connection_discarded=False`, reset
and reuse the same physical SPID; the conversion/protocol-uncertain path must
record discard and use a different later SPID. Both paths require a clean
later checkout.

- [ ] **Step 4: Test lifecycle shutdown**

With one live response:

1. graceful `disconnect()` remains pending;
2. normal `finish()` lets it complete without force;
3. a second case exceeds `shutdown_timeout`;
4. force cancels the producer, retires the SPID and raises the existing typed
   shutdown error with exact active-operation counts.

- [ ] **Step 5: Test transaction ownership**

For pooled and direct `Transaction`, implement `RESULT-027`:

```text
normal stream EOF returns to the prior transaction state
ResultSet.aclose reaches the next set and preserves transaction
normal EOF for retirement-classified SQL fails/retires the transaction
full response close/drop makes the transaction Failed
commit after failed stream is rejected
transport close rolls back an open SQL transaction
```

Run a competing transaction operation while a response is live and prove it
cannot interleave on the session.

- [ ] **Step 6: Test ACK-before-release directly**

Configure buffer size 4, receive metadata, and pause before consuming queued
rows. Assert `active_connections == 1` even after SQL Server has completed
the request. Consume/ACK all rows and summary; only then may active count
become zero. Trigger output/row conversion failure after server completion
and assert the broken-close counter increases rather than lease reuse.
Separately fill the event queue before a forced timeout/error, then prove the
producer releases without blocking on another event send, already-queued
rows retain order, channel EOF raises the stored typed terminal error and a
repeated terminal call returns the same classification.

- [ ] **Step 7: Register and observe RED**

Add the six case groups to the central SQL-auth design and strict runner.
Build/load environment, then run:

```bash
../../.venv/bin/pytest tests/sql_auth_strict/test_resultstream_lifecycle.py -q
```

Expected: transaction stream methods and at least the forced/drop/ACK
contracts fail on the incomplete feature branch; no setup failure or skip.

- [ ] **Step 8: Commit and push RED**

```bash
git diff --check
git add \
  VERSION.md \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  scripts/sql_auth/run_all.sh \
  tests/sql_auth_strict/test_resultstream_lifecycle.py
git commit -m "test: require fail-closed result stream lifecycle"
git push -u origin test/resultstream-lifecycle
```

---

## Task 9: Implement Fail-Closed Lifecycle and Transaction Streaming

**Branch:** `feat/resultstream-lifecycle`

**Files:**

- Modify: `src/result_stream.rs`
- Modify: `src/connection.rs`
- Modify: `src/transaction.rs`
- Modify: `src/lifecycle.rs`
- Modify: `src/operation_metrics.rs`
- Modify: `src/type_mapping.rs`
- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/__init__.pyi`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `README.md`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: Task 8 RED and basic producer.
- Produces: complete pooled/direct transaction streaming, forced shutdown,
  drop retirement and conversion-ACK ownership.

- [ ] **Step 1: Create feature worktree from lifecycle RED**

```bash
git worktree add \
  .worktrees/feat-resultstream-lifecycle \
  -b feat/resultstream-lifecycle \
  test/resultstream-lifecycle
```

- [ ] **Step 2: Add `TransactionResponseLease`**

In `transaction.rs`, acquire the session with
`Arc<AsyncMutex<_>>::lock_owned()`. The owned lease stores:

```rust
pub(crate) struct TransactionResponseLease {
    session: OwnedMutexGuard<TransactionSession>,
    previous_state: TransactionState,
    epoch: u64,
    operation: OperationName,
    retire_after_operation: bool,
    completed: bool,
}
```

It dereferences to the Tiberius client. `complete()` applies the normal
operation disposition and restores `previous_state` only when
`retire_after_operation` is false. A successfully drained classified request
still exposes its rows/summary, but `complete()` retires the transport and
transitions the transaction to `Failed`; later commit is rejected. `fail()`
marks the connection unusable, removes the transport and transitions to
`Failed`. Drop calls `fail()` unless terminal completion was recorded.
Closing only one `ResultSet` never calls `complete()` or releases this guard;
the complete response must reach normal EOF first.

- [ ] **Step 3: Add transaction `stream()` and `batch()`**

Perform parameter/buffer validation before session acquisition. Keep
`TransactionCancellationGuard` armed until the producer owns
`TransactionResponseLease`, then disarm it. The producer holds the owned
session mutex for the full response, so query/execute/commit/rollback cannot
interleave. Select on the transaction permit's force receiver and lifetime
deadline.

- [ ] **Step 4: Complete explicit/drop cancellation**

Implement `Drop` for `PyResultStream` and `PyResultSet`:

```text
closed/exhausted object -> no action
open response           -> send full cancellation
open active result set  -> send full cancellation
```

The producer detects event receiver closure even if cancellation notification
is lost. Explicit `aclose()` is idempotent and awaits resource-release ACK.
Cancelled `aclose()` can be called again to await the same terminal state.

- [ ] **Step 5: Complete conversion ACK failure handling**

Refactor cell conversion so a raw `ColumnData` and a row cell use the same
scalar mapping. On metadata/row/summary conversion failure, synchronously
`try_send(ConversionFailed)` before returning the typed error. The producer
drops the still-borrowed response first, leaves disposition broken, records
one error outcome and only then acknowledges terminal release.

- [ ] **Step 6: Preserve cancellation-safe receives**

Do not add an await between a successful event receive and state/ACK update.
Use `try_lock` for the single-consumer gate and `try_send` for ACKs; channel
capacity invariants make one ACK slot available for the sole consumer.
Unit-test sequence monotonicity, duplicate/stale ACK rejection and terminal
release notification exactly once, including failure delivery while the
event channel is full and cancellation/retry during the post-summary release
wait.

- [ ] **Step 7: Make lifecycle RED green**

Build/load SQL-auth and run:

```bash
../../.venv/bin/pytest tests/sql_auth_strict/test_resultstream_lifecycle.py -q
../../.venv/bin/pytest \
  tests/sql_auth_strict/test_lifecycle.py \
  tests/sql_auth_strict/test_operation_timeouts.py \
  tests/sql_auth_strict/test_transactions_strict.py \
  tests/sql_auth_strict/test_pool_observability.py -q
```

Expected: every lifecycle case passes with DMV and pool evidence; existing
shutdown/timeout/transaction behavior remains green.

- [ ] **Step 8: Re-run load and compatibility**

Run:

```bash
scripts/sql_auth/run_result_stream_stress.sh
../../.venv/bin/pytest \
  tests/test_result_stream_contract.py \
  tests/sql_auth_strict/test_resultsets_streaming.py \
  tests/sql_auth_strict/test_results_strict.py -q
```

- [ ] **Step 9: Run Rust, privacy and graph gates**

Run:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked
../../.venv/bin/pytest tests/test_tiberius_response_privacy_contract.py -q
uvx code-review-graph build
```

Review changed nodes, affected flows and test coverage around transaction
settlement, lifecycle force, operation metrics and pool disposition.

- [ ] **Step 10: Commit and push**

```bash
git diff --check
git add \
  VERSION.md README.md \
  src \
  python/fastmssql
git commit -m "feat: make result streams fail closed"
git push -u origin feat/resultstream-lifecycle
```

---

## Task 10: Record RED Stored-Procedure OUT and Return Contracts

**Branch:** `test/rpc-output-results`

**Files:**

- Create: `tests/sql_auth_strict/test_rpc_results.py`
- Modify: `tests/test_result_stream_contract.py`
- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `scripts/sql_auth/run_all.sh`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: complete result/lifecycle API and Tiberius direct RPC.
- Produces: RED `Connection.callproc()` and `Transaction.callproc()` contracts
  for RPC-001..011.

- [ ] **Step 1: Create RPC RED worktree**

```bash
git worktree add \
  .worktrees/test-rpc-output-results \
  -b test/rpc-output-results \
  feat/resultstream-lifecycle
```

- [ ] **Step 2: Add procedure fixture helpers**

Use per-test UUID-derived ordinary identifiers accepted by the closed grammar.
Create procedures through direct DDL and register each for unconditional
fixture cleanup. Helpers must not catch unexpected SQL/test exceptions.

- [ ] **Step 3: Add scalar and result cases**

Implement:

```text
RPC-001 no-parameter direct RPC and exact nonzero return status
RPC-002 INPUT/OUTPUT/INPUT_OUTPUT integer round trip
RPC-003 Unicode/binary/decimal/UUID/date/time/datetime2/datetimeoffset outputs
RPC-004 multiple and empty sets before terminal outputs
RPC-005 reordered NVARCHAR(MAX)/VARBINARY(MAX) outputs matched by ordinal/name
RPC-006 zero return status distinct from no status
```

Assert Python types, exact values, result-set order and summary availability
only after EOF.

- [ ] **Step 4: Add misuse, lifecycle and concurrency cases**

Implement:

```text
RPC-007 SQL error/cancel/early-close disposition
RPC-008 positional/named/direction/name grammar and injection safety
RPC-009 pooled/direct-transaction parity
RPC-010 concurrent calls bounded by pool sessions
RPC-011 recycled isolation restored to READ COMMITTED before named RPC
```

For RPC-008, cover both the procedure-component grammar and canonical named
parameter grammar, including optional single `@`, exact length boundaries,
`@@`, Unicode, whitespace, quoting and punctuation. For RPC-011, deliberately
set SERIALIZABLE on one lease, return it, call a procedure that reports
`transaction_isolation_level`, and require READ COMMITTED. Also prove a
punctuation-bearing procedure name is rejected before pool checkout.

- [ ] **Step 5: Extend static API contracts**

Require exact callproc signatures, output mapping key types, fresh-dict
behavior and direction documentation in both stubs and wrapper exports.

- [ ] **Step 6: Register and observe RED**

Add RPC-001..011 to the central SQL-auth design and strict runner. Build/load,
then:

```bash
../../.venv/bin/pytest \
  tests/test_result_stream_contract.py \
  tests/sql_auth_strict/test_rpc_results.py -q
```

Expected: missing `callproc()` and output behavior fail deterministically.

- [ ] **Step 7: Commit and push RED**

```bash
git diff --check
git add \
  VERSION.md \
  docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md \
  scripts/sql_auth/run_all.sh \
  tests/test_result_stream_contract.py \
  tests/sql_auth_strict/test_rpc_results.py
git commit -m "test: require direct RPC output results"
git push -u origin test/rpc-output-results
```

---

## Task 11: Implement Direct RPC, OUT Values and Return Status

**Branch:** `feat/rpc-output-results`

**Files:**

- Create: `src/procedure.rs`
- Modify: `src/result_stream.rs`
- Modify: `src/result_types.rs`
- Modify: `src/connection.rs`
- Modify: `src/transaction.rs`
- Modify: `src/parameter_conversion.rs`
- Modify: `src/py_parameters.rs`
- Modify: `src/type_mapping.rs`
- Modify: `src/lib.rs`
- Modify: `python/fastmssql/__init__.py`
- Modify: `python/fastmssql/__init__.pyi`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `README.md`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: Task 10 RED, `ResponseRequest::Procedure`, raw RETURNVALUE and
  RETURNSTATUS events.
- Produces: complete `callproc()` behavior for connection and transaction.

- [ ] **Step 1: Create RPC feature worktree from RED**

```bash
git worktree add \
  .worktrees/feat-rpc-output-results \
  -b feat/rpc-output-results \
  test/rpc-output-results
```

- [ ] **Step 2: Implement closed procedure and parameter-name validation**

`validate_procedure_name(&str) -> PyResult<String>` splits on `.` and requires
1..3 components. Require 1..128 ASCII characters per component and apply the
exact first/subsequent character grammar from the design.

`validate_parameter_name(&str) -> PyResult<(String, String)>` removes at most
one leading `@`, validates the 1..127-character canonical ASCII grammar,
returns `(canonical_name, wire_name_with_one_at)`, and rejects `@@`.
Both validators return value-free `ValueError` instances before checkout;
the procedure validator returns the unchanged validated string.

- [ ] **Step 3: Build owned procedure arguments**

Define:

```rust
pub(crate) struct ProcedureCall {
    procedure: String,
    arguments: Vec<tiberius::RpcParameter>,
    output_slots: Vec<OutputSlot>,
    return_slot: Option<OutputSlot>,
}

pub(crate) struct OutputSlot {
    wire_ordinal: Option<u16>,
    canonical_name: Option<String>,
    public_key: OutputKey,
    parameter_type: SqlParameterType,
}
```

Reject mixed modes, duplicate canonical names, expanded descriptors, invalid
direction/value combinations, absent output types and more than 2,100
encoded arguments. INPUT may infer type; OUTPUT and INPUT_OUTPUT require
explicit type. RETURN_VALUE accepts only omitted/INT type, is not encoded and
has at most one slot.

- [ ] **Step 4: Share exact input conversion**

Split typed scalar conversion into:

```rust
fn python_to_typed_fast_parameter_value(
    obj: &Bound<PyAny>,
    sql_type: &SqlParameterType,
    parameter_index: usize,
) -> PyResult<FastParameter>;
```

Keep direction rejection in query-specific descriptor handling.
Procedure-specific validation calls the same scalar function after validating
direction. Convert `FastParameter` into owned `ColumnData<'static>` plus
optional `SqlParameterType` without retaining Python values.

- [ ] **Step 5: Match output tokens by ordinal and validated name**

Collect RETURNVALUE tokens without assuming arrival order. Normalize token
names by removing one leading `@`; match both original wire ordinal and
canonical name when named. Reject duplicates, unknown tokens or mismatches as
protocol uncertainty with connection retirement. Store results in original
descriptor order.

- [ ] **Step 6: Convert output cells with row-equivalent semantics**

Expose a single `column_data_to_python` converter used by both row cells and
output cells. It must preserve:

```text
bool/int/float/str/bytes
decimal.Decimal
uuid.UUID
date/time/naive datetime
aware datetime with original offset
XML string
None
```

MONEY/SMALLMONEY, SQL_VARIANT, TVP, spatial, hierarchyid, CLR UDT and legacy
LOB outputs return deterministic unsupported conversion errors. Output
conversion happens before terminal summary ACK, so failure retires the lease.

- [ ] **Step 7: Add `callproc()` startup paths**

Connection and transaction validate/convert before network I/O, then create
`ResponseRequest::Procedure`. Map metrics to `query`. Reuse the same
producer, deadline, lifecycle, transaction lease and terminal-ACK logic as
stream/batch; do not duplicate a second response state machine.

- [ ] **Step 8: Complete summary and Python surface**

Populate `return_status` from the signed token even without a descriptor.
When a RETURN_VALUE descriptor exists, add the same integer under its
string/int public key. Populate only output-capable slots in a fresh
`output_parameters` mapping. Add wrapper/stub methods and one complete README
procedure example.

- [ ] **Step 9: Make RPC RED green**

Build/load, then run:

```bash
../../.venv/bin/pytest \
  tests/test_result_stream_contract.py \
  tests/sql_auth_strict/test_rpc_results.py -q
```

Expected: RPC-001..011 pass with exact types, values, reset isolation,
transaction parity and pool/session bounds.

- [ ] **Step 10: Run all affected SQL-auth tests**

Run:

```bash
../../.venv/bin/pytest \
  tests/sql_auth_strict/test_parameters_strict.py \
  tests/sql_auth_strict/test_type_mapping_strict.py \
  tests/sql_auth_strict/test_resultsets_streaming.py \
  tests/sql_auth_strict/test_resultstream_lifecycle.py \
  tests/sql_auth_strict/test_transactions_strict.py \
  tests/sql_auth_strict/test_operation_metrics.py \
  tests/sql_auth_strict/test_operation_timeouts.py -q
scripts/sql_auth/run_result_stream_stress.sh
```

- [ ] **Step 11: Run Rust, privacy and graph gates**

Run:

```bash
cargo fmt --check
cargo fmt --manifest-path vendor/tiberius/Cargo.toml --check
cargo clippy --all-targets --all-features -- -D warnings
cargo clippy \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --all-targets -- -D warnings
cargo test --locked
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --lib
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test response_api
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test token_safety_sql_auth -- --test-threads=1
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls \
  --test response_events_sql_auth -- --test-threads=1
../../.venv/bin/pytest tests/test_tiberius_response_privacy_contract.py -q
uvx code-review-graph build
```

Review response/procedure/type conversion impact and test coverage.

- [ ] **Step 12: Commit and push**

```bash
git diff --check
git add \
  VERSION.md README.md \
  src \
  python/fastmssql
git commit -m "feat: add direct RPC output results"
git push -u origin feat/rpc-output-results
```

---

## Task 12: Create and Verify the Exact Technical Merge

**Branch:** `verify/resultsets-streaming-merge`

**Files:**

- Merge reviewed histories; do not hand-edit production files.
- Generate ignored `.artifacts/sql-auth/*` evidence.
- Build ignored wheel/isolated-environment artifacts.

**Interfaces:**

- Consumes: `feat/rpc-output-results` and cumulative baseline `88ac9c0`.
- Produces: one history-preserving technical merge eligible for cumulative
  integration only if every local and hosted gate passes.

- [ ] **Step 1: Create verification worktree from the cumulative baseline**

```bash
git worktree add \
  .worktrees/verify-resultsets-streaming-merge \
  -b verify/resultsets-streaming-merge \
  88ac9c00d3d80edbb84377fc1f812070b5cf289b
cd .worktrees/verify-resultsets-streaming-merge
git merge --no-ff feat/rpc-output-results \
  -m "merge: verify bounded result streaming"
```

Expected: one merge commit preserves every design, RED and feature commit.

- [ ] **Step 2: Verify exact ancestry and repository boundary**

```bash
git merge-base --is-ancestor \
  test/tiberius-token-safety HEAD
git merge-base --is-ancestor \
  fix/tiberius-token-safety HEAD
git merge-base --is-ancestor \
  test/tiberius-response-events HEAD
git merge-base --is-ancestor \
  feat/tiberius-response-events HEAD
git merge-base --is-ancestor \
  test/resultsets-streaming HEAD
git merge-base --is-ancestor \
  feat/resultsets-streaming HEAD
git merge-base --is-ancestor \
  test/resultstream-lifecycle HEAD
git merge-base --is-ancestor \
  feat/resultstream-lifecycle HEAD
git merge-base --is-ancestor \
  test/rpc-output-results HEAD
git merge-base --is-ancestor \
  feat/rpc-output-results HEAD
git remote get-url origin
git remote get-url --push upstream
```

- [ ] **Step 3: Run the complete local orchestrator**

Run with the approved Docker MSSQL container:

```bash
scripts/sql_auth/run_all.sh
```

Required lanes include uv sync, exact maturin build, fmt, clippy, root Cargo,
vendored fmt/clippy/unit/public response tests, provision, the real
tiberius-token-safety-sql-auth and tiberius-response-sql-auth lanes, strict,
async, framework, resilience, load, result-stream-load,
original-local-regression and report. Every lane must have exit code zero
and required integration/pytest lanes must report zero skips.

- [ ] **Step 4: Run staged stream stress**

First required:

```bash
FASTMSSQL_RESULT_STREAM_STRESS_PROFILES=1000:64 \
FASTMSSQL_RESULT_STREAM_STRESS_POOL_SIZE=8 \
FASTMSSQL_RESULT_STREAM_STRESS_BUFFER_SIZE=8 \
FASTMSSQL_RESULT_STREAM_STRESS_METRICS_PATH=.artifacts/sql-auth/result-stream-stress-1000-metrics.json \
scripts/sql_auth/run_result_stream_stress.sh
```

After that profile is stable, run:

```bash
FASTMSSQL_RESULT_STREAM_STRESS_PROFILES=10000:128,99999:200 \
FASTMSSQL_RESULT_STREAM_STRESS_POOL_SIZE=32 \
FASTMSSQL_RESULT_STREAM_STRESS_BUFFER_SIZE=16 \
FASTMSSQL_RESULT_STREAM_STRESS_METRICS_PATH=.artifacts/sql-auth/result-stream-stress-extended-metrics.json \
scripts/sql_auth/run_result_stream_stress.sh
```

The 1,000 profile is the release gate fixed by the design. Record the larger
profiles as extended driver evidence; if host resource limits prevent them,
report the exact completed profile and resource boundary without weakening
the required 1,000 gate. Validate every required JSON field, arithmetic
invariant, exact-once ID, percentile ordering, pool bound, zero timeout/error,
RSS ceiling, CPU separation, ticker progress and post-load smoke result.

- [ ] **Step 5: Build and test an isolated installed wheel**

This step is the executable contract for `RESULT-030`.

```bash
CARGO_TARGET_DIR=/private/tmp/fastmssql-result-stream-wheel-target \
UV_CACHE_DIR=/private/tmp/fastmssql-result-stream-wheel-cache \
../../.venv/bin/maturin build --release --locked \
  --out .artifacts/result-stream-wheel
uv venv --python 3.13 .artifacts/result-stream-wheel-venv
uv pip install \
  --python .artifacts/result-stream-wheel-venv/bin/python \
  pytest==9.1.1 pytest-asyncio==1.4.0 psutil==7.2.2 \
  .artifacts/result-stream-wheel/*.whl
.artifacts/result-stream-wheel-venv/bin/python \
  -m pytest --noconftest tests/test_result_stream_contract.py -q
```

Then load SQL-auth variables and run the focused installed-wheel real-MSSQL
result, lifecycle and RPC files with `PYTHONPATH` absent:

```bash
env -u PYTHONPATH \
  .artifacts/result-stream-wheel-venv/bin/python -c \
  'import fastmssql, pathlib; print(pathlib.Path(fastmssql.__file__).resolve())'
env -u PYTHONPATH \
  .artifacts/result-stream-wheel-venv/bin/python -m pytest \
  tests/sql_auth_strict/test_resultsets_streaming.py \
  tests/sql_auth_strict/test_resultstream_lifecycle.py \
  tests/sql_auth_strict/test_rpc_results.py -q
shasum -a 256 .artifacts/result-stream-wheel/*.whl
```

Assert the printed import path is inside
`.artifacts/result-stream-wheel-venv`, every real-MSSQL case passes with zero
skips, and record the wheel filename plus SHA-256. Preserve the command log,
resolved import path, test counts, wheel filename/hash and exact HEAD as the
external RESULT-030 evidence consumed by Task 13; a source/editable pytest
run is never substituted.

- [ ] **Step 6: Run privacy, secret and artifact checks**

Scan tracked diffs, logs, JUnit, reports and stress JSON for known credential
sentinels and connection-string password forms. Assert no SQL/row/output
payload appears in result object reprs or driver-authored logs. Run
`git diff --check` and confirm the verification worktree is otherwise clean.

- [ ] **Step 7: Rebuild graph and review the exact merge**

```bash
uvx code-review-graph build
```

Run graph `get_minimal_context`, `detect_changes`, `get_affected_flows`,
impact radius and tests-for queries for response, connection, transaction,
lifecycle, pool, metrics, parameter and type-conversion nodes. Any missing
critical coverage becomes a separate RED/fix pair before proceeding.

- [ ] **Step 8: Push the verification branch only to the fork**

```bash
git push -u origin verify/resultsets-streaming-merge
git ls-remote --heads origin \
  refs/heads/verify/resultsets-streaming-merge
git remote get-url --push upstream
```

- [ ] **Step 9: Integrate by fast-forward into the cumulative fork branch**

In the primary cumulative worktree:

```bash
git switch test/sql-auth-validation
git merge --ff-only verify/resultsets-streaming-merge
git push origin test/sql-auth-validation
git rev-parse HEAD
git ls-remote --heads origin refs/heads/test/sql-auth-validation
git remote get-url --push upstream
```

Expected: exact local/fork cumulative SHA parity and upstream `DISABLED`.

- [ ] **Step 10: Verify hosted Linux/macOS/Windows and RustSec gates**

Read public GitHub Actions for the exact cumulative SHA. Require:

```text
Rust unit tests / Cargo on ubuntu-latest   success
Rust unit tests / Cargo on macos-latest    success
Rust unit tests / Cargo on windows-latest  success
Dependency security / RustSec audit        success
```

Each Cargo job must include raw Cargo, database-independent vendored
response unit/public tests, wheel build, isolated install and result-stream
static contract. Real Tiberius SQL Server evidence comes only from the local
Docker SQL-auth lane. Do not infer success from an older SHA.

---

## Task 13: Update the Live Audit from Exact Evidence

**Branch:** `docs/resultsets-streaming-status`

**Files:**

- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify: `docs/SQL_AUTH_TEST_MATRIX.md`
- Modify: `docs/SQL_AUTH_TEST_REPORT.md`
- Create: `docs/SQL_AUTH_RESULT_STREAM_STRESS_REPORT.md`
- Modify: `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes: exact cumulative local, Docker, wheel and hosted evidence.
- Produces: truthful live-audit status; no runtime or release change.

- [ ] **Step 1: Create the status worktree from exact cumulative HEAD**

```bash
git worktree add \
  .worktrees/docs-resultsets-streaming-status \
  -b docs/resultsets-streaming-status \
  test/sql-auth-validation
```

- [ ] **Step 2: Record exact evidence**

Update the audit with:

```text
original first-result/buffered reproduction
selected producer/channel/ACK architecture
every branch and exact SHA
multiple/empty result-set and metadata counts
DONE/INFO/return/output evidence
normal reset and every retirement SPID observation
pool/session/RSS/load metrics
transaction/timeout/lifecycle outcomes
installed-wheel paths and counts
hosted run IDs and exact SHA
remaining TVP/MONEY/SQL_VARIANT/bulk/framework work
```

Mark only the resultsets/streaming/RPC items proven by exact gates. Do not
claim the entire driver production-ready.

- [ ] **Step 3: Update the future original-repository PR roadmap**

List independently reviewable candidate slices without opening a PR:

```text
Tiberius response-event/named-RPC patch
FastMssql bounded ResultStream API
fail-closed lifecycle/transaction ownership
stored-procedure OUT/return API
```

Keep publication conditional on a future explicit user approval and fresh
original-repository ancestry/reproduction.

- [ ] **Step 4: Self-review documentation**

Run placeholder, secret, SHA, case-count, Markdown-fence and whitespace
checks. Rebuild the graph and confirm documentation-only risk/flow impact.

- [ ] **Step 5: Commit, push and merge status only in the fork**

```bash
git add \
  VERSION.md \
  docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md \
  docs/SQL_AUTH_TEST_MATRIX.md \
  docs/SQL_AUTH_TEST_REPORT.md \
  docs/SQL_AUTH_RESULT_STREAM_STRESS_REPORT.md \
  docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md
git commit -m "docs: record bounded result streaming evidence"
git push -u origin docs/resultsets-streaming-status
```

Then fast-forward the reviewed status branch into
`test/sql-auth-validation`, push origin, verify local/fork SHA parity and
reconfirm upstream push `DISABLED`.

---

## Final Completion Evidence for This Audit Subsystem

Do not call this subsystem complete until one evidence table contains actual
values for:

```text
design commit
plan/amendment commit
token-safety RED commit
token-safety fix commit
FOR BROWSE same-client query/smoke evidence
SQL_VARIANT/UDT typed-error and no-panic evidence
Tiberius RED commit
Tiberius feature commit
result-stream RED commit
result-stream feature commit
lifecycle RED commit
lifecycle feature commit
RPC RED commit
RPC feature commit
technical merge commit
status commit
focused pytest counts
complete strict/matrix counts and zero skips
legacy buffered-result counts
1,000-stream throughput/p50/p95/p99/concurrency/pool-wait/RSS/CPU/ticker metrics
extended 10,000/99,999 results or exact host boundary
physical SPID retirement observations
wheel filename/hash/isolated import path/test counts
Linux hosted run ID/result
macOS hosted run ID/result
Windows hosted run ID/result
RustSec run ID/result
local/fork cumulative SHA parity
upstream push URL = DISABLED
remaining risks/non-goals
```

Any missing required value remains an open audit item rather than an inferred
pass.
