# FastMssql local Tiberius patch

This directory is the minimal build source subset of the published `tiberius`
crate version `0.12.3`, whose registry source records upstream commit
`c34fab2e14c52ab74519d073d7a7b65bd023fc1a`.

FastMssql temporarily carries six implemented narrowly scoped patch sets.
The `test/tiberius-bulk-column-subset` RED branch also records the required
contract for a seventh patch; the method is intentionally absent until the
descendant feature branch observes these tests fail.

The TLS dependency migration includes:

- `tokio-rustls` 0.24 to 0.26;
- `rustls-native-certs` 0.6 to 0.8;
- removal of the unmaintained `rustls-pemfile`;
- adaptation of `rustls_tls_stream.rs` to Rustls 0.23.

The adapted TLS stream is taken byte-for-byte from technical commit
`d46e4c028e5b55cbd362506f24b5ef5fe645c5d5` in
[prisma/tiberius PR #419](https://github.com/prisma/tiberius/pull/419).
That commit resolves `RUSTSEC-2026-0098`, `RUSTSEC-2026-0099`, and
`RUSTSEC-2026-0104`.

The connection-pool safety patch adds protocol-level session reset support:

- the first packet of a Batch or RPC reset request carries the MS-TDS
  `RESETCONNECTION` status bit;
- a one-packet reset request combines `RESETCONNECTION` and EOM as `0x09`;
- the client-side transaction descriptor and cached metadata are cleared at
  the reset boundary;
- `Client::reset_connection_on_next_request()` arms the next application
  request, so reset adds no extra network round-trip;
- that request is prefixed with `SET TRANSACTION ISOLATION LEVEL READ
  COMMITTED`, because
  [MS-TDS section 2.2.3.1.2](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/ce398f9a-7d47-4ede-8f36-9dd6fc21ca43)
  explicitly excludes transaction isolation level from `RESETCONNECTION`.

The SQL numeric compatibility patch:

- accepts SQL Server's complete scale range from 0 through 38;
- derives precision as the maximum of coefficient digits, scale, and one;
- avoids overstating sub-unit and zero values such as `1E-38` as precision
  39;
- covers the `DECIMAL(38,38)` wire payload and real SQL Server decoding path.

The typed RPC parameter patch:

- adds a closed, validated `SqlParameterType` representation; raw SQL type
  text is never accepted by the TDS layer;
- carries optional explicit declarations and TDS `TYPE_INFO` beside each
  parameter while leaving the existing inferred `ToSql` path unchanged;
- encodes integer width, float storage, precision/scale, character and binary
  length, temporal scale, UUID, XML, and typed NULL metadata exactly;
- emits `DATEN` `TYPE_INFO` without a duplicate value-length byte, keeping
  every following RPC field aligned;
- serializes exact numeric payloads using the storage width declared by
  `DECIMAL(p,s)` or `NUMERIC(p,s)`, including zero-padding narrower values;
- treats the `0xffff` character/binary length as the TDS PLP `MAX` sentinel
  instead of an application payload ceiling;
- checks typed character/binary and XML PLP chunk counters before narrowing
  them to the 32-bit TDS wire length;
- encodes empty XML as one PLP terminator rather than an empty chunk followed
  by a second terminator that would shift the next RPC parameter;
- tracks SQL Server's negotiated collation through `ENVCHANGE` tokens and
  captures the initial LOGIN7 collation; a connection-pool reset restores
  that baseline before deriving the reset request's ANSI parameter metadata;
- advertises TDS 7.4 `UTF8_SUPPORT`, validates its `FEATUREEXTACK`, preserves
  the negotiated capability across pool reset, and honors the collation
  `fUTF8` flag for `_UTF8` database collations;
- converts missing collation, incompatible metadata, and scale mismatches
  into driver errors instead of `unwrap`, `todo!`, or malformed wire data.
- marks a response pending only after the complete request payload encodes,
  preserving connection synchronization after a local parameter error.

The token-decoder safety patch:

- recognizes TABNAME and COLINFO browse metadata emitted by `FOR BROWSE`;
- consumes each USHORT-length payload exactly without retaining base-table
  names or logging payload bytes;
- replaces wildcard token-dispatch panics with an exhaustive match;
- returns typed protocol errors for unsupported UDT and SQL_VARIANT metadata
  instead of reaching `todo!` or unwinding a runtime worker;
- lets the FastMssql boundary normalize those controlled typed errors to its
  stable public metadata-decoding `ProtocolError`;
- runs Clippy with every non-baseline warning denied while explicitly listing
  the legacy lint categories already emitted by unchanged Tiberius 0.12.3 on
  the pinned Rust 1.94 toolchain;
- deliberately does not claim UDT or SQL_VARIANT value conversion support.

The complete-response and direct-RPC patch:

- adds an owned `ResponseStream` that preserves result metadata, rows,
  DONE/DONEPROC/DONEINPROC state, informational messages, signed return
  status and output parameter values in wire order;
- keeps the existing `QueryStream` API as a metadata/row-only compatibility
  adapter over the complete response stream;
- preserves nullability, precision, scale and declared character/binary
  capacities, including UTF-16 units and MAX metadata;
- resolves nullable `MONEYN(4)` and `DATETIMEN(4)` metadata to SMALLMONEY and
  SMALLDATETIME instead of their wider types;
- implements checked MS-TDS US_VARCHAR procedure-name and B_VARCHAR
  parameter-name encoding without panic paths;
- supports direct named RPC input/output parameters and restores READ
  COMMITTED with a fully consumed reset-bearing batch before the first RPC on
  a recycled session;
- makes token traces and connection diagnostics structural, with no SQL
  Server message, metadata name, row/output value, environment value,
  procedure name or raw-buffer payload.

The pending bulk-column-subset contract requires:

- an additive `Client::bulk_insert_columns(table, columns)` method while
  preserving `Client::bulk_insert(table)`;
- closed validation and bracket quoting for raw table and column identifiers;
- exact requested metadata count and order;
- typed rejection for identity, computed, rowversion and unsupported
  metadata;
- a total checked `INSERT BULK` declaration formatter with no panic path;
- direct SQL-auth coverage for subsets, defaults, NULLs, hostile identifier
  characters and post-rejection connection recovery.

This RED branch does not claim that the primitive is implemented.

No Tiberius fork has been created or published by the FastMssql fork owner.
The path dependency keeps the reviewed source inside the FastMssql repository
and makes builds independent of the contributor fork remaining available.
The registry-only `Cargo.toml.orig` file is intentionally omitted because
Cargo reserves that name when `maturin` packages a local path dependency; the
effective normalized manifest is retained as `Cargo.toml`.
Upstream examples, CI files, Docker fixtures, and test certificate keys are
omitted because they are not part of the FastMssql runtime dependency.
FastMssql-specific unit and SQL-auth integration contracts are retained under
`tests/` to make each local protocol patch independently reproducible.

Remove this directory and return to a crates.io dependency after an equivalent
Tiberius release is published and passes the full FastMssql SQL-auth matrix.
The original MIT and Apache-2.0 license files are retained beside this notice.
