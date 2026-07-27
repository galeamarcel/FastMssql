use super::{AllHeaderTy, Encode, ALL_HEADERS_LEN_TX};
use crate::{
    tds::codec::{ColumnData, TypeInfo},
    BytesMutWithTypeInfo, Error, Result,
};
use bytes::{BufMut, BytesMut};
use enumflags2::{bitflags, BitFlags};
use std::borrow::Cow;

#[bitflags]
#[repr(u8)]
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum RpcStatus {
    ByRefValue = 1 << 0,
    DefaultValue = 1 << 1,
    // reserved
    Encrypted = 1 << 3,
}

#[bitflags]
#[repr(u16)]
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum RpcOption {
    WithRecomp = 1 << 0,
    NoMeta = 1 << 1,
    ReuseMeta = 1 << 2,
}

#[derive(Debug)]
pub struct TokenRpcRequest<'a> {
    proc_id: RpcProcIdValue<'a>,
    flags: BitFlags<RpcOption>,
    params: Vec<RpcParam<'a>>,
    transaction_desc: [u8; 8],
}

impl<'a> TokenRpcRequest<'a> {
    pub fn new<I>(proc_id: I, params: Vec<RpcParam<'a>>, transaction_desc: [u8; 8]) -> Self
    where
        I: Into<RpcProcIdValue<'a>>,
    {
        Self {
            proc_id: proc_id.into(),
            flags: BitFlags::empty(),
            params,
            transaction_desc,
        }
    }
}

#[derive(Debug)]
pub struct RpcParam<'a> {
    pub name: Cow<'a, str>,
    pub flags: BitFlags<RpcStatus>,
    pub value: ColumnData<'a>,
    pub type_info: Option<TypeInfo>,
    pub(crate) parameter_metadata: Option<RpcParameterMetadata>,
}

#[derive(Debug)]
pub(crate) struct RpcParameterMetadata {
    pub(crate) parameter_index: usize,
    pub(crate) declaration: String,
}

impl RpcParameterMetadata {
    fn wrap_encoding_error(self, error: crate::Error) -> crate::Error {
        let reason = match &error {
            crate::Error::Encoding(_) => "encoding_error",
            crate::Error::BulkInput(message)
                if message.contains("length")
                    || message.contains("large")
                    || message.contains("limit") =>
            {
                "length_overflow"
            }
            crate::Error::BulkInput(_) => "incompatible_metadata",
            crate::Error::Protocol(_) => "metadata_error",
            crate::Error::Conversion(_) => "conversion_failed",
            crate::Error::ParameterConversion { .. } => return error,
            _ => "encoding_failed",
        };
        crate::Error::parameter_conversion(
            self.parameter_index,
            self.declaration,
            reason,
            "SQL parameter value is incompatible with its declared type",
        )
    }
}

/// 2.2.6.6 RPC Request
#[allow(dead_code)]
#[repr(u8)]
#[derive(Clone, Copy, Debug)]
pub enum RpcProcId {
    CursorOpen = 2,
    CursorFetch = 7,
    CursorClose = 9,
    ExecuteSQL = 10,
    Prepare = 11,
    Execute = 12,
    PrepExec = 13,
    Unprepare = 15,
}

#[derive(Debug)]
#[allow(dead_code)]
pub enum RpcProcIdValue<'a> {
    Name(Cow<'a, str>),
    Id(RpcProcId),
}

impl<'a, S> From<S> for RpcProcIdValue<'a>
where
    S: Into<Cow<'a, str>>,
{
    fn from(s: S) -> Self {
        Self::Name(s.into())
    }
}

impl<'a> From<RpcProcId> for RpcProcIdValue<'a> {
    fn from(id: RpcProcId) -> Self {
        Self::Id(id)
    }
}

impl<'a> Encode<BytesMut> for TokenRpcRequest<'a> {
    fn encode(self, dst: &mut BytesMut) -> Result<()> {
        dst.put_u32_le(ALL_HEADERS_LEN_TX as u32);
        dst.put_u32_le(ALL_HEADERS_LEN_TX as u32 - 4);
        dst.put_u16_le(AllHeaderTy::TransactionDescriptor as u16);
        dst.put_slice(&self.transaction_desc);
        dst.put_u32_le(1);

        match self.proc_id {
            RpcProcIdValue::Id(ref id) => {
                let val = (0xffff_u32) | ((*id as u16) as u32) << 16;
                dst.put_u32_le(val);
            }
            RpcProcIdValue::Name(ref name) => {
                let utf16_len = u16::try_from(name.encode_utf16().count()).map_err(|_| {
                    Error::Protocol("RPC procedure name exceeds the US_VARCHAR limit".into())
                })?;
                dst.put_u16_le(utf16_len);
                for code_unit in name.encode_utf16() {
                    dst.put_u16_le(code_unit);
                }
            }
        }

        dst.put_u16_le(self.flags.bits());

        for param in self.params.into_iter() {
            param.encode(dst)?;
        }

        Ok(())
    }
}

impl<'a> Encode<BytesMut> for RpcParam<'a> {
    fn encode(self, dst: &mut BytesMut) -> Result<()> {
        let utf16_len = u8::try_from(self.name.encode_utf16().count()).map_err(|_| {
            Error::Protocol("RPC parameter name exceeds the B_VARCHAR limit".into())
        })?;
        dst.put_u8(utf16_len);

        for codepoint in self.name.encode_utf16() {
            dst.put_u16_le(codepoint);
        }

        dst.put_u8(self.flags.bits());

        let result = if let Some(type_info) = self.type_info {
            type_info.clone().encode(dst)?;
            let mut dst_fi = BytesMutWithTypeInfo::new(dst).with_type_info(&type_info);
            self.value.encode(&mut dst_fi)
        } else {
            let mut dst_fi = BytesMutWithTypeInfo::new(dst);
            self.value.encode(&mut dst_fi)
        };
        if let Err(error) = result {
            return Err(match self.parameter_metadata {
                Some(metadata) => metadata.wrap_encoding_error(error),
                None => error,
            });
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::{
        Encode, RpcParam, RpcParameterMetadata, RpcStatus, TokenRpcRequest, ALL_HEADERS_LEN_TX,
    };
    use crate::{
        tds::{
            codec::{ColumnData, TypeInfo, VarLenContext, VarLenType},
            Collation,
        },
        Error, SqlParameterType, TypeLength,
    };
    use bytes::BytesMut;
    use enumflags2::BitFlags;
    use std::borrow::Cow;

    fn encode_explicit(value: ColumnData<'static>, parameter_type: SqlParameterType) -> Vec<u8> {
        let collation = Collation::new(0x0000_0409, 0);
        let declaration = parameter_type.declaration();
        let parameter = RpcParam {
            name: Cow::Borrowed(""),
            flags: BitFlags::empty(),
            value,
            type_info: Some(parameter_type.type_info(Some(collation)).unwrap()),
            parameter_metadata: Some(RpcParameterMetadata {
                parameter_index: 0,
                declaration,
            }),
        };
        let mut bytes = BytesMut::new();
        parameter.encode(&mut bytes).unwrap();
        bytes.to_vec()
    }

    #[test]
    fn explicit_integer_and_decimal_rpc_bytes_use_declared_storage() {
        assert_eq!(
            encode_explicit(ColumnData::I32(Some(7)), SqlParameterType::int()),
            [0, 0, 0x26, 4, 4, 7, 0, 0, 0]
        );
        assert_eq!(
            encode_explicit(
                ColumnData::Numeric(Some(crate::numeric::Numeric::new_with_scale(123_400, 4))),
                SqlParameterType::decimal(19, 4).unwrap(),
            ),
            [0, 0, 0x6a, 9, 19, 4, 9, 1, 0x08, 0xe2, 0x01, 0, 0, 0, 0, 0,]
        );
    }

    #[test]
    fn explicit_character_and_binary_rpc_bytes_use_length_and_collation() {
        assert_eq!(
            encode_explicit(
                ColumnData::String(Some(Cow::Borrowed("café"))),
                SqlParameterType::varchar(TypeLength::Limited(4)).unwrap(),
            ),
            [0, 0, 0xa7, 4, 0, 0x09, 0x04, 0, 0, 0, 4, 0, b'c', b'a', b'f', 0xe9,]
        );
        assert_eq!(
            encode_explicit(
                ColumnData::String(Some(Cow::Borrowed("😀"))),
                SqlParameterType::nvarchar(TypeLength::Limited(2)).unwrap(),
            ),
            [0, 0, 0xe7, 4, 0, 0x09, 0x04, 0, 0, 0, 4, 0, 0x3d, 0xd8, 0, 0xde,]
        );
        assert_eq!(
            encode_explicit(
                ColumnData::Binary(Some(Cow::Borrowed(&[0x01, 0xff]))),
                SqlParameterType::varbinary(TypeLength::Limited(2)).unwrap(),
            ),
            [0, 0, 0xa5, 2, 0, 2, 0, 0x01, 0xff]
        );
    }

    #[test]
    fn explicit_utf8_varchar_rpc_bytes_use_utf8_collation_and_payload() {
        let parameter_type = SqlParameterType::varchar(TypeLength::Limited(4)).unwrap();
        let declaration = parameter_type.declaration();
        let parameter = RpcParam {
            name: Cow::Borrowed(""),
            flags: BitFlags::empty(),
            value: ColumnData::String(Some(Cow::Borrowed("😀"))),
            type_info: Some(
                parameter_type
                    .type_info(Some(Collation::new(0x0400_0409, 0)))
                    .unwrap(),
            ),
            parameter_metadata: Some(RpcParameterMetadata {
                parameter_index: 0,
                declaration,
            }),
        };
        let mut bytes = BytesMut::new();

        parameter.encode(&mut bytes).unwrap();

        assert_eq!(
            bytes.as_ref(),
            [0, 0, 0xa7, 4, 0, 0x09, 0x04, 0x00, 0x04, 0, 4, 0, 0xf0, 0x9f, 0x98, 0x80,]
        );
    }

    #[cfg(feature = "tds73")]
    #[test]
    fn explicit_temporal_and_typed_null_rpc_bytes_use_declared_scale() {
        let time = crate::time::Time::new(1, 3);
        let date = crate::time::Date::new(2);
        let datetime2 = crate::time::DateTime2::new(date, time);
        let datetimeoffset = crate::time::DateTimeOffset::new(datetime2, 60);

        assert_eq!(
            encode_explicit(ColumnData::Date(Some(date)), SqlParameterType::date()),
            [0, 0, 0x28, 3, 2, 0, 0]
        );
        assert_eq!(
            encode_explicit(
                ColumnData::Time(Some(time)),
                SqlParameterType::time(3).unwrap(),
            ),
            [0, 0, 0x29, 3, 4, 1, 0, 0, 0]
        );
        assert_eq!(
            encode_explicit(
                ColumnData::DateTime2(Some(datetime2)),
                SqlParameterType::date_time2(3).unwrap(),
            ),
            [0, 0, 0x2a, 3, 7, 1, 0, 0, 0, 2, 0, 0]
        );
        assert_eq!(
            encode_explicit(
                ColumnData::DateTimeOffset(Some(datetimeoffset)),
                SqlParameterType::date_time_offset(3).unwrap(),
            ),
            [0, 0, 0x2b, 3, 9, 1, 0, 0, 0, 2, 0, 0, 60, 0]
        );
        assert_eq!(
            encode_explicit(ColumnData::I32(None), SqlParameterType::int()),
            [0, 0, 0x26, 4, 0]
        );
    }

    #[test]
    fn explicit_parameter_encoding_errors_keep_structured_metadata() {
        let parameter = RpcParam {
            name: Cow::Borrowed("@P1"),
            flags: BitFlags::empty(),
            value: ColumnData::String(Some(Cow::Borrowed("too long"))),
            type_info: Some(TypeInfo::VarLenSized(VarLenContext::new(
                VarLenType::BigVarChar,
                1,
                Some(Collation::new(13_632_521, 52)),
            ))),
            parameter_metadata: Some(RpcParameterMetadata {
                parameter_index: 0,
                declaration: "VARCHAR(1)".to_owned(),
            }),
        };
        let mut bytes = BytesMut::new();

        let error = parameter.encode(&mut bytes).unwrap_err();

        assert!(matches!(
            error,
            Error::ParameterConversion {
                parameter_index: 0,
                ref sql_type,
                ref reason,
                ..
            } if sql_type == "VARCHAR(1)" && reason == "length_overflow"
        ));
    }

    #[test]
    fn tib_result_007_named_rpc_uses_us_varchar() {
        let mut bytes = BytesMut::new();
        TokenRpcRequest::new("dbo.fm_rpc", Vec::new(), [0; 8])
            .encode(&mut bytes)
            .expect("a bounded named RPC must encode");

        assert_eq!(
            &bytes[ALL_HEADERS_LEN_TX..],
            &[
                10, 0, b'd', 0, b'b', 0, b'o', 0, b'.', 0, b'f', 0, b'm', 0, b'_', 0, b'r', 0,
                b'p', 0, b'c', 0, 0, 0,
            ]
        );

        let mut procedure_overflow = BytesMut::new();
        let procedure_error =
            TokenRpcRequest::new("p".repeat(u16::MAX as usize + 1), Vec::new(), [0; 8])
                .encode(&mut procedure_overflow)
                .expect_err("a procedure name wider than USHORT must be rejected");
        assert!(matches!(procedure_error, Error::Protocol(_)));

        let mut parameter_overflow = BytesMut::new();
        let parameter_error = RpcParam {
            name: Cow::Owned("p".repeat(u8::MAX as usize + 1)),
            flags: BitFlags::empty(),
            value: ColumnData::I32(Some(1)),
            type_info: None,
            parameter_metadata: None,
        }
        .encode(&mut parameter_overflow)
        .expect_err("a parameter name wider than BYTE must be rejected");
        assert!(matches!(parameter_error, Error::Protocol(_)));
    }

    #[test]
    fn tib_result_008_by_ref_flag_is_output_only() {
        fn encoded_status(flags: BitFlags<RpcStatus>) -> u8 {
            let name = "@value";
            let mut bytes = BytesMut::new();
            RpcParam {
                name: Cow::Borrowed(name),
                flags,
                value: ColumnData::I32(Some(7)),
                type_info: None,
                parameter_metadata: None,
            }
            .encode(&mut bytes)
            .expect("a bounded RPC parameter must encode");

            bytes[1 + name.encode_utf16().count() * 2]
        }

        assert_eq!(encoded_status(BitFlags::empty()), 0);
        assert_eq!(
            encoded_status(BitFlags::from_flag(RpcStatus::ByRefValue)),
            RpcStatus::ByRefValue as u8
        );
    }
}
