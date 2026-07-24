# FastMssql SQL-auth validation report

## Scope

- Authentication under test: SQL Server username/password only.
- Azure authentication and Windows authentication are explicitly excluded.
- SQL Server target: isolated Developer Edition container.
- ARM64 hosts may execute the `linux/amd64` image under emulation; timings are diagnostic rather than marketing benchmarks.

## Environment

- Python: `3.13.14`
- Platform: `macOS-26.5-arm64-arm-64bit-Mach-O`
- Machine: `arm64`
- Git commit: `00dfda3b9656ab61d1ff08852549b2573bbe2704`

## Matrix outcomes

| Status | Count |
|---|---:|
| PASS | 0 |
| FAIL | 0 |
| ERROR | 0 |
| SKIPPED | 0 |
| NOT RUN | 226 |

## Execution lanes

| Lane | Exit code | Tests | Failures | Errors | Skipped | Command |
|---|---:|---:|---:|---:|---:|---|
| none | NOT RUN | 0 | 0 | 0 | 0 | |

## Failures and errors

None recorded.
