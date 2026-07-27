"""FastMSSQL - High-Performance Microsoft SQL Server Driver for Python

High-performance Rust-backed Python driver for SQL Server with async/await support,
connection pooling, SSL/TLS encryption, Azure Active Directory authentication, and parameterized queries.
"""

import asyncio

# Import from the compiled Rust module
from .fastmssql import (
    Connection as _RustConnection,
)
from .fastmssql import (
    AzureCredential,
    AzureCredentialType,
    CommitOutcomeUnknown,
    ConnectionLifecycleError,
    ConnectionLifecycleState,
    ColumnMetadata,
    ConversionError,
    DoneResult,
    SqlConnectionError,
    EncryptionLevel,
    FastRow,
    Parameter,
    Parameters,
    PoolConfig,
    LifecycleConfig,
    OperationMetricsConfig,
    OperationTimeoutError,
    ProtocolError,
    QueryStream,
    ResultSet,
    ResultStream,
    ResultSummary,
    SqlError,
    SslConfig,
    ShutdownTimeoutError,
    SqlMessage,
    TlsError,
    TimeoutConfig,
    TypedNull,
    _ResultReceiveCancelled,
    version,
)
from .fastmssql import (
    Transaction as _RustTransaction,
)

from enum import StrEnum


async def _await_result_stream_receive(awaitable, cancellation):
    """Preserve receive state until Rust acknowledges Python cancellation."""
    try:
        return await asyncio.shield(awaitable)
    except asyncio.CancelledError as cancelled:
        cancellation.cancel()
        while True:
            try:
                await asyncio.shield(awaitable)
            except _ResultReceiveCancelled:
                break
            except asyncio.CancelledError as repeated_cancel:
                if awaitable.cancelled():
                    cancelled.__cause__ = repeated_cancel
                    break
            except BaseException as cleanup_error:
                cancelled.__cause__ = cleanup_error
                break
            else:
                break
        raise cancelled
    finally:
        cancellation.disarm()


def _observe_result_stream_receive(awaitable):
    """Mark completion observed without changing what an awaiter receives."""
    if not awaitable.cancelled():
        awaitable.exception()


class ApplicationIntent(StrEnum):
    READ_ONLY = "ReadOnly"
    READ_WRITE = "ReadWrite"


class Connection:
    """Thin wrapper to fix async context manager behavior."""

    def __init__(self, *args, **kwargs):
        self._conn = _RustConnection(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def connect(self, validate: bool = True) -> bool:
        """Initialize the pool and validate SQL Server by default.

        Pass ``validate=False`` only to allocate the pool lazily without
        claiming readiness.
        """
        return await self._conn.connect(validate)

    async def ping(self) -> bool:
        """Run a complete ``SELECT 1`` through the shared pool.

        Returns ``True`` on success and raises a typed FastMssql exception on
        failure.
        """
        return await self._conn.ping()

    async def disconnect(self) -> bool:
        """Drain and close this connection generation.

        Raises ``ShutdownTimeoutError`` after bounded forced cleanup when
        admitted work exceeds the graceful shutdown budget.
        """
        return await self._conn.disconnect()

    async def is_connected(self) -> bool:
        """Return whether this object owns a pool handle.

        This performs no network I/O. Use ``ping()`` for SQL Server readiness.
        """
        return await self._conn.is_connected()

    async def stream(self, sql, params=None, *, buffer_size=64):
        """Stream all result sets with bounded true-async backpressure."""
        return await self._conn.stream(
            sql,
            params,
            buffer_size=buffer_size,
        )

    async def batch(self, sql, *, buffer_size=64):
        """Stream every result set from an unparameterized SQL batch."""
        return await self._conn.batch(sql, buffer_size=buffer_size)

    async def callproc(self, procedure, params=None, *, buffer_size=64):
        """Call a named procedure by direct RPC and stream all results."""
        return await self._conn.callproc(
            procedure,
            params,
            buffer_size=buffer_size,
        )

    async def __aenter__(self):
        await self._conn.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self._conn.__aexit__(exc_type, exc_val, exc_tb)

    async def pool_stats(self):
        """Get connection pool statistics.

        Returns the current pool epoch as a fixed scalar dictionary:
        - connected (bool): whether this object owns a pool handle
        - connections (int): managed physical connections
        - idle_connections (int): currently idle connections
        - active_connections (int): currently leased connections
        - max_size (int): configured maximum pool size
        - min_idle (int | None): configured minimum idle count
        - get_started (int): checkout attempts started
        - get_direct (int): checkouts completed without waiting
        - get_waited (int): checkouts completed after waiting
        - get_timed_out (int): checkouts that reached the acquire timeout
        - pending_gets (int): currently outstanding checkouts
        - get_wait_time_seconds (float): cumulative checkout wait seconds
        - connections_created (int): physical connections created
        - connections_closed_broken (int): broken connections retired
        - connections_closed_invalid (int): validation failures retired
        - connections_closed_max_lifetime (int): lifetime retirements
        - connections_closed_idle_timeout (int): idle-timeout retirements

        Checkout counters include readiness acquisitions made by
        ``connect(validate=True)`` and ``ping()``.
        Retirement-event counters are not mutually exclusive. A failed
        validation can also retire the same transport as broken.

        Reading statistics performs no SQL and never creates a pool.
        """
        return await self._conn.pool_stats()

    async def operation_stats(self):
        """Return fixed connection-lifetime operation metrics without SQL."""
        return await self._conn.operation_stats()

    def transaction(self):
        """Create a transaction backed by this connection's shared pool."""
        return Transaction._from_rust(self._conn.transaction())

    @property
    def timeout_config(self):
        """Return an isolated copy of the effective timeout policy."""
        return self._conn.timeout_config

    @property
    def lifecycle_config(self):
        """Return an isolated copy of the effective lifecycle policy."""
        return self._conn.lifecycle_config

    @property
    def operation_metrics_config(self):
        """Return an isolated copy of the operation-metrics policy."""
        return self._conn.operation_metrics_config

    @property
    def lifecycle_state(self):
        """Return Open, Closing, or Closed without network I/O."""
        return self._conn.lifecycle_state


class Transaction:
    """SQL Server transaction on a shared pool lease or direct connection.

    Prefer ``Connection.transaction()`` for bounded enterprise concurrency.
    The direct constructor remains available for backward compatibility.

    Example:
        async with Transaction(server="localhost", database="mydb") as conn:
            await conn.execute("INSERT INTO ...")
    """

    def __init__(self, *args, **kwargs):
        """Initialize a dedicated non-pooled connection for transactions."""
        self._initialize(_RustTransaction(*args, **kwargs))

    @classmethod
    def _from_rust(cls, rust_transaction):
        transaction = cls.__new__(cls)
        transaction._initialize(rust_transaction)
        return transaction

    def _initialize(self, rust_transaction):
        self._rust_conn = rust_transaction
        self._TRANSACTION_BEGUN = False
        self._TRANSACTION_COMMITTED = False
        self._TRANSACTION_ROLLEDBACK = False

    def _reset_transaction_flags(self):
        """Reset the transaction state flags."""
        self._TRANSACTION_BEGUN = False
        self._TRANSACTION_COMMITTED = False
        self._TRANSACTION_ROLLEDBACK = False
    
    def _validate_transaction_flags(self):
        if not self._TRANSACTION_BEGUN:
            raise RuntimeError("Transaction has not begun")
        if self._TRANSACTION_COMMITTED:
            raise RuntimeError("Transaction has already been committed")
        if self._TRANSACTION_ROLLEDBACK:
            raise RuntimeError("Transaction has already been rolled back")

    async def query(self, sql, params=None):
        """Execute a SELECT query that returns rows."""
        return await self._rust_conn.query(sql, params)

    async def stream(self, sql, params=None, *, buffer_size=64):
        """Stream all result sets while retaining this transaction session."""
        return await self._rust_conn.stream(
            sql,
            params,
            buffer_size=buffer_size,
        )

    async def batch(self, sql, *, buffer_size=64):
        """Stream an unparameterized batch on this transaction session."""
        return await self._rust_conn.batch(sql, buffer_size=buffer_size)

    async def callproc(self, procedure, params=None, *, buffer_size=64):
        """Call a named procedure by direct RPC on this transaction."""
        return await self._rust_conn.callproc(
            procedure,
            params,
            buffer_size=buffer_size,
        )

    async def execute(self, sql, params=None):
        """Execute an INSERT/UPDATE/DELETE/DDL command."""
        return await self._rust_conn.execute(sql, params)

    async def execute_batch(self, commands):
        """Execute multiple commands in sequence on this connection."""
        return await self._rust_conn.execute_batch(commands)

    async def query_batch(self, queries):
        """Execute multiple SELECT queries in sequence on this connection."""
        return await self._rust_conn.query_batch(queries)

    async def simple_query(self, sql):
        """Execute a raw (non-prepared) SQL query and return a QueryStream."""
        return await self._rust_conn.simple_query(sql)

    def is_connected(self):
        """Return True if the underlying connection is currently established."""
        return self._rust_conn.is_connected()

    @property
    def timeout_config(self):
        """Return an isolated copy of the effective timeout policy."""
        return self._rust_conn.timeout_config

    async def begin(self):
        """Begin a transaction."""
        # If previous transaction completed, reset flags to allow reuse
        if self._TRANSACTION_COMMITTED or self._TRANSACTION_ROLLEDBACK:
            self._reset_transaction_flags()
        
        if self._TRANSACTION_BEGUN:
            raise RuntimeError("Transaction has already begun")
        await self._rust_conn.begin()
        self._TRANSACTION_BEGUN = True

    async def commit(self):
        """Commit the current transaction."""

        self._validate_transaction_flags()
        await self._rust_conn.commit()
        self._TRANSACTION_COMMITTED = True

    async def rollback(self):
        """Rollback the current transaction."""

        self._validate_transaction_flags()
        await self._rust_conn.rollback()
        self._TRANSACTION_ROLLEDBACK = True

    async def close(self):
        """Close the connection."""
        result = await self._rust_conn.close()
        self._reset_transaction_flags()
        return result

    async def __aenter__(self):
        """Async context manager entry - automatically BEGIN transaction."""
        await self.begin()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - automatically COMMIT or ROLLBACK."""
        try:
            if exc_type is not None:
                # An exception occurred - rollback (unless already settled)
                if not self._TRANSACTION_COMMITTED and not self._TRANSACTION_ROLLEDBACK:
                    try:
                        await self.rollback()
                    except Exception:
                        pass
            else:
                # No exception - commit (unless user already committed/rolled back manually)
                if not self._TRANSACTION_COMMITTED and not self._TRANSACTION_ROLLEDBACK:
                    try:
                        await self.commit()
                    except CommitOutcomeUnknown:
                        raise
                    except Exception as commit_err:
                        # Commit failed - attempt rollback then re-raise so the
                        # caller knows the transaction was never committed.
                        try:
                            await self.rollback()
                        except Exception:
                            pass
                        raise commit_err
        finally:
            # Always close the TCP connection so it is not held open until GC.
            # close() issues a best-effort ROLLBACK before dropping the stream,
            # so calling it here is safe even after a successful commit/rollback.
            await self.close()

        self._reset_transaction_flags()
        return False  # Don't suppress exceptions


__all__ = [
    "AzureCredential",
    "AzureCredentialType",
    "CommitOutcomeUnknown",
    "Connection",
    "ConnectionLifecycleError",
    "ConnectionLifecycleState",
    "ColumnMetadata",
    "ConversionError",
    "DoneResult",
    "SqlConnectionError",
    "EncryptionLevel",
    "FastRow",
    "Parameter",
    "Parameters",
    "PoolConfig",
    "LifecycleConfig",
    "OperationMetricsConfig",
    "OperationTimeoutError",
    "ProtocolError",
    "QueryStream",
    "ResultSet",
    "ResultStream",
    "ResultSummary",
    "SqlError",
    "SslConfig",
    "ShutdownTimeoutError",
    "SqlMessage",
    "TlsError",
    "TimeoutConfig",
    "Transaction",
    "ApplicationIntent",
    "TypedNull",
    "version",
]
