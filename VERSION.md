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
