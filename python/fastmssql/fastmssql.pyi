"""
FastMSSQL - High-Performance Microsoft SQL Server Driver for Python

High-performance Rust-backed Python driver for SQL Server with:
- Async/await support for non-blocking operations
- Connection pooling with configurable parameters
- SSL/TLS encryption support
- Parameterized queries with automatic type conversion
- Memory-efficient result handling
"""

from typing import Any, Coroutine, Dict, List, Optional, Tuple
from enum import StrEnum
from .fastmssql import _RustConnection, _RustTransaction

class PoolConfig:
    """
    Configuration for connection pool behavior.

    Attributes:
        max_size: Maximum number of connections in the pool (default: 15)
        min_idle: Minimum idle connections to maintain (default: 3; None leaves the FastMssql override unset)
        max_lifetime_secs: Maximum connection lifetime in seconds (default: 1800; None leaves the FastMssql override unset)
        idle_timeout_secs: Idle connection timeout in seconds (default: 300; None leaves the FastMssql override unset)
        connection_timeout_secs: Pool acquisition timeout in seconds (default: 30; None leaves the FastMssql override unset)
        test_on_check_out: Whether to test connections when checking out (default: None)
        retry_connection: Whether to retry connection attempts (default: None)

    Performance Note:
        Pool size should match your actual concurrency needs, not theoretical maximum.
        Rule of thumb: max_size ≈ (concurrent_workers * 1.2) + 5
        Larger pools can cause lock contention and degrade performance.
    """

    max_size: int
    min_idle: Optional[int]
    max_lifetime_secs: Optional[int]
    idle_timeout_secs: Optional[int]
    connection_timeout_secs: Optional[int]
    test_on_check_out: Optional[bool]
    retry_connection: Optional[bool]

    def __init__(
        self,
        max_size: int = 15,
        min_idle: Optional[int] = 3,
        max_lifetime_secs: Optional[int] = 1800,
        idle_timeout_secs: Optional[int] = 300,
        connection_timeout_secs: Optional[int] = 30,
        test_on_check_out: Optional[bool] = None,
        retry_connection: Optional[bool] = None,
    ) -> None: ...
    @staticmethod
    def one() -> PoolConfig:
        """Pre-configured pool for single-connection scenarios (max_size=1, min_idle=1)."""
        ...

    @staticmethod
    def high_throughput() -> PoolConfig:
        """Pre-configured pool for high-throughput scenarios (max_size=25, min_idle=8)."""
        ...

    @staticmethod
    def low_resource() -> PoolConfig:
        """Pre-configured pool for resource-constrained environments (max_size=3, min_idle=1)."""
        ...

    @staticmethod
    def development() -> PoolConfig:
        """Pre-configured pool for development (max_size=5, min_idle=1)."""
        ...

    @staticmethod
    def performance() -> PoolConfig:
        """Pre-configured pool for maximum performance (max_size=30, min_idle=10)."""
        ...
    @staticmethod
    def adaptive(concurrent_workers: int) -> PoolConfig:
        """
        Create an adaptive pool configuration based on expected concurrency.

        Args:
            concurrent_workers: Expected number of concurrent Python workers/asyncio tasks

        Returns:
            PoolConfig with max_size = ceil(concurrent_workers * 1.2) + 5

        Example:
            For 20 concurrent workers: adaptive(20) → max_size=29
        """
        ...

class TimeoutConfig:
    """Timeout policy shared by connection and transaction operations."""

    connect_timeout_secs: Optional[float]
    acquire_timeout_secs: float
    operation_timeout_secs: Optional[float]
    transaction_timeout_secs: Optional[float]
    rollback_timeout_secs: Optional[float]

    def __init__(
        self,
        connect_timeout_secs: Optional[float] = 30.0,
        acquire_timeout_secs: float = 30.0,
        operation_timeout_secs: Optional[float] = None,
        transaction_timeout_secs: Optional[float] = None,
        rollback_timeout_secs: Optional[float] = 30.0,
    ) -> None: ...

class LifecycleConfig:
    """Bounded graceful-shutdown and forced-retirement policy."""

    shutdown_timeout_secs: float
    force_timeout_secs: float

    def __init__(
        self,
        shutdown_timeout_secs: float = 30.0,
        force_timeout_secs: float = 5.0,
    ) -> None: ...

class ConnectionLifecycleState(StrEnum):
    """Connection admission lifecycle state."""

    OPEN: str
    CLOSING: str
    CLOSED: str

class EncryptionLevel(StrEnum):
    """SQL Server encryption level constants."""

    Disabled: str
    """No encryption."""
    LoginOnly: str
    """Encrypt only during login."""
    Required: str
    """Full encryption required."""

class ApplicationIntent(StrEnum):
    """SQL Server application intent constants."""

    READ_ONLY: str
    """Read-only workload."""
    READ_WRITE: str
    """Read-write workload."""

class SqlError(Exception):
    """
    Raised when the SQL Server returns an error response.

    Attributes:
        code: SQL Server error number (e.g. 208 for object not found).
        message: Human-readable error message from the server.
        state: SQL Server error state byte.

    Example::

        try:
            await conn.execute("INVALID SQL")
        except SqlError as e:
            print(e.code, e.message, e.state)
    """

    code: int
    message: str
    state: int
    ...

class SqlConnectionError(Exception):
    """
    Raised when a network I/O or routing error occurs connecting to SQL Server.

    Attributes:
        message: Human-readable error description, if provided by the underlying error.
        host: Redirect target host for routing errors, if available.
        port: Redirect target port for routing errors, if available.
    """

    message: Optional[str]
    host: Optional[str]
    port: Optional[int]
    ...

class OperationTimeoutError(SqlConnectionError):
    """Raised when a configured FastMssql deadline expires."""

    message: str
    operation: str
    phase: str
    timeout_seconds: float
    retryable: bool
    connection_discarded: bool
    outcome_unknown: bool
    ...

class ConnectionLifecycleError(SqlConnectionError):
    """Work was rejected or interrupted by connection shutdown."""

    message: str
    operation: str
    phase: str
    state: str
    generation: int
    retryable: bool
    connection_discarded: bool
    outcome_unknown: bool
    forced: bool
    ...

class ShutdownTimeoutError(ConnectionLifecycleError):
    """Graceful shutdown expired and forced retirement was attempted."""

    shutdown_timeout_seconds: float
    force_timeout_seconds: float
    active_operations_at_timeout: int
    active_transactions_at_timeout: int
    force_completed: bool
    ...

class CommitOutcomeUnknown(Exception):
    """COMMIT may have been applied but its completion was not confirmed."""

    message: str
    operation: str
    retryable: bool
    connection_discarded: bool
    ...

class TlsError(Exception):
    """
    Raised when a TLS/SSL handshake error occurs.

    Attributes:
        message: Human-readable error description.
    """

    message: str
    ...

class ProtocolError(Exception):
    """
    Raised when a protocol-level parsing error occurs during request or response handling.

    Attributes:
        message: Human-readable error description.
    """

    message: str
    ...

class ConversionError(Exception):
    """
    Raised when a type conversion or encoding error occurs.

    Attributes:
        message: Human-readable error description.
    """

    message: str
    ...

class SslConfig:
    """
    Configuration for SSL/TLS encrypted connections.

    Attributes:
        encryption_level: Level of encryption (Disabled, LoginOnly, or Required)
        trust_server_certificate: Whether to trust the server certificate without validation
        ca_certificate_path: Path to CA certificate file for certificate validation
    """

    encryption_level: str | EncryptionLevel
    trust_server_certificate: bool
    ca_certificate_path: Optional[str]

    def __init__(
        self,
        encryption_level: str | EncryptionLevel = "Required",
        trust_server_certificate: bool = False,
        ca_certificate_path: Optional[str] = None,
    ) -> None: ...
    @staticmethod
    def development() -> SslConfig:
        """Development configuration (Required encryption, trust server certificate)."""
        ...

    @staticmethod
    def login_only() -> SslConfig:
        """LoginOnly encryption configuration."""
        ...

    @staticmethod
    def disabled() -> SslConfig:
        """No encryption configuration."""
        ...

    @staticmethod
    def with_ca_certificate(path: str) -> SslConfig:
        """Create config with CA certificate validation from file path (.pem, .crt, .cer, or .der)."""
        ...
    def __eq__(self, other: object) -> bool: ...

class FastRow:
    """
    Represents a single row from a query result with optimized column access.

    Provides zero-copy access to row data with both dictionary-like and index-based access patterns.
    """

    def __getitem__(self, key: str | int) -> Any:
        """Access column value by name (string) or index (int)."""
        ...

    def columns(self) -> List[str]:
        """Get list of all column names in this row."""
        ...

    def __len__(self) -> int:
        """Get number of columns in this row."""
        ...

    def get(self, column: str) -> Any:
        """Get column value by name."""
        ...

    def get_by_index(self, index: int) -> Any:
        """Get column value by index."""
        ...

    def values(self) -> List[Any]:
        """Get all column values as a list in column order."""
        ...

    def to_dict(self) -> Dict[str, Any]:
        """Convert row to dictionary mapping column names to values."""
        ...

class QueryStream:
    """
    Async iterator for streaming query results row-by-row.

    Enables memory-efficient processing of large result sets by fetching rows
    on-demand instead of loading all rows into memory at once.

    Example:
        stream = await conn.query("SELECT * FROM large_table")
        async for row in stream:
            process(row)

        # Or fetch all remaining rows at once
        remaining = await stream.all()
    """

    async def __anext__(self) -> FastRow:
        """Get the next row in the stream (for async iteration)."""
        ...

    def all(self) -> List[FastRow]:
        """Load and return all remaining rows at once."""
        ...

    def fetch(self, n: int) -> List[FastRow]:
        """Fetch the next n rows as a batch."""
        ...

    def columns(self) -> List[str]:
        """Get list of all column names in the result set."""
        ...

    def reset(self) -> None:
        """Reset iteration to the beginning of the stream."""
        ...

    def position(self) -> int:
        """Get the current position in the stream (number of rows iterated)."""
        ...

    def len(self) -> int:
        """Get the total number of rows in the stream."""
        ...

    def __getitem__(self, key: int | slice) -> FastRow | List[FastRow]:
        """
        Access rows by index or slice.

        Supports:
        - Positive indexing: result[0], result[5]
        - Negative indexing: result[-1], result[-5]
        - Slicing: result[10:20], result[:5], result[5:]

        Lazily converts only the requested row(s) to Python objects.
        Uses cache so repeated access to the same index is efficient.

        Args:
            key: Integer index or slice object

        Returns:
            Single FastRow for integer index, List[FastRow] for slice

        Raises:
            IndexError: If index is out of range
            ValueError: If slice uses step other than 1
        """
        ...

    def is_empty(self) -> bool:
        """Check if the stream is empty."""
        ...

    def has_rows(self) -> bool:
        """Check if stream has rows."""
        ...

    def rows(self) -> List[FastRow]:
        """
        Get all rows at once (resets to beginning).

        .. warning::
            This method eagerly converts all rows to Python objects at once,
            which can cause GIL contention and poor performance with large result sets.
            For better performance, use iteration instead: ``for row in result: ...``
            This provides lazy, row-by-row conversion that distributes GIL acquisition.
        """
        ...

    def fetchone(self) -> Optional[FastRow]:
        """Fetch the next single row."""
        ...

    def fetchmany(self, n: int) -> List[FastRow]:
        """Fetch the next n rows."""
        ...

    def fetchall(self) -> List[FastRow]:
        """
        Fetch all remaining rows.

        .. warning::
            This method eagerly converts all remaining rows to Python objects at once,
            which can cause GIL contention and poor performance with large result sets.
            For better performance, use iteration instead: ``for row in result: ...``
            This provides lazy, row-by-row conversion that distributes GIL acquisition.
        """
        ...

class Parameter:
    """
    Parameter object for SQL queries with optional type hints.

    Use in parameter lists for parameterized queries. Parameters can specify explicit SQL types
    for automatic conversion and validation.

    Attributes:
        value: The parameter value (any Python type that can be converted to SQL)
        sql_type: Optional SQL Server type name (e.g., 'INT', 'VARCHAR', 'DATETIME2')
        is_expanded: Whether this parameter is an iterable for IN clause expansion
    """

    value: Any
    sql_type: Optional[str]
    is_expanded: bool

    def __init__(
        self,
        value: Any,
        sql_type: Optional[str] = None,
    ) -> None:
        """
        Create a new parameter with optional type specification.

        Args:
            value: The parameter value
            sql_type: Optional SQL Server type name for explicit type conversion
        """
        ...

class Parameters:
    """
    Collection of parameters for SQL queries with positional and named support.

    Supports both positional parameters (@P1, @P2, etc.) and named parameters (@name, @id, etc.).
    Can be constructed with positional and keyword arguments, with optional type specifications.

    Attributes:
        *args: List of Parameter objects in positional order
        **kwargs: Dictionary of named Parameter objects
    """

    positional: List[Parameter]
    named: Dict[str, Parameter]

    def __init__(
        self,
        *args: Any | Parameter,
        **kwargs: Any | Parameter,
    ) -> None:
        """
        Create a new Parameters collection.

        Args:
            *args: Positional parameters (raw values or Parameter objects)
            **kwargs: Named parameters (raw values or Parameter objects with keys as names)
        """
        ...

    def add(
        self,
        value: Any,
        sql_type: Optional[str] = None,
    ) -> Parameters:
        """
        Add a positional parameter and return self for chaining.

        Args:
            value: The parameter value
            sql_type: Optional SQL Server type name

        Returns:
            Self for method chaining
        """
        ...

    def set(
        self,
        key: str,
        value: Any,
        sql_type: Optional[str] = None,
    ) -> Parameters:
        """
        Add or update a named parameter and return self for chaining.

        Args:
            key: Parameter name
            value: The parameter value
            sql_type: Optional SQL Server type name

        Returns:
            Self for method chaining
        """
        ...

    def to_list(self) -> List[Any]:
        """Convert positional parameters to a list of values."""
        ...

    def __len__(self) -> int:
        """Get total number of parameters (positional + named)."""
        ...

    def __repr__(self) -> str:
        """Get string representation of parameters."""
        ...

class AzureCredentialType(StrEnum):
    """Azure credential type constants for authentication."""

    SERVICE_PRINCIPAL: str = "ServicePrincipal"
    MANAGED_IDENTITY: str = "ManagedIdentity"
    ACCESS_TOKEN: str = "AccessToken"
    DEFAULT_AZURE: str = "DefaultAzure"

class AzureCredential:
    """
    Azure Active Directory credential for SQL Server authentication.

    Supports various Azure authentication methods:
    - Service Principal: Client credentials (client_id, client_secret, tenant_id)
    - Managed Identity: For Azure resources (VMs, Functions, App Service, etc.)
    - Access Token: Pre-obtained access token
    - Default Azure: Azure SDK default credential chain
    """

    credential_type: AzureCredentialType
    config: Dict[str, str]

    @staticmethod
    def service_principal(
        client_id: str, client_secret: str, tenant_id: str
    ) -> AzureCredential:
        """
        Create Azure credential for Service Principal authentication.

        Args:
            client_id: Azure AD application (client) ID
            client_secret: Azure AD application client secret
            tenant_id: Azure AD tenant ID

        Returns:
            AzureCredential configured for Service Principal authentication
        """
        ...

    @staticmethod
    def managed_identity(client_id: Optional[str] = None) -> AzureCredential:
        """
        Create Azure credential for Managed Identity authentication.

        Args:
            client_id: Optional client ID for user-assigned managed identity.
                      If None, uses system-assigned managed identity.

        Returns:
            AzureCredential configured for Managed Identity authentication

        Note:
            This only works when running on Azure resources (VMs, Functions, App Service, etc.)
            with managed identity enabled.
        """
        ...

    @staticmethod
    def access_token(token: str) -> AzureCredential:
        """
        Create Azure credential with pre-obtained access token.

        Args:
            token: Valid Azure AD access token for SQL Database resource

        Returns:
            AzureCredential configured with the provided access token

        Note:
            The token must be valid for the SQL Database resource scope:
            https://database.windows.net/
        """
        ...

    @staticmethod
    def default() -> AzureCredential:
        """
        Create Azure credential using default credential chain.

        Returns:
            AzureCredential using Azure SDK's default credential chain

        Note:
            Default chain attempts credentials in this order:
            1. Environment variables (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, etc.)
            2. Managed Identity
            3. Azure CLI
            4. Azure PowerShell
            5. Visual Studio/VS Code
        """
        ...

class Connection:
    """
    High-performance SQL Server connection with async/await support.

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
    ) -> None:
        """
        Initialize a new SQL Server connection.

        Args:
            connection_string: Complete ADO.NET-style connection string (takes precedence)
            server: SQL Server hostname or IP address
            database: Database name
            username: Username for SQL authentication (required when using individual parameters)
            password: Password for SQL authentication
            pool_config: Connection pool configuration
            ssl_config: SSL/TLS configuration
            azure_credential: Azure Active Directory credential for authentication
            application_intent: Sets ApplicationIntent to "ReadOnly" or "ReadWrite" (default: ReadWrite)
            port: TCP port number (default: 1433)
            instance_name: Named instance of SQL Server
            application_name: Application name for SQL Server connection

        Note:
            - Either connection_string OR individual parameters must be provided
            - When using individual parameters, either username/password OR azure_credential must be provided
            - azure_credential and username/password are mutually exclusive
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
        Execute SELECT query that returns rows as an async stream.

        Returns a QueryStream for memory-efficient iteration over large result sets.

        Args:
            sql: SQL query with @P1, @P2, etc. placeholders for parameters
            params: List of parameter values in order
        Returns:
            QueryStream for iterating over result rows
        """
        ...

    def simple_query(
        self,
        sql: str,
    ) -> Coroutine[Any, Any, QueryStream]:
        """
        Execute a raw SQL query (non-prepared statement) that returns rows as an async stream.

        Only use this when required (creating stored procedures may require this in certain cases)

        Returns a QueryStream for memory-efficient iteration over large result sets.

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

    def transaction(self) -> Transaction:
        """Create a transaction backed by this connection's shared pool."""
        ...

    async def __aenter__(self) -> _RustConnection:
        """Validate SQL Server readiness before entering the async context."""
        ...

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit (closes pool)."""
        ...

    def version() -> str:
        """Get the fastmssql library version."""
        ...

class Transaction:
    """
    Single dedicated connection for SQL Server transactions.

    Provides a non-pooled connection where all operations happen on the same
    underlying connection, ensuring transaction safety for BEGIN/COMMIT/ROLLBACK.

    Example:
        async with Transaction(server="localhost", database="mydb") as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO users VALUES (@P1)", ["Alice"])
    """

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
        """Initialize a dedicated non-pooled connection for transactions."""
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
        """Execute a SELECT query that returns rows as a stream."""
        ...

    def simple_query(
        self,
        sql: str,
    ) -> Coroutine[Any, Any, QueryStream]:
        """
        Execute a raw SQL query (non-prepared statement) that returns rows as an async stream.

        Only use this when required (creating stored procedures may require this in certain cases)

        Returns a QueryStream for memory-efficient iteration over large result sets.

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
        """Execute an INSERT/UPDATE/DELETE/DDL command."""
        ...

    def execute_batch(
        self,
        commands: List[tuple[str, Optional[List[Any]]]],
    ) -> Coroutine[Any, Any, List[int]]:
        """
        Execute multiple commands in a batch on the transaction connection.

        Does NOT automatically wrap in transaction - use begin/commit/rollback manually.
        Returns a list of row counts affected by each command.

        Args:
            commands: List of (sql, parameters) tuples

        Returns:
            List of integers, one per command, indicating rows affected
        """
        ...

    def query_batch(
        self,
        queries: List[tuple[str, Optional[List[Any]]]],
    ) -> Coroutine[Any, Any, List[QueryStream]]:
        """
        Execute multiple queries in a batch on the transaction connection.

        Returns a list of QueryStream objects, one per query.

        Args:
            queries: List of (sql, parameters) tuples

        Returns:
            List of QueryStream objects
        """
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

    def is_connected(self) -> bool:
        """Return True if the underlying connection is currently established."""
        ...

    async def __aenter__(self) -> _RustTransaction:
        """Async context manager entry (begins transaction)."""
        ...

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit (commits or rolls back)."""
        ...

class TypedNull(StrEnum):
    """Class to store a typed null value

    This is required as some SQL Server features such as stored procedures etc. sometimes require type information for which is
    not possible for nulls when just using `None`. In such cases, SQL Server will complain about being unable to cast 'tinyint'
    to the desired data type.

    If a TypedNull is not explicitly used, fastmssql will default to using tinyint as the 'underlying type'
    when sending to SQL server
    """

    TINYINT = "TINYINT"
    SMALLINT = "SMALLINT"
    INT = "INT"
    BIGINT = "BIGINT"
    FLOAT32 = "FLOAT32"
    FLOAT64 = "FLOAT64"
    BIT = "BIT"
    STRING = "STRING"
    GUID = "GUID"
    BINARY = "BINARY"
    NUMERIC = "NUMERIC"
    XML = "XML"
    DATETIME = "DATETIME"
    SMALLDATETIME = "SMALLDATETIME"
    TIME = "TIME"
    DATE = "DATE"
    DATETIME2 = "DATETIME2"
    DATETIMEOFFSET = "DATETIMEOFFSET"

def version() -> str: ...
