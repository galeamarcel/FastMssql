# FastMssql Production Framework Process Matrix Design

**Status:** `APPROVED_DESIGN` under Marcel Galea's standing approval for
enterprise designs, specifications, plans and inline implementations, subject
to self-review. Implementation and verification have not started.

**Source baseline:** `docs/named-instance-status` at
`fabd073cdd585fb35309ae79c1b163e234f67979`

**Technical predecessor:** named-instance candidate
`5728421a3941ce3ca957c5497bc53a78d5553b30`

**Target repository:** `https://github.com/galeamarcel/FastMssql.git`

**Publication boundary:** every branch, commit, workflow and validation
artifact produced by this feature belongs only to Marcel Galea's fork. The
original `Rivendael/FastMssql` repository remains fetch-only with push URL
`DISABLED`. This design authorizes no upstream branch, push, pull request,
release, package publication or external message.

## Decision summary

The existing framework suite is valuable but in-process. FastAPI currently
runs through HTTPX `ASGITransport`, Flask/WSGI through Flask's test client and
adapted Flask through `WsgiToAsgi` plus `ASGITransport`. Those tests prove
driver/framework behavior without a listening socket or production server
process. They do not prove:

- import from an installed candidate wheel in the server workers;
- real Uvicorn or Gunicorn startup, worker ownership and shutdown;
- post-spawn/post-fork pool initialization;
- aggregate SQL session budgets across processes;
- real client disconnects, socket backpressure or streaming;
- WSGI worker occupancy under a process manager; or
- platform-specific server behavior.

Feature 21 closes only that evidence gap. It preserves the existing
in-process suite as fast regression coverage and adds an external-process
matrix with real loopback TCP, real SQL authentication and an isolated wheel.

The selected server families are:

1. native FastAPI under standalone Uvicorn;
2. native FastAPI under Gunicorn with the current external
   `uvicorn_worker.UvicornWorker`;
3. Flask `async def` under Gunicorn `sync`;
4. Flask `async def` under Gunicorn `gthread`;
5. Flask wrapped by `asgiref.wsgi.WsgiToAsgi` under standalone Uvicorn.

Uvicorn's `asyncio` loop is the cross-platform gate. Uvicorn's `uvloop` mode
and every Gunicorn lane are POSIX-only. Gunicorn is a server for UNIX and
`uvloop` is unavailable on Windows; Windows records those lanes as
structurally not applicable rather than skipping a required runnable test or
pretending to execute them.

Every real database profile uses:

- the exact candidate wheel installed into a fresh virtual environment;
- a server working directory containing only a copied test application and
  generated non-secret configuration;
- no repository root and no `python/` source directory on `PYTHONPATH`;
- SQL Server authentication, not integrated authentication;
- one FastMssql pool per worker process;
- pool construction and connection after worker spawn/fork;
- a deployment-level global connection budget; and
- real HTTP/1.1 clients over a loopback socket.

## Authoritative framework behavior

The design was reconciled on 2026-07-30 with current primary documentation
and the installed locked source:

- Uvicorn documents ASGI lifespan once per application instance, therefore
  once in each worker, and a spawn-based built-in process manager that also
  supports Windows:
  <https://uvicorn.dev/concepts/lifespan/> and
  <https://uvicorn.dev/deployment/>.
- Uvicorn documents `asyncio`, `uvloop` and `auto` loop selection and states
  that `uvloop` is unavailable on Windows:
  <https://uvicorn.dev/concepts/event-loop/>.
- Uvicorn documents graceful-shutdown and worker-health-check timeouts:
  <https://uvicorn.dev/settings/>.
- the maintained external Gunicorn worker uses
  `uvicorn_worker.UvicornWorker`; the deprecated
  `uvicorn.workers.UvicornWorker` path is not used:
  <https://github.com/Kludex/uvicorn-worker>.
- Gunicorn documents a pre-fork master/worker model, one request at a time
  for `sync`, and a thread pool for `gthread`:
  <https://gunicorn.org/design/>.
- Gunicorn identifies itself as a WSGI/ASGI HTTP server for UNIX. It is not a
  Windows gate:
  <https://pypi.org/project/gunicorn/>.
- FastAPI documents that worker processes do not share memory. A Python
  connection pool therefore cannot be shared across worker processes:
  <https://fastapi.tiangolo.com/deployment/concepts/>.
- Flask documents that each request still occupies one WSGI worker, an async
  view receives a per-request event loop, unfinished child tasks are
  cancelled when that loop stops, and an ASGI adapter is required for a
  persistent loop:
  <https://flask.palletsprojects.com/en/stable/async-await/> and
  <https://flask.palletsprojects.com/en/stable/deploying/asgi/>.
- locked `asgiref==3.12.1` implements `WsgiToAsgiInstance.run_wsgi_app` with
  the default `@sync_to_async` configuration. The default is
  `thread_sensitive=True`, which selects the process-wide single-thread
  executor when there is no enclosing thread-sensitive context. Concurrent
  WSGI calls through one adapted process must therefore be measured as
  serialized, not advertised as native-ASGI request throughput:
  <https://github.com/django/asgiref/blob/main/asgiref/wsgi.py> and
  <https://github.com/django/asgiref/blob/main/asgiref/sync.py>.

Context7 supplied the current Uvicorn, Gunicorn, FastAPI, Flask and asgiref
documentation. Current PyPI and project-primary pages were used where
Context7's Gunicorn snapshot did not yet contain the 2026 release metadata.

## Goals

1. Prove FastMssql from an isolated installed wheel under real Uvicorn and
   Gunicorn server processes.
2. Preserve and continue running every existing in-process framework test.
3. Prove native FastAPI/Uvicorn true-async concurrency over real TCP with
   both `asyncio` and, on POSIX, `uvloop`.
4. Prove standalone Uvicorn and Gunicorn/Uvicorn worker counts `1`, `2`, `4`
   and `8`.
5. Construct and connect every pool inside its worker after spawn/fork.
6. Prove each process owns an independent pool and physical SQL sessions.
7. Enforce an explicit aggregate connection budget across all workers in one
   server instance.
8. Prove real client disconnect handling while SQL is in flight and bounded
   driver recovery.
9. Prove graceful process shutdown while a query and a pooled transaction
   are in flight.
10. Prove saturated-pool behavior with bounded HTTP admission, bounded pool
    acquisition and recovery.
11. Stream a large SQL result through a real chunked HTTP response with
    bounded `ResultStream` buffering and bounded early-client-close cleanup.
12. Prove Flask async-view compatibility under real WSGI while reporting that
    the worker or worker thread remains occupied.
13. Prove concurrent SQL inside one Flask async request without claiming
    inter-request ASGI concurrency.
14. Prove adapted Flask uses a persistent event loop under Uvicorn.
15. Measure `WsgiToAsgi` thread-sensitive serialization per process.
16. Compare execution models without claiming equivalent throughput.
17. Run a required 1,000-operation fixed-worker HTTP/SQL load profile and
    explicit extended profiles at 10,000 and 99,999 operations.
18. Prove bounded teardown: no child process, listening socket, SQL request,
    SQL session, pool lease or waiter remains.
19. Keep credentials out of commands, HTTP responses, logs and artifacts.
20. Add exact-SHA Linux, macOS and Windows hosted gates whose claims match the
    services each platform actually runs.

## Non-goals

This feature does not:

- make Flask a native ASGI framework;
- claim that `WsgiToAsgi` provides native-ASGI throughput;
- make a WSGI worker available for another request while its async view runs;
- make Gunicorn or `uvloop` work on Windows;
- share one Python/Rust connection pool object across processes;
- add cross-process IPC, a distributed semaphore or a database-wide pool
  governor to FastMssql;
- benchmark SQL Server capacity or publish a server performance number;
- prescribe one production worker or pool size for every application;
- add an HTTP framework as a FastMssql runtime dependency;
- add authentication, authorization, ORM or application serialization
  behavior;
- depend on client disconnect automatically cancelling an arbitrary ASGI
  handler that never observes `http.disconnect`;
- treat forced Windows process termination as graceful signal handling;
- duplicate every SQL feature test across every server profile;
- weaken TLS defaults; `SslConfig.development()` remains test-only for the
  self-signed local SQL Server;
- publish a wheel, release or package version;
- close unrelated P2 items such as TDS 8, TVP, Always Encrypted, spatial
  types, tracing exporters or MARS; or
- declare the entire enterprise audit complete merely because feature 21
  passes.

## Dependency contract

Server packages remain development/test dependencies. FastMssql's runtime
dependency list remains empty.

The implementation will lock:

- `uvicorn==0.51.0`;
- `gunicorn==26.0.0; sys_platform != 'win32'`;
- `uvicorn-worker==0.4.0; sys_platform != 'win32'`; and
- `uvloop==0.22.1; sys_platform != 'win32'`.

Existing FastAPI, Flask, HTTPX, asgiref and test dependencies remain in the
development group. The direct `uvloop` marker repairs the current
unconditional POSIX-only development dependency so a Windows development
sync is valid.

The selected Gunicorn ASGI lane intentionally uses
`uvicorn_worker.UvicornWorker`, even though Gunicorn 26 also has a native ASGI
worker. The audit asks for the Gunicorn/Uvicorn combination, and the external
worker package is the current Uvicorn-maintained migration path. The
deprecated `uvicorn.workers` module is forbidden.

## Isolated-wheel execution model

The candidate is built once per verification SHA:

1. build one release ABI3 wheel with `maturin --locked`;
2. compute and record its SHA-256;
3. create a fresh Python 3.13 virtual environment;
4. install the candidate wheel plus exact locked test/server dependencies;
5. run `uv pip check`;
6. prove `fastmssql.__file__` resolves under that environment's
   `site-packages`;
7. copy the repository-owned process application into a fresh run directory;
8. start every server with that run directory as its working directory and
   with repository paths removed from `PYTHONPATH`; and
9. record the import path independently from every worker.

The application copy is test harness code, not part of the FastMssql wheel.
Its presence on `sys.path` does not weaken wheel isolation. Every worker must
report:

- candidate git SHA supplied by the runner;
- wheel filename and SHA-256 supplied by the runner;
- Python executable;
- `fastmssql.__file__`;
- FastMssql distribution version;
- process ID and parent process ID;
- process start time;
- selected framework/server/loop/worker model; and
- the worker-specific SQL application name.

The report fails if a worker imports FastMssql from the repository or if two
workers claim one process-local pool identity.

## Worker-local lifecycle

### Native FastAPI

The module may create the `FastAPI` object before a worker starts, but it
must not create a FastMssql `Connection` at import time.

Each FastAPI lifespan:

1. reads already-validated non-secret configuration from environment;
2. records the current worker PID;
3. constructs that worker's `Connection`;
4. calls `connect(validate=True)`;
5. writes one atomic ready record only after SQL authentication succeeds;
6. serves requests;
7. calls `disconnect()` during lifespan shutdown; and
8. writes an atomic shutdown record only after the pool is closed.

### Flask under Gunicorn WSGI

Gunicorn must run with `preload_app = False`. A repository-owned Gunicorn
configuration uses:

- `post_worker_init` to construct/connect the worker-local FastMssql pool and
  write its ready record; and
- `worker_exit` to disconnect the pool and write its shutdown record.

Both hooks execute inside the worker. The Flask app must not construct a pool
in the Gunicorn master.

Flask async views continue to use Flask's WSGI async bridge. The shared
worker-local `Connection` is deliberately exercised across the per-request
event loops and, for `gthread`, across worker threads.

### Flask through `WsgiToAsgi`

A small ASGI lifespan wrapper owns:

1. the worker-local connection;
2. the wrapped Flask WSGI app; and
3. the `WsgiToAsgi` adapter.

It handles ASGI lifespan directly and delegates HTTP scopes to the adapter.
The adapter itself rejects non-HTTP scopes and therefore cannot own startup
or shutdown.

## Cross-process connection budget

Worker processes cannot share a pool in memory. The selected enterprise
contract is a deployment invariant:

```text
worker_count * pool_max_per_worker <= global_connection_budget
```

The required worker counts divide the selected budget exactly. The runner
rejects:

- a zero/negative budget;
- a worker count outside `1`, `2`, `4`, `8`;
- a budget smaller than the worker count;
- a non-integral per-worker allocation; or
- any generated command whose pool maximum can exceed the budget.

Every worker receives only `pool_max_per_worker`; it does not receive or
independently reinterpret the global budget. The observer:

- reads each worker ready record;
- observes `sys.dm_exec_sessions` grouped by worker-specific application
  name;
- samples aggregate sessions throughout the request wave; and
- requires the observed maximum to remain at or below the global budget.

This proves a bounded deployment configuration. It does not claim that
FastMssql implements a distributed cross-process semaphore.

## Real process profile matrix

Worker counts are always `1`, `2`, `4`, `8`.

| Profile | Server/worker | Loop | Linux | macOS | Windows |
| --- | --- | --- | --- | --- | --- |
| native FastAPI | standalone Uvicorn | asyncio | required | required | required |
| native FastAPI | standalone Uvicorn | uvloop | required | required | N/A |
| native FastAPI | Gunicorn + `uvicorn_worker.UvicornWorker` | uvloop/auto | required | required | N/A |
| Flask WSGI | Gunicorn `sync` | Flask per-request | required | required | N/A |
| Flask WSGI | Gunicorn `gthread`, four threads | Flask per-request | required | required | N/A |
| adapted Flask | standalone Uvicorn | asyncio | required | required | required |
| adapted Flask | standalone Uvicorn | uvloop | required | required | N/A |

The process-scaling phase performs a compact but real SQL-auth wave on every
applicable row/count combination:

- wait for the exact worker-ready count;
- reach every worker identity;
- prove the SQL-auth principal;
- execute parameterized SQL concurrently;
- sample aggregate SQL sessions;
- inspect pool/operation state;
- stop the server; and
- require exact ready/shutdown PID reconciliation and zero sessions.

Specialized cancellation, saturation, streaming and shutdown scenarios run
on representative profiles rather than multiplying long fault scenarios
across all 28 POSIX combinations.

## HTTP application contract

The copied test application exposes only loopback test routes. All SQL values
are parameters. Dynamic identifiers are generated and validated by the
runner before server start.

Required routes include:

- `/ready` — worker/process/import/lifecycle identity after SQL validation;
- `/value/{value}` — parameterized scalar plus SQL session identity;
- `/pool` — privacy-safe pool and operation snapshots;
- `/wait/{value}` — bounded enumerated SQL delays for concurrency tests;
- `/cancel/{token}` — an ASGI route that explicitly observes client
  disconnect, cancels and settles the SQL task, then restores pool capacity;
- `/transaction/{id}` — pooled transaction with bounded hold and explicit
  commit/rollback behavior;
- `/saturated/{value}` — admission-limited SQL operation for saturation;
- `/stream` — `Connection.stream()` to newline-delimited HTTP chunks;
- Flask `/loop` — running event-loop identity;
- Flask `/gather` — measured sequential and `asyncio.gather` SQL inside one
  async view; and
- a privacy-safe error route.

No route returns a password, connection string, SQL text, credential object,
environment dump or raw exception representation.

## Native-ASGI concurrency

On one native FastAPI/Uvicorn worker, the validator measures the same bounded
SQL `WAITFOR` workload sequentially and concurrently. It requires:

- exact independent values;
- more than one simultaneous SQL request in SQL Server;
- the concurrent wave to beat its same-process sequential baseline by a
  conservative ratio;
- a lightweight event-loop/health request to complete while SQL waits;
- pool connections not to exceed the per-worker maximum; and
- no operation or pool waiter after the wave.

The result is a framework/driver concurrency proof, not a SQL Server
throughput benchmark.

## Real client disconnect cancellation

A TCP peer disappearing does not automatically cancel application code that
never reads the ASGI receive channel. The FastAPI cancellation route therefore
implements the production pattern explicitly:

1. start one identifiable SQL wait task;
2. concurrently observe `request.is_disconnected()`;
3. if SQL finishes first, cancel and settle the monitor;
4. if the client disconnects first, cancel and await the SQL task;
5. settle `ResultStream`/query cleanup before leaving the route; and
6. expose no response requirement after the peer has gone.

The external client uses a real socket, waits until the observer sees the SQL
token, then closes without reading a response. Acceptance requires:

- the SQL request disappears within the driver cancellation bound;
- a retired physical connection is replaced when required;
- pool active/pending counts return to zero;
- a subsequent request succeeds; and
- no credential or SQL text enters logs.

This scenario is required for native ASGI. Flask/WSGI makes no automatic
disconnect-cancellation claim.

## Graceful shutdown

POSIX signal tests start a real request, confirm its SQL token in
`sys.dm_exec_requests`, then send `SIGTERM` to the server master/manager.

Two scenarios are required:

1. a bounded read completes and returns its response while the server stops
   accepting new work; and
2. a pooled transaction reaches its selected commit or rollback point before
   lifespan/worker teardown disconnects the pool.

Acceptance requires:

- no request admitted after Closing begins;
- the in-flight operation has a deterministic outcome;
- every worker emits a shutdown record;
- the master and all children exit within the configured graceful bound;
- no forced kill is needed; and
- SQL sessions reach zero.

Windows runs real server process lifecycle and bounded teardown, but a
`TerminateProcess`-style exit is not labeled graceful. A future verified
console-control implementation may add that claim without weakening the
POSIX gate.

## Saturation and bounded admission

FastMssql bounds physical connections and acquisition time; it does not own
an HTTP server's request queue. The test app therefore makes both layers
explicit:

- pool maximum: `P`;
- admitted pool waiters: at most `Q`;
- application admission capacity: `P + Q`;
- excess HTTP work: immediate structured `503`;
- admitted pool acquisition: bounded by the configured FastMssql timeout.

The one-worker scenario:

1. occupies all `P` physical connections;
2. admits exactly `Q` bounded waiters;
3. sends excess requests and observes structured rejection;
4. samples driver and app counters;
5. releases the holders;
6. settles every admitted and rejected request; and
7. executes a recovery query.

No assertion implies that `PoolConfig` itself owns the application admission
queue.

## Real large-response streaming

The native FastAPI `/stream` route:

1. opens `Connection.stream(..., buffer_size=B)`;
2. consumes one result set through its async iterator;
3. renders bounded newline-delimited records;
4. yields them through Starlette `StreamingResponse`; and
5. closes the `ResultStream` on exhaustion, error, cancellation or generator
   close.

The SQL query produces deterministic row numbers and bounded payloads without
creating an application table. The real HTTP client validates:

- response uses incremental body delivery;
- the first validated data arrives before full response completion;
- row count, order and digest are exact;
- process RSS and driver buffer settings remain within recorded bounds;
- pool capacity returns after full consumption; and
- an early client close also settles the stream and permits a recovery query.

This proves application-row and HTTP streaming. It does not claim byte-level
chunking inside one SQL LOB or a total network byte budget.

## Flask/WSGI contract

### Gunicorn `sync`

One worker handles one request at a time. A long async SQL view must delay a
second request assigned to that worker. The report records:

- worker count;
- one request slot per worker;
- per-request event-loop identifiers;
- elapsed sequential and concurrent client waves; and
- exact SQL/session outcomes.

### Gunicorn `gthread`

One process with four worker threads admits at most four concurrent WSGI
requests. Each request still occupies its thread until the async view
finishes. A fifth request must wait when all four threads are occupied.

The shared worker-local FastMssql pool is exercised across request threads and
per-request loops. Correctness and bounded resources are required; no
greenlet/gevent/eventlet worker is introduced.

### Concurrency inside one request

The `/gather` view compares four sequential SQL waits with the same four
waits under `asyncio.gather`. The concurrent portion must beat its own
sequential baseline while consuming only one WSGI request slot. This is the
useful Flask async case documented by Flask; it is not inter-request ASGI
concurrency.

## Adapted Flask contract

Under one real Uvicorn worker:

- two sequential `/loop` requests must report one persistent ASGI loop;
- four concurrent WSGI requests must retain exact values;
- the same four calls must exhibit the serialization expected from
  `thread_sensitive=True`;
- process scaling may provide one serialized WSGI lane per worker process;
- SQL sessions remain under the aggregate budget; and
- lifecycle shutdown disconnects each worker-local pool.

The comparative report must use these exact labels:

- `native ASGI: concurrent requests on persistent event loop`;
- `Flask WSGI: async view, occupied WSGI worker/thread`;
- `Flask via WsgiToAsgi: persistent ASGI loop, thread-sensitive WSGI
  serialization per process`.

It must not use `equivalent`, `same throughput`, `fully async Flask` or a
similar claim.

## Load profiles

The load generator uses a fixed number of long-lived HTTP client workers. It
does not create one task per logical operation.

| Profile | Operations | Requirement |
| --- | ---: | --- |
| required | 1,000 | every feature verification |
| large | 10,000 | explicit extended verification |
| maximum approved | 99,999 | explicit extended verification |

The extended profiles run on one representative native FastAPI/Uvicorn
`uvloop` deployment with four server processes. They do not multiply 99,999
operations by every server/worker combination.

For each profile the runner records:

- fixed client-worker count and bounded per-worker loop;
- server worker count;
- per-worker and global pool budget;
- completed/error totals;
- exact returned-value count, sum and digest;
- elapsed time and latency histogram;
- maximum server child count;
- maximum aggregate SQL sessions and requests;
- process RSS start/peak/end;
- pool/operation snapshots; and
- zero-resource teardown.

The pass criterion is correctness, boundedness and recovery. There is no
minimum transactions-per-second threshold and no claim about SQL Server's
maximum capacity.

## Platform and hosted evidence

### Local macOS with Docker SQL Server

The development host runs the complete POSIX SQL-auth matrix, including
Gunicorn and `uvloop`, against the dedicated
`fastmssql-sql-auth-dev` container. This supplies real macOS
process/wheel/server-to-Docker-SQL evidence.

### Hosted Linux

A repository-owned workflow:

- generates and masks ephemeral SQL credentials;
- starts Microsoft SQL Server 2022 in Docker;
- provisions SQL-auth users/database;
- builds and installs the exact wheel;
- runs the complete POSIX required matrix;
- runs the 1,000-operation load profile;
- verifies teardown and privacy; and
- uploads only sanitized structural artifacts.

### Hosted Windows

A `windows-2022` job:

- generates and masks an ephemeral SQL credential;
- installs genuine Microsoft SQL Server 2022 Express with SQL auth and TCP;
- builds and installs the exact wheel;
- runs standalone Uvicorn `asyncio` profiles at `1`, `2`, `4`, `8` workers
  for native and adapted apps;
- records Gunicorn and `uvloop` as N/A with their explicit platform reason;
- verifies bounded process/session teardown; and
- uploads only sanitized artifacts.

The existing genuine named-instance workflow may supply installation
patterns, but feature 21 owns its own branch triggers, contract and report.

### Hosted macOS

GitHub-hosted macOS does not provide a local SQL Server service. Its required
job therefore proves:

- exact wheel build/install/import;
- real Uvicorn `asyncio` and `uvloop` process startup over loopback;
- real Gunicorn `sync`, `gthread` and Uvicorn-worker startup over loopback;
- worker-count/process/lifecycle structure using a no-database package probe;
  and
- bounded clean shutdown.

It makes no hosted macOS SQL-auth claim. Real macOS SQL-auth evidence comes
from the local Docker gate above. The report must preserve this distinction.

## Deterministic process supervision

The Python runner owns every process and artifact:

- reserve a loopback port and retry a bounded number of launch collisions;
- start each server in an isolated process group;
- capture stdout and stderr separately;
- wait for exact per-worker atomic ready records;
- fail if readiness occurs before SQL validation in a database profile;
- never pass credentials on the command line;
- sanitize command/environment summaries before persistence;
- stop through the profile's graceful mechanism when that claim is made;
- enumerate descendants with `psutil`;
- bound every wait;
- force-kill only as cleanup after recording a required failure; and
- verify that no descendant or listening socket remains.

No required profile may:

- use `pytest.skip`;
- use `continue-on-error`;
- catch an exception and convert it to success;
- ignore a non-zero child exit;
- accept fewer ready/shutdown workers than configured; or
- reuse stale evidence from another SHA or wheel.

## Required cases

- `FRAME-027`: Uvicorn, Gunicorn, uvicorn-worker and uvloop are locked
  development-only dependencies with correct platform markers; FastMssql
  retains zero runtime framework dependencies.
- `FRAME-028`: every real server worker imports the exact candidate from the
  isolated wheel environment and reports the exact wheel/SHA provenance.
- `FRAME-029`: standalone Uvicorn with the `asyncio` loop serves real
  loopback HTTP on every Linux, macOS and Windows gate.
- `FRAME-030`: standalone Uvicorn with `uvloop` serves real loopback HTTP on
  Linux/macOS, while Windows records a structural platform N/A.
- `FRAME-031`: native FastAPI under standalone Uvicorn passes `1`, `2`, `4`
  and `8` worker process profiles.
- `FRAME-032`: native FastAPI under Gunicorn with
  `uvicorn_worker.UvicornWorker` passes `1`, `2`, `4` and `8` workers on
  POSIX, with no deprecated worker import or preload.
- `FRAME-033`: every worker creates/connects its own pool after spawn/fork;
  ready/shutdown PIDs and pool identities reconcile exactly.
- `FRAME-034`: generated per-worker pool maxima satisfy the global connection
  budget and observed aggregate SQL sessions never exceed it.
- `FRAME-035`: native FastAPI/Uvicorn handles real concurrent SQL requests
  while its event loop and health traffic remain responsive.
- `FRAME-036`: a real FastAPI client disconnect cancels and settles the
  identified SQL task, restores pool capacity and permits a recovery request.
- `FRAME-037`: POSIX graceful shutdown during an in-flight query completes
  deterministically and leaves zero sessions/processes.
- `FRAME-038`: POSIX graceful shutdown during a pooled transaction preserves
  its selected commit/rollback outcome and leaves zero leases/sessions.
- `FRAME-039`: saturated native ASGI enforces bounded application admission,
  bounded pool acquisition, structured rejection/timeout and full recovery.
- `FRAME-040`: real HTTP streaming from bounded FastMssql `ResultStream`
  delivers exact large data incrementally and settles both full-consumption
  and early-client-close paths.
- `FRAME-041`: Flask `async def` under Gunicorn `sync` remains one occupied
  request slot per worker and returns exact SQL results.
- `FRAME-042`: Flask under Gunicorn `gthread` admits only the configured
  threads, keeps each thread occupied for its request and remains correct
  across request loops/threads.
- `FRAME-043`: concurrent FastMssql I/O inside one Flask async view beats its
  same-view sequential baseline without an inter-request ASGI claim.
- `FRAME-044`: Flask through `WsgiToAsgi` under real Uvicorn uses one
  persistent event loop per worker and worker-local pool lifecycle.
- `FRAME-045`: concurrent adapted-Flask WSGI calls demonstrate
  `thread_sensitive` serialization per process; scaling comes only from
  additional processes.
- `FRAME-046`: generated comparison/report language distinguishes native
  ASGI, worker-bound WSGI and persistent-but-serialized adapted Flask, with
  no equivalence claim.
- `FRAME-047`: every applicable profile/count in the declared process matrix
  executes through a real server; required work is neither skipped nor
  swallowed.
- `FRAME-048`: fixed-worker HTTP/SQL load passes 1,000 operations and the
  explicit extended 10,000/99,999 profiles with exact correctness and bounded
  resources.
- `FRAME-049`: normal and fault scenarios leave no child process, listener,
  SQL request/session, pool lease or waiter.
- `FRAME-050`: credentials and connection strings are absent from commands,
  HTTP bodies, exceptions, logs and generated artifacts.
- `FRAME-051`: hosted Linux builds the exact wheel and passes real Docker
  SQL-auth production-server evidence.
- `FRAME-052`: hosted Windows builds the exact wheel and passes real SQL
  Server Express plus Uvicorn evidence while preserving Gunicorn/uvloop N/A.
- `FRAME-053`: hosted macOS passes exact-wheel real-server structural gates,
  local macOS Docker supplies SQL-auth, and neither is mislabeled as the
  other.
- `FRAME-054`: cumulative SQL-auth, upstream regression, Rust, vendored
  Tiberius, wheel, security, graph and generated-report gates pass on one
  exact technical candidate.

Each identifier has one canonical pytest evidence validator. Expensive
process execution occurs once per selected runner invocation and emits an
exact-SHA JSON artifact; case validators consume that artifact instead of
restarting the full matrix once per case.

## Files and ownership

Expected implementation ownership is:

- `tests/production_framework/app.py` — copied FastAPI/Flask applications,
  lifecycle, routes and privacy-safe worker records;
- `tests/production_framework/gunicorn_conf.py` — WSGI post-fork
  startup/shutdown hooks and explicit worker configuration;
- `scripts/sql_auth/production_framework_matrix.py` — portable process,
  HTTP, SQL-observer, load, resource and artifact orchestrator;
- `scripts/sql_auth/run_production_framework_matrix.sh` — local
  source/wheel/Docker wrapper and required/extended profile selection;
- `tests/test_production_framework_contract.py` — wheel-safe offline
  dependency, source, workflow, command, platform and no-swallow contracts;
- `tests/sql_auth_strict/test_production_framework_matrix.py` — canonical
  `FRAME-027` through `FRAME-054` evidence validators;
- `tests/sql_auth_strict/conftest.py` — artifact recorder integration only
  where required;
- `pyproject.toml` and `uv.lock` — development server dependencies and
  platform markers;
- `.github/workflows/production-framework-matrix.yml` — hosted platform and
  SQL-auth gates;
- `.github/workflows/rust-unit-tests.yml` and the security workflow — exact
  feature-branch triggers and cumulative gates;
- `scripts/sql_auth/run_all.sh` — production-framework lane;
- `scripts/sql_auth/generate_report.py` — production-framework evidence
  rendering;
- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
  — canonical case requirements;
- `docs/SQL_AUTH_TEST_MATRIX.md` and `docs/SQL_AUTH_TEST_REPORT.md` —
  generated exact-SHA evidence;
- `docs/validation/fastmssql-production-framework-matrix-report.md` — final
  human-readable closure;
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` — live status only after
  evidence exists;
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md` —
  future candidate intake only; and
- `VERSION.md` — every repository modification while displayed version stays
  `0.7.7`.

The implementation may split a file when self-review shows that one module
would become unsafe or unreviewable, but it may not introduce an unrelated
application framework abstraction.

## Branch and ancestry contract

1. `docs/production-framework-matrix-design`
   - design commit;
   - executable-plan commit.
2. `test/production-framework-matrix`
   - created from the exact committed plan;
   - offline and evidence-contract RED;
   - no implementation behavior.
3. `feat/production-framework-matrix`
   - descendant of the observed RED;
   - dependencies, app, runner, reporting and workflows.
4. Any discovered FastMssql defect:
   - new `test/<specific-defect>` branch with a focused RED;
   - new `fix/<specific-defect>` descendant;
   - merge back without erasing RED ancestry.
5. `verify/production-framework-matrix`
   - history-only technical integration;
   - exact local/Docker/wheel/load/hosted candidate.
6. `docs/production-framework-matrix-status`
   - generated reports, validation report and live-audit closure after every
     required gate passes.

No status document may mark feature 21 verified before hosted evidence on the
exact technical SHA is complete.

## Acceptance gates

Feature 21 is complete only when all of the following are true:

- [ ] design and executable plan are committed and self-reviewed;
- [ ] the RED branch fails only for the missing production matrix contract;
- [ ] the feature branch retains the RED commit as an ancestor;
- [ ] current server dependencies and platform markers are locked;
- [ ] every required process profile/count runs from an isolated wheel;
- [ ] native FastAPI passes concurrency, cancellation, graceful shutdown,
      saturation and streaming scenarios;
- [ ] Flask `sync`, `gthread` and adapted-ASGI limits are measured and
      correctly labeled;
- [ ] aggregate connection budgets and post-fork pool ownership are proven;
- [ ] required 1,000 and extended 10,000/99,999 operation profiles pass;
- [ ] local macOS Docker SQL-auth evidence passes;
- [ ] hosted Linux SQL-auth, Windows SQL-auth/Uvicorn and macOS structural
      server gates pass on the exact SHA;
- [ ] every server/process/socket/session/lease/waiter is gone after teardown;
- [ ] privacy scans pass on tracked files and produced artifacts;
- [ ] the cumulative SQL-auth and original-local regression suites pass;
- [ ] root Rust, vendored Tiberius, formatting, lint, wheel and RustSec gates
      pass;
- [ ] code-review-graph is rebuilt and change/flow/coverage review has no
      unexplained gap;
- [ ] generated matrix/report totals and links are internally consistent;
- [ ] the original repository has no branch, push or pull request from this
      work; and
- [ ] the live audit records exact commit/run evidence without closing
      unrelated enterprise backlog.

## Residual risks after completion

Even a passing feature leaves deployment choices outside the driver:

- applications must choose a global database connection budget appropriate
  to their SQL Server tier;
- container/orchestrator replica counts multiply per-process budgets again;
- HTTP proxies and load balancers have their own buffering, disconnect and
  timeout behavior;
- Windows graceful console-control behavior remains separate from POSIX
  signal evidence unless explicitly implemented and verified;
- Flask/WSGI remains worker-bound;
- `WsgiToAsgi` remains serialized per process under the selected asgiref
  contract; and
- 99,999 correct operations do not establish an unlimited capacity or
  universal latency promise.

These are reported as operational boundaries, not hidden behind a
production-ready label.
