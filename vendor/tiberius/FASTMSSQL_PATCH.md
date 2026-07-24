# FastMssql local Tiberius patch

This directory is the minimal build source subset of the published `tiberius`
crate version `0.12.3`, whose registry source records upstream commit
`c34fab2e14c52ab74519d073d7a7b65bd023fc1a`.

FastMssql temporarily carries one narrowly scoped TLS dependency migration:

- `tokio-rustls` 0.24 to 0.26;
- `rustls-native-certs` 0.6 to 0.8;
- removal of the unmaintained `rustls-pemfile`;
- adaptation of `rustls_tls_stream.rs` to Rustls 0.23.

The adapted TLS stream is taken byte-for-byte from technical commit
`d46e4c028e5b55cbd362506f24b5ef5fe645c5d5` in
[prisma/tiberius PR #419](https://github.com/prisma/tiberius/pull/419).
That commit resolves `RUSTSEC-2026-0098`, `RUSTSEC-2026-0099`, and
`RUSTSEC-2026-0104`.

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
