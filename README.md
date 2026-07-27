# FastMSSQL ⚡

FastMSSQL is an async Python library for Microsoft SQL Server (MSSQL), built in Rust.
Unlike standard libaries, it uses a native SQL Server client—no ODBC required—simplifying installation on Windows, macOS, and Linux.
Great for data ingestion, bulk inserts, and large-scale query workloads.

[![Python Versions](https://img.shields.io/pypi/pyversions/fastmssql)](https://pypi.org/project/fastmssql/)

[![License](https://img.shields.io/badge/license-MIT%20-green)](LICENSE)

[![Unit Tests](https://github.com/Rivendael/fastmssql/actions/workflows/unittests.yml/badge.svg)](https://github.com/Rivendael/fastmssql/actions/workflows/unittests.yml)

[![Latest Release](https://img.shields.io/github/v/release/Rivendael/fastmssql)](https://github.com/Rivendael/fastmssql/releases)

[![Platform](https://img.shields.io/badge/platform-Windows%20|%20Linux%20|%20macOS-lightgrey)](https://github.com/Rivendael/fastmssql)

[![Rust Backend](https://img.shields.io/badge/backend-rust-orange)](https://github.com/Rivendael/pymssql-rs)

## Features

- High performance: optimized for very high RPS and low overhead
- Rust core: memory‑safe and reliable, tuned Tokio runtime
- No ODBC: native SQL Server client, no external drivers needed
- Azure authentication: Service Principal, Managed Identity, and access token support (**BETA**)
- Connection pooling: bb8‑based, smart defaults (default max_size=15, min_idle=3)
- Async first: clean async/await API with `async with` context managers
- Strong typing: fast conversions for common SQL Server types
- Thread‑safe: safe to use in concurrent apps
- Cross‑platform: Windows, macOS, Linux
- Batch operations: high-performance bulk inserts and batch query execution

## Installation

### From PyPI (recommended)

```bash
pip install fastmssql
```

### Prerequisites

- Python 3.11 to 3.14
- Microsoft SQL Server (any recent version)

## Quick start

### Basic async usage

```python
import asyncio
from fastmssql import Connection

async def main():
    conn_str = "Server=localhost;Database=master;User Id=myuser;Password=mypass"
    async with Connection(conn_str) as conn:
        # SELECT: use query() -> rows()
        result = await conn.query("SELECT @@VERSION as version")
        for row in result.rows():
            print(row['version'])

        stats = await conn.pool_stats()
        print(
            "Pool: "
            f"connected={stats['connected']}, "
            f"size={stats['connections']}/{stats['max_size']}, "
            f"idle={stats['idle_connections']}, "
            f"min_idle={stats['min_idle']}, "
            f"pending={stats['pending_gets']}, "
            f"wait_s={stats['get_wait_time_seconds']:.6f}, "
            f"closed_broken={stats['connections_closed_broken']}"
        )

asyncio.run(main())
```

### Bounded true-async result streaming

Use `stream()` for a parameterized statement and `batch()` for an
unparameterized multi-statement batch. On `Connection`, both retain one pooled
physical connection until the complete SQL Server response is consumed,
finished, or closed. The same methods are available on `Transaction` and keep
its session mutex and physical connection for the full response.
`buffer_size` bounds unacknowledged result events; individual rows and LOB
values are not byte-chunked.

```python
async with Connection(conn_str) as conn:
    response = await conn.stream(
        """
        SELECT id, payload
        FROM dbo.events
        WHERE tenant_id = @P1
        ORDER BY id
        """,
        [tenant_id],
        buffer_size=64,
    )

    async with response:
        async for result_set in response:
            print(result_set.index, result_set.column_names)
            async for row in result_set:
                await handle(row)

    summary = response.summary
    print(summary.result_set_count, summary.done, summary.messages)
```

`ResultStream` and each nested `ResultSet` are async-only. Call
`await result_set.aclose()` to skip the remainder of one set while retaining
later sets, `await response.finish()` to discard remaining rows and obtain the
terminal summary, or `await response.aclose()` to abort the full response.
Explicit full-response close and abandonment are fail-closed: FastMssql waits
for resource-release acknowledgement or retires the uncertain physical
connection. Cancelling a receive before it owns an event leaves that event
available for a later receive; cancellation after ownership conservatively
aborts the full response so no event can be silently lost.

Terminal SQL, protocol, and conversion errors exposed by a result stream carry
`operation`, `phase`, `retryable`, `wire_sent`, `connection_discarded`, and
`outcome_unknown` metadata. A fully drained nonfatal SQL Server error can keep
`connection_discarded=False`; conversion or protocol uncertainty retires the
connection. FastMssql never retries a response automatically.

### Direct stored-procedure RPC

Use `callproc()` for a stored procedure that has output parameters or a return
status. FastMssql sends the validated procedure name as a native TDS RPC; it
does not build an `EXEC` SQL string. `callproc()` is available on both
`Connection` and a begun `Transaction`.

```python
from fastmssql import Parameter, Parameters

params = Parameters(
    order_id=Parameter(42, "INT"),
    new_total=Parameter(
        None,
        "DECIMAL(19,4)",
        direction="OUTPUT",
    ),
    status=Parameter(
        None,
        direction="RETURN_VALUE",
    ),
)

response = await conn.callproc(
    "dbo.reprice_order",
    params,
    buffer_size=64,
)

async with response:
    async for result_set in response:
        async for row in result_set:
            await handle(row)

summary = response.summary
print(summary.return_status)
print(summary.output_parameters["new_total"])
print(summary.output_parameters["status"])
```

Named parameters may include one leading `@`; output dictionary keys omit it.
Positional output keys are their original zero-based descriptor indices.
`INPUT` may infer its SQL type. `OUTPUT` and `INPUT_OUTPUT` require an explicit
SQL type, while `RETURN_VALUE` accepts only an omitted type or `INT` and is not
sent as an RPC argument. Output values and the return status become available
only after normal terminal completion. Invalid procedure names, parameter
names, direction combinations, counts, and buffer sizes are rejected locally
before pool checkout.

The legacy `query()` and `simple_query()` methods remain synchronous-iteration
compatibility APIs after awaiting them; they buffer the first result set.

### Pool statistics

`await connection.pool_stats()` is a pull-only snapshot of the current pool.
It performs no SQL, does not create a pool, does not log or export anything,
and contains only fixed low-cardinality scalar values:

- `connected`: whether the `Connection` currently owns a pool handle.
- `connections`: managed physical connections.
- `idle_connections`: managed connections currently idle.
- `active_connections`: managed connections currently leased.
- `max_size`: configured pool maximum.
- `min_idle`: configured minimum idle value or `None`.
- `get_started`: checkout attempts started.
- `get_direct`: successful checkouts that did not wait.
- `get_waited`: successful checkouts that waited.
- `get_timed_out`: checkouts that reached the acquisition timeout.
- `pending_gets`: checkouts currently outstanding.
- `get_wait_time_seconds`: cumulative checkout wait time in seconds, not an
  average or percentile.
- `connections_created`: physical connections created.
- `connections_closed_broken`: connections retired as broken.
- `connections_closed_invalid`: connections retired after validation failed.
- `connections_closed_max_lifetime`: connections retired at maximum lifetime.
- `connections_closed_idle_timeout`: connections retired by idle timeout.

Checkout counters include every shared-pool acquisition, including readiness
checks. Repeating `connect(validate=True)` reuses the same pool but counts one
new readiness checkout; `ping()` also counts a checkout.
`connect(validate=False)` and `pool_stats()` do not acquire a connection.

The four `connections_closed_*` values are bb8 retirement-event counters, not
mutually exclusive close reasons. For example, a failed checkout validation
can increment both `connections_closed_invalid` and
`connections_closed_broken` when the same transport is also unsafe. Do not sum
them to calculate a count of unique physical connections closed.

Every snapshot satisfies:

```text
active_connections == connections - idle_connections
get_started == get_direct + get_waited + get_timed_out + pending_gets
```

Counters are monotonic only within the current concrete pool epoch. They reset
after `disconnect()` removes that pool and a reconnect creates a new one.
Applications that need process-lifetime totals should scrape before shutdown
and let their monitoring system handle counter resets.

These values never include SQL, parameters, connection strings, credentials,
server/database/login/application identifiers or arbitrary labels. The method
describes shared-pool operations only; direct `execute_batch()` and direct
`Transaction(...)` sockets are intentionally excluded.

### Operation duration and outcome metrics

Operation metrics are opt-in and belong to one logical `Connection` for its
entire connection lifetime:

```python
from fastmssql import Connection, OperationMetricsConfig

metrics = OperationMetricsConfig(enabled=True)
connection = Connection(
    "Server=localhost;Database=app;User Id=myuser;Password=mypass",
    operation_metrics_config=metrics,
)

await connection.connect()
await connection.query("SELECT @P1", [42])

stats = await connection.operation_stats()
query = stats["operations"]["query"]
print(query["succeeded"], query["timed_out"])
```

Metrics are disabled by default. A disabled connection allocates no metrics
registry and its normal operations perform no metrics clock read or atomic
update. `operation_stats()` remains available and returns the fixed schema
with `enabled=False` and zero values.

The registry has exactly 13 operation series:

```text
connect, ping, query, simple_query, execute, query_batch, execute_batch,
bulk_insert, begin, commit, rollback, close, disconnect
```

Each completed call increments exactly one mutually exclusive outcome.
Classification priority is `succeeded`, then `outcome_unknown` for
`CommitOutcomeUnknown`, then `timed_out` for the typed operation/shutdown
timeout exceptions, then `errors` for every other returned exception.
`cancelled` is used only when a started Rust future is dropped without
returning a result. Causes are not double-counted.

Durations cover the end-to-end Rust async body: lazy connection creation, pool
acquisition, SQL Server I/O, response consumption, and Rust-side result
construction. They do not include Python task-queue time before the Rust future
is first polled. The histogram has 17 fixed finite bounds from 100 microseconds
through 30 seconds; cumulative counts are returned in
`duration_seconds_buckets`. `completed` is the implicit positive-infinity
(`+Inf`) bucket, so calls longer than 30 seconds still complete without
incrementing a larger finite bucket.

Snapshots are fresh dictionaries and lists. During concurrent updates they
are weakly consistent but always preserve their documented arithmetic
invariants; after writers quiesce they are exact. Counters and duration sums
saturate at `u64::MAX` instead of wrapping, and the affected operation keeps
`saturated=True` permanently.

The registry survives `disconnect()` and later reconnect generations.
Transactions created by `connection.transaction()` aggregate into the owner's
same registry; the compatibility constructor `Transaction(...)` is excluded.
There is no reset API or runtime enable/disable toggle, avoiding ambiguous
epochs for operations already in flight.

This is a pull-only source: FastMssql installs no exporter, callback,
background task, SQL label, or global telemetry provider. Snapshots contain no
SQL, parameters, credentials, connection strings, or application-defined
labels. Applications may copy the fixed values into their own monitoring
stack.

Operation metrics do not change framework execution models. FastAPI and other
ASGI applications retain a persistent event loop. Flask `async def` under
WSGI still creates a loop per request; use an ASGI adapter when persistent-loop
behavior is required.

## Explicit Connection Management

`query()`, `execute()`, and the other data methods still initialize the pool
lazily when needed. Explicit `connect()` is strict by default: it returns only
after a complete `SELECT 1` round-trip through the shared pool.

Use `connect(validate=False)` only when allocating the pool lazily is
intentional. `is_connected()` reports whether the object owns a pool handle;
it performs no network I/O. Use `ping()` for current SQL Server readiness.

```python
import asyncio
from fastmssql import Connection

async def main():
    conn_str = "Server=localhost;Database=master;User Id=myuser;Password=mypass"
    conn = Connection(conn_str)

    await conn.connect()
    assert await conn.is_connected()  # pool handle exists
    assert await conn.ping()          # live SQL Server round-trip

    result = await conn.query("SELECT 42 as answer")
    print(result.rows()[0]["answer"])

    await conn.disconnect()
    assert not await conn.is_connected()

asyncio.run(main())
```

The intentional lazy-allocation path is explicit:

```python
await conn.connect(validate=False)
assert await conn.is_connected()  # pool handle only
await conn.ping()                 # first required readiness check
```

`async with Connection(...)` always validates SQL Server before entering the
context body.

### FastAPI startup and readiness

Use one shared connection for the application lifespan. Put startup inside the
`try` so a failed validation also drops the pool handle:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastmssql import Connection

conn_str = "Server=localhost;Database=app;User Id=myuser;Password=mypass"
database = Connection(conn_str)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await database.connect()  # strict SELECT 1 round-trip
        app.state.database = database
        yield
    finally:
        await database.disconnect()


app = FastAPI(lifespan=lifespan)


@app.get("/ready")
async def ready():
    await database.ping()
    return {"ready": True}
```

An unreachable SQL Server prevents lifespan startup. A readiness endpoint uses
`ping()`, not `is_connected()`. Flask `async def` under WSGI remains
functionally compatible but has per-request event-loop limits; Flask through
an ASGI adapter should use the same persistent startup/cleanup ownership
pattern.

## Usage

### Connection options

You can connect either with a connection string or individual parameters.

1) Connection string

```python
import asyncio
from fastmssql import Connection

async def main():
    conn_str = "Server=localhost;Database=master;User Id=myuser;Password=mypass"
    async with Connection(connection_string=conn_str) as conn:
        rows = (await conn.query("SELECT DB_NAME() as db")).rows()
        print(rows[0]['db'])

asyncio.run(main())
```

1) Individual parameters

```python
import asyncio
from fastmssql import Connection

async def main():
    async with Connection(
        server="localhost",
        database="master",
        username="myuser",
        password="mypassword"
    ) as conn:
        rows = (await conn.query("SELECT SUSER_SID() as sid")).rows()
        print(rows[0]['sid'])

asyncio.run(main())
```

Note: Windows authentication (Trusted Connection) is currently not supported. Use SQL authentication (username/password).

### Azure Authentication (BETA)

🧪 **This is a beta feature.** Azure authentication functionality is experimental and may change in future versions.

FastMSSSQL supports Azure Active Directory (AAD) authentication for Azure SQL Database and Azure SQL Managed Instance. You can authenticate using Service Principals, Managed Identity, or access tokens.

#### Service Principal Authentication

```python
import asyncio
from fastmssql import Connection, AzureCredential

async def main():
    # Create Azure credential using Service Principal
    azure_cred = AzureCredential.service_principal(
        client_id="your-client-id",
        client_secret="your-client-secret", 
        tenant_id="your-tenant-id"
    )
    
    async with Connection(
        server="yourserver.database.windows.net",
        database="yourdatabase",
        azure_credential=azure_cred
    ) as conn:
        result = await conn.query("SELECT GETDATE() as current_time")
        for row in result.rows():
            print(f"Connected! Current time: {row['current_time']}")

asyncio.run(main())
```

#### Managed Identity Authentication

For Azure resources (VMs, Function Apps, App Service, etc.):

```python
import asyncio
from fastmssql import Connection, AzureCredential

async def main():
    # System-assigned managed identity
    azure_cred = AzureCredential.managed_identity()
    
    # Or user-assigned managed identity
    # azure_cred = AzureCredential.managed_identity(client_id="user-assigned-identity-client-id")
    
    async with Connection(
        server="yourserver.database.windows.net",
        database="yourdatabase",
        azure_credential=azure_cred
    ) as conn:
        result = await conn.query("SELECT USER_NAME() as user_name")
        for row in result.rows():
            print(f"Connected as: {row['user_name']}")

asyncio.run(main())
```

#### Access Token Authentication

If you already have an access token from another Azure service:

```python
import asyncio
from fastmssql import Connection, AzureCredential

async def main():
    # Use a pre-obtained access token
    access_token = "your-access-token"
    azure_cred = AzureCredential.access_token(access_token)
    
    async with Connection(
        server="yourserver.database.windows.net",
        database="yourdatabase",
        azure_credential=azure_cred
    ) as conn:
        result = await conn.query("SELECT 1 as test")
        print("Connected with access token!")

asyncio.run(main())
```

#### Default Azure Credential

Uses the Azure credential chain (environment variables → managed identity → Azure CLI → Azure PowerShell):

```python
import asyncio
from fastmssql import Connection, AzureCredential

async def main():
    # Use default Azure credential chain
    azure_cred = AzureCredential.default()
    
    async with Connection(
        server="yourserver.database.windows.net",
        database="yourdatabase",
        azure_credential=azure_cred
    ) as conn:
        result = await conn.query("SELECT 1 as test")
        print("Connected with default credentials!")

asyncio.run(main())
```

**Prerequisites for Azure Authentication:**
- Azure SQL Database or Azure SQL Managed Instance
- Service Principal with appropriate SQL Database permissions
- For Managed Identity: Azure resource with managed identity enabled
- For Default credential: Azure CLI installed and authenticated (`az login`)

See [examples/azure_auth_example.py](examples/azure_auth_example.py) for comprehensive usage examples.

### Working with data

```python
import asyncio
from fastmssql import Connection

async def main():
    async with Connection("Server=.;Database=MyDB;User Id=sa;Password=StrongPwd;") as conn:
        # SELECT (returns rows)
        users = (await conn.query(
            "SELECT id, name, email FROM users WHERE active = 1"
        )).rows()
        for u in users:
            print(f"User {u['id']}: {u['name']} ({u['email']})")

        # INSERT / UPDATE / DELETE (returns affected row count)
        inserted = await conn.execute(
            "INSERT INTO users (name, email) VALUES (@P1, @P2)",
            ["Jane", "jane@example.com"],
        )
        print(f"Inserted {inserted} row(s)")

        updated = await conn.execute(
            "UPDATE users SET last_login = GETDATE() WHERE id = @P1",
            [123],
        )
        print(f"Updated {updated} row(s)")

asyncio.run(main())
```

Parameters use positional placeholders: `@P1`, `@P2`, ... Provide values as a list in the same order.

### Batch operations

For high-throughput scenarios, use batch methods to reduce network round-trips:

```python
import asyncio
from fastmssql import Connection

async def main_fetching():
    # Replace with your actual connection string
    async with Connection("Server=.;Database=MyDB;User Id=sa;Password=StrongPwd;") as conn:

        # --- 1. Prepare Data for Demonstration ---
        columns = ["name", "email", "age"]
        data_rows = [
            ["Alice Johnson", "alice@example.com", 28],
            ["Bob Smith", "bob@example.com", 32],
            ["Carol Davis", "carol@example.com", 25],
            ["David Lee", "david@example.com", 35],
            ["Eva Green", "eva@example.com", 29]
        ]
        await conn.bulk_insert("users", columns, data_rows)

        # --- 2. Execute Query and Retrieve the Result Object ---
        print("\n--- Result Object Fetching (fetchone, fetchmany, fetchall) ---")

        # The Result object is returned after the awaitable query executes.
        result = await conn.query("SELECT name, age FROM users ORDER BY age DESC")

        # fetchone(): Retrieves the next single row synchronously.
        oldest_user = result.fetchone()
        if oldest_user:
            print(f"1. fetchone: Oldest user is {oldest_user['name']} (Age: {oldest_user['age']})")

        # fetchmany(2): Retrieves the next set of rows synchronously.
        next_two_users = result.fetchmany(2)
        print(f"2. fetchmany: Retrieved {len(next_two_users)} users: {[r['name'] for r in next_two_users]}.")

        # fetchall(): Retrieves all remaining rows synchronously.
        remaining_users = result.fetchall()
        print(f"3. fetchall: Retrieved all {len(remaining_users)} remaining users: {[r['name'] for r in remaining_users]}.")

        # Exhaustion Check: Subsequent calls return None/[]
        print(f"4. Exhaustion Check (fetchone): {result.fetchone()}")
        print(f"5. Exhaustion Check (fetchmany): {result.fetchmany(1)}")

        # --- 3. Batch Commands for multiple operations ---
        print("\n--- Batch Commands (execute_batch) ---")
        commands = [
            ("UPDATE users SET last_login = GETDATE() WHERE name = @P1", ["Alice Johnson"]),
            ("INSERT INTO user_logs (action, user_name) VALUES (@P1, @P2)", ["login", "Alice Johnson"])
        ]

        affected_counts = await conn.execute_batch(commands)
        print(f"Updated {affected_counts[0]} users, inserted {affected_counts[1]} logs")

asyncio.run(main_fetching())
```

### Connection pooling

Tune the pool to fit your workload. Constructor signature:

```python
from fastmssql import PoolConfig

config = PoolConfig(
    max_size=20,              # max connections in pool
    min_idle=5,               # keep at least this many idle
    max_lifetime_secs=3600,   # recycle connections after 1h
    idle_timeout_secs=600,    # close idle connections after 10m
    connection_timeout_secs=30
)
```

Presets:

```python
one   = PoolConfig.one()                     # max_size=1,  min_idle=1  (single connection)
low   = PoolConfig.low_resource()            # max_size=3,  min_idle=1  (constrained environments)
dev   = PoolConfig.development()             # max_size=5,  min_idle=1  (local development)
high  = PoolConfig.high_throughput()         # max_size=25, min_idle=8  (high-throughput workloads)
maxp  = PoolConfig.performance()             # max_size=30, min_idle=10 (maximum performance)

# ✨ RECOMMENDED: Adaptive pool sizing based on your concurrency
adapt = PoolConfig.adaptive(20)              # Dynamically sized for 20 concurrent workers
                                             # Formula: max_size = ceil(workers * 1.2) + 5
```

**⚡ Performance Tip**: Use `PoolConfig.adaptive(n)` where `n` is your expected concurrent workers/tasks. This prevents connection pool lock contention that can degrade performance with oversized pools.

Apply to a connection:

```python
# Recommended: adaptive sizing
async with Connection(conn_str, pool_config=PoolConfig.adaptive(20)) as conn:
    rows = (await conn.query("SELECT 1 AS ok")).rows()

# Or use presets
async with Connection(conn_str, pool_config=PoolConfig.high_throughput()) as conn:
    rows = (await conn.query("SELECT 1 AS ok")).rows()
```

Default pool (if omitted or constructed with `PoolConfig()`):
`max_size=15`, `min_idle=3`, `max_lifetime_secs=1800`,
`idle_timeout_secs=300`, `connection_timeout_secs=30`.

Explicit values always win. `None` leaves the corresponding FastMssql bb8
override unset; it does not by itself guarantee an unlimited timeout. To
retain the historical direct-constructor field profile explicitly:

```python
legacy_direct_profile = PoolConfig(
    max_size=20,
    min_idle=2,
    max_lifetime_secs=None,
    idle_timeout_secs=None,
    connection_timeout_secs=30,
)
```

### Operation deadlines and timeout safety

Use `TimeoutConfig` to give each part of database work an independent budget:

```python
from fastmssql import (
    Connection,
    OperationTimeoutError,
    PoolConfig,
    TimeoutConfig,
)

timeouts = TimeoutConfig(
    connect_timeout_secs=10.0,
    acquire_timeout_secs=2.0,
    operation_timeout_secs=15.0,
    transaction_timeout_secs=30.0,
    rollback_timeout_secs=5.0,
)

async with Connection(
    conn_str,
    pool_config=PoolConfig(max_size=20, min_idle=5),
    timeout_config=timeouts,
) as database:
    try:
        await database.execute(
            "UPDATE jobs SET status = @P1 WHERE id = @P2",
            ["running", 42],
        )
    except OperationTimeoutError as error:
        # Use structured fields; do not parse the human-readable message.
        print(
            error.operation,
            error.phase,
            error.timeout_seconds,
            error.retryable,
            error.connection_discarded,
            error.outcome_unknown,
        )
        raise
```

The five settings have distinct boundaries:

| Setting | Phase | What it bounds |
| --- | --- | --- |
| `connect_timeout_secs` | `connect` | credential acquisition, TCP, TLS/login and a routed reconnect |
| `acquire_timeout_secs` | `acquire` | pool queueing, checkout and checkout validation/reset |
| `operation_timeout_secs` | `operation` | one complete query, command, ping, batch or bulk call |
| `transaction_timeout_secs` | `transaction` | absolute lifetime after SQL Server confirms `BEGIN` |
| `rollback_timeout_secs` | `rollback` | explicit rollback and rollback performed by `close()` |

Every configured number must be finite, between one nanosecond and
3,153,600,000 seconds (100 × 365 days) inclusive, and able to form a deadline
on the platform's monotonic clock; booleans are rejected. The explicit
100-year ceiling keeps validation identical on Linux, macOS, and Windows.
Subsecond values are supported. `None` means that FastMssql does not install a
deadline for that phase and is accepted for connect, operation, transaction
and rollback. Acquisition must always be bounded, so
`acquire_timeout_secs` cannot be `None`. Operating-system, network,
infrastructure or SQL Server timeouts may still apply when a FastMssql
deadline is disabled.

For backward compatibility, omitting `timeout_config` derives the effective
policy from the legacy pool setting:

```text
connect_timeout_secs       PoolConfig.connection_timeout_secs or 30.0
acquire_timeout_secs       PoolConfig.connection_timeout_secs or 30.0
operation_timeout_secs     None
transaction_timeout_secs   None
rollback_timeout_secs      30.0
```

If both configurations are supplied, `TimeoutConfig.acquire_timeout_secs`
wins and the effective pool checkout timeout is aligned to it. Read
`connection.timeout_config` or `transaction.timeout_config` to inspect a
clone of the effective policy.

A timeout after a TDS request may have started is fail-closed: FastMssql
retires that physical connection instead of returning a possibly desynchronized
session to the pool. It does not retry SQL operations. A general write timeout
has `outcome_unknown=True`; reconcile the write using an idempotency or
business key before deciding what to do next. `retryable=True` is used only
when connect or acquisition expired before application SQL began, and is
guidance for the caller rather than an automatic retry.

An unconfirmed `COMMIT` has a stricter exception contract:

```python
from fastmssql import CommitOutcomeUnknown, OperationTimeoutError

request_id = "order-2026-000042"  # unique business/idempotency key
transaction = database.transaction()

try:
    await transaction.begin()
    await transaction.execute(
        "INSERT INTO orders (request_id, total) VALUES (@P1, @P2)",
        [request_id, 99.99],
    )
    await transaction.commit()
except CommitOutcomeUnknown as error:
    # The connection was retired. Never issue a blind second COMMIT or
    # ROLLBACK: SQL Server may already have committed the first request.
    cause = error.__cause__
    if isinstance(cause, OperationTimeoutError):
        print(cause.phase, cause.timeout_seconds, cause.outcome_unknown)

    result = await database.query(
        "SELECT id, status FROM orders WHERE request_id = @P1",
        [request_id],
    )
    reconciled_rows = result.rows()
    # Continue from the reconciled database state. Retry only if the
    # application can prove that the original write was not committed.
```

For a timeout while awaiting `COMMIT`, the top-level exception is always
`CommitOutcomeUnknown`; its `__cause__` is the structured
`OperationTimeoutError` whose phase is `operation` or `transaction`.

### Graceful connection lifecycle

Use `LifecycleConfig` to bound application shutdown without claiming that
in-flight database work disappeared:

```python
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastmssql import Connection, LifecycleConfig


lifecycle = LifecycleConfig(
    shutdown_timeout_secs=30.0,
    force_timeout_secs=5.0,
)
connection = Connection(
    server="sql.example.internal",
    database="application",
    username=os.environ["MSSQL_USER"],
    password=os.environ["MSSQL_PASSWORD"],
    lifecycle_config=lifecycle,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    await connection.connect()
    try:
        yield
    finally:
        await connection.disconnect()


app = FastAPI(lifespan=lifespan)
```

Every connection has an explicit admission state:

- `Open` accepts work. It does not mean that a pool exists or that SQL Server
  is ready; use `ping()` for readiness.
- `Closing` rejects new SQL with `ConnectionLifecycleError` while work already
  admitted to that generation drains.
- `Closed` owns no active pool and can lazily or explicitly reopen as a new
  generation.

`disconnect()` waits up to `shutdown_timeout_secs`. If work remains, it
force-retires affected transports for at most `force_timeout_secs`, reaches
`Closed`, and then raises `ShutdownTimeoutError`. A forced cleanup is never
reported as a graceful success. Forced query, write, batch, or bulk outcomes
are conservatively marked `outcome_unknown=True`; FastMssql never retries
them. Reconcile uncertain writes with a unique business or idempotency key.
An unconfirmed forced `COMMIT` remains `CommitOutcomeUnknown` and chains the
structured lifecycle error as its cause.

FastAPI/native ASGI and Flask through an ASGI adapter can own an
application-scoped connection on a persistent event loop. Flask `async def`
under plain WSGI remains functional compatibility only: WSGI may create a
different event loop per request, so it is not a true-async concurrency or
persistent-lifecycle deployment model.


### Transactions

Create production transactions from a `Connection`. `transaction()` reserves
one physical session from that connection's bb8 pool, keeps the same SPID for
the entire active transaction, and releases the lease immediately after
`COMMIT` or `ROLLBACK`. Transactions and ordinary queries therefore share the
same `pool.max_size` budget.

#### Automatic transaction control (recommended)

Use the context manager for automatic `BEGIN`, `COMMIT`, and `ROLLBACK`:

```python
import asyncio
from fastmssql import Connection, PoolConfig

async def main():
    conn_str = "Server=localhost;Database=master;User Id=myuser;Password=mypass"

    async with Connection(
        conn_str,
        pool_config=PoolConfig(max_size=20, min_idle=2),
    ) as database:
        async with database.transaction() as transaction:
            await transaction.execute(
                "INSERT INTO orders (customer_id, total) VALUES (@P1, @P2)",
                [123, 99.99],
            )
            await transaction.execute(
                "INSERT INTO order_items (order_id, product_id, qty) "
                "VALUES (@P1, @P2, @P3)",
                [1, 456, 2],
            )
            # COMMIT on success; ROLLBACK when the block raises.

asyncio.run(main())
```

#### Manual transaction control

For more control, explicitly call `begin()`, `commit()`, and `rollback()`:

```python
import asyncio
from fastmssql import Connection, PoolConfig, SqlError

async def main():
    conn_str = "Server=localhost;Database=master;User Id=myuser;Password=mypass"
    database = Connection(conn_str, pool_config=PoolConfig(max_size=20))
    transaction = database.transaction()

    try:
        await transaction.begin()
        await transaction.execute(
            "UPDATE accounts SET balance = balance - @P1 WHERE id = @P2",
            [50, 1],
        )
        await transaction.execute(
            "UPDATE accounts SET balance = balance + @P1 WHERE id = @P2",
            [50, 2],
        )
        await transaction.commit()  # releases the pool lease
    except SqlError:
        await transaction.rollback()  # also releases the lease
        raise
    finally:
        await transaction.close()
        await database.disconnect()

asyncio.run(main())
```

When an operation is cancelled while TDS is in flight, the transaction becomes
fail-closed. Call `close()`; the uncertain physical connection is retired
instead of being returned to the pool.

Transactions support the same bounded multi-result API while retaining the
session exclusively until terminal disposition:

```python
async with database.transaction() as transaction:
    response = await transaction.stream(
        "SELECT id, total FROM orders WHERE customer_id = @P1",
        [123],
        buffer_size=32,
    )
    async with response:
        async for result_set in response:
            async for row in result_set:
                await handle(row)
```

While `response` is live, another query, execute, commit, or rollback on that
transaction waits for the same session mutex. Normal complete EOF restores the
prior transaction state. Closing or dropping the full response, a post-wire
conversion failure, a deadline, or forced shutdown retires the transport,
marks the transaction failed, and makes a later commit fail locally. Closing
only one `ResultSet` preserves the transaction and advances to the next set.

#### Direct constructor compatibility

`Transaction(conn_str)` remains supported for backward compatibility. It owns
a dedicated direct socket and is not bounded by another `Connection` object's
pool:

```python
from fastmssql import Transaction

async with Transaction(conn_str) as transaction:
    await transaction.execute("INSERT INTO audit_log(message) VALUES (@P1)", ["ok"])
```

For concurrent production workloads, prefer `Connection.transaction()` so
transactions and non-transactional operations obey one explicit capacity
limit.


### SSL/TLS

Full-session encryption is required by default for both connection strings and
individual connection parameters. If a connection string omits `Encrypt`,
FastMssql treats it as `Encrypt=True`. Certificate verification uses the system
trust store unless you configure one of the following policies.

Choose exactly one source for TLS settings:

- put `Encrypt`, `TrustServerCertificate`, and
  `TrustServerCertificateCA` in the connection string; or
- omit all three connection-string options and pass `ssl_config`.

Mixing the two sources raises `ValueError`. `TrustServerCertificate=True` only
disables certificate verification; it does not by itself opt out of
full-session encryption.

For required or login-only encryption, specify how to validate the server
certificate:

**Option 1: Trust Server Certificate** (development/self-signed certs):

```python
from fastmssql import SslConfig, EncryptionLevel, Connection

ssl = SslConfig(
    encryption_level=EncryptionLevel.Required,
    trust_server_certificate=True
)

async with Connection(conn_str, ssl_config=ssl) as conn:
    ...
```

**Option 2: Custom CA Certificate** (production):

```python
from fastmssql import SslConfig, EncryptionLevel, Connection

ssl = SslConfig(
    encryption_level=EncryptionLevel.Required,
    ca_certificate_path="/path/to/ca-cert.pem"
)

async with Connection(conn_str, ssl_config=ssl) as conn:
    ...
```

**Note**: `trust_server_certificate` and `ca_certificate_path` are mutually exclusive.

Helpers:

- `SslConfig.development()` – encrypt, trust all (dev only)
- `SslConfig.with_ca_certificate(path)` – use custom CA
- `SslConfig.login_only()` / `SslConfig.disabled()` – legacy modes
- `SslConfig.disabled()` – no encryption (not recommended)

Legacy modes must be selected explicitly, either with
`SslConfig.login_only()` / `SslConfig.disabled()` or with
`Encrypt=False` / `Encrypt=DANGER_PLAINTEXT` in the connection string.

## Performance tips

### 1. Use adaptive pool sizing for optimal concurrency

Match your pool size to actual concurrency to avoid connection pool lock contention:

```python
import asyncio
from fastmssql import Connection, PoolConfig

async def worker(conn_str, cfg):
    async with Connection(conn_str, pool_config=cfg) as conn:
        for _ in range(1000):
            result = await conn.query("SELECT 1 as v")
            # ✅ Good: Lazy iteration (minimal GIL hold per row)
            for row in result:
                process(row)

async def main():
    conn_str = "Server=.;Database=master;User Id=sa;Password=StrongPwd;"
    num_workers = 32
    
    # ✅ Adaptive sizing prevents pool contention
    cfg = PoolConfig.adaptive(num_workers)  # → max_size=43 for 32 workers
    
    await asyncio.gather(*[worker(conn_str, cfg) for _ in range(num_workers)])

asyncio.run(main())
```

### 2. Use bounded async streaming for large result sets

```python
response = await conn.stream(
    "SELECT * FROM large_table",
    buffer_size=64,
)
async with response:
    async for result_set in response:
        async for row in result_set:
            await process(row)
```

`query()` still performs lazy Python conversion while iterating, but its SQL
rows are already buffered before the compatibility `QueryStream` is returned.
Use `stream()` when wire-level backpressure and bounded in-flight result events
matter.

## Examples & benchmarks

- Examples: `examples/comprehensive_example.py`
- Benchmarks: `benchmarks/`

## Troubleshooting

- Import/build: ensure Rust toolchain and `maturin` are installed if building from source
- Connection: verify connection string; Windows auth not supported
- Timeouts: increase pool size or tune `connection_timeout_secs`
- Parameters: use `@P1, @P2, ...` and pass a list of values

## Contributing

Contributions are welcome. Please open an issue or PR.

## License

FastMSSQL is licensed under MIT:

See the [LICENSE](LICENSE) file for details.

## Third‑party attributions

Built on excellent open source projects: Tiberius, PyO3, pyo3‑asyncio, bb8, tokio, serde, pytest, maturin, and more. See `licenses/NOTICE.txt` for the full list. The full texts of Apache‑2.0 and MIT are in `licenses/`.

## Acknowledgments

Thanks to the maintainers of Tiberius, bb8, PyO3, Tokio, pytest, maturin, and the broader open source community.
