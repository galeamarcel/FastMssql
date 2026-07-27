use std::borrow::Cow;

use crate::{
    tds::{
        codec::{TypeInfo, TypeLength, VarLenContext, VarLenType},
        Collation,
    },
    Error,
};

/// The closed set of SQL Server scalar types that can be declared for an RPC
/// parameter.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SqlParameterKind {
    /// `bit`.
    Bit,
    /// `tinyint`.
    TinyInt,
    /// `smallint`.
    SmallInt,
    /// `int`.
    Int,
    /// `bigint`.
    BigInt,
    /// `real`.
    Real,
    /// `float(n)`.
    Float,
    /// `decimal(p,s)`.
    Decimal,
    /// `numeric(p,s)`.
    Numeric,
    /// `char(n)`.
    Char,
    /// `varchar(n|max)`.
    VarChar,
    /// `nchar(n)`.
    NChar,
    /// `nvarchar(n|max)`.
    NVarChar,
    /// `binary(n)`.
    Binary,
    /// `varbinary(n|max)`.
    VarBinary,
    /// `uniqueidentifier`.
    UniqueIdentifier,
    /// `date`.
    Date,
    /// `time(s)`.
    Time,
    /// `datetime`.
    DateTime,
    /// `smalldatetime`.
    SmallDateTime,
    /// `datetime2(s)`.
    DateTime2,
    /// `datetimeoffset(s)`.
    DateTimeOffset,
    /// `xml`.
    Xml,
}

/// A validated SQL Server RPC parameter declaration.
///
/// Values can only be created through the type-specific constructors. This
/// keeps untrusted SQL declaration text out of the TDS request builder.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SqlParameterType {
    kind: SqlParameterKind,
    precision: Option<u8>,
    scale: Option<u8>,
    length: Option<TypeLength>,
}

impl SqlParameterType {
    fn scalar(kind: SqlParameterKind) -> Self {
        Self {
            kind,
            precision: None,
            scale: None,
            length: None,
        }
    }

    /// Construct `bit`.
    pub fn bit() -> Self {
        Self::scalar(SqlParameterKind::Bit)
    }

    /// Construct `tinyint`.
    pub fn tiny_int() -> Self {
        Self::scalar(SqlParameterKind::TinyInt)
    }

    /// Construct `smallint`.
    pub fn small_int() -> Self {
        Self::scalar(SqlParameterKind::SmallInt)
    }

    /// Construct `int`.
    pub fn int() -> Self {
        Self::scalar(SqlParameterKind::Int)
    }

    /// Construct `bigint`.
    pub fn big_int() -> Self {
        Self::scalar(SqlParameterKind::BigInt)
    }

    /// Construct `real`.
    pub fn real() -> Self {
        Self::scalar(SqlParameterKind::Real)
    }

    /// Construct `float(precision)`.
    pub fn float(precision: u8) -> crate::Result<Self> {
        if !(1..=53).contains(&precision) {
            return Err(Self::invalid_metadata());
        }

        Ok(Self {
            kind: SqlParameterKind::Float,
            precision: Some(precision),
            scale: None,
            length: None,
        })
    }

    /// Construct `decimal(precision, scale)`.
    pub fn decimal(precision: u8, scale: u8) -> crate::Result<Self> {
        Self::decimal_like(SqlParameterKind::Decimal, precision, scale)
    }

    /// Construct `numeric(precision, scale)`.
    pub fn numeric(precision: u8, scale: u8) -> crate::Result<Self> {
        Self::decimal_like(SqlParameterKind::Numeric, precision, scale)
    }

    fn decimal_like(kind: SqlParameterKind, precision: u8, scale: u8) -> crate::Result<Self> {
        if !(1..=38).contains(&precision) || scale > precision {
            return Err(Self::invalid_metadata());
        }

        Ok(Self {
            kind,
            precision: Some(precision),
            scale: Some(scale),
            length: None,
        })
    }

    /// Construct `char(length)`.
    pub fn char(length: u16) -> crate::Result<Self> {
        Self::fixed_length(SqlParameterKind::Char, length, 8000)
    }

    /// Construct `varchar(length|max)`.
    pub fn varchar(length: TypeLength) -> crate::Result<Self> {
        Self::variable_length(SqlParameterKind::VarChar, length, 8000)
    }

    /// Construct `nchar(length)`.
    pub fn nchar(length: u16) -> crate::Result<Self> {
        Self::fixed_length(SqlParameterKind::NChar, length, 4000)
    }

    /// Construct `nvarchar(length|max)`.
    pub fn nvarchar(length: TypeLength) -> crate::Result<Self> {
        Self::variable_length(SqlParameterKind::NVarChar, length, 4000)
    }

    /// Construct `binary(length)`.
    pub fn binary(length: u16) -> crate::Result<Self> {
        Self::fixed_length(SqlParameterKind::Binary, length, 8000)
    }

    /// Construct `varbinary(length|max)`.
    pub fn varbinary(length: TypeLength) -> crate::Result<Self> {
        Self::variable_length(SqlParameterKind::VarBinary, length, 8000)
    }

    fn fixed_length(kind: SqlParameterKind, length: u16, maximum: u16) -> crate::Result<Self> {
        if length == 0 || length > maximum {
            return Err(Self::invalid_metadata());
        }

        Ok(Self {
            kind,
            precision: None,
            scale: None,
            length: Some(TypeLength::Limited(length)),
        })
    }

    fn variable_length(
        kind: SqlParameterKind,
        length: TypeLength,
        maximum: u16,
    ) -> crate::Result<Self> {
        if matches!(length, TypeLength::Limited(0))
            || matches!(length, TypeLength::Limited(value) if value > maximum)
        {
            return Err(Self::invalid_metadata());
        }

        Ok(Self {
            kind,
            precision: None,
            scale: None,
            length: Some(length),
        })
    }

    /// Construct `uniqueidentifier`.
    pub fn unique_identifier() -> Self {
        Self::scalar(SqlParameterKind::UniqueIdentifier)
    }

    /// Construct `date`.
    pub fn date() -> Self {
        Self::scalar(SqlParameterKind::Date)
    }

    /// Construct `time(scale)`.
    pub fn time(scale: u8) -> crate::Result<Self> {
        Self::temporal_scale(SqlParameterKind::Time, scale)
    }

    /// Construct `datetime`.
    pub fn date_time() -> Self {
        Self::scalar(SqlParameterKind::DateTime)
    }

    /// Construct `smalldatetime`.
    pub fn small_date_time() -> Self {
        Self::scalar(SqlParameterKind::SmallDateTime)
    }

    /// Construct `datetime2(scale)`.
    pub fn date_time2(scale: u8) -> crate::Result<Self> {
        Self::temporal_scale(SqlParameterKind::DateTime2, scale)
    }

    /// Construct `datetimeoffset(scale)`.
    pub fn date_time_offset(scale: u8) -> crate::Result<Self> {
        Self::temporal_scale(SqlParameterKind::DateTimeOffset, scale)
    }

    fn temporal_scale(kind: SqlParameterKind, scale: u8) -> crate::Result<Self> {
        if scale > 7 {
            return Err(Self::invalid_metadata());
        }

        Ok(Self {
            kind,
            precision: None,
            scale: Some(scale),
            length: None,
        })
    }

    /// Construct `xml`.
    pub fn xml() -> Self {
        Self::scalar(SqlParameterKind::Xml)
    }

    /// Return the declaration's scalar kind.
    pub fn kind(&self) -> SqlParameterKind {
        self.kind
    }

    /// Return the declared numeric or floating-point precision.
    pub fn precision(&self) -> Option<u8> {
        self.precision
    }

    /// Return the declared numeric or temporal scale.
    pub fn scale(&self) -> Option<u8> {
        self.scale
    }

    /// Return the declared character or binary length.
    pub fn length(&self) -> Option<TypeLength> {
        self.length
    }

    /// Return the canonical SQL declaration used by `sp_executesql`.
    pub fn declaration(&self) -> String {
        match self.kind {
            SqlParameterKind::Bit => "BIT".to_owned(),
            SqlParameterKind::TinyInt => "TINYINT".to_owned(),
            SqlParameterKind::SmallInt => "SMALLINT".to_owned(),
            SqlParameterKind::Int => "INT".to_owned(),
            SqlParameterKind::BigInt => "BIGINT".to_owned(),
            SqlParameterKind::Real => "REAL".to_owned(),
            SqlParameterKind::Float => format!("FLOAT({})", self.precision.unwrap_or(53)),
            SqlParameterKind::Decimal => {
                format!(
                    "DECIMAL({},{})",
                    self.precision.unwrap_or(18),
                    self.scale.unwrap_or(0)
                )
            }
            SqlParameterKind::Numeric => {
                format!(
                    "NUMERIC({},{})",
                    self.precision.unwrap_or(18),
                    self.scale.unwrap_or(0)
                )
            }
            SqlParameterKind::Char => Self::format_length("CHAR", self.length),
            SqlParameterKind::VarChar => Self::format_length("VARCHAR", self.length),
            SqlParameterKind::NChar => Self::format_length("NCHAR", self.length),
            SqlParameterKind::NVarChar => Self::format_length("NVARCHAR", self.length),
            SqlParameterKind::Binary => Self::format_length("BINARY", self.length),
            SqlParameterKind::VarBinary => Self::format_length("VARBINARY", self.length),
            SqlParameterKind::UniqueIdentifier => "UNIQUEIDENTIFIER".to_owned(),
            SqlParameterKind::Date => "DATE".to_owned(),
            SqlParameterKind::Time => format!("TIME({})", self.scale.unwrap_or(7)),
            SqlParameterKind::DateTime => "DATETIME".to_owned(),
            SqlParameterKind::SmallDateTime => "SMALLDATETIME".to_owned(),
            SqlParameterKind::DateTime2 => format!("DATETIME2({})", self.scale.unwrap_or(7)),
            SqlParameterKind::DateTimeOffset => {
                format!("DATETIMEOFFSET({})", self.scale.unwrap_or(7))
            }
            SqlParameterKind::Xml => "XML".to_owned(),
        }
    }

    fn format_length(name: &str, length: Option<TypeLength>) -> String {
        match length.unwrap_or(TypeLength::Max) {
            TypeLength::Limited(length) => format!("{name}({length})"),
            TypeLength::Max => format!("{name}(MAX)"),
        }
    }

    pub(crate) fn type_info(&self, collation: Option<Collation>) -> crate::Result<TypeInfo> {
        let type_info = match self.kind {
            SqlParameterKind::Bit => Self::var_len(VarLenType::Bitn, 1, None),
            SqlParameterKind::TinyInt => Self::var_len(VarLenType::Intn, 1, None),
            SqlParameterKind::SmallInt => Self::var_len(VarLenType::Intn, 2, None),
            SqlParameterKind::Int => Self::var_len(VarLenType::Intn, 4, None),
            SqlParameterKind::BigInt => Self::var_len(VarLenType::Intn, 8, None),
            SqlParameterKind::Real => Self::var_len(VarLenType::Floatn, 4, None),
            SqlParameterKind::Float => {
                let size = if self.precision.unwrap_or(53) <= 24 {
                    4
                } else {
                    8
                };
                Self::var_len(VarLenType::Floatn, size, None)
            }
            SqlParameterKind::Decimal | SqlParameterKind::Numeric => {
                let precision = self.precision.ok_or_else(Self::invalid_metadata)?;
                let scale = self.scale.ok_or_else(Self::invalid_metadata)?;
                let size = match precision {
                    1..=9 => 5,
                    10..=19 => 9,
                    20..=28 => 13,
                    29..=38 => 17,
                    _ => return Err(Self::invalid_metadata()),
                };
                TypeInfo::VarLenSizedPrecision {
                    ty: if self.kind == SqlParameterKind::Decimal {
                        VarLenType::Decimaln
                    } else {
                        VarLenType::Numericn
                    },
                    size,
                    precision,
                    scale,
                }
            }
            SqlParameterKind::Char => {
                Self::character_type_info(VarLenType::BigChar, self.length, false, collation)?
            }
            SqlParameterKind::VarChar => {
                Self::character_type_info(VarLenType::BigVarChar, self.length, false, collation)?
            }
            SqlParameterKind::NChar => {
                Self::character_type_info(VarLenType::NChar, self.length, true, collation)?
            }
            SqlParameterKind::NVarChar => {
                Self::character_type_info(VarLenType::NVarchar, self.length, true, collation)?
            }
            SqlParameterKind::Binary => Self::binary_type_info(VarLenType::BigBinary, self.length)?,
            SqlParameterKind::VarBinary => {
                Self::binary_type_info(VarLenType::BigVarBin, self.length)?
            }
            SqlParameterKind::UniqueIdentifier => Self::var_len(VarLenType::Guid, 16, None),
            #[cfg(feature = "tds73")]
            SqlParameterKind::Date => Self::var_len(VarLenType::Daten, 3, None),
            #[cfg(not(feature = "tds73"))]
            SqlParameterKind::Date => return Err(Self::unsupported_tds_version()),
            #[cfg(feature = "tds73")]
            SqlParameterKind::Time => {
                Self::var_len(VarLenType::Timen, self.scale.unwrap_or(7) as usize, None)
            }
            #[cfg(not(feature = "tds73"))]
            SqlParameterKind::Time => return Err(Self::unsupported_tds_version()),
            SqlParameterKind::DateTime => Self::var_len(VarLenType::Datetimen, 8, None),
            SqlParameterKind::SmallDateTime => Self::var_len(VarLenType::Datetimen, 4, None),
            #[cfg(feature = "tds73")]
            SqlParameterKind::DateTime2 => Self::var_len(
                VarLenType::Datetime2,
                self.scale.unwrap_or(7) as usize,
                None,
            ),
            #[cfg(not(feature = "tds73"))]
            SqlParameterKind::DateTime2 => return Err(Self::unsupported_tds_version()),
            #[cfg(feature = "tds73")]
            SqlParameterKind::DateTimeOffset => Self::var_len(
                VarLenType::DatetimeOffsetn,
                self.scale.unwrap_or(7) as usize,
                None,
            ),
            #[cfg(not(feature = "tds73"))]
            SqlParameterKind::DateTimeOffset => return Err(Self::unsupported_tds_version()),
            SqlParameterKind::Xml => TypeInfo::Xml {
                schema: None,
                size: 0,
            },
        };

        Ok(type_info)
    }

    pub(crate) fn requires_utf8_support(&self, collation: Option<Collation>) -> bool {
        matches!(
            self.kind,
            SqlParameterKind::Char | SqlParameterKind::VarChar
        ) && matches!(collation, Some(collation) if collation.is_utf8())
    }

    fn character_type_info(
        kind: VarLenType,
        length: Option<TypeLength>,
        unicode: bool,
        collation: Option<Collation>,
    ) -> crate::Result<TypeInfo> {
        let collation = collation.ok_or(Error::Protocol(Cow::Borrowed(
            "SQL Server did not negotiate a parameter collation",
        )))?;
        let size = match length.ok_or_else(Self::invalid_metadata)? {
            TypeLength::Limited(length) if unicode => usize::from(length) * 2,
            TypeLength::Limited(length) => usize::from(length),
            TypeLength::Max => 0xffff,
        };

        Ok(Self::var_len(kind, size, Some(collation)))
    }

    fn binary_type_info(kind: VarLenType, length: Option<TypeLength>) -> crate::Result<TypeInfo> {
        let size = match length.ok_or_else(Self::invalid_metadata)? {
            TypeLength::Limited(length) => usize::from(length),
            TypeLength::Max => 0xffff,
        };

        Ok(Self::var_len(kind, size, None))
    }

    fn var_len(kind: VarLenType, size: usize, collation: Option<Collation>) -> TypeInfo {
        TypeInfo::VarLenSized(VarLenContext::new(kind, size, collation))
    }

    fn invalid_metadata() -> Error {
        Error::Conversion(Cow::Borrowed("invalid SQL parameter type metadata"))
    }

    #[cfg(not(feature = "tds73"))]
    fn unsupported_tds_version() -> Error {
        Error::Protocol(Cow::Borrowed(
            "the SQL parameter type requires TDS 7.3 or newer",
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::{SqlParameterKind, SqlParameterType};
    use crate::{
        tds::{
            codec::{TypeInfo, VarLenContext, VarLenType},
            Collation,
        },
        TypeLength,
    };

    #[test]
    fn constructors_reject_invalid_metadata() {
        assert!(SqlParameterType::float(0).is_err());
        assert!(SqlParameterType::float(54).is_err());
        assert!(SqlParameterType::decimal(0, 0).is_err());
        assert!(SqlParameterType::decimal(38, 39).is_err());
        assert!(SqlParameterType::numeric(39, 0).is_err());
        assert!(SqlParameterType::char(0).is_err());
        assert!(SqlParameterType::char(8001).is_err());
        assert!(SqlParameterType::varchar(TypeLength::Limited(8001)).is_err());
        assert!(SqlParameterType::varchar(TypeLength::Limited(0)).is_err());
        assert!(SqlParameterType::nchar(0).is_err());
        assert!(SqlParameterType::nchar(4001).is_err());
        assert!(SqlParameterType::nvarchar(TypeLength::Limited(4001)).is_err());
        assert!(SqlParameterType::nvarchar(TypeLength::Limited(0)).is_err());
        assert!(SqlParameterType::binary(0).is_err());
        assert!(SqlParameterType::binary(8001).is_err());
        assert!(SqlParameterType::varbinary(TypeLength::Limited(0)).is_err());
        assert!(SqlParameterType::varbinary(TypeLength::Limited(8001)).is_err());
        assert!(SqlParameterType::time(8).is_err());
        assert!(SqlParameterType::date_time2(8).is_err());
        assert!(SqlParameterType::date_time_offset(8).is_err());
    }

    #[test]
    fn declarations_are_canonical_and_metadata_is_readable() {
        let decimal = SqlParameterType::decimal(19, 4).unwrap();
        assert_eq!(decimal.kind(), SqlParameterKind::Decimal);
        assert_eq!(decimal.precision(), Some(19));
        assert_eq!(decimal.scale(), Some(4));
        assert_eq!(decimal.declaration(), "DECIMAL(19,4)");

        let varchar = SqlParameterType::varchar(TypeLength::Max).unwrap();
        assert_eq!(varchar.length(), Some(TypeLength::Max));
        assert_eq!(varchar.declaration(), "VARCHAR(MAX)");
    }

    #[test]
    fn every_supported_constructor_emits_a_closed_canonical_declaration() {
        let cases = [
            (SqlParameterType::bit(), "BIT"),
            (SqlParameterType::tiny_int(), "TINYINT"),
            (SqlParameterType::small_int(), "SMALLINT"),
            (SqlParameterType::int(), "INT"),
            (SqlParameterType::big_int(), "BIGINT"),
            (SqlParameterType::real(), "REAL"),
            (SqlParameterType::float(1).unwrap(), "FLOAT(1)"),
            (SqlParameterType::float(53).unwrap(), "FLOAT(53)"),
            (SqlParameterType::decimal(38, 38).unwrap(), "DECIMAL(38,38)"),
            (SqlParameterType::numeric(19, 4).unwrap(), "NUMERIC(19,4)"),
            (SqlParameterType::char(8000).unwrap(), "CHAR(8000)"),
            (
                SqlParameterType::varchar(TypeLength::Limited(8000)).unwrap(),
                "VARCHAR(8000)",
            ),
            (
                SqlParameterType::varchar(TypeLength::Max).unwrap(),
                "VARCHAR(MAX)",
            ),
            (SqlParameterType::nchar(4000).unwrap(), "NCHAR(4000)"),
            (
                SqlParameterType::nvarchar(TypeLength::Limited(4000)).unwrap(),
                "NVARCHAR(4000)",
            ),
            (
                SqlParameterType::nvarchar(TypeLength::Max).unwrap(),
                "NVARCHAR(MAX)",
            ),
            (SqlParameterType::binary(8000).unwrap(), "BINARY(8000)"),
            (
                SqlParameterType::varbinary(TypeLength::Limited(8000)).unwrap(),
                "VARBINARY(8000)",
            ),
            (
                SqlParameterType::varbinary(TypeLength::Max).unwrap(),
                "VARBINARY(MAX)",
            ),
            (SqlParameterType::unique_identifier(), "UNIQUEIDENTIFIER"),
            (SqlParameterType::date(), "DATE"),
            (SqlParameterType::time(0).unwrap(), "TIME(0)"),
            (SqlParameterType::time(7).unwrap(), "TIME(7)"),
            (SqlParameterType::date_time(), "DATETIME"),
            (SqlParameterType::small_date_time(), "SMALLDATETIME"),
            (SqlParameterType::date_time2(7).unwrap(), "DATETIME2(7)"),
            (
                SqlParameterType::date_time_offset(7).unwrap(),
                "DATETIMEOFFSET(7)",
            ),
            (SqlParameterType::xml(), "XML"),
        ];

        for (parameter_type, expected) in cases {
            assert_eq!(parameter_type.declaration(), expected);
        }
    }

    #[test]
    fn validated_declarations_generate_exact_tds_metadata() {
        let collation = Collation::new(0x0000_0409, 52);
        assert_eq!(
            SqlParameterType::int().type_info(Some(collation)).unwrap(),
            TypeInfo::VarLenSized(VarLenContext::new(VarLenType::Intn, 4, None))
        );
        assert_eq!(
            SqlParameterType::decimal(19, 4)
                .unwrap()
                .type_info(Some(collation))
                .unwrap(),
            TypeInfo::VarLenSizedPrecision {
                ty: VarLenType::Decimaln,
                size: 9,
                precision: 19,
                scale: 4,
            }
        );
        assert_eq!(
            SqlParameterType::nvarchar(TypeLength::Limited(9))
                .unwrap()
                .type_info(Some(collation))
                .unwrap(),
            TypeInfo::VarLenSized(VarLenContext::new(
                VarLenType::NVarchar,
                18,
                Some(collation),
            ))
        );
        assert_eq!(
            SqlParameterType::varbinary(TypeLength::Max)
                .unwrap()
                .type_info(Some(collation))
                .unwrap(),
            TypeInfo::VarLenSized(VarLenContext::new(VarLenType::BigVarBin, 0xffff, None,))
        );
        assert_eq!(
            SqlParameterType::time(3)
                .unwrap()
                .type_info(Some(collation))
                .unwrap(),
            TypeInfo::VarLenSized(VarLenContext::new(VarLenType::Timen, 3, None))
        );
        assert!(matches!(
            SqlParameterType::xml().type_info(Some(collation)).unwrap(),
            TypeInfo::Xml { schema: None, .. }
        ));
    }

    #[test]
    fn only_ansi_types_require_utf8_negotiation_for_a_utf8_collation() {
        let utf8_collation = Some(Collation::new(0x0400_0409, 0));
        let legacy_collation = Some(Collation::new(0x0000_0409, 0));

        assert!(SqlParameterType::varchar(TypeLength::Limited(4))
            .unwrap()
            .requires_utf8_support(utf8_collation));
        assert!(SqlParameterType::char(4)
            .unwrap()
            .requires_utf8_support(utf8_collation));
        assert!(!SqlParameterType::nvarchar(TypeLength::Limited(4))
            .unwrap()
            .requires_utf8_support(utf8_collation));
        assert!(!SqlParameterType::varchar(TypeLength::Limited(4))
            .unwrap()
            .requires_utf8_support(legacy_collation));
    }
}
