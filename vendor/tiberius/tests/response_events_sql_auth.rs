use std::{env, panic::AssertUnwindSafe};

use anyhow::{Context, Result};
use futures_util::{FutureExt, TryStreamExt};
use tiberius::{
    AuthMethod, Client, ColumnData, ColumnType, Config, ResponseColumn, ResponseDone,
    ResponseDoneKind, ResponseEvent, ResponseLength, ResponseMetadata, ResponseReturnValue,
    ResponseStream, RpcParameter, SqlParameterType,
};
use tokio::net::TcpStream;
use tokio_util::compat::{Compat, TokioAsyncWriteCompatExt};
use uuid::Uuid;

type SqlAuthClient = Client<Compat<TcpStream>>;

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

async fn collect_events(mut stream: ResponseStream<'_>) -> Result<Vec<ResponseEvent>> {
    let mut events = Vec::new();
    while let Some(event) = stream.try_next().await? {
        events.push(event);
    }
    Ok(events)
}

fn event_kind(event: &ResponseEvent) -> &'static str {
    match event {
        ResponseEvent::Metadata(_) => "metadata",
        ResponseEvent::Row(_) => "row",
        ResponseEvent::Done(_) => "done",
        ResponseEvent::Info(_) => "info",
        ResponseEvent::ReturnStatus(_) => "return_status",
        ResponseEvent::ReturnValue(_) => "return_value",
    }
}

fn only_metadata(events: &[ResponseEvent]) -> Result<&ResponseMetadata> {
    let metadata: Vec<_> = events
        .iter()
        .filter_map(|event| match event {
            ResponseEvent::Metadata(metadata) => Some(metadata),
            _ => None,
        })
        .collect();
    anyhow::ensure!(
        metadata.len() == 1,
        "response must contain exactly one metadata event"
    );
    Ok(metadata[0])
}

fn column<'a>(metadata: &'a ResponseMetadata, name: &str) -> Result<&'a ResponseColumn> {
    metadata
        .columns()
        .iter()
        .find(|column| column.name() == name)
        .with_context(|| format!("missing response column {name}"))
}

fn assert_column(
    metadata: &ResponseMetadata,
    name: &str,
    column_type: ColumnType,
    type_name: &str,
    precision: Option<u8>,
    scale: Option<u8>,
    length: Option<ResponseLength>,
) -> Result<()> {
    let column = column(metadata, name)?;
    anyhow::ensure!(
        column.column_type() == column_type,
        "{name} has an unexpected ColumnType"
    );
    anyhow::ensure!(
        column.type_name() == type_name,
        "{name} has an unexpected canonical type name"
    );
    anyhow::ensure!(
        column.nullable() == Some(true),
        "{name} must preserve nullable metadata"
    );
    anyhow::ensure!(
        column.precision() == precision,
        "{name} has unexpected precision metadata"
    );
    anyhow::ensure!(
        column.scale() == scale,
        "{name} has unexpected scale metadata"
    );
    anyhow::ensure!(
        column.length() == length,
        "{name} has unexpected length metadata"
    );
    Ok(())
}

async fn rpc_fixture_events(
    application_name: &str,
    reset_before_rpc: bool,
) -> Result<(Vec<ResponseEvent>, String)> {
    let mut client = connect_sql_auth(application_name).await?;
    let fixture = format!("fastmssql_response_{}", Uuid::new_v4().simple());
    let qualified = format!("dbo.[{fixture}]");
    let create_sql = format!(
        "
        CREATE PROCEDURE {qualified}
            @value INT OUTPUT
        AS
        BEGIN
            SET NOCOUNT ON;
            RAISERROR(N'fastmssql-response-info', 5, 17);
            SET @value = @value + 1;
            SELECT
                @value AS echoed,
                (
                    SELECT transaction_isolation_level
                    FROM sys.dm_exec_sessions
                    WHERE session_id = @@SPID
                ) AS isolation_level;
            RETURN -7;
        END
        "
    );
    if let Err(error) = drain_batch(&mut client, &create_sql).await {
        if let Err(cleanup_error) = cleanup_batch(
            "FastMssql TIB-RESULT RPC create cleanup",
            &format!("DROP PROCEDURE IF EXISTS {qualified}"),
        )
        .await
        {
            return Err(error.context(format!(
                "RPC fixture creation failed and cleanup also failed: {cleanup_error:#}"
            )));
        }
        return Err(error);
    }

    let response_result = AssertUnwindSafe(async {
        if reset_before_rpc {
            drain_batch(&mut client, "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE").await?;
            client.reset_connection_on_next_request();
        }

        let parameters = vec![RpcParameter::new(
            "@value".to_owned(),
            ColumnData::I32(Some(41)),
            Some(SqlParameterType::int()),
            true,
            0,
        )];
        let stream = client
            .response_rpc(format!("dbo.{fixture}"), parameters)
            .await?;
        collect_events(stream).await
    })
    .catch_unwind()
    .await;

    let cleanup_result = cleanup_batch(
        "FastMssql TIB-RESULT RPC cleanup",
        &format!("DROP PROCEDURE IF EXISTS {qualified}"),
    )
    .await;
    match response_result {
        Ok(Ok(events)) => {
            cleanup_result.context("failed to remove the RPC response fixture")?;
            Ok((events, fixture))
        }
        Ok(Err(error)) => match cleanup_result {
            Ok(()) => Err(error),
            Err(cleanup_error) => Err(error.context(format!(
                "RPC response failed and fixture cleanup also failed: {cleanup_error:#}"
            ))),
        },
        Err(_) => {
            cleanup_result.context("response panicked and RPC fixture cleanup failed")?;
            anyhow::bail!("response processing panicked")
        }
    }
}

fn response_done_events(events: &[ResponseEvent]) -> Vec<&ResponseDone> {
    events
        .iter()
        .filter_map(|event| match event {
            ResponseEvent::Done(done) => Some(done),
            _ => None,
        })
        .collect()
}

fn output_value(events: &[ResponseEvent]) -> Result<&ResponseReturnValue> {
    let values: Vec<_> = events
        .iter()
        .filter_map(|event| match event {
            ResponseEvent::ReturnValue(value) => Some(value),
            _ => None,
        })
        .collect();
    anyhow::ensure!(
        values.len() == 1,
        "RPC response must contain exactly one RETURNVALUE"
    );
    Ok(values[0])
}

#[tokio::test]
async fn tib_result_001_multiple_metadata_indices_and_rows() -> Result<()> {
    let mut client = connect_sql_auth("FastMssql TIB-RESULT-001").await?;
    let events = collect_events(
        client
            .response_batch(
                "SELECT CAST(11 AS INT) AS first_value;
                 SELECT CAST(22 AS INT) AS second_value;",
            )
            .await?,
    )
    .await?;

    let kinds: Vec<_> = events.iter().map(event_kind).collect();
    anyhow::ensure!(
        kinds == ["metadata", "row", "done", "metadata", "row", "done"],
        "multiple result token order changed"
    );

    let metadata_indices: Vec<_> = events
        .iter()
        .filter_map(|event| match event {
            ResponseEvent::Metadata(metadata) => Some(metadata.result_index()),
            _ => None,
        })
        .collect();
    let row_indices: Vec<_> = events
        .iter()
        .filter_map(|event| match event {
            ResponseEvent::Row(row) => Some(row.result_index()),
            _ => None,
        })
        .collect();
    anyhow::ensure!(metadata_indices == [0, 1], "metadata indices changed");
    anyhow::ensure!(row_indices == [0, 1], "row indices changed");

    let row_values: Vec<i32> = events
        .iter()
        .filter_map(|event| match event {
            ResponseEvent::Row(row) => row.get::<i32, _>(0),
            _ => None,
        })
        .collect();
    anyhow::ensure!(row_values == [11, 22], "row values or order changed");

    Ok(())
}

#[tokio::test]
async fn tib_result_002_empty_metadata_is_an_event() -> Result<()> {
    let mut client = connect_sql_auth("FastMssql TIB-RESULT-002").await?;
    let fixture = format!("fastmssql_metadata_{}", Uuid::new_v4().simple());
    let qualified = format!("dbo.[{fixture}]");
    let create_sql = format!(
        "
        CREATE TABLE {qualified} (
            nullable_int TINYINT NULL,
            nullable_float FLOAT NULL,
            nullable_money MONEY NULL,
            nullable_smallmoney SMALLMONEY NULL,
            nullable_uuid UNIQUEIDENTIFIER NULL,
            nullable_varchar VARCHAR(12) NULL,
            nullable_nvarchar NVARCHAR(12) NULL,
            nullable_binary VARBINARY(12) NULL,
            nullable_decimal DECIMAL(19, 4) NULL,
            nullable_date DATE NULL,
            nullable_time TIME(3) NULL,
            nullable_datetime2 DATETIME2(3) NULL,
            nullable_datetimeoffset DATETIMEOFFSET(3) NULL,
            nullable_xml XML NULL
        )
        "
    );
    if let Err(error) = drain_batch(&mut client, &create_sql).await {
        if let Err(cleanup_error) = cleanup_batch(
            "FastMssql TIB-RESULT-002 create cleanup",
            &format!("DROP TABLE IF EXISTS {qualified}"),
        )
        .await
        {
            return Err(error.context(format!(
                "metadata fixture creation failed and cleanup also failed: {cleanup_error:#}"
            )));
        }
        return Err(error);
    }

    let response_result = AssertUnwindSafe(async {
        let sql = format!(
            "
            SELECT
                nullable_int,
                nullable_float,
                nullable_money,
                nullable_smallmoney,
                nullable_uuid,
                nullable_varchar,
                nullable_nvarchar,
                nullable_binary,
                nullable_decimal,
                nullable_date,
                nullable_time,
                nullable_datetime2,
                nullable_datetimeoffset,
                nullable_xml
            FROM {qualified}
            WHERE 1 = 0
            "
        );
        collect_events(client.response_batch(sql).await?).await
    })
    .catch_unwind()
    .await;

    let cleanup_result = cleanup_batch(
        "FastMssql TIB-RESULT-002 cleanup",
        &format!("DROP TABLE IF EXISTS {qualified}"),
    )
    .await;
    let events = match response_result {
        Ok(Ok(events)) => {
            cleanup_result.context("failed to remove the metadata fixture")?;
            events
        }
        Ok(Err(error)) => match cleanup_result {
            Ok(()) => return Err(error),
            Err(cleanup_error) => {
                return Err(error.context(format!(
                    "metadata response failed and fixture cleanup also failed: {cleanup_error:#}"
                )));
            }
        },
        Err(_) => {
            cleanup_result.context("response panicked and metadata fixture cleanup failed")?;
            anyhow::bail!("response processing panicked");
        }
    };

    anyhow::ensure!(
        !events
            .iter()
            .any(|event| matches!(event, ResponseEvent::Row(_))),
        "empty result unexpectedly emitted a row"
    );
    let metadata = only_metadata(&events)?;
    anyhow::ensure!(metadata.result_index() == 0, "first result index changed");
    anyhow::ensure!(metadata.columns().len() == 14, "column count changed");

    assert_column(
        metadata,
        "nullable_int",
        ColumnType::Int1,
        "tinyint",
        None,
        None,
        None,
    )?;
    assert_column(
        metadata,
        "nullable_float",
        ColumnType::Float8,
        "float",
        None,
        None,
        None,
    )?;
    assert_column(
        metadata,
        "nullable_money",
        ColumnType::Money,
        "money",
        None,
        None,
        None,
    )?;
    assert_column(
        metadata,
        "nullable_smallmoney",
        ColumnType::Money4,
        "smallmoney",
        None,
        None,
        None,
    )?;
    assert_column(
        metadata,
        "nullable_uuid",
        ColumnType::Guid,
        "uniqueidentifier",
        None,
        None,
        None,
    )?;
    assert_column(
        metadata,
        "nullable_varchar",
        ColumnType::BigVarChar,
        "varchar",
        None,
        None,
        Some(ResponseLength::Limited(12)),
    )?;
    assert_column(
        metadata,
        "nullable_nvarchar",
        ColumnType::NVarchar,
        "nvarchar",
        None,
        None,
        Some(ResponseLength::Limited(12)),
    )?;
    assert_column(
        metadata,
        "nullable_binary",
        ColumnType::BigVarBin,
        "varbinary",
        None,
        None,
        Some(ResponseLength::Limited(12)),
    )?;
    assert_column(
        metadata,
        "nullable_decimal",
        ColumnType::Decimaln,
        "decimal",
        Some(19),
        Some(4),
        None,
    )?;
    assert_column(
        metadata,
        "nullable_date",
        ColumnType::Daten,
        "date",
        None,
        None,
        None,
    )?;
    assert_column(
        metadata,
        "nullable_time",
        ColumnType::Timen,
        "time",
        None,
        Some(3),
        None,
    )?;
    assert_column(
        metadata,
        "nullable_datetime2",
        ColumnType::Datetime2,
        "datetime2",
        None,
        Some(3),
        None,
    )?;
    assert_column(
        metadata,
        "nullable_datetimeoffset",
        ColumnType::DatetimeOffsetn,
        "datetimeoffset",
        None,
        Some(3),
        None,
    )?;
    assert_column(
        metadata,
        "nullable_xml",
        ColumnType::Xml,
        "xml",
        None,
        None,
        None,
    )?;

    Ok(())
}

#[tokio::test]
async fn tib_result_003_done_count_distinguishes_absent_and_zero() -> Result<()> {
    let mut client = connect_sql_auth("FastMssql TIB-RESULT-003").await?;

    let absent = collect_events(
        client
            .response_batch("SET NOCOUNT ON; SELECT CAST(1 AS INT) AS value WHERE 1 = 0;")
            .await?,
    )
    .await?;
    let absent_done = response_done_events(&absent);
    anyhow::ensure!(
        absent_done
            .last()
            .is_some_and(|done| done.rows_affected().is_none()),
        "DONE_COUNT absence must map to None"
    );

    let zero = collect_events(
        client
            .response_batch("SET NOCOUNT OFF; SELECT CAST(1 AS INT) AS value WHERE 1 = 0;")
            .await?,
    )
    .await?;
    let zero_done = response_done_events(&zero);
    anyhow::ensure!(
        zero_done
            .last()
            .is_some_and(|done| done.rows_affected() == Some(0)),
        "DONE_COUNT with a valid zero must map to Some(0)"
    );

    Ok(())
}

#[tokio::test]
async fn tib_result_004_info_fields_round_trip() -> Result<()> {
    let (events, fixture) = rpc_fixture_events("FastMssql TIB-RESULT-004", false).await?;
    let info: Vec<_> = events
        .iter()
        .filter_map(|event| match event {
            ResponseEvent::Info(info) => Some(info),
            _ => None,
        })
        .collect();
    anyhow::ensure!(info.len() == 1, "RPC must emit exactly one INFO event");
    let info = info[0];

    anyhow::ensure!(info.number() == 50_000, "INFO number changed");
    anyhow::ensure!(info.state() == 17, "INFO state changed");
    anyhow::ensure!(info.severity() == 5, "INFO severity changed");
    anyhow::ensure!(
        info.message() == "fastmssql-response-info",
        "INFO message changed"
    );
    anyhow::ensure!(!info.server().is_empty(), "INFO server must be preserved");
    anyhow::ensure!(
        info.procedure().ends_with(&fixture),
        "INFO procedure must be preserved"
    );
    anyhow::ensure!(info.line() > 0, "INFO line must be preserved");

    Ok(())
}

#[tokio::test]
async fn tib_result_005_return_status_is_signed() -> Result<()> {
    let (events, _) = rpc_fixture_events("FastMssql TIB-RESULT-005", false).await?;
    let statuses: Vec<_> = events
        .iter()
        .filter_map(|event| match event {
            ResponseEvent::ReturnStatus(status) => Some(*status),
            _ => None,
        })
        .collect();

    anyhow::ensure!(statuses == [-7i32], "RETURNSTATUS lost its signed value");
    Ok(())
}

#[tokio::test]
async fn tib_result_006_return_value_keeps_ordinal_name_type_and_value() -> Result<()> {
    let (events, _) = rpc_fixture_events("FastMssql TIB-RESULT-006", true).await?;
    let value = output_value(&events)?;

    anyhow::ensure!(value.ordinal() == 0, "RETURNVALUE ordinal changed");
    anyhow::ensure!(value.name() == "@value", "RETURNVALUE name changed");
    anyhow::ensure!(!value.is_udf(), "stored-procedure output was marked as UDF");
    anyhow::ensure!(
        value.column_type() == ColumnType::Int4,
        "RETURNVALUE ColumnType changed"
    );
    anyhow::ensure!(value.type_name() == "int", "RETURNVALUE type name changed");
    anyhow::ensure!(
        value.nullable() == Some(false),
        "RETURNVALUE nullable metadata changed"
    );
    anyhow::ensure!(
        value.precision().is_none() && value.scale().is_none() && value.length().is_none(),
        "RETURNVALUE integer metadata gained unrelated dimensions"
    );
    anyhow::ensure!(
        matches!(value.value(), ColumnData::I32(Some(42))),
        "RETURNVALUE payload changed"
    );
    let isolation_levels: Vec<_> = events
        .iter()
        .filter_map(|event| match event {
            ResponseEvent::Row(row) => row.get::<i16, _>("isolation_level"),
            _ => None,
        })
        .collect();
    anyhow::ensure!(
        isolation_levels == [2],
        "reset-bearing direct RPC did not restore READ COMMITTED"
    );

    Ok(())
}

#[tokio::test]
async fn tib_result_009_query_stream_adapter_contract_is_unchanged() -> Result<()> {
    let mut client = connect_sql_auth("FastMssql TIB-RESULT-009").await?;
    collect_events(
        client
            .response_batch("SELECT CAST(17 AS INT) AS response_value")
            .await?,
    )
    .await?;

    let results = client
        .simple_query(
            "SELECT CAST(31 AS INT) AS legacy_value;
             SELECT CAST(32 AS INT) AS legacy_value;",
        )
        .await?
        .into_results()
        .await?;
    anyhow::ensure!(results.len() == 2, "legacy result-set count changed");
    anyhow::ensure!(
        results[0][0].get::<i32, _>("legacy_value") == Some(31),
        "legacy first row changed"
    );
    anyhow::ensure!(
        results[1][0].get::<i32, _>("legacy_value") == Some(32),
        "legacy second row changed"
    );
    anyhow::ensure!(
        results[0][0].result_index() == 0 && results[1][0].result_index() == 1,
        "legacy QueryStream result indices changed"
    );

    let done_probe = collect_events(
        client
            .response_batch("SELECT CAST(1 AS INT) AS done_probe")
            .await?,
    )
    .await?;
    let done = response_done_events(&done_probe);
    anyhow::ensure!(
        done.last()
            .is_some_and(|done| done.kind() == ResponseDoneKind::Done),
        "ordinary batch must retain the DONE token kind"
    );

    Ok(())
}

#[tokio::test]
async fn tib_reset_001_immediate_reset_preserves_next_ddl_batch() -> Result<()> {
    let mut client = connect_sql_auth("FastMssql TIB-RESET-001").await?;
    let suffix = Uuid::new_v4().simple();
    let source = format!("dbo.[fastmssql_tib_reset_source_{suffix}]");
    let audit = format!("dbo.[fastmssql_tib_reset_audit_{suffix}]");
    let trigger = format!("dbo.[fastmssql_tib_reset_trigger_{suffix}]");

    let primary_result = AssertUnwindSafe(async {
        drain_batch(
            &mut client,
            &format!(
                "
                CREATE TABLE {source} (id INT PRIMARY KEY);
                CREATE TABLE {audit} (source_id INT NOT NULL);
                "
            ),
        )
        .await?;

        let baseline = client
            .simple_query(
                "
                SELECT
                    @@SPID AS session_id,
                    DB_NAME() AS database_name,
                    @@OPTIONS AS options_mask,
                    @@DATEFIRST AS date_first,
                    @@LANGUAGE AS language_name,
                    @@LOCK_TIMEOUT AS lock_timeout_ms,
                    CONVERT(VARCHAR(256), CONTEXT_INFO(), 2) AS context_info,
                    session_state.transaction_isolation_level,
                    session_state.deadlock_priority,
                    session_state.date_format
                FROM sys.dm_exec_sessions AS session_state
                WHERE session_state.session_id = @@SPID
                ",
            )
            .await?
            .into_row()
            .await?
            .context("baseline session-state query returned no row")?;
        let baseline_session_id = baseline
            .get::<i16, _>("session_id")
            .context("baseline session_id is NULL")?;
        let baseline_database = baseline
            .get::<&str, _>("database_name")
            .context("baseline database_name is NULL")?
            .to_owned();
        let baseline_options = baseline
            .get::<i32, _>("options_mask")
            .context("baseline options_mask is NULL")?;
        let baseline_date_first = baseline
            .get::<u8, _>("date_first")
            .context("baseline date_first is NULL")?;
        let baseline_language = baseline
            .get::<&str, _>("language_name")
            .context("baseline language_name is NULL")?
            .to_owned();
        let baseline_lock_timeout = baseline
            .get::<i32, _>("lock_timeout_ms")
            .context("baseline lock_timeout_ms is NULL")?;
        let baseline_context_info = baseline.get::<&str, _>("context_info").map(str::to_owned);
        let baseline_isolation = baseline
            .get::<i16, _>("transaction_isolation_level")
            .context("baseline transaction_isolation_level is NULL")?;
        let baseline_deadlock_priority = baseline
            .get::<i32, _>("deadlock_priority")
            .context("baseline deadlock_priority is NULL")?;
        let baseline_date_format = baseline
            .get::<&str, _>("date_format")
            .context("baseline date_format is NULL")?
            .to_owned();

        drain_batch(
            &mut client,
            "
            SET LANGUAGE French;
            SET DATEFIRST 3;
            SET DATEFORMAT ymd;
            SET LOCK_TIMEOUT 731;
            SET DEADLOCK_PRIORITY HIGH;
            SET NOCOUNT ON;
            SET XACT_ABORT ON;
            SET ANSI_NULLS OFF;
            SET ANSI_WARNINGS OFF;
            SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;
            USE tempdb;
            SET CONTEXT_INFO 0x464153544D5353514C;
            EXEC sys.sp_set_session_context
                @key = N'fastmssql_tib_reset_001',
                @value = N'contaminated',
                @read_only = 1;
            CREATE TABLE #fastmssql_tib_reset_001 (value INT NOT NULL);
            ",
        )
        .await?;

        client.reset_connection().await?;

        drain_batch(
            &mut client,
            &format!(
                "
                CREATE TRIGGER {trigger}
                ON {source}
                AFTER INSERT
                AS
                BEGIN
                    SET NOCOUNT ON;
                    INSERT INTO {audit} (source_id)
                    SELECT id FROM inserted;
                END
                "
            ),
        )
        .await?;

        let restored = client
            .simple_query(
                "
                SELECT
                    @@SPID AS session_id,
                    DB_NAME() AS database_name,
                    @@OPTIONS AS options_mask,
                    @@DATEFIRST AS date_first,
                    @@LANGUAGE AS language_name,
                    @@LOCK_TIMEOUT AS lock_timeout_ms,
                    CONVERT(VARCHAR(256), CONTEXT_INFO(), 2) AS context_info,
                    CONVERT(
                        NVARCHAR(128),
                        SESSION_CONTEXT(N'fastmssql_tib_reset_001')
                    ) AS session_context_value,
                    OBJECT_ID(N'tempdb..#fastmssql_tib_reset_001')
                        AS temp_object_id,
                    session_state.transaction_isolation_level,
                    session_state.deadlock_priority,
                    session_state.date_format
                FROM sys.dm_exec_sessions AS session_state
                WHERE session_state.session_id = @@SPID
                ",
            )
            .await?
            .into_row()
            .await?
            .context("restored session-state query returned no row")?;
        anyhow::ensure!(
            restored.get::<i16, _>("session_id") == Some(baseline_session_id),
            "immediate reset replaced the physical SQL Server session"
        );
        anyhow::ensure!(
            restored.get::<&str, _>("database_name") == Some(baseline_database.as_str()),
            "immediate reset did not restore the login database"
        );
        anyhow::ensure!(
            restored.get::<i32, _>("options_mask") == Some(baseline_options),
            "immediate reset did not restore @@OPTIONS"
        );
        anyhow::ensure!(
            restored.get::<u8, _>("date_first") == Some(baseline_date_first),
            "immediate reset did not restore @@DATEFIRST"
        );
        anyhow::ensure!(
            restored.get::<&str, _>("language_name") == Some(baseline_language.as_str()),
            "immediate reset did not restore @@LANGUAGE"
        );
        anyhow::ensure!(
            restored.get::<i32, _>("lock_timeout_ms") == Some(baseline_lock_timeout),
            "immediate reset did not restore @@LOCK_TIMEOUT"
        );
        anyhow::ensure!(
            restored.get::<&str, _>("context_info") == baseline_context_info.as_deref(),
            "immediate reset did not clear CONTEXT_INFO"
        );
        anyhow::ensure!(
            restored.get::<&str, _>("session_context_value").is_none(),
            "immediate reset did not clear SESSION_CONTEXT"
        );
        anyhow::ensure!(
            restored.get::<i32, _>("temp_object_id").is_none(),
            "immediate reset did not remove the local temporary table"
        );
        anyhow::ensure!(
            restored.get::<i16, _>("transaction_isolation_level") == Some(baseline_isolation),
            "immediate reset did not restore transaction isolation"
        );
        anyhow::ensure!(
            restored.get::<i32, _>("deadlock_priority") == Some(baseline_deadlock_priority),
            "immediate reset did not restore deadlock priority"
        );
        anyhow::ensure!(
            restored.get::<&str, _>("date_format") == Some(baseline_date_format.as_str()),
            "immediate reset did not restore date format"
        );

        drain_batch(
            &mut client,
            &format!("INSERT INTO {source} (id) VALUES (1)"),
        )
        .await?;
        let side_effect = client
            .simple_query(format!(
                "
                SELECT
                    COUNT(*) AS audit_count,
                    MIN(source_id) AS source_id
                FROM {audit}
                "
            ))
            .await?
            .into_row()
            .await?
            .context("trigger audit query returned no row")?;
        anyhow::ensure!(
            side_effect.get::<i32, _>("audit_count") == Some(1),
            "trigger did not produce exactly one side effect"
        );
        anyhow::ensure!(
            side_effect.get::<i32, _>("source_id") == Some(1),
            "trigger side effect did not preserve the inserted identity"
        );

        Ok::<(), anyhow::Error>(())
    })
    .catch_unwind()
    .await;

    let cleanup_result = cleanup_batch(
        "FastMssql TIB-RESET-001 cleanup",
        &format!(
            "
            DROP TRIGGER IF EXISTS {trigger};
            DROP TABLE IF EXISTS {source};
            DROP TABLE IF EXISTS {audit};
            "
        ),
    )
    .await;

    match primary_result {
        Ok(Ok(())) => cleanup_result.context("failed to remove immediate-reset fixtures"),
        Ok(Err(error)) => match cleanup_result {
            Ok(()) => Err(error),
            Err(cleanup_error) => Err(error.context(format!(
                "immediate reset failed and fixture cleanup also failed: {cleanup_error:#}"
            ))),
        },
        Err(_) => {
            cleanup_result
                .context("response panicked and immediate-reset fixture cleanup failed")?;
            anyhow::bail!("immediate reset response processing panicked")
        }
    }
}
