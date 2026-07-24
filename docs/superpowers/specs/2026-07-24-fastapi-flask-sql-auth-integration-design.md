# FastMssql FastAPI and Flask SQL-Auth Integration Design

**Status:** approved in conversation on 2026-07-24  
**Parent validation design:** `2026-07-24-fastmssql-sql-auth-validation-design.md`  
**Working branch:** `test/sql-auth-validation`

## 1. Goal and evidence boundary

Extend the strict SQL-auth validation system with end-to-end framework tests
that prove FastMssql can be used from:

1. native FastAPI/ASGI request handlers with true asynchronous concurrency;
2. Flask `async def` views under normal WSGI execution for functional
   compatibility;
3. the same Flask application behind `asgiref.wsgi.WsgiToAsgi`, where an ASGI
   event loop remains alive across requests.

Every framework path must execute real TDS operations through FastMssql against
the dedicated `fastmssql-sql-auth-dev` SQL Server container. Mocks, fake
repositories, SQLite, ODBC, Windows authentication, and Azure authentication do
not satisfy these tests.

The tests prove the integration boundary and record its limitations. They do
not claim that Flask becomes an async-first framework or that a WSGI adapter
has the same request-concurrency model as native ASGI.

## 2. Selected approach

The approved approach is a three-lane framework suite:

- **FastAPI/ASGI:** the primary proof of true-async web use. Concurrent HTTP
  requests, event-loop progress, cancellation, pooled connection reuse,
  transaction rollback, and lifespan cleanup receive strict assertions.
- **Flask/WSGI:** a compatibility proof for `async def` routes. It verifies
  correct SQL behavior across the per-request event loops created by Flask,
  while reporting that each request still occupies one WSGI worker.
- **Flask through `WsgiToAsgi`:** a persistent-event-loop compatibility proof.
  It verifies loop reuse, SQL correctness, and lifecycle behavior without
  describing the adapted WSGI application as equivalent to native FastAPI.

This separation prevents a passing Flask smoke test from being reported as
evidence of end-to-end true-async request handling.

## 3. Test architecture

### 3.1 Files and ownership

The implementation adds:

- `tests/sql_auth_strict/framework_apps.py`: small app factories and lifecycle
  handles; no assertions and no database provisioning;
- `tests/sql_auth_strict/test_framework_integration.py`: all framework-facing
  behavior and the case identifiers below;
- framework-only fixtures in `tests/sql_auth_strict/conftest.py`;
- a `framework` pytest marker and a dedicated runner lane;
- framework results and limitations in `docs/SQL_AUTH_TEST_MATRIX.md` and
  `docs/SQL_AUTH_TEST_REPORT.md`.

App factories accept an already validated `SqlAuthConfig`. Credentials remain
in ignored environment configuration and are never placed in URLs, responses,
assertion messages, or captured logs.

### 3.2 Development-only dependencies

The framework packages are test dependencies, not FastMssql runtime
dependencies:

- `fastapi`;
- `flask[async]`;
- `httpx`;
- `asgiref`;
- `asgi-lifespan`.

Exact resolved versions are recorded in `uv.lock`. The installed package and
version metadata is captured in the test report. Importing `fastmssql` must not
import or require any framework package.

### 3.3 Shared database contract

All lanes use:

- SQL Server username/password authentication;
- the existing `fastmssql_owner` login for normal operations;
- `fastmssql_validation`;
- unique table and object names per test;
- parameterized values for data;
- deterministic cleanup that cannot hide the primary failure.

Every route used for evidence also checks `SUSER_SNAME()` or
`ORIGINAL_LOGIN()` at least once per application lifecycle so the report proves
that the intended SQL-auth principal executed the work.

Each app instance receives a unique SQL Server application name in its
connection configuration. Session cleanup assertions query SQL Server DMVs by
that exact application name, so they do not count unrelated owner-login
sessions or rely only on client-side pool statistics.

## 4. Application and resource lifecycle

### 4.1 FastAPI

The FastAPI app owns one `Connection` per process. Its lifespan context:

1. constructs and explicitly connects the shared FastMssql pool;
2. exposes the connection through `app.state`;
3. serves requests;
4. disconnects the pool on shutdown;
5. proves that `is_connected()` is false after shutdown.

`httpx.AsyncClient` with `ASGITransport` drives deterministic in-process ASGI
requests. `asgi-lifespan` starts and stops the real application lifespan.

Routes cover parameterized reads and writes, SQL errors, transactions,
`WAITFOR` concurrency, and cancellation. Request-scoped transactions are
created explicitly; pooled queries use the process-level `Connection`.

### 4.2 Flask under WSGI

The Flask app owns one lazy shared `Connection`. Normal Flask test clients
exercise actual WSGI request dispatch and `async def` views.

The suite records and retains strong references to the running event loops for
two sequential requests. It requires distinct loop objects, avoiding false
identity reuse after Python deallocates an earlier loop. It verifies that
FastMssql's Rust/Tokio-backed pool remains correct when Python awaitables are
created from different request loops.

Concurrency is tested in two distinct ways:

- multiple FastMssql operations awaited concurrently inside one async view;
- multiple WSGI requests executed by distinct worker threads.

The first is evidence that FastMssql does not block the view's event loop. The
second is compatibility evidence only: the report must state that WSGI remains
worker-bound.

An explicit test-harness shutdown closes the shared pool after all WSGI
requests. The design does not invent a Flask production lifecycle hook that
Flask does not provide.

### 4.3 Flask through `WsgiToAsgi`

The same Flask app factory is wrapped with `asgiref.wsgi.WsgiToAsgi` and driven
as an ASGI application. The test harness starts the shared FastMssql connection
before requests and closes it afterward.

Sequential routes report their running event-loop identity. The suite requires
the adapted application to reuse the continually running ASGI loop. Concurrent
requests must all return correct database results, but timing is recorded
separately from FastAPI because the WSGI adapter may retain WSGI serialization
or worker constraints.

For adapted-request cancellation, client cancellation must surface within
500 ms. The WSGI work is allowed to finish its already-started two-second SQL
wait because cancelling an ASGI caller cannot forcibly terminate a synchronous
WSGI worker. Pool capacity must return within 3.5 seconds of cancellation, and
the same application connection must then execute `SELECT 1`. This bounded
contract is intentionally different from native FastAPI cancellation.

The report labels this lane “persistent ASGI loop around Flask/WSGI”, not
“native ASGI”.

## 5. Required framework matrix

Every identifier below must appear exactly once in test source and in the final
matrix.

### FRAME — framework integration

- `FRAME-001`: framework dependencies are development-only and their locked
  versions are recorded.
- `FRAME-002`: importing FastMssql does not import FastAPI, Flask, HTTPX, or
  asgiref.
- `FRAME-003`: every framework lane authenticates as `fastmssql_owner` using
  SQL authentication.
- `FRAME-004`: credentials are absent from HTTP responses, exception strings,
  and captured framework logs.
- `FRAME-005`: FastAPI lifespan connects one shared pool and disconnects it on
  shutdown.
- `FRAME-006`: FastAPI parameterized read/write routes return correct HTTP and
  persisted database results.
- `FRAME-007`: FastAPI request-scoped transaction commits on success.
- `FRAME-008`: FastAPI request-scoped transaction rolls back on failure.
- `FRAME-009`: FastAPI concurrent `WAITFOR` requests beat a measured sequential
  baseline with the configured pool size.
- `FRAME-010`: the Python event loop continues ticking during FastAPI SQL waits.
- `FRAME-011`: cancelling a FastAPI request does not leak pool capacity or
  poison the next query.
- `FRAME-012`: a FastMssql SQL error propagates through FastAPI with its class
  and code intact when application exceptions are enabled.
- `FRAME-013`: the normal FastAPI 500 response and logs do not disclose SQL
  credentials.
- `FRAME-014`: Flask executes a real parameterized FastMssql query from an
  `async def` WSGI view.
- `FRAME-015`: one shared FastMssql connection remains correct across
  sequential Flask requests with different per-request event loops.
- `FRAME-016`: concurrent FastMssql operations inside one Flask async view
  overlap and return independent results.
- `FRAME-017`: concurrent Flask WSGI worker requests return correct independent
  SQL results; worker-bound timing is recorded without an ASGI claim.
- `FRAME-018`: Flask WSGI SQL failures remain typed, redact credentials, and
  leave the shared pool usable.
- `FRAME-019`: explicit Flask WSGI test shutdown disconnects the shared pool
  and leaves no test-owned active SQL sessions.
- `FRAME-020`: Flask wrapped by `WsgiToAsgi` executes real FastMssql SQL-auth
  queries.
- `FRAME-021`: sequential adapted Flask requests reuse the persistent ASGI
  event loop.
- `FRAME-022`: concurrent adapted Flask requests all complete correctly and
  their measured timing is reported without native-ASGI equivalence.
- `FRAME-023`: cancelling an adapted Flask request has a bounded outcome and
  the FastMssql pool remains usable afterward.
- `FRAME-024`: adapted Flask startup and shutdown leave the FastMssql pool
  disconnected and no test-owned active SQL sessions.

The parent strict matrix therefore grows from 226 to 250 case identifiers.
Parameterized test instances may make the collected pytest count larger than
250; completion is based on unique case identifiers, not pytest item count.

## 6. Timing and concurrency evidence

Timing tests use server-side `WAITFOR DELAY` rather than client-side sleeps.
Each timing assertion first measures a sequential baseline in the same process
and environment.

For native FastAPI:

- at least four one-second waits run through a pool sized for four concurrent
  connections;
- concurrent wall time must be below 65% of the measured sequential baseline;
- an independent event-loop ticker must advance at least ten times while SQL
  waits are in flight;
- every request must return a distinct expected value so overlap cannot hide
  data corruption.

For Flask WSGI and adapted Flask:

- correctness and measured timing are mandatory;
- concurrency inside one async view uses the same 65% relative threshold;
- request-level timing is reported by execution model;
- the report must not convert a timing pass into a claim that WSGI has native
  ASGI scalability.

Threshold failures remain failures. The suite does not skip or loosen them
based on machine speed.

## 7. Cancellation, errors, and cleanup

Cancellation tests start a real SQL wait, observe an active pooled connection,
and cancel the HTTP-side task.

For native FastAPI, the HTTP task must raise `asyncio.CancelledError` within
500 ms, active pool usage must return to zero within one second, and the next
query through the same application connection must complete immediately.

For adapted Flask, the HTTP task must raise `asyncio.CancelledError` within
500 ms, the already-started WSGI operation may continue only until the
two-second SQL wait finishes, active pool usage must return to zero within
3.5 seconds, and the next query through the same connection must succeed.

Both paths require the cancelled or completed request to disappear from SQL
Server request metadata. Application shutdown must leave zero sessions matching
the app's unique SQL Server application name.

Framework exception propagation is tested separately from production-style
HTTP 500 behavior. Tests never use a broad exception handler to turn an
unexpected result into a pass.

Cleanup is registered before object creation and runs after the primary
assertions. Cleanup errors are attached to the primary failure or fail the test
when no earlier failure exists.

## 8. Runner and reporting

The full SQL-auth runner adds a serial `framework` lane after strict functional
tests and before disruptive resilience tests. It captures:

- exact command and exit code;
- framework package versions;
- per-case pass/fail duration;
- sequential and concurrent timing samples;
- event-loop ticker counts and loop identities;
- pool state before and after lifecycle/cancellation checks;
- SQL Server principal and session evidence;
- environment limitations.

The report has three separate conclusions:

- **FastAPI/native ASGI:** eligible for a true-async end-to-end claim only if
  all strict concurrency and cancellation cases pass.
- **Flask/WSGI:** functional async-view compatibility, explicitly worker-bound.
- **Flask via WsgiToAsgi:** persistent event-loop compatibility, explicitly
  still an adapted WSGI application.

No framework claim is inferred from another lane.

## 9. Fixed exclusions

The framework extension does not test:

- production deployment tuning for Gunicorn, uWSGI, Hypercorn, or Uvicorn;
- WebSockets, because Flask/WSGI does not provide a comparable path and
  FastMssql has no WebSocket-specific API;
- framework authentication, authorization, serialization, or ORM behavior;
- Quart, gevent, eventlet, or non-`asyncio` event loops;
- multi-process pool sharing, because each process must own its own pool;
- Windows or Azure SQL authentication.

These exclusions are reported and cannot silently become runtime skips.

## 10. Completion criteria

Framework validation is complete only when:

1. all 24 `FRAME-*` identifiers are present exactly once;
2. every case runs against the dedicated real SQL Server container;
3. the FastAPI strict async thresholds pass;
4. both Flask execution modes pass their declared compatibility assertions;
5. no unexpected skip or xfail exists;
6. framework logs and results pass credential-redaction checks;
7. repeated framework-lane execution is deterministic;
8. the generated report preserves the execution-model distinctions above.

Publication of test or library branches remains a separate explicit approval
gate.

## 11. Authoritative framework behavior

The design follows the framework maintainers' documented execution models:

- FastAPI async path operations and async testing:
  <https://fastapi.tiangolo.com/async/> and
  <https://fastapi.tiangolo.com/advanced/async-tests/>;
- FastAPI application lifespan testing:
  <https://fastapi.tiangolo.com/advanced/testing-events/>;
- Flask async views, WSGI worker limits, per-request event loops, and
  `WsgiToAsgi` persistent-loop guidance:
  <https://flask.palletsprojects.com/en/stable/async-await/>.
