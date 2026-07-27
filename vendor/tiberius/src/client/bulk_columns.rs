use std::{borrow::Cow, collections::HashSet};

use crate::{
    error::Error,
    tds::{
        codec::{ColumnFlag, FixedLenType, MetaDataColumn, TypeInfo, VarLenContext, VarLenType},
        Collation,
    },
};

const MAX_IDENTIFIER_UTF16: usize = 128;
const MAX_TABLE_PARTS: usize = 3;
const MAX_FINITE_BYTES: usize = 8000;
const MAX_TYPE_LENGTH: usize = 0xffff;

#[derive(Debug)]
pub(super) struct BulkInsertColumns {
    quoted_table: String,
    requested_columns: Vec<String>,
    quoted_columns: Vec<String>,
}

impl BulkInsertColumns {
    pub(super) fn new(table: &str, columns: &[&str]) -> crate::Result<Self> {
        if columns.is_empty() {
            return Err(bulk_input("bulk column list must not be empty"));
        }
        if columns.len() > u16::MAX as usize {
            return Err(bulk_input("bulk column count exceeds the TDS limit"));
        }

        let quoted_table = quote_table(table)?;
        let mut requested_columns = Vec::with_capacity(columns.len());
        let mut quoted_columns = Vec::with_capacity(columns.len());
        let mut unique_columns = HashSet::with_capacity(columns.len());

        for (index, column) in columns.iter().enumerate() {
            if !unique_columns.insert(*column) {
                return Err(bulk_input("bulk column list contains an exact duplicate"));
            }
            quoted_columns.push(quote_identifier_part(column, "bulk column", index, true)?);
            requested_columns.push((*column).to_owned());
        }

        Ok(Self {
            quoted_table,
            requested_columns,
            quoted_columns,
        })
    }

    pub(super) fn metadata_query(&self) -> String {
        format!(
            "SELECT TOP (0) {} FROM {}",
            self.quoted_columns.join(", "),
            self.quoted_table
        )
    }

    pub(super) fn validate_metadata(
        &self,
        resultset_count: usize,
        columns: Option<Vec<MetaDataColumn<'static>>>,
    ) -> crate::Result<Vec<MetaDataColumn<'static>>> {
        if resultset_count != 1 {
            return Err(protocol_error(
                "bulk metadata query returned an unexpected result-set count",
            ));
        }
        let columns = columns
            .ok_or_else(|| protocol_error("bulk metadata query did not return column metadata"))?;
        if columns.len() != self.requested_columns.len() {
            return Err(protocol_error(
                "bulk metadata column count does not match the request",
            ));
        }

        let mut canonical_names = HashSet::with_capacity(columns.len());
        for column in &columns {
            if !canonical_names.insert(column.col_name.as_ref()) {
                return Err(protocol_error(
                    "bulk metadata contains a duplicate canonical column",
                ));
            }
        }

        if self
            .requested_columns
            .iter()
            .zip(&columns)
            .any(|(requested, column)| requested != column.col_name.as_ref())
        {
            return Err(protocol_error(
                "bulk metadata does not preserve requested column order",
            ));
        }

        Ok(columns)
    }

    pub(super) fn insert_query(&self, columns: &[MetaDataColumn<'_>]) -> crate::Result<String> {
        if columns.len() != self.requested_columns.len() {
            return Err(protocol_error(
                "bulk metadata column count changed before request setup",
            ));
        }

        let mut declarations = Vec::with_capacity(columns.len());
        for (index, (requested, column)) in self.requested_columns.iter().zip(columns).enumerate() {
            if requested != column.col_name.as_ref() {
                return Err(protocol_error(
                    "bulk metadata order changed before request setup",
                ));
            }
            validate_writable_flags(column, index)?;
            let quoted_name =
                quote_identifier_part(column.col_name.as_ref(), "bulk metadata", index, false)?;
            let declaration = checked_bulk_type_declaration(&column.base.ty)?;
            declarations.push(format!("{quoted_name} {declaration}"));
        }

        Ok(format!(
            "INSERT BULK {} ({}) WITH (CHECK_CONSTRAINTS, FIRE_TRIGGERS, KEEP_NULLS)",
            self.quoted_table,
            declarations.join(", ")
        ))
    }
}

pub(super) fn normalize_ordered_bulk_wire_metadata(
    columns: &mut [MetaDataColumn<'static>],
    collation: Option<Collation>,
) -> crate::Result<()> {
    if !columns
        .iter()
        .any(|column| matches!(&column.base.ty, TypeInfo::Xml { .. }))
    {
        return Ok(());
    }

    let collation = collation.ok_or_else(|| {
        protocol_error("bulk XML wire metadata requires a negotiated server collation")
    })?;

    for column in columns {
        if matches!(&column.base.ty, TypeInfo::Xml { .. }) {
            column.base.ty = TypeInfo::VarLenSized(VarLenContext::new(
                VarLenType::NVarchar,
                MAX_TYPE_LENGTH,
                Some(collation),
            ));
        }
    }

    Ok(())
}

pub(crate) fn checked_bulk_type_declaration(ty: &TypeInfo) -> crate::Result<String> {
    match ty {
        TypeInfo::FixedLen(ty) => fixed_type_declaration(*ty),
        TypeInfo::VarLenSized(context) => {
            let len = context.len();
            match context.r#type() {
                VarLenType::Guid => exact_length(len, 16, "uniqueidentifier"),
                VarLenType::Intn => match len {
                    1 => Ok("tinyint".to_owned()),
                    2 => Ok("smallint".to_owned()),
                    4 => Ok("int".to_owned()),
                    8 => Ok("bigint".to_owned()),
                    _ => Err(bulk_input("invalid bulk integer metadata width")),
                },
                VarLenType::Bitn => exact_length(len, 1, "bit"),
                VarLenType::Decimaln | VarLenType::Numericn => Err(bulk_input(
                    "bulk numeric metadata is missing precision and scale",
                )),
                VarLenType::Floatn => match len {
                    4 => Ok("real".to_owned()),
                    8 => Ok("float".to_owned()),
                    _ => Err(bulk_input("invalid bulk floating-point metadata width")),
                },
                VarLenType::Money => match len {
                    4 => Ok("smallmoney".to_owned()),
                    8 => Ok("money".to_owned()),
                    _ => Err(bulk_input("invalid bulk money metadata width")),
                },
                VarLenType::Datetimen => match len {
                    4 => Ok("smalldatetime".to_owned()),
                    8 => Ok("datetime".to_owned()),
                    _ => Err(bulk_input("invalid bulk datetime metadata width")),
                },
                #[cfg(feature = "tds73")]
                VarLenType::Daten => exact_length(len, 3, "date"),
                #[cfg(feature = "tds73")]
                VarLenType::Timen => temporal_declaration("time", len),
                #[cfg(feature = "tds73")]
                VarLenType::Datetime2 => temporal_declaration("datetime2", len),
                #[cfg(feature = "tds73")]
                VarLenType::DatetimeOffsetn => temporal_declaration("datetimeoffset", len),
                VarLenType::BigVarBin => variable_byte_declaration("varbinary", len),
                VarLenType::BigVarChar => variable_byte_declaration("varchar", len),
                VarLenType::BigBinary => finite_byte_declaration("binary", len),
                VarLenType::BigChar => finite_byte_declaration("char", len),
                VarLenType::NVarchar => variable_unicode_declaration("nvarchar", len),
                VarLenType::NChar => finite_unicode_declaration("nchar", len),
                VarLenType::Xml => Err(bulk_input(
                    "bulk XML metadata uses an invalid variable-length representation",
                )),
                VarLenType::Udt => Err(bulk_input("bulk UDT metadata is unsupported")),
                VarLenType::Text => Err(bulk_input("bulk TEXT metadata is unsupported")),
                VarLenType::Image => Err(bulk_input("bulk IMAGE metadata is unsupported")),
                VarLenType::NText => Err(bulk_input("bulk NTEXT metadata is unsupported")),
                VarLenType::SSVariant => {
                    Err(bulk_input("bulk SQL_VARIANT metadata is unsupported"))
                }
            }
        }
        TypeInfo::VarLenSizedPrecision {
            ty,
            size,
            precision,
            scale,
        } => numeric_declaration(*ty, *size, *precision, *scale),
        TypeInfo::Xml { .. } => Ok("xml".to_owned()),
    }
}

fn quote_table(table: &str) -> crate::Result<String> {
    let parts: Vec<_> = table.split('.').collect();
    if parts.is_empty() || parts.len() > MAX_TABLE_PARTS {
        return Err(bulk_input(
            "bulk table identifier has an unsupported qualification depth",
        ));
    }

    let mut quoted_parts = Vec::with_capacity(parts.len());
    for (index, part) in parts.into_iter().enumerate() {
        quoted_parts.push(quote_identifier_part(part, "bulk table part", index, true)?);
    }
    Ok(quoted_parts.join("."))
}

fn quote_identifier_part(
    value: &str,
    kind: &'static str,
    index: usize,
    reject_prequoted: bool,
) -> crate::Result<String> {
    if value.is_empty() {
        return Err(Error::BulkInput(
            format!("{kind} at index {index} must not be empty").into(),
        ));
    }
    if value.contains('\0') {
        return Err(Error::BulkInput(
            format!("{kind} at index {index} contains a NUL").into(),
        ));
    }
    if value.encode_utf16().count() > MAX_IDENTIFIER_UTF16 {
        return Err(Error::BulkInput(
            format!("{kind} at index {index} exceeds 128 UTF-16 units").into(),
        ));
    }
    if reject_prequoted && value.starts_with('[') && value.ends_with(']') {
        return Err(Error::BulkInput(
            format!("{kind} at index {index} must be an unquoted identifier").into(),
        ));
    }

    let mut quoted = String::with_capacity(value.len() + 2);
    quoted.push('[');
    for character in value.chars() {
        if character == ']' {
            quoted.push(']');
        }
        quoted.push(character);
    }
    quoted.push(']');
    Ok(quoted)
}

fn validate_writable_flags(column: &MetaDataColumn<'_>, index: usize) -> crate::Result<()> {
    if !column.base.flags.contains(ColumnFlag::Updateable) {
        return Err(Error::BulkInput(
            format!("bulk column at index {index} is not writable").into(),
        ));
    }

    for restricted in [
        ColumnFlag::Identity,
        ColumnFlag::Computed,
        ColumnFlag::FixedLenClrType,
        ColumnFlag::SparseColumnSet,
        ColumnFlag::Encrypted,
        ColumnFlag::Hidden,
    ] {
        if column.base.flags.contains(restricted) {
            return Err(Error::BulkInput(
                format!("bulk column at index {index} has a restricted metadata flag").into(),
            ));
        }
    }
    Ok(())
}

fn fixed_type_declaration(ty: FixedLenType) -> crate::Result<String> {
    match ty {
        FixedLenType::Null => Err(bulk_input("bulk NULL metadata is unsupported")),
        FixedLenType::Int1 => Ok("tinyint".to_owned()),
        FixedLenType::Bit => Ok("bit".to_owned()),
        FixedLenType::Int2 => Ok("smallint".to_owned()),
        FixedLenType::Int4 => Ok("int".to_owned()),
        FixedLenType::Datetime4 => Ok("smalldatetime".to_owned()),
        FixedLenType::Float4 => Ok("real".to_owned()),
        FixedLenType::Money => Ok("money".to_owned()),
        FixedLenType::Datetime => Ok("datetime".to_owned()),
        FixedLenType::Float8 => Ok("float".to_owned()),
        FixedLenType::Money4 => Ok("smallmoney".to_owned()),
        FixedLenType::Int8 => Ok("bigint".to_owned()),
    }
}

fn exact_length(len: usize, expected: usize, declaration: &str) -> crate::Result<String> {
    if len == expected {
        Ok(declaration.to_owned())
    } else {
        Err(bulk_input("invalid bulk metadata width"))
    }
}

fn temporal_declaration(name: &str, scale: usize) -> crate::Result<String> {
    if scale <= 7 {
        Ok(format!("{name}({scale})"))
    } else {
        Err(bulk_input("invalid bulk temporal metadata scale"))
    }
}

fn variable_byte_declaration(name: &str, len: usize) -> crate::Result<String> {
    if len == MAX_TYPE_LENGTH {
        Ok(format!("{name}(max)"))
    } else {
        finite_byte_declaration(name, len)
    }
}

fn finite_byte_declaration(name: &str, len: usize) -> crate::Result<String> {
    if (1..=MAX_FINITE_BYTES).contains(&len) {
        Ok(format!("{name}({len})"))
    } else {
        Err(bulk_input("invalid finite bulk metadata length"))
    }
}

fn variable_unicode_declaration(name: &str, byte_len: usize) -> crate::Result<String> {
    if byte_len == MAX_TYPE_LENGTH {
        Ok(format!("{name}(max)"))
    } else {
        finite_unicode_declaration(name, byte_len)
    }
}

fn finite_unicode_declaration(name: &str, byte_len: usize) -> crate::Result<String> {
    if byte_len == 0 || byte_len > MAX_FINITE_BYTES || !byte_len.is_multiple_of(2) {
        return Err(bulk_input("invalid Unicode bulk metadata length"));
    }
    Ok(format!("{name}({})", byte_len / 2))
}

fn numeric_declaration(
    ty: VarLenType,
    size: usize,
    precision: u8,
    scale: u8,
) -> crate::Result<String> {
    let name = match ty {
        VarLenType::Decimaln => "decimal",
        VarLenType::Numericn => "numeric",
        _ => {
            return Err(bulk_input(
                "bulk precision metadata has an unsupported type",
            ));
        }
    };
    if scale > precision {
        return Err(bulk_input("bulk numeric scale exceeds precision"));
    }
    let expected_size = match precision {
        1..=9 => 5,
        10..=19 => 9,
        20..=28 => 13,
        29..=38 => 17,
        _ => return Err(bulk_input("invalid bulk numeric precision")),
    };
    if !matches!(size, 5 | 9 | 13 | 17) || size < expected_size {
        return Err(bulk_input(format!(
            "invalid bulk numeric storage width {size} for precision {precision}; \
             expected at least {expected_size}"
        )));
    }
    Ok(format!("{name}({precision},{scale})"))
}

fn bulk_input(message: impl Into<Cow<'static, str>>) -> Error {
    Error::BulkInput(message.into())
}

fn protocol_error(message: impl Into<Cow<'static, str>>) -> Error {
    Error::Protocol(message.into())
}
