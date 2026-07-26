# FastMssql local Tiberius patch

This directory is the minimal build source subset of the published `tiberius`
crate version `0.12.3`, whose registry source records upstream commit
`c34fab2e14c52ab74519d073d7a7b65bd023fc1a`.

FastMssql temporarily carries three narrowly scoped patch sets.

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

No Tiberius fork has been created or published by the FastMssql fork owner.
The path dependency keeps the reviewed source inside the FastMssql repository
and makes builds independent of the contributor fork remaining available.
The registry-only `Cargo.toml.orig` file is intentionally omitted because
Cargo reserves that name when `maturin` packages a local path dependency; the
effective normalized manifest is retained as `Cargo.toml`.
Examples, tests, CI files, Docker fixtures, and test certificate keys are also
omitted because they are not part of the FastMssql runtime dependency.

Remove this directory and return to a crates.io dependency after an equivalent
Tiberius release is published and passes the full FastMssql SQL-auth matrix.
The original MIT and Apache-2.0 license files are retained beside this notice.
