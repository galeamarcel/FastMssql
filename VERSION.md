# Version status

Current displayed package version: `0.7.7`.

## 0.8.0 — unreleased candidate

The candidate version follows the repository convention that the middle
component represents a feature-level patch. It is documented here but is not
yet applied to package metadata; release versioning remains a separate,
explicit decision.

Changes currently integrated in fork history through technical candidate
`5728421a3941ce3ca957c5497bc53a78d5553b30`:

- closed, validated SQL parameter descriptors with exact TDS metadata;
- exact decimal, UUID, temporal, ANSI/Unicode, binary, XML, and typed-null
  input handling;
- TDS 7.4 UTF-8 feature negotiation and `_UTF8` collation support;
- structured, privacy-safe conversion failures shared by connection,
  transaction, and batch paths;
- local rejection when temporal rounding would cross into year 10000;
- complete vendored-Tiberius metadata/row/DONE/INFO/return/output response
  events with panic-free unsupported metadata handling;
- bounded async `ResultStream` APIs for pooled and transactional SQL/batch,
  including multiple and empty result sets and fail-closed lifecycle;
- direct named `callproc()` with INPUT/OUTPUT/INPUT_OUTPUT/RETURN_VALUE,
  exact scalar output conversion and terminal summaries;
- a safe ordered vendored-Tiberius bulk column-subset primitive with
  pre-wire raw-identifier validation, exact metadata checks and a total
  declaration formatter;
- native TDS bulk input from bounded synchronous/asynchronous iterables with
  one lease/transaction/deadline/metric, global diagnostics and
  cancellation-safe terminal cleanup;
- bounded fixed-worker `query_many()` over independent pooled reads, with one
  shared producer/result capacity window, ordered or completion-order output
  and deterministic `aclose()`/async-context cleanup;
- bounded SQL Browser/SSRP discovery for named instances without an explicit
  port, with hardened vendored-Tiberius parsing, explicit-port bypass and one
  shared FastMssql physical-connect selector;
- deterministic SQL-auth, exact-wire, compatibility, and 1,000-operation
  concurrent load coverage;
- load-metric contract coverage for the typed-parameter case `PARAM-033`;
- regenerated SQL-auth matrix/report evidence tied to exact cumulative merge
  `5728421a3941ce3ca957c5497bc53a78d5553b30`: 442/442 required
  matrix cases, 460 strict, 16 async, 36 framework, 6 resilience, 14 load,
  and 1,263
  original-local-regression tests pass;
- hosted raw Cargo, Rust tests, wheel build/install contracts on Linux,
  macOS, and Windows passed in run `30522520458`; RustSec passed in run
  `30522520267`; genuine Windows SQL Express named-instance discovery passed
  in run `30522520410`.

No release, package-version change, or artifact publication has occurred.

### Production framework SQL-auth routes and observer

- Added worker-local SQL-auth routes for readiness, SQL principal/application
  identity, bounded BIGINT values and allowlisted delays, pool/operation
  snapshots, pooled commit/rollback transactions, disconnect-aware native
  ASGI cancellation, deterministic `P + Q` admission backed by an allowlisted
  SQL wait with structured 503, bounded NDJSON `ResultStream` output with
  cleanup on exhaustion, close, error and cancellation, and privacy-safe
  errors.
- Added the Flask WSGI compatibility routes, including per-request event-loop
  identity and a measured sequential-versus-`asyncio.gather` SQL workload;
  these routes make no inter-request ASGI-concurrency claim. Flask value/wait
  paths enforce the complete signed SQL `BIGINT` interval and transaction IDs
  enforce `1..BIGINT_MAX`, with deterministic 422 rejection before the driver.
- Worker records now include the exact validated `prefix-PID` SQL Server
  application name. Names that would exceed SQL Server's 128-character limit
  fail before connection construction or false readiness.
- Added an independent, lazily imported installed-wheel FastMssql observer
  with a size-one pool. It queries only `sys.dm_exec_sessions` and
  `sys.dm_exec_requests`, filters the exact run prefix twice, excludes its own
  session, tracks aggregate maxima, reconciles worker names/PIDs and performs
  bounded zero-session waits whose deadline includes each DMV query, without
  persisting SQL text, raw context tokens or credentials.
- Vendored Tiberius currently emits LOGIN7 `client_pid=0`, so OS PID evidence
  is correctly reconciled from the application-name suffix and atomic ready
  record; DMV `host_process_id` is retained only as diagnostic data.
- Focused route/observer tests passed, and the cumulative implemented Task 6
  contract passed 107 tests with only nine future-task gates deselected. The
  dedicated Docker SQL Server 2022 smoke passed one real Uvicorn/asyncio
  worker with SQL authentication: principal and parameterized value were
  exact, a size-one driver pool exposed exactly one pending waiter at
  application admission capacity two, the surplus request received 503 and
  recovery succeeded, observer maximum was one worker session,
  ready/shutdown PIDs reconciled, and post-run checks found zero matching SQL
  sessions and no Uvicorn process.
- Context7 was checked for current FastAPI lifespan/streaming/disconnect and
  Flask async-view behavior. The real smoke found no FastMssql runtime defect;
  its initial harness failures were isolated to selecting the wrong local
  venv and then using the supervisor's live-wait API after controlled exit.
- This task changes only the repository-owned production-framework harness,
  runner and tests. It changes no FastMssql/Tiberius runtime, package metadata,
  displayed `0.7.7` version, release, published artifact or
  original-repository state.

### Production framework native ASGI real-network matrix

- Added deterministic installed-wheel FastAPI process scenarios for Uvicorn
  asyncio, Uvicorn uvloop and Gunicorn with `UvicornWorker`, each at exact
  worker counts `1/2/4/8`. All 12 Docker SQL-auth profiles passed worker
  reachability, parameter correctness, per-worker pool division, aggregate
  session/request bounds, ready/shutdown PID reconciliation and teardown;
  the maximum observed aggregate worker-session count was eight, every
  profile ended at zero matching sessions and none used forced cleanup.
- Proved true inter-request async overlap on one persistent Uvicorn asyncio
  event loop using the same four SQL waits: `1.0635 s` sequential versus
  `0.2998 s` concurrent, four simultaneously observed SQL requests and a
  `0.0065 s` health probe during the wave. The pool returned to zero active
  and pending acquisitions.
- Added raw-client disconnect evidence that waits for an exact private SQL
  context token before closing the socket, requires the SQL request to
  disappear, proves the cancelled physical connection is replaced and then
  executes a successful recovery request. Only the token SHA-256 is retained.
- Added POSIX graceful-shutdown evidence for an active pooled query plus
  separate pooled commit and rollback transactions. `SIGTERM` is sent only
  after SQL Server and the atomic transaction-phase record agree; the query
  response completes, commit leaves the selected row present, rollback leaves
  it absent, and all three servers exit gracefully with zero sessions.
- Added the exact two-layer saturation case for pool size `P=4`: four holders,
  four admitted waiters, three immediate structured `503` rejections, four
  structured pool-acquire `504` timeouts, a maximum of four SQL sessions and
  requests, zero active/pending work after settlement and successful recovery.
- Added real incremental NDJSON streaming through the HTTP transport. Full
  consumption validated the exact order and digest of 10,000 rows with first
  data at `0.1027 s` and completion at `0.9620 s`; the early-close case asked
  for 10,000 rows, validated 32 and then closed the peer. SQL requests and
  pool usage settled to zero, recovery passed, and worker RSS grew
  `2,228,224` bytes against the explicit `67,108,864`-byte bound while the
  driver buffer remained eight rows.
- The cumulative implemented contract passes `151/151`, with exactly eight
  future Task 9--11 shell/load/report/hosted gates explicitly deselected. The
  complete contract is intentionally `151 passed, 8 failed`; no Task 7
  behavior remains hidden behind a skip or swallowed exception. Self-review
  added one global wall-clock deadline over each streaming response so a
  drip-fed peer cannot reset a per-read timeout indefinitely, and corrected
  three direct endpoint tests to pass the query-injected optional stream token
  explicitly.
- Real evidence used the healthy dedicated SQL Server 2022 Docker container,
  SQL authentication and the ABI3 wheel installed into the fresh Python
  `3.13.14` venv. The wheel runtime is exact commit
  `9a020924ff78d40cbe6ebe8595a204b0f14d46ea`; Task 7 changes only the
  repository-owned framework application, runner, tests, implementation plan
  and `VERSION.md`.
  It changes no FastMssql/Tiberius runtime, package metadata, displayed
  `0.7.7` version, release, published artifact or original-repository state.

### Production framework Flask execution-model RED

- Added focused Task 8 contracts for Gunicorn `sync` and `gthread` at
  `1/2/4/8` workers, one-request internal `asyncio.gather`, Flask adapted
  through `WsgiToAsgi`, persistent-loop evidence, thread-sensitive
  serialization and the three exact comparison labels. These contracts are
  intentionally committed before their application/runner implementation.
- A real installed-wheel Docker SQL-auth diagnostic reproduced a harness
  defect at `flask-gunicorn-gthread-w8`: all eight parameterized SQL requests
  returned correctly and teardown reached zero sessions, but the shared
  Gunicorn listener dispatched two requests to one worker and none to another,
  so SQL Server observed a legitimate maximum of seven concurrent requests.
  Requiring exactly one request per worker was therefore nondeterministic and
  did not indicate a FastMssql driver failure.
- Added explicit RED contracts requiring a conservative shared-listener SQL
  observation threshold while retaining the exact one-worker slot/thread
  proof, and requiring the primary HTTP exception to propagate with complete
  task settlement instead of being hidden by an observer timeout.
- This RED changes only repository-owned tests and documentation. It changes
  no FastMssql/Tiberius runtime, package metadata, displayed `0.7.7` version,
  release, published artifact or original-repository state.

### Dynamic Flask worker evidence RED

- The remediated shared-listener profile reached its SQL overlap gate, then a
  second real `gthread-w8` run exposed a distinct evidence defect: repeated
  `/execution/state` probes for the same worker advanced
  `completed_requests` from six to seven, and the generic collector rejected
  that legitimate request-local change as worker identity drift.
- Root-cause tracing also showed that control endpoints were counted as WSGI
  workload. This could inflate `request_sequence`, completed-request and
  maximum-active evidence, so a fan-out probe could falsely strengthen the
  execution-model claim it was measuring.
- Added RED contracts requiring control/state probes not to perturb workload
  evidence, repeated settled snapshots to remain identical, and dynamic
  `/loop` collection to accept only one explicitly declared monotonic counter
  while still rejecting changes to stable worker identity fields.
- This focused RED changes only repository-owned tests and documentation. It
  changes no FastMssql/Tiberius runtime, package metadata, displayed `0.7.7`
  version, release, published artifact or original-repository state.

### Production framework external-process supervisor

- Added a schema-1, fail-closed process runner with closed operation bounds,
  deterministic Linux/macOS/Windows profile expansion and shell-free Uvicorn
  and Gunicorn command arrays using the isolated venv interpreter.
- Added bounded external-process ownership with separate process groups,
  recursive `psutil` descendant tracking, PID create-time checks, bounded
  readiness/teardown, cancellation-safe cleanup, exact child-exit
  propagation, bounded stdout/stderr and credential redaction that also
  covers a secret truncated at the capture boundary.
- Port allocation retries only verified address-in-use exits and now treats
  any forced descendant cleanup as a terminal failure instead of hiding it
  behind a retry.
- Added a closed atomic copy of the process application, source hashes before
  and after every matrix run, bytecode suppression, repository/`PYTHONPATH`
  exclusion, candidate wheel SHA-256 verification, installed
  `site-packages` import validation and PEP 610 `direct_url.json` proof that
  the distribution came from the exact non-editable wheel path.
- Added real `/package` probes for FastAPI and Flask plus an atomic,
  non-overwriting offline artifact containing sanitized command, launch,
  process, ready/shutdown PID, listener and wheel provenance evidence.
- Built
  `fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl` with Python `3.13.14`
  and verified SHA-256
  `a9c6baeaf286b4daae01397ca4f6ff884f0f7e1598aae4355c29d12411c406b4`
  in a fresh 25-package venv. `uv pip check` passed.
- The real offline smoke passed Uvicorn `asyncio`, Uvicorn `uvloop`,
  Gunicorn with `uvicorn_worker.UvicornWorker`, Flask Gunicorn `sync` and
  Flask Gunicorn `gthread`: five exact ready/shutdown PID reconciliations,
  no forced cleanup, no surviving listener, no orphan process and no
  generated bytecode in the isolated application.
- The cumulative implemented contract is `88 passed, 10 deselected`; the
  complete RED remains intentionally `10 failed, 88 passed`, with those ten
  failures owned by SQL-auth routes/observer, fixed-worker load, shell/report
  integration and hosted workflows in Tasks 6 and 9–11.
- Context7 confirmed the public Python `Distribution.read_text` interface;
  the current PyPA PEP 610 specification confirmed that a wheel direct
  origin uses `url` plus `archive_info`, while editable directory and VCS
  origins are distinct and rejected here.
- This is incremental structural process evidence, not the final exact-SHA
  installed-wheel acceptance scheduled for Task 13. It changes no
  FastMssql/Tiberius runtime, package metadata, displayed `0.7.7` version,
  release, published artifact or original-repository state.

### Production framework worker configuration and lifecycle

- Added an immutable, fail-closed worker configuration that separates
  `sql_auth` from `offline`, validates every required setting, accepts only
  worker counts `1/2/4/8`, divides the global connection budget exactly and
  rejects unsafe identifiers, unlisted SQL delays and artifact paths outside
  the run root.
- Kept credentials out of configuration representations, public records and
  validation errors; the application module still constructs no FastMssql
  connection at import time.
- Added worker-PID ownership and atomic ready/shutdown records around strict
  `connect(validate=True)`/`disconnect()` lifecycle. Startup, stale-record,
  disconnect and bounded-hook failures remain visible and cannot emit false
  readiness or shutdown evidence.
- Implemented native FastAPI lifespan, Gunicorn WSGI
  `post_worker_init`/`worker_exit` and an ASGI lifespan owner that delegates
  only HTTP scopes to Flask through `WsgiToAsgi`.
- Context7 and the installed locked sources were checked for current FastAPI
  lifespan ordering, Gunicorn hook timing/signatures and asgiref's HTTP-only,
  thread-sensitive `WsgiToAsgi` behavior.
- The 37 focused configuration/lifecycle cases pass. The complete offline
  contract is intentionally `13 failed, 41 passed`; every remaining failure
  belongs to routes, orchestration, reporting or hosted gates scheduled in
  later tasks.
- This task changes only the repository-owned production-framework harness
  and tests. It changes no FastMssql/Tiberius runtime, package metadata,
  displayed `0.7.7` version, release or published artifact.

### Production framework process matrix scaffold

- Created `feat/production-framework-matrix` directly from the committed RED
  boundary `144052491d6c23f14253d2688a43f5ea265ce848`.
- Added exact development-only pins for Uvicorn `0.51.0`, Gunicorn `26.0.0`,
  uvicorn-worker `0.4.0` and uvloop `0.22.1`; POSIX-only packages carry an
  explicit `sys_platform != 'win32'` marker.
- Registered the dedicated `production_framework` pytest marker and added
  repository-owned, fail-closed application and Gunicorn configuration
  interfaces for the subsequent lifecycle implementation.
- The three focused dependency/post-worker source contracts pass. The full
  offline contract remains intentionally RED at `13 failed, 4 passed` for the
  routes, runner, shell integration, reports and hosted workflow owned by
  later tasks.
- This scaffold does not yet claim process, SQL-auth, load or hosted evidence.
  It changes no FastMssql/Tiberius runtime, package metadata, displayed
  `0.7.7` version, release or published artifact.

### Production framework process matrix RED

- Created `test/production-framework-matrix` directly from the approved
  executable-plan commit
  `f7b269d38164843bda53bcdb27ccd1bd78872609`.
- Added database-independent contracts for exact development-only server
  dependencies, platform markers, repository-owned app/runner/workflow
  files, post-spawn/post-fork lifecycle, current Uvicorn worker path,
  shell-free bounded supervision, isolated-wheel provenance, fixed-worker
  99,999-operation opt-in, privacy and complete teardown.
- Added one canonical evidence validator for each `FRAME-027` through
  `FRAME-054`. Each validates its own schema fields rather than accepting a
  generic aggregate PASS.
- Extended the central matrix contract with runner-before-validator ordering,
  report metrics wiring, exact candidate branch triggers and
  Linux/macOS/Windows claim boundaries.
- The focused commands are intentionally RED against the unchanged
  implementation because the production app, runner, dependencies, workflow
  and exact-SHA artifact do not yet exist. The database-independent command
  collected 17 tests and produced the intended `16 failed, 1 passed`;
  the strict command collected 65 tests and produced the intended
  `5 failed, 32 passed, 28 errors`. All 28 setup errors have the same
  fail-closed cause—the required exact-SHA artifact is absent—and neither
  command has a collection error or skip.
- Self-review made the evidence contract executable on every claimed
  platform: lock versions are distinct from platform-selected installed
  versions, Windows requires explicit N/A records for POSIX shutdown and
  Gunicorn scenarios, database pool assertions exclude the hosted macOS
  structural mode, PID reconciliation is order-independent, and extended
  10,000/99,999 load is required only when explicitly selected.
- This test-only reproduction changes no FastMssql/Tiberius runtime,
  dependency, lockfile, package metadata, displayed `0.7.7` version, release,
  published artifact or original-repository state.

### Production framework process matrix design

- Specification commit:
  `dfaba8d8ccca571309ca0c147273f53702aa31ef`.
- Approved the final ordered audit feature: real FastAPI and Flask process
  profiles through standalone Uvicorn and POSIX Gunicorn, importing
  FastMssql exclusively from an isolated candidate wheel.
- Defined worker counts `1`, `2`, `4` and `8`, Uvicorn `asyncio` on
  Linux/macOS/Windows, POSIX `uvloop`, Gunicorn plus the current external
  `uvicorn_worker.UvicornWorker`, Flask `sync`/`gthread` and adapted Flask
  through `WsgiToAsgi`.
- Required pool construction/connection after spawn/fork, one pool per
  worker and the explicit deployment invariant
  `workers * pool_max_per_worker <= global_connection_budget`; the design
  does not claim an impossible shared in-memory pool between processes.
- Required real-network concurrency, client-disconnect cancellation,
  graceful query/transaction shutdown, bounded saturation/admission,
  ResultStream-to-HTTP streaming and exact teardown.
- Preserved the approved Flask distinction: async views remain WSGI
  worker/thread-bound; the ASGI adapter supplies a persistent loop but
  thread-sensitive WSGI calls serialize per process.
- Defined `FRAME-027` through `FRAME-054`, required 1,000-operation and
  explicit 10,000/99,999 fixed-worker load profiles, local Docker SQL-auth
  and exact-wheel Linux/macOS/Windows hosted claim boundaries.
- Context7 verified current Uvicorn, Gunicorn, FastAPI, Flask and asgiref
  behavior; current primary project/PyPI sources resolved 2026 Gunicorn
  release and platform details not yet present in Context7's snapshot.
- This is specification-only. It changes no runtime, test dependency,
  lockfile, package metadata, displayed `0.7.7` version, release, published
  artifact or original-repository state.

### Production framework process matrix executable plan

- Added the branch-by-branch plan preserving separate
  `test/production-framework-matrix`, `feat/production-framework-matrix`,
  focused defect RED/fix, `verify/production-framework-matrix` and
  `docs/production-framework-matrix-status` ancestry.
- Assigned exact files and TDD checkpoints for dependency/platform contracts,
  post-spawn/post-fork worker lifecycle, portable process supervision,
  installed-wheel isolation, SQL observer, native FastAPI scenarios, Flask
  execution models, fixed-worker load, report generation and hosted gates.
- Required one versioned exact-SHA artifact to be generated once per matrix
  invocation and validated by the 28 canonical `FRAME-027` through
  `FRAME-054` pytest owners, avoiding one expensive server restart per case.
- Defined complete local macOS/Docker SQL-auth, hosted Linux SQL-auth, hosted
  Windows SQL Express/Uvicorn and hosted macOS structural process gates with
  no cross-platform claim inflation.
- Required a repeated installed-wheel matrix plus explicit 1,000, 10,000 and
  99,999 operation profiles before technical verification, followed by an
  exact-hosted-SHA status branch and a fresh full-document enterprise audit.
- The plan requires a dedicated RED/fix pair for every FastMssql defect
  discovered by the matrix and forbids hiding a runtime problem in the
  harness.
- This is planning-only. It changes no runtime, test dependency, lockfile,
  package metadata, displayed `0.7.7` version, release, published artifact or
  original-repository state.

### Named-instance refused-target fixture RED

- Created `test/named-instance-refused-fixture` directly from FastMssql
  named-instance RED commit
  `ebe13bedd407846874f92e9b82157bef2ba2179a`.
- Added a behavior-level loopback contract requiring `refused_tcp` mode to
  produce `ConnectionRefusedError` in less than `0.5` seconds, which is the
  prerequisite for testing preservation of a refused discovered TCP target
  rather than an unrelated outer timeout.
- Observed the intended RED with
  `../../.venv/bin/pytest tests/sql_auth_strict/test_sql_browser_fixture_contract.py -q`:
  `1 failed`; the held bound-but-unlistened socket produced `TimeoutError`
  after the exact `0.5`-second bound instead of `ConnectionRefusedError`.
- This RED changes one test and `VERSION.md` only. It changes no fixture,
  runtime, dependency, package metadata, displayed `0.7.7` version, release
  or artifact publication.

### Named-instance refused-target fixture fix

- Created `fix/named-instance-refused-fixture` directly from RED commit
  `d8c32c9`.
- Changed `refused_tcp` mode to release the kernel-selected loopback port
  before advertising it in the SSRP response. A bound-but-unlistened TCP
  socket is not a refusal primitive on macOS: it can leave SYN attempts
  pending until timeout.
- The fixture retains the selected numeric port only for the lifetime of the
  SSRP responder and clears it during bounded teardown; it opens no listener
  and accepts no TCP connection.
- The unchanged RED command now passes `1/1` in `0.01s`; Ruff check/format
  and `git diff --check` are also required before publication.
- This harness-only fix changes no FastMssql/Tiberius runtime, dependency,
  package metadata, displayed `0.7.7` version, release or artifact
  publication.

### Named-instance discovery design

- Approved the enterprise contract for `instance_name` without an explicit
  port: bounded SQL Server Resolution Protocol discovery over UDP `1434`,
  followed by true-async TCP/TLS/TDS login to the returned port.
- Preserved ordinary direct host/port behavior and defined an explicit port as
  authoritative, including `host\instance,port`, so no SQL Browser request is
  made when the caller already supplied the TCP target.
- Decomposed the work into vendored-Tiberius protocol RED/fix and FastMssql
  integration RED/fix branches. The Tiberius slice requires a NUL-terminated
  request, total bounded response parsing, peer validation and useful error
  preservation before FastMssql enables `sql-browser-tokio`.
- Required deterministic SSRP plus real Docker SQL-auth, 99,999 pooled logical
  operations, an isolated installed wheel, Linux/macOS/Windows gates and a
  genuine hosted Windows SQL Server Express named instance through the real
  SQL Server Browser service.
- Context7 was attempted but unavailable because its monthly quota was
  exhausted; the design records exact vendored-source evidence and primary
  Tiberius/Microsoft documentation instead.
- This is specification-only. It changes no runtime, dependency, package
  metadata, displayed `0.7.7` version, release or artifact publication.

### Named-instance discovery executable plan

- Added the task-by-task implementation plan for the approved SSRP design,
  preserving separate vendored-Tiberius protocol RED/fix ancestry and
  FastMssql integration RED/fix ancestry before cumulative verification.
- Assigned exact work to the pure request/response parser, connected Tokio
  UDP/TCP transport, read-only Tiberius configuration introspection, the
  shared FastMssql physical-connect classifier, structured Python discovery
  errors, stubs and documentation.
- Mapped `NINST-001` through `NINST-022` to deterministic protocol,
  real Docker SQL-auth, explicit-port, deadline, cancellation, pool-bound,
  99,999-operation, isolated-wheel, privacy, teardown and genuine hosted
  Windows SQL Browser evidence.
- Required exact-SHA Linux/macOS/Windows Cargo and installed-wheel gates, a
  repository-owned Windows SQL Server Express lane, complete cumulative
  SQL-auth/Rust/RustSec verification and evidence-backed live-audit closure.
- The plan authorizes no runtime change by itself and changes no dependency,
  package metadata, displayed `0.7.7` version, release, artifact publication
  or original-repository state.

### Vendored Tiberius named-instance pure-protocol RED

- Created `test/tiberius-named-instance-discovery` from exact approved plan
  commit `0083a472b5547c50629c006f095e9d8bc49fdd6f`.
- Added test-only contracts for the exact NUL-terminated SQL Browser request,
  empty/NUL/encoded-length rejection, total response header/size parsing,
  the 1,024-byte payload boundary, case-insensitive unique TCP tokens,
  non-UTF-8 unrelated fields and valid port range.
- Added setter and ADO.NET parser contracts requiring read-only distinction
  between a named instance with no port and a caller-supplied explicit port.
- The exact focused command
  `cargo test --manifest-path vendor/tiberius/Cargo.toml
  --no-default-features --features
  chrono,tds73,rustls,sql-browser-tokio --lib sql_browser` exited `101`
  against the unchanged runtime. Rust reported only the intended unresolved
  `build_instance_request`/`parse_instance_response` imports and missing
  `has_instance_name`/`has_explicit_port` methods.
- This RED changes test-only module wiring and documentation, not production
  behavior, dependency selection, package metadata, displayed `0.7.7`
  version, release or published artifact.

### Vendored Tiberius named-instance network RED

- Extended the same RED branch with deterministic Tokio loopback contracts
  for the exact observed request, discovered TCP target, connected-UDP peer
  filtering, the one-second silent-browser bound, preservation of a refused
  discovered TCP error and unchanged no-instance direct TCP behavior.
- The wrong-source test sends a valid response from a second UDP socket,
  proves that its TCP target is not selected, and only then releases the
  expected browser response. All fixture tasks, accepts and channels are
  independently bounded.
- The network contract adds no live external service, DNS dependency,
  credential, SQL text or arbitrary task fan-out. It uses only ephemeral
  loopback UDP/TCP sockets.
- The unchanged runtime remains intentionally RED through the missing pure
  helper/introspection compile contract. After those interfaces are supplied,
  the old transport must still fail the exact-NUL, connected-peer and
  preserved-TCP-error assertions before the runtime fix.
- This RED changes tests and `VERSION.md` only. It changes no production
  behavior, dependency, package metadata, displayed `0.7.7` version, release
  or published artifact.

### Vendored Tiberius named-instance discovery fix

- Created `fix/tiberius-named-instance-discovery` directly from final RED
  commit `ebe96f7cd195c4d70a586fc9da6c5d8cfe7178b6`; both protocol and network
  RED commits remain ancestors.
- Added pure request construction with the required trailing NUL and
  pre-I/O empty/NUL/32-byte validation. Replaced the panic-prone parser with
  exact header, declared-length, 1,024-byte, case-insensitive unique-TCP and
  checked port validation over raw bytes. SQLR fields are consumed as
  key/value pairs, so an unrelated value or instance literally named `tcp`
  cannot be mistaken for a duplicate transport key.
- Added read-only Tiberius configuration introspection for instance presence
  and caller-supplied port presence while retaining all setter, ADO.NET and
  `get_addr()` behavior.
- After adding only the pure helpers/getters, the focused suite produced the
  intended second RED: 9 passed and 3 failed because the old Tokio transport
  omitted NUL, accepted a response from the wrong UDP peer and replaced a
  refused discovered TCP port with `NotFound`.
- Reworked the Tokio transport to connect its UDP socket to each resolved
  browser peer, send one bounded request, retain the one-second response
  timer, validate the reply and preserve the last meaningful transport
  failure before TCP connect. Trace messages are structural.
- Used a fixed 1,028-byte receive buffer: 1,027 bytes is the maximum accepted
  header plus payload and the final byte is a rejection sentinel. This closes
  the UDP truncation ambiguity while retaining the 1,024-byte accepted
  payload ceiling.
- Added a loopback regression that sends a valid declared 1,024-byte payload
  plus one trailing byte and proves neither socket truncation nor parsing can
  select its embedded TCP target.
- Clarified the approved design and executable plan with that sentinel
  distinction after implementation self-review established that ordinary UDP
  receive APIs do not report a silently truncated datagram's original size.
- Applied the exact request builder and structural traces to the async-std and
  smol adapters; their existing transport policy remains otherwise
  unchanged.
- The focused Tokio suite passes 14/14; the complete vendored library passes
  184/184 with `sql-browser-tokio` and 170/170 without a browser feature.
  Pure compatibility suites pass 8/8 for both async-std and smol.
- Vendored format passes and Clippy passes for all targets with every
  non-baseline warning denied under the repository's audited legacy-lint
  allowlist. A combined check with Tokio, async-std and smol SQL Browser
  features enabled together also passes.
- This fix changes only the vendored transport and read-only configuration
  introspection. It changes no root FastMssql dependency feature yet, package
  metadata, displayed `0.7.7` version, release or published artifact.

### FastMssql named-instance integration RED

- Created `test/named-instance-discovery` directly from exact vendored
  Tiberius fix `e5ccb60f5c7d05513e8fd36b60a3372f85577380`; both Tiberius RED
  commits and their fix remain ancestors.
- Added deterministic FastMssql contracts for root feature selection, the
  single initial-target classifier, separate direct/discovery stream paths,
  structured Python discovery metadata, routing precedence, stubs,
  documentation, installed-wheel coverage and the first-party hosted Windows
  named-instance lane.
- Added a bounded loopback SQL Browser fixture and one canonical owner for
  each `NINST-001` through `NINST-022`. The SQL-auth specification now
  contains exactly 442 unique required cases, with deterministic malformed,
  wrong-source, silent, refused-target, deadline, cancellation, pool,
  privacy, wheel, Windows and teardown coverage.
- Added a fixed-worker persistent-pool stress harness. Its normal profile is
  1,000 parameterized logical operations; exactly 99,999 operations require
  the explicit extended switch. Structural evidence records operation
  integrity, SQL Browser/physical-connection counts, pool/session/RSS/event
  loop bounds and teardown without logging credentials or connection
  strings.
- The offline runtime contract is intentionally RED at the unchanged
  FastMssql root: 5 failed and 3 passed. The failures are exactly the absent
  root `sql-browser-tokio` feature, classifier/stream integration, discovery
  metadata and public documentation. The 42 matrix/PyO3/harness contracts
  pass.
- Against the healthy approved Docker SQL Server, the canonical named
  instance file produced 26 collected tests: 23 failed, 3 passed, 0 errors
  and 0 skips. Direct host/port and explicit-port-with-instance behavior pass;
  the direct transaction exposes the intended root cause as
  `I/O error connecting to 127.0.0.1:1434: Connection refused`, proving that
  unchanged FastMssql attempts TCP against the SQL Browser UDP endpoint
  instead of invoking SSRP. No fixture bind/setup failure occurred.
- The normal 1,000-operation stress command exits 1 as intended and writes a
  privacy-safe schema-1 artifact tied to exact source SHA `e5ccb60`, with one
  `SqlConnectionError` and `stress_execution_failure`. This is RED evidence,
  not load success.
- This branch changes tests, harnesses, specifications, reporting and CI
  contracts only. It does not enable the root dependency feature or modify
  FastMssql runtime/stubs/README, package metadata, displayed `0.7.7`
  version, release state, original repository or any published artifact.

### FastMssql named-instance discovery integration fix

- Created `fix/named-instance` directly from FastMssql integration RED
  `ebe13bedd407846874f92e9b82157bef2ba2179a`. The separately demonstrated
  refused-target fixture RED/fix pair `d8c32c9`/`18c14a1` is also preserved
  in ancestry through the history-only merge `d153eb5`.
- Enabled only the existing vendored-Tiberius `sql-browser-tokio` feature.
  Cargo regenerated one real lockfile edge: the already locked root Tokio
  crate is now an active dependency of the local Tiberius package; no new
  crate was added.
- Added one closed initial-target classifier: an instance without an explicit
  port uses SQL Browser, while direct hosts, explicit ports and
  instance-plus-explicit-port configurations use the ordinary TCP path.
  Initial discovery and direct routing streams are separate helpers, both set
  `TCP_NODELAY`, and Azure routing always reconnects directly to the server
  supplied host/port without a second browser lookup.
- Maps discovery failures to `SqlConnectionError` with stable
  `stage="sql_browser_discovery"`, retryable/discard/outcome metadata and the
  existing panic-free metadata-failure cause chain. Messages preserve useful
  structural transport detail without connection strings or credentials.
- Hardened bb8 `0.9.1` error delivery for `retry_connection=False`. That bb8
  version forwards background physical-connect errors and checkout
  validation errors through the same pool-wide error sink while a waiting
  `get()` otherwise reports only acquisition timeout. Physical creation
  errors now carry an internal connect-wave tag; validation errors cannot
  consume that wave, mismatched tags are ignored and waiting checkouts receive
  the underlying typed failure. The wrapper exposes only the used
  `get()`/`get_owned()`/`state()` surface, so callers cannot bypass the
  attribution contract through unrelated bb8 acquisition methods. Recursive
  unwrapping also preserves the triggering public operation on a physical
  connect timeout instead of relabeling it generically as `connect`. A caller
  arriving during an older cancelled attempt ignores exactly that inherited
  wave, then treats the next terminal wave as its own failure instead of
  cascading through unrelated later attempts.
- The self-review regression was observed RED in two focused Rust tests:
  validation incorrectly published a physical-connect wave and the tagged
  terminal result retained its internal wrapper instead of the underlying
  I/O error. Both are now green, and a mutation that removed wave matching
  made the cross-wave guard test fail before the correct condition was
  restored.
- Documented individual-argument and ADO.NET named-instance forms in both
  public stubs and README. The contract states that an explicit port is
  authoritative and bypasses UDP 1434 discovery.
- A fresh offline ABI3 build loaded the native module from this worktree.
  Focused Rust tests pass `10/10`; the complete offline/matrix/fixture/real
  SQL-auth named-instance selection passes `67/67` with no skip or swallowed
  exception, including direct, ADO.NET, transaction, malformed response,
  wrong-source, silent-browser, refused-target, deadline, cancellation,
  concurrent-pool and teardown cases.
- Extended 99,999-operation load, isolated-wheel and hosted
  Linux/macOS/Windows verification remain Task 7/8 gates and are not claimed
  by this implementation entry.
- This unreleased fix changes runtime behavior, one existing dependency
  feature selection, stubs, README, tests and `VERSION.md`. It changes no
  package metadata, displayed `0.7.7` version, release state, original
  repository, artifact publication or upstream authorization.

### Named-instance exact-SHA hosted-trigger RED

- Created `test/named-instance-hosted-triggers` directly from exact
  named-instance candidate `ff769825f6d3b7833b7be9d83fed4c0cc560f59b`.
- Added an offline contract requiring the cross-platform raw-Cargo,
  vendored-Tiberius and installed-wheel workflow plus the RustSec workflow
  to run automatically for `fix/named-instance`, `verify/named-instance`
  and `docs/named-instance-status`.
- The focused contract failed exactly as intended: both
  `rust-unit-tests.yml` and `dependency-security.yml` lacked all three
  named-instance candidate branches. The public push of `ff769825` launched
  only the genuine Windows named-instance workflow, so the unchanged harness
  cannot produce the required exact-SHA cross-platform/RustSec evidence.
- This branch changes one test and `VERSION.md` only. It changes no workflow,
  runtime, dependency, package metadata, displayed `0.7.7` version, release,
  artifact publication or original-repository state.

### Named-instance exact-SHA hosted-trigger fix

- Created `fix/named-instance-hosted-triggers` directly from RED commit
  `3b87aac167813b080e1a78b7a88974ba9779a2bd`; the executable failure remains
  its mandatory ancestor.
- Added only `fix/named-instance`, `verify/named-instance` and
  `docs/named-instance-status` to the existing push branch lists of the
  cross-platform Rust/wheel workflow and the RustSec workflow.
- Manual dispatch remains available but is not used as verification
  evidence. Both workflows retain read-only repository permissions,
  credential-free checkout and their existing concurrency policy.
- The unchanged named-instance contract now passes `9/9`; both workflow files
  also parse as YAML and `git diff --check` is clean before history-only
  integration into `verify/named-instance`.
- This harness-only change modifies two workflow branch filters and
  `VERSION.md`. It changes no runtime, test semantics, dependency, package
  metadata, displayed `0.7.7` version, release, artifact publication or
  original-repository state.

### Named-instance hosted installed-wheel `pip check` RED

- Created `test/named-instance-hosted-pip-check` directly from exact
  candidate `a4addefe668087561e937df8aa221958f92a9032`, after the first automatic
  Linux/macOS/Windows run completed successfully.
- Added an offline contract requiring `uv pip check` against the exact
  isolated wheel interpreter after installation and before the installed
  contract suite. Because the workflow job is a three-platform matrix, this
  supplies direct dependency-integrity evidence on Linux, macOS and Windows.
- The local CPython 3.13 wheel already passed `pip check`; this RED targets
  only the missing hosted proof required by Task 8.
- The focused contract failed exactly on the absent
  `uv pip check --python "${POOL_CONTRACT_PYTHON}"` command; wheel
  build/install and the installed test invocation were already present.
- This branch changes one test and `VERSION.md` only. It changes no workflow,
  runtime, dependency, package metadata, displayed `0.7.7` version, release,
  artifact publication or original-repository state.

### Named-instance hosted installed-wheel `pip check` fix

- Created `fix/named-instance-hosted-pip-check` directly from RED commit
  `185cdd54efe2fa1bbfa624988b7f705c25f5fd37`; the missing-check contract
  remains its mandatory ancestor.
- Added one cross-platform workflow step after wheel/test-dependency
  installation and before contract execution:
  `uv pip check --python "${POOL_CONTRACT_PYTHON}"`.
- The command targets the exact matrix interpreter selected independently on
  Linux, macOS and Windows; it does not inspect the runner's unrelated
  ambient Python environment and requires no network.
- The unchanged named-instance contract now passes `10/10`. The exact local
  command checked all 11 packages in the isolated CPython 3.13 wheel
  environment and reported that every installed package is compatible; the
  workflow YAML and `git diff --check` are also clean before history-only
  integration into `verify/named-instance`.
- This harness-only change modifies one workflow step and `VERSION.md`. It
  changes no runtime, dependency selection, package metadata, displayed
  `0.7.7` version, release, artifact publication or original-repository
  state.

### Named-instance generated-report zero-value RED

- Created `test/named-instance-report-zero-values` directly from exact
  named-instance candidate
  `99c4a7c95f14557697eda1920ece718e8a63958f`.
- Added an end-to-end generator regression requiring the canonical Markdown
  report to preserve zero residual sessions and transactions from a valid
  named-instance stress artifact. These zeroes are evidence of clean teardown,
  not missing values.
- The focused test fails exactly on both new assertions: the artifact contains
  integer zero for both counters, while `markdown_cell()` currently renders
  every falsy value as an empty string through `value or ""`.
- This RED changes one test and `VERSION.md` only. It changes no generator,
  runtime, dependency, package metadata, displayed `0.7.7` version, release,
  artifact publication or original-repository state.

### Named-instance generated-report zero-value fix

- Created `fix/named-instance-report-zero-values` directly from RED commit
  `a6409f603e08c62045bf0309762a7dce3e33e55e`; the executable report
  regression remains its mandatory ancestor.
- Changed the shared Markdown serializer to treat only `None` as absent.
  Valid falsy evidence such as integer `0` and boolean `False` is now
  preserved, while redaction and Markdown escaping remain unchanged.
- The unchanged focused RED now passes `1/1`; the complete SQL-auth matrix
  contract file passes `32/32`, Ruff check passes for both changed Python
  files, compilation succeeds and `git diff --check` is clean.
- The fix is limited to generated evidence serialization. It changes no
  FastMssql/Tiberius runtime, dependency, package metadata, displayed `0.7.7`
  version, release, artifact publication or original-repository state.

### Named-instance exact-SHA verification and live-audit closure

- Closed feature 20 as `VERIFIED_FORK` on exact cumulative technical
  candidate `5728421a3941ce3ca957c5497bc53a78d5553b30`.
- Preserved the approved design/plan, both Tiberius RED boundaries and fix,
  FastMssql RED/fix, refused-target fixture RED/fix, both hosted harness
  RED/fix pairs and the generated-report zero-value RED/fix in ancestry.
- Regenerated the canonical SQL-auth matrix/report from the exact candidate:
  442/442 required IDs, 460 strict, 16 true-async, 36 framework, 6
  resilience, 14 load and 1,263 original-local-regression tests pass, with all
  23 runner exit-code artifacts equal to zero. Root Rust passes 122/122 and
  vendored Tiberius passes 184/184.
- Ran named-instance profiles at 1,000 and 99,999 logical operations. Both
  completed with zero failure/timeout/missing/duplicate IDs, eight browser
  requests, eight physical connections, maximum eight sessions, post-load
  smoke PASS and teardown sessions/transactions 0/0.
- Built and installed
  `fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl`, SHA-256
  `c9b1e6b8705182e2a1e5dffe6c30645a10ae4d768683ce88937c24737dbfc227`,
  in isolated CPython 3.13.14. With `PYTHONPATH` and ambient `VIRTUAL_ENV`
  unset, it passed 10/10 offline contracts, 26/26 real SQL-auth tests,
  1,000-operation stress and dependency-integrity checks for 11 packages.
- Verified exact-SHA hosted raw Cargo, Rust, Tiberius, wheel build/install,
  `pip check` and installed contracts on Ubuntu, macOS and Windows in
  `30522520458`; RustSec passed in `30522520267`. The first-party Windows SQL
  Express `SQLEXPRESS` plus real SQL Browser lane passed pooled parameterized
  query, direct transaction and zero-session teardown in `30522520410`,
  without an explicit instance port.
- Rebuilt the graph on the exact SHA: 190 supported files, 4,289 nodes and
  52,330 edges with `head_matches_build=true`. Same-module Rust, PyO3 and
  subprocess graph gaps are reconciled by direct RED/GREEN, SQL-auth, stress,
  wheel and hosted evidence.
- Added the exact validation report, updated the live audit and future
  upstream candidate intake, and kept feature 21—the real
  Uvicorn/Gunicorn installed-wheel process matrix—explicitly pending.
- The closure changes documentation and generated evidence only beyond the
  separately committed report serializer fix. It changes no displayed
  `0.7.7` package metadata, release or package publication and sends nothing
  to either original repository.

### Seven-slice batch/bulk cumulative verification and live-audit closure

- Closed the seventh batch/bulk slice, bounded-concurrency `query_many()`,
  while preserving every documentation → RED → implementation ancestry
  through exact technical candidate
  `306b44d1aafce6b0dc763bfe179784de5bfd6f06`.
- Regenerated the canonical SQL-auth matrix and report from the exact clean
  technical tree: 420/420 required IDs, 434 strict, 16 true-async, 36
  framework, 6 resilience, 13 load and 1,253 original-local-regression tests
  passed, with all 22 runner exit-code artifacts equal to zero.
- Recorded cumulative native-list, native-iterable, `execute_many()` and
  `query_many()` stress through 99,999 rows/parameter sets/operations.
  Every profile reported exact producer/output/persistence counts,
  post-load smoke success, bounded RSS/event-loop/pool/session observations
  and zero teardown sessions.
- Recorded the isolated CPython 3.13.14 ABI3 wheel
  `fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl`, SHA-256
  `0f84fb6469a3df3e113b6333326bfe34452647ef55ea07163f14b88b6eb4d49f`.
  It was imported from external `site-packages` with `PYTHONPATH` unset and
  passed 104 hosted-contract selections, 160 batch/bulk offline contracts,
  31 matrix contracts, 58 real SQL-auth batch/bulk tests, 36 framework tests
  and `pip check`.
- Recorded the exact-SHA hosted Linux/macOS/Windows raw-Cargo, Rust,
  wheel-build/install and installed-contract success in run `30507343856`,
  plus the zero-warning/zero-vulnerability RustSec success in run
  `30507343869`.
- Added the cumulative batch/bulk stress report, updated the live
  production-readiness audit and future-PR candidate intake, and regenerated
  the canonical SQL-auth evidence. Also removed one historical trailing
  blank line that made the cumulative `git diff --check` report whitespace
  noise.
- These changes are status/evidence only. They do not change FastMssql
  runtime behavior, package metadata, the displayed `0.7.7` version,
  dependencies, release state, artifact publication or authorization for an
  upstream PR.

### Hosted query-many wheel dependency fix

- Created `fix/hosted-wheel-query-many-dependencies` directly from RED commit
  `faef03288faca8bc96e921579e631a6b0ec81e2f`, preserving the executable
  failing contract in ancestry.
- Added pinned `pytest-timeout==2.4.0` and `psutil==7.2.2` only to the
  workflow's isolated installed-wheel contract venv. They enforce the
  existing timeout marker and satisfy the stress harness's process-monitor
  import; they are test-runner dependencies, not FastMssql runtime
  dependencies.
- The unchanged RED contract moved from one expected failure listing both
  omissions to 1/1 PASS, and the complete PyO3 build-contract file passed
  10/10.
- Re-ran the exact hosted pytest command against the candidate ABI3 wheel
  under Python 3.13.14 with `PYTHONPATH` unset: all 104 selected
  configuration/query-many contracts passed from the minimal venv.
- A hosted Linux/macOS/Windows rerun remains required on the later cumulative
  exact SHA; this local result is not recorded as hosted success.
- This correction changes no FastMssql runtime, `pyproject.toml`, lockfile,
  package metadata, displayed `0.7.7` version, release or published artifact.

### Hosted query-many wheel dependency RED

- Created `test/hosted-wheel-query-many-dependencies` from exact cumulative
  candidate `8bc09316839db1496c411fca679aade52669a9f2` after hosted Rust run
  `30505273021`, Ubuntu job `90753556718`, failed only in
  `Verify installed Python configuration contracts`.
- Reproduced the workflow environment with the exact candidate wheel,
  Python 3.13.14, `pytest==9.1.1` and `pytest-asyncio==1.4.0`: collection
  exited `2` because the selected query-many coordinator uses the
  unregistered `timeout` marker.
- Adding only `pytest-timeout==2.4.0` to the ignored reproduction venv moved
  collection forward to 99 passing tests and five failures caused by the
  missing `psutil` import in the selected query-many stress contract.
  Adding only the remaining `psutil==7.2.2` requirement made the exact hosted
  command pass 104/104.
- Added one deterministic workflow contract requiring both test-only
  dependencies in the same minimal installed-wheel environment as the
  selected tests. It is expected to fail on the unchanged candidate by
  listing both omissions.
- This RED commit changes no workflow, FastMssql runtime, package dependency,
  package metadata, displayed `0.7.7` version, release or published artifact.

### Cumulative SQL Server fresh-process gate fix

- Created `fix/cumulative-sqlserver-fresh-start` directly from RED commit
  `98e80c52e346c8113f5bf9263039840e41f569bc`, preserving the executable
  failing contract in ancestry.
- Added only `--force-recreate` to the canonical
  `docker compose ... up -d sqlserver` lane; no retry, volume deletion,
  memory tuning, sleep or additional Docker operation was introduced.
- The exact unchanged RED contract moved from the intended `1 failed` to
  `1 passed`, proving the runner now emits the required argument vector and
  completes recreation before provision.
- The complete SQL-auth matrix-contract file passed `31/31`, including
  original-local-regression naming, required stress wiring, hosted wheel
  command, privacy, unique case ownership and no-swallowed-failure contracts.
- The persistent named data volume and all library runtime behavior remain
  unchanged; the correction applies only to the canonical validation
  harness.
- This fix does not alter package metadata, displayed version, dependency,
  release or published artifact.

### Cumulative SQL Server fresh-process gate RED

- Created `test/cumulative-sqlserver-fresh-start` from exact approved plan
  commit `bcb51d9cd208c03e28122ddc4f02d72e63e40d03`.
- Refactored the existing full-runner sandbox setup into one test helper and
  added a real executable Docker recorder plus a provision event marker.
- Added one behavior contract requiring the exact Compose argument vector
  `up -d --force-recreate sqlserver`, exactly one Docker invocation and
  provision only after that invocation completes.
- The new test failed once for the intended sole reason: the unchanged runner
  emitted `up -d sqlserver` without `--force-recreate`; it did not fail on
  collection, imports, fixtures, sandbox execution or event ordering.
- The pre-existing original-local-regression display and artifact contract
  remained green `1/1` against the same extracted helper.
- This test-only RED changes no runner or library behavior, package metadata,
  displayed version, dependency, release or published artifact.

### Cumulative SQL Server fresh-process gate plan

- Added the executable TDD plan for a recording Docker/provision boundary,
  one behavior-focused RED contract and the minimal
  `--force-recreate` runner correction.
- Fixed the branch topology as documentation → RED → fix → history-only
  cumulative merge, with `VERSION.md`, fork-only publication and exact remote
  SHA verification at every repository-changing stage.
- Required the unchanged timed-out native-bulk test, full matrix contract,
  real container recreation, complete canonical runner, privacy/artifact
  checks and exact knowledge-graph review before resuming parent Task 12.
- Preserved the named SQL Server data volume and prohibited retries, volume
  deletion, memory tuning, upstream publication and broad runner changes.
- This plan changes no runtime-library behavior, package metadata, displayed
  version, dependency, release or published artifact.

### Cumulative SQL Server fresh-process gate design

- Recorded the cumulative-runner failure at exact candidate `b997ab3`: the
  unchanged native-bulk `BULK-008` request waited on
  `RESOURCE_SEMAPHORE` when SQL Server exposed only about 15 MB of query
  memory for a request requiring about 58 MB.
- Confirmed the environmental boundary by restarting only the approved
  dedicated container, reprovisioning the persistent databases, observing
  zero semaphore waiters and 638–837 MB available, and rerunning the exact
  unchanged test successfully in 0.14 seconds.
- Selected `docker compose up -d --force-recreate sqlserver` for the
  canonical cumulative runner so every gate begins with a fresh SQL Server
  process while preserving the named data volume.
- Defined an executable RED contract that runs the real runner with a
  recording Docker fake and proves the exact recreate argument vector and
  ordering before provision, rather than grepping shell source.
- Rejected retries, destructive volume removal, process-only `restart`, and
  SQL Server memory tuning as solutions to the inherited-process-state
  defect.
- Added no runtime-library behavior, package metadata, displayed-version
  change, dependency, release or published artifact.

### Query-many bounded-concurrency design

- Added the focused design for the seventh and final batch/bulk slice on
  branch `docs/query-many-design`, based on exact status commit
  `7b5ee080ab57ff1d777b1d607590141870f1b9bd`.
- Selected a fixed Python worker coordinator over the already verified raw
  `Connection.query()` path so every child retains existing typed conversion,
  lifecycle, pool timeout, operation timeout, metric and fail-closed
  connection disposition semantics.
- Defined one shared capacity window across producer input, queued work,
  active queries, completed results and ordered reassembly, bounded by
  `min(concurrency, pool.max_size)` and never implemented as one task per
  input item.
- Corrected the parent contract: a bare `async for ... break` cannot
  synchronously notify a general async iterator. Deterministic early exit
  therefore uses public idempotent `aclose()` or the iterator's async context
  manager; drop cleanup remains an explicitly best-effort fallback.
- Reserved `QMANY-001`–`QMANY-013`, required load at 1,000 and 10,000
  operations, extended load at 99,999 operations, installed-wheel evidence
  and cumulative SQL-auth/Rust/graph gates.
- Kept the operation-metrics schema exactly 2 with one existing `query`
  observation per child, no fabricated aggregate `query_many` metric and no
  concurrent Transaction surface.
- Limited `query_index` to producer, parameter-set and child-query failures;
  consumer cancellation or explicit close cannot truthfully select one index
  from multiple outstanding workers and therefore fabricates none.
- Added the executable TDD plan
  `docs/superpowers/plans/2026-07-30-fastmssql-query-many-bounded-concurrency.md`
  with separate public/coordinator/SQL-auth RED commits, a real RED→feature
  ancestry, shared parameter validation, fixed-worker implementation tasks,
  Docker fault tests, required/99,999 stress, installed-wheel checks,
  framework smoke, exact graph review and fork-only publication.
- Separated the public `QueryManyIterator` facade from private
  `_QueryManyState`: background tasks retain only the state, so they cannot
  keep the facade alive and make its explicitly best-effort drop fallback
  unreachable.
- Self-review corrected the executable plan to use only existing SQL-auth
  fixtures, admit exactly `QMANY-013` into load metrics, test JOIN/CTE/empty
  result/stored-procedure query-many use cases, count uniquely named
  candidate sessions, keep background tasks state-only, and place isolated
  wheel products under the repository's actually ignored
  `.artifacts/sql-auth/` tree.
- Reconciled the final handoff with the authoritative parent batch/bulk plan:
  it regenerates the canonical SQL-auth matrix/report and creates
  `SQL_AUTH_BATCH_BULK_STRESS_REPORT.md`, rather than referring to an
  unplanned validation-report filename.
- Added deterministic startup-failure/traceback cleanup coverage and a
  resolved-prefix plus single-wheel preflight so malformed pool metadata or
  stale ignored build products cannot make the implementation or wheel gate
  pass accidentally.
- This documentation-only change does not alter runtime behavior, package
  metadata, displayed `0.7.7` version, release state or published artefacts.

### Query-many absent-API RED

- Created branch `test/query-many-bounded-concurrency` from exact approved
  plan commit `7d8a4dd0d39c8a39495e2c1aaa845f6fd89ffa98`.
- Added the public contract for the regular `Connection.query_many()`
  factory, `QueryManyIterator`, exact wrapper typing, deliberate absence from
  Transaction/raw surfaces, synchronous scalar/source validation, one
  preferred producer protocol, preserved producer-acquisition
  `BaseException` and concrete-list resize detection.
- The focused RED collected 30 cases: 29 failed for the intended absent
  wrapper/stub/runtime surface and one existing negative-surface case passed.
  Ruff passed, so the failure is not collection, syntax or lint noise.
- This test-only commit changes no runtime behavior, package metadata,
  displayed `0.7.7` version, release state or published artifact.

### Query-many coordinator RED

- Added deterministic event/barrier contracts for fixed worker count,
  pool-capped effective concurrency, one accepted-result capacity window,
  slow-consumer backpressure, exact ordered/completion-order delivery and
  normal exhaustion.
- Required first-error identity/traceback/index preservation, privacy-safe
  producer/parameter/conversion failures, startup pool-stat validation,
  quiescent close ordering and cleanup-error chaining.
- Covered close before/after start, async-context early break, caller and
  repeated cancellation, concurrent consumers, second-loop rejection,
  supervised drop fallback and absence of leaked named tasks.
- The focused coordinator RED collected 29 cases and all 29 failed at the
  intended absent `Connection.query_many()` surface; Ruff passed and
  ordering assertions use explicit gates rather than wall-clock completion
  guesses.
- This test-only commit changes no runtime behavior, package metadata,
  displayed `0.7.7` version, release state or published artifact.

### Query-many SQL-auth, matrix and stress RED

- Extended the canonical SQL-auth specification from 407 to 420 unique case
  IDs with `QMANY-001`–`QMANY-013`, mapped exactly once to six strict
  real-SQL functions and one required load function.
- Added deterministic SQL Server contracts for the public/empty surface,
  typed list/synchronous/asynchronous sources, parameterized JOIN, CTE,
  empty-result and stored-procedure queries, pool-capped concurrency,
  lock-controlled input/completion ordering, first-error propagation,
  cancellation, timeout, saturated acquire, killed SPID, graceful/forced
  shutdown, schema-2 child query metrics and security-context session
  retirement.
- Added the lazy query-many stress harness and shell lane with exact required
  profiles at 1,000 and 10,000 operations, explicit 99,999-operation opt-in,
  bounded admission and latency tracking, child-query/pool/session sampling,
  atomic privacy-safe evidence and 13 non-advisory resource/correctness
  gates.
- Wired the required stress lane before strict execution, the focused strict
  file into `strict_functional`, a redacted query-many stress section into
  report generation and all three offline contracts into the installed-wheel
  workflow on Linux, macOS and Windows.
- Fresh structural verification passed 6/6 stress contracts and 30/30 matrix
  contracts. The dedicated Docker SQL Server was healthy and freshly
  provisioned; all six strict functions and the `QMANY-013` required-load
  function failed for the intended sole reason that
  `Connection.query_many()` is absent.
- This complete RED branch adds no FastMssql runtime behavior, dependency,
  package-metadata change, displayed-version change, release or published
  artifact.

### Shared parameter-set validation

- Characterized the exact `execute_many` invalid-set, named-parameter and
  2,098-user-parameter-limit messages before moving any runtime code,
  including privacy protection for named parameter values.
- Extracted the existing producer-type constants, SQL Server parameter limit
  and per-set validation into the private shared `_parameter_sets` module;
  `execute_many` now supplies its operation name explicitly and otherwise
  preserves the same bounded coordinator behavior.
- Fresh verification passed all 38 focused execute-many contract/coordinator
  tests, all 11 real Docker SQL-auth execute-many tests and Ruff.
- This internal refactor adds no public API or behavior, dependency,
  package-metadata change, displayed `0.7.7` version, release or published
  artifact.

### Query-many public facade and bounded coordination

- Added the regular `Connection.query_many()` factory and public
  `QueryManyIterator` protocol while deliberately keeping the raw and
  transactional surfaces unchanged.
- Added synchronous scalar validation, exactly one producer-protocol
  acquisition with async precedence, captured-list resize detection and
  lazy event-loop/pool binding.
- Implemented one producer task, exactly
  `min(concurrency, pool.max_size)` fixed workers and one capacity token that
  follows every accepted item through query completion to yield/discard.
  Ordered and completion-order modes therefore share the same bounded input,
  active-query, completed-result and slow-consumer window.
- Fresh verification passed all 30 public query-many contracts and 6/6
  deterministic empty/full-exhaustion, pool-cap, fixed-worker, capacity and
  ordering coordinator cases; Ruff and diff checks passed.
- Terminal first-error and cancellation-shielded cleanup semantics remain
  the immediately following implementation task. Package metadata and the
  displayed `0.7.7` version remain unchanged; no release or artifact was
  published.

### Query-many terminal cleanup supervision

- Made first producer/parameter/query failure registration single-assignment
  and terminal: it records the truthful `query_index`, stops admission,
  cancels sibling work and starts one state-owned cleanup supervisor without
  retaining the public iterator facade.
- Added quiescent abnormal producer close, complete queue/pending-token
  reconciliation and cancellation-shielded cleanup that survives repeated
  consumer cancellation and leaves no unobserved task exception.
- Preserved the original failure object and traceback, existing
  `parameter_index`, body/caller cancellation primacy and privacy; the first
  cleanup failure is chained as `__cause__`, while explicit close raises it
  when no primary exists.
- Self-review fixed an exhaustion-vs-failure race that could skip abnormal
  producer close and a shield-result path that could replace the primary
  exception with its cleanup failure.
- Fresh verification passed 65/65 offline query-many public/coordinator/stress
  contracts and 46/46 execute-many/operation-metrics regressions with no
  pending-task or never-retrieved warning.
- Package metadata and the displayed `0.7.7` version remain unchanged; no
  release or artifact was published.

### Query-many SQL-auth contract corrections

- Corrected the typed-conversion fault input to use an explicit `INT`
  `Parameter`, matching the asserted `ConversionError` and preserving its
  existing `parameter_index` before query-many adds `query_index`.
- Scheduled the raw PyO3 query awaitable with `asyncio.ensure_future()`;
  `asyncio.create_task()` accepts coroutine objects only and therefore
  rejected this valid driver Future before the pool-saturation scenario
  could start.
- Both focused real-MSSQL failure/lifecycle functions now pass, including
  producer/conversion/SQL errors, cancellation, early exit, acquire timeout,
  KILL SPID and graceful/forced shutdown.
- This test-only correction changes no FastMssql runtime, dependency, package
  metadata, displayed `0.7.7` version, release or published artifact.

### Query-many SQL-auth, stress and framework evidence

- All six strict real-SQL query-many functions passed, covering
  `QMANY-001`–`QMANY-012`, followed by 6/6 structural stress contracts.
- The four required 1,000/10,000-operation profiles yielded exactly 22,000
  results with no duplicate or missing ID, at most 16 accepted/active
  queries and SQL sessions, exact child-query metrics, successful smoke
  queries and zero teardown sessions.
- Both explicitly approved 99,999-operation profiles passed on exact source
  `4d7a58cd78992379018d808a2ff2020b615d1243`: 199,998 exact pulls/yields,
  maximum accepted window, active queries and SQL sessions of 32, maximum
  RSS growth 42,041,344 bytes, maximum event-loop gap
  0.008793624816462398 seconds and zero teardown sessions.
- Documented canonical full-consumption and explicit early-close usage,
  ordinary per-child query metrics, pool-capped effective concurrency,
  first-result-set buffering and the deliberate absence of a concurrent
  Transaction surface.
- Added one shared real-SQL helper and route for FastAPI/ASGI, Flask
  `async def` under WSGI and Flask through `WsgiToAsgi`. The three tests were
  first RED with the expected 404 responses, then passed with exact
  `[1, 2, 3]` payloads and zero active pool leases after every request.
- The complete framework suite passed 36/36. Flask/WSGI remains functionally
  compatible with a per-request event loop; the ASGI adapter smoke verifies
  the approved persistent-loop ownership path without claiming the later
  production process-server matrix.
- This documentation/test-harness change modifies no FastMssql runtime,
  dependency, package metadata, displayed `0.7.7` version, release or
  published artifact.

### Query-many hosted-wheel contract regression RED

- Created `test/query-many-hosted-gate-contract` from exact query-many
  candidate `74051228a80a5e498fbb443b1942cdf2dca95f1f` after the cumulative
  SQL-auth runner exposed one original-local-regression failure.
- Reproduced
  `test_hosted_gate_builds_extension_and_checks_configuration_contracts`
  alone: 1/1 failed in 0.02 seconds for the same deterministic assertion;
  the complete lane otherwise passed 1,250 tests and failed only this case.
- Root-cause tracing showed that the hosted workflow correctly appended the
  three installed-wheel query-many contracts, while the older exact-command
  assertion still required `-q` immediately after
  `tests/test_result_stream_contract.py`.
- The required correction is to extend the existing exact installed-wheel
  command contract with all three query-many paths. The assertion must not
  be weakened to accept tests elsewhere in the workflow.
- This RED evidence changes no runtime, test expectation, dependency,
  package metadata, displayed `0.7.7` version, release or published
  artifact.

### Query-many hosted-wheel contract correction

- Extended the existing exact installed-wheel command assertion with
  `test_query_many_contract.py`, `test_query_many_coordinator.py` and
  `test_query_many_stress_contract.py`, matching their already required
  workflow invocation.
- Preserved the stronger same-command and terminal `-q` contract; the fix
  does not accept those paths merely appearing elsewhere in the workflow.
- This test-harness-only correction changes no FastMssql runtime, workflow,
  dependency, package metadata, displayed `0.7.7` version, release or
  published artifact.

### Cumulative vendor-artifact cleanliness RED

- Created `test/cumulative-vendor-artifact-cleanliness` from exact
  cumulative candidate `e2beffce95e9b5abbfb3ffb027d73f3eaf1d1c6b`
  after every required runner lane passed but query-many stress truthfully
  recorded `worktree_dirty=true`.
- Traced the dirty state to `vendor/tiberius/Cargo.lock` and
  `vendor/tiberius/target/`, generated by required Tiberius commands before
  query-many stress. SQL-auth reports are generated later and therefore
  cannot explain that recorded value.
- Added a real `git check-ignore` contract for both vendor build products.
  It fails against the unchanged repository because neither path is ignored.
- The future fix must explicitly ignore only these known generated products;
  it must not weaken `worktree_is_dirty()` to hide arbitrary untracked source
  files.
- This RED branch changes tests and evidence only, with no runtime,
  dependency, package metadata, displayed `0.7.7` version, release or
  published artifact.

### Cumulative vendor-artifact cleanliness correction

- Added repository-rooted ignore rules only for the vendored Tiberius
  library's generated `Cargo.lock` and `target/` products.
- Preserved strict dirty-source detection for every other untracked file;
  `query_many_stress.py` and its `git status --porcelain` evidence were not
  weakened.
- This repository-hygiene correction changes no FastMssql/Tiberius runtime,
  dependency, package metadata, displayed `0.7.7` version, release or
  published artifact.

### Execute-many live audit status

- Marked only the sixth of seven batch/bulk slices,
  `Connection.execute_many()` and `Transaction.execute_many()`, as
  `VERIFIED_FORK` at evidence commit
  `9c02379028af7a94a0814d06aa86c16aa4b4d204`, whose verified runtime parent
  is `b270205128fc6bd3c951a3e822b600c9ad049ee9`.
- Added `docs/EXECUTE_MANY_VALIDATION_REPORT.md` and updated the live
  production-readiness audit with exact design/plan/RED/feature ancestry,
  the intended absent-API RED, atomic/partial/caller-Transaction behavior,
  schema-2 metrics, timeout/cancellation/security retirement and remaining
  risks.
- Recorded the fresh exact-runtime gate: 407/407 canonical IDs, 424 strict,
  16 true-async, 33 framework, 6 resilience, 12 load and 1,183 original
  local regression tests; FastMssql Rust passed 116/116 and vendored
  Tiberius 168/168 plus its 2/2, 8/8 and 8/8 real-SQL integration lanes.
- Recorded all seven clean-source stress profiles from 1,000 through 99,999
  parameter sets, including the 10,000-set `atomic=False` profile. Every
  profile had exact affected/persisted counts, at most one session and one
  chunk, zero error/timeout/violation, successful smoke and zero teardown
  sessions.
- Recorded the isolated macOS arm64 ABI3 wheel SHA-256
  `233008ea32a57a3689822edcd0df99cd9bc9fc4207484373a01812a327492097`,
  imported without source-tree paths, with 40/40 offline, 11/11 `EMANY` and
  3/3 representative real-SQL tests plus `pip check`.
- Recorded the exact technical graph at 176 files, 3,914 nodes and 48,297
  edges, reconciled dynamic Python/PyO3 coverage manually, and preserved the
  public GitHub truth: zero workflow runs and zero check-runs for `9c02379`,
  therefore hosted status `NOT RUN`.
- Added `execute_many` to the future upstream candidate intake as a
  separately reviewable change. It still requires a fresh upstream rebase,
  reproduction, exact hosted gate and explicit approval before any
  original-repository PR.
- Kept `query_many(concurrency=...)` visibly open as the seventh batch/bulk
  slice. This status-only branch changes no runtime behavior, package
  metadata, displayed `0.7.7` version, release state or published artifact.

### Execute-many exact-candidate validation evidence

- Integrated the deterministic compatibility-bulk timeout correction into
  `feat/execute-many` and verified the exact clean technical candidate
  `b270205128fc6bd3c951a3e822b600c9ad049ee9`.
- The canonical SQL-auth runner passed all 407 required matrix IDs, 424
  strict tests, 16 true-async tests, 33 framework tests, 6 resilience tests
  with 12 intentional deselections, 12 load tests with 6 intentional
  deselections, and 1,183 original-local-regression tests. No required case
  failed, errored, skipped or remained not run.
- Root Rust passed 116/116; vendored Tiberius passed 168/168 library tests,
  2/2 token-safety SQL-auth tests, 8/8 bulk-column-subset SQL-auth tests and
  8/8 response SQL-auth tests. Rust and vendored formatting/Clippy gates
  passed with warnings denied.
- The canonical ResultStream stress gate passed 1,000 operations at
  concurrency 64 with zero failures/timeouts, 2,707.44 operations/second,
  at most eight SQL sessions, a 6.900 ms maximum event-loop gap, 16,252,928
  bytes RSS growth and a successful post-load smoke query.
- Execute-many stress passed all seven exact-source profiles: atomic sync and
  async inputs at 1,000, 10,000 and 99,999 parameter sets plus the 10,000-set
  sync `atomic=False` profile. Every profile persisted and affected the exact
  requested count, used at most one physical SQL session, retained at most
  1,000 sets/2,000 cells, completed its smoke query and left zero application
  sessions after teardown.
- Across those profiles throughput was 2,344.08–2,706.15 parameter sets per
  second, maximum event-loop gap was 22.548 ms against the 100 ms hard limit,
  and maximum RSS growth was 4,603,904 bytes against the 64 MiB hard limit.
  The partial profile reported exactly 10,000 acknowledged committed sets;
  every profile recorded one successful `execute_many` operation and no
  internal `execute` metric.
- Built
  `fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl` from that exact SHA
  with SHA-256
  `233008ea32a57a3689822edcd0df99cd9bc9fc4207484373a01812a327492097`.
  A fresh external Python 3.12 environment imported it from isolated
  `site-packages` with `PYTHONPATH` removed, then passed 40/40 offline
  execute-many contracts, 11/11 real `EMANY-001`–`EMANY-011` cases, 3/3
  representative native-bulk/query/ResultStream SQL-auth smoke tests and
  `pip check`.
- The first sandboxed `uv` wheel command stopped before compilation in macOS
  `SystemConfiguration`; the identical command outside that sandbox built
  successfully. The first import probe contained a command-line quoting
  `SyntaxError` before import; the corrected probe proved the isolated module
  path and package version. Neither harness issue changed source or masked a
  FastMssql failure.
- A fresh graph built on the exact candidate and matching HEAD contains 3,914
  nodes and 48,297 edges across 176 files. The cumulative feature diff
  touches 42 files and 102 statically detected flows. Both public
  `execute_many` wrappers map to the strict real-MSSQL test file; the shared
  coordinator's dynamic protocol tests are not represented by direct static
  edges, so its graph warning was reconciled against the 40 offline
  coordinator contracts and the complete SQL-auth/non-regression gates.
- Every changed source/test/documentation hunk was reviewed after graph
  analysis. `git diff --check` passed; high-confidence token/private-key
  scanning found nothing; all password-shaped matches were environment-fed
  arguments or explicit `not-used` disconnected probes; no `.env`, wheel,
  build cache or runner artifact is tracked.
- `origin` remains `galeamarcel/FastMssql`; the original
  `Rivendael/FastMssql` remote remains fetch-only with push URL exactly
  `DISABLED`. This evidence does not publish the wheel, create a release or
  create an original-repository pull request.
- This documentation-only evidence update does not change runtime behavior,
  package metadata, the displayed `0.7.7` version or release state.

### Compatibility-bulk timeout phase boundary

- Restored the approved compatibility `bulk_insert()` boundary by creating
  its operation deadline only after pool checkout and construction of the
  pooled-operation guard.
- Preserved bounded first-chunk conversion before pool work, so first-chunk
  shape/type failures remain zero-I/O and zero-lease.
- Reused the same post-checkout deadline for `BEGIN`, every SQL chunk,
  later-chunk conversion and `COMMIT`; no per-chunk reset or retry was added.
- Left native bulk iterable and `execute_many()` unchanged because their
  later approved sequence contracts explicitly include acquisition.
- The corrected extension loaded from the exact worktree and passed focused
  `TIME-006` `1/1`, the bounded repetition `20/20`, timeout/strict/bounded
  coverage `43/43`, and legacy batch/bulk in its canonical
  original-local-regression lane `38/38`.
- Root Rust tests passed `116/116`; fmt, Clippy with warnings denied, Ruff,
  compileall and diff checks passed. The first direct Cargo invocation omitted
  the required canonical `PYTHONHOME` and failed before the test harness at
  embedded-Python codec initialization; rerunning with the runner's exact
  environment passed all 116 tests.
- Graph review found five statically affected shared flows and linked 40 tests
  to compatibility `bulk_insert()`. Its simultaneous untested warning is a
  PyO3/dynamic-wrapper mapping limitation contradicted by the focused,
  strict, bounded and legacy executions above.
- A first combined Python invocation omitted the legacy fixture's
  `FASTMSSQL_TEST_CONNECTION_STRING`; its 38 setup failures were reproduced
  as `connection_string=None`, then all 38 passed in the canonical lane.
- This unreleased fork fix changes no public API, displayed `0.7.7` version,
  release state or package metadata.

### Compatibility-bulk timeout phase-boundary RED

- Extended existing canonical `TIME-006` with a size-one pool held for 0.95
  seconds after `pending_gets == 1`, longer than its 0.85 second operation
  budget but shorter than its separate 2.0 second acquire budget.
- Kept 4.001 rows/five compatibility chunks and required exactly two real SQL
  requests under a 500 ms trigger delay, typed fail-closed timeout metadata
  and zero rows after rollback.
- Added a bounded opt-in runner with a 20-iteration default and strict
  `1..100` input validation, isolated temporary result output and immediate
  failure propagation.
- Scheduled the native PyO3 awaitable with `asyncio.ensure_future()` after the
  first RED attempt proved that `asyncio.create_task()` correctly rejects a
  preconstructed `Future`; this was a test-harness correction only.
- This branch changes tests and test harness only. The unchanged runtime
  observed `pending_gets == 1`, remained pending through the hold, then
  expired its operation deadline immediately after checkout and submitted
  exactly zero bulk requests instead of the required two.
- The deterministic RED completed in 1,37 seconds; the typed
  `OperationTimeoutError` metadata remained correct, so the failing dimension
  is the acquire/operation boundary rather than error classification.
- No runtime, public API, displayed `0.7.7` version, release state or package
  metadata changes here.

### Compatibility-bulk timeout phase-boundary corrected plan

- Corrected the initial test-only hypothesis after correlating `TIME-006`,
  the foundational timeout specification and bounded-buffering commit
  `adb6637`.
- Required a deterministic RED that holds a size-one pool beyond the
  operation budget but within the acquire budget and waits for the exact
  `pending_gets == 1` precondition.
- Limited the runtime fix to moving the existing compatibility-bulk deadline
  after checkout, while preserving pre-acquire first-chunk conversion and one
  deadline across `BEGIN`, later conversion, all chunks and `COMMIT`.
- Retained exact-two request observation, 20 consecutive SQL-auth
  repetitions, complete execute-many gates and fork-only publication.
- Corrected the executable plan to schedule the PyO3 awaitable with
  `asyncio.ensure_future()`; `asyncio.create_task()` rejects an already
  constructed native `Future`.
- This documentation-only plan correction changes no runtime, public API,
  displayed `0.7.7` version, release state or package metadata.

### Compatibility-bulk timeout phase-boundary corrected design

- Recorded that the 160/250 ms intermittent failure exposed more than a stale
  test margin: compatibility `bulk_insert()` currently creates its operation
  deadline before first-chunk conversion and pool acquisition.
- Resolved the conflict between the foundational post-checkout operation
  boundary and the later bounded-buffering instruction in favor of separate
  acquire and operation phases for the compatibility API.
- Specified a size-one saturated-pool RED with acquire/operation/hold values
  `2.0/0.85/0.95` seconds and a 500 ms trigger, making the unchanged runtime
  expire before application SQL and the corrected runtime observe exactly
  two requests.
- Kept native bulk iterable and `execute_many()` out of scope because their
  later approved sequence designs explicitly include acquisition in broader
  public-method deadlines.
- The first test-only diagnosis was corrected through descendant commits,
  without rewriting published history; no runtime changes occur in this
  documentation commit.

### Execute-many security-retirement characterization

- Extended canonical `EMANY-009` on its dedicated test branch to prove that
  a successful `EXECUTE AS` response records exactly one successful
  `execute_many` operation while the caller Transaction becomes fail-closed.
- Required query, commit and rollback to reject the retired Transaction,
  verified idempotent close, and proved that the size-one pool replaces the
  physical session by comparing `connection_id` rather than reusable SPIDs.
- Added a successful query and rollback on the replacement Transaction plus
  an independent DMV teardown assertion for zero application sessions.
- This test-only characterization changes no runtime, public API, displayed
  `0.7.7` version, release state or package metadata.

### Execute-many security-retirement characterization plan

- Added the executable test-only plan that extends canonical `EMANY-009`
  with successful `EXECUTE AS` response delivery, fail-closed Transaction
  state, exact operation metrics, physical replacement by `connection_id`
  and zero-session teardown on Docker SQL Server.
- Kept the approved lineage on separate design, test, feature and live-status
  branches, with a distinct runtime-fix branch required only if the
  deterministic characterization contradicts the existing policy.
- Required fresh source, full SQL-auth, 99.999-set stress, isolated-wheel,
  graph, credential and fork-only publication evidence before the sixth of
  seven batch/bulk slices can become `VERIFIED_FORK`.
- This documentation-only plan does not change runtime, public API, the
  displayed `0.7.7` version, release state or package metadata.

### Execute-many security-retirement contract design

- Reconciled the ordinary caller-Transaction success rule with the existing
  fail-closed policy for effective `EXECUTE AS`, `EXEC AS`, and `SETUSER`
  statements.
- Specified that a successfully consumed response remains a successful
  `execute_many` operation, while the impersonated physical session is
  retired, the caller Transaction becomes `Failed`, and its uncommitted work
  is not represented as durable.
- Kept `EMANY-009` as the single owner of caller-Transaction settlement
  semantics and required a distinct-`connection_id` pool-recovery proof
  rather than an unreliable SPID comparison.
- Selected a test-only characterization because the runtime already follows
  the approved ResultStream security policy; no public behavior, displayed
  `0.7.7` version, release state, or package metadata changes here.

### PyO3 uv matrix-harness correction

- Replaced the matrix runner test's zero-output `uv` stub with a bounded fake
  executable that emulates only the two interpreter-discovery expressions
  consumed by the production runner.
- Passed the active test interpreter and base prefix through explicit
  test-only environment variables; no local path is embedded in repository
  source or generated command evidence.
- Made unsupported fake Python expressions fail with exit 64 while unrelated
  `uv` invocations remain successful and silent, preserving the original
  display-name test's isolation from package, Docker and network work.
- Drove the new behavioral contract from explicit `NotImplementedError` to
  GREEN. Both focused harness tests passed 2/2, the complete matrix-contract
  file passed 27/27, and Ruff/diff checks passed.
- The correction changes only deterministic test infrastructure; it does not
  weaken runner discovery, modify runtime APIs, or alter the displayed
  `0.7.7` version and release state.

### PyO3 uv matrix-harness RED

- Preserved the complete-gate failure that exposed the harness boundary:
  root Rust 116/116 and every non-strict runner lane passed, while strict
  reported one failure among 423 tests because its sandboxed fake `uv`
  returned no interpreter discovery output.
- Added a focused behavioral contract for a fake uv executable that must
  return the configured executable/base prefix for the two supported
  `uv run python -c` expressions, reject any other Python expression and
  leave unrelated uv commands successful and silent.
- Observed the intended RED independently: the new test failed with the
  explicit `_write_fake_uv` `NotImplementedError`, not because of
  collection, import, Docker or SQL Server setup.
- This test-only change does not weaken fail-closed runner discovery or alter
  the displayed `0.7.7` version and release state.

### PyO3 uv embedded-runtime bootstrap fix

- Made the canonical local SQL-auth runner resolve the exact worktree
  interpreter and base installation through `uv run python` immediately after
  locked environment synchronization.
- Added explicit fail-closed validation for an absent, empty or invalid
  executable/base-prefix result; discovery failures cannot be hidden by a
  `readonly` declaration in the runner's intentionally non-`errexit` shell.
- Scoped `PYO3_PYTHON` and `PYTHONHOME` through `env` only to the existing
  `cargo test --locked` lane, leaving maturin, fmt/Clippy, vendored Tiberius,
  pytest and SQL-auth processes unchanged.
- Drove the focused PyO3 configuration contracts from one intended failure
  to 9/9 PASS; Ruff and Bash syntax checks also passed.
- Compiled and executed the raw test binary in a completely fresh external
  Cargo target against uv CPython 3.12: all 116 Rust tests passed with zero
  failures and no `/install`/`encodings` bootstrap error.
- This runner-only fix does not alter FastMssql runtime APIs, the displayed
  `0.7.7` version or release state.

### PyO3 uv embedded-runtime bootstrap RED

- Added a deterministic configuration contract requiring the canonical
  SQL-auth runner to discover the current worktree's `sys.executable` and
  `sys.base_prefix` through `uv run python`, reject invalid discovery and
  scope both `PYO3_PYTHON` and `PYTHONHOME` only to `cargo test --locked`.
- Preserved the existing manifest, hosted workflow, lock enforcement and
  failure-bypass contracts unchanged.
- Observed the focused contract on the unchanged runner: exactly one intended
  failure and eight passes. The new test failed because interpreter discovery
  was absent, not because of collection, import or environment setup.
- Retained the runtime reproduction evidence: a fresh target compiled 116
  Rust tests but uv CPython initialization retained `/install` and could not
  import `encodings`; the identical executable passed 116/116 with
  command-scoped `PYTHONHOME=sys.base_prefix`.
- This test-only change does not alter the displayed `0.7.7` version or
  release state.

### PyO3 uv embedded-runtime bootstrap design

- Documented a clean-target failure in the canonical local runner when raw
  PyO3 Rust tests embed the worktree's relocatable uv CPython: the test
  executable linked successfully but CPython retained its `/install` build
  prefix and could not import `encodings`.
- Confirmed the root cause without changing source: the identical executable
  passed all 116 Rust tests when `PYTHONHOME` was set to the selected
  interpreter's `sys.base_prefix`.
- Specified a test-first, command-scoped runner correction that resolves
  `PYO3_PYTHON` and `PYTHONHOME` through `uv run python`, without global
  environment mutation, hard-coded installation paths or runtime API
  changes.
- Added the executable branch-by-branch plan for immutable RED evidence, the
  minimal scoped runner fix, a completely fresh Cargo target and a zero-fail
  canonical SQL-auth gate before history-only integration.
- This documentation change does not alter the displayed `0.7.7` version or
  release state.

### Public bounded execute-many adapter

- Added the public `Connection.execute_many()` and
  `Transaction.execute_many()` wrappers for concrete lists plus lazy
  synchronous and asynchronous parameter-set producers.
- Kept concrete lists on the raw Rust fast path, while iterable sources use
  one private sequence, reserve before their first pull and retain at most one
  configured chunk.
- Added privacy-safe per-set preflight, global failure metadata, acknowledged
  partial-commit tracking and cancellation-shielded abort/producer-close
  cleanup.
- Kept an explicitly supplied invalid Connection `atomic=None` distinct from
  the Transaction adapter's omitted option so Rust validation cannot be
  bypassed.
- Made producer-boundary expiry prefer Python's exact next-set index over the
  raw sequence's necessarily synthetic no-active-statement fallback.
- Corrected the caller-Transaction isolation proof for the canonical
  `READ_COMMITTED_SNAPSHOT=OFF` database: `READPAST` observes zero committed
  rows without misclassifying SQL Server's expected lock wait as a driver
  deadlock.
- Corrected the focused cancellation harness to inspect `CancelledError` at
  the API boundary; Python 3.12 reconstructs the exception for waiters of a
  terminal cancelled `Task` and therefore cannot preserve instance
  attributes or cleanup causes there.
- Aligned the late-invalid-set coordinator assertion with the approved lazy
  contract: activation follows the first valid set, while no incomplete
  chunk is sent after a later preflight failure.
- Published the exact public/raw type-stub split and private sequence progress
  contract, plus README guidance for atomic, per-chunk and caller-Transaction
  use.
- Verified 35 focused execute-many contracts, 64 unchanged native-bulk,
  metrics, result-stream and packaging contracts, all 116 Rust tests,
  warning-free all-target Clippy, Rust formatting, Ruff and `compileall`.
- Exercised `EMANY-001`–`EMANY-011` together on the approved Docker SQL
  Server after the focused fixes; all 11 cases passed.
- This unreleased adapter does not change package metadata, the displayed
  `0.7.7` version or release state.

### Stateful execute-many native engine

- Added strict raw-list `Connection.execute_many()` and
  `Transaction.execute_many()` entry points plus the private
  `_ExecuteManySequence` used by the bounded iterable adapter.
- Added checked, privacy-safe parameter-set conversion and affected-row
  accounting with global set indices, the existing 2,098-parameter limit and
  direct-batch parity for scope-sensitive parameter-free DDL.
- Added one-session atomic and acknowledged per-chunk settlement modes,
  explicit caller-Transaction reservation/rollback-only handoff, one absolute
  deadline and one `execute_many` metric observer without internal
  execute/begin/commit/rollback metric inflation.
- Added fail-closed cancellation, timeout, unknown-COMMIT and dropped-sequence
  cleanup, including truthful confirmed-commit progress returned by private
  abort cleanup.
- Preserved the existing security-context SQL retirement policy so an
  `execute_many` statement classified as session impersonation can never
  return its physical connection to the pool.
- Added focused Rust invariants for validation, list resizing, empty
  neutrality, checked overflow, global/privacy-safe metadata, commit
  acknowledgement boundaries and caller-Transaction reservation states.
- Verified all 116 Rust tests and warning-free all-target Clippy, built the
  release extension, and exercised the raw API against Docker SQL Server for
  atomic success/rollback, acknowledged partial commits, list-resize
  rollback, caller-Transaction neutrality/rollback-only handoff, schema-2
  metric isolation and security-SQL physical retirement. SQL Server reused
  the same SPID during the retirement proof, while its physical
  `connection_id` changed as required.
- This feature work does not change the displayed `0.7.7` version or release
  state.

### Operation metrics schema 2

- Added the stable `execute_many` metric slot between `execute` and
  `query_batch`, advancing the current snapshot/stub contract from schema
  version 1 to 2 with exactly 14 ordered operation keys.
- Added a Rust invariant that every active operation maps to its exact array
  index while the reserved non-I/O `transaction` name remains unmetered.
- Updated current public stubs and README documentation without rewriting
  historical schema-1 evidence; no execute-many operation is emitted until
  its state machine is implemented.
- Rebuilt the editable extension and verified all 98 Rust tests, warning-free
  all-target Clippy, all 8 offline Python schema contracts and all 11 real
  SQL-auth operation-metrics cases.
- This schema migration does not change the displayed `0.7.7` version or
  release state.

### Shared bounded producer coordinator

- Extracted native-bulk's synchronous/asynchronous protocol acquisition,
  deadline-aware pulls, bounded chunking and strong-referenced terminal
  cleanup into
  `python/fastmssql/_bounded_sequence.py`.
- Kept native-bulk validation, normalization, raw sequence construction and
  public dispatch feature-specific, including the concrete-list fast path and
  existing task names.
- Extended the boundedness contract across both the feature adapter and the
  shared coordinator; all 30 focused offline contracts and all 9 real
  SQL-auth iterable native-bulk cases passed after extraction.
- This internal refactor does not change the displayed `0.7.7` version or
  release state.

### Bounded execute-many immutable RED coverage

- Added deterministic public/raw/stub contracts for list, synchronous-
  iterable and asynchronous-iterable `execute_many()` sources, including the
  Connection-only `atomic` option and private bounded sequence protocol.
- Added fake-sequence tests for reservation-before-pull, async protocol
  preference, empty-input neutrality, one-chunk backpressure, global and
  confirmed-commit metadata, deadline precedence, repeated-cancellation
  cleanup shielding, timeout and cleanup-error precedence.
- Required successful private `abort()` cleanup to return Rust's exact active
  index and confirmed-commit progress for Python-owned primary exceptions.
- Advanced the current operation-metrics requirement to schema version 2
  with one exact `execute_many` key while leaving runtime implementation for
  the feature branch.
- Added `EMANY-001`–`EMANY-011` to the canonical SQL-auth specification and
  raised its exact unique-case contract from 396 to 407.
- Added real SQL Server cases for API/validation/empty behavior, typed ordered
  DML, TDS-gated pulls, atomic rollback, acknowledged partial commits,
  cancellation, timeout, caller-Transaction state, stored procedures,
  metric isolation and deterministic lost atomic-COMMIT acknowledgement.
- Added a bounded, privacy-safe execute-many stress harness for sync/async
  1,000, 10,000 and explicit 99,999-set profiles plus the 10,000-set
  `atomic=False` profile, with external-only evidence paths and hard gates for
  retained sets/cells and exact confirmed commits.
- Observed focused offline RED as `37 failed, 9 passed`; all failures were the
  absent API/coordinator or schema-2 contract, while stress contracts passed
  `5/5`, matrix contracts passed `26/26` and native-bulk contracts passed
  `30/30`.
- On the dedicated Docker SQL-auth server, all 86 pre-existing batch,
  native-bulk and transaction baseline tests passed. All 11 `EMANY` cases
  failed through the absent `execute_many` API, without an authentication,
  build or fixture error.
- The real 1,000-set sync stress reproduction failed through the same absent
  API after zero pulls, observed at most one candidate SQL session and
  confirmed zero candidate sessions after teardown.
- This RED branch changes requirements, tests and test tooling only; it does
  not modify runtime behavior, package metadata, the displayed `0.7.7`
  version or release state.

### Bounded execute-many focused design

- Added the sixth-slice design for repeating one parameterized statement over
  bounded list, synchronous-iterable or asynchronous-iterable parameter
  sets.
- Chose a shared audited Python producer coordinator over a private Rust
  `_ExecuteManySequence`, keeping iterator/GIL behavior in Python and all
  lease, transaction, deadline, metric and connection-disposition decisions
  in Rust.
- Defined atomic-by-default Connection semantics, explicit per-chunk commits
  for `atomic=False`, settlement-neutral active Transaction semantics and
  privacy-safe global/confirmed-commit error metadata.
- Defined operation-metrics schema version 2 with an exact `execute_many`
  key and no internal `execute`/settlement metric inflation.
- Defined `EMANY-001`–`EMANY-011`, deterministic RED/cancellation contracts,
  isolated-wheel gates and bounded sync/async stress through 99,999
  parameter sets.
- Added the executable TDD plan with immutable RED ancestry, a semantics-
  preserving shared-coordinator refactor, operation-metrics schema migration,
  private Rust sequence, wrapper exposure, real SQL-auth/fault-proxy cases and
  exact fork-only publication gates.
- Made the plan's commit-acknowledgement-loss, active-Transaction reservation,
  empty-input, abnormal producer-close and native-bulk non-regression
  checkpoints explicit before any implementation begins.
- Clarified the cross-language cancellation boundary: successful private
  `abort()` cleanup returns Rust's active-index and confirmed-commit progress
  so Python can annotate its own primary producer/cancellation exception
  exactly rather than guessing.
- Kept `query_many()`, byte-level LOB streaming, remaining enterprise SQL
  types, release publication and any original-repository PR outside this
  slice.
- This documentation-only change does not modify runtime behavior, package
  metadata, the displayed `0.7.7` version or release state.

### Native bulk iterable backpressure design

- Added the focused fifth-slice design for synchronous and asynchronous
  producer input to `native_bulk_insert()`.
- Added the executable branch-by-branch TDD plan covering offline RED,
  canonical Docker SQL-auth cases, the private Rust sequence, the bounded
  Python coordinator, isolated-wheel validation and sync/async stress through
  99,999 rows.
- Added test-only RED contracts requiring the public iterable/raw-list surface
  split, one-time protocol acquisition, pre-pull transaction reservation,
  bounded sync/async backpressure and cancellation-safe abnormal cleanup.
- Extended the canonical SQL-auth matrix from 387 to 396 unique cases with
  `BULK-013`–`BULK-021`, covering real connection/transaction atomicity,
  producer/conversion failure, cancellation, timeout and TDS-gated pull
  counts.
- Added a dedicated lazy sync/async iterable stress harness with hard
  1,000/10,000/99,999-row gates for buffered cells, RSS, event-loop gaps,
  physical sessions, metrics, exact persistence and teardown, including typed
  timeout classification and unmasking cleanup evidence.
- The immutable RED branch contains only requirements and test
  infrastructure; it does not implement iterable input.
- Split native bulk target validation from row-chunk preparation, added
  checked global row/parameter diagnostics and exposed a crate-private
  exact-count one-chunk engine while preserving the concrete-list public
  path at global row base zero.
- Added Rust RED state-machine contracts requiring an exclusive
  `BulkProducing` caller-transaction reservation, distinct pre-wire and
  post-wire abort states, bounded/terminal private-sequence progress with
  global indices, and one bulk metric whose duration starts before the first
  producer row.
- Implemented the underscore-prefixed raw `_NativeBulkSequence` with
  side-effect-free construction, pre-pull caller reservation, lazy
  Connection lifecycle/pool activation, one absolute deadline and metric,
  exact per-chunk/global progress, caller-neutral finish, Connection-owned
  commit/rollback and fail-closed cancellation/drop cleanup. Repeated healthy
  activation is idempotent, while any post-wire accounting inconsistency
  permanently requires abort instead of allowing settlement.
- Added the public bounded iterable coordinator: concrete lists retain the
  unchanged raw Rust fast path, while synchronous and asynchronous producers
  reserve before their first pull, prefer the async protocol, hold at most one
  configured chunk, share the absolute Rust deadline and finish shielded
  abort/producer-close cleanup before re-raising the original failure.
- Fixed the cancellation handoff between a cancelled PyO3 bulk awaitable and
  mandatory sequence cleanup: `abort()`/`expire()` now wait for the cancelled
  call to release the private sequence lock, so database state and the single
  operation observer are terminal before the original exception is re-raised.
- Closed the reservation-completion cancellation race by assigning cleanup
  ownership before awaiting `reserve()`, ensuring an effective caller
  transaction reservation is explicitly released before cancellation is
  re-raised rather than relying on asynchronous drop cleanup.
- Restored the disabled operation-metrics fast path so it does not read the
  monotonic clock before confirming that a metrics registry exists.
- Made the result-stream retirement proof account for SQL Server's bounded,
  asynchronous DMV visibility after a physical TCP session is removed from
  the pool, while still requiring retirement before any recovery checkout.
- Corrected the iterable identifier contract to use a structurally invalid
  raw identifier; safely bracket-quoted SQL Server identifier characters are
  not misclassified as injection.
- This unreleased development slice does not change package metadata, the
  displayed `0.7.7` version or release state.
- Selected a bounded Python coordinator over one private Rust sequence so
  all chunks retain one lease, transaction, absolute deadline, metric and
  global diagnostic index space.
- Preserved the concrete-list raw-extension fast path and kept the raw stub
  intentionally list-only while widening only the public wrapper contract.
- Regenerated the canonical SQL-auth evidence at reservation-race fix commit
  `cb60cf8192c480809b2283742572ca265a5e0c50`: all 396 required matrix
  cases, 412 strict tests, 16 async tests, 33 framework tests, 6 resilience
  tests, 12 load tests and 1,141 original-local-regression tests pass with no
  failures, errors, skips or missing evidence.
- Defined deterministic `BULK-013`–`BULK-021` RED contracts, Docker
  SQL-auth evidence, isolated-wheel gates and 1,000/10,000/99,999-row stress
  profiles.
- Kept `execute_many()`, `query_many()`, byte-level LOB streaming and the
  unsupported SQL type families as separate visible slices.
- This documentation-only change does not modify runtime behavior, package
  metadata, the displayed `0.7.7` version or release state.

### Native bulk iterable backpressure audit status

- Marked the fifth of seven approved batch/bulk slices `VERIFIED_FORK` at
  technical commit `13c91c06bcf27da0e00bc514364c42e591b0632f`.
- Added
  `docs/NATIVE_BULK_ITERABLE_BACKPRESSURE_VALIDATION_REPORT.md` with exact
  design/RED/feature ancestry, the reservation-cancellation race found during
  self-review and its deterministic RED/GREEN proof.
- Recorded the canonical runtime gate at `cb60cf8`: 396/396 required IDs,
  412 strict, 16 async, 33 framework, 6 resilience, 12 load and 1,141
  original-local-regression tests, all without fail/error/skip/not-run.
- Recorded exact-HEAD sync/async stress through 99,999 rows, artifact hashes,
  one-session/one-chunk invariants and zero sessions after teardown.
- Recorded isolated wheel SHA-256
  `cc6f114a9a5f84accb4388b46197aed1d930acb410ab4fd339481e23ce63e30d`
  with 43 offline, 9 iterable SQL-auth and 4 representative SQL tests from
  `site-packages` without `PYTHONPATH`.
- Recorded the exact graph state and the GitHub status truthfully: zero
  workflow/check runs for `13c91c0`, therefore candidate hosted status
  `NOT RUN`; ancestral Linux/macOS/Windows and RustSec results are not
  presented as candidate success.
- Updated the future upstream candidate intake to keep the Tiberius
  primitive, list fast path and iterable layer reviewable, with a fresh
  rebase, reproduction, exact hosted gate and explicit approval required
  before any original-repository PR.
- Kept `execute_many()`, `query_many()`, byte-level LOB streaming and
  unsupported SQL type families open. This status-only change does not alter
  runtime behavior, package metadata, the displayed `0.7.7` version or
  release state.

### Checkout reset and first-statement DDL audit status

- Closed the live `OPEN_REPRO_REQUIRED` observation with exact
  design/RED/Tiberius/fix ancestry ending at technical commit `9e86cc4`.
- Recorded 452/452 deterministic SQL-auth, 1,106/1,106 original local
  regression, 6/6 resilience, 82/82 FastMssql Rust, 168/168 vendored
  Tiberius and 8/8 direct reset SQL-auth results.
- Recorded the isolated ABI3 wheel SHA-256
  `37795d74a8aa275b7ef1b0295f0b2c46ee0186c03d9fab4f3323a337c365b280`
  and its offline plus real SQL-auth installed-package gates.
- Added privacy-safe pre/post stress evidence for 1,000, 10,000 and 99,999
  operation/ResultStream profiles and a 99,999-transaction shared-pool
  profile.
- Documented the mandatory private-reset round-trip cost when
  `test_on_check_out=False` and a reused lease is `NeedsReset`; no zero-cost
  claim is made for that policy.
- Documented RSS high-water under repeated pool creation as a separate
  residual allocator/native-buffer audit item rather than attributing it to
  the checkout-reset fix.
- Recorded that the exact candidate has no hosted GitHub Actions run; the
  latest ancestral Linux/macOS/Windows and RustSec successes are identified
  separately and are not presented as candidate success.
- Updated PR-15 to require a clean Tiberius immediate-reset slice followed by
  the FastMssql checkout-policy slice, with a fresh rebase, exact hosted gate
  and explicit approval before any original-repository PR.
- All documentation and evidence remain on Marcel Galea's fork. This status
  change does not modify runtime behavior, package metadata, the displayed
  `0.7.7` version or release state.

### Immediate checkout reset and pristine application batches

- Added an additive vendored-Tiberius `Client::reset_connection()` primitive
  that sends and fully drains RESETCONNECTION plus the required
  `READ COMMITTED` baseline before returning.
- Added an explicit checkout-action matrix that separates mandatory session
  reset from the optional health probe.
- The internal bb8 checkout hook now remains enabled for every policy:
  `Clean + False` performs no wire I/O, `NeedsReset + False` performs a private
  reset, and the enabled policy combines reset with validation when required.
- Removed deferred reset arming from application and pooled-transaction paths,
  so trigger, procedure, function and view definitions remain pristine.
- Schema-qualified the module-DDL regression fixtures so the scalar-function
  invocation reaches the reset assertion instead of relying on a default
  schema lookup.
- Checkout reset and validation are fail-closed under the acquisition
  deadline; cancellation or timeout leaves the physical session broken and
  prevents application SQL from starting.
- A killed idle session is now rejected and replaced by the private checkout
  reset before `ping()` sends application SQL; the readiness call succeeds on
  the replacement without retrying an already-started application request.
- Post-wire timeout and forced-shutdown fixtures now let the private checkout
  reset finish before gating a later application response, preserving their
  exact operation-phase and unknown-outcome assertions.
- Documented that `test_on_check_out=False` disables only the optional health
  probe and that a reused lease may therefore incur a private reset round trip.
- This runtime fix does not modify package metadata, the displayed `0.7.7`
  version or release state.

### Immediate Tiberius reset RED contract

- Added a no-skip real SQL-auth Tiberius contract requiring an explicit
  `Client::reset_connection()` call to send and fully drain RESETCONNECTION
  before the next application request.
- The test contaminates isolation, session context, database, session options,
  context info and a local temporary object, then requires the same SPID and
  complete login-baseline restoration.
- The first application batch after reset is a trigger definition; the test
  fires it and verifies one exact side effect.
- Cleanup uses an independent SQL-auth client and combines primary, panic and
  cleanup failures without swallowing any error.
- The expected source-baseline failure is a compiler error because the
  additive async reset method does not exist yet.
- This test-only change contains no runtime fix and does not modify package
  metadata, the displayed `0.7.7` version or release state.

### Checkout reset and first-statement DDL RED contract

- Added real SQL-auth contracts requiring trigger, procedure, function and
  view definitions to remain pristine after a reused one-connection lease
  with the optional checkout probe disabled.
- The same-SPID trigger contract contaminates transaction isolation first,
  then requires `READ COMMITTED`, one exact trigger side effect and no physical
  replacement after mandatory reset.
- Added a deterministic downstream-gate contract requiring a stalled reset to
  expire under the acquire deadline before the unique application write
  reaches SQL Server, retire the uncertain session and recover on a new
  physical identity.
- Raised the canonical SQL-auth matrix from 384 to 387 unique IDs and aligned
  its report/complete-evidence count contracts.
- The expected source-baseline failures are SQL Server's first-statement DDL
  rejection and an operation-phase rather than acquire-phase timeout.
- This test-only change contains no runtime fix and does not modify package
  metadata, the displayed `0.7.7` version or release state.

### Checkout reset and first-statement DDL design

- Added the approved fail-closed design for completing mandatory pooled
  session reset before application SQL when
  `PoolConfig(test_on_check_out=False)`.
- Separated the optional checkout health probe from mandatory cross-lease
  session isolation while keeping reset inside bb8's acquisition timeout and
  cancellation-safe checkout wrapper.
- Selected an additive immediate-reset primitive in vendored Tiberius so
  first-statement module DDL is never prefixed or rewritten by the driver.
- Added the executable implementation plan with separate public RED,
  Tiberius compile-RED, runtime-fix and audit-status branches, exact Docker
  SQL-auth, timeout, isolated-wheel, graph and self-review gates.
- Anchored the execution lineage to the exact design-plus-plan commit so every
  later RED, fix and status branch retains both approved documents in ancestry.
- Defined separate RED/fix/status branches, same-SPID SQL-auth isolation
  contracts, fault/deadline recovery, isolated-wheel gates and load profiles
  through the approved 99,999-operation ceiling.
- The expected private reset round trip for reused leases with the optional
  probe disabled is explicit and must be quantified; the default/health
  enabled path retains one combined reset/health request.
- This documentation-only change does not modify runtime behavior, package
  metadata, the displayed `0.7.7` version or release state.

### Native TDS bulk insert audit status

- Updated the live production-readiness audit to the exact cumulative feature
  SHA `428bc7471f61376294a8cb0f43587e86dd76ed9d`.
- Recorded the design/plan, principal RED contract, five corrective test
  commits and final feature ancestry for the fourth of seven batch/bulk
  slices.
- Added the privacy-safe native-bulk stress report for clean-source profiles
  of 1,000, 10,000 and 99,999 rows. Every primary/probe count was exact, with
  one physical session, zero error/timeout, post-load smoke and zero teardown
  sessions.
- The 99,999-row profile recorded approximately 188,628 rows/second, p99
  13.389 ms, RSS growth 3,981,312 bytes and maximum event-loop stall
  8.266 ms under the explicit 64 MiB/100 ms/60 s budgets.
- Recorded the final unit, SQL-auth, compatibility and isolated-wheel gates,
  including the wheel SHA-256 and the graph's static PyO3/vendor indexing
  limitations.
- Classified the failed unapproved localhost run and `uv` macOS
  system-configuration panic as sandbox-policy failures only after their
  approved retries passed.
- Recorded a separate `OPEN_REPRO_REQUIRED` observation for first-statement
  DDL after a pending pool reset when `test_on_check_out=False`; it is not
  classified as a native-bulk regression and will use its own RED/fix branch.
- This documentation-only status change does not modify runtime behavior,
  package metadata, the displayed `0.7.7` version or release state.

### Native TDS bulk insert implementation

- Added explicit list-only `Connection.native_bulk_insert()` and
  `Transaction.native_bulk_insert()` APIs over the ordered-column TDS bulk
  primitive, with exact server-target-guided conversion and checked affected
  counts.
- The connection form keeps one pooled physical lease and one SQL transaction
  across every chunk. The transaction form remains settlement-neutral and
  enters an explicit rollback-only state after a reusable post-wire failure.
- Added mandatory request finalization or fail-closed connection retirement,
  one absolute operation deadline, cancellation/forced-shutdown handling and
  privacy-safe global row/column/parameter diagnostics.
- Local row-encoding failures discovered only after `send()` starts now
  retire the partial bulk stream immediately instead of issuing cleanup SQL
  on an undrained request.
- Extended the additive ordered Tiberius path with constraint checking,
  trigger firing, explicit-NULL preservation and XML-as-NVARCHAR(MAX) bulk
  wire normalization while keeping compatibility `bulk_insert()` unchanged.
- Added an atomic native-bulk stress harness. Real Docker SQL-auth profiles
  passed for 1,000, 10,000 and the explicitly enabled 99,999 rows with exact
  duplicate primary/probe persistence, one physical session, stable identity,
  event-loop progress, bounded RSS and zero teardown sessions.
- The 99,999-row primary call completed at approximately 165,430 rows/second
  on the local validation host; the one-chunk transaction-call probe recorded
  p99 approximately 23.1 ms, RSS growth 4,554,752 bytes and maximum event-loop
  stall approximately 7.9 ms under explicit 64 MiB/100 ms/60 s budgets.
- These measurements cover concrete list input only and do not claim iterable
  backpressure, byte-level LOB streaming, `execute_many()` or `query_many()`.
  Package metadata, the displayed `0.7.7` version and release state remain
  unchanged.

### Native TDS bulk wire-encoding retirement RED contract

- Extended `BULK-010` with a real UTF-8 `VARCHAR(1)` boundary where one
  Unicode character exceeds the target's encoded-byte capacity only inside
  Tiberius row encoding.
- Required a post-`send()` local encoding failure to retire the physical
  connection immediately, without issuing cleanup SQL on the undrained bulk
  stream or attaching a secondary cleanup error.
- The regression also requires zero persisted rows, a replacement physical
  identity and successful post-fault pool smoke. This test-only change does
  not change runtime behavior, package metadata, the displayed `0.7.7`
  version or release state.

### Native TDS bulk stress row-shape RED contract

- Added a focused executable contract requiring the stress harness to build
  each concrete native-bulk row as a Python `list`, matching the deliberately
  strict list-only public API.
- The expected RED is the absent `build_rows()` helper after real Docker
  baselines exposed `ConversionError(reason="row_must_be_list")` for tuple
  rows. This test-only change does not change runtime behavior, package
  metadata, the displayed `0.7.7` version or release state.

### Native TDS bulk stress failure-reporting RED contract

- Added a focused executable contract requiring an operation failure before
  resource sampling to remain the primary reported failure instead of being
  masked by arithmetic on unavailable RSS or event-loop metrics.
- The expected RED is a `TypeError` from comparing `None` with a numeric
  budget. This test-only change does not change runtime behavior, package
  metadata, the displayed `0.7.7` version or release state.

### Native TDS bulk transaction fixture determinism

- Made `BULK-011` verify transaction neutrality directly on the pinned
  transaction session with `@@TRANCOUNT = 1` and `XACT_STATE() = 1`.
- Removed the pre-settlement cross-session table read, which blocks under
  SQL Server `READ COMMITTED` when `READ_COMMITTED_SNAPSHOT` is disabled and
  therefore tested a database isolation setting rather than driver behavior.
- The post-rollback count remains the persistence proof. This test-only
  correction does not change runtime behavior, package metadata, the
  displayed `0.7.7` version or release state.

### Native TDS bulk stress RED contract

- Added an executable contract for a bounded native-bulk stress harness with
  exact `ROWS:CHUNK_SIZE` profiles, a 99,999-row hard ceiling and explicit
  opt-in for the extended profile.
- Required atomic metrics output, deterministic percentile calculation and
  explicit RSS, event-loop-stall and operation-timeout controls.
- The expected RED is the absent
  `scripts/sql_auth/native_bulk_stress.py` runner. This test-only change does
  not implement the harness, change runtime behavior, package metadata, the
  displayed `0.7.7` version or release state.

### Native TDS bulk insert RED coverage

- Raised the canonical SQL-auth matrix from 377 to 384 unique IDs and added
  `BULK-006` through `BULK-012` for the exact public surface, ordered
  subsets, target-guided values, restricted targets, multi-chunk rollback,
  rollback-only transaction behavior and cancellation/timeout recovery.
- Added offline raw/wrapper/stub/source contracts, seven Docker SQL-auth
  contracts and public vendored-Tiberius metadata-bridge contracts without
  changing production code.
- The canonical matrix contract passes `26/26` with 384 unique IDs and
  one-to-one strict-test attachment.
- The observed vendored RED is exactly `E0432` for the absent public
  `validate_bulk_insert_columns` function and `E0599` for the absent
  `BulkLoadRequest::column_declarations()` method.
- The exact unchanged extension was rebuilt from this worktree; its focused
  offline contract fails `5/5` only for absent native-bulk surfaces/source,
  and a real Docker SQL-auth table fixture reaches the same missing
  `Connection.native_bulk_insert` `AttributeError` after successful DDL.
- Ruff lint/format, vendored Cargo fmt and Python syntax gates pass. The
  generated vendored `Cargo.lock` was moved recoverably to
  `/private/tmp/fastmssql-native-red-lock-20260727/Cargo.lock`.
- This RED-only change does not implement native bulk, change package
  metadata, alter the displayed `0.7.7` version or publish an artifact.

### Documentation-only FastMssql native TDS bulk insert design and plan

- Added the focused public/runtime design for list-bounded
  `Connection.native_bulk_insert()` and `Transaction.native_bulk_insert()`
  over the verified vendored-Tiberius ordered-column primitive.
- Required exact target-metadata-guided cell conversion, one finalized TDS
  bulk request per chunk, connection-form atomicity across all chunks and an
  explicit rollback-only transaction state after reusable post-wire failure.
- Defined seven deterministic SQL-auth contracts (`BULK-006` through
  `BULK-012`), observed-RED ancestry, compatibility regression gates and
  concrete-list load profiles through the explicitly approved 99,999 rows.
- Kept iterable/async-iterable backpressure, `execute_many()` and
  `query_many()` as separate later slices and preserved compatibility
  `bulk_insert()` unchanged.
- This documentation-only change does not modify runtime behavior, package
  metadata, the displayed `0.7.7` version or release state.

### Tiberius bulk column-subset audit status

- Updated the live production-readiness audit to the exact cumulative feature
  SHA `52c04c35a27dd6a79ccb5f54b15d7a0413a8965b`.
- Recorded the design, plan, RED and feature ancestry, the exact `E0432`/
  `E0599` RED evidence and every fresh local/Docker verification count.
- Classified the earlier localhost `Operation not permitted` result as a
  sandbox-policy failure only after the explicitly approved retry passed all
  `16/16` Tiberius integration tests.
- Recorded that code-review-graph rebuilt successfully but `detect_changes`
  exposed no node or flow for `vendor/tiberius`; its zero-risk result is
  therefore not treated as evidence for this slice.
- The batch/bulk program now has three of seven slices verified on the fork.
  FastMssql native bulk exposure, iterable backpressure, `execute_many()` and
  `query_many()` remain four separate open slices.
- This documentation update does not change package metadata, the displayed
  `0.7.7` version, release state or artifact-publication state.

### Tiberius bulk column-subset implementation

- Added the additive vendored-Tiberius
  `Client::bulk_insert_columns(table, columns)` primitive without changing
  `Client::bulk_insert(table)`.
- Added a closed raw-identifier grammar with independent bracket quoting,
  exact ordered metadata validation, explicit writable/restricted-flag
  checks and privacy-safe error context.
- Replaced the new path's use of `MetaDataColumn::fmt` with a total checked
  declaration formatter covering fixed, integer, float, money, temporal,
  binary, ANSI/Unicode, exact numeric and XML metadata. Invalid widths,
  precision/scale, legacy LOB, UDT and SQL_VARIANT metadata return typed
  errors without a panic path.
- The committed RED ancestor is `549ea180e5d80e9881a782b8cf12c60b06663d96`;
  it failed exactly for the absent private module and public method.
- Verification passed on the exact feature worktree: vendored Tiberius
  `168/168`; new direct SQL-auth `5/5`; response API `2/2`; response
  SQL-auth `7/7`; token safety SQL-auth `2/2`; root Rust `73/73`; focused
  Python batch/bulk `29/29`; strict batch/parameters `87/87`; and matrix
  contract `26/26` with 377 unique case IDs.
- Vendored/root Cargo fmt and Clippy with warnings denied passed. The native
  editable build resolved both the Python wrapper and ABI3 extension from
  this feature worktree.
- This local dependency feature does not yet expose FastMssql
  `native_bulk_insert()`, complete iterable backpressure, `execute_many()` or
  `query_many()`. It does not change package metadata, the displayed `0.7.7`
  version or release state.

### Tiberius bulk column-subset RED coverage

- Added unit contracts for closed table/column identifier grammar, exact
  metadata count/order, writable flags and a total checked declaration
  formatter across every supported and rejected TDS metadata family.
- Added direct SQL-auth cases `TIB-BULK-001` through `TIB-BULK-005` for
  reordered subsets, defaults/NULLs, hostile identifier characters,
  identity/computed/rowversion rejection, SQL_VARIANT panic safety and
  pre-wire malformed input.
- Registered the new direct-Tiberius SQL-auth target in the complete runner.
- The expected RED is a compile-time missing-module/missing-method failure on
  the unchanged vendored implementation; dependency, syntax or fixture
  failures are not accepted as evidence.
- This RED branch changes tests and status documentation only. It does not
  implement the primitive, change package metadata, alter the displayed
  `0.7.7` version or publish an artifact.

### Documentation-only Tiberius bulk column-subset plan

- Added the task-by-task RED/feature/status plan for the approved vendored
  Tiberius column-subset primitive.
- Locked the exact Rust method/helper interfaces, exhaustive checked metadata
  declarations, pre-wire identifier validation and exact ordered metadata
  behavior.
- Defined five direct Docker SQL-auth contracts for ordered subsets,
  defaults/NULLs, hostile identifier characters, restricted columns,
  unsupported metadata and connection recovery.
- Required vendored/root Cargo, FastMssql regression, privacy, graph and
  fork-only remote gates with observed RED ancestry.
- This plan-only change does not modify runtime behavior, package metadata,
  the displayed `0.7.7` version or release state.

### Documentation-only Tiberius bulk column-subset design

- Added the focused design for a safe additive
  `Client::bulk_insert_columns(table, columns)` primitive in the vendored
  Tiberius source while preserving `Client::bulk_insert(table)`.
- Closed the raw-identifier grammar, exact metadata count/order checks,
  writability flags, checked SQL type declarations, typed error behavior and
  mandatory `BulkLoadRequest::finalize()` boundary.
- Required a total formatter with no new-path `todo!()`, `unreachable!()` or
  panic, plus unit and Docker SQL-auth coverage for subsets, defaults, NULLs,
  hostile identifier characters and restricted/unsupported metadata.
- Defined separate documentation, RED, feature and live-status branches,
  all published only to the FastMssql fork.
- This documentation-only design does not modify runtime behavior, package
  metadata, the displayed `0.7.7` version or release state.

### Live audit status for typed bulk row conversion

- Advanced the live production-readiness audit from bounded compatibility
  bulk at `dec2914874d35d04655305d41d7a21fde23c40da` to the exact typed-row
  conversion fix `deef315cc6be7b2c303040cca99a268aa201a28d`.
- Recorded the committed RED reproduction at `b283c5c`, the shared
  single-value converter, global privacy-safe error positions, exact typed
  NULL/value round trips, late-chunk rollback and replacement physical
  `connection_id` evidence.
- Recorded exact local/Docker verification while keeping the latest complete
  hosted Linux/macOS/Windows, RustSec and 372-case matrix evidence tied to its
  actual ancestor `a9d5c2a`; no 377-case full run is inferred.
- Marked the first two of seven batch/bulk slices verified and kept the five
  remaining slices explicit: Tiberius column subsets, native TDS bulk,
  iterable backpressure, `execute_many()` and bounded-concurrency
  `query_many()`.
- This status-only documentation change does not modify runtime behavior,
  package metadata, the displayed `0.7.7` version or release state.

### Typed bulk row conversion implementation

- Shared the existing closed raw/typed single-value converter between
  ordinary query descriptors and compatibility bulk cells while preserving
  ordinary iterable expansion and query parameter-limit accounting.
- Added privacy-safe, zero-based global bulk row, column and flattened
  parameter metadata to conversion failures. First-chunk preflight remains
  pre-wire; later-chunk failure reports prior wire activity and conservative
  physical-connection retirement without exposing SQL identifiers or values.
- Rejected expanded and non-input bulk descriptors through stable
  `ConversionError` reasons, and documented the new bulk position fields in
  the public type stub.
- Preserved explicit SQL type metadata during bulk NULL inference, including
  an explicitly typed `TINYINT` NULL beside an inferred `BIGINT` sibling.
- Corrected the live retirement assertion after SQL Server immediately reused
  a numeric SPID: `sys.dm_exec_connections.connection_id` proved that the
  original physical connection was closed and replaced.
- Verification passed: Rust `73/73`; focused offline descriptor `5/5`;
  bounded-buffering regression `3/3`; SQL-auth batch/bulk `25/25`; strict
  parameters `62/62`; matrix contract `26/26` with 377 unique IDs; legacy
  batch validation `21/21`; and the 1,000-row resource probe with 8,634,368
  bytes RSS growth, 0.000384-second maximum event-loop stall and zero
  violations. Cargo fmt/Clippy and Ruff lint/format checks also passed on
  their scoped files.
- This implementation does not change package metadata, the displayed
  `0.7.7` version or release state, and it does not claim completion of the
  remaining compatibility/native bulk work.

### Typed bulk row conversion RED coverage

- Added five deterministic offline contracts requiring compatibility bulk
  cells to use the existing typed `Parameter` conversion family, reject
  expansion/non-input directions and expose privacy-safe zero-based global
  row, column and flattened parameter positions.
- Added SQL-auth cases `BULK-003` through `BULK-005` for exact
  numeric/temporal/UUID values, typed NULLs, local descriptor rejection and a
  late second-chunk conversion failure with atomic rollback and replacement
  session recovery.
- Reproduced the intended defect on an exact unchanged native build: all five
  offline contracts and all three new real-MSSQL cases failed with
  `ValueError: Unsupported type: Parameter`.
- The complete batch/bulk strict file proved the boundary precisely: all 22
  predecessor cases passed and only the three new descriptor cases failed.
- Raised the canonical SQL-auth specification from 374 to 377 unique IDs; all
  26 matrix-contract tests pass without regenerating or overstating the last
  complete 372-case evidence report.
- This RED branch changes tests, canonical specification and version history
  only; runtime behavior, package metadata, the displayed `0.7.7` version and
  release state remain unchanged.

### Documentation-only typed bulk row conversion plan

- Added the executable RED/fix/status plan for sharing the closed
  non-expanded `Parameter` conversion path with compatibility bulk rows.
- Locked zero-based global row, column and flattened parameter indexes,
  privacy-safe error fields, truthful first/late
  `wire_sent`/`connection_discarded` semantics, replacement physical
  connection recovery and preservation of the original typed conversion
  exception.
- Defined SQL-auth cases `BULK-003` through `BULK-005` for exact
  numeric/temporal/UUID values, typed NULLs, expanded/non-input rejection,
  late-chunk rollback and identifier/value redaction.
- Required explicit typed NULLs to remain outside untyped column-null
  inference and ordinary query/batch expansion behavior to remain unchanged.
- Defined isolated documentation, RED, fix and live-status branches plus
  focused Python/Rust/Docker/graph gates without claiming completion of the
  wider batch/bulk program.
- This plan-only change does not modify runtime behavior, package metadata,
  the displayed `0.7.7` version or release state.

### Live audit status for bounded compatibility bulk

- Advanced the live production-readiness audit from cumulative ancestor
  `a9d5c2ab42de0f03051c771bb8942ee15dfe6e28` to the exact technical tree
  `dec2914874d35d04655305d41d7a21fde23c40da`.
- Recorded the RED reproduction, one-chunk compatibility-bulk fix, exact
  focused Rust/Python/SQL-auth verification and 1,000/10,000/99,999-row
  resource profiles without claiming completion of the wider batch/bulk
  program.
- Separated the new local/Docker evidence from the last complete hosted
  Linux/macOS/Windows and RustSec gates, which remain tied to ancestor
  `a9d5c2a`, and kept the last full generated matrix at its evidenced
  `372/372` state while identifying the two new focused cases in the
  374-ID canonical specification.
- Kept six remaining batch/bulk slices explicit: shared row conversion,
  Tiberius column subsets, native TDS bulk, iterable backpressure,
  `execute_many()` and bounded-concurrency `query_many()`.
- This status-only documentation change does not modify runtime behavior,
  package metadata, the displayed `0.7.7` version or release state.

### Compatibility bulk bounded-buffering fix

- Replaced eager whole-input conversion in `Connection.bulk_insert()` with an
  owned `Py<PyList>` handle and one-chunk-at-a-time conversion inside the
  returned awaitable.
- The first chunk is converted before pool initialization; later chunks are
  converted only after the previous SQL response is fully drained and its
  converted values are explicitly dropped.
- Empty input now returns `0` before lifecycle admission, pool initialization
  or operation-metric accounting. Identifier validation remains local and
  synchronous.
- Captured the top-level input length, revalidate it at each conversion
  boundary, and reject any detected resize. A resize detected during first
  preflight remains zero-I/O; one detected after `BEGIN` follows the same full
  rollback path as a late value-conversion failure.
- Preserved one atomic transaction across all compatibility chunks, added
  checked `u64` affected-row aggregation, and retained timeout, cancellation,
  rollback and connection-retirement behavior.
- The exact final native build passes all 3 focused offline contracts, all 21
  legacy batch parameter validations, all 22 strict SQL-auth batch/bulk cases,
  3 focused deadline/operation-metric cases, all 71 FastMssql Rust tests, Rust
  formatting and Clippy with warnings denied.
- Real SQL-auth probes passed with exact affected/readback counts and a
  post-load `SELECT 1`: at 1,000 rows RSS grew `8,732,672` bytes with
  `0.000340333` seconds maximum event-loop stall; at 10,000 rows RSS grew
  `28,803,072` bytes with `0.002861375` seconds stall; at 99,999 rows RSS grew
  `45,203,456` bytes with `0.005358250` seconds stall. Every profile stayed
  below the `67,108,864`-byte and `0.100`-second gates with no violations.
- The maximum-profile RSS growth fell by `93,044,736` bytes from the unchanged
  RED implementation (`138,248,192` to `45,203,456`) while preserving
  99,999/99,999 affected and persisted rows.
- The ABI3 CPython 3.13 editable wheel build/install succeeded from the exact
  fix worktree. Package metadata, the displayed `0.7.7` version and release
  state remain unchanged.

### Compatibility bulk bounded-buffering RED coverage

- Added deterministic offline contracts requiring `bulk_insert()` method
  creation to leave every Python cell untouched until the returned awaitable
  runs and requiring empty input to return zero without pool or metric
  activity. Self-review added a third contract requiring a top-level input-list
  resize before await to fail locally rather than consume added rows.
- Reproduced all three defects on the unchanged implementation: the conversion
  probe was invoked once during method creation, empty input reached the
  acquire phase, and a resized non-empty input also reached acquire instead of
  raising the required local `ValueError`; both acquire paths timed out against
  a deliberately closed endpoint.
- Added SQL-auth cases `BULK-001` and `BULK-002`; on the exact RED worktree,
  the late invalid value was rejected synchronously before any first-chunk
  server activity, while the empty input again timed out in acquire.
- Added an opt-in RSS/event-loop probe whose baseline is captured only after
  the complete Python input list exists. The unchanged implementation added
  `40,681,472` bytes RSS at 10,000 rows and `138,248,192` bytes at 99,999
  rows (1 KiB shared payload), exceeding the `67,108,864`-byte gate at the
  maximum profile while still inserting and reading back all 99,999 rows.
- Raised the canonical SQL-auth matrix contract from 372 to 374 unique cases;
  all 26 matrix-contract checks pass, and all 20 pre-existing batch/bulk
  SQL-auth cases remain green on the exact RED build.
- Tightened the offline probe to one-second connect/acquire budgets so the RED
  reason is deterministic rather than a global pytest timeout.
- The RED branch adds tests and execution-plan evidence only; those commits do
  not change runtime behavior, package metadata, the displayed `0.7.7` version
  or release state.

### Documentation-only enterprise batch/bulk design

- Added the approved enterprise design for bounded compatibility bulk,
  explicit native TDS bulk copy, iterable backpressure, `execute_many()` and
  bounded-concurrency `query_many()`.
- The design preserves the existing `INSERT ... VALUES` semantics under
  `bulk_insert()` and gives native bulk a separate API so trigger, identity,
  default and computed-column behavior cannot change silently.
- The executable plan decomposes the work into seven focused
  RED/implementation branch pairs, followed by cumulative Docker, stress,
  wheel, hosted-OS, RustSec and live-audit gates.
- Added the first fully executable slice plan for bounded compatibility-bulk
  conversion, including deterministic pre-await RED evidence, real SQL Server
  late-conversion rollback, empty zero-I/O behavior, RSS/event-loop probes and
  exact RED-to-fix ancestry.
- Plan self-review fixed explicit-transaction settlement and rollback-only
  behavior, and made operation-metric ownership exact: compatibility/native
  bulk share the `bulk_insert` family while `execute_many` introduces the
  versioned schema-2 key.
- Baseline evidence on the design base is FastMssql Rust `71/71`, vendored
  Tiberius `162/162` and real SQL-auth batch/bulk `20/20`.
- This documentation change does not modify runtime behavior, package
  metadata, the displayed `0.7.7` version or release state.

### Documentation-only design work

- Added the approved enterprise resultsets, bounded async streaming and RPC
  output design on branch `docs/resultsets-streaming-design`, based on
  cumulative SHA `88ac9c00d3d80edbb84377fc1f812070b5cf289b`.
- The design records the current first-result/buffered baseline and decomposes
  future protocol, streaming, lifecycle and RPC work into separate RED and
  feature branches.
- Plan-time self-review strengthened the design with bounded consumer
  acknowledgements before lease release, fail-closed active-result drops,
  exact metadata units, privacy-safe token/raw-TDS diagnostics and an
  isolation baseline before direct RPC on recycled sessions, plus
  out-of-band terminal release when the row channel is full.
- The same review fixed result-boundary look-ahead, transaction-guard
  ownership, successful-retirement and nonfatal-error ACK semantics,
  conservative SQL Server procedure/parameter identifier limits, and a
  fixed-worker load model through 99,999 operations.
- Read-only Docker probes reproduced missing TABNAME/COLINFO handling for
  `FOR BROWSE` and a Rust panic on SQL_VARIANT metadata; the plan now inserts
  a dedicated RED/fix token-safety pair with raw payload and panic-hook gates,
  without claiming SQL_VARIANT support.
- Added the executable TDD plan for the protocol-event, bounded-streaming,
  lifecycle, RPC output, vendored fmt/clippy, load, wheel, hosted-CI and
  live-audit branch chain.
- This documentation change does not modify runtime behavior, package
  metadata, the displayed `0.7.7` version or release state.

### Tiberius token-safety RED coverage

- Added real SQL-auth reproductions for TABNAME/COLINFO emitted by
  `FOR BROWSE` and the SQL_VARIANT metadata panic.
- Added correctly typed raw `u8` token/type-info and source contracts that
  require exact payload consumption and typed unsupported errors without Rust
  panic macros.
- Added required local-runner and hosted-CI contracts without changing
  production decoder behavior on this RED branch.
- This test-only change does not modify package metadata, the displayed
  `0.7.7` version or release state.

### Panic-free Tiberius browse and unsupported metadata decoding

- TABNAME and COLINFO browse payloads are consumed structurally and preserve
  following rows/tokens without retaining base-table names.
- UDT and SQL_VARIANT metadata now return typed protocol errors instead of
  reaching a Rust panic path; value conversion remains explicitly unsupported.
- The Rust-to-Python boundary preserves the existing stable metadata-decoding
  `ProtocolError` contract, including query, batch and transaction paths.
- Added vendored format, baseline-aware clippy, unit, real SQL-auth and hosted
  desktop gates. Clippy denies every warning category except ten explicitly
  listed Rust 1.94 diagnostics already present in unchanged Tiberius 0.12.3
  code.
- This fix does not modify package metadata, the displayed `0.7.7` version or
  release state.

### RED response-event and trace-privacy contracts

- Added database-independent API/encoder contracts and real SQL-auth
  reproductions for complete TDS response events, named RPC output metadata,
  signed return status, DONE_COUNT semantics and legacy QueryStream parity.
- Added fail-closed source contracts for value-redacted response `Debug`,
  token tracing and connection-buffer diagnostics.
- This RED branch records required behavior only; it does not change runtime
  response decoding, package metadata, the displayed `0.7.7` version or
  release state.

### Complete TDS response events and direct named RPC

- Added an owned vendored-Tiberius response stream for metadata, rows,
  DONE-family state, INFO messages, signed return status and output values,
  while retaining legacy `QueryStream` behavior as a filtered adapter.
- Added checked named-RPC encoding, typed input/output parameter metadata and
  a fully drained reset-bearing READ COMMITTED baseline before direct RPC on
  recycled sessions.
- Preserved complete nullable/precision/scale/length metadata and corrected
  nullable SMALLMONEY/SMALLDATETIME storage-width mapping found by the real
  SQL-auth suite.
- Removed value-bearing token traces and raw connection-buffer diagnostics,
  and added database-independent Linux/macOS/Windows response API coverage.
- This vendored-driver feature does not yet expose a new FastMssql Python API
  or change package metadata, the displayed `0.7.7` version or release state.

### RED bounded async result-stream contracts

- Added runtime, installed-package and stub contracts for immutable result
  types, pooled `stream()`/`batch()` entry points, nested async-only iteration,
  terminal summaries and honest buffered `QueryStream` compatibility.
- Added SQL-auth reproductions for ordered and empty result sets, exact
  metadata units, explicit set skipping, reset versus security retirement,
  DONE/INFO separation, local concurrency errors and measured slow-consumer
  RSS.
- Added the required SHA-bound 1,000-operation profile at concurrency 64,
  pool size 8 and event buffer 8, plus an opt-in fixed-worker path through
  99,999 operations with separate admitted and scheduled latency evidence.
- Added atomic pass/fail artifacts, stale-evidence rejection, report
  rendering and the future installed-wheel gate contract.
- This RED branch intentionally does not implement the new Python result API
  and does not change package metadata, the displayed `0.7.7` version or
  release state.

### Bounded async result-stream implementation

- Added pooled `Connection.stream()` and `Connection.batch()` entry points
  backed by owned SQL Server leases and async-only nested result iterators.
- Added bounded producer/consumer event credit, explicit conversion
  acknowledgements, deterministic close/finish behavior and terminal release
  only after the response and lease are released or retired.
- Added immutable column, DONE, informational-message and terminal-summary
  value types while preserving exact empty-result metadata.
- Corrected the legacy `QueryStream`, `query()` and `simple_query()` contract:
  the first result set is buffered before synchronous compatibility
  iteration; bounded wire-level streaming uses the new API.
- Added Tokio macro support required by cancellation-aware producer selects
  and extended the installed-wheel Linux/macOS/Windows contract gate.
- Preserved conversion failures over simultaneous cancellation, kept
  post-final-ACK cancellation fail-closed, and made context exit resume a
  summary-ACKed terminal wait instead of spuriously aborting it. Explicit
  close after a consumer failure now also waits for confirmed lease release
  and re-raises the first stored failure.
- Made the SQL-auth retirement proof robust to immediate SQL Server SPID
  number reuse by asserting a new `connection_id` and the bb8 broken-close
  counter; concurrent-consumer coverage now schedules the PyO3 awaitable with
  `asyncio.ensure_future()`.
- Updated the disabled-metrics source contract to follow the movable
  `OperationObserver`: its `Option::and_then` gate still prevents clock and
  atomic work when metrics are disabled.
- This feature does not change package metadata, the displayed `0.7.7`
  version or release state.

### RED fail-closed result-stream lifecycle contracts

- Added real SQL-auth and DMV coverage for explicit close, dropped response
  objects, cancellation-safe outer/inner receives, queued server errors and
  post-wire conversion failure.
- Added graceful/forced shutdown, pooled/direct transaction ownership and
  consumer-acknowledgement-before-release contracts, including a full event
  queue at operation timeout.
- Registered the six lifecycle case groups in the strict SQL-auth design and
  local runner.
- This RED branch records required lifecycle behavior only; it does not
  change production runtime behavior, package metadata, the displayed
  `0.7.7` version or release state.

### Fail-closed result-stream lifecycle and transaction streaming

- Added bounded `stream()` and `batch()` support to pooled and direct
  transactions while retaining the owned transaction-session mutex for the
  complete SQL Server response.
- Made cancelled and abandoned outer/inner receives preserve unowned events,
  while cancellation after event ownership, full-response close/drop,
  deadlines and forced shutdown retire uncertain physical sessions.
- Added terminal stream metadata for SQL, protocol and post-wire conversion
  failures, preserving safe reuse after fully drained nonfatal SQL errors and
  fail-closed retirement for uncertain conversion/protocol paths.
- Preserved transaction state after normal EOF or single-result-set close;
  classified successful retirement and every uncertain terminal path instead
  retire the transport, mark the transaction failed and reject later commit.
- Added public wrapper/stub contracts and documentation for transaction
  streaming and fail-closed resource-release semantics.
- This feature does not change package metadata, the displayed `0.7.7`
  version or release state.

### RED direct RPC output and return-status contracts

- Added real SQL-auth procedure fixtures for direct RPC return status,
  INPUT/OUTPUT/INPUT_OUTPUT values, exact scalar output types, multiple and
  empty result sets, and reordered MAX output tokens.
- Added fail-closed SQL error, receive-cancellation and early-close coverage,
  conservative procedure/parameter identifier grammars, direction misuse,
  pooled/direct transaction parity, bounded concurrent calls and recycled
  READ COMMITTED isolation.
- Added runtime/stub contracts for exact `callproc()` signatures, output
  mapping key types, fresh-dictionary snapshots and complete direction
  documentation.
- Registered RPC-001..011 in the required local runner and raised the exact
  central SQL-auth matrix total from 361 to 372.
- This RED branch records required behavior only; it does not implement
  `callproc()`, change package metadata, alter the displayed `0.7.7` version,
  or publish a release.

### Direct RPC output values and return status

- Added `Connection.callproc()` and `Transaction.callproc()` as native named
  TDS RPC entry points sharing the bounded `ResultStream` response and
  fail-closed lease lifecycle.
- Added closed local procedure/parameter identifier validation, positional or
  named parameter modes, exact INPUT/OUTPUT/INPUT_OUTPUT/RETURN_VALUE rules,
  the 2,100 encoded-argument limit, and pre-checkout rejection of invalid
  descriptors.
- Added ordinal-and-name output-token matching independent of wire arrival
  order, exact scalar conversion shared with result rows, signed return-status
  capture, and fresh string/integer output dictionaries after terminal ACK.
- Added wrapper, stub and README contracts plus real SQL-auth coverage for
  exact output types, multiple/empty result sets, MAX-value reordering,
  transaction parity, connection disposition, recycled isolation and bounded
  concurrent calls.
- Expanded RPC-003 to exercise every supported scalar declaration through
  both typed-NULL OUTPUT and value-bearing INPUT_OUTPUT encoding, including
  fixed/variable ANSI, Unicode and binary types plus exact NULL handling.
- Removed the last invariant `expect()` from output collection so a missing
  token remains a typed, privacy-safe protocol error on every path.
- Corrected the Task 11 vendored-Clippy command to reuse the repository's
  audited ten-category Tiberius 0.12.3/Rust 1.94 legacy baseline while
  continuing to deny every non-baseline warning.
- This feature does not change package metadata, the displayed `0.7.7`
  version or release state.

### RED configurable result-stream stress runner contract

- Added a deterministic runner contract proving that custom pool and event
  buffer sizes are forwarded to the bounded result-stream stress program.
- Required explicit environment-variable defaults of pool size 8 and buffer
  size 8 while rejecting the previous hard-coded command arguments that made
  extended-profile configuration ineffective.
- This test-only change does not modify runtime behavior, package metadata,
  the displayed `0.7.7` version or release state.

### Configurable result-stream stress runner

- The shell runner now forwards
  `FASTMSSQL_RESULT_STREAM_STRESS_POOL_SIZE` and
  `FASTMSSQL_RESULT_STREAM_STRESS_BUFFER_SIZE` to the Python stress program.
- Defaults remain pool size 8 and event buffer size 8, while extended
  verification can exercise the planned pool size 32 and buffer size 16
  instead of silently using the defaults.
- This test-harness fix does not modify package runtime behavior, package
  metadata, the displayed `0.7.7` version or release state.

### RED isolated-wheel SQL-auth dependency contract

- Added a deterministic contract requiring the RESULT-030 isolated-wheel gate
  to install every dependency needed for SQL-auth test collection and timeout
  enforcement.
- The contract reproduces the missing `python-dotenv` collection dependency
  and also requires `pytest-timeout` so the documented 30-second integration
  timeout is active in the isolated environment.
- This test-only change does not modify runtime behavior, package metadata,
  the displayed `0.7.7` version or release state.

### Executable isolated-wheel SQL-auth gate

- Corrected the RESULT-030 installation command to include the locked
  `python-dotenv` dependency required by the root SQL-auth conftest.
- Added the locked `pytest-timeout` plugin so the isolated test run enforces,
  rather than merely documents, the repository's 30-second integration
  timeout.
- This verification-plan fix does not modify runtime behavior, package
  metadata, the displayed `0.7.7` version or release state.

### RED hosted installed-wheel async dependency contract

- Added a deterministic workflow contract requiring the hosted installed-wheel
  environment to install the locked pytest plugin used by its asynchronous
  result-stream cancellation test.
- The contract reproduces GitHub Actions run `30281898838`, where wheel builds
  and Rust suites passed but pytest rejected the async contract on Ubuntu and
  macOS because `pytest-asyncio` was absent.
- This test-only change does not modify runtime behavior, package metadata,
  the displayed `0.7.7` version or release state.

### Hosted installed-wheel async test environment

- Added locked `pytest-asyncio==1.4.0` installation to the hosted
  Linux/macOS/Windows wheel-contract environment.
- The asynchronous result-stream cancellation contract now executes under its
  intended pytest plugin instead of failing during test dispatch after a
  successful wheel build.
- This CI-environment fix does not modify runtime behavior, package metadata,
  the displayed `0.7.7` version or release state.

### RED Windows Tiberius authentication-test feature gates

- Added a deterministic source contract requiring vendored ADO.NET and JDBC
  Windows-auth parser tests to share the `winauth` feature gate of the
  `AuthMethod` APIs they reference.
- The contract reproduces GitHub Actions run `30283257883`, where the
  SQL-auth/rustls feature set compiled on Linux and macOS but Windows emitted
  six `E0599` errors from tests enabled by OS alone.
- This test-only change does not modify runtime behavior, package metadata,
  the displayed `0.7.7` version or release state.

### Portable Tiberius Windows authentication-test gates

- ADO.NET and JDBC tests that reference `AuthMethod::Integrated` or
  `AuthMethod::windows` now compile only when both Windows and the vendored
  Tiberius `winauth` feature are active, matching the production API gates.
- The SQL-auth/rustls-only feature profile therefore keeps the same coverage
  on every hosted OS without accidentally compiling unavailable
  Windows-authentication test APIs.
- This test-gating fix does not modify runtime behavior, package metadata,
  the displayed `0.7.7` version or release state.

### Exact bounded-result and RPC verification status

- Regenerated the strict SQL-auth matrix and report from the exact technical
  SHA `a9d5c2ab42de0f03051c771bb8942ee15dfe6e28`: 372/372 requirement IDs,
  386 strict tests, 16 async, 33 framework, 6 resilience, 12 load and 1,090
  original-local-regression tests pass with no failures, errors or skips.
- Recorded the required 1,000-operation result-stream profile and extended
  10,000/99,999 profiles, all with exact IDs, bounded pool/event buffers,
  bounded RSS, continued event-loop progress, zero timeout/failure and
  post-load recovery.
- Recorded the isolated ABI3 wheel hash and site-packages import, 37/37 static
  installed contracts and 34/34 real-MSSQL result/lifecycle/RPC tests.
- Recorded hosted run `30284587006` as green on Ubuntu, macOS and Windows at
  the exact technical SHA and RustSec run `30284587019` as green at the same
  SHA.
- Updated the live production-readiness audit and the future original
  repository PR roadmap with four independently reviewable candidate slices.
  Publication remains forbidden without fresh original ancestry,
  reproduction and a new explicit approval.
- This status-only documentation change does not modify runtime behavior,
  package metadata, the displayed `0.7.7` version or release state.
