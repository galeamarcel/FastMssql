# FastMssql Named-Instance Discovery Validation Report

**Status:** `VERIFIED_FORK`

**Technical candidate:**
`5728421a3941ce3ca957c5497bc53a78d5553b30`

**Repository:** `https://github.com/galeamarcel/FastMssql.git`

**Authentication under test:** SQL Server username/password authentication

**Publication boundary:** every branch and commit in this report exists only
on Marcel Galea's fork. The original `Rivendael/FastMssql` remote is
fetch-only with push URL `DISABLED`. No original-repository PR, Tiberius fork,
release, package or wheel was published.

## Verdict

The selected implementation is locally complete on the exact cumulative
candidate above:

- a named instance without an explicit port performs bounded SQL Server
  Resolution Protocol discovery over UDP `1434`, then opens the discovered
  TCP/TLS/TDS endpoint asynchronously;
- an explicit port is authoritative and bypasses SQL Browser;
- ordinary direct connections and Azure routing retain direct TCP behavior;
- pooled `Connection`, direct `Transaction` and direct batch setup converge
  on the same physical-connect selector;
- malformed, oversized, wrong-source, silent and refused-target responses
  fail without panic and retain structured connection errors;
- Docker SQL-auth, 1,000- and 99,999-operation profiles, an isolated CPython
  3.13 wheel, teardown and privacy gates pass.

The three hosted workflows for the same SHA also pass. Feature 20 is
therefore closed as `VERIFIED_FORK`. Feature 21, the production framework
process matrix, remains separate and pending.

## Root cause

FastMssql accepted `instance_name` in both public constructors but always
opened:

```text
TcpStream::connect(config.get_addr())
```

Vendored Tiberius returns port `1434` when an instance is present without an
explicit port. Port `1434` is the SQL Browser UDP endpoint, not the instance's
TDS TCP endpoint. FastMssql therefore attempted TCP against the UDP discovery
port and never sent an SSRP request. The root dependency also omitted the
Tiberius `sql-browser-tokio` feature.

Simply enabling the old Tiberius adapter was not sufficient. The vendored
implementation omitted the required request NUL, could slice a response
shorter than three bytes, did not enforce response type/declared size/source,
accepted excessive data, matched tokens case-sensitively, did not reject
duplicate or invalid TCP fields deterministically and replaced meaningful TCP
failures with a misleading host-resolution error.

## Selected transport matrix

| Configuration | Selected transport |
|---|---|
| no instance, no explicit port | direct TCP to the existing default `1433` |
| no instance, explicit port | direct TCP to that port |
| instance, no explicit port | SSRP to UDP `1434`, then TCP to the discovered port |
| instance and explicit port | direct TCP to the explicit port; zero browser requests |
| Azure routing response | direct TCP to the routed host/port; no rediscovery |

The same rules apply to individual constructor arguments and ADO.NET
connection strings.

## Branches, commits and ancestry

| Boundary | Exact commit |
|---|---|
| live-audit baseline | `30f975033090d623b27795da76d59ddcef1277ab` |
| approved design | `3162a26` |
| approved executable plan / `docs/named-instance-design` | `0083a472b5547c50629c006f095e9d8bc49fdd6f` |
| initial Tiberius pure-protocol RED | `a10f1ca` |
| final Tiberius protocol/network RED | `ebe96f7cd195c4d70a586fc9da6c5d8cfe7178b6` |
| Tiberius fix | `e5ccb60f5c7d05513e8fd36b60a3372f85577380` |
| FastMssql integration RED | `ebe13bedd407846874f92e9b82157bef2ba2179a` |
| refused-target fixture RED | `d8c32c98f42fc2dcce2b4467235d547a0beb251b` |
| refused-target fixture fix | `18c14a146f6696cac19edac0f5999258959520c5` |
| FastMssql runtime fix | `ff769825f6d3b7833b7be9d83fed4c0cc560f59b` |
| hosted-trigger RED / fix | `3b87aac167813b080e1a78b7a88974ba9779a2bd` / `bd9bbcacc5f9887f678faf57ae667f596635845d` |
| hosted `pip check` RED / fix | `185cdd54efe2fa1bbfa624988b7f705c25f5fd37` / `d71ae583fec9aa37f70c4d73c95a3ac9ceacd584` |
| first complete technical verification | `99c4a7c95f14557697eda1920ece718e8a63958f` |
| generated-report zero-value RED / fix | `a6409f603e08c62045bf0309762a7dce3e33e55e` / `17a343839f75c48d6c159f5581e4d4e79a54c9e1` |
| final cumulative technical candidate | `5728421a3941ce3ca957c5497bc53a78d5553b30` |
| status branch base | `5728421a3941ce3ca957c5497bc53a78d5553b30` |

Every RED is an ancestor of its corresponding fix and every listed fix is an
ancestor of the final candidate. The exact identity of the commit containing
this report is verified against the fork after push; a Git commit cannot
embed its own hash without changing that hash.

## Runtime and protocol evidence

Vendored Tiberius now:

- builds `0x04 || INSTANCE_NAME_BYTES || 0x00`;
- rejects empty names, embedded NUL and encoded names longer than 32 bytes
  before network I/O;
- connects the UDP socket to the resolved browser peer;
- uses a fixed `3 + 1024 + 1` byte buffer, where the last byte is an oversize
  sentinel;
- validates response type `0x05`, exact declared length, the 1,024-byte
  payload ceiling, one case-insensitive `tcp` token and port `1..=65535`;
- preserves meaningful DNS, UDP and discovered-TCP errors;
- keeps the one-second protocol receive bound.

FastMssql enables only `sql-browser-tokio` and uses one closed classifier:

```text
Direct(config.get_addr())
SqlBrowser
```

Initial direct and discovery streams are separate, set `TCP_NODELAY` and then
enter the existing TLS/TDS login path. A routing response always opens the
server-supplied host and port directly, so it cannot trigger a second browser
lookup.

Focused and cumulative Rust evidence:

| Gate | Result |
|---|---:|
| Tiberius focused Tokio SQL Browser suite | 14/14 PASS |
| complete vendored Tiberius library with `sql-browser-tokio` | 184/184 PASS |
| root FastMssql Rust tests | 122/122 PASS |
| vendored format and audited Clippy allowlist | PASS |
| root format and Clippy with warnings denied | PASS |
| local RustSec, 1,173 advisories / 220 locked dependencies | zero vulnerability or policy warning |

## Canonical `NINST` ownership

Each required ID has exactly one owner in
[SQL_AUTH_TEST_MATRIX.md](../SQL_AUTH_TEST_MATRIX.md). Rows `NINST-019`,
`NINST-020` and `NINST-021` own mandatory-evidence contracts; the separate
wheel, hosted Windows and privacy evidence below is the direct runtime proof.

| ID | Canonical owner | Direct evidence |
|---|---|---|
| `NINST-001` | `test_pooled_named_instance_observes_exact_request_and_queries_real_sql` | exact NUL-terminated request observed before real SQL query |
| `NINST-002` | `test_instance_name_wire_limits_are_rejected_before_udp` | empty/NUL/>32-byte names rejected before UDP |
| `NINST-003` | `test_pooled_named_instance_observes_exact_request_and_queries_real_sql` | bounded valid response selects advertised TCP port |
| `NINST-004` | `test_malformed_browser_responses_fail_without_escape_or_panic` | truncated/type/length/oversize matrix fails without panic |
| `NINST-005` | `test_invalid_or_duplicate_tcp_fields_are_rejected` | missing/empty/nonnumeric/range/duplicate TCP rejected |
| `NINST-006` | `test_wrong_source_response_cannot_select_a_tcp_target` | wrong-source response cannot select its TCP target |
| `NINST-007` | `test_silent_browser_has_a_bounded_inner_failure` | silent browser terminates under the protocol timer |
| `NINST-008` | `test_refused_discovered_tcp_is_structured_and_preserves_detail` | refused discovered port remains meaningful |
| `NINST-009` | `test_direct_no_instance_sql_path_remains_unchanged` | direct SQL-auth path remains functional |
| `NINST-010` | `test_pooled_named_instance_observes_exact_request_and_queries_real_sql` | pooled constructor discovers/authenticates/queries |
| `NINST-011` | `test_ado_named_instance_string_discovers_and_queries` | ADO.NET named target discovers/authenticates/queries |
| `NINST-012` | `test_direct_transaction_discovers_and_settles` | direct transaction discovers, writes and settles |
| `NINST-013` | `test_explicit_port_bypasses_sql_browser_even_with_instance_metadata` | explicit port connects with zero browser requests |
| `NINST-014` | `test_shorter_outer_connect_deadline_wins_over_browser_timeout` | absolute FastMssql connect deadline wins |
| `NINST-015` | `test_cancelled_discovery_leaves_connection_recoverable` | cancelled attempt settles and object reconnects |
| `NINST-016` | `test_refused_discovered_tcp_is_structured_and_preserves_detail` | `SqlConnectionError`, stable discovery-stage metadata |
| `NINST-017` | `test_concurrent_pool_creation_discovers_per_physical_connection_only` | discovery and sessions remain pool-bounded |
| `NINST-018` | `test_required_named_instance_stress_artifact_is_fresh_and_bounded` | exact 1,000 and 99,999 profiles |
| `NINST-019` | `test_wheel_windows_and_privacy_gates_are_mandatory` | installed CPython 3.13 wheel tests and stress below |
| `NINST-020` | `test_wheel_windows_and_privacy_gates_are_mandatory` | genuine Windows `SQLEXPRESS` hosted job below |
| `NINST-021` | `test_wheel_windows_and_privacy_gates_are_mandatory` | four-secret tracked/artifact/wheel scan below |
| `NINST-022` | `test_named_instance_teardown_leaves_zero_sessions_and_transactions` | post-disconnect sessions/transactions are 0/0 |

## Docker SQL-auth and cumulative results

The deterministic loopback SSRP fixture advertised the real Docker SQL Server
TCP port without placing a port in the FastMssql configuration. This is a
protocol-faithful fixture in front of real SQL Server; it is not described as
a native Linux named instance.

| Lane | Result |
|---|---:|
| required SQL-auth matrix | 442/442 PASS |
| strict SQL-auth | 460/460 PASS |
| true-async | 16/16 PASS |
| framework compatibility | 36/36 PASS |
| resilience | 6/6 PASS |
| load | 14/14 PASS |
| original local regression | 1,263/1,263 PASS |
| runner exit-code artifacts | 23/23 equal to zero |

No required case failed, errored, skipped or remained not run.

### Named-instance load profiles

| Profile | Operations | Browser / physical | Pool / sessions | Failed / timeout | RSS growth | Max loop gap | Duration | Throughput |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| required | 1,000 | 8 / 8 | 8 / 8 | 0 / 0 | 22,986,752 B | 0.005689 s | 0.324664 s | 3,080.11 op/s |
| extended | 99,999 | 8 / 8 | 8 / 8 | 0 / 0 | 42,582,016 B | 0.005939 s | 20.382916 s | 4,906.02 op/s |

Both profiles used 32 long-lived workers and a queue capacity of 64, had zero
missing or duplicate operation IDs, passed the post-load smoke query, stayed
below the 128 MiB RSS and 0.1-second loop-gap limits, and ended with zero
candidate sessions and transactions.

## Installed-wheel evidence

| Property | Exact result |
|---|---|
| wheel | `fastmssql-0.7.7-cp311-abi3-macosx_11_0_arm64.whl` |
| SHA-256 | `c9b1e6b8705182e2a1e5dffe6c30645a10ae4d768683ce88937c24737dbfc227` |
| archive integrity | `unzip -t`: no errors |
| interpreter | CPython 3.13.14 |
| package origin | external temporary `site-packages/fastmssql/__init__.py` |
| native origin | external temporary `site-packages/fastmssql/fastmssql.abi3.so` |
| dependency integrity | 11 packages compatible; `pip check` clean |
| offline named-instance contracts | 10/10 PASS |
| real Docker SQL-auth named-instance tests | 26/26 PASS |

All import/test commands unset `PYTHONPATH` and ambient `VIRTUAL_ENV`.

The installed-wheel 1,000-operation profile also passed:

- 1,000/1,000 succeeded, zero failure or timeout;
- 8 browser requests and 8 successful physical connections;
- maximum pool/session/in-flight counts 8/8/32;
- RSS growth 22,020,096 bytes;
- maximum event-loop gap 0.005513 seconds;
- 0.270433 seconds, 3,697.77 operations/second;
- zero missing/duplicate IDs, post-load smoke PASS and teardown 0/0.

## Hosted exact-SHA gates

All runs below target
`5728421a3941ce3ca957c5497bc53a78d5553b30`.

| Workflow | Run | Job(s) | Current result |
|---|---:|---|---|
| Dependency security | [30522520267](https://github.com/galeamarcel/FastMssql/actions/runs/30522520267) | `90805897320` RustSec audit | SUCCESS |
| cross-platform Rust/wheel | [30522520458](https://github.com/galeamarcel/FastMssql/actions/runs/30522520458) | `90805898779` Ubuntu, `90805898852` macOS, `90805898762` Windows | SUCCESS / SUCCESS / SUCCESS |
| genuine Windows named instance | [30522520410](https://github.com/galeamarcel/FastMssql/actions/runs/30522520410) | `90805897798` SQL Browser and installed wheel | SUCCESS |

The Windows lane is first-party and read-only. It installs Microsoft SQL
Server Express as `SQLEXPRESS`, enables TCP, starts the real SQL Server Browser
service, authenticates with SQL authentication and passes no instance TCP
port to FastMssql. Its downloaded schema-1 artifact reports
`instance="SQLEXPRESS"`, a successful parameterized pooled query, a successful
direct transaction and zero residual sessions.

Every cross-platform job passed raw Cargo build, root Rust tests, vendored
Tiberius tests, wheel build/install, installed dependency integrity and the
installed Python contract selection.

## Privacy, teardown and evidence integrity

- Four nonempty SQL-auth secret values were searched without printing them
  across tracked sources, canonical artifacts, the extracted wheel and the
  isolated-wheel result directory: no match.
- No credentials, connection strings, raw environment contents, wheel,
  `.env`, target tree or cache are tracked.
- Required and extended Docker profiles and the isolated-wheel profile report
  sessions/transactions after disconnect as `0/0`.
- The generator bug that previously rendered integer zero as an empty
  Markdown cell was reproduced on `a6409f6` and fixed on `17a3438`. The
  canonical report now preserves both teardown zeroes explicitly.
- A manual load-lane invocation that omitted the SQL-auth environment failed
  in fixture setup and was discarded as non-evidence. The complete lane was
  rerun with the canonical environment and replaced it with 14/14 PASS before
  the final report was generated with `--require-complete`.
- Subset installed-wheel results use a dedicated path and never overwrite the
  canonical `strict-results.json`.

## Knowledge-graph review

The graph was rebuilt on the exact candidate:

```text
branch              verify/named-instance
built_at_sha         5728421a3941ce3ca957c5497bc53a78d5553b30
head_matches_build   true
supported files      190
nodes                4,289
edges                52,330
```

Change detection from `30f9750` reports 33 files, 140 changed code/test nodes,
73 affected flows and high review risk. Static `tests_for` cannot see:

- same-module Rust `use super` tests for private connection helpers;
- PyO3 and shell/subprocess calls;
- the end-to-end generator test that invokes `generate_report.py` as a CLI.

These are explained graph boundaries, not missing executable evidence.
Classifier/transport behavior has same-file Rust tests plus offline and real
SQL-auth public-path tests. The report serializer has a witnessed RED/GREEN
CLI test and the regenerated canonical report. Stress paths are exercised by
the canonical runner and `NINST-018`.

## Residual risks and excluded scope

- SSRP is unauthenticated UDP. Connected-peer filtering and strict parsing
  reduce spoofing exposure but cannot authenticate SQL Browser; normal TLS
  certificate validation remains the final server-identity boundary.
- SQL Browser must be running and UDP `1434` must be reachable. Production
  environments that prohibit it should configure a static explicit TCP port.
- Discovery is intentionally per new physical connection; there is no stale
  global/pool port cache. Persistent pooling prevents discovery per logical
  SQL operation.
- The Docker fixture is not a genuine named instance. Genuine named-instance
  proof belongs only to the hosted Windows SQL Express lane.
- This feature validates SQL authentication, not Windows integrated or Azure
  authentication.
- Instance enumeration, named pipes, DAC discovery, automatic fallback to
  `1433`, query retry, TDS 8, HA/multi-subnet routing and additional enterprise
  SQL types remain outside this feature.
- Feature 21 must still validate real production Uvicorn/Gunicorn process
  models from an installed wheel. Existing in-process framework tests do not
  close that item.

## Exact commands

Representative commands used for the final candidate:

```text
scripts/sql_auth/run_all.sh
FASTMSSQL_NAMED_INSTANCE_OPERATIONS=99999 \
  FASTMSSQL_NAMED_INSTANCE_ALLOW_EXTENDED=1 \
  scripts/sql_auth/run_named_instance_stress.sh
cargo test --locked
cargo test --manifest-path vendor/tiberius/Cargo.toml \
  --no-default-features \
  --features chrono,tds73,rustls,sql-browser-tokio --lib
scripts/security/audit_dependencies.sh
uvx code-review-graph build
```

Commands that load `.env.sql-auth.local` are recorded only by name; values and
raw environment output are deliberately excluded.
