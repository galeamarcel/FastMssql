"""Type stubs for FastMSSQL wrapper classes.

The re-exported ``Parameter`` surface includes closed SQL declarations,
canonical metadata, typed NULL handling, bounded expansion, and INPUT-only
execution semantics for the currently modeled direction field.
"""

from typing import Any, Coroutine, Dict, List, Literal, Optional, StrEnum, Tuple, TypedDict
from .fastmssql import (
    AzureCredential,
    AzureCredentialType,
    CommitOutcomeUnknown,
    ColumnMetadata,
    ConnectionLifecycleError,
    ConnectionLifecycleState,
    ConversionError,
    DoneResult,
    EncryptionLevel,
    FastRow,
    Parameter,
    Parameters,
    OperationTimeoutError,
    LifecycleConfig,
    PoolConfig,
    ProtocolError,
    QueryStream,
    ResultSet,
    ResultStream,
    ResultSummary,
    SqlConnectionError,
    SqlError,
    SslConfig,
    ShutdownTimeoutError,
    SqlMessage,
    TlsError,
    TimeoutConfig,
    TypedNull,
)

class _OperationStatsEntry(TypedDict):
    started: int
    completed: int
    in_flight: int
    succeeded: int
    errors: int
    timed_out: int
    cancelled: int
    outcome_unknown: int
    duration_seconds_sum: float
    duration_seconds_min: Optional[float]
    duration_seconds_max: Optional[float]
    duration_seconds_buckets: List[int]
    saturated: bool

class _OperationStatsByName(TypedDict):
    connect: _OperationStatsEntry
    ping: _OperationStatsEntry
    query: _OperationStatsEntry
    simple_query: _OperationStatsEntry
    execute: _OperationStatsEntry
    query_batch: _OperationStatsEntry
    execute_batch: _OperationStatsEntry
    bulk_insert: _OperationStatsEntry
    begin: _OperationStatsEntry
    commit: _OperationStatsEntry
    rollback: _OperationStatsEntry
    close: _OperationStatsEntry
    disconnect: _OperationStatsEntry

class _OperationStatsSnapshot(TypedDict):
    schema_version: Literal[1]
    enabled: bool
    bucket_bounds_seconds: List[float]
    operations: _OperationStatsByName

class OperationMetricsConfig:
    """Default-off policy for fixed, connection-lifetime operation metrics."""

    enabled: bool

    def __init__(self, enabled: bool = False) -> None: ...

class ApplicationIntent(StrEnum):
    """SQL Server application intent constants."""

    READ_ONLY: str
    READ_WRITE: str

class Connection:
    """
    High-performance SQL Server connection with async/await support.

    Thin wrapper around the Rust-backed connection that fixes async context manager behavior.
    Delegates all methods to the underlying Rust connection.

    Supports multiple initialization patterns:
    - Connection string: Connection("Server=localhost;Database=test")
    - Individual parameters: Connection(server="localhost", database="test")
    - SQL auth: Connection(server="host", username="user", password="pass")
    - Azure auth: Connection(server="host", azure_credential=azure_cred)

    Features:
    - Thread-safe connection pooling with configurable parameters
    - Async/await support for non-blocking I/O
    - SSL/TLS encryption support
    - Azure Active Directory authentication
    - Parameterized queries with automatic type conversion
    - Batch operations for high-performance bulk inserts and multiple queries
    - Connection pool statistics and monitoring
    """

    _conn: Any  # The underlying Rust connection

    def __init__(
        self,
        connection_string: Optional[str] = None,
        server: Optional[str] = None,
        database: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        pool_config: Optional[PoolConfig] = None,
        ssl_config: Optional[SslConfig] = None,
        azure_credential: Optional[AzureCredential] = None,
        application_intent: Optional[ApplicationIntent | str] = None,
        port: Optional[int] = None,
        instance_name: Optional[str] = None,
        application_name: Optional[str] = None,
        timeout_config: Optional[TimeoutConfig] = None,
        lifecycle_config: Optional[LifecycleConfig] = None,
        operation_metrics_config: Optional[OperationMetricsConfig] = None,
    ) -> None:
        """
        Initialize a new SQL Server connection.

        Args:
            connection_string: Complete ADO.NET-style connection string. Full-session
                encryption is required when Encrypt is omitted.
            server: SQL Server hostname or IP address
            database: Database name
            username: Username for SQL authentication (required when using individual parameters)
            password: Password for SQL authentication
            pool_config: Connection pool configuration
            ssl_config: SSL/TLS configuration. Do not combine it with Encrypt,
                TrustServerCertificate, or TrustServerCertificateCA in the
                connection string.
            azure_credential: Azure Active Directory credential for authentication
            application_intent: Sets ApplicationIntent to "ReadOnly" or "ReadWrite" (default: ReadWrite)
            port: TCP port number (default: 1433)
            instance_name: Named instance of SQL Server
            application_name: Application name for SQL Server connection

        Note:
            - Either connection_string OR individual parameters must be provided
            - When using individual parameters, either username/password OR azure_credential must be provided
            - azure_credential and username/password are mutually exclusive
            - Weaker TLS modes require an explicit ssl_config or Encrypt opt-out
        """
        ...

    @property
    def timeout_config(self) -> TimeoutConfig:
        """Return an isolated copy of the effective timeout policy."""
        ...

    @property
    def lifecycle_config(self) -> LifecycleConfig:
        """Return an isolated copy of the effective lifecycle policy."""
        ...

    @property
    def operation_metrics_config(self) -> OperationMetricsConfig:
        """Return an isolated copy of the operation-metrics policy."""
        ...

    @property
    def lifecycle_state(self) -> ConnectionLifecycleState:
        """Return Open, Closing, or Closed without network I/O."""
        ...

    def connect(
        self,
        validate: bool = True,
    ) -> Coroutine[Any, Any, bool]:
        """Initialize the pool and validate SQL Server by default.

        Set validate=False for intentional lazy pool allocation without a
        readiness claim.
        """
        ...

    def ping(self) -> Coroutine[Any, Any, bool]:
        """Execute SELECT 1 through the shared pool.

        Return True on success and raise a typed FastMssql exception on failure.
        """
        ...

    def disconnect(self) -> Coroutine[Any, Any, bool]:
        """Drop this connection object's pool handle."""
        ...

    def is_connected(self) -> Coroutine[Any, Any, bool]:
        """Return whether this object currently owns a pool handle.

        This method performs no network I/O. Use ping() for readiness.
        """
        ...

    def query(
        self,
        sql: str,
        params: Optional[List[Any]] = None,
    ) -> Coroutine[Any, Any, QueryStream]:
        """
        Execute SELECT and return the buffered first result set for synchronous
        compatibility iteration after awaiting this method.

        Args:
            sql: SQL query with @P1, @P2, etc. placeholders for parameters
            params: List of parameter values in order
        Returns:
            QueryStream for iterating over result rows
        """
        ...

    async def stream(
        self,
        sql: str,
        params: list[Any] | Parameters | None = None,
        *,
        buffer_size: int = 64,
    ) -> ResultStream: ...

    async def batch(
        self,
        sql: str,
        *,
        buffer_size: int = 64,
    ) -> ResultStream: ...

    def simple_query(
        self,
        sql: str,
    ) -> Coroutine[Any, Any, QueryStream]:
        """
        Execute raw SQL and return the buffered first result set for
        synchronous compatibility iteration after awaiting this method.

        Only use this when required (creating stored procedures may require this in certain cases)

        Args:
            sql: Raw SQL query
        Returns:
            QueryStream for iterating over result rows
        """
        ...

    def execute(
        self,
        sql: str,
        params: Optional[List[Any]] = None,
    ) -> Coroutine[Any, Any, int]:
        """
        Execute INSERT/UPDATE/DELETE/DDL command.

        Args:
            sql: SQL command with @P1, @P2, etc. placeholders
            params: List of parameter values in order

        Returns:
            Number of affected rows
        """
        ...

    def execute_batch(
        self,
        commands: List[Tuple[str, Optional[List[Any]]]],
    ) -> Coroutine[Any, Any, List[int]]:
        """
        Execute multiple commands in a single batch for better performance.

        Args:
            commands: List of (sql, params) tuples

        Returns:
            List of affected row counts for each command
        """
        ...

    def bulk_insert(
        self,
        table: str,
        columns: List[str],
        data: List[List[Any]],
    ) -> Coroutine[Any, Any, None]:
        """
        High-performance bulk insert for large datasets.

        Args:
            table: Target table name (can be schema-qualified)
            columns: List of column names
            data: List of rows, each row is a list of values
        """
        ...

    def query_batch(
        self,
        queries: List[str] | List[Tuple[str, Optional[List[Any]]]],
    ) -> Coroutine[Any, Any, List[QueryStream]]:
        """
        Execute multiple SELECT queries in a single batch.

        Args:
            queries: List of (sql, params) tuples or just sql strings

        Returns:
            List of QueryStream objects for each query
        """
        ...

    def pool_stats(
        self,
    ) -> Coroutine[Any, Any, Dict[str, int | float | bool | None]]:
        """
        Get the current connection-pool statistics without performing SQL.

        Returns a dictionary with the following keys:
        - connected (bool): Whether this object owns a pool handle
        - connections (int): Total number of connections in the pool
        - idle_connections (int): Number of idle connections available
        - active_connections (int): Number of connections currently in use
        - max_size (int): Maximum pool size
        - min_idle (int | None): Minimum idle connections to maintain
        - get_started (int): Checkout attempts started
        - get_direct (int): Checkouts completed without waiting
        - get_waited (int): Checkouts completed after waiting
        - get_timed_out (int): Checkouts that reached the acquire timeout
        - pending_gets (int): Currently outstanding checkouts
        - get_wait_time_seconds (float): Cumulative checkout wait seconds
        - connections_created (int): Physical connections created
        - connections_closed_broken (int): Broken connections retired
        - connections_closed_invalid (int): Validation failures retired
        - connections_closed_max_lifetime (int): Lifetime retirements
        - connections_closed_idle_timeout (int): Idle-timeout retirements

        Checkout counters include connect/ping readiness acquisitions.
        Retirement-event counters are not mutually exclusive.
        """
        ...

    def operation_stats(
        self,
    ) -> Coroutine[Any, Any, _OperationStatsSnapshot]:
        """Return fixed connection-lifetime operation metrics without SQL."""
        ...

    def transaction(self) -> Transaction:
        """Create a transaction backed by this connection's shared pool."""
        ...

    async def __aenter__(self) -> Connection:
        """Validate SQL Server readiness before entering the async context."""
        ...

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit (closes pool)."""
        ...

class Transaction:
    """
    SQL Server transaction on a shared pool lease or direct connection.

    Prefer Connection.transaction() for bounded enterprise concurrency. The
    direct constructor remains available for backward compatibility.

    Example:
        async with Transaction(server="localhost", database="mydb") as conn:
            await conn.execute("INSERT INTO ...")
    """

    _rust_conn: Any  # The underlying Rust transaction connection

    def __init__(
        self,
        connection_string: Optional[str] = None,
        ssl_config: Optional[SslConfig] = None,
        azure_credential: Optional[AzureCredential] = None,
        server: Optional[str] = None,
        database: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        application_intent: Optional[ApplicationIntent | str] = None,
        port: Optional[int] = None,
        instance_name: Optional[str] = None,
        application_name: Optional[str] = None,
        timeout_config: Optional[TimeoutConfig] = None,
    ) -> None:
        """Initialize a dedicated non-pooled connection for transactions.

        A connection string without Encrypt requires full-session encryption.
        TLS options must come from either the connection string or ssl_config,
        never both.
        """
        ...

    @property
    def timeout_config(self) -> TimeoutConfig:
        """Return an isolated copy of the effective timeout policy."""
        ...

    def query(
        self,
        sql: str,
        params: Optional[List[Any]] = None,
    ) -> Coroutine[Any, Any, QueryStream]:
        """Return the buffered first result set for synchronous iteration."""
        ...

    async def stream(
        self,
        sql: str,
        params: list[Any] | Parameters | None = None,
        *,
        buffer_size: int = 64,
    ) -> ResultStream:
        """Stream every result set while retaining this transaction session."""
        ...

    async def batch(
        self,
        sql: str,
        *,
        buffer_size: int = 64,
    ) -> ResultStream:
        """Stream an unparameterized batch on this transaction session."""
        ...

    def execute(
        self,
        sql: str,
        params: Optional[List[Any]] = None,
    ) -> Coroutine[Any, Any, int]:
        """Execute an INSERT/UPDATE/DELETE/DDL command."""
        ...

    def execute_batch(
        self,
        commands: List[Tuple[str, Optional[List[Any]]]],
    ) -> Coroutine[Any, Any, List[int]]:
        """Execute multiple commands in sequence on this connection."""
        ...

    def query_batch(
        self,
        queries: List[Tuple[str, Optional[List[Any]]]],
    ) -> Coroutine[Any, Any, List[QueryStream]]:
        """Execute multiple SELECT queries in sequence on this connection."""
        ...

    def simple_query(
        self,
        sql: str,
    ) -> Coroutine[Any, Any, QueryStream]:
        """
        Execute raw SQL and return the buffered first result set for
        synchronous compatibility iteration after awaiting this method.

        Only use this when required (creating stored procedures may require this in certain cases)
        """
        ...

    def is_connected(self) -> bool:
        """Return True if the underlying connection is currently established."""
        ...

    async def begin(self) -> None:
        """Begin a transaction."""
        ...

    async def commit(self) -> None:
        """Commit the current transaction."""
        ...

    async def rollback(self) -> None:
        """Rollback the current transaction."""
        ...

    async def close(self) -> None:
        """Close the connection."""
        ...

    async def __aenter__(self) -> Transaction:
        """Async context manager entry - automatically BEGIN transaction."""
        ...

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit - automatically COMMIT or ROLLBACK."""
        ...

__all__ = [
    "ApplicationIntent",
    "AzureCredential",
    "AzureCredentialType",
    "CommitOutcomeUnknown",
    "ConversionError",
    "Connection",
    "ConnectionLifecycleError",
    "ConnectionLifecycleState",
    "ColumnMetadata",
    "EncryptionLevel",
    "FastRow",
    "DoneResult",
    "Parameter",
    "Parameters",
    "OperationTimeoutError",
    "LifecycleConfig",
    "OperationMetricsConfig",
    "PoolConfig",
    "ProtocolError",
    "QueryStream",
    "ResultSet",
    "ResultStream",
    "ResultSummary",
    "SqlConnectionError",
    "SqlError",
    "SslConfig",
    "ShutdownTimeoutError",
    "SqlMessage",
    "TlsError",
    "TimeoutConfig",
    "Transaction",
    "TypedNull",
    "version",
]

def version() -> str: ...
