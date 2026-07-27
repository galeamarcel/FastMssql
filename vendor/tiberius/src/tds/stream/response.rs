use super::ReceivedToken;
use crate::{
    tds::codec::{
        ColumnFlag, FixedLenType, MetaDataColumn, TokenColMetaData, TokenDone, TokenInfo,
        TokenReturnValue, TypeInfo, VarLenType,
    },
    Column, ColumnData, ColumnType, Error, Row, SqlParameterType,
};
use enumflags2::BitFlags;
use futures_util::{
    ready,
    stream::{BoxStream, Stream, StreamExt},
};
use std::{
    fmt,
    pin::Pin,
    sync::Arc,
    task::{Context, Poll},
};

/// The declared capacity of a character or binary SQL Server value.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ResponseLength {
    /// A finite capacity in bytes, except for Unicode character types where
    /// the value is the number of UTF-16 code units.
    Limited(usize),
    /// A MAX/PLP declaration.
    Max,
}

/// Complete metadata for one SQL Server response column.
#[derive(Clone)]
pub struct ResponseColumn {
    name: String,
    column_type: ColumnType,
    type_name: String,
    nullable: Option<bool>,
    precision: Option<u8>,
    scale: Option<u8>,
    length: Option<ResponseLength>,
}

impl fmt::Debug for ResponseColumn {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ResponseColumn")
            .field("name", &"<redacted>")
            .field("column_type", &self.column_type)
            .field("type_name", &self.type_name)
            .field("nullable", &self.nullable)
            .field("precision", &self.precision)
            .field("scale", &self.scale)
            .field("length", &self.length)
            .finish()
    }
}

impl ResponseColumn {
    fn from_metadata(column: &MetaDataColumn<'_>) -> Self {
        Self {
            name: column.col_name.to_string(),
            column_type: ColumnType::from(&column.base.ty),
            type_name: canonical_type_name(&column.base.ty).to_owned(),
            nullable: nullable_from_flags(column.base.flags),
            precision: precision_from_type_info(&column.base.ty),
            scale: scale_from_type_info(&column.base.ty),
            length: length_from_type_info(&column.base.ty),
        }
    }

    /// The server-provided column name.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// The decoded TDS column type.
    pub fn column_type(&self) -> ColumnType {
        self.column_type
    }

    /// The canonical lowercase SQL Server base type name.
    pub fn type_name(&self) -> &str {
        &self.type_name
    }

    /// Whether the column is nullable, or `None` when SQL Server reports that
    /// nullability is unknown.
    pub fn nullable(&self) -> Option<bool> {
        self.nullable
    }

    /// The declared decimal or numeric precision, when present.
    pub fn precision(&self) -> Option<u8> {
        self.precision
    }

    /// The declared decimal or fractional-seconds scale, when present.
    pub fn scale(&self) -> Option<u8> {
        self.scale
    }

    /// The declared character or binary capacity, when present.
    pub fn length(&self) -> Option<ResponseLength> {
        self.length
    }
}

/// Metadata that starts one result set in a response.
pub struct ResponseMetadata {
    columns: Vec<ResponseColumn>,
    row_columns: Arc<Vec<Column>>,
    result_index: usize,
}

impl fmt::Debug for ResponseMetadata {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ResponseMetadata")
            .field("column_count", &self.columns.len())
            .field("result_index", &self.result_index)
            .finish()
    }
}

impl ResponseMetadata {
    fn from_token(token: &TokenColMetaData<'_>, result_index: usize) -> Self {
        Self {
            columns: token
                .columns
                .iter()
                .map(ResponseColumn::from_metadata)
                .collect(),
            row_columns: Arc::new(token.columns().collect()),
            result_index,
        }
    }

    pub(crate) fn row_columns(&self) -> Arc<Vec<Column>> {
        self.row_columns.clone()
    }

    /// Columns in the order used by subsequent rows.
    pub fn columns(&self) -> &[ResponseColumn] {
        &self.columns
    }

    /// The zero-based result-set index.
    pub fn result_index(&self) -> usize {
        self.result_index
    }
}

/// The wire token that completed a statement or stored procedure.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ResponseDoneKind {
    /// A DONE token.
    Done,
    /// A DONEPROC token.
    DoneProc,
    /// A DONEINPROC token.
    DoneInProc,
}

/// Structural completion information from a DONE-family token.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ResponseDone {
    kind: ResponseDoneKind,
    rows_affected: Option<u64>,
    more_results: bool,
    in_transaction: bool,
    attention_acknowledged: bool,
}

impl ResponseDone {
    fn from_token(kind: ResponseDoneKind, token: TokenDone) -> Self {
        Self {
            kind,
            rows_affected: token.has_count().then_some(token.rows()),
            more_results: token.has_more_results(),
            in_transaction: token.is_in_transaction(),
            attention_acknowledged: token.attention_acknowledged(),
        }
    }

    /// The DONE-family token kind.
    pub fn kind(&self) -> ResponseDoneKind {
        self.kind
    }

    /// The affected-row count when the DONE_COUNT validity bit is set.
    pub fn rows_affected(&self) -> Option<u64> {
        self.rows_affected
    }

    /// Whether another result follows this token.
    pub fn more_results(&self) -> bool {
        self.more_results
    }

    /// Whether SQL Server reports an active transaction.
    pub fn in_transaction(&self) -> bool {
        self.in_transaction
    }

    /// Whether SQL Server acknowledges a cancellation attention.
    pub fn attention_acknowledged(&self) -> bool {
        self.attention_acknowledged
    }
}

/// An informational SQL Server message.
pub struct ResponseInfo {
    number: u32,
    state: u8,
    severity: u8,
    message: String,
    server: String,
    procedure: String,
    line: u32,
}

impl fmt::Debug for ResponseInfo {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ResponseInfo")
            .field("number", &self.number)
            .field("state", &self.state)
            .field("severity", &self.severity)
            .field("message", &"<redacted>")
            .field("server", &"<redacted>")
            .field("procedure", &"<redacted>")
            .field("line", &self.line)
            .finish()
    }
}

impl From<TokenInfo> for ResponseInfo {
    fn from(token: TokenInfo) -> Self {
        Self {
            number: token.number,
            state: token.state,
            severity: token.class,
            message: token.message,
            server: token.server,
            procedure: token.procedure,
            line: token.line,
        }
    }
}

impl ResponseInfo {
    /// The SQL Server message number.
    pub fn number(&self) -> u32 {
        self.number
    }

    /// The message state.
    pub fn state(&self) -> u8 {
        self.state
    }

    /// The message severity.
    pub fn severity(&self) -> u8 {
        self.severity
    }

    /// The message text.
    pub fn message(&self) -> &str {
        &self.message
    }

    /// The reporting server name.
    pub fn server(&self) -> &str {
        &self.server
    }

    /// The reporting stored-procedure name, if any.
    pub fn procedure(&self) -> &str {
        &self.procedure
    }

    /// The reporting line number.
    pub fn line(&self) -> u32 {
        self.line
    }
}

/// One output value returned by a direct RPC request.
pub struct ResponseReturnValue {
    ordinal: u16,
    name: String,
    udf: bool,
    column_type: ColumnType,
    type_name: String,
    nullable: Option<bool>,
    precision: Option<u8>,
    scale: Option<u8>,
    length: Option<ResponseLength>,
    value: ColumnData<'static>,
}

impl fmt::Debug for ResponseReturnValue {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ResponseReturnValue")
            .field("ordinal", &self.ordinal)
            .field("name", &"<redacted>")
            .field("udf", &self.udf)
            .field("column_type", &self.column_type)
            .field("type_name", &self.type_name)
            .field("nullable", &self.nullable)
            .field("precision", &self.precision)
            .field("scale", &self.scale)
            .field("length", &self.length)
            .field("value", &"<redacted>")
            .finish()
    }
}

impl From<TokenReturnValue> for ResponseReturnValue {
    fn from(token: TokenReturnValue) -> Self {
        Self {
            ordinal: token.param_ordinal,
            name: token.param_name,
            udf: token.udf,
            column_type: ColumnType::from(&token.meta.ty),
            type_name: canonical_type_name(&token.meta.ty).to_owned(),
            nullable: nullable_from_flags(token.meta.flags),
            precision: precision_from_type_info(&token.meta.ty),
            scale: scale_from_type_info(&token.meta.ty),
            length: length_from_type_info(&token.meta.ty),
            value: token.value,
        }
    }
}

impl ResponseReturnValue {
    /// The zero-based RPC parameter ordinal.
    pub fn ordinal(&self) -> u16 {
        self.ordinal
    }

    /// The server-returned parameter name.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Whether this value belongs to a user-defined function.
    pub fn is_udf(&self) -> bool {
        self.udf
    }

    /// The decoded TDS value type.
    pub fn column_type(&self) -> ColumnType {
        self.column_type
    }

    /// The canonical lowercase SQL Server base type name.
    pub fn type_name(&self) -> &str {
        &self.type_name
    }

    /// Whether the returned value is nullable, or `None` when unknown.
    pub fn nullable(&self) -> Option<bool> {
        self.nullable
    }

    /// The declared decimal or numeric precision, when present.
    pub fn precision(&self) -> Option<u8> {
        self.precision
    }

    /// The declared decimal or fractional-seconds scale, when present.
    pub fn scale(&self) -> Option<u8> {
        self.scale
    }

    /// The declared character or binary capacity, when present.
    pub fn length(&self) -> Option<ResponseLength> {
        self.length
    }

    /// The returned scalar value.
    pub fn value(&self) -> &ColumnData<'static> {
        &self.value
    }
}

/// An owned event from one complete SQL Server response.
pub enum ResponseEvent {
    /// Result-set metadata.
    Metadata(ResponseMetadata),
    /// One row belonging to the current result set.
    Row(Row),
    /// Statement or procedure completion.
    Done(ResponseDone),
    /// An informational SQL Server message.
    Info(ResponseInfo),
    /// The signed stored-procedure return status.
    ReturnStatus(i32),
    /// One stored-procedure output value.
    ReturnValue(ResponseReturnValue),
}

impl fmt::Debug for ResponseEvent {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Metadata(metadata) => f.debug_tuple("Metadata").field(metadata).finish(),
            Self::Row(row) => f
                .debug_struct("Row")
                .field("column_count", &row.len())
                .field("result_index", &row.result_index())
                .finish(),
            Self::Done(done) => f.debug_tuple("Done").field(done).finish(),
            Self::Info(info) => f.debug_tuple("Info").field(info).finish(),
            Self::ReturnStatus(_) => f.write_str("ReturnStatus(<redacted>)"),
            Self::ReturnValue(value) => f.debug_tuple("ReturnValue").field(value).finish(),
        }
    }
}

/// An owned direct-RPC request parameter.
pub struct RpcParameter {
    name: String,
    value: ColumnData<'static>,
    parameter_type: Option<SqlParameterType>,
    by_ref: bool,
    parameter_index: usize,
}

impl fmt::Debug for RpcParameter {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RpcParameter")
            .field("name", &"<redacted>")
            .field(
                "parameter_type",
                &self
                    .parameter_type
                    .as_ref()
                    .map(SqlParameterType::declaration),
            )
            .field("by_ref", &self.by_ref)
            .field("parameter_index", &self.parameter_index)
            .field("value", &"<redacted>")
            .finish()
    }
}

impl RpcParameter {
    /// Construct an owned direct-RPC parameter.
    pub fn new(
        name: String,
        value: ColumnData<'static>,
        parameter_type: Option<SqlParameterType>,
        by_ref: bool,
        parameter_index: usize,
    ) -> Self {
        Self {
            name,
            value,
            parameter_type,
            by_ref,
            parameter_index,
        }
    }

    pub(crate) fn into_parts(
        self,
    ) -> (
        String,
        ColumnData<'static>,
        Option<SqlParameterType>,
        bool,
        usize,
    ) {
        (
            self.name,
            self.value,
            self.parameter_type,
            self.by_ref,
            self.parameter_index,
        )
    }
}

/// A stream that preserves complete SQL Server response events in wire order.
pub struct ResponseStream<'a> {
    token_stream: BoxStream<'a, crate::Result<ReceivedToken>>,
    columns: Option<Arc<Vec<Column>>>,
    result_set_index: Option<usize>,
}

impl fmt::Debug for ResponseStream<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ResponseStream")
            .field("token_stream", &"BoxStream<ReceivedToken>")
            .field("has_columns", &self.columns.is_some())
            .field("result_set_index", &self.result_set_index)
            .finish()
    }
}

impl<'a> ResponseStream<'a> {
    pub(crate) fn new(token_stream: BoxStream<'a, crate::Result<ReceivedToken>>) -> Self {
        Self {
            token_stream,
            columns: None,
            result_set_index: None,
        }
    }
}

impl Stream for ResponseStream<'_> {
    type Item = crate::Result<ResponseEvent>;

    fn poll_next(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        let this = self.get_mut();

        loop {
            let token = match ready!(this.token_stream.poll_next_unpin(cx)) {
                Some(result) => match result {
                    Ok(token) => token,
                    Err(error) => return Poll::Ready(Some(Err(error))),
                },
                None => return Poll::Ready(None),
            };

            let event = match token {
                ReceivedToken::NewResultset(token) => {
                    let result_index = this.result_set_index.map_or(0, |index| index + 1);
                    this.result_set_index = Some(result_index);

                    let metadata = ResponseMetadata::from_token(token.as_ref(), result_index);
                    this.columns = Some(metadata.row_columns());
                    ResponseEvent::Metadata(metadata)
                }
                ReceivedToken::Row(data) => {
                    let Some(columns) = this.columns.as_ref().cloned() else {
                        return Poll::Ready(Some(Err(Error::Protocol(
                            "received a row before column metadata".into(),
                        ))));
                    };
                    let Some(result_index) = this.result_set_index else {
                        return Poll::Ready(Some(Err(Error::Protocol(
                            "received a row without a result-set index".into(),
                        ))));
                    };

                    ResponseEvent::Row(Row {
                        columns,
                        data,
                        result_index,
                    })
                }
                ReceivedToken::Done(token) => {
                    ResponseEvent::Done(ResponseDone::from_token(ResponseDoneKind::Done, token))
                }
                ReceivedToken::DoneProc(token) => {
                    ResponseEvent::Done(ResponseDone::from_token(ResponseDoneKind::DoneProc, token))
                }
                ReceivedToken::DoneInProc(token) => ResponseEvent::Done(ResponseDone::from_token(
                    ResponseDoneKind::DoneInProc,
                    token,
                )),
                ReceivedToken::Info(token) => ResponseEvent::Info(token.into()),
                ReceivedToken::ReturnStatus(status) => ResponseEvent::ReturnStatus(status),
                ReceivedToken::ReturnValue(token) => ResponseEvent::ReturnValue(token.into()),
                ReceivedToken::Error(_) => continue,
                ReceivedToken::Order(_)
                | ReceivedToken::EnvChange(_)
                | ReceivedToken::LoginAck(_)
                | ReceivedToken::Sspi(_)
                | ReceivedToken::FeatureExtAck(_)
                | ReceivedToken::TableName(_)
                | ReceivedToken::ColInfo(_) => continue,
            };

            return Poll::Ready(Some(Ok(event)));
        }
    }
}

fn nullable_from_flags(flags: BitFlags<ColumnFlag>) -> Option<bool> {
    if flags.contains(ColumnFlag::NullableUnknown) {
        None
    } else {
        Some(flags.contains(ColumnFlag::Nullable))
    }
}

fn canonical_type_name(type_info: &TypeInfo) -> &'static str {
    match type_info {
        TypeInfo::FixedLen(ty) => match ty {
            FixedLenType::Null => "null",
            FixedLenType::Int1 => "tinyint",
            FixedLenType::Bit => "bit",
            FixedLenType::Int2 => "smallint",
            FixedLenType::Int4 => "int",
            FixedLenType::Datetime4 => "smalldatetime",
            FixedLenType::Float4 => "real",
            FixedLenType::Money => "money",
            FixedLenType::Datetime => "datetime",
            FixedLenType::Float8 => "float",
            FixedLenType::Money4 => "smallmoney",
            FixedLenType::Int8 => "bigint",
        },
        TypeInfo::VarLenSized(context) => {
            canonical_var_len_type_name(context.r#type(), context.len())
        }
        TypeInfo::VarLenSizedPrecision { ty, size, .. } => canonical_var_len_type_name(*ty, *size),
        TypeInfo::Xml { .. } => "xml",
    }
}

fn canonical_var_len_type_name(ty: VarLenType, size: usize) -> &'static str {
    match ty {
        VarLenType::Guid => "uniqueidentifier",
        VarLenType::Intn => match size {
            1 => "tinyint",
            2 => "smallint",
            4 => "int",
            8 => "bigint",
            _ => "int",
        },
        VarLenType::Bitn => "bit",
        VarLenType::Decimaln => "decimal",
        VarLenType::Numericn => "numeric",
        VarLenType::Floatn => match size {
            4 => "real",
            _ => "float",
        },
        VarLenType::Money => match size {
            4 => "smallmoney",
            _ => "money",
        },
        VarLenType::Datetimen => match size {
            4 => "smalldatetime",
            _ => "datetime",
        },
        #[cfg(feature = "tds73")]
        VarLenType::Daten => "date",
        #[cfg(feature = "tds73")]
        VarLenType::Timen => "time",
        #[cfg(feature = "tds73")]
        VarLenType::Datetime2 => "datetime2",
        #[cfg(feature = "tds73")]
        VarLenType::DatetimeOffsetn => "datetimeoffset",
        VarLenType::BigVarBin => "varbinary",
        VarLenType::BigVarChar => "varchar",
        VarLenType::BigBinary => "binary",
        VarLenType::BigChar => "char",
        VarLenType::NVarchar => "nvarchar",
        VarLenType::NChar => "nchar",
        VarLenType::Xml => "xml",
        VarLenType::Udt => "udt",
        VarLenType::Text => "text",
        VarLenType::Image => "image",
        VarLenType::NText => "ntext",
        VarLenType::SSVariant => "sql_variant",
    }
}

fn precision_from_type_info(type_info: &TypeInfo) -> Option<u8> {
    match type_info {
        TypeInfo::VarLenSizedPrecision { precision, .. } => Some(*precision),
        TypeInfo::FixedLen(_) | TypeInfo::VarLenSized(_) | TypeInfo::Xml { .. } => None,
    }
}

fn scale_from_type_info(type_info: &TypeInfo) -> Option<u8> {
    match type_info {
        TypeInfo::VarLenSizedPrecision { scale, .. } => Some(*scale),
        TypeInfo::VarLenSized(context) => match context.r#type() {
            #[cfg(feature = "tds73")]
            VarLenType::Timen | VarLenType::Datetime2 | VarLenType::DatetimeOffsetn => {
                u8::try_from(context.len()).ok()
            }
            _ => None,
        },
        TypeInfo::FixedLen(_) | TypeInfo::Xml { .. } => None,
    }
}

fn length_from_type_info(type_info: &TypeInfo) -> Option<ResponseLength> {
    match type_info {
        TypeInfo::VarLenSized(context) => response_length(context.r#type(), context.len()),
        TypeInfo::VarLenSizedPrecision { ty, size, .. } => response_length(*ty, *size),
        TypeInfo::FixedLen(_) | TypeInfo::Xml { .. } => None,
    }
}

fn response_length(ty: VarLenType, size: usize) -> Option<ResponseLength> {
    match ty {
        VarLenType::BigVarBin
        | VarLenType::BigVarChar
        | VarLenType::BigBinary
        | VarLenType::BigChar => Some(if size == usize::from(u16::MAX) {
            ResponseLength::Max
        } else {
            ResponseLength::Limited(size)
        }),
        VarLenType::NVarchar | VarLenType::NChar => Some(if size == usize::from(u16::MAX) {
            ResponseLength::Max
        } else {
            ResponseLength::Limited(size / 2)
        }),
        VarLenType::Text | VarLenType::Image => Some(ResponseLength::Limited(size)),
        VarLenType::NText => Some(ResponseLength::Limited(size / 2)),
        VarLenType::Guid
        | VarLenType::Intn
        | VarLenType::Bitn
        | VarLenType::Decimaln
        | VarLenType::Numericn
        | VarLenType::Floatn
        | VarLenType::Money
        | VarLenType::Datetimen
        | VarLenType::Xml
        | VarLenType::Udt
        | VarLenType::SSVariant => None,
        #[cfg(feature = "tds73")]
        VarLenType::Daten
        | VarLenType::Timen
        | VarLenType::Datetime2
        | VarLenType::DatetimeOffsetn => None,
    }
}

#[cfg(test)]
mod tests {
    use super::{ResponseColumn, ResponseDoneKind, ResponseEvent, ResponseLength, ResponseStream};
    use crate::{
        tds::codec::{
            BaseMetaDataColumn, ColumnFlag, FixedLenType, MetaDataColumn, TokenDone, TokenError,
            TypeInfo, VarLenContext, VarLenType,
        },
        tds::stream::ReceivedToken,
        ColumnType, Error,
    };
    use enumflags2::BitFlags;
    use futures_util::{stream, StreamExt, TryStreamExt};
    use std::borrow::Cow;

    fn response_column(ty: TypeInfo, flags: BitFlags<ColumnFlag>) -> ResponseColumn {
        ResponseColumn::from_metadata(&MetaDataColumn {
            base: BaseMetaDataColumn { flags, ty },
            col_name: Cow::Borrowed("private_name"),
        })
    }

    #[test]
    fn response_type_name_mapping_is_total_for_decoded_metadata() {
        let fixed = [
            (FixedLenType::Null, "null"),
            (FixedLenType::Int1, "tinyint"),
            (FixedLenType::Bit, "bit"),
            (FixedLenType::Int2, "smallint"),
            (FixedLenType::Int4, "int"),
            (FixedLenType::Datetime4, "smalldatetime"),
            (FixedLenType::Float4, "real"),
            (FixedLenType::Money, "money"),
            (FixedLenType::Datetime, "datetime"),
            (FixedLenType::Float8, "float"),
            (FixedLenType::Money4, "smallmoney"),
            (FixedLenType::Int8, "bigint"),
        ];
        for (ty, expected) in fixed {
            let column = response_column(TypeInfo::FixedLen(ty), BitFlags::empty());
            assert_eq!(column.type_name(), expected);
        }

        let variable = [
            (VarLenType::Guid, 16, "uniqueidentifier"),
            (VarLenType::Intn, 1, "tinyint"),
            (VarLenType::Intn, 2, "smallint"),
            (VarLenType::Intn, 4, "int"),
            (VarLenType::Intn, 8, "bigint"),
            (VarLenType::Bitn, 1, "bit"),
            (VarLenType::Floatn, 4, "real"),
            (VarLenType::Floatn, 8, "float"),
            (VarLenType::Money, 4, "smallmoney"),
            (VarLenType::Money, 8, "money"),
            (VarLenType::Datetimen, 4, "smalldatetime"),
            (VarLenType::Datetimen, 8, "datetime"),
            (VarLenType::BigVarBin, 12, "varbinary"),
            (VarLenType::BigVarChar, 12, "varchar"),
            (VarLenType::BigBinary, 12, "binary"),
            (VarLenType::BigChar, 12, "char"),
            (VarLenType::NVarchar, 24, "nvarchar"),
            (VarLenType::NChar, 24, "nchar"),
            (VarLenType::Text, 32, "text"),
            (VarLenType::Image, 32, "image"),
            (VarLenType::NText, 32, "ntext"),
        ];
        for (ty, size, expected) in variable {
            let column = response_column(
                TypeInfo::VarLenSized(VarLenContext::new(ty, size, None)),
                BitFlags::empty(),
            );
            assert_eq!(column.type_name(), expected);
        }

        let numeric = response_column(
            TypeInfo::VarLenSizedPrecision {
                ty: VarLenType::Numericn,
                size: 9,
                precision: 19,
                scale: 4,
            },
            BitFlags::empty(),
        );
        assert_eq!(numeric.type_name(), "numeric");

        #[cfg(feature = "tds73")]
        for (ty, size, expected) in [
            (VarLenType::Daten, 3, "date"),
            (VarLenType::Timen, 3, "time"),
            (VarLenType::Datetime2, 3, "datetime2"),
            (VarLenType::DatetimeOffsetn, 3, "datetimeoffset"),
        ] {
            let column = response_column(
                TypeInfo::VarLenSized(VarLenContext::new(ty, size, None)),
                BitFlags::empty(),
            );
            assert_eq!(column.type_name(), expected);
        }

        let xml = response_column(
            TypeInfo::Xml {
                schema: None,
                size: usize::MAX - 1,
            },
            BitFlags::empty(),
        );
        assert_eq!(xml.type_name(), "xml");
        assert_eq!(xml.column_type(), ColumnType::Xml);
    }

    #[test]
    fn response_metadata_preserves_nullability_and_dimensions() {
        let unknown = response_column(
            TypeInfo::FixedLen(FixedLenType::Int4),
            BitFlags::from_flag(ColumnFlag::NullableUnknown),
        );
        assert_eq!(unknown.nullable(), None);

        let decimal = response_column(
            TypeInfo::VarLenSizedPrecision {
                ty: VarLenType::Decimaln,
                size: 9,
                precision: 19,
                scale: 4,
            },
            BitFlags::from_flag(ColumnFlag::Nullable),
        );
        assert_eq!(decimal.nullable(), Some(true));
        assert_eq!(decimal.precision(), Some(19));
        assert_eq!(decimal.scale(), Some(4));
        assert_eq!(decimal.length(), None);

        let unicode = response_column(
            TypeInfo::VarLenSized(VarLenContext::new(VarLenType::NVarchar, 24, None)),
            BitFlags::empty(),
        );
        assert_eq!(unicode.nullable(), Some(false));
        assert_eq!(unicode.length(), Some(ResponseLength::Limited(12)));

        let maximum = response_column(
            TypeInfo::VarLenSized(VarLenContext::new(
                VarLenType::BigVarBin,
                usize::from(u16::MAX),
                None,
            )),
            BitFlags::empty(),
        );
        assert_eq!(maximum.length(), Some(ResponseLength::Max));

        let small_money = response_column(
            TypeInfo::VarLenSized(VarLenContext::new(VarLenType::Money, 4, None)),
            BitFlags::empty(),
        );
        assert_eq!(small_money.column_type(), ColumnType::Money4);
        assert_eq!(small_money.type_name(), "smallmoney");

        let small_datetime = response_column(
            TypeInfo::VarLenSized(VarLenContext::new(VarLenType::Datetimen, 4, None)),
            BitFlags::empty(),
        );
        assert_eq!(small_datetime.column_type(), ColumnType::Datetime4);
        assert_eq!(small_datetime.type_name(), "smalldatetime");

        #[cfg(feature = "tds73")]
        {
            let time = response_column(
                TypeInfo::VarLenSized(VarLenContext::new(VarLenType::Timen, 3, None)),
                BitFlags::empty(),
            );
            assert_eq!(time.scale(), Some(3));
            assert_eq!(time.length(), None);
        }
    }

    #[async_std::test]
    async fn server_error_token_is_hidden_until_trailing_done_is_drained() {
        let server_error = TokenError {
            code: 50_000,
            state: 1,
            class: 16,
            message: "private server message".to_owned(),
            server: "private server".to_owned(),
            procedure: "private procedure".to_owned(),
            line: 1,
        };
        let tokens = stream::iter(vec![
            Ok(ReceivedToken::Error(server_error.clone())),
            Ok(ReceivedToken::Done(TokenDone::default())),
            Err(Error::Server(server_error)),
        ])
        .boxed();
        let mut response = ResponseStream::new(tokens);

        let done = response
            .try_next()
            .await
            .expect("the trailing DONE must be consumed before the server error")
            .expect("the trailing DONE must remain visible");
        assert!(matches!(
            done,
            ResponseEvent::Done(done) if done.kind() == ResponseDoneKind::Done
        ));

        let error = response
            .try_next()
            .await
            .expect_err("the stored server error must remain terminal");
        assert!(matches!(error, Error::Server(_)));
    }
}
