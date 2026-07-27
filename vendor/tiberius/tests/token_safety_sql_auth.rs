use std::{
    env,
    panic::{self, AssertUnwindSafe, PanicHookInfo},
    sync::atomic::{AtomicBool, Ordering},
};

use anyhow::{Context, Result};
use futures_util::FutureExt;
use tiberius::{error::Error, AuthMethod, Client, Config};
use tokio::net::TcpStream;
use tokio_util::compat::{Compat, TokioAsyncWriteCompatExt};

type SqlAuthClient = Client<Compat<TcpStream>>;
type PanicHook = Box<dyn Fn(&PanicHookInfo<'_>) + Send + Sync + 'static>;

static PANIC_HOOK_INVOKED: AtomicBool = AtomicBool::new(false);

struct ScopedPanicHook {
    previous: Option<PanicHook>,
}

impl ScopedPanicHook {
    fn install() -> Self {
        PANIC_HOOK_INVOKED.store(false, Ordering::SeqCst);
        let previous = panic::take_hook();
        panic::set_hook(Box::new(|_| {
            PANIC_HOOK_INVOKED.store(true, Ordering::SeqCst);
        }));

        Self {
            previous: Some(previous),
        }
    }

    fn restore(mut self) -> bool {
        if let Some(previous) = self.previous.take() {
            panic::set_hook(previous);
        }

        PANIC_HOOK_INVOKED.load(Ordering::SeqCst)
    }
}

impl Drop for ScopedPanicHook {
    fn drop(&mut self) {
        if std::thread::panicking() {
            return;
        }

        if let Some(previous) = self.previous.take() {
            panic::set_hook(previous);
        }
    }
}

fn required_environment(name: &str) -> Result<String> {
    env::var(name).with_context(|| format!("required environment variable {name} is missing"))
}

async fn connect_sql_auth(application_name: &str) -> Result<SqlAuthClient> {
    let host = required_environment("FASTMSSQL_SQL_AUTH_HOST")?;
    let port = required_environment("FASTMSSQL_SQL_AUTH_PORT")?
        .parse::<u16>()
        .context("FASTMSSQL_SQL_AUTH_PORT must be a valid TCP port")?;
    let database = required_environment("FASTMSSQL_SQL_AUTH_DATABASE")?;
    let username = required_environment("FASTMSSQL_SQL_AUTH_OWNER_USER")?;
    let password = required_environment("FASTMSSQL_SQL_AUTH_OWNER_PASSWORD")?;

    let mut config = Config::new();
    config.host(host);
    config.port(port);
    config.database(database);
    config.application_name(application_name);
    config.authentication(AuthMethod::sql_server(username, password));
    config.trust_cert();

    let tcp = TcpStream::connect(config.get_addr())
        .await
        .context("failed to connect to the configured SQL Server endpoint")?;
    tcp.set_nodelay(true)
        .context("failed to enable TCP_NODELAY")?;

    Client::connect(config, tcp.compat_write())
        .await
        .context("SQL-auth login failed")
}

async fn smoke_query(client: &mut SqlAuthClient) -> Result<()> {
    let row = client
        .simple_query("SELECT CAST(1 AS INT) AS value")
        .await?
        .into_row()
        .await?
        .context("smoke query returned no row")?;
    let value: Option<i32> = row.get("value");
    anyhow::ensure!(value == Some(1), "smoke query returned an unexpected value");

    Ok(())
}

#[tokio::test]
async fn tib_safe_001_for_browse_consumes_tabname_and_colinfo() -> Result<()> {
    let mut client = connect_sql_auth("FastMssql TIB-SAFE-001").await?;
    let rows = client
        .simple_query("SELECT TOP (1) name FROM sys.objects FOR BROWSE")
        .await?
        .into_first_result()
        .await?;

    anyhow::ensure!(rows.len() == 1, "FOR BROWSE must return exactly one row");
    let object_name: Option<&str> = rows[0].get("name");
    anyhow::ensure!(
        object_name.is_some_and(|name| !name.is_empty()),
        "FOR BROWSE returned an empty object name"
    );

    smoke_query(&mut client).await
}

#[tokio::test]
async fn tib_safe_002_sql_variant_is_typed_error_without_panic() -> Result<()> {
    let mut client = connect_sql_auth("FastMssql TIB-SAFE-002").await?;
    let panic_hook = ScopedPanicHook::install();

    let outcome = AssertUnwindSafe(async {
        let stream = client
            .simple_query("SELECT CAST(1 AS SQL_VARIANT) AS value")
            .await?;
        let _ = stream.into_results().await?;
        Ok::<(), Error>(())
    })
    .catch_unwind()
    .await;

    let panic_hook_invoked = panic_hook.restore();
    drop(client);
    let mut smoke_client = connect_sql_auth("FastMssql TIB-SAFE-002 smoke").await?;
    smoke_query(&mut smoke_client).await?;

    anyhow::ensure!(
        !panic_hook_invoked,
        "SQL_VARIANT metadata invoked the Rust panic hook"
    );
    let driver_result =
        outcome.map_err(|_| anyhow::anyhow!("SQL_VARIANT metadata caused a Rust panic"))?;
    let error = driver_result.expect_err("SQL_VARIANT unexpectedly decoded successfully");
    let error_text = error.to_string().to_ascii_lowercase();

    anyhow::ensure!(
        matches!(error, Error::Protocol(_)),
        "SQL_VARIANT must return a typed protocol error"
    );
    anyhow::ensure!(
        error_text.contains("ssvariant") || error_text.contains("sql_variant"),
        "SQL_VARIANT error must contain a safe type classification"
    );

    Ok(())
}
