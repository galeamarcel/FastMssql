# FastMssql SQL-auth result-stream stress report

## Scope

This report validates FastMssql's bounded asynchronous result-stream path
against a real SQL Server using username/password authentication. It measures
driver, pool and Python-process behavior; it is not a universal SQL Server
capacity benchmark.

The target was the dedicated SQL Server 2022 Developer Edition container on an
ARM64 macOS host. The `linux/amd64` SQL Server image ran under emulation, so
throughput and latency are diagnostic rather than production sizing claims.

All profiles below were generated from the exact technical source SHA
`a9d5c2ab42de0f03051c771bb8942ee15dfe6e28` with FastMssql `0.7.7`,
Python `3.13.14`, 10 logical host CPUs and 32 GiB host memory.

## Boundedness contract

The harness uses a fixed set of long-lived Python workers and a bounded work
queue. Each SQL response uses the public `ResultStream` API with a bounded
event buffer and consumer acknowledgements. It therefore does not allocate
one Python task per operation and does not permit the TDS producer to release
the lease before Python has converted or discarded every delivered event.

The current bound is by response-event count. An individual row or LOB value
is not byte-chunked, so callers must still size result shapes and LOB values
appropriately. The 128 MiB RSS-growth gate is an independent process-level
safety check, not a claim that every stream consumes a fixed number of bytes.

## Required gate

The required `RESULT-029` profile ran through the complete SQL-auth
orchestrator with pool size 8, result-event buffer 8, 64 long-lived workers
and a bounded work queue of 128 items.

| Operations | Concurrency | Success | Failure / timeout | Seconds | Ops/s | Peak SQL SPIDs | Peak pool / pending | RSS growth | Event-loop ticks | Post-load smoke |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1,000 | 64 | 1,000 | 0 / 0 | 0.3163 | 3,161.56 | 8 | 8 / 62 | 17,743,872 B | 60 | PASS |

Latency used the nearest-rank method:

| Boundary | p50 | p95 | p99 | max |
|---|---:|---:|---:|---:|
| Admitted driver latency | 17.451 ms | 30.922 ms | 33.462 ms | 47.531 ms |
| Scheduled end-to-end latency | 60.788 ms | 68.301 ms | 76.558 ms | 80.682 ms |

The pool recorded exactly 1,000 checkout starts, zero checkout timeouts,
14 direct checkouts and 986 waited checkouts. Active connections returned to
zero before the post-load smoke query.

## Extended profiles

The opt-in extended run used pool size 32 and result-event buffer 16. It kept
the same fixed-worker/bounded-queue model and the same 128 MiB RSS-growth
limit.

| Operations | Concurrency | Success | Failure / timeout | Seconds | Ops/s | Peak SQL SPIDs | Peak pool / pending | RSS growth | Event-loop ticks | Post-load smoke |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10,000 | 128 | 10,000 | 0 / 0 | 2.3000 | 4,347.88 | 32 | 32 / 127 | 36,044,800 B | 407 | PASS |
| 99,999 | 200 | 99,999 | 0 / 0 | 22.9050 | 4,365.82 | 32 | 32 / 198 | 31,834,112 B | 4,029 | PASS |

| Profile | Boundary | p50 | p95 | p99 | max |
|---|---|---:|---:|---:|---:|
| 10,000 / 128 | Admitted driver | 28.189 ms | 44.043 ms | 56.742 ms | 101.603 ms |
| 10,000 / 128 | Scheduled end-to-end | 84.377 ms | 103.401 ms | 116.152 ms | 170.684 ms |
| 99,999 / 200 | Admitted driver | 44.363 ms | 74.627 ms | 89.447 ms | 218.813 ms |
| 99,999 / 200 | Scheduled end-to-end | 132.962 ms | 164.458 ms | 186.784 ms | 320.773 ms |

The 10,000-operation profile recorded 653 direct and 9,347 waited checkouts.
The 99,999-operation profile recorded 6,574 direct and 93,425 waited
checkouts. Both totals reconcile exactly with their operation count, neither
profile timed out, and both returned to zero active pool connections.

## Exactness and recovery evidence

Every profile validated:

- exact completed-ID count, minimum, maximum, arithmetic sum and SHA-256;
- no missing or duplicate operation IDs;
- zero driver failures, timeouts or recorded invariant violations;
- SQL concurrency never above `pool.max_size`;
- continued event-loop progress and a finite maximum scheduling gap;
- RSS growth below 134,217,728 bytes;
- zero active connections after quiescence;
- a successful query after the load.

The required profile's completed-ID digest is
`8db91b2ee25d579493dbc2ca66417cc945e215b5424349884013834d43df7ac4`.
The 10,000 and 99,999 digests are respectively
`a658f34417004048e470697bf202006272fd1e2f99bf3b9051a56fbef15a586c`
and
`af203b9010c6eaf4cd9bf5240b2d87b3486caedb505f1d4fad3cbe8f102039e9`.

## Reproduction

The required profile is part of:

```text
scripts/sql_auth/run_all.sh
```

The extended profiles use:

```text
FASTMSSQL_RESULT_STREAM_STRESS_PROFILES=10000:128,99999:200 \
FASTMSSQL_RESULT_STREAM_STRESS_POOL_SIZE=32 \
FASTMSSQL_RESULT_STREAM_STRESS_BUFFER_SIZE=16 \
FASTMSSQL_RESULT_STREAM_STRESS_METRICS_PATH=.artifacts/sql-auth/result-stream-stress-extended-metrics.json \
scripts/sql_auth/run_result_stream_stress.sh
```

The machine-readable artifacts are deliberately ignored workspace evidence;
the committed SQL-auth matrix, main report and this report preserve the
verified SHA, configuration, outcomes and diagnostic metrics without
credentials or SQL payloads.
