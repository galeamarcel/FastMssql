# Version status

Current displayed package version: `0.7.7`.

## 0.8.0 — unreleased candidate

The candidate version follows the repository convention that the middle
component represents a feature-level patch. It is documented here but is not
yet applied to package metadata; release versioning remains a separate,
explicit decision.

Changes currently integrated on `test/sql-auth-validation`:

- closed, validated SQL parameter descriptors with exact TDS metadata;
- exact decimal, UUID, temporal, ANSI/Unicode, binary, XML, and typed-null
  input handling;
- TDS 7.4 UTF-8 feature negotiation and `_UTF8` collation support;
- structured, privacy-safe conversion failures shared by connection,
  transaction, and batch paths;
- local rejection when temporal rounding would cross into year 10000;
- deterministic SQL-auth, exact-wire, compatibility, and 1,000-operation
  concurrent load coverage;
- load-metric contract coverage for the typed-parameter case `PARAM-033`;
- regenerated SQL-auth matrix/report evidence tied to exact cumulative merge
  `450ea446b799cf2ce7e035c332c6ce3d5d1f0adc`: 346/346 required
  matrix cases, 16 async, 33 framework, 6 resilience, 12 load, and 1,072
  original-local-regression tests pass;
- hosted raw Cargo, Rust tests, wheel build/install contracts on Linux,
  macOS, and Windows passed in run `30240874471`; RustSec passed in run
  `30240874468`.

No release, package-version change, or artifact publication has occurred.

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
