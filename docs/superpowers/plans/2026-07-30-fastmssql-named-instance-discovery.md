# FastMssql Named-Instance Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:executing-plans` to implement this plan task-by-task. Apply
> `superpowers:systematic-debugging` to every unexpected failure,
> `superpowers:test-driven-development` to both RED/fix pairs, and
> `superpowers:verification-before-completion` before every success claim.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `instance_name` without an explicit port perform bounded,
true-async SQL Server Resolution Protocol (SSRP) discovery and then connect to
the discovered TDS TCP port, while preserving direct-port behavior, one
connect deadline, structured errors, pool bounds and every cumulative
production-readiness gate.

**Architecture:** First harden the vendored Tiberius 0.12.3 Tokio SQL Browser
transport with pure request/response helpers and deterministic UDP/TCP
fixtures. Then let the single FastMssql physical-connection helper classify
the initial target as direct TCP or SQL Browser. Pooled connections, direct
`Transaction`, direct batch and Azure-authenticated paths already converge on
that helper. Azure routing remains a direct host/port reconnect. The
persistent pool amortizes discovery; the driver adds no discovery cache and
no SQL retry.

**Tech Stack:** Rust 2024 FastMssql core, vendored Rust 2021 Tiberius 0.12.3,
Tokio UDP/TCP, PyO3 0.29 ABI3, bb8, Python 3.11+, pytest/pytest-asyncio,
Docker SQL Server 2022 with SQL authentication, Windows Server 2022 with SQL
Server Express `SQLEXPRESS`, maturin, Cargo/RustSec and code-review-graph.

## Global Constraints

- Work only in `https://github.com/galeamarcel/FastMssql.git`.
- Keep `Rivendael/FastMssql` fetch-only and its push URL exactly `DISABLED`.
- Start protocol RED work from the exact committed plan on
  `docs/named-instance-design`.
- Preserve this public ancestry:
  `test/tiberius-named-instance-discovery` must be an ancestor of
  `fix/tiberius-named-instance-discovery`, and
  `test/named-instance-discovery` must be an ancestor of
  `fix/named-instance`.
- Never squash away an observed RED boundary.
- Update `VERSION.md` in every repository-modifying commit. Keep displayed
  package metadata at `0.7.7` unless a later explicit release decision
  authorizes a version change.
- Use `apply_patch` for text edits and project-local ignored worktrees under
  `.worktrees/`.
- Enable only the existing vendored Tiberius `sql-browser-tokio` feature; add
  no new third-party runtime dependency.
- Preserve ordinary direct host/port selection and error behavior.
- Treat an explicit port as authoritative even when an instance name exists;
  that path performs no SQL Browser request.
- Use SQL Browser only when an instance name exists and no port was supplied.
- Keep Azure routing direct. A routed host/port must never re-enter discovery.
- Keep one absolute FastMssql connect deadline across Azure credential
  acquisition, DNS, UDP discovery, TCP, TLS, TDS login and one routing
  reconnect.
- Keep the protocol-recommended one-second SSRP receive timeout as an inner
  bound. A shorter FastMssql connect deadline wins.
- Add no SQL operation retry, automatic port-1433 fallback, broadcast,
  discovery cache, named-pipe fallback or shared-memory fallback.
- Send exactly `0x04 || INSTANCE_BYTES || 0x00`; send no credential,
  connection string, database name, application name or SQL text.
- Reject an empty instance, embedded NUL and encoded instance name longer
  than 32 bytes before UDP I/O.
- Accept only a `0x05` response with an exact little-endian payload length no
  greater than 1,024 bytes and exactly one case-insensitive `tcp` token whose
  decimal port is in `1..=65535`.
- Use a connected UDP socket so a response from another source is not
  accepted.
- Preserve the last meaningful DNS, UDP or TCP failure. Do not replace a
  refused discovered TCP port with a false host-not-found error.
- Map discovery failures to `SqlConnectionError` with
  `stage="sql_browser_discovery"`, `retryable=True`,
  `connection_discarded=False` and `outcome_unknown=False`.
- Keep outer deadline expiry as the existing `OperationTimeoutError` with
  `phase="connect"`.
- Required SQL behavior uses the real Docker SQL Server and SQL
  authentication. The deterministic Linux SSRP responder is a protocol
  fixture, not a claimed Linux named instance.
- Required hosted evidence uses a genuine Windows SQL Server Express named
  instance plus the real SQL Server Browser service without an explicit TCP
  port.
- Required load is 1,000 logical operations; the approved extended gate is
  exactly 99,999 logical operations.
- A required test may not catch and suppress its failure or skip because the
  required service is unavailable.
- Do not publish a wheel, package, release, Tiberius fork, upstream branch or
  original-repository pull request.

---

## File Responsibility Map

### Vendored Tiberius runtime

- `vendor/tiberius/src/sql_browser.rs` — pure request builder, total bounded
  response parser and shared protocol constants.
- `vendor/tiberius/src/sql_browser/tokio.rs` — connected UDP exchange,
  one-second bound, same-address TCP connect and meaningful error retention.
- `vendor/tiberius/src/sql_browser/tests.rs` — pure and loopback network
  protocol tests.
- `vendor/tiberius/src/client/config.rs` — read-only
  `has_instance_name()`/`has_explicit_port()` introspection and parser tests.
- `vendor/tiberius/FASTMSSQL_PATCH.md` — exact local SQL Browser patch scope.

### FastMssql runtime and public contract

- `Cargo.toml` — enable the existing `sql-browser-tokio` feature.
- `Cargo.lock` — retain unchanged unless the locked Cargo invocation itself
  produces a justified dependency delta; feature selections are not recorded
  as lockfile metadata.
- `src/pool_manager.rs` — one target classifier, direct/browser stream
  opener, discovery error variant and Python metadata mapping.
- `python/fastmssql/fastmssql.pyi` — clarify `instance_name` and explicit-port
  semantics without changing the constructor signature.
- `README.md` — document individual arguments and ADO.NET examples.

### Deterministic and real-SQL tests

- `tests/test_named_instance_contract.py` — offline source, feature, stub,
  workflow, privacy and installed-wheel contracts.
- `tests/sql_auth_strict/sql_browser_fixture.py` — bounded deterministic SSRP
  responder with exact counters and no secret logging.
- `tests/sql_auth_strict/test_named_instance_strict.py` — canonical
  `NINST-001` through `NINST-022` owners and real SQL-auth behavior.
- `tests/sql_auth_strict/test_matrix_contract.py` — exact case total, runner,
  stress, workflow and no-swallowed-failure contracts.
- `tests/sql_auth_strict/conftest.py` — admit `NINST-018` to the load metric
  recorder.

### Stress, reporting and hosted verification

- `scripts/sql_auth/named_instance_stress.py` — fixed-worker logical-operation
  load through one persistent discovered pool.
- `scripts/sql_auth/run_named_instance_stress.sh` — required 1,000 profile
  and explicit 99,999 extended profile.
- `scripts/sql_auth/run_all.sh` — focused named-instance and stress lanes.
- `scripts/sql_auth/generate_report.py` — privacy-safe named-instance evidence.
- `.github/workflows/rust-unit-tests.yml` — Linux/macOS/Windows protocol and
  installed-wheel contracts with the feature enabled.
- `.github/workflows/named-instance-windows.yml` — repository-owned
  `windows-2022` SQL Express/SQL Browser end-to-end gate.
- `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
  — canonical `NINST-001` through `NINST-022` requirements.
- `docs/validation/` — exact-SHA local, Docker, wheel, load and hosted
  evidence.
- `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md` — live feature status.
- `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md` —
  future candidate intake only.
- `VERSION.md` — every commit and final candidate record.

---

### Task 1: Publish the Approved Design and Executable Plan

**Branch:** `docs/named-instance-design`

**Files:**

- Create:
  `docs/superpowers/specs/2026-07-30-fastmssql-named-instance-discovery-design.md`
- Create:
  `docs/superpowers/plans/2026-07-30-fastmssql-named-instance-discovery.md`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes exact batch/bulk closure
  `30f975033090d623b27795da76d59ddcef1277ab`.
- Produces the selected transport matrix, NINST case namespace, branch
  topology and executable gate sequence.

- [ ] **Step 1: verify the graph, worktree and fork boundary**

Run:

```bash
uvx code-review-graph build
git status --short --branch
git rev-parse HEAD
git rev-parse --git-dir
git rev-parse --git-common-dir
git remote get-url --push origin
git remote get-url --push upstream
```

Expected: graph and `HEAD` agree, the branch is
`docs/named-instance-design`, `origin` is Marcel Galea's fork and upstream
push is `DISABLED`.

- [ ] **Step 2: self-review the design and plan**

Run:

```bash
rg -n 'T''BD|T''ODO|imple''ment later|fi''ll in|appropri''ate error|sim''ilar to' \
  docs/superpowers/specs/2026-07-30-fastmssql-named-instance-discovery-design.md \
  docs/superpowers/plans/2026-07-30-fastmssql-named-instance-discovery.md
git diff --check
```

Also verify:

- every `NINST-001` through `NINST-022` appears once in the required-case
  table;
- all Markdown code fences are balanced;
- every branch named in the design is represented in this plan;
- no password, token, connection string or environment-file value appears.

Expected: no placeholder match, no whitespace error and no gate omission.

- [ ] **Step 3: commit and push only the documentation branch**

```bash
git add \
  docs/superpowers/specs/2026-07-30-fastmssql-named-instance-discovery-design.md \
  docs/superpowers/plans/2026-07-30-fastmssql-named-instance-discovery.md \
  VERSION.md
git commit -m "docs: plan named instance discovery"
git push -u origin docs/named-instance-design
```

Verify `git ls-remote origin refs/heads/docs/named-instance-design` equals
local `HEAD` and the worktree is clean.

---

### Task 2: Create the Vendored-Tiberius Pure-Protocol RED

**Branch:** `test/tiberius-named-instance-discovery`

**Files:**

- Create: `vendor/tiberius/src/sql_browser/tests.rs`
- Modify: `vendor/tiberius/src/sql_browser.rs`
- Modify: `vendor/tiberius/src/client/config.rs`
- Modify: `vendor/tiberius/FASTMSSQL_PATCH.md`
- Modify: `VERSION.md`

**Test-required private interfaces:**

```rust
fn build_instance_request(instance_name: &str) -> crate::Result<Vec<u8>>;
fn parse_instance_response(
    response: &[u8],
    instance_name: &str,
) -> crate::Result<u16>;
```

**Test-required public read-only interfaces:**

```rust
pub fn has_instance_name(&self) -> bool;
pub fn has_explicit_port(&self) -> bool;
```

- [ ] **Step 1: create the isolated RED worktree from the plan commit**

From the repository root:

```bash
git worktree add \
  .worktrees/test-tiberius-named-instance-discovery \
  -b test/tiberius-named-instance-discovery \
  docs/named-instance-design
```

Expected: the parent is the exact plan commit and the new worktree is clean.

- [ ] **Step 2: add only the test module declaration and pure tests**

Add to `sql_browser.rs`:

```rust
#[cfg(test)]
mod tests;
```

Do not add the missing helpers yet. In `tests.rs`, require:

- `FASTMSSQL` encodes as
  `[0x04, b'F', b'A', b'S', b'T', b'M', b'S', b'S', b'Q', b'L', 0x00]`;
- empty, embedded-NUL and 33-byte names fail before any I/O;
- exactly 32 encoded bytes succeeds and still has one trailing NUL;
- the request contains only opcode, instance bytes and terminator;
- valid `0x05 + LE length + payload` returns the declared TCP port;
- `tcp`, `TCP` and mixed-case tokens are equivalent;
- unrelated non-UTF-8 field values do not prevent ASCII TCP parsing;
- lengths `0`, `1` and `2`, wrong type, short payload, trailing bytes and
  declared sizes above 1,024 fail without panic;
- missing, empty, duplicate, signed, whitespace, non-decimal, zero and
  greater-than-65535 TCP ports fail;
- a payload at the exact 1,024-byte boundary remains bounded.

Use table-driven assertions against error variants or stable categories, not
the complete prose message.

- [ ] **Step 3: add configuration-introspection RED tests**

In the existing config test module, require this matrix:

| Input | instance | explicit port | `get_addr()` |
| --- | ---: | ---: | --- |
| defaults | false | false | `localhost:1433` |
| host + port | false | true | selected TCP port |
| `host\SQLEXPRESS` | true | false | browser endpoint `:1434` |
| `host\SQLEXPRESS,51433` | true | true | `:51433` |

Exercise both setters and `Config::from_ado_string()`.

- [ ] **Step 4: record and run the intended RED**

Run:

```bash
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls,sql-browser-tokio \
  --lib sql_browser
```

Expected RED: compilation fails only because the new helper/introspection
interfaces do not exist, or the unchanged parser fails the new assertions.
Environment or dependency failures are not acceptable RED evidence.

- [ ] **Step 5: commit and push the executable RED**

Update `FASTMSSQL_PATCH.md` as a pending test contract and `VERSION.md` with
the exact parent and command/result. Then:

```bash
git add \
  vendor/tiberius/src/sql_browser.rs \
  vendor/tiberius/src/sql_browser/tests.rs \
  vendor/tiberius/src/client/config.rs \
  vendor/tiberius/FASTMSSQL_PATCH.md \
  VERSION.md
git commit -m "test: require safe Tiberius named instance discovery"
git push -u origin test/tiberius-named-instance-discovery
```

Verify the remote SHA and retain the failing command output as ignored local
evidence.

---

### Task 3: Extend the Same Tiberius RED with Deterministic Network Behavior

**Branch:** `test/tiberius-named-instance-discovery`

**Files:**

- Modify: `vendor/tiberius/src/sql_browser/tests.rs`
- Modify: `VERSION.md`

**Interfaces:**

- Consumes `tokio::net::{UdpSocket, TcpListener}` on loopback.
- Exercises `<TcpStream as SqlBrowser>::connect_named(&Config)`.

- [ ] **Step 1: add one-shot UDP/TCP fixtures**

The fixture must:

- bind the expected browser to `127.0.0.1:0`;
- configure `Config::port()` with that UDP endpoint for direct Tiberius
  protocol testing;
- observe the complete request and return one exact response;
- optionally expose a correct TCP listener and record acceptance;
- close every task/socket under a bounded timeout;
- retain only structural counters and ports, never credentials.

- [ ] **Step 2: add deterministic async RED cases**

Require:

1. exact NUL-terminated request, valid response and acceptance on the returned
   TCP port;
2. an attacker UDP socket sends a valid response first, but only the response
   from the configured connected peer can select the TCP target;
3. a silent expected browser fails in approximately one second, never
   unbounded;
4. a valid discovered port with no TCP listener preserves
   `ConnectionRefused`/equivalent I/O detail;
5. multiple resolved address attempts retain the last meaningful failure;
6. no-instance `connect_named()` still opens the configured direct TCP port
   without sending UDP.

Use barriers rather than arbitrary sleeps except for the protocol-defined
one-second timeout assertion.

- [ ] **Step 3: rerun and record RED**

Run the focused command from Task 2. Expected: at least the missing NUL,
wrong-source acceptance, misleading terminal error or malformed-response
behavior fails against unchanged runtime. Commit:

```bash
git add vendor/tiberius/src/sql_browser/tests.rs VERSION.md
git commit -m "test: require bounded Tiberius SQL Browser transport"
git push origin test/tiberius-named-instance-discovery
```

The final RED SHA is the mandatory parent for Task 4.

---

### Task 4: Harden Vendored Tiberius and Make the Protocol RED Green

**Branch:** `fix/tiberius-named-instance-discovery`

**Files:**

- Modify: `vendor/tiberius/src/sql_browser.rs`
- Modify: `vendor/tiberius/src/sql_browser/tokio.rs`
- Modify: `vendor/tiberius/src/client/config.rs`
- Modify: `vendor/tiberius/FASTMSSQL_PATCH.md`
- Modify: `VERSION.md`

- [ ] **Step 1: branch directly from the final Tiberius RED**

```bash
git worktree add \
  .worktrees/fix-tiberius-named-instance-discovery \
  -b fix/tiberius-named-instance-discovery \
  test/tiberius-named-instance-discovery
git merge-base --is-ancestor \
  test/tiberius-named-instance-discovery \
  fix/tiberius-named-instance-discovery
```

Expected: the ancestry check exits zero.

- [ ] **Step 2: implement the pure request builder**

Use `instance_name.as_bytes()` as the existing wire encoding. Reject before
allocation/send:

```text
empty
contains 0x00
encoded length > 32
```

Allocate exactly `1 + len + 1`, push `0x04`, the bytes and one `0x00`.
Return a controlled Tiberius error; do not panic or log the raw name.

- [ ] **Step 3: replace the response parser with a total slice parser**

Validate in this order:

1. `response.len() >= 3`;
2. response byte `0 == 0x05`;
3. declared `u16::from_le_bytes([response[1], response[2]])`;
4. declared payload `<= 1_024`;
5. `response.len() == 3 + declared`;
6. split payload on `;` without decoding unrelated values;
7. find exactly one key equal to ASCII `tcp` ignoring case;
8. require one nonempty ASCII-decimal value and port `1..=65535`.

Never index before a length check. Do not use `unwrap`, `expect`, `todo!` or
`unreachable!` in the production parser.

- [ ] **Step 4: harden the Tokio exchange**

For each DNS candidate:

- bind the same-family wildcard address;
- `connect()` the UDP socket to that candidate;
- `send()` the exact request;
- receive into a fixed `3 + 1_024 + 1` byte buffer under one second, treating
  the final byte only as an oversize-rejection sentinel;
- reject any response longer than the accepted `3 + 1_024` bytes;
- parse the exact received slice;
- replace only that candidate's port with the parsed TCP port;
- attempt `TcpStream::connect(candidate)` and set `TCP_NODELAY`;
- retain the last meaningful failure for final return.

Direct no-instance behavior must remain a TCP attempt to the configured
address. Trace events must be structural and must not print the instance
name.

- [ ] **Step 5: add Config introspection without changing setters/parsers**

Implement:

```rust
pub fn has_instance_name(&self) -> bool {
    self.instance_name.is_some()
}

pub fn has_explicit_port(&self) -> bool {
    self.port.is_some()
}
```

Do not change `get_addr()` or Tiberius's direct `connect_named()` convention
that `Config::port()` selects its browser endpoint when an instance exists.

- [ ] **Step 6: verify vendored Tiberius**

Run:

```bash
cargo fmt --manifest-path vendor/tiberius/Cargo.toml --check
cargo test \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls,sql-browser-tokio \
  --lib
cargo clippy \
  --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls,sql-browser-tokio \
  --all-targets -- -D warnings \
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
```

Expected: all focused RED cases and the complete vendored library pass.

- [ ] **Step 7: self-review, commit and push**

Run graph impact analysis, `git diff --check`, privacy scans and inspect every
changed production line. Update the patch notice from pending to implemented.
Then:

```bash
git add \
  vendor/tiberius/src/sql_browser.rs \
  vendor/tiberius/src/sql_browser/tokio.rs \
  vendor/tiberius/src/client/config.rs \
  vendor/tiberius/FASTMSSQL_PATCH.md \
  VERSION.md
git commit -m "fix: harden Tiberius named instance discovery"
git push -u origin fix/tiberius-named-instance-discovery
```

Verify the exact remote SHA and RED ancestry.

---

### Task 5: Create the FastMssql Integration RED and Complete NINST Matrix

**Branch:** `test/named-instance-discovery`

**Files:**

- Create: `tests/test_named_instance_contract.py`
- Create: `tests/sql_auth_strict/sql_browser_fixture.py`
- Create: `tests/sql_auth_strict/test_named_instance_strict.py`
- Create: `scripts/sql_auth/named_instance_stress.py`
- Create: `scripts/sql_auth/run_named_instance_stress.sh`
- Modify: `tests/sql_auth_strict/test_matrix_contract.py`
- Modify: `tests/sql_auth_strict/conftest.py`
- Modify: `scripts/sql_auth/run_all.sh`
- Modify: `scripts/sql_auth/generate_report.py`
- Modify:
  `docs/superpowers/specs/2026-07-24-fastmssql-sql-auth-validation-design.md`
- Modify: `.github/workflows/rust-unit-tests.yml`
- Create: `.github/workflows/named-instance-windows.yml`
- Modify: `VERSION.md`

- [ ] **Step 1: create the FastMssql RED from the Tiberius fix**

```bash
git worktree add \
  .worktrees/test-named-instance-discovery \
  -b test/named-instance-discovery \
  fix/tiberius-named-instance-discovery
```

Expected: exact Tiberius fix parent, clean worktree and fork-only remotes.

- [ ] **Step 2: add offline compile/source contracts**

Require:

- root `Cargo.toml` includes `sql-browser-tokio`;
- `src/pool_manager.rs` imports `tiberius::SqlBrowser`;
- one pure target classifier selects Browser only for
  instance-without-explicit-port;
- direct, explicit-port and routing branches do not invoke discovery;
- discovery errors attach all four stable Python metadata fields;
- the stub and README document the exact transport matrix;
- the installed-wheel workflow runs the named-instance contract;
- the Windows workflow has `permissions: contents: read`, `windows-2022`,
  no third-party SQL setup action, no explicit FastMssql TCP port and no skip;
- no required named-instance test catches `Exception` merely to pass or calls
  `pytest.skip`.

- [ ] **Step 3: add the deterministic SSRP responder**

The fixture must:

- bind a caller-selected loopback UDP address;
- require the exact configured instance request including NUL;
- return a valid bounded response mapping `FASTMSSQL` to the real Docker SQL
  Server TCP port;
- expose exact request, response, source-rejection and shutdown counters;
- support silent, malformed, wrong-source and refused-TCP modes;
- provide deterministic readiness and bounded teardown;
- keep passwords and connection strings outside messages/artifacts.

Because FastMssql correctly fixes the browser endpoint at UDP 1434, the
Docker lane must use an isolated loopback alias/network arrangement that
allows the fixture to bind 1434 without changing FastMssql's public API. If
the host cannot bind the required address, the test fails with setup
diagnostics; it does not silently switch to a non-production port.

- [ ] **Step 4: assign each NINST case exactly once**

Add the following canonical requirements to the SQL-auth specification and
one `@case(...)` owner per ID:

| ID | Owning evidence |
| --- | --- |
| `NINST-001` | exact request observed by fixture |
| `NINST-002` | pre-I/O name rejection |
| `NINST-003` | valid response/real SQL |
| `NINST-004` | malformed response table, panic-free |
| `NINST-005` | invalid/duplicate TCP tokens |
| `NINST-006` | wrong-source response rejected |
| `NINST-007` | silent browser bounded |
| `NINST-008` | refused discovered TCP error detail |
| `NINST-009` | direct no-instance behavior unchanged |
| `NINST-010` | pooled constructor discovers and queries |
| `NINST-011` | ADO.NET named-instance string discovers and queries |
| `NINST-012` | direct `Transaction` discovers and settles |
| `NINST-013` | explicit port bypasses fixture |
| `NINST-014` | shorter outer connect deadline wins |
| `NINST-015` | cancellation then successful recovery |
| `NINST-016` | structured discovery exception metadata |
| `NINST-017` | concurrent pool creation bounded by `max_size` |
| `NINST-018` | 99,999 logical operations, bounded discoveries |
| `NINST-019` | isolated installed-wheel import and discovery |
| `NINST-020` | hosted Windows workflow contract plus exact run |
| `NINST-021` | source/artifact privacy scan |
| `NINST-022` | zero relevant sessions/transactions after teardown |

Raise the exact canonical total from 420 to 442 and update matrix assertions.
The local owner for `NINST-020` verifies the hosted lane contract; the actual
Windows run is additional mandatory evidence, not a duplicate case marker.

- [ ] **Step 5: add required and extended stress contracts**

The stress implementation must use:

- one `Connection` and one persistent pool;
- a fixed task set bounded by pool size, never 99,999 live tasks;
- parameterized `SELECT` operations with exact ID digest/count checks;
- 1,000 operations in the ordinary full runner;
- 99,999 only when the explicit extended switch is set;
- discovery request count equal to successful physical connection creation
  on the deterministic one-address fixture, never logical operation count;
- `max_total_connections <= pool.max_size`;
- bounded RSS, event-loop ticker, success/failure/timeout counts;
- post-load smoke query and zero post-disconnect application sessions.

Register `NINST-018` with the load-metric recorder and add privacy-safe report
fields only.

- [ ] **Step 6: add hosted workflow contracts before implementation**

The Windows workflow contract must require:

- download URL owned by Microsoft;
- Authenticode status `Valid` before installer execution;
- named instance `SQLEXPRESS`;
- SQL authentication/mixed mode;
- TCP enabled and SQL Server Browser set to automatic/running;
- firewall/local connectivity readiness;
- `sqlcmd -S localhost\SQLEXPRESS` without a port before FastMssql;
- exact candidate wheel built and installed in an isolated Python 3.13 venv;
- pooled `Connection`, direct `Transaction`, parameterized SQL and
  `SERVERPROPERTY('InstanceName')` checks;
- zero application sessions after disconnect;
- artifact upload only for redacted structural logs;
- no repository credential persistence and no write permission.

- [ ] **Step 7: run the intended FastMssql RED**

Run:

```bash
.venv/bin/pytest tests/test_named_instance_contract.py -q
.venv/bin/pytest \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/sql_auth_strict/test_named_instance_strict.py -q
```

Expected RED must show the absent root feature/classifier or the existing TCP
attempt to browser port 1434. Fixture/setup failures are not acceptable RED.

- [ ] **Step 8: commit and push the RED**

Update `VERSION.md` with exact commands/results, then commit all test,
specification and harness files:

```bash
git commit -m "test: require FastMssql named instance discovery"
git push -u origin test/named-instance-discovery
```

Verify the remote SHA and retain it as the mandatory parent of Task 6.

---

### Task 6: Integrate Discovery into FastMssql

**Branch:** `fix/named-instance`

**Files:**

- Modify: `Cargo.toml`
- Modify: `Cargo.lock` only if the locked Cargo invocation produces a real
  dependency delta
- Modify: `src/pool_manager.rs`
- Modify: `python/fastmssql/fastmssql.pyi`
- Modify: `README.md`
- Modify: `.github/workflows/rust-unit-tests.yml`
- Modify: `.github/workflows/named-instance-windows.yml`
- Modify: `VERSION.md`

- [ ] **Step 1: branch directly from FastMssql RED**

```bash
git worktree add \
  .worktrees/fix-named-instance \
  -b fix/named-instance \
  test/named-instance-discovery
git merge-base --is-ancestor \
  test/named-instance-discovery \
  fix/named-instance
```

Expected: ancestry check exits zero.

- [ ] **Step 2: enable the existing Tokio SQL Browser feature**

Change the root Tiberius features to:

```toml
features = ["chrono", "tds73", "rustls", "sql-browser-tokio"]
```

Regenerate the lock only through Cargo. Confirm no new runtime crate is added
beyond already locked optional Tiberius/Tokio paths. If `Cargo.lock` is
unchanged, do not touch it merely to create a diff.

- [ ] **Step 3: implement one pure initial-target classifier**

Represent the decision as a closed enum such as:

```rust
enum InitialTarget {
    Direct(String),
    SqlBrowser,
}
```

Classification must be:

```text
has instance && !has explicit port -> SqlBrowser
otherwise                           -> Direct(config.get_addr())
```

Add Rust unit tests for all four instance/port combinations.

- [ ] **Step 4: centralize the initial stream open**

In `src/pool_manager.rs`, create one helper that:

- calls ordinary `TcpStream::connect(address)` unchanged for `Direct`;
- calls `<TcpStream as SqlBrowser>::connect_named(config)` for `SqlBrowser`;
- sets `TCP_NODELAY`;
- wraps only discovery-branch failures in
  `PoolConnectionError::Discovery`;
- leaves direct I/O failures in `PoolConnectionError::Io { address }`.

Do not clone or expose credentials in the error. The existing outer
`run_until()` remains the sole FastMssql connect deadline.

- [ ] **Step 5: map discovery failures to stable Python metadata**

Create `SqlConnectionError`, set:

```text
message = redacted structural detail
stage = "sql_browser_discovery"
retryable = True
connection_discarded = False
outcome_unknown = False
```

If attaching metadata itself fails, return the existing safe metadata failure
pattern rather than panic. Direct and timeout mappings remain unchanged.

- [ ] **Step 6: keep routing unconditionally direct**

After `Error::Routing { host, port }`:

- overwrite host and port;
- call the direct TCP helper explicitly;
- do not classify using the original instance;
- perform exactly one second TDS connect attempt.

This preserves routed-address authority and prevents a second browser lookup.

- [ ] **Step 7: document the public matrix**

Add individual-argument and ADO.NET examples for:

```python
Connection(server="db-host", instance_name="SQLEXPRESS")
Connection(
    "Server=tcp:db-host\\SQLEXPRESS;..."
)
Connection(
    server="db-host",
    instance_name="SQLEXPRESS",
    port=51433,
)
```

State that the first two use SQL Browser and the third connects directly.
Do not claim Linux Docker is a native named instance.

- [ ] **Step 8: make focused RED green**

Run:

```bash
cargo fmt --check
cargo test --locked pool_manager
.venv/bin/pytest tests/test_named_instance_contract.py -q
.venv/bin/pytest \
  tests/sql_auth_strict/test_matrix_contract.py \
  tests/sql_auth_strict/test_named_instance_strict.py -q
```

Expected: all focused contracts pass with no skip or swallowed failure.

- [ ] **Step 9: self-review, graph review, commit and push**

Inspect the impact radius of `connect_client_inner`, verify pooled/direct/batch
callers, run privacy scans and `git diff --check`. Then:

```bash
git commit -m "fix: support SQL Server named instances"
git push -u origin fix/named-instance
```

Verify remote exact SHA and both RED ancestries.

---

### Task 7: Run Real Docker SQL-Auth, Failure, Cancellation and Load Gates

**Branch:** `verify/named-instance`

**Files:**

- Add only generated ignored artifacts during execution.
- Modify tracked files only for a separately demonstrated harness defect on a
  dedicated RED/fix branch.
- Modify: `VERSION.md` only when recording durable verification evidence in a
  later tracked commit.

- [ ] **Step 1: create the verification branch from the exact fix**

```bash
git worktree add \
  .worktrees/verify-named-instance \
  -b verify/named-instance \
  fix/named-instance
```

Verify fork boundaries and a clean exact parent.

- [ ] **Step 2: build the exact candidate wheel/runtime**

Use isolated build/cache directories and the repository lock:

```bash
uv sync --locked --all-extras --dev
uv run maturin develop --release
```

Record toolchain versions and exact SHA without recording environment values.

- [ ] **Step 3: start and provision only the approved Docker target**

Run the canonical Docker Compose SQL-auth service with force recreation, wait
for readiness and provision the databases. Verify the container identity and
TCP endpoint before tests. Do not delete the named data volume.

- [ ] **Step 4: run deterministic SSRP plus real SQL**

Run the named-instance strict file serially and require:

- pooled query;
- ADO.NET query;
- direct transaction insert/commit/readback;
- explicit-port bypass;
- deadline and cancellation recovery;
- malformed/silent/wrong-source/refused-target failures;
- pool max-size bound;
- zero relevant sessions and transactions after teardown.

- [ ] **Step 5: run 1,000 and 99,999 logical operations**

Run the required profile first, inspect counts, then explicitly enable the
extended profile. Required assertions:

```text
total == succeeded == requested
failed == timed_out == 0
missing_ids == duplicate_ids == []
1 <= physical_sessions <= pool.max_size
browser_requests == successful_physical_connections
browser_requests << logical_operations
post_load_smoke == true
post_teardown_sessions == 0
post_teardown_transactions == 0
```

Do not interpret SQL Server capacity as the test target; report driver
throughput, event-loop progress, RSS and pool/session bounds separately.

- [ ] **Step 6: run the complete canonical SQL-auth suite**

Execute `scripts/sql_auth/run_all.sh` and require every lane exit code zero:
Rust, vendored Tiberius, strict, true-async, framework, resilience, load,
original-local-regression, reports, privacy and teardown.

- [ ] **Step 7: isolate any real harness defect**

If a gate fails because the new deterministic harness is wrong rather than
runtime behavior:

1. reproduce it on a new `test/<specific-name>` branch;
2. commit the executable RED;
3. branch `fix/<specific-name>` directly from that RED;
4. make only the proven harness correction;
5. history-only integrate the correction into `verify/named-instance`;
6. retain all exact SHAs in `VERSION.md`.

Never edit around a failure directly on the verification branch.

---

### Task 8: Verify the Installed Wheel and Hosted Platforms

**Branch:** `verify/named-instance`

- [ ] **Step 1: build and hash an isolated ABI3 wheel**

Build from the exact clean candidate SHA. Install it into a fresh Python 3.13
venv outside the source import path. Unset `PYTHONPATH`, confirm
`fastmssql.__file__` and the native extension resolve under that venv's
`site-packages`, run `pip check` and hash the wheel.

- [ ] **Step 2: run installed-wheel protocol and Docker discovery**

Against the installed wheel, run:

- offline named-instance contract;
- deterministic SSRP fixture;
- pooled and direct real-SQL paths;
- explicit-port bypass;
- at least the 1,000-operation profile;
- teardown and privacy scans.

`NINST-019` is not satisfied by an in-tree `maturin develop` import.

- [ ] **Step 3: push the exact verification candidate only to origin**

Push `verify/named-instance`, verify `ls-remote`, then inspect public Actions
read-only. Do not dispatch or write to the original repository.

- [ ] **Step 4: require cross-platform Rust/wheel success**

The exact SHA must pass on Linux, macOS and Windows:

```text
cargo build --locked
cargo test --locked
vendored Tiberius tests with sql-browser-tokio
wheel build
isolated wheel install
named-instance offline/loopback contract
pip check
```

- [ ] **Step 5: require genuine Windows named-instance success**

On `windows-2022`, the repository-owned lane must:

1. download the SQL Server Express installer from Microsoft;
2. validate a Microsoft Authenticode signature;
3. install `SQLEXPRESS` with SQL authentication;
4. enable TCP and restart the instance;
5. enable/start SQL Server Browser;
6. verify `sqlcmd -S localhost\SQLEXPRESS` without a port;
7. build/install the exact wheel;
8. connect without a port through `Connection` and direct `Transaction`;
9. prove `SERVERPROPERTY('InstanceName') = 'SQLEXPRESS'`;
10. execute parameterized SQL and commit/rollback behavior;
11. disconnect and prove zero residual candidate sessions.

No required step may use `continue-on-error`, skip or a swallowed exception.

- [ ] **Step 6: run RustSec and inspect every job**

Require zero warning/vulnerability result from the exact lockfile. Read every
job conclusion and relevant failing log if any. A green aggregate status does
not replace per-job inspection.

---

### Task 9: Perform the Exact-SHA Completion Audit

**Branch:** `verify/named-instance`

- [ ] **Step 1: rebuild the knowledge graph**

```bash
uvx code-review-graph build
```

Require graph branch/SHA equality, then run change detection, affected flows,
impact radius and `tests_for` queries for the classifier and physical
connection helper. Explain or close every test gap.

- [ ] **Step 2: verify repository integrity**

Run:

```bash
git diff --check
cargo fmt --check
cargo test --locked
cargo audit
```

Also run vendored format, Clippy and test commands with
`sql-browser-tokio`, Python lint, focused tests, complete SQL-auth runner,
installed-wheel tests and privacy scans.

- [ ] **Step 3: audit every acceptance criterion against evidence**

For each checkbox in the approved design, record:

- exact source or test that owns the behavior;
- exact command and result;
- exact candidate SHA;
- local/Docker/wheel/hosted evidence location;
- whether evidence is direct or only a contract;
- any residual platform or environment risk.

Feature closure is forbidden if any item is missing, indirect where runtime
evidence is required, skipped, stale or tied to a different SHA.

- [ ] **Step 4: verify fork-only publication**

List every feature branch and exact remote SHA. Confirm:

- all exist on `galeamarcel/FastMssql`;
- no pushable original remote exists;
- no upstream PR, release, package or wheel was published;
- all RED commits remain ancestors of their fixes.

---

### Task 10: Close Feature 20 in the Live Audit

**Branch:** `docs/named-instance-status`

**Files:**

- Modify: `docs/FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`
- Modify:
  `docs/superpowers/specs/2026-07-30-fastmssql-named-instance-discovery-design.md`
- Create: `docs/validation/fastmssql-named-instance-report.md`
- Modify: generated SQL-auth matrix/report evidence
- Modify:
  `docs/superpowers/plans/2026-07-24-fastmssql-upstream-pr-roadmap.md`
- Modify: `VERSION.md`

- [ ] **Step 1: branch from the exact verified candidate**

```bash
git worktree add \
  .worktrees/docs-named-instance-status \
  -b docs/named-instance-status \
  verify/named-instance
```

- [ ] **Step 2: write evidence, not intent**

The report must include:

- root cause;
- selected transport matrix;
- exact documentation/RED/fix/verification/status SHAs;
- Tiberius request/parser/network results;
- Docker SQL-auth results;
- 1,000 and 99,999 operation metrics;
- wheel filename/hash/import origin;
- Linux/macOS/Windows job/run identifiers;
- genuine Windows SQL Browser evidence;
- full cumulative test counts;
- teardown/privacy results;
- residual risks and deliberately excluded scope.

Do not copy credentials, connection strings or raw environment output.

- [ ] **Step 3: mark the live audit only when proven**

Change item 20 from pending to `VERIFIED_FORK` only if every Task 9 criterion
is directly supported. Keep item 21, the production framework matrix, pending
and next in sequence.

- [ ] **Step 4: regenerate canonical reports and self-review**

Regenerate from exact candidate artifacts, run report/matrix contract tests,
privacy scans, `git diff --check`, link checks and graph review. Confirm all
counts and SHAs agree across documents.

- [ ] **Step 5: commit and push status only to the fork**

```bash
git commit -m "docs: close named instance discovery"
git push -u origin docs/named-instance-status
```

Verify the remote exact SHA. Do not open an original-repository PR or publish
an artifact.

---

## Completion Definition

Feature 20 is complete only when Tasks 1 through 10 are checked, both RED
commits remain in fix ancestry, every `NINST-001` through `NINST-022` has
exactly one canonical owner, deterministic SSRP and genuine Windows SQL
Browser evidence both pass, the 99,999-operation profile remains pool- and
discovery-bounded, the installed wheel and all hosted platforms pass on one
exact cumulative SHA, teardown/privacy are clean, and the live audit records
that exact evidence. Passing only the loopback fixture, only Docker, only
source-tree imports or only a hosted contract is insufficient.
