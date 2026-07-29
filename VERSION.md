# Version status

Current displayed package version: `0.7.7`.

## 0.8.0 — unreleased candidate

The candidate version follows the repository convention that the middle
component represents a feature-level patch. It is documented here but is not
yet applied to package metadata; release versioning remains a separate,
explicit decision.

Changes currently integrated in fork history through
`428bc7471f61376294a8cb0f43587e86dd76ed9d`:

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
- deterministic SQL-auth, exact-wire, compatibility, and 1,000-operation
  concurrent load coverage;
- load-metric contract coverage for the typed-parameter case `PARAM-033`;
- regenerated SQL-auth matrix/report evidence tied to exact cumulative merge
  `a9d5c2ab42de0f03051c771bb8942ee15dfe6e28`: 372/372 required
  matrix cases, 386 strict, 16 async, 33 framework, 6 resilience, 12 load,
  and 1,090
  original-local-regression tests pass;
- hosted raw Cargo, Rust tests, wheel build/install contracts on Linux,
  macOS, and Windows passed in run `30284587006`; RustSec passed in run
  `30284587019`.

No release, package-version change, or artifact publication has occurred.

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
