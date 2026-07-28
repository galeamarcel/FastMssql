mod auth;
pub(crate) mod bulk_columns;
#[cfg(test)]
mod bulk_columns_tests;
mod config;
mod connection;

mod tls;
#[cfg(any(
    feature = "rustls",
    feature = "native-tls",
    feature = "vendored-openssl"
))]
mod tls_stream;

pub use auth::*;
use bulk_columns::{
    checked_bulk_type_declaration, normalize_ordered_bulk_wire_metadata, BulkInsertColumns,
};
pub use config::*;
pub(crate) use connection::*;

use crate::tds::stream::ReceivedToken;
use crate::{
    result::ExecuteResult,
    tds::{
        codec::{self, IteratorJoin},
        stream::{QueryStream, ResponseStream, RpcParameter, TokenStream},
    },
    BulkLoadRequest, ColumnFlag, SqlParameterType, SqlReadBytes, ToSql,
};

/// Validates the raw table and ordered column identifiers accepted by
/// [`Client::bulk_insert_columns`] without performing any network I/O.
pub fn validate_bulk_insert_columns(table: &str, columns: &[&str]) -> crate::Result<()> {
    BulkInsertColumns::new(table, columns).map(|_| ())
}
use codec::{
    BatchRequest, ColumnData, PacketHeader, RpcParam, RpcParameterMetadata, RpcProcId, RpcStatus,
    TokenRpcRequest,
};
use enumflags2::BitFlags;
use futures_util::io::{AsyncRead, AsyncWrite};
use futures_util::stream::TryStreamExt;
use std::{borrow::Cow, fmt::Debug};

/// `Client` is the main entry point to the SQL Server, providing query
/// execution capabilities.
///
/// A `Client` is created using the [`Config`], defining the needed
/// connection options and capabilities.
///
/// # Example
///
/// ```no_run
/// # use tiberius::{Config, AuthMethod};
/// use tokio_util::compat::TokioAsyncWriteCompatExt;
///
/// # #[tokio::main]
/// # async fn main() -> Result<(), Box<dyn std::error::Error>> {
/// let mut config = Config::new();
///
/// config.host("0.0.0.0");
/// config.port(1433);
/// config.authentication(AuthMethod::sql_server("SA", "<Mys3cureP4ssW0rD>"));
///
/// let tcp = tokio::net::TcpStream::connect(config.get_addr()).await?;
/// tcp.set_nodelay(true)?;
/// // Client is ready to use.
/// let client = tiberius::Client::connect(config, tcp.compat_write()).await?;
/// # Ok(())
/// # }
/// ```
///
/// [`Config`]: struct.Config.html
#[derive(Debug)]
pub struct Client<S: AsyncRead + AsyncWrite + Unpin + Send> {
    pub(crate) connection: Connection<S>,
}

impl<S: AsyncRead + AsyncWrite + Unpin + Send> Client<S> {
    const RESET_ISOLATION_BASELINE: &'static str =
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED;\n";

    /// Uses an instance of [`Config`] to specify the connection
    /// options required to connect to the database using an established
    /// tcp connection
    ///
    /// [`Config`]: struct.Config.html
    pub async fn connect(config: Config, tcp_stream: S) -> crate::Result<Client<S>> {
        Ok(Client {
            connection: Connection::connect(config, tcp_stream).await?,
        })
    }

    /// Reset the SQL Server session immediately before the next application
    /// request while retaining the physical transport connection.
    ///
    /// The next Batch or RPC request carries the MS-TDS RESETCONNECTION status
    /// bit. Because MS-TDS explicitly excludes transaction isolation level
    /// from the server-side reset, that request is also prefixed with
    /// `SET TRANSACTION ISOLATION LEVEL READ COMMITTED`.
    pub fn reset_connection_on_next_request(&mut self) {
        self.connection.reset_connection_on_next_request();
    }

    /// Resets the SQL Server session immediately and fully consumes the reset
    /// response before returning.
    ///
    /// Unlike [`Client::reset_connection_on_next_request`], this method never
    /// leaves reset SQL to be combined with a later application batch.
    pub async fn reset_connection(&mut self) -> crate::Result<()> {
        self.connection.flush_stream().await?;
        self.connection.reset_connection_on_next_request();
        self.complete_pending_connection_reset().await
    }

    /// Executes SQL statements in the SQL Server, returning the number rows
    /// affected. Useful for `INSERT`, `UPDATE` and `DELETE` statements. The
    /// `query` can define the parameter placement by annotating them with
    /// `@PN`, where N is the index of the parameter, starting from `1`. If
    /// executing multiple queries at a time, delimit them with `;` and refer to
    /// [`ExecuteResult`] how to get results for the separate queries.
    ///
    /// For mapping of Rust types when writing, see the documentation for
    /// [`ToSql`]. For reading data from the database, see the documentation for
    /// [`FromSql`].
    ///
    /// This API is not quite suitable for dynamic query parameters. In these
    /// cases using a [`Query`] object might be easier.
    ///
    /// # Example
    ///
    /// ```no_run
    /// # use tiberius::Config;
    /// # use tokio_util::compat::TokioAsyncWriteCompatExt;
    /// # use std::env;
    /// # #[tokio::main]
    /// # async fn main() -> Result<(), Box<dyn std::error::Error>> {
    /// # let c_str = env::var("TIBERIUS_TEST_CONNECTION_STRING").unwrap_or(
    /// #     "server=tcp:localhost,1433;integratedSecurity=true;TrustServerCertificate=true".to_owned(),
    /// # );
    /// # let config = Config::from_ado_string(&c_str)?;
    /// # let tcp = tokio::net::TcpStream::connect(config.get_addr()).await?;
    /// # tcp.set_nodelay(true)?;
    /// # let mut client = tiberius::Client::connect(config, tcp.compat_write()).await?;
    /// let results = client
    ///     .execute(
    ///         "INSERT INTO ##Test (id) VALUES (@P1), (@P2), (@P3)",
    ///         &[&1i32, &2i32, &3i32],
    ///     )
    ///     .await?;
    /// # Ok(())
    /// # }
    /// ```
    ///
    /// [`ExecuteResult`]: struct.ExecuteResult.html
    /// [`ToSql`]: trait.ToSql.html
    /// [`FromSql`]: trait.FromSql.html
    /// [`Query`]: struct.Query.html
    pub async fn execute<'a>(
        &mut self,
        query: impl Into<Cow<'a, str>>,
        params: &[&dyn ToSql],
    ) -> crate::Result<ExecuteResult> {
        self.connection.flush_stream().await?;
        let query =
            Self::query_with_reset_baseline(query, self.connection.is_connection_reset_pending());
        let rpc_params = Self::rpc_params(query);

        let params = params
            .iter()
            .map(|value| (value.to_sql(), value.sql_parameter_type()));
        self.rpc_perform_query(RpcProcId::ExecuteSQL, rpc_params, params)
            .await?;

        ExecuteResult::new(&mut self.connection).await
    }

    /// Executes SQL statements in the SQL Server, returning resulting rows.
    /// Useful for `SELECT` statements. The `query` can define the parameter
    /// placement by annotating them with `@PN`, where N is the index of the
    /// parameter, starting from `1`. If executing multiple queries at a time,
    /// delimit them with `;` and refer to [`QueryStream`] on proper stream
    /// handling.
    ///
    /// For mapping of Rust types when writing, see the documentation for
    /// [`ToSql`]. For reading data from the database, see the documentation for
    /// [`FromSql`].
    ///
    /// This API can be cumbersome for dynamic query parameters. In these cases,
    /// if fighting too much with the compiler, using a [`Query`] object might be
    /// easier.
    ///
    /// # Example
    ///
    /// ```
    /// # use tiberius::Config;
    /// # use tokio_util::compat::TokioAsyncWriteCompatExt;
    /// # use std::env;
    /// # #[tokio::main]
    /// # async fn main() -> Result<(), Box<dyn std::error::Error>> {
    /// # let c_str = env::var("TIBERIUS_TEST_CONNECTION_STRING").unwrap_or(
    /// #     "server=tcp:localhost,1433;integratedSecurity=true;TrustServerCertificate=true".to_owned(),
    /// # );
    /// # let config = Config::from_ado_string(&c_str)?;
    /// # let tcp = tokio::net::TcpStream::connect(config.get_addr()).await?;
    /// # tcp.set_nodelay(true)?;
    /// # let mut client = tiberius::Client::connect(config, tcp.compat_write()).await?;
    /// let stream = client
    ///     .query(
    ///         "SELECT @P1, @P2, @P3",
    ///         &[&1i32, &2i32, &3i32],
    ///     )
    ///     .await?;
    /// # Ok(())
    /// # }
    /// ```
    ///
    /// [`QueryStream`]: struct.QueryStream.html
    /// [`Query`]: struct.Query.html
    /// [`ToSql`]: trait.ToSql.html
    /// [`FromSql`]: trait.FromSql.html
    pub async fn query<'a, 'b>(
        &'a mut self,
        query: impl Into<Cow<'b, str>>,
        params: &'b [&'b dyn ToSql],
    ) -> crate::Result<QueryStream<'a>>
    where
        'a: 'b,
    {
        let response = self.response_query(query, params).await?;
        let mut result = QueryStream::new(response);
        result.forward_to_metadata().await?;

        Ok(result)
    }

    /// Executes parameterized SQL and preserves every supported response
    /// event in wire order.
    pub async fn response_query<'a, 'b>(
        &'a mut self,
        query: impl Into<Cow<'b, str>>,
        params: &'b [&'b dyn ToSql],
    ) -> crate::Result<ResponseStream<'a>>
    where
        'a: 'b,
    {
        self.connection.flush_stream().await?;
        let query =
            Self::query_with_reset_baseline(query, self.connection.is_connection_reset_pending());
        let rpc_params = Self::rpc_params(query);

        let params = params
            .iter()
            .map(|value| (value.to_sql(), value.sql_parameter_type()));
        self.rpc_perform_query(RpcProcId::ExecuteSQL, rpc_params, params)
            .await?;

        let ts = TokenStream::new(&mut self.connection);
        Ok(ResponseStream::new(ts.try_unfold()))
    }

    /// Execute multiple queries, delimited with `;` and return multiple result
    /// sets; one for each query.
    ///
    /// # Example
    ///
    /// ```
    /// # use tiberius::Config;
    /// # use tokio_util::compat::TokioAsyncWriteCompatExt;
    /// # use std::env;
    /// # #[tokio::main]
    /// # async fn main() -> Result<(), Box<dyn std::error::Error>> {
    /// # let c_str = env::var("TIBERIUS_TEST_CONNECTION_STRING").unwrap_or(
    /// #     "server=tcp:localhost,1433;integratedSecurity=true;TrustServerCertificate=true".to_owned(),
    /// # );
    /// # let config = Config::from_ado_string(&c_str)?;
    /// # let tcp = tokio::net::TcpStream::connect(config.get_addr()).await?;
    /// # tcp.set_nodelay(true)?;
    /// # let mut client = tiberius::Client::connect(config, tcp.compat_write()).await?;
    /// let row = client.simple_query("SELECT 1 AS col").await?.into_row().await?.unwrap();
    /// assert_eq!(Some(1i32), row.get("col"));
    /// # Ok(())
    /// # }
    /// ```
    ///
    /// # Warning
    ///
    /// Do not use this with any user specified input. Please resort to prepared
    /// statements using the [`query`] method.
    ///
    /// [`query`]: #method.query
    pub async fn simple_query<'a, 'b>(
        &'a mut self,
        query: impl Into<Cow<'b, str>>,
    ) -> crate::Result<QueryStream<'a>>
    where
        'a: 'b,
    {
        let response = self.response_batch(query).await?;
        let mut result = QueryStream::new(response);
        result.forward_to_metadata().await?;

        Ok(result)
    }

    /// Executes a raw SQL batch and preserves every supported response event
    /// in wire order.
    pub async fn response_batch<'a, 'b>(
        &'a mut self,
        query: impl Into<Cow<'b, str>>,
    ) -> crate::Result<ResponseStream<'a>>
    where
        'a: 'b,
    {
        self.connection.flush_stream().await?;
        let query =
            Self::query_with_reset_baseline(query, self.connection.is_connection_reset_pending());

        let req = BatchRequest::new(query, self.connection.context().transaction_descriptor());

        let id = self.connection.context_mut().next_packet_id();
        self.connection.send(PacketHeader::batch(id), req).await?;

        let ts = TokenStream::new(&mut self.connection);
        Ok(ResponseStream::new(ts.try_unfold()))
    }

    /// Executes a stored procedure through a direct named RPC request and
    /// preserves every supported response event in wire order.
    pub async fn response_rpc<'a>(
        &'a mut self,
        procedure: String,
        params: Vec<RpcParameter>,
    ) -> crate::Result<ResponseStream<'a>> {
        self.connection.flush_stream().await?;
        self.complete_pending_connection_reset().await?;

        let collation = self.connection.context().collation();
        let utf8_support = self.connection.context().utf8_support();
        let mut rpc_params = Vec::with_capacity(params.len());

        for parameter in params {
            let (name, value, parameter_type, by_ref, parameter_index) = parameter.into_parts();
            let declaration = parameter_type
                .as_ref()
                .map(SqlParameterType::declaration)
                .unwrap_or_else(|| value.type_name().into_owned());
            let type_info = match parameter_type.as_ref() {
                Some(parameter_type) => {
                    if parameter_type.requires_utf8_support(collation) && !utf8_support {
                        return Err(crate::Error::parameter_conversion(
                            parameter_index,
                            declaration,
                            "metadata_error",
                            "SQL Server did not acknowledge UTF-8 parameter support",
                        ));
                    }

                    Some(parameter_type.type_info(collation).map_err(|_| {
                        crate::Error::parameter_conversion(
                            parameter_index,
                            declaration.clone(),
                            "metadata_error",
                            "SQL parameter metadata is incompatible with the active session",
                        )
                    })?)
                }
                None => None,
            };
            let flags = if by_ref {
                BitFlags::from_flag(RpcStatus::ByRefValue)
            } else {
                BitFlags::empty()
            };

            rpc_params.push(RpcParam {
                name: Cow::Owned(name),
                flags,
                value,
                type_info,
                parameter_metadata: Some(RpcParameterMetadata {
                    parameter_index,
                    declaration,
                }),
            });
        }

        let request = TokenRpcRequest::new(
            procedure,
            rpc_params,
            self.connection.context().transaction_descriptor(),
        );
        let id = self.connection.context_mut().next_packet_id();
        self.connection.send(PacketHeader::rpc(id), request).await?;

        let token_stream = TokenStream::new(&mut self.connection);
        Ok(ResponseStream::new(token_stream.try_unfold()))
    }

    /// Execute a `BULK INSERT` statement, efficiantly storing a large number of
    /// rows to a specified table. Note: make sure the input row follows the same
    /// schema as the table, otherwise calling `send()` will return an error.
    ///
    /// # Example
    ///
    /// ```
    /// # use tiberius::{Config, IntoRow};
    /// # use tokio_util::compat::TokioAsyncWriteCompatExt;
    /// # use std::env;
    /// # #[tokio::main]
    /// # async fn main() -> Result<(), Box<dyn std::error::Error>> {
    /// # let c_str = env::var("TIBERIUS_TEST_CONNECTION_STRING").unwrap_or(
    /// #     "server=tcp:localhost,1433;integratedSecurity=true;TrustServerCertificate=true".to_owned(),
    /// # );
    /// # let config = Config::from_ado_string(&c_str)?;
    /// # let tcp = tokio::net::TcpStream::connect(config.get_addr()).await?;
    /// # tcp.set_nodelay(true)?;
    /// # let mut client = tiberius::Client::connect(config, tcp.compat_write()).await?;
    /// let create_table = r#"
    ///     CREATE TABLE ##bulk_test (
    ///         id INT IDENTITY PRIMARY KEY,
    ///         val INT NOT NULL
    ///     )
    /// "#;
    ///
    /// client.simple_query(create_table).await?;
    ///
    /// // Start the bulk insert with the client.
    /// let mut req = client.bulk_insert("##bulk_test").await?;
    ///
    /// for i in [0i32, 1i32, 2i32] {
    ///     let row = (i).into_row();
    ///
    ///     // The request will handle flushing to the wire in an optimal way,
    ///     // balancing between memory usage and IO performance.
    ///     req.send(row).await?;
    /// }
    ///
    /// // The request must be finalized.
    /// let res = req.finalize().await?;
    /// assert_eq!(3, res.total());
    /// # Ok(())
    /// # }
    /// ```
    pub async fn bulk_insert<'a>(
        &'a mut self,
        table: &'a str,
    ) -> crate::Result<BulkLoadRequest<'a, S>> {
        // Start the bulk request
        self.connection.flush_stream().await?;

        // retrieve column metadata from server
        let query = format!("SELECT TOP 0 * FROM {}", table);
        let query =
            Self::query_with_reset_baseline(query, self.connection.is_connection_reset_pending());

        let req = BatchRequest::new(query, self.connection.context().transaction_descriptor());

        let id = self.connection.context_mut().next_packet_id();
        self.connection.send(PacketHeader::batch(id), req).await?;

        let token_stream = TokenStream::new(&mut self.connection).try_unfold();

        let columns = token_stream
            .try_fold(None, |mut columns, token| async move {
                if let ReceivedToken::NewResultset(metadata) = token {
                    columns = Some(metadata.columns.clone());
                };

                Ok(columns)
            })
            .await?;

        // now start bulk upload
        let columns: Vec<_> = columns
            .ok_or_else(|| {
                crate::Error::Protocol("expecting column metadata from query but not found".into())
            })?
            .into_iter()
            .filter(|column| column.base.flags.contains(ColumnFlag::Updateable))
            .collect();

        self.connection.flush_stream().await?;
        let col_data = columns.iter().map(|c| format!("{}", c)).join(", ");
        let query = format!("INSERT BULK {} ({})", table, col_data);

        let req = BatchRequest::new(query, self.connection.context().transaction_descriptor());
        let id = self.connection.context_mut().next_packet_id();

        self.connection.send(PacketHeader::batch(id), req).await?;

        let ts = TokenStream::new(&mut self.connection);
        ts.flush_done().await?;

        BulkLoadRequest::new(&mut self.connection, columns)
    }

    /// Starts a bulk upload for an exact ordered subset of table columns.
    ///
    /// `table` and every entry in `columns` are raw SQL Server identifiers,
    /// not SQL fragments. The table accepts one through three dot-separated
    /// qualification parts; every column is treated as one literal identifier
    /// part. The returned request must be finalized after all rows are sent.
    pub async fn bulk_insert_columns<'a>(
        &'a mut self,
        table: &str,
        columns: &[&str],
    ) -> crate::Result<BulkLoadRequest<'a, S>> {
        let target = BulkInsertColumns::new(table, columns)?;

        self.connection.flush_stream().await?;

        let query = Self::query_with_reset_baseline(
            target.metadata_query(),
            self.connection.is_connection_reset_pending(),
        );
        let req = BatchRequest::new(query, self.connection.context().transaction_descriptor());
        let id = self.connection.context_mut().next_packet_id();
        self.connection.send(PacketHeader::batch(id), req).await?;

        let token_stream = TokenStream::new(&mut self.connection).try_unfold();
        let (resultset_count, columns) = token_stream
            .try_fold(
                (0usize, None),
                |(mut resultset_count, mut columns), token| async move {
                    if let ReceivedToken::NewResultset(metadata) = token {
                        resultset_count = resultset_count.checked_add(1).ok_or_else(|| {
                            crate::Error::Protocol("bulk metadata result-set count overflow".into())
                        })?;
                        columns = Some(metadata.columns.clone());
                    }
                    Ok((resultset_count, columns))
                },
            )
            .await?;

        let mut columns = target.validate_metadata(resultset_count, columns)?;
        let query = target.insert_query(&columns)?;
        let target_declarations = columns
            .iter()
            .map(|column| checked_bulk_type_declaration(&column.base.ty))
            .collect::<crate::Result<Vec<_>>>()?;
        normalize_ordered_bulk_wire_metadata(&mut columns, self.connection.context().collation())?;

        self.connection.flush_stream().await?;
        let req = BatchRequest::new(query, self.connection.context().transaction_descriptor());
        let id = self.connection.context_mut().next_packet_id();
        self.connection.send(PacketHeader::batch(id), req).await?;

        let token_stream = TokenStream::new(&mut self.connection);
        token_stream.flush_done().await?;

        BulkLoadRequest::new_with_target_declarations(
            &mut self.connection,
            columns,
            target_declarations,
        )
    }

    /// Closes this database connection explicitly.
    pub async fn close(self) -> crate::Result<()> {
        self.connection.close().await
    }

    fn query_with_reset_baseline<'a>(
        query: impl Into<Cow<'a, str>>,
        reset_connection: bool,
    ) -> Cow<'a, str> {
        let query = query.into();
        if !reset_connection {
            return query;
        }

        let mut prefixed =
            String::with_capacity(Self::RESET_ISOLATION_BASELINE.len() + query.len());
        prefixed.push_str(Self::RESET_ISOLATION_BASELINE);
        prefixed.push_str(query.as_ref());
        Cow::Owned(prefixed)
    }

    async fn complete_pending_connection_reset(&mut self) -> crate::Result<()> {
        if !self.connection.is_connection_reset_pending() {
            return Ok(());
        }

        let request = BatchRequest::new(
            Cow::Borrowed(Self::RESET_ISOLATION_BASELINE),
            self.connection.context().transaction_descriptor(),
        );
        let id = self.connection.context_mut().next_packet_id();
        self.connection
            .send(PacketHeader::batch(id), request)
            .await?;

        let mut tokens = TokenStream::new(&mut self.connection).try_unfold();
        while tokens.try_next().await?.is_some() {}

        Ok(())
    }

    pub(crate) fn rpc_params<'a>(query: impl Into<Cow<'a, str>>) -> Vec<RpcParam<'a>> {
        vec![
            RpcParam {
                name: Cow::Borrowed("stmt"),
                flags: BitFlags::empty(),
                value: ColumnData::String(Some(query.into())),
                type_info: None,
                parameter_metadata: None,
            },
            RpcParam {
                name: Cow::Borrowed("params"),
                flags: BitFlags::empty(),
                value: ColumnData::I32(Some(0)),
                type_info: None,
                parameter_metadata: None,
            },
        ]
    }

    pub(crate) async fn rpc_perform_query<'a, 'b>(
        &'a mut self,
        proc_id: RpcProcId,
        mut rpc_params: Vec<RpcParam<'b>>,
        params: impl Iterator<Item = (ColumnData<'b>, Option<SqlParameterType>)>,
    ) -> crate::Result<()>
    where
        'a: 'b,
    {
        let mut param_str = String::new();

        let collation = self.connection.context().collation();
        let utf8_support = self.connection.context().utf8_support();

        for (i, (param, parameter_type)) in params.enumerate() {
            if i > 0 {
                param_str.push(',')
            }
            param_str.push_str(&format!("@P{} ", i + 1));
            let declaration = parameter_type
                .as_ref()
                .map(SqlParameterType::declaration)
                .unwrap_or_else(|| param.type_name().into_owned());
            param_str.push_str(&declaration);

            let type_info = match parameter_type.as_ref() {
                Some(parameter_type) => {
                    if parameter_type.requires_utf8_support(collation) && !utf8_support {
                        return Err(crate::Error::parameter_conversion(
                            i,
                            declaration,
                            "metadata_error",
                            "SQL Server did not acknowledge UTF-8 parameter support",
                        ));
                    }

                    Some(parameter_type.type_info(collation).map_err(|_| {
                        crate::Error::parameter_conversion(
                            i,
                            declaration.clone(),
                            "metadata_error",
                            "SQL parameter metadata is incompatible with the active session",
                        )
                    })?)
                }
                None => None,
            };

            rpc_params.push(RpcParam {
                name: Cow::Owned(format!("@P{}", i + 1)),
                flags: BitFlags::empty(),
                value: param,
                type_info,
                parameter_metadata: parameter_type.map(|_| RpcParameterMetadata {
                    parameter_index: i,
                    declaration,
                }),
            });
        }

        if let Some(params) = rpc_params.iter_mut().find(|x| x.name == "params") {
            params.value = ColumnData::String(Some(param_str.into()));
        }

        let req = TokenRpcRequest::new(
            proc_id,
            rpc_params,
            self.connection.context().transaction_descriptor(),
        );

        let id = self.connection.context_mut().next_packet_id();
        self.connection.send(PacketHeader::rpc(id), req).await?;

        Ok(())
    }
}

#[cfg(test)]
mod reset_connection_tests {
    use super::Client;
    use std::borrow::Cow;

    #[test]
    fn ordinary_query_is_borrowed_without_rewrite() {
        let query = Client::<futures_util::io::Cursor<Vec<u8>>>::query_with_reset_baseline(
            "SELECT 1", false,
        );
        assert_eq!(query, Cow::Borrowed("SELECT 1"));
    }

    #[test]
    fn reset_query_restores_the_isolation_level_exception() {
        let query = Client::<futures_util::io::Cursor<Vec<u8>>>::query_with_reset_baseline(
            "SELECT 1", true,
        );
        assert_eq!(
            query,
            Cow::<str>::Owned(
                "SET TRANSACTION ISOLATION LEVEL READ COMMITTED;\nSELECT 1".to_owned()
            )
        );
    }
}
