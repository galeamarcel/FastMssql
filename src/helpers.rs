use futures_util::FutureExt;
use pyo3::prelude::*;
use std::future::Future;
use std::panic::AssertUnwindSafe;
use tiberius::{Client, Row};
use tokio::net::TcpStream;
use tokio_util::compat::Compat;

use crate::types::{create_protocol_error, create_sql_error};

type SqlClient = Client<Compat<TcpStream>>;

/// Convert panics in the SQL Server driver into a stable Python exception.
///
/// Tiberius 0.12 still uses `todo!()` for metadata belonging to SQL_VARIANT
/// and UDT-backed SQL Server types. A query supplied by an application must
/// never leak that dependency panic through the Python API.
pub async fn catch_driver_panic<F, T>(future: F) -> Result<T, PyErr>
where
    F: Future<Output = T>,
{
    AssertUnwindSafe(future).catch_unwind().await.map_err(|_| {
        create_protocol_error(
            "SQL Server driver could not decode SQL Server result metadata",
        )
    })
}

/// Return the first SQL keyword while ignoring whitespace and leading comments.
fn first_sql_keyword(mut sql: &str) -> Option<&str> {
    loop {
        sql = sql.trim_start();
        if let Some(comment) = sql.strip_prefix("--") {
            sql = comment.split_once('\n')?.1;
            continue;
        }
        if let Some(comment) = sql.strip_prefix("/*") {
            sql = comment.split_once("*/")?.1;
            continue;
        }
        return sql.split_whitespace().next();
    }
}

/// DDL definitions execute in the caller's batch scope. In particular,
/// `CREATE SCHEMA` cannot run inside `sp_executesql`, and local temporary
/// tables created in that nested scope disappear when the call returns.
pub fn requires_direct_batch(command: &str) -> bool {
    first_sql_keyword(command).is_some_and(|keyword| {
        keyword.eq_ignore_ascii_case("CREATE") || keyword.eq_ignore_ascii_case("ALTER")
    })
}

/// Wrap `Vec<Row>` into a `Py<PyAny>` via `PyQueryStream`.
/// Shared between connection.rs and transaction.rs.
pub fn wrap_query_stream(rows: Vec<Row>) -> PyResult<Py<PyAny>> {
    Python::attach(|py| -> PyResult<Py<PyAny>> {
        let query_stream = crate::types::PyQueryStream::from_tiberius_rows(rows, py)?;
        let py_result = Py::new(py, query_stream)?;
        Ok(py_result.into_any())
    })
}

/// Execute scope-sensitive parameter-free DDL as a direct TDS batch.
///
/// Tiberius's `execute()` always uses `sp_executesql`. That nested scope is
/// incorrect for statements such as `CREATE SCHEMA` and for creating local
/// temporary tables which must survive the call. A follow-up query on the same
/// session preserves the existing `execute()` row-count contract.
pub async fn execute_unparameterized_command(
    client: &mut SqlClient,
    command: &str,
    error_context: &'static str,
) -> PyResult<u64> {
    client
        .simple_query(command)
        .await
        .map_err(|error| create_sql_error(error, error_context))?
        .into_results()
        .await
        .map_err(|error| create_sql_error(error, error_context))?;

    let row = client
        .simple_query("SELECT CAST(@@ROWCOUNT AS BIGINT) AS fastmssql_rows_affected")
        .await
        .map_err(|error| create_sql_error(error, error_context))?
        .into_row()
        .await
        .map_err(|error| create_sql_error(error, error_context))?;

    let affected = match row {
        Some(row) => row
            .try_get::<i64, usize>(0)
            .map_err(|error| create_sql_error(error, error_context))?
            .unwrap_or(0),
        None => 0,
    };

    Ok(u64::try_from(affected).unwrap_or(0))
}

#[cfg(test)]
mod tests {
    use super::requires_direct_batch;

    #[test]
    fn create_and_alter_require_direct_batch_scope() {
        assert!(requires_direct_batch("  CREATE SCHEMA [example]"));
        assert!(requires_direct_batch("\nAlTeR TABLE [t] ADD [v] INT"));
    }

    #[test]
    fn leading_comments_are_ignored_when_classifying_ddl() {
        assert!(requires_direct_batch(
            "-- migration step\nCREATE TABLE #local (id INT)"
        ));
        assert!(requires_direct_batch(
            "/* migration step */ CREATE PROCEDURE [p] AS SELECT 1"
        ));
    }

    #[test]
    fn ordinary_dml_keeps_parameterized_execution_semantics() {
        assert!(!requires_direct_batch("INSERT INTO [t] VALUES (1)"));
        assert!(!requires_direct_batch("SELECT 1"));
        assert!(!requires_direct_batch("-- comment only"));
    }
}
