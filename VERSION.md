# Version status

Current displayed package version: `0.7.7`.

## 0.8.0 — unreleased candidate

The candidate version follows the repository convention that the middle
component represents a feature-level patch. It is documented here but is not
yet applied to package metadata; release versioning remains a separate,
explicit decision.

Changes currently prepared on `feat/typed-parameter-descriptor`:

- closed, validated SQL parameter descriptors with exact TDS metadata;
- exact decimal, UUID, temporal, ANSI/Unicode, binary, XML, and typed-null
  input handling;
- TDS 7.4 UTF-8 feature negotiation and `_UTF8` collation support;
- structured, privacy-safe conversion failures shared by connection,
  transaction, and batch paths;
- local rejection when temporal rounding would cross into year 10000;
- deterministic SQL-auth, exact-wire, compatibility, and 1,000-operation
  concurrent load coverage;
- load-metric contract coverage for the typed-parameter case `PARAM-033`.
- regenerated SQL-auth matrix/report evidence tied to technical commit
  `1909a72cb83fd8f36638a6aa99072b3a99045ed8` (346/346 required cases pass).

No release, package-version change, or artifact publication has occurred.
