# FastMssql PoolConfig Default Consistency Design

**Status:** Approved by Marcel Galea on 2026-07-25

**Source baseline:** `test/sql-auth-validation` at `f35176c`

**Target repository:** `https://github.com/galeamarcel/FastMssql.git`

**Publication boundary:** every branch, commit and hosted validation produced by
this candidate belongs only to Marcel Galea's fork. The original
`Rivendael/FastMssql` repository remains fetch-only; no upstream push, branch,
pull request or publication is authorized by this design.

## Decision summary

FastMssql will expose one canonical default pool profile everywhere:

| Setting | Canonical value |
| --- | ---: |
| `max_size` | `15` |
| `min_idle` | `3` |
| `max_lifetime_secs` | `1800` |
| `idle_timeout_secs` | `300` |
| `connection_timeout_secs` | `30` |
| `test_on_check_out` | `None` |
| `retry_connection` | `None` |

Both of these public construction paths must produce that profile:

```python
PoolConfig()
Connection(..., pool_config=None)
```

The correction is deliberately narrow. Explicit arguments remain explicit,
including `None`; the named and adaptive presets do not change; timeout API
expansion, pool backpressure, shutdown and observability remain separate
enterprise candidates.

## Problem

The same release currently has two different meanings for "default pool".

### Runtime evidence

On source baseline `f35176c`, direct runtime inspection produces:

| Surface | `max_size` | `min_idle` | `max_lifetime_secs` | `idle_timeout_secs` | `connection_timeout_secs` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `PoolConfig()` | `20` | `2` | `None` | `None` | `30` |
| `Connection(..., pool_config=None)` | `15` | `3` | `1800` | `300` | `30` |

The divergence is structural:

- `PoolConfig()` is populated by literal defaults on the PyO3 `#[new]`
  signature in `src/pool_config.rs`;
- `Connection(..., pool_config=None)` calls
  `pool_config.unwrap_or_default()` in `src/connection.rs`;
- `PyPoolConfig::default()` contains a different profile.

The discrepancy is observable before a network connection is made and is
also visible through `Connection.pool_stats()` after the pool is initialized.
It is therefore a public API contract defect, not merely an internal
implementation detail.

### Documentation and typing evidence

The public surfaces disagree with one another as well:

- the README feature summary advertises `max_size=20, min_idle=2`;
- the README pooling section says an omitted pool uses
  `max_size=15, min_idle=3`;
- `python/fastmssql/fastmssql.pyi` declares `15/3`, but declares
  `max_lifetime_secs=None` and `idle_timeout_secs=None`;
- focused Python and strict SQL-auth tests currently encode the direct
  constructor's `20/2/None/None/30` profile;
- PyO3 currently renders some `PoolConfig.__text_signature__` defaults as
  `...`, so introspection cannot reliably communicate the public defaults.

A production caller can consequently get different connection counts,
warm-up behavior and recycling settings by replacing an omitted argument with
an apparently equivalent `PoolConfig()`.

### Historical provenance

Upstream commit `2c5620a` ("Improve pool configs") intentionally changed
`PyPoolConfig::default()` from `10/2` to `15/3`, retained the managed
`1800/300/30` durations, and documented the omitted connection profile as
`15/3`. The PyO3 constructor defaults were left at
`20/2/None/None/30`.

The current original upstream branch contains the same inconsistency. This
candidate fixes it only on Marcel Galea's fork. The history supports treating
`15/3/1800/300/30` as the intended managed default rather than inventing a
third profile.

### Existing `None` translation

`PoolConfig` fields with a Python value of `None` are currently left unset by
FastMssql's `establish_pool()` builder path. FastMssql does not call the
corresponding bb8 setter in that case, so the current bb8 0.9.1 builder default
applies. For example, bb8 currently defaults maximum lifetime to 1800 seconds,
idle timeout to 600 seconds and connection acquisition timeout to 30 seconds.

This detail means the old stub phrases "`None = unlimited`" and
"`None = no timeout`" do not describe the current implementation. This
candidate will not silently redefine `None` to disable a bb8 policy. It will:

1. preserve the existing runtime translation of every explicit `None`;
2. describe it accurately as leaving the FastMssql override unset;
3. defer any redesign of `None`, acquisition timeouts or operation timeouts to
   a separately approved candidate.

## Goals

1. Make the no-argument Python constructor and the implicit connection path
   return the same canonical profile.
2. Keep the implicit `Connection(..., pool_config=None)` runtime behavior
   unchanged.
3. Centralize the executable Rust defaults so internal construction paths
   cannot drift silently.
4. Preserve explicit user overrides, including every accepted explicit
   `None`.
5. Preserve all named presets and `PoolConfig.adaptive()` exactly.
6. Expose a complete, readable Python signature with concrete default values.
7. Align runtime behavior, Python stubs, README guidance and `repr`.
8. Add deterministic pure-Python, Rust and real SQL-auth regression contracts.
9. Prove on a real Docker SQL Server that both default construction paths
   enforce the same pool bounds and release their sessions.
10. Keep strict matrix coverage and the full upstream regression suite green.

## Non-goals

This candidate does not:

- choose pool sizes dynamically for an application's workload;
- change any named preset or the adaptive sizing formula;
- change validation rules or exception classes;
- add query, execution, cancellation or transaction timeouts;
- redefine an explicit `None` as "disabled", "infinite" or zero;
- change bb8 itself or pin its internal defaults in FastMssql for explicit
  `None` values;
- add graceful shutdown, pool queues, metrics, tracing or TDS ATTENTION;
- change connection ownership, transaction leasing or cancellation retirement;
- change authentication, TLS or connection-string parsing;
- alter framework lifecycle behavior for FastAPI, Flask/WSGI or Flask/ASGI;
- perform a package version bump or publish a wheel;
- create a fork of bb8, Tiberius or PyO3;
- push to, open a pull request against, or otherwise modify the original
  FastMssql repository.

These boundaries keep the compatibility correction independently reviewable,
testable and revertible.

## Options considered

### Option A — Canonicalize on the implicit `PyPoolConfig::default()` profile

Use `15/3/1800/300/30` for both construction paths, preserve explicit
overrides and align every public contract.

**Selected.** This is the latest intentionally changed upstream default, is
already used by callers that omit `pool_config`, provides bounded recycling
independent of dependency-default drift, and matches the README's explicit
"if omitted" statement.

### Option B — Canonicalize on the direct `PoolConfig()` profile

Change `PyPoolConfig::default()` and the implicit connection path to
`20/2/None/None/30`.

**Rejected.** It would alter the runtime behavior of every caller that
currently omits `pool_config`, reverse the latest intentional upstream
default change, remove FastMssql's explicit managed lifetime/idle policy and
retain documentation drift.

### Option C — Preserve two defaults and document the distinction

Keep direct and implicit construction different, but explain that
`PoolConfig()` is a separate profile.

**Rejected.** The two forms are semantically presented as defaults, there is
no useful type-level distinction, and the behavior is a recurring source of
configuration mistakes.

### Option D — Bundle the correction with the full enterprise pool roadmap

Change defaults while also adding operation timeouts, lifecycle APIs,
backpressure, metrics, tracing and protocol cancellation.

**Rejected.** Those features have different failure modes and compatibility
surfaces. Bundling them would make a small reproducible defect harder to
review, bisect and upstream later.

## Approved public API contract

### No-argument constructor

After the fix:

```python
config = PoolConfig()

assert config.max_size == 15
assert config.min_idle == 3
assert config.max_lifetime_secs == 1800
assert config.idle_timeout_secs == 300
assert config.connection_timeout_secs == 30
assert config.test_on_check_out is None
assert config.retry_connection is None
```

`repr(config)` must report those same values.

### Implicit connection pool

Constructing a connection without a pool configuration remains supported:

```python
connection = Connection(connection_string)
```

After initialization, `connection.pool_stats()` must report:

```python
{
    "max_size": 15,
    "min_idle": 3,
    # remaining counters are runtime state
}
```

The connection path continues to use `PyPoolConfig::default()`. It must not
duplicate a second implicit profile in `connection.rs`.

### Explicit arguments

Every explicitly supplied valid argument must continue to win over the
default:

```python
PoolConfig(
    max_size=20,
    min_idle=2,
    max_lifetime_secs=None,
    idle_timeout_secs=None,
    connection_timeout_secs=30,
)
```

The example above recovers the old direct-constructor field values. Its
explicit `None` values continue to leave the corresponding FastMssql bb8
builder overrides unset; this candidate does not change that translation.

The same preservation applies to:

- `min_idle=None`;
- `connection_timeout_secs=None`;
- `test_on_check_out=True`, `False` or `None`;
- `retry_connection=True`, `False` or `None`;
- all valid non-default numeric values.

The exact operational meaning and future API shape of
`connection_timeout_secs=None` are explicitly deferred to
`feat/operation-timeouts`.

### Presets

These profiles remain byte-for-byte equivalent at the field level:

| Factory | `max_size` | `min_idle` | `max_lifetime_secs` | `idle_timeout_secs` | `connection_timeout_secs` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `PoolConfig.one()` | `1` | `1` | `1800` | `300` | `30` |
| `PoolConfig.low_resource()` | `3` | `1` | `900` | `300` | `15` |
| `PoolConfig.development()` | `5` | `1` | `600` | `180` | `10` |
| `PoolConfig.high_throughput()` | `25` | `8` | `1800` | `600` | `30` |
| `PoolConfig.performance()` | `30` | `10` | `7200` | `1800` | `10` |

`PoolConfig.adaptive(concurrent_workers)` retains its existing formula and
boundary behavior.

### Validation and errors

No new exception type or error path is introduced. Existing validation
remains authoritative:

- `max_size >= 1`;
- when present, `min_idle <= max_size`;
- when present, `max_lifetime_secs > 0`;
- when present, `idle_timeout_secs > 0`;
- when present, `connection_timeout_secs >= 1`.

Validation errors remain immediate `ValueError` instances. No exception may be
swallowed or replaced by an implicit fallback.

## Approved implementation architecture

### Canonical Rust constants and helper

`src/pool_config.rs` will define private, typed constants for all seven
canonical fields. A private constructor/helper will build the canonical
`PyPoolConfig` from those constants. `impl Default for PyPoolConfig` will call
that helper rather than repeat the values.

This is the executable source of truth for internal Rust construction.
`Connection(..., pool_config=None)` therefore inherits the same canonical
profile without new logic in `connection.rs`.

### PyO3 constructor defaults

The PyO3 `#[new]` signature will use the canonical constants as valid Rust
default expressions. Omitted Python arguments must therefore enter
`PyPoolConfig::new()` as the canonical values, while an explicit Python
`None` must remain distinguishable for optional fields.

PyO3 does not render arbitrary Rust expressions such as `Some(CONSTANT)` as a
concrete Python default automatically. The class currently exposes `...` for
some such values. The implementation will provide an explicit public text
signature:

```text
(max_size=15, min_idle=3, max_lifetime_secs=1800,
 idle_timeout_secs=300, connection_timeout_secs=30,
 test_on_check_out=None, retry_connection=None)
```

Both `PoolConfig.__text_signature__` and `inspect.signature(PoolConfig)` must
remain readable and must not contain `...`/`Ellipsis`.

The text signature necessarily mirrors values for Python metadata, so a
static/runtime contract will compare it with an actual `PoolConfig()` object.
This prevents metadata drift while preserving one executable Rust default
profile.

### Stubs and README

`python/fastmssql/fastmssql.pyi` will declare:

```python
def __init__(
    self,
    max_size: int = 15,
    min_idle: Optional[int] = 3,
    max_lifetime_secs: Optional[int] = 1800,
    idle_timeout_secs: Optional[int] = 300,
    connection_timeout_secs: Optional[int] = 30,
    test_on_check_out: Optional[bool] = None,
    retry_connection: Optional[bool] = None,
) -> None: ...
```

Using `Optional[int]` where `None` is accepted keeps the stub aligned with the
existing runtime API. Descriptions of `None` will say that the FastMssql
override is left unset; they will not promise an infinite timeout.

The README feature summary, pooling section, constructor example and omitted
pool description will all state the same canonical profile. Examples that
intentionally demonstrate custom values remain custom examples and must not be
mislabelled as defaults.

## Compatibility and migration

### Unchanged callers

Callers that omit `pool_config` retain their current
`15/3/1800/300/30` behavior. Each explicitly supplied argument retains its
value; only omitted constructor arguments receive the corrected defaults.
Named and adaptive preset callers are unchanged.

### Changed callers

Only callers that explicitly construct `PoolConfig()` while omitting one or
more constructor arguments receive the corrected defaults for those omitted
arguments. A completely empty constructor changes from:

```text
20 / 2 / None / None / 30
```

to:

```text
15 / 3 / 1800 / 300 / 30
```

This can reduce the pool ceiling by five connections, warm one additional
idle connection and make FastMssql explicitly request the managed lifetime
and idle timeout already used by the implicit connection path.

The old field profile is recoverable with explicit arguments shown in the
public contract section. The eventual release notes must identify this as a
bug fix with an explicit migration snippet; this design does not select or
publish a release version.

### Resource and concurrency implications

The canonical `max_size=15` is a per-`Connection` pool ceiling, not a
process-wide or SQL Server-wide ceiling. An application that constructs
multiple persistent `Connection` objects can still open up to the sum of
their pool ceilings.

`min_idle=3` is likewise per pool. Framework guidance remains:

- use one persistent application-owned pool per database identity/workload
  where practical;
- avoid constructing one pool per request;
- size explicit pools from measured concurrency and SQL Server capacity;
- use the approved framework lifecycle patterns documented elsewhere.

This candidate proves consistency; it does not claim that 15 is optimal for
every deployment.

## Test-first design

Implementation follows a strict RED/GREEN sequence. Production code must not
be changed until the committed tests fail for the expected default mismatch.

### RED contract

The test branch will add or strengthen contracts for:

1. `PyPoolConfig::default()` returning the exact canonical Rust profile.
2. `PoolConfig()` exposing all seven canonical Python values.
3. `repr(PoolConfig())` containing those values.
4. `PoolConfig.__text_signature__` and `inspect.signature(PoolConfig)`
   containing concrete values and no ellipsis.
5. Explicit `None` and non-default values retaining their current field
   semantics.
6. Every named/adaptive preset retaining its existing values.
7. Stub defaults/types and README default statements matching the runtime
   contract.
8. The implicit `Connection` path and explicit `PoolConfig()` path reporting
   the same real pool configuration.

The RED evidence must identify the assertions that fail because the direct
constructor still returns `20/2/None/None/30`. It must not rely on an expected
exception being swallowed, a skipped test or a missing SQL Server.

### Pure Python placement

Existing focused tests in:

- `tests/test_pool_config.py`;
- `tests/test_pool_config_validation.py`;

will be strengthened where they already own the relevant contract. A focused
metadata/static contract may be added where needed, but duplicate tests with
different expected defaults are forbidden.

The current pure baseline is:

```text
89 passed, 16 deselected
```

for those two modules under `-m "not integration"`.

### Real SQL-auth placement

Strict case `POOL-001` in `tests/sql_auth_strict/test_pool.py` will be
strengthened rather than allocating a new specification ID. It must prove
against the dedicated Docker SQL Server, using MSSQL authentication:

1. an explicit no-argument `PoolConfig()` connects with `15/3`;
2. a `Connection` with omitted `pool_config` connects with the same profile;
3. pool stats satisfy the existing invariants;
4. connection count is never below the established `min_idle` after warm-up
   and never above `max_size`;
5. a concurrency burst larger than 15 is bounded by the pool ceiling;
6. parameterized queries return the expected values;
7. unique application names isolate server-side session observations;
8. every observed application session disappears after disconnect within a
   bounded polling deadline.

Assertions must tolerate valid asynchronous pool timing. They may assert
ranges and eventual state, but must not assume an incidental exact session
count before warm-up/readiness has completed.

The approved local baseline for the 16 focused integration tests is:

```text
16 passed, 89 deselected
```

against the healthy `fastmssql-sql-auth-dev` container. A sandbox-denied
localhost socket is an execution-environment error, not a driver failure; the
SQL-auth lane must run with the already approved localhost/Docker permission
and must still fail normally for a genuine connection or assertion error.

### Strict matrix accounting

Strengthening `POOL-001` does not add a new approved SQL-auth specification
ID. The exact specification registry remains `285/285`.

The current full strict executable baseline is `295/295 PASS`; the current
applicable upstream regression baseline is `901/901 PASS`. If a new
non-matrix contract test intentionally increases an executable count, the
implementation report must record both the old baseline and the new exact
count. Passing percentages without exact counts are insufficient.

## Branch and commit discipline

The work is isolated into four branches/worktrees:

1. `docs/pool-config-default-consistency-design`
   - this approved design only;
2. `test/pool-config-default-consistency`
   - deterministic RED tests and reproduction evidence only;
3. `fix/pool-config-default-consistency`
   - the minimum GREEN runtime, stub and README correction;
4. `docs/pool-config-default-consistency-status`
   - final audit/live-status evidence after all gates pass.

Each branch starts from the exact reviewed predecessor commit. Tests are
committed while still RED before the fix branch is created. Unrelated dirty
worktree content must not be staged.

After review, completed commits may be integrated into the cumulative
`test/sql-auth-validation` branch. Every push target must be rechecked as
`origin = galeamarcel/FastMssql`; `upstream` must continue to report push URL
`DISABLED`.

## Verification gates

The GREEN candidate is not complete until fresh evidence passes for all
applicable gates:

### Rust and extension

```text
cargo build --locked
cargo test --locked
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo audit --deny warnings
maturin develop --release
```

Rust test counts must be reported exactly. The current baseline is `13/13`.

### Focused contracts

```text
focused pure PoolConfig tests
focused real SQL-auth PoolConfig tests
PyO3 manifest/build contract
stub and signature contract
```

The existing PyO3 contract baseline is `6/6`. Pool tests must use the rebuilt
extension from the candidate source, not a stale extension from another
worktree.

### Full regressions

```text
full strict SQL-auth suite
exact SQL-auth specification registry
applicable upstream SQL-auth regression
Ruff
compileall
zero leaked candidate application sessions
```

Expected no-regression baselines are `295/295` strict executable tests,
`285/285` specification IDs and `901/901` upstream tests, adjusted upward only
for deliberately added executable tests and reported exactly.

### Hosted platform gate

The final cumulative commit must pass the existing required Linux, macOS and
Windows hosted gate. The gate must exercise raw Cargo build/test separation
and the Python extension contract. No platform may be inferred green from a
different commit SHA.

### Git/publication safety

Before every push and in the final handoff:

```text
origin push URL = https://github.com/galeamarcel/FastMssql.git
upstream push URL = DISABLED
branch parity with its fork branch = 0 / 0
no branch or pull request created on Rivendael/FastMssql
```

## Acceptance criteria

The candidate is accepted only when:

1. `PoolConfig()` and `PyPoolConfig::default()` expose the exact canonical
   seven-field profile.
2. `Connection(..., pool_config=None)` remains behaviorally unchanged and
   matches `PoolConfig()`.
3. every explicit override and every preset remains unchanged.
4. Python introspection, stubs, README and `repr` agree with runtime values.
5. explicit `None` translation remains unchanged and is no longer
   misdocumented as necessarily infinite.
6. the RED commit fails for the intended mismatch and the GREEN commit passes
   the same tests without weakening assertions.
7. real MSSQL-auth tests prove bounded concurrency and complete session
   cleanup.
8. all local and hosted verification gates pass at the exact candidate SHA.
9. the production-readiness audit records the defect, branch/commit evidence,
   exact counts and remaining limitations.
10. all commits and pushes exist only on Marcel Galea's fork.

## Rollback

The change is independently revertible. Reverting the GREEN commit restores
the direct constructor's historical defaults while leaving the test and
design evidence available for diagnosis.

Application-level rollback does not require a package downgrade: callers can
always provide an explicit `PoolConfig` profile. No schema, wire-protocol or
persisted-data migration is involved.

## Follow-on enterprise sequencing

After this foundational consistency candidate is closed, the broader audit
item `feat/timeouts-lifecycle-observability` remains decomposed into separately
designed candidates:

1. `feat/operation-timeouts`;
2. `feat/graceful-shutdown-lifecycle`;
3. `feat/pool-backpressure`;
4. `feat/pool-metrics`;
5. `feat/tracing-observability`;
6. `feat/tds-attention-cancellation`.

Each requires its own approved design, RED reproduction, GREEN branch and
real SQL-auth verification. None is implicitly authorized for upstream
publication by approval of this document.
