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
/// The vendored decoder maps its known SQL_VARIANT and UDT metadata paths to
/// typed errors. Keep this boundary as defence in depth: no other dependency
/// decoder panic may unwind through the Python API.
pub async fn catch_driver_panic<F, T>(future: F) -> Result<T, PyErr>
where
    F: Future<Output = T>,
{
    AssertUnwindSafe(future).catch_unwind().await.map_err(|_| {
        create_protocol_error("SQL Server driver could not decode SQL Server result metadata")
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

/// Return whether execution can leave the physical session under a different
/// security principal, even if a later statement in the batch fails.
///
/// SQL Server can put a RESETCONNECTION request into kill state after an
/// unbalanced `EXECUTE AS USER`. Retiring the connection immediately avoids
/// both cross-lease privilege leakage and an error on the next caller. Tokens
/// inside strings, quoted identifiers, and comments are intentionally ignored.
pub fn requires_connection_retirement(sql: &str) -> bool {
    let bytes = sql.as_bytes();
    let mut index = 0;
    let mut previous_was_execute = false;

    while index < bytes.len() {
        match bytes[index] {
            b'\'' | b'"' => {
                let quote = bytes[index];
                index += 1;
                while index < bytes.len() {
                    if bytes[index] == quote {
                        if bytes.get(index + 1) == Some(&quote) {
                            index += 2;
                        } else {
                            index += 1;
                            break;
                        }
                    } else {
                        index += 1;
                    }
                }
                previous_was_execute = false;
            }
            b'[' => {
                index += 1;
                while index < bytes.len() {
                    if bytes[index] == b']' {
                        if bytes.get(index + 1) == Some(&b']') {
                            index += 2;
                        } else {
                            index += 1;
                            break;
                        }
                    } else {
                        index += 1;
                    }
                }
                previous_was_execute = false;
            }
            b'-' if bytes.get(index + 1) == Some(&b'-') => {
                index += 2;
                while index < bytes.len() && bytes[index] != b'\n' {
                    index += 1;
                }
            }
            b'/' if bytes.get(index + 1) == Some(&b'*') => {
                index += 2;
                let mut depth = 1usize;
                while index < bytes.len() && depth > 0 {
                    if bytes[index..].starts_with(b"/*") {
                        depth += 1;
                        index += 2;
                    } else if bytes[index..].starts_with(b"*/") {
                        depth -= 1;
                        index += 2;
                    } else {
                        index += 1;
                    }
                }
            }
            byte if byte.is_ascii_alphanumeric() || byte == b'_' => {
                let start = index;
                index += 1;
                while index < bytes.len()
                    && (bytes[index].is_ascii_alphanumeric() || bytes[index] == b'_')
                {
                    index += 1;
                }
                let token = &sql[start..index];
                if token.eq_ignore_ascii_case("SETUSER")
                    || (previous_was_execute && token.eq_ignore_ascii_case("AS"))
                {
                    return true;
                }
                previous_was_execute =
                    token.eq_ignore_ascii_case("EXECUTE") || token.eq_ignore_ascii_case("EXEC");
            }
            _ => {
                index += 1;
            }
        }
    }

    false
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
    use super::{requires_connection_retirement, requires_direct_batch};

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

    #[test]
    fn session_impersonation_requires_connection_retirement() {
        assert!(requires_connection_retirement(
            "EXECUTE AS USER = N'limited_user'"
        ));
        assert!(requires_connection_retirement(
            "IF 1 = 1 EXEC /* security boundary */ AS LOGIN = 'limited'"
        ));
        assert!(requires_connection_retirement("SETUSER 'limited'"));
    }

    #[test]
    fn quoted_or_commented_impersonation_text_does_not_retire_connection() {
        assert!(!requires_connection_retirement(
            "SELECT N'EXECUTE AS USER = ''limited'''"
        ));
        assert!(!requires_connection_retirement(
            "SELECT [EXECUTE AS USER] FROM [audit]"
        ));
        assert!(!requires_connection_retirement(
            "-- EXECUTE AS USER = 'limited'\nSELECT 1"
        ));
        assert!(!requires_connection_retirement(
            "/* outer /* EXECUTE AS USER = 'limited' */ comment */ SELECT 1"
        ));
        assert!(!requires_connection_retirement(
            "EXEC dbo.do_work @message = N'AS'"
        ));
    }
}
