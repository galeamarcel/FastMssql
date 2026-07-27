use std::fmt::Debug;

use futures_util::{
    io::{AsyncRead, AsyncWrite},
    Stream,
};
use tiberius::{
    validate_bulk_insert_columns, BulkLoadRequest, ResponseDoneKind, ResponseEvent, ResponseLength,
    ResponseStream, RpcParameter,
};

fn assert_send<T: Send>() {}
fn assert_debug<T: Debug>() {}

fn assert_response_stream<'a>()
where
    ResponseStream<'a>: Stream<Item = tiberius::Result<ResponseEvent>> + Send + Debug,
{
}

#[allow(dead_code)]
fn require_public_bulk_declarations<S>(request: &BulkLoadRequest<'_, S>)
where
    S: AsyncRead + AsyncWrite + Unpin + Send,
{
    let _: tiberius::Result<Vec<String>> = request.column_declarations();
}

#[allow(dead_code)]
fn require_public_response_getters(event: &ResponseEvent) {
    match event {
        ResponseEvent::Metadata(metadata) => {
            let _: usize = metadata.result_index();
            for column in metadata.columns() {
                let _: &str = column.name();
                let _: tiberius::ColumnType = column.column_type();
                let _: &str = column.type_name();
                let _: Option<bool> = column.nullable();
                let _: Option<u8> = column.precision();
                let _: Option<u8> = column.scale();
                let _: Option<ResponseLength> = column.length();
            }
        }
        ResponseEvent::Row(row) => {
            let _: usize = row.result_index();
        }
        ResponseEvent::Done(done) => {
            let _: ResponseDoneKind = done.kind();
            let _: Option<u64> = done.rows_affected();
            let _: bool = done.more_results();
            let _: bool = done.in_transaction();
            let _: bool = done.attention_acknowledged();
        }
        ResponseEvent::Info(info) => {
            let _: u32 = info.number();
            let _: u8 = info.state();
            let _: u8 = info.severity();
            let _: &str = info.message();
            let _: &str = info.server();
            let _: &str = info.procedure();
            let _: u32 = info.line();
        }
        ResponseEvent::ReturnStatus(status) => {
            let _: i32 = *status;
        }
        ResponseEvent::ReturnValue(value) => {
            let _: u16 = value.ordinal();
            let _: &str = value.name();
            let _: bool = value.is_udf();
            let _: tiberius::ColumnType = value.column_type();
            let _: &str = value.type_name();
            let _: Option<bool> = value.nullable();
            let _: Option<u8> = value.precision();
            let _: Option<u8> = value.scale();
            let _: Option<ResponseLength> = value.length();
            let _: &tiberius::ColumnData<'static> = value.value();
        }
    }
}

#[test]
fn response_types_are_owned_sendable_and_structurally_debuggable() {
    assert_send::<ResponseEvent>();
    assert_debug::<ResponseEvent>();
    assert_response_stream::<'static>();

    let limited = ResponseLength::Limited(32);
    let copied = limited;
    assert_eq!(limited, copied);
    assert_eq!(ResponseLength::Max, ResponseLength::Max);

    let kinds = [
        ResponseDoneKind::Done,
        ResponseDoneKind::DoneProc,
        ResponseDoneKind::DoneInProc,
    ];
    assert_eq!(kinds.len(), 3);
}

#[test]
fn rpc_parameter_constructor_owns_all_request_inputs() {
    let parameter = RpcParameter::new(
        "@value".to_owned(),
        tiberius::ColumnData::I32(Some(7)),
        Some(tiberius::SqlParameterType::int()),
        true,
        0,
    );

    assert_send::<RpcParameter>();
    drop(parameter);
}

#[test]
fn native_bulk_metadata_bridge_is_public_and_validates_without_io() {
    validate_bulk_insert_columns("database.schema.target", &["first", "second"])
        .expect("valid raw identifiers must pass without a client or wire I/O");

    assert!(validate_bulk_insert_columns("[dbo].[target]", &["value"]).is_err());
    assert!(validate_bulk_insert_columns("dbo.target", &[]).is_err());
}
