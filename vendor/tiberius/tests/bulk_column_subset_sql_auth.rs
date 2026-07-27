use std::{
    env,
    panic::{self, AssertUnwindSafe, PanicHookInfo},
    sync::atomic::{AtomicBool, Ordering},
};

use anyhow::{Context, Result};
use futures_util::FutureExt;
use tiberius::{error::Error, xml::XmlData, AuthMethod, Client, Config, IntoRow};
use tokio::net::TcpStream;
use tokio_util::compat::{Compat, TokioAsyncWriteCompatExt};
use uuid::Uuid;

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

async fn drain_batch(client: &mut SqlAuthClient, sql: &str) -> Result<()> {
    client.simple_query(sql).await?.into_results().await?;
    Ok(())
}

async fn cleanup_batch(application_name: &str, sql: &str) -> Result<()> {
    let mut cleanup_client = connect_sql_auth(application_name).await?;
    drain_batch(&mut cleanup_client, sql).await
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

async fn settle_with_cleanup(
    primary: Result<()>,
    application_name: &str,
    cleanup_sql: &str,
) -> Result<()> {
    let cleanup = cleanup_batch(application_name, cleanup_sql).await;
    match (primary, cleanup) {
        (Ok(()), Ok(())) => Ok(()),
        (Ok(()), Err(cleanup_error)) => Err(cleanup_error),
        (Err(primary_error), Ok(())) => Err(primary_error),
        (Err(primary_error), Err(cleanup_error)) => {
            Err(primary_error.context(format!("fixture cleanup also failed: {cleanup_error:#}")))
        }
    }
}

fn quote_identifier(value: &str) -> String {
    format!("[{}]", value.replace(']', "]]"))
}

async fn expect_bulk_input(
    client: &mut SqlAuthClient,
    table: &str,
    columns: &[&str],
) -> Result<()> {
    match client.bulk_insert_columns(table, columns).await {
        Err(Error::BulkInput(_)) => Ok(()),
        Err(other) => Err(anyhow::anyhow!(
            "expected a typed bulk-input error, received {other}"
        )),
        Ok(request) => {
            drop(request);
            Err(anyhow::anyhow!(
                "restricted bulk input unexpectedly started a request"
            ))
        }
    }
}

#[tokio::test]
async fn tib_bulk_001_subset_preserves_order_defaults_and_nulls() -> Result<()> {
    let suffix = Uuid::new_v4().simple().to_string();
    let raw_table = format!("fastmssql_tib_bulk_subset_{suffix}");
    let raw_default = format!("df_fastmssql_tib_bulk_subset_{suffix}");
    let qualified = format!("dbo.{}", quote_identifier(&raw_table));
    let table_argument = format!("dbo.{raw_table}");
    let create_sql = format!(
        "
        CREATE TABLE {qualified} (
            id INT IDENTITY PRIMARY KEY,
            first_value INT NOT NULL,
            second_value NVARCHAR(40) NOT NULL,
            defaulted INT NOT NULL
                CONSTRAINT {} DEFAULT (41),
            nullable_value INT NULL
        )
        ",
        quote_identifier(&raw_default)
    );
    let cleanup_sql = format!("DROP TABLE IF EXISTS {qualified}");
    let mut client = connect_sql_auth("FastMssql TIB-BULK-001").await?;
    drain_batch(&mut client, &create_sql).await?;

    let primary = async {
        let mut request = client
            .bulk_insert_columns(
                &table_argument,
                &["second_value", "first_value", "nullable_value"],
            )
            .await?;
        request
            .send(("ordered", 7_i32, Option::<i32>::None).into_row())
            .await?;
        let result = request.finalize().await?;
        anyhow::ensure!(result.total() == 1, "bulk request must report one row");

        let row = client
            .simple_query(format!(
                "
                SELECT first_value, second_value, defaulted, nullable_value
                FROM {qualified}
                "
            ))
            .await?
            .into_row()
            .await?
            .context("subset fixture returned no row")?;
        let first_value: Option<i32> = row.get(0);
        let second_value: Option<&str> = row.get(1);
        let defaulted: Option<i32> = row.get(2);
        let nullable_value: Option<i32> = row.get(3);

        anyhow::ensure!(first_value == Some(7), "first_value order was changed");
        anyhow::ensure!(
            second_value == Some("ordered"),
            "second_value order was changed"
        );
        anyhow::ensure!(defaulted == Some(41), "omitted default was not applied");
        anyhow::ensure!(nullable_value.is_none(), "typed NULL was not retained");
        Ok(())
    }
    .await;

    drop(client);
    settle_with_cleanup(primary, "FastMssql TIB-BULK-001 cleanup", &cleanup_sql).await
}

#[tokio::test]
async fn tib_bulk_002_hostile_identifier_characters_remain_data() -> Result<()> {
    let suffix = Uuid::new_v4().simple().to_string();
    let raw_table = format!("order]bulk_{suffix}");
    let raw_guard = format!("fastmssql_bulk_guard_{suffix}");
    let raw_payload = format!("payload]; DROP TABLE dbo.{raw_guard};--");
    let qualified = format!("dbo.{}", quote_identifier(&raw_table));
    let guard = format!("dbo.{}", quote_identifier(&raw_guard));
    let table_argument = format!("dbo.{raw_table}");
    let create_sql = format!(
        "
        CREATE TABLE {qualified} (
            [select] INT NOT NULL,
            {} NVARCHAR(40) NOT NULL
        );
        CREATE TABLE {guard} (sentinel INT NOT NULL);
        INSERT INTO {guard} (sentinel) VALUES (73);
        ",
        quote_identifier(&raw_payload)
    );
    let cleanup_sql = format!("DROP TABLE IF EXISTS {qualified}; DROP TABLE IF EXISTS {guard}");
    let mut client = connect_sql_auth("FastMssql TIB-BULK-002").await?;
    drain_batch(&mut client, &create_sql).await?;

    let primary = async {
        let mut request = client
            .bulk_insert_columns(&table_argument, &["select", raw_payload.as_str()])
            .await?;
        request.send((11_i32, "safe").into_row()).await?;
        let result = request.finalize().await?;
        anyhow::ensure!(result.total() == 1, "hostile-name bulk inserted no row");

        let target_row = client
            .simple_query(format!(
                "SELECT [select], {} FROM {qualified}",
                quote_identifier(&raw_payload)
            ))
            .await?
            .into_row()
            .await?
            .context("hostile-name target returned no row")?;
        let key: Option<i32> = target_row.get(0);
        let payload: Option<&str> = target_row.get(1);
        anyhow::ensure!(key == Some(11), "reserved column did not round-trip");
        anyhow::ensure!(
            payload == Some("safe"),
            "closing-bracket column did not round-trip"
        );

        let guard_row = client
            .simple_query(format!("SELECT sentinel FROM {guard}"))
            .await?
            .into_row()
            .await?
            .context("guard table was removed or emptied")?;
        let sentinel: Option<i32> = guard_row.get(0);
        anyhow::ensure!(
            sentinel == Some(73),
            "identifier text changed the guard table"
        );
        Ok(())
    }
    .await;

    drop(client);
    settle_with_cleanup(primary, "FastMssql TIB-BULK-002 cleanup", &cleanup_sql).await
}

#[tokio::test]
async fn tib_bulk_003_restricted_columns_are_typed_errors_and_connection_is_reusable() -> Result<()>
{
    let suffix = Uuid::new_v4().simple().to_string();
    let raw_table = format!("fastmssql_tib_bulk_restricted_{suffix}");
    let qualified = format!("dbo.{}", quote_identifier(&raw_table));
    let table_argument = format!("dbo.{raw_table}");
    let create_sql = format!(
        "
        CREATE TABLE {qualified} (
            id INT IDENTITY PRIMARY KEY,
            base_value INT NOT NULL,
            computed_value AS (base_value + 1),
            rv ROWVERSION
        )
        "
    );
    let cleanup_sql = format!("DROP TABLE IF EXISTS {qualified}");
    let mut client = connect_sql_auth("FastMssql TIB-BULK-003").await?;
    drain_batch(&mut client, &create_sql).await?;

    let primary = async {
        for column in ["id", "computed_value", "rv"] {
            expect_bulk_input(&mut client, &table_argument, &[column]).await?;
            smoke_query(&mut client).await?;
        }

        let row = client
            .simple_query(format!("SELECT COUNT_BIG(*) AS row_count FROM {qualified}"))
            .await?
            .into_row()
            .await?
            .context("restricted-column fixture returned no count")?;
        let row_count: Option<i64> = row.get("row_count");
        anyhow::ensure!(
            row_count == Some(0),
            "restricted metadata sent an unexpected row"
        );
        Ok(())
    }
    .await;

    drop(client);
    settle_with_cleanup(primary, "FastMssql TIB-BULK-003 cleanup", &cleanup_sql).await
}

#[tokio::test]
async fn tib_bulk_004_sql_variant_is_protocol_error_without_panic() -> Result<()> {
    let suffix = Uuid::new_v4().simple().to_string();
    let raw_table = format!("fastmssql_tib_bulk_variant_{suffix}");
    let qualified = format!("dbo.{}", quote_identifier(&raw_table));
    let table_argument = format!("dbo.{raw_table}");
    let create_sql = format!("CREATE TABLE {qualified} (value SQL_VARIANT NULL)");
    let cleanup_sql = format!("DROP TABLE IF EXISTS {qualified}");
    let mut client = connect_sql_auth("FastMssql TIB-BULK-004").await?;
    drain_batch(&mut client, &create_sql).await?;
    let panic_hook = ScopedPanicHook::install();

    let outcome = AssertUnwindSafe(client.bulk_insert_columns(&table_argument, &["value"]))
        .catch_unwind()
        .await;
    let panic_hook_invoked = panic_hook.restore();

    let primary = match outcome {
        Err(_) => Err(anyhow::anyhow!(
            "SQL_VARIANT bulk metadata caused a Rust panic"
        )),
        Ok(Err(Error::Protocol(_))) if !panic_hook_invoked => Ok(()),
        Ok(Err(other)) => Err(anyhow::anyhow!(
            "SQL_VARIANT bulk metadata returned the wrong error: {other}"
        )),
        Ok(Ok(request)) => {
            drop(request);
            Err(anyhow::anyhow!(
                "SQL_VARIANT bulk metadata unexpectedly started a request"
            ))
        }
    };
    drop(client);

    let mut smoke_client = connect_sql_auth("FastMssql TIB-BULK-004 smoke").await?;
    smoke_query(&mut smoke_client).await?;
    drop(smoke_client);
    settle_with_cleanup(primary, "FastMssql TIB-BULK-004 cleanup", &cleanup_sql).await
}

#[tokio::test]
async fn tib_bulk_005_malformed_identifiers_are_pre_wire_bulk_input() -> Result<()> {
    let mut client = connect_sql_auth("FastMssql TIB-BULK-005").await?;
    let overlong = "x".repeat(129);
    let invalid_tables = vec![
        "".to_owned(),
        ".table".to_owned(),
        "schema.".to_owned(),
        "db..table".to_owned(),
        "a.b.c.d".to_owned(),
        "nul\0part".to_owned(),
        overlong.clone(),
    ];

    for table in invalid_tables {
        expect_bulk_input(&mut client, &table, &["value"]).await?;
        smoke_query(&mut client).await?;
    }

    for columns in [
        Vec::<&str>::new(),
        vec![""],
        vec!["nul\0column"],
        vec![overlong.as_str()],
        vec!["duplicate", "duplicate"],
    ] {
        expect_bulk_input(&mut client, "dbo.never_used", &columns).await?;
        smoke_query(&mut client).await?;
    }

    let too_many = vec!["value"; (u16::MAX as usize) + 1];
    expect_bulk_input(&mut client, "dbo.never_used", &too_many).await?;
    smoke_query(&mut client).await
}

#[tokio::test]
async fn tib_bulk_006_real_target_declarations_cover_native_input_types() -> Result<()> {
    let suffix = Uuid::new_v4().simple().to_string();
    let raw_table = format!("fastmssql_tib_bulk_types_{suffix}");
    let qualified = format!("dbo.{}", quote_identifier(&raw_table));
    let table_argument = format!("dbo.{raw_table}");
    let create_sql = format!(
        "
        CREATE TABLE {qualified} (
            bit_value BIT NULL,
            tiny_value TINYINT NULL,
            small_value SMALLINT NULL,
            int_value INT NULL,
            big_value BIGINT NULL,
            real_value REAL NULL,
            float_value FLOAT NULL,
            decimal_value DECIMAL(19,4) NULL,
            varchar_value VARCHAR(20) NULL,
            nvarchar_value NVARCHAR(20) NULL,
            binary_value VARBINARY(20) NULL,
            guid_value UNIQUEIDENTIFIER NULL,
            date_value DATE NULL,
            time_value TIME(3) NULL,
            datetime_value DATETIME NULL,
            small_datetime_value SMALLDATETIME NULL,
            datetime2_value DATETIME2(3) NULL,
            offset_value DATETIMEOFFSET(3) NULL,
            xml_value XML NULL
        )
        "
    );
    let cleanup_sql = format!("DROP TABLE IF EXISTS {qualified}");
    let mut client = connect_sql_auth("FastMssql TIB-BULK-006").await?;
    drain_batch(&mut client, &create_sql).await?;

    let primary = async {
        for (column, expected) in [
            ("bit_value", "bit"),
            ("tiny_value", "tinyint"),
            ("small_value", "smallint"),
            ("int_value", "int"),
            ("big_value", "bigint"),
            ("real_value", "real"),
            ("float_value", "float"),
            ("decimal_value", "decimal(19,4)"),
            ("varchar_value", "varchar(20)"),
            ("nvarchar_value", "nvarchar(20)"),
            ("binary_value", "varbinary(20)"),
            ("guid_value", "uniqueidentifier"),
            ("date_value", "date"),
            ("time_value", "time(3)"),
            ("datetime_value", "datetime"),
            ("small_datetime_value", "smalldatetime"),
            ("datetime2_value", "datetime2(3)"),
            ("offset_value", "datetimeoffset(3)"),
            ("xml_value", "xml"),
        ] {
            let request = client
                .bulk_insert_columns(&table_argument, &[column])
                .await
                .with_context(|| format!("native target declaration {expected} was rejected"))?;
            anyhow::ensure!(
                request.column_declarations()? == [expected],
                "native target declaration {expected} changed"
            );
            anyhow::ensure!(
                request
                    .finalize()
                    .await
                    .with_context(|| {
                        format!("empty native target declaration {expected} failed")
                    })?
                    .total()
                    == 0,
                "empty native target declaration request affected rows"
            );
        }
        smoke_query(&mut client).await
    }
    .await;

    drop(client);
    settle_with_cleanup(primary, "FastMssql TIB-BULK-006 cleanup", &cleanup_sql).await
}

#[tokio::test]
async fn tib_bulk_007_xml_target_uses_supported_bcp_wire_type_and_inserts_data() -> Result<()> {
    let suffix = Uuid::new_v4().simple().to_string();
    let raw_table = format!("fastmssql_tib_bulk_xml_{suffix}");
    let qualified = format!("dbo.{}", quote_identifier(&raw_table));
    let table_argument = format!("dbo.{raw_table}");
    let create_sql = format!("CREATE TABLE {qualified} (xml_value XML NULL)");
    let cleanup_sql = format!("DROP TABLE IF EXISTS {qualified}");
    let mut client = connect_sql_auth("FastMssql TIB-BULK-007").await?;
    drain_batch(&mut client, &create_sql).await?;

    let primary = async {
        let mut request = client
            .bulk_insert_columns(&table_argument, &["xml_value"])
            .await?;
        anyhow::ensure!(
            request.column_declarations()? == ["xml"],
            "the target declaration must remain XML"
        );
        request
            .send(XmlData::new("<root attribute=\"value\">text</root>").into_row())
            .await?;
        anyhow::ensure!(
            request.finalize().await?.total() == 1,
            "XML bulk request must report one row"
        );

        let row = client
            .simple_query(format!(
                "SELECT CONVERT(NVARCHAR(MAX), xml_value) AS xml_value FROM {qualified}"
            ))
            .await?
            .into_row()
            .await?
            .context("XML bulk fixture returned no row")?;
        let xml_value: Option<&str> = row.get("xml_value");
        anyhow::ensure!(
            xml_value == Some("<root attribute=\"value\">text</root>"),
            "XML bulk value did not round-trip"
        );
        smoke_query(&mut client).await
    }
    .await;

    drop(client);
    settle_with_cleanup(primary, "FastMssql TIB-BULK-007 cleanup", &cleanup_sql).await
}

#[tokio::test]
async fn tib_bulk_008_safe_options_preserve_database_invariants() -> Result<()> {
    let suffix = Uuid::new_v4().simple().to_string();
    let raw_parent = format!("fastmssql_tib_bulk_parent_{suffix}");
    let raw_target = format!("fastmssql_tib_bulk_safe_{suffix}");
    let raw_audit = format!("fastmssql_tib_bulk_audit_{suffix}");
    let raw_default = format!("df_fastmssql_tib_bulk_safe_{suffix}");
    let raw_check = format!("ck_fastmssql_tib_bulk_safe_{suffix}");
    let raw_foreign_key = format!("fk_fastmssql_tib_bulk_safe_{suffix}");
    let raw_trigger = format!("tr_fastmssql_tib_bulk_safe_{suffix}");
    let parent = format!("dbo.{}", quote_identifier(&raw_parent));
    let target = format!("dbo.{}", quote_identifier(&raw_target));
    let audit = format!("dbo.{}", quote_identifier(&raw_audit));
    let table_argument = format!("dbo.{raw_target}");
    let create_sql = format!(
        "
        CREATE TABLE {parent} (id INT PRIMARY KEY);
        CREATE TABLE {audit} (target_id INT NOT NULL);
        CREATE TABLE {target} (
            id INT PRIMARY KEY,
            parent_id INT NOT NULL,
            explicit_null INT NULL
                CONSTRAINT {} DEFAULT (73),
            checked_value INT NOT NULL
                CONSTRAINT {} CHECK (checked_value > 0),
            CONSTRAINT {} FOREIGN KEY (parent_id) REFERENCES {parent}(id)
        );
        INSERT INTO {parent} (id) VALUES (1);
        ",
        quote_identifier(&raw_default),
        quote_identifier(&raw_check),
        quote_identifier(&raw_foreign_key),
    );
    let trigger_sql = format!(
        "CREATE TRIGGER {} ON {target} AFTER INSERT AS
        BEGIN
            SET NOCOUNT ON;
            INSERT INTO {audit} (target_id) SELECT id FROM inserted;
        END",
        quote_identifier(&raw_trigger),
    );
    let cleanup_sql =
        format!("DROP TABLE IF EXISTS {target}; DROP TABLE IF EXISTS {parent}; DROP TABLE IF EXISTS {audit}");
    let mut client = connect_sql_auth("FastMssql TIB-BULK-008").await?;
    drain_batch(&mut client, &create_sql).await?;
    drain_batch(&mut client, &trigger_sql).await?;

    let primary = async {
        let mut request = client
            .bulk_insert_columns(
                &table_argument,
                &["id", "parent_id", "explicit_null", "checked_value"],
            )
            .await?;
        request
            .send((1_i32, 1_i32, Option::<i32>::None, 1_i32).into_row())
            .await?;
        anyhow::ensure!(
            request.finalize().await?.total() == 1,
            "safe bulk request must report one row"
        );

        let row = client
            .simple_query(format!(
                "
                SELECT
                    (SELECT explicit_null FROM {target} WHERE id = 1)
                        AS explicit_null,
                    (SELECT COUNT(*) FROM {audit}) AS audit_count,
                    (SELECT COUNT(*) FROM sys.foreign_keys
                     WHERE parent_object_id = OBJECT_ID(N'{target}')
                       AND is_not_trusted = 1) AS untrusted_foreign_keys,
                    (SELECT COUNT(*) FROM sys.check_constraints
                     WHERE parent_object_id = OBJECT_ID(N'{target}')
                       AND is_not_trusted = 1) AS untrusted_checks
                "
            ))
            .await?
            .into_row()
            .await?
            .context("safe bulk fixture returned no row")?;
        let explicit_null: Option<i32> = row.get("explicit_null");
        let audit_count: Option<i32> = row.get("audit_count");
        let untrusted_foreign_keys: Option<i32> = row.get("untrusted_foreign_keys");
        let untrusted_checks: Option<i32> = row.get("untrusted_checks");
        anyhow::ensure!(explicit_null.is_none(), "explicit NULL used its default");
        anyhow::ensure!(audit_count == Some(1), "insert trigger did not fire");
        anyhow::ensure!(
            untrusted_foreign_keys == Some(0),
            "foreign key became untrusted"
        );
        anyhow::ensure!(untrusted_checks == Some(0), "check became untrusted");

        for (id, parent_id, checked_value) in [(2_i32, 999_i32, 1_i32), (3, 1, -1)] {
            let invalid = async {
                let mut request = client
                    .bulk_insert_columns(
                        &table_argument,
                        &["id", "parent_id", "explicit_null", "checked_value"],
                    )
                    .await?;
                request
                    .send((id, parent_id, Option::<i32>::None, checked_value).into_row())
                    .await?;
                request.finalize().await
            }
            .await;
            anyhow::ensure!(invalid.is_err(), "invalid constrained row was accepted");
            smoke_query(&mut client).await?;
        }

        let row = client
            .simple_query(format!("SELECT COUNT(*) AS row_count FROM {target}"))
            .await?
            .into_row()
            .await?
            .context("safe bulk fixture returned no count")?;
        let row_count: Option<i32> = row.get("row_count");
        anyhow::ensure!(
            row_count == Some(1),
            "constraint failures changed persisted rows"
        );
        Ok(())
    }
    .await;

    drop(client);
    settle_with_cleanup(primary, "FastMssql TIB-BULK-008 cleanup", &cleanup_sql).await
}
