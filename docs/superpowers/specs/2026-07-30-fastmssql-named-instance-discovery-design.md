# FastMssql Named-Instance Discovery Design

**Status:** Approved by Marcel Galea through the standing approval for future
enterprise designs, specifications, plans and inline implementations, subject
to self-review

**Source baseline:** `docs/batch-bulk-status` at
`30f975033090d623b27795da76d59ddcef1277ab`

**Technical predecessor:** batch/bulk candidate
`306b44d1aafce6b0dc763bfe179784de5bfd6f06`

**Target repository:** `https://github.com/galeamarcel/FastMssql.git`

**Publication boundary:** every branch, commit and hosted validation produced
by this candidate belongs only to Marcel Galea's fork. The original
`Rivendael/FastMssql` repository remains fetch-only with push URL `DISABLED`.
No upstream branch, push, pull request, release, package publication or
separate Tiberius fork is authorized by this design.

## Decision summary

FastMssql will make the existing `instance_name` argument functional without
changing ordinary host/port connections:

| Configuration | Transport decision |
| --- | --- |
| no instance, no explicit port | direct TCP to the existing default `1433` |
| no instance, explicit port | direct TCP to that port |
| instance, no explicit port | SQL Server Resolution Protocol (SSRP) over UDP `1434`, then direct TCP to the discovered port |
| instance and explicit port | direct TCP to the explicit port; no SQL Browser request |
| Azure routing response | direct TCP to the routed host and port; no second SQL Browser request |

The same matrix applies to individual constructor arguments and ADO.NET
connection strings. In particular:

```text
Server=tcp:db-host\SQLEXPRESS
```

uses SQL Browser, while:

```text
Server=tcp:db-host\SQLEXPRESS,51433
```

uses direct TCP port `51433`.

This preserves the current FastMssql behavior and stub contract for an
explicit `port`, while repairing the broken no-port named-instance case.

The selected implementation has two layers:

1. harden the vendored Tiberius 0.12.3 Tokio SQL Browser implementation and
   prove its SSRP wire behavior on a separate RED/fix ancestry;
2. add one shared FastMssql TCP-target selector used by pooled connections,
   the direct compatibility `Transaction`, direct batch paths and connection
   routing.

The complete FastMssql connect deadline continues to cover Azure credential
acquisition, DNS, UDP discovery, TCP, TLS, TDS login and one routing reconnect.
There is no driver-level query retry and no discovery-port cache.

Feature closure requires three distinct levels of evidence:

1. deterministic protocol tests with a bounded SSRP responder;
2. end-to-end SQL authentication against the real Docker SQL Server reached
   through that responder, with no TCP port in the FastMssql configuration;
3. a hosted Windows SQL Server Express named instance reached through the real
   SQL Server Browser service, again with no explicit port.

The Docker database is a real SQL Server but, because SQL Server on Linux
supports only one default instance per host, its SSRP responder is explicitly
identified as a protocol fixture. It is not mislabeled as a Windows named
instance. The Windows gate supplies the missing genuine named-instance
evidence.

## Evidence and root cause

### FastMssql accepts the option but opens the wrong transport

Both public constructors currently call:

```rust
config.instance_name(instance_name);
```

The shared physical connection path in `src/pool_manager.rs` then always does:

```rust
let address = config.get_addr();
tokio::net::TcpStream::connect(&address).await
```

Vendored Tiberius returns port `1434` from `Config::get_addr()` when an
instance exists and no port was configured. Port `1434` is the SQL Browser UDP
endpoint, not the TDS TCP endpoint. FastMssql therefore attempts TCP against
the UDP discovery port and never sends SSRP.

The root `Cargo.toml` enables Tiberius features `chrono`, `tds73` and `rustls`,
but not `sql-browser-tokio`. The correct Tiberius extension trait is therefore
not compiled into the FastMssql wheel.

### Existing tests do not prove discovery

The original named-instance tests are not acceptance evidence:

- `tests/test_explicit_connection_advanced.py` catches timeouts and connection
  failures, and may skip the test;
- `tests/test_transaction.py` proves only that the constructor accepts the
  argument;
- no test observes a UDP request, a discovered TCP destination, successful
  SQL authentication through discovery or a genuine named instance.

The replacement suite must contain no swallowed exception and no skip for a
required gate.

### The vendored SQL Browser implementation also needs hardening

Enabling `connect_named()` unchanged would expose additional defects:

1. `CLNT_UCAST_INST` is encoded as byte `0x04` plus the instance bytes, but the
   required trailing NUL byte is missing.
2. A response shorter than three bytes reaches `&buf[3..len]` and can panic.
3. The response type byte and little-endian declared payload length are not
   validated.
4. A 4,096-byte buffer is accepted even though an instance response is bounded
   to 1,024 payload bytes.
5. The UDP socket is unconnected and accepts a datagram from any source.
6. token matching is case-sensitive even though SQLR response content is
   case-insensitive.
7. duplicate or malformed `tcp` fields are not rejected deterministically.
8. TCP failures are discarded and replaced by the misleading
   `"Could not resolve server host"` error.
9. the Tiberius `Conversion` error used for discovery would become a Python
   `ConversionError`, although discovery is a connection-stage failure.

These findings are present in both the vendored 0.12.3 source and the current
public Tiberius `main` source inspected on 2026-07-30. The FastMssql fix must
not merely activate that code.

## Goals

1. Make `instance_name` without an explicit port perform true async SSRP and
   then establish a true async TDS connection.
2. Preserve byte-for-byte behavior of the ordinary direct TCP branch as far
   as the selected target and Python exception taxonomy are concerned.
3. Preserve explicit-port compatibility and document that the port bypasses
   discovery.
4. Use the same selector for pool creation, direct transactions, direct batch
   connections and Azure-authenticated connections.
5. Keep one absolute connect deadline across discovery and all later connect
   phases.
6. Make malformed, truncated, oversized, spoofed-source and semantically
   invalid SSRP responses fail without panic.
7. Preserve useful DNS, UDP and TCP failure detail instead of replacing it
   with a false host-resolution error.
8. Map discovery failures to `SqlConnectionError` and connect-deadline expiry
   to the existing structured `OperationTimeoutError`.
9. Keep credentials, connection strings and SQL text out of SSRP, diagnostics
   and test artifacts.
10. Prove `Connection`, direct `Transaction`, connection-string parsing,
    explicit-port bypass, timeout, cancellation and pool recovery.
11. Prove that a persistent pool does not perform discovery per SQL operation.
12. Verify Linux, macOS and Windows builds and protocol tests from an installed
    wheel.
13. Verify SQL authentication through both a deterministic SSRP fixture plus
    Docker SQL Server and a genuine Windows named instance plus SQL Browser.
14. Preserve all cumulative SQL-auth, framework, load, original-local-
    regression, Rust, packaging and RustSec gates.

## Non-goals

This feature does not:

- implement SQL Server named pipes, shared memory or VIA;
- add instance enumeration (`CLNT_BCAST_EX` or `CLNT_UCAST_EX`);
- implement the Dedicated Administrator Connection discovery packet;
- add automatic fallback from a failed instance lookup to port `1433`;
- retry a SQL statement, transaction command or uncertain operation;
- add a global or per-pool discovered-port cache;
- add a configurable public SQL Browser port;
- add a public discovery timeout distinct from `connect_timeout_secs`;
- alter TLS policy or make `TrustServerCertificate=True` a production
  recommendation;
- authenticate SSRP itself, which is an unauthenticated UDP protocol;
- claim that the Linux Docker SQL Server is a native named instance;
- add Unicode/code-page transcoding configuration for legacy Windows code
  pages;
- change pool size, session reset, operation metrics or lifecycle semantics;
- bump the displayed package version, publish a wheel or publish a release;
- create a Tiberius fork or send any change to either original repository.

## Options considered

### Option A — Call `connect_named()` for every connection

Enable `sql-browser-tokio` and replace every direct `TcpStream::connect()` with
`TcpStream::connect_named()`.

**Rejected.** It changes DNS/TCP error behavior for every existing user,
discards useful direct-connect failures, does not preserve FastMssql's
explicit-port contract and exposes the malformed-response panic.

### Option B — Implement a second SSRP client inside FastMssql

Keep Tiberius unchanged and duplicate SQL Browser discovery in
`src/pool_manager.rs`.

**Rejected.** FastMssql would need a second interpretation of Tiberius
configuration and ADO.NET server syntax. Two independent parsers would create
exactly the configuration drift this feature is intended to eliminate.

### Option C — Harden vendored Tiberius and select it conditionally

Add minimal read-only target introspection to vendored `Config`, harden the
Tokio SSRP implementation, and call it only for an instance with no explicit
port.

**Selected.** It repairs the protocol at its owning layer, keeps one FastMssql
connect path and leaves ordinary TCP behavior unchanged.

## Public behavior

No new Python constructor argument or exception class is introduced.

### Individual parameters

```python
Connection(
    server="db-host",
    instance_name="SQLEXPRESS",
    database="app",
    username="app_user",
    password="...",
)
```

performs discovery at `db-host:1434/udp`.

```python
Connection(
    server="db-host",
    instance_name="SQLEXPRESS",
    port=51433,
    ...
)
```

connects directly to `db-host:51433/tcp`. `instance_name` remains available as
configuration metadata, but does not trigger a browser request because the
caller supplied the target port.

The same rule applies to the direct compatibility constructor:

```python
Transaction(server=..., instance_name=..., port=...)
```

Transactions obtained from `Connection.transaction()` inherit the already
classified pooled configuration.

### ADO.NET connection strings

Supported forms are documented as:

```text
Server=tcp:db-host\SQLEXPRESS;...
Data Source=db-host\SQLEXPRESS;...
Server=tcp:db-host\SQLEXPRESS,51433;...
Server=tcp:db-host,51433;...
```

The first two discover a port. The latter two connect directly.

`connection_string` keeps its existing precedence over individual connection
arguments. FastMssql will not merge `instance_name` or `port` passed
separately into an already supplied connection string.

### Instance-name wire validation

Before any UDP send, vendored Tiberius rejects:

- an empty instance name;
- an embedded NUL;
- an encoded request name longer than the SQLR limit of 32 bytes.

The client appends one NUL terminator on the wire. FastMssql does not attempt
to reproduce all SQL Server Setup naming rules because a real installed
instance has already passed those rules and legacy non-ASCII names depend on
the Windows code page shared with the server. The 32-byte protocol boundary is
the deterministic transport invariant.

### Error contract

Browser/DNS/UDP/response/TCP failures on the discovery branch raise
`SqlConnectionError`. The exception receives stable metadata:

```text
stage = "sql_browser_discovery"
retryable = True
connection_discarded = False
outcome_unknown = False
```

No SQL operation has been sent at this point.

If the outer FastMssql connect deadline expires first, the existing
`OperationTimeoutError` remains authoritative with:

```text
phase = "connect"
retryable = True
connection_discarded = False
outcome_unknown = False
```

Tiberius's protocol-recommended one-second receive timer remains a bounded
inner failure for each resolved browser address. A shorter FastMssql connect
deadline wins. A longer deadline does not turn one silent browser address
into an unbounded wait.

An explicit-port connection continues to use the existing direct
`PoolConnectionError::Io { address }` mapping.

## Vendored Tiberius contract

### Feature and configuration introspection

The root dependency enables:

```toml
features = ["chrono", "tds73", "rustls", "sql-browser-tokio"]
```

Vendored `Config` adds read-only methods sufficient to classify the target:

```rust
pub fn has_instance_name(&self) -> bool;
pub fn has_explicit_port(&self) -> bool;
```

The existing setters, parser fields and `get_addr()` remain source-compatible.
FastMssql owns the policy that an explicit port bypasses browser discovery.
Direct users of Tiberius `connect_named()` retain its existing ability to use
`Config::port()` as the browser endpoint, which is useful for deterministic
protocol tests.

### SSRP request

For `CLNT_UCAST_INST`, the request is exactly:

```text
0x04 || INSTANCE_NAME_BYTES || 0x00
```

The request is built by a pure, unit-tested helper. No username, password,
database, application name or SQL text is present.

### UDP exchange

For every DNS address candidate:

1. bind an ephemeral wildcard address of the same IP family;
2. connect the UDP socket to the selected browser address;
3. send one bounded request;
4. receive at most `3 + 1024` bytes;
5. enforce the one-second response timer;
6. parse only a response from the connected peer;
7. connect TCP to the returned port on that same IP address.

There is no broadcast, retransmission or unbounded allocation.

### SSRP response parser

The parser is total over every byte slice. It validates:

- at least the three-byte response header;
- response type `0x05`;
- little-endian `RESP_SIZE`;
- exact equality between declared and received payload length;
- payload length at most 1,024 bytes;
- exactly one case-insensitive `tcp` token;
- a nonempty decimal port in the inclusive range `1..=65535`;
- no duplicate `tcp` token.

Only the ASCII token and port bytes are interpreted. Other response values may
use the server's multibyte code page and are not decoded as UTF-8.

Malformed data returns an error. No index, slice, integer parse or formatting
path may panic.

### Address and error preservation

The implementation keeps the last meaningful DNS/UDP/TCP error while trying
resolved addresses. A refused discovered TCP port remains a connection-
refused I/O failure; it is not rewritten as host-not-found.

## FastMssql connector architecture

`src/pool_manager.rs` remains the only owner of physical connection setup.
A small pure classifier selects:

```text
Direct(address)
SqlBrowser
```

`connect_client_inner()` performs:

```text
clone config
resolve Azure credential when present
select and open the initial TCP stream
set TCP_NODELAY
perform TDS/TLS/login
if Azure routing is returned:
    overwrite host and port
    open one direct TCP stream
    perform TDS/TLS/login once more
```

The routing response always includes an explicit host and port, so it cannot
re-enter SQL Browser discovery even if the original configuration contained
an instance name.

All current callers already converge on this helper:

- `AzureConnectionManager::connect()` for the pool;
- `Transaction::ensure_connected_inner()` for the direct constructor;
- the direct batch connection path.

No constructor-specific network implementation is added.

## Cancellation, pooling and concurrency

SSRP runs inside the existing `run_until()` connect deadline. Dropping or
timing out the future drops the UDP socket and any not-yet-admitted TCP stream.
There is no pooled connection to retire before TDS login succeeds.

Each new physical connection resolves the instance independently. This avoids
a stale global port after SQL Server restarts on a new dynamic port. The pool
then amortizes discovery over many SQL operations:

```text
logical operations >> browser requests ~= physical connection attempts
```

The required load gate records:

- logical operation count;
- physical `connections_created`;
- maximum pool connections;
- number of valid SSRP requests;
- final sessions and open transactions.

At 99,999 logical operations, SSRP requests must remain bounded by physical
connection attempts, DNS address candidates and explicitly induced reconnects,
never by the logical operation count. The deterministic Docker fixture uses
one numeric loopback address, so its successful browser-request count must
equal its successful physical-connection count.

## Security and privacy

SQL Browser is unauthenticated UDP. A same-host/network attacker able to spoof
the browser endpoint could redirect the client to another TCP port. This
feature reduces, but cannot remove, that protocol-level risk:

- the UDP socket accepts only the queried peer;
- responses are length- and grammar-validated;
- there is no broadcast;
- TLS certificate validation remains the authentication boundary for the
  final SQL Server connection when the caller does not opt into
  `TrustServerCertificate`;
- the discovered port is never treated as proof of server identity.

Production guidance will recommend a static explicit TCP port when UDP `1434`
is prohibited, and normal certificate verification for the final connection.

Error messages and artifacts must not contain:

- passwords or access tokens;
- complete connection strings;
- SQL statements or parameter values;
- environment-file contents.

Host and instance names may appear in connection diagnostics because they are
the requested endpoint, but tests use non-sensitive synthetic names.

## Verification layers

### Layer 1 — static and source contracts

Offline tests prove:

- the root Tiberius feature is enabled;
- ordinary direct connect code remains present;
- conditional discovery is centralized;
- pool, transaction and batch use the shared helper;
- stubs and README describe explicit-port precedence;
- no required named-instance test catches and suppresses its failure.

### Layer 2 — vendored Tiberius RED/fix

Pure parser and Tokio loopback tests cover:

- exact request bytes including NUL;
- empty, NUL-containing and oversized names;
- valid lowercase/uppercase `tcp` token;
- response shorter than three bytes;
- wrong response type;
- inconsistent or oversized declared length;
- missing and duplicate TCP tokens;
- empty, zero, negative, overflow and nonnumeric ports;
- valid UDP response followed by TCP acceptance;
- wrong-source datagram rejection;
- TCP refusal detail preservation;
- bounded silent-browser timeout;
- direct no-instance behavior.

Tests use an ephemeral custom browser port through Tiberius directly. They do
not need SQL Server and run on Linux, macOS and Windows.

### Layer 3 — installed-wheel transport contract

An installed-wheel test owns a loopback SSRP responder on UDP `1434` and a
bounded TCP blackhole. It constructs FastMssql without an explicit port,
observes the exact UDP request and proves that the wheel reaches the returned
TCP listener before the configured connect timeout.

This test runs in an isolated environment with `PYTHONPATH` unset. It proves
that the feature is compiled into the wheel rather than accidentally imported
from the source tree.

### Layer 4 — real Docker SQL Server through deterministic SSRP

The strict SQL-auth suite runs a protocol-faithful responder that maps the
synthetic instance `FASTMSSQL` to the host port of
`fastmssql-sql-auth-dev`. Required tests cover:

- pooled `Connection` with individual arguments;
- pooled `Connection` with an ADO.NET connection string;
- direct `Transaction`;
- SQL-auth login and a real query;
- `APP_NAME()`, SPID and database identity;
- explicit-port bypass with zero browser request;
- unknown instance;
- malformed response;
- connect timeout and external cancellation;
- reconnect after cancellation/failure;
- concurrent pool establishment;
- 1,000 and 99,999 logical operations with bounded pool/discovery counts;
- zero residual test sessions and open transactions.

The responder validates the exact request before replying and records only
synthetic instance names, source addresses, counts and ports.

### Layer 5 — genuine Windows named instance

A dedicated hosted `windows-2022` job installs SQL Server Express as a named
instance with SQL authentication, enables TCP, starts SQL Server Browser and
does not pass the instance TCP port to FastMssql.

Before FastMssql runs, `sqlcmd` verifies the named-instance endpoint through
SQL Browser. The exact candidate wheel is then installed into an isolated
Python 3.13 environment and must:

- connect to `localhost\SQLEXPRESS` without a port;
- authenticate with SQL Server authentication;
- prove `SERVERPROPERTY('InstanceName') = 'SQLEXPRESS'`;
- execute a parameterized query and transaction;
- exercise both `Connection` and direct `Transaction`;
- disconnect with zero residual application sessions.

The SQL Server installer is downloaded only from Microsoft, its Authenticode
signature is validated before execution, and the job receives no repository
write permission. Any third-party setup action would have to be pinned by full
commit SHA and separately reviewed; the implementation plan prefers the
auditable repository-owned PowerShell lane.

No skip is allowed if installation, SQL Browser startup, discovery or SQL
authentication fails.

## Required case IDs

The feature uses a separate named-instance contract namespace:

| ID | Requirement |
| --- | --- |
| `NINST-001` | exact NUL-terminated `CLNT_UCAST_INST` request |
| `NINST-002` | request name length and embedded-NUL rejection |
| `NINST-003` | valid bounded `SVR_RESP` TCP-port parsing |
| `NINST-004` | malformed/truncated/oversized response is panic-free |
| `NINST-005` | missing/duplicate/invalid TCP fields are rejected |
| `NINST-006` | connected UDP rejects a wrong-source response |
| `NINST-007` | silent browser is bounded |
| `NINST-008` | discovered TCP failure detail is preserved |
| `NINST-009` | direct host/port behavior is unchanged |
| `NINST-010` | individual pooled connection discovers and queries real SQL |
| `NINST-011` | ADO.NET named-instance string discovers and queries real SQL |
| `NINST-012` | direct `Transaction` discovers and settles on real SQL |
| `NINST-013` | explicit port bypasses SQL Browser |
| `NINST-014` | connect deadline covers discovery |
| `NINST-015` | cancellation leaves the object recoverable |
| `NINST-016` | discovery failures use structured connection taxonomy |
| `NINST-017` | concurrent pool creation remains bounded |
| `NINST-018` | 99,999 logical operations do not discover per operation |
| `NINST-019` | installed wheel contains and exercises discovery |
| `NINST-020` | genuine Windows named instance succeeds without a port |
| `NINST-021` | privacy scan contains no credential/connection-string leak |
| `NINST-022` | post-test SQL sessions and transactions are zero |

Each required ID has one owning test node in the canonical matrix. A required
failure or skip fails the report.

## Branch and ancestry discipline

The planned public fork history is:

1. `docs/named-instance-design`
2. `test/tiberius-named-instance-discovery`
3. `fix/tiberius-named-instance-discovery`
4. `test/named-instance-discovery`
5. `fix/named-instance`
6. any separately justified test/harness correction branch discovered by a
   real failing gate
7. `verify/named-instance`
8. `docs/named-instance-status`

Every fix branch begins at its corresponding RED commit so the failing
contract remains in ancestry. History-only integration is used when multiple
independently reviewable corrections must be combined. No squash may erase a
RED boundary.

Every repository-changing commit updates `VERSION.md`. No commit in this
feature changes the displayed `0.7.7` metadata unless a later explicit release
decision authorizes it.

## Acceptance criteria

Feature 20 may be marked `VERIFIED_FORK` only when all of the following are
true on one exact cumulative SHA:

- [ ] `sql-browser-tokio` is compiled into FastMssql;
- [ ] instance without port uses UDP discovery and the discovered TCP port;
- [ ] explicit port performs no browser request;
- [ ] ordinary no-instance direct connection retains its existing behavior;
- [ ] pool, direct transaction, direct batch and routing use the shared path;
- [ ] the request includes the required NUL and respects the 32-byte limit;
- [ ] every response parser input is panic-free and bounded;
- [ ] response source, header, declared size, token uniqueness and port are
      validated;
- [ ] discovery failures are structured `SqlConnectionError`;
- [ ] the absolute connect deadline and cancellation contracts pass;
- [ ] deterministic SSRP plus real Docker SQL-auth passes without a configured
      TCP port;
- [ ] 99,999 logical operations retain bounded pool and discovery counts;
- [ ] the installed wheel passes with `PYTHONPATH` unset;
- [ ] Linux, macOS and Windows raw Cargo/Tiberius/wheel contracts pass;
- [ ] a genuine hosted Windows `SQLEXPRESS` instance plus SQL Browser passes
      without an explicit port;
- [ ] cumulative strict, async, framework, resilience, load,
      original-local-regression, Rust and RustSec gates pass;
- [ ] teardown reports zero relevant sessions and transactions;
- [ ] privacy and tracked-artifact scans pass;
- [ ] the knowledge graph is rebuilt on the exact SHA and impact review has no
      unexplained test gap;
- [ ] `FASTMSSQL_PRODUCTION_READINESS_AUDIT.md`, the SQL-auth matrix/report,
      validation evidence, stubs, README and `VERSION.md` agree;
- [ ] every branch and exact SHA is present only on
      `galeamarcel/FastMssql`.

## Documentation sources

Context7 was attempted on 2026-07-30 but returned its monthly-quota error.
The design therefore uses the exact vendored source plus primary public
documentation:

- [Tiberius 0.12.3 `SqlBrowser`](https://docs.rs/tiberius/0.12.3/tiberius/trait.SqlBrowser.html)
- [Tiberius 0.12.3 `Config`](https://docs.rs/tiberius/0.12.3/tiberius/struct.Config.html)
- [Tiberius 0.12.3 Tokio SQL Browser source](https://github.com/prisma/tiberius/blob/v0.12.3/src/sql_browser/tokio.rs)
- [Microsoft SQL Server Browser service](https://learn.microsoft.com/en-us/sql/database-engine/configure-windows/sql-server-browser-service-database-engine-and-ssas?view=sql-server-ver17)
- [Microsoft SQLR `CLNT_UCAST_INST`](https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-sqlr/c97b04b5-d80f-4d3e-9195-83bbfe246639)
- [Microsoft SQLR `SVR_RESP`](https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-sqlr/2e1560c9-5097-4023-9f5e-72b9ff1ec3b1)
- [Microsoft SQLR timers](https://learn.microsoft.com/en-us/openspecs/windows_protocols/mc-sqlr/eb2f6036-e02b-4df5-9c04-e7bd64ecba15)
- [Microsoft SQL Server instance naming](https://learn.microsoft.com/en-us/sql/sql-server/install/instance-configuration?view=sql-server-ver17)
- [Microsoft SQL Server 2022 on Linux feature support](https://learn.microsoft.com/en-us/sql/linux/sql-server-linux-editions-and-components-2022?view=sql-server-ver17)
