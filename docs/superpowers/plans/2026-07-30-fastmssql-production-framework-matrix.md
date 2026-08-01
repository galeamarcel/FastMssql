# FastMssql Production Framework Process Matrix Implementation Plan

> **Execution workflow:** use `superpowers:executing-plans` task by task,
> `superpowers:test-driven-development` for the matrix RED/feature ancestry,
> `superpowers:systematic-debugging` for every unexpected result and
> `superpowers:verification-before-completion` before every success claim.
> Implementation stays inline in the primary agent; do not delegate it.
> Checkboxes are live execution state, not retrospective decoration.

**Goal:** close feature 21 of the production-readiness audit by executing
FastAPI and Flask through real Uvicorn/Gunicorn processes from an isolated
candidate wheel, with deterministic post-fork pool ownership, cross-process
connection budgets, real-network lifecycle/fault/streaming behavior,
fixed-worker load through 99,999 operations and exact platform claims.

**Architecture:** retain the current in-process framework suite. Add a copied
test application plus a portable Python process orchestrator. The
orchestrator starts external server process groups, observes SQL Server from
the installed FastMssql wheel, drives real loopback HTTP, records one
privacy-safe exact-SHA JSON artifact and tears down every resource. Canonical
pytest cases validate that artifact. Uvicorn `asyncio` is cross-platform;
`uvloop` and Gunicorn are POSIX-only. Each worker constructs/connects its pool
after spawn/fork, and deployment configuration enforces
`workers * pool_max <= global budget`.

**Tech stack:** Python 3.13 verification environment, FastMssql PyO3 ABI3
wheel, Rust 1.94, Docker SQL Server 2022, hosted Windows SQL Server 2022
Express, FastAPI 0.139.2, Flask 3.1.3, asgiref 3.12.1, Uvicorn 0.51.0,
Gunicorn 26.0.0, uvicorn-worker 0.4.0, uvloop 0.22.1, HTTPX 0.28.1, psutil,
pytest, maturin, GitHub Actions and code-review-graph.

## Global constraints

- Work only in `https://github.com/galeamarcel/FastMssql.git`.
- Keep `Rivendael/FastMssql` fetch-only with push URL `DISABLED`.
- Start RED work from the exact committed plan on
  `docs/production-framework-matrix-design`.
- Preserve public ancestry:
  `test/production-framework-matrix` must be an ancestor of
  `feat/production-framework-matrix`.
- Preserve any discovered defect's focused `test/<defect>` as an ancestor of
  `fix/<defect>` and of the final technical candidate.
- Never squash away an observed RED boundary.
- Use project-local ignored worktrees under `.worktrees/`.
- Use `apply_patch` for text changes.
- Update `VERSION.md` in every repository-modifying commit.
- Keep displayed package metadata at `0.7.7`.
- Keep framework/server packages development-only.
- Keep SQL-auth credentials only in environment variables.
- Never persist credentials, connection strings or raw environment dumps.
- Do not put a credential in a process command line.
- Use real loopback TCP HTTP. `ASGITransport` and Flask's test client remain
  only in the pre-existing regression suite.
- Required SQL tests use Microsoft SQL Server with SQL authentication.
- A required runnable profile may not skip, swallow an exception,
  `continue-on-error`, ignore child exit status or accept partial worker
  readiness.
- Gunicorn/uvloop on Windows is a validated N/A, not a skipped POSIX test.
- Do not infer a hosted macOS SQL-auth claim from its structural server job.
- Do not infer an unlimited capacity claim from 99,999 correct operations.
- Do not publish a wheel, release or package.
- Do not push or open a pull request against the original repository.

## Branch topology

| Stage | Branch | Required ancestry/result |
| --- | --- | --- |
| design | `docs/production-framework-matrix-design` | design commit, then plan commit |
| RED | `test/production-framework-matrix` | focused test-only failing contract |
| feature | `feat/production-framework-matrix` | descendant of RED; harness/deps/workflows |
| defect RED | `test/<specific-defect>` | created only if behavior exposes a driver defect |
| defect fix | `fix/<specific-defect>` | descendant of focused defect RED |
| technical verification | `verify/production-framework-matrix` | history-only cumulative candidate |
| status | `docs/production-framework-matrix-status` | reports/audit after exact hosted gates |

## File responsibility map

### Test application

- `tests/production_framework/__init__.py`
  - package marker only.
- `tests/production_framework/app.py`
  - environment validation;
  - worker-local FastMssql lifecycle;
  - FastAPI, Flask WSGI and adapted Flask app objects;
  - SQL-auth routes;
  - atomic ready/shutdown records;
  - bounded admission and privacy-safe error payloads.
- `tests/production_framework/gunicorn_conf.py`
  - `preload_app = False`;
  - explicit bind/workers/threads/timeouts from validated environment;
  - Flask `post_worker_init` and `worker_exit`;
  - no credential logging.

### Orchestration and reports

- `scripts/sql_auth/production_framework_matrix.py`
  - schema/versioned CLI;
  - exact wheel/import provenance;
  - platform/profile expansion;
  - isolated app copy;
  - external process supervision;
  - SQL observer;
  - HTTP scenarios;
  - fixed-worker load;
  - resource sampling;
  - redaction and atomic JSON output.
- `scripts/sql_auth/run_production_framework_matrix.sh`
  - existing SQL-auth environment/container boundary;
  - exact wheel build/install;
  - required and explicit extended profiles;
  - result paths and cleanup.
- `scripts/sql_auth/generate_report.py`
  - load and render production-framework metrics.
- `scripts/sql_auth/run_all.sh`
  - required production-framework lane.

### Contracts

- `tests/test_production_framework_contract.py`
  - database-independent dependency, source, command, platform, schema,
    workflow and fail-closed contracts;
  - safe under `pytest --noconftest` from the installed-wheel venv.
- `tests/sql_auth_strict/test_production_framework_matrix.py`
  - one canonical evidence validator for each `FRAME-027` through
    `FRAME-054`;
  - no server restart per test.
- `tests/sql_auth_strict/test_matrix_contract.py`
  - runner/report/case/branch/workflow inclusion and no-swallow contracts.
- `tests/sql_auth_strict/conftest.py`
  - only artifact/case recorder extensions that are actually required.
- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
  - canonical case descriptions.

### Dependencies and CI

- `pyproject.toml`
  - `production_framework` marker;
  - locked server dependencies in the development group;
  - Windows marker for POSIX-only packages.
- `uv.lock`
  - exact resolved packages and markers.
- `.github/workflows/production-framework-matrix.yml`
  - cross-platform wheel/server gates;
  - Linux and Windows real SQL-auth jobs.
- `.github/workflows/rust-unit-tests.yml`
  - exact candidate branch triggers and wheel contract inclusion.
- `.github/workflows/dependency-security.yml`
  - exact candidate branch triggers.

### Live evidence

- `docs/SQL_AUTH_TEST_MATRIX.md`
- `docs/SQL_AUTH_TEST_REPORT.md`
- `docs/validation/fastmssql-production-framework-matrix-report.md`
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
- `VERSION.md`

---

## Task 1: commit the approved design and executable plan

**Branch:** `docs/production-framework-matrix-design`

**Files:**

- create:
  `docs/superpowers/specs/2026-07-30-fastmssql-production-framework-matrix-design.md`
- create:
  `docs/superpowers/plans/2026-07-30-fastmssql-production-framework-matrix.md`
- modify: `VERSION.md`

- [x] **Step 1.1: graph-first baseline and fork boundary**

Required evidence:

```bash
uvx code-review-graph build
git status --short --branch
git rev-parse HEAD
git remote get-url --push origin
git remote get-url --push upstream
```

Expected:

- graph and HEAD agree on
  `fabd073cdd585fb35309ae79c1b163e234f67979`;
- branch is `docs/production-framework-matrix-design`;
- origin is Marcel Galea's fork;
- upstream push is `DISABLED`.

- [x] **Step 1.2: self-review the design**

Verify:

- all `FRAME-027` through `FRAME-054` appear exactly once in the required
  case section;
- Markdown fences are balanced;
- no placeholder remains;
- real SQL-auth secrets do not match either changed file;
- `git diff --check` passes;
- code-review-graph reports no affected code flow or test gap.

- [x] **Step 1.3: commit design separately**

```bash
git add \
  VERSION.md \
  docs/superpowers/specs/2026-07-30-fastmssql-production-framework-matrix-design.md
git commit -m "docs: design production framework matrix"
```

- [x] **Step 1.4: self-review and commit this plan**

Verify:

- every task names its branch and files;
- every behavior-changing task starts with a failing test;
- required/extended profiles are never conflated;
- Windows/macOS claim boundaries match the design;
- every repository-changing stage updates `VERSION.md`;
- original-repository publication remains forbidden;
- no placeholder/secret/whitespace error exists.

Then:

```bash
git add \
  VERSION.md \
  docs/superpowers/plans/2026-07-30-fastmssql-production-framework-matrix.md
git commit -m "docs: plan production framework matrix"
git push -u origin docs/production-framework-matrix-design
```

Record the already-known design commit in this plan commit. Record the
resulting plan commit in Task 2's `VERSION.md` entry; a commit cannot embed
its own final hash without changing that hash.

---

## Task 2: create the focused production-matrix RED

**Branch:** `test/production-framework-matrix`

**Start:** exact plan commit from Task 1

**Files:**

- create: `tests/test_production_framework_contract.py`
- create: `tests/sql_auth_strict/test_production_framework_matrix.py`
- modify: `tests/sql_auth_strict/test_matrix_contract.py`
- modify: `VERSION.md`

- [x] **Step 2.1: create isolated RED worktree**

Create the branch/worktree from the exact plan SHA. Rebuild the graph and
verify fork/HEAD/worktree cleanliness before editing.

- [x] **Step 2.2: write database-independent failing contracts**

`tests/test_production_framework_contract.py` initially requires:

- all three application/runner files;
- exact dependency names and platform markers;
- no runtime FastMssql dependencies;
- current `uvicorn_worker.UvicornWorker` path;
- no `uvicorn.workers`, `--preload`, gevent, eventlet or arbitrary shell
  execution;
- schema-versioned JSON output;
- fixed profile worker counts `(1, 2, 4, 8)`;
- an explicit Windows N/A reason for Gunicorn/uvloop;
- no `pytest.skip`, `continue-on-error`, `|| true` or broad exception-to-PASS
  path;
- an isolated-wheel command with no repository driver path;
- a fixed-worker load generator, not one task per operation;
- bounds accepting exactly `1..99_999`, with explicit extended opt-in for
  10,000 and 99,999; and
- process/session/socket teardown contracts.

The test reads files as text/AST and must not import absent production
modules during collection.

- [x] **Step 2.3: write failing evidence contracts**

`tests/sql_auth_strict/test_production_framework_matrix.py` contains one
canonical `@case` owner for each `FRAME-027` through `FRAME-054`.

A session fixture:

- reads only the explicitly configured metrics path or the canonical artifact
  path;
- rejects a missing artifact;
- rejects a schema mismatch;
- rejects a candidate SHA different from `git rev-parse HEAD`;
- rejects a wheel hash/import-path mismatch;
- rejects a non-PASS required profile;
- never skips due to platform/service absence.

Each test validates only its case's evidence. It does not execute a server.

- [x] **Step 2.4: extend central static contracts**

Add assertions that:

- the production runner precedes evidence validation in `run_all.sh`;
- the central report receives the production metrics path;
- all exact feature branches trigger required hosted workflows;
- the hosted workflow has Linux/macOS/Windows jobs;
- only Linux/Windows make hosted SQL-auth claims;
- the installed-wheel environment includes every server dependency required
  by its selected profile; and
- no expected command imports FastMssql from `python/`.

- [x] **Step 2.5: observe and record RED**

Run:

```bash
../../.venv/bin/pytest --noconftest \
  tests/test_production_framework_contract.py -q

../../.venv/bin/pytest \
  tests/sql_auth_strict/test_production_framework_matrix.py \
  tests/sql_auth_strict/test_matrix_contract.py -q
```

Expected:

- offline contracts fail because the runner/app/dependencies/workflow do not
  exist;
- evidence contracts fail because no exact-SHA artifact exists;
- no unrelated existing test fails;
- there is no collection error and no skipped required case.

- [x] **Step 2.6: self-review and commit RED**

Run Ruff, compile the new tests, `git diff --check`, secret scan,
code-review-graph build/detect/affected-flows/tests-for, then commit:

```bash
git add \
  VERSION.md \
  tests/test_production_framework_contract.py \
  tests/sql_auth_strict/test_production_framework_matrix.py \
  tests/sql_auth_strict/test_matrix_contract.py
git commit -m "test: require production framework process matrix"
git push -u origin test/production-framework-matrix
```

The commit must contain tests/documentation only.

---

## Task 3: establish the feature branch and locked dependency contract

**Branch:** `feat/production-framework-matrix`

**Start:** exact RED commit from Task 2

**Files:**

- modify: `pyproject.toml`
- modify: `uv.lock`
- modify: `VERSION.md`
- initially create the application package paths required by RED

- [x] **Step 3.1: create feature worktree from RED**

Verify:

```bash
git merge-base --is-ancestor \
  test/production-framework-matrix \
  feat/production-framework-matrix
```

Expected: success.

- [x] **Step 3.2: add exact development dependencies**

Update the development group to contain:

```toml
"uvicorn==0.51.0",
"gunicorn==26.0.0; sys_platform != 'win32'",
"uvicorn-worker==0.4.0; sys_platform != 'win32'",
"uvloop==0.22.1; sys_platform != 'win32'",
```

Replace the current unconditional uvloop entry; do not duplicate it.
Regenerate `uv.lock` with uv. Verify:

```bash
uv lock --check
uv sync --locked --all-extras --dev
uv run python -c \
  'import importlib.metadata as m; print({n: m.version(n) for n in ("uvicorn", "gunicorn", "uvicorn-worker", "uvloop")})'
```

On Windows, an equivalent lock/install contract must prove that POSIX-only
packages are not selected.

- [x] **Step 3.3: add marker and package skeleton**

Add:

```toml
"production_framework: real installed-wheel Uvicorn/Gunicorn process matrix",
```

Create `tests/production_framework/__init__.py`, `app.py` and
`gunicorn_conf.py` with explicit interfaces but initially minimal behavior.
Update `VERSION.md`.

- [x] **Step 3.4: make dependency/source-shape RED contracts green**

Run only static tests. Do not fake runtime evidence or weaken artifact
requirements. Commit:

```bash
git add pyproject.toml uv.lock VERSION.md tests/production_framework
git commit -m "feat: scaffold production framework matrix"
```

---

## Task 4: implement fail-closed configuration and worker lifecycle by TDD

**Branch:** `feat/production-framework-matrix`

**Files:**

- modify: `tests/test_production_framework_contract.py`
- modify: `tests/production_framework/app.py`
- modify: `tests/production_framework/gunicorn_conf.py`
- modify: `VERSION.md`

- [x] **Step 4.1: write failing pure configuration tests**

Cover:

- required environment keys;
- integer bounds;
- worker counts exactly `1/2/4/8`;
- exact division of the global connection budget;
- sanitized application/run identifiers;
- table/identifier allowlist;
- SQL delay allowlist;
- artifact directory containment;
- credentials excluded from `repr`, records and errors;
- database and offline modes cannot be confused; and
- no pool object at module import.

Run and observe RED.

- [x] **Step 4.2: implement typed immutable configuration**

Use dataclasses/enums and closed mappings. Parse environment once per worker.
Never persist the raw environment.

- [x] **Step 4.3: write failing lifecycle tests with a fake connection**

Test native ASGI lifespan:

- construct inside current PID;
- connect before ready record;
- atomic unique per-PID ready record;
- disconnect before shutdown record;
- exception prevents readiness and propagates.

Test WSGI hooks:

- `preload_app` false;
- startup/teardown execute in worker PID;
- no master-created pool;
- duplicate hook calls are deterministic.

Test adapted Flask:

- wrapper handles `lifespan`;
- adapter receives only `http`;
- startup failure prevents service.

- [x] **Step 4.4: implement lifecycle**

Use one worker-state object. Native/adapted apps construct the connection
inside lifespan. Gunicorn WSGI hooks call worker-local async start/stop with
bounded behavior.

- [x] **Step 4.5: verify and commit**

Run offline unit/static tests, Ruff, compileall, graph review and commit:

```bash
git commit -m "feat: own framework pools per worker"
```

---

## Task 5: implement the portable external-process supervisor by TDD

**Branch:** `feat/production-framework-matrix`

**Files:**

- create: `scripts/sql_auth/production_framework_matrix.py`
- modify: `tests/test_production_framework_contract.py`
- modify: `VERSION.md`

- [x] **Step 5.1: write failing CLI/profile expansion tests**

Test:

- schema version;
- required/large/maximum operation bounds;
- explicit extended opt-in;
- platform profile expansion;
- exact applicable counts;
- Windows N/A records;
- POSIX full profile set;
- representative specialized profiles;
- stable profile IDs;
- no duplicate profile; and
- deterministic JSON ordering.

- [x] **Step 5.2: implement pure CLI/profile model**

No subprocess or network work yet. Make pure tests green.

- [x] **Step 5.3: write failing command-builder tests**

Require exact argument arrays:

- Uvicorn native/adapted with explicit `--loop`, `--workers`, host, port,
  graceful and worker-health timeouts;
- Gunicorn Uvicorn worker with
  `-k uvicorn_worker.UvicornWorker`, no deprecated path and no preload;
- Gunicorn Flask `sync`;
- Gunicorn Flask `gthread --threads 4`;
- no shell interpolation;
- no secret-bearing argument; and
- the isolated venv executable.

- [x] **Step 5.4: implement command builder**

Return `list[str]`; never `shell=True`.

- [x] **Step 5.5: write failing process-supervisor tests**

Use repository-owned tiny local test processes, not the real server, to prove:

- isolated process group;
- bounded readiness;
- stdout/stderr capture;
- exact child exit propagation;
- graceful stop record;
- descendant enumeration;
- forced cleanup is recorded as failure;
- port collision retry is bounded;
- stale ready records are rejected; and
- no orphan remains after test exceptions/cancellation.

- [x] **Step 5.6: implement supervisor**

Use `asyncio.create_subprocess_exec`, `psutil` and platform-specific process
group flags. All waits have explicit deadlines.

- [x] **Step 5.7: implement isolated app copy/provenance**

Copy only the test app/config into a new run directory. Validate:

- exact source hashes;
- worker CWD is the copy;
- repository root and `python/` absent from worker `sys.path`;
- FastMssql import under isolated `site-packages`;
- wheel filename/hash/SHA match runner inputs.

- [x] **Step 5.8: offline real-server smoke**

From an isolated test venv, start a no-database `/package` app through:

- Uvicorn asyncio;
- Uvicorn uvloop on POSIX;
- Gunicorn Uvicorn worker on POSIX;
- Gunicorn Flask sync/gthread on POSIX.

Use real loopback HTTP, then verify exact process teardown.

- [x] **Step 5.9: verify and commit**

Commit:

```bash
git commit -m "feat: supervise real framework server processes"
```

---

## Task 6: implement SQL-auth application routes and observer

**Branch:** `feat/production-framework-matrix`

**Files:**

- modify: `tests/production_framework/app.py`
- modify: `scripts/sql_auth/production_framework_matrix.py`
- modify: `tests/test_production_framework_contract.py`
- modify: `VERSION.md`

- [x] **Step 6.1: write failing route/source contracts**

Require parameterized:

- principal/value/session identity;
- bounded delay;
- pooled transaction;
- pool/operation snapshot;
- explicit disconnect-aware cancellation;
- saturation admission;
- bounded ResultStream streaming;
- Flask loop and gather;
- privacy-safe errors.

Reject raw parameter interpolation and arbitrary delay/identifier input.

- [x] **Step 6.2: implement common worker state and routes**

Use:

- `Connection.transaction()` for pooled transactions;
- `Connection.stream(buffer_size=...)` for HTTP streaming;
- `SslConfig.development()` only in explicit test mode;
- `TimeoutConfig` and `LifecycleConfig` with bounded values;
- `OperationMetricsConfig(enabled=True)`;
- per-worker application names containing a sanitized run ID and PID.

- [x] **Step 6.3: write failing SQL observer tests**

With a fake result source, verify:

- sessions/requests grouped only by exact run prefix;
- observer session excluded;
- max aggregate session/request sampling;
- worker application names/PIDs reconcile;
- no credential/SQL text persistence; and
- zero-session wait is bounded.

- [x] **Step 6.4: implement observer**

Use an independently named installed-wheel FastMssql connection. Do not use
ODBC, SQLAlchemy or synchronous database clients.

- [x] **Step 6.5: first real Docker SQL-auth smoke**

Start/provision only the dedicated container:

```bash
docker compose --env-file .env.sql-auth.local \
  -f docker-compose.sql-auth.yml up -d sqlserver
scripts/sql_auth/provision.sh
```

Run one native Uvicorn asyncio worker from the development environment, hit
ready/principal/value/pool over real HTTP and stop it. This is an incremental
smoke, not wheel acceptance.

If a FastMssql defect appears, stop feature implementation and execute the
focused defect branch protocol below.

- [x] **Step 6.6: verify and commit**

Commit:

```bash
git commit -m "feat: exercise SQL auth through real framework servers"
```

---

## Focused defect branch protocol

Use this protocol for every runtime defect found in Tasks 6–10:

1. capture the exact failing profile, command, sanitized log and expected
   invariant;
2. create `test/<specific-defect>` from the current feature commit;
3. add the smallest deterministic reproduction plus `VERSION.md`;
4. run it against unchanged runtime and record RED;
5. commit/push the RED branch only to the fork;
6. create `fix/<specific-defect>` from the RED commit;
7. implement the smallest runtime repair;
8. run focused, related and cumulative tests;
9. graph-review blast radius, affected flows and tests;
10. commit/push fix only to the fork;
11. merge the fixed ancestry back into the feature branch without squashing;
12. rerun the process profile that exposed it.

No defect is hidden in the harness and no external fork/publication occurs.

---

## Task 7: implement native FastAPI real-network scenarios

**Branch:** `feat/production-framework-matrix` unless a defect branch is
required.

**Files:**

- modify: `scripts/sql_auth/production_framework_matrix.py`
- modify: `tests/production_framework/app.py`
- modify: `tests/test_production_framework_contract.py`
- modify: `VERSION.md`

- [x] **Step 7.1: process scaling matrix**

Implement Uvicorn asyncio/uvloop and Gunicorn Uvicorn-worker profiles at
`1/2/4/8`.

For each:

- wait exact ready count;
- enumerate/reach every worker PID;
- prove owner SQL principal;
- execute a compact concurrent parameter wave;
- sample sessions and process count;
- enforce aggregate budget;
- stop and reconcile shutdown PIDs/sessions.

- [x] **Step 7.2: native concurrency**

Measure sequential and concurrent SQL waits on one Uvicorn worker and a
lightweight health request during the wave. Use same-run ratios and exact
results, not an absolute throughput threshold.

- [x] **Step 7.3: real disconnect cancellation**

Use raw/HTTP streaming client behavior to close the socket only after the SQL
observer sees the unique token. Require SQL disappearance, settled
cancellation, pool recovery and a successful next request.

- [x] **Step 7.4: graceful query shutdown**

On POSIX:

- start one bounded query;
- observe it in SQL Server;
- send `SIGTERM` to manager/master;
- require the response and normal server exit;
- require every shutdown record and zero sessions.

- [x] **Step 7.5: graceful pooled transaction shutdown**

Run separate commit and rollback profiles. The app signals its transaction
phase structurally; SQL Server proves the selected durable/absent row outcome.

- [x] **Step 7.6: saturation and recovery**

For one worker:

- hold all `P` pool connections;
- admit exactly `Q` waiters;
- reject excess work with structured 503;
- observe FastMssql acquire timeout where selected;
- assert max queue/pool/session bounds;
- settle holders/waiters;
- run recovery.

- [x] **Step 7.7: real HTTP streaming**

Full-consumption profile:

- bounded SQL-generated rows/payload;
- first HTTP data before completion;
- exact row count/order/digest;
- bounded RSS/pool;
- normal recovery.

Early-close profile:

- read a validated prefix;
- close client;
- settle generator/ResultStream;
- observe SQL disappearance and pool recovery.

- [x] **Step 7.8: verify specialized native evidence and commit**

Commit:

```bash
git commit -m "test: cover native ASGI production behavior"
```

This commit may contain harness behavior plus tests, but no untested
FastMssql runtime change.

---

## Task 8: implement Flask WSGI and adapted-ASGI scenarios

**Branch:** `feat/production-framework-matrix` unless a defect branch is
required.

**Files:**

- modify: `tests/production_framework/app.py`
- modify: `tests/production_framework/gunicorn_conf.py`
- modify: `scripts/sql_auth/production_framework_matrix.py`
- modify: `VERSION.md`

- [ ] **Step 8.1: Gunicorn sync matrix**

At `1/2/4/8` workers:

- exact post-fork worker readiness;
- SQL-auth principal/value correctness;
- one request at a time per worker;
- a second request delayed behind an occupied one-worker profile;
- distinct Flask request loops;
- aggregate pool/session budget;
- exact worker-exit teardown.

- [ ] **Step 8.2: Gunicorn gthread matrix**

At `1/2/4/8`, four threads each:

- at most four requests active per process;
- a fifth request waits when all threads are occupied in one-worker profile;
- every async view keeps one thread occupied;
- exact results across per-request loops and threads;
- process/pool/session bounds and teardown.

- [ ] **Step 8.3: one-request internal concurrency**

Over real Gunicorn HTTP, `/gather` compares four sequential vs concurrent SQL
waits in one Flask async view. Require the conservative same-view ratio and
one occupied WSGI request slot.

- [ ] **Step 8.4: adapted Flask matrix**

Under Uvicorn asyncio on all OS and uvloop on POSIX at `1/2/4/8`:

- exact worker readiness;
- persistent loop IDs per worker;
- SQL-auth correctness;
- pool/session budget;
- lifecycle shutdown.

- [ ] **Step 8.5: thread-sensitive serialization**

On one adapted worker:

- establish sequential baseline;
- issue four concurrent wait requests;
- require elapsed behavior consistent with serialization;
- prove only one WSGI call active in that process;
- report multi-process scaling separately.

- [ ] **Step 8.6: comparison language contract**

Generate only the three approved execution-model labels. Add static
forbidden-claim tests.

- [ ] **Step 8.7: verify and commit**

Commit:

```bash
git commit -m "test: cover production Flask execution models"
```

---

## Task 9: implement fixed-worker load and schema-1 evidence

**Branch:** `feat/production-framework-matrix`

**Files:**

- modify: `scripts/sql_auth/production_framework_matrix.py`
- create: `scripts/sql_auth/run_production_framework_matrix.sh`
- modify: `tests/test_production_framework_contract.py`
- modify: `tests/sql_auth_strict/test_production_framework_matrix.py`
- modify: `VERSION.md`

- [ ] **Step 9.1: write failing pure load tests**

Require:

- operations `1..99_999`;
- 99,999 only with explicit extended flag;
- fixed long-lived client worker count;
- deterministic partitioning;
- bounded connection/client state;
- exact expected count/sum/digest;
- no list of all response payloads;
- latency histogram with bounded buckets;
- process/session/RSS sampling; and
- fail-fast cancellation plus complete settlement.

- [ ] **Step 9.2: implement fixed-worker load**

Representative deployment:

- native FastAPI;
- standalone Uvicorn;
- uvloop;
- four server workers;
- explicit global connection budget;
- bounded HTTP client workers.

Run required 1,000 by default. `--allow-extended` admits 10,000 and 99,999.

- [ ] **Step 9.3: implement metrics schema**

Schema 1 top-level fields:

- schema and candidate SHA;
- wheel provenance;
- platform/tool/package versions;
- sanitized configuration;
- expected/applicable/N/A profile inventory;
- per-profile worker/process/pool/session/request/timing evidence;
- native specialized scenarios;
- Flask specialized scenarios;
- load profiles;
- privacy scan;
- teardown;
- aggregate violations;
- overall PASS only if violations empty.

Writes are atomic. A partially generated artifact is never accepted.

- [ ] **Step 9.4: make case validators meaningful**

Implement `FRAME-027` through `FRAME-054` assertions against the schema. No
case may merely assert `overall == PASS`; each checks its own primary fields.

- [ ] **Step 9.5: wrapper safety**

The shell wrapper:

- sources only `.env.sql-auth.local`;
- validates the exact dedicated container name;
- builds/hashes one wheel;
- creates one fresh wheel venv;
- installs exact server/test dependencies;
- runs `pip check`;
- unsets repository `PYTHONPATH`;
- passes credentials only via inherited environment;
- always runs bounded cleanup; and
- records non-zero status.

- [ ] **Step 9.6: verify and commit**

Commit:

```bash
git commit -m "test: add bounded production framework load"
```

---

## Task 10: integrate central case registry, runner and reports

**Branch:** `feat/production-framework-matrix`

**Files:**

- modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- modify: `tests/sql_auth_strict/test_matrix_contract.py`
- modify: `tests/sql_auth_strict/conftest.py` only if needed
- modify: `scripts/sql_auth/run_all.sh`
- modify: `scripts/sql_auth/generate_report.py`
- modify: `VERSION.md`

- [ ] **Step 10.1: add canonical case descriptions**

Copy `FRAME-027` through `FRAME-054` semantics exactly from the approved
design. Case-registry tests must prove:

- each spec ID appears exactly once;
- each source owner appears exactly once;
- no orphan source/spec ID;
- updated total is exact.

- [ ] **Step 10.2: add required lane ordering**

`run_all.sh`:

1. builds/develops and runs existing gates;
2. starts/provisions SQL Server;
3. executes existing in-process framework lane;
4. executes the installed-wheel production runner once;
5. executes production evidence validators;
6. renders reports with the production artifact;
7. fails if any required lane failed.

Do not rebuild a different wheel after recording candidate provenance.

- [ ] **Step 10.3: render report**

Add:

- server/package/platform versions;
- exact wheel hash/import path;
- profile inventory/counts;
- worker and connection-budget tables;
- native cancellation/shutdown/saturation/streaming results;
- Flask execution-model comparison;
- required/extended load;
- hosted claim boundaries;
- teardown/privacy.

All zero values must render as real `0`, not missing.

- [ ] **Step 10.4: generator unit/contracts**

Test missing, malformed, stale-SHA, partial and zero-valued artifacts. Preserve
redaction.

- [ ] **Step 10.5: verify and commit**

Commit:

```bash
git commit -m "test: report production framework evidence"
```

---

## Task 11: add hosted production framework workflow by TDD

**Branch:** `feat/production-framework-matrix`

**Files:**

- create: `.github/workflows/production-framework-matrix.yml`
- modify: `.github/workflows/rust-unit-tests.yml`
- modify: `.github/workflows/dependency-security.yml`
- modify: `tests/test_production_framework_contract.py`
- modify: `tests/sql_auth_strict/test_matrix_contract.py`
- modify: `VERSION.md`

- [ ] **Step 11.1: write failing workflow contracts first**

Require:

- read-only contents permission;
- no persisted checkout credentials;
- pinned action revisions;
- exact candidate branches;
- Linux/macOS/Windows jobs;
- no `continue-on-error`;
- no required skip;
- no tracked password;
- generated/masked ephemeral SQL credentials;
- exact wheel build/hash/install/pip-check;
- `PYTHONPATH` isolation;
- sanitized artifact upload;
- teardown on success/failure; and
- exact-SHA report metadata.

- [ ] **Step 11.2: Linux SQL-auth job**

Steps:

1. checkout read-only;
2. install pinned Python/Rust/uv;
3. generate/mask ephemeral SA and app passwords;
4. start Microsoft SQL Server 2022 container manually after credential
   generation;
5. provision database/users;
6. build/install exact wheel;
7. run complete POSIX required matrix;
8. run required 1,000 load;
9. validate evidence/privacy/teardown;
10. upload sanitized artifacts;
11. remove container/volumes in always-run cleanup.

- [ ] **Step 11.3: Windows SQL-auth job**

Use the verified first-party SQL Express installation pattern. Then:

- build/install exact wheel;
- use genuine SQL-auth TCP/named-instance connectivity;
- run native/adapted Uvicorn asyncio `1/2/4/8`;
- validate Gunicorn/uvloop N/A records;
- validate process/session teardown;
- upload sanitized evidence.

- [ ] **Step 11.4: macOS structural process job**

Build/install exact wheel and run real no-database loopback server startup for:

- Uvicorn asyncio/uvloop;
- Gunicorn Uvicorn worker;
- Gunicorn Flask sync/gthread.

The artifact must say `hosted_sql_auth=false` and point to no nonexistent SQL
service.

- [ ] **Step 11.5: cumulative branch triggers**

Add exact feature/verify/status branch names to Rust/wheel and dependency
security workflows. Do not broaden original-repository publication.

- [ ] **Step 11.6: local static verification and commit**

Run workflow contracts, YAML parsing/structural checks, Ruff, compileall,
secret scan, graph review and `git diff --check`.

Commit:

```bash
git commit -m "ci: gate production framework processes"
git push -u origin feat/production-framework-matrix
```

---

## Task 12: local source and required Docker verification

**Branch:** `feat/production-framework-matrix`

- [ ] **Step 12.1: clean dependency/static gates**

Run:

```bash
uv sync --locked --all-extras --dev
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run python -m compileall \
  scripts/sql_auth/production_framework_matrix.py \
  tests/production_framework \
  tests/test_production_framework_contract.py \
  tests/sql_auth_strict/test_production_framework_matrix.py
uv run pytest --noconftest tests/test_production_framework_contract.py -q
```

- [ ] **Step 12.2: existing in-process framework regression**

Against the dedicated Docker SQL Server:

```bash
uv run pytest tests/sql_auth_strict/test_framework_integration.py -q
```

Expected: every existing test remains green.

- [ ] **Step 12.3: required source process smoke**

Run the minimum real SQL-auth profile from the development environment only
to debug the harness. Do not use it as installed-wheel acceptance.

- [ ] **Step 12.4: cumulative source tests**

Run focused matrix/static tests plus the full existing SQL-auth lanes
proportional to changes. Any failure is debugged, not waived.

---

## Task 13: exact installed-wheel Docker matrix

**Branch:** `feat/production-framework-matrix`

- [ ] **Step 13.1: build one exact candidate wheel**

Use a fresh artifact directory. Record:

- candidate SHA;
- wheel filename;
- SHA-256;
- Python ABI/platform tag;
- build command/status.

- [ ] **Step 13.2: install fresh isolated environment**

Install:

- candidate wheel;
- exact framework/server/test dependencies;
- no editable FastMssql.

Run `uv pip check` and import-path proof with repository `PYTHONPATH` absent.

- [ ] **Step 13.3: run complete required POSIX SQL-auth matrix**

On local macOS:

- all seven profile families;
- every `1/2/4/8` count;
- native specialized scenarios;
- Flask specialized scenarios;
- required 1,000-operation load;
- exact evidence validators.

- [ ] **Step 13.4: teardown verification**

Require:

- no server descendants;
- no listener;
- no profile SQL session/request;
- no pool active/pending;
- no temporary application table;
- zero violations.

- [ ] **Step 13.5: repeat required matrix**

Run required profile twice to detect stale files, port leaks, process leaks,
session leaks and non-deterministic evidence.

---

## Task 14: explicit extended 10,000 and 99,999 load

**Branch:** `feat/production-framework-matrix`

- [ ] **Step 14.1: run 10,000**

Use the exact installed wheel and representative four-worker native
FastAPI/Uvicorn/uvloop profile. Validate exact count/sum/digest, zero errors,
worker/pool/session/RSS bounds and recovery.

- [ ] **Step 14.2: run 99,999**

Require explicit extended flag. Run the same fixed-worker model. Do not
increase task count with operation count.

- [ ] **Step 14.3: compare resource envelopes**

Verify:

- process count fixed;
- client worker count fixed;
- pool/global session budget fixed;
- no monotonic residual RSS/session growth after cleanup;
- no pending operation/waiter;
- post-load query succeeds.

- [ ] **Step 14.4: persist sanitized extended evidence**

Record results in the validation artifact/report. A timeout, interruption or
incomplete total is failure, not partial success.

---

## Task 15: technical verification branch

**Branch:** `verify/production-framework-matrix`

**Start:** exact final feature/fix ancestry

- [ ] **Step 15.1: create history-only candidate**

The verify branch must retain:

- design commit;
- plan commit;
- matrix RED commit;
- every feature commit;
- every focused defect RED/fix pair.

No status-doc edits yet.

- [ ] **Step 15.2: rebuild graph and review**

Run:

```bash
uvx code-review-graph build
```

Then use:

- `detect_changes`;
- `get_affected_flows`;
- `get_impact_radius`;
- `query_graph` `tests_for`;
- source verification for important findings.

Resolve every unexplained high-risk or missing-test result.

- [ ] **Step 15.3: full cumulative local gates**

Run:

- format and lint;
- raw root Cargo build/test;
- vendored Tiberius format/clippy/tests;
- full SQL-auth runner;
- upstream/local regression;
- exact installed wheel;
- required matrix twice;
- 10,000 and 99,999 extended load;
- `pip check`;
- RustSec;
- privacy and artifact link/consistency checks.

- [ ] **Step 15.4: push verify branch only to fork**

```bash
git push -u origin verify/production-framework-matrix
```

- [ ] **Step 15.5: inspect exact hosted gates read-only**

Require success for:

- dependency security;
- Linux/macOS/Windows Rust/wheel;
- production framework Linux SQL-auth;
- production framework Windows SQL-auth/Uvicorn;
- production framework macOS structural processes.

If any gate fails:

- diagnose exact log;
- create focused RED/fix branch if behavior/config needs change;
- create a new technical SHA;
- rerun all exact-SHA gates.

Do not reuse successful jobs from an earlier SHA as final proof.

---

## Task 16: status branch and live-audit closure

**Branch:** `docs/production-framework-matrix-status`

**Start:** exact hosted-green technical candidate

**Files:**

- create:
  `docs/validation/fastmssql-production-framework-matrix-report.md`
- regenerate: `docs/SQL_AUTH_TEST_MATRIX.md`
- regenerate: `docs/SQL_AUTH_TEST_REPORT.md`
- modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- modify:
  `docs/superpowers/specs/2026-07-30-fastmssql-production-framework-matrix-design.md`
- modify:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
- modify: `VERSION.md`

- [ ] **Step 16.1: write exact validation report**

Include:

- branch/commit ancestry;
- wheel provenance;
- dependency versions;
- complete profile inventory;
- OS applicability;
- local Docker/server evidence;
- native specialized evidence;
- Flask limitations/comparison;
- 1,000/10,000/99,999 load;
- resource/teardown/privacy;
- exact hosted run/job links and conclusions;
- no original-repository action.

- [ ] **Step 16.2: regenerate matrix/report**

Use only artifacts tied to the exact technical SHA. Verify:

- every required case PASS;
- totals consistent;
- no stale prior SHA;
- zero rendered correctly;
- links resolve locally;
- no credential match.

- [ ] **Step 16.3: update live audit**

Mark item 21 and its acceptance checkbox complete only if all gates above
passed. Record exact technical/status SHAs and hosted runs.

Do not:

- mark unrelated P2 items complete;
- erase residual Flask/platform/deployment limits;
- call hosted macOS structural evidence SQL-auth; or
- declare unlimited capacity.

- [ ] **Step 16.4: re-audit the whole enterprise document**

After closing the ordered 21-item sequence, search every unchecked checkbox,
`OPEN`, `PENDING`, `TODO`, unresolved P1/P2 table row, residual-risk section
and non-goal. Classify each as:

- required remaining implementation;
- deliberately deferred capability with explicit reason;
- operational boundary;
- stale text needing correction; or
- actually complete with evidence.

The persistent goal is not complete while any required audit issue remains
unimplemented or unverified.

- [ ] **Step 16.5: self-review, commit and push status**

Run:

- exact report/matrix contract suite;
- link validation;
- tracked/artifact secret scan;
- `git diff --check`;
- graph build/detect/affected-flows;
- local/remote exact commit verification.

Commit and push only:

```bash
git commit -m "docs: close production framework matrix"
git push -u origin docs/production-framework-matrix-status
```

- [ ] **Step 16.6: verify publication boundary**

Read-only checks must prove:

- origin branch exists at exact status SHA;
- no matching branch exists on original;
- no PR targets original;
- no release/package/artifact was published;
- upstream push URL remains `DISABLED`.

## Final success criteria

Feature 21 is not complete because code exists or one smoke test passes. It is
complete only when:

1. design → plan → RED → feature/fix → verify → status ancestry is preserved;
2. every `FRAME-027` through `FRAME-054` validator passes against exact-SHA
   evidence;
3. every applicable real server/process/worker-count profile runs from the
   isolated wheel;
4. native ASGI cancellation/shutdown/saturation/streaming behavior is proven;
5. Flask WSGI/adapted limits are measured and accurately reported;
6. global/per-worker pool and observed SQL session bounds hold;
7. required 1,000 and explicit 10,000/99,999 load profiles pass;
8. local Docker and hosted Linux/Windows/macOS claim-scoped gates pass;
9. teardown/privacy/cumulative Rust/wheel/security/regression gates pass;
10. the live audit records exact evidence and the subsequent full audit
    identifies no hidden required remainder; and
11. all publication remains exclusively on Marcel Galea's fork.
