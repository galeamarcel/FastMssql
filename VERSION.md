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
