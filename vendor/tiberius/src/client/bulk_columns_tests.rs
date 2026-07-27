use std::{
    borrow::Cow,
    panic::{catch_unwind, AssertUnwindSafe},
};

use enumflags2::BitFlags;

use super::bulk_columns::{checked_bulk_type_declaration, BulkInsertColumns};
use crate::{
    error::Error,
    tds::codec::{
        BaseMetaDataColumn, ColumnFlag, FixedLenType, MetaDataColumn, TypeInfo, VarLenContext,
        VarLenType,
    },
};

fn flags(values: &[ColumnFlag]) -> BitFlags<ColumnFlag> {
    let mut result = BitFlags::empty();
    for value in values {
        result.insert(*value);
    }
    result
}

fn metadata_column(
    name: &str,
    flags: BitFlags<ColumnFlag>,
    ty: TypeInfo,
) -> MetaDataColumn<'static> {
    MetaDataColumn {
        base: BaseMetaDataColumn { flags, ty },
        col_name: Cow::Owned(name.to_owned()),
    }
}

fn variable_type(ty: VarLenType, len: usize) -> TypeInfo {
    TypeInfo::VarLenSized(VarLenContext::new(ty, len, None))
}

fn assert_bulk_input<T>(result: crate::Result<T>) {
    assert!(
        matches!(result, Err(Error::BulkInput(_))),
        "expected a typed bulk-input error"
    );
}

fn assert_protocol<T>(result: crate::Result<T>) {
    assert!(
        matches!(result, Err(Error::Protocol(_))),
        "expected a typed protocol error"
    );
}

#[test]
fn identifiers_are_closed_and_metadata_sql_preserves_order() {
    let target = BulkInsertColumns::new(
        "db]name.dbo.order",
        &["select", "amount]net", "literal.dot"],
    )
    .expect("valid raw identifiers must be accepted");

    assert_eq!(
        target.metadata_query(),
        "SELECT TOP (0) [select], [amount]]net], [literal.dot] \
FROM [db]]name].[dbo].[order]"
    );

    let columns = vec![
        metadata_column(
            "select",
            flags(&[ColumnFlag::Updateable]),
            TypeInfo::FixedLen(FixedLenType::Int4),
        ),
        metadata_column(
            "amount]net",
            flags(&[ColumnFlag::Updateable]),
            variable_type(VarLenType::BigVarChar, 20),
        ),
        metadata_column(
            "literal.dot",
            flags(&[ColumnFlag::Updateable]),
            variable_type(VarLenType::NVarchar, 40),
        ),
    ];

    assert_eq!(
        target
            .insert_query(&columns)
            .expect("validated declarations must produce a closed query"),
        "INSERT BULK [db]]name].[dbo].[order] \
([select] int, [amount]]net] varchar(20), [literal.dot] nvarchar(20))"
    );
}

#[test]
fn malformed_or_ambiguous_identifiers_are_bulk_input_errors() {
    for table in ["", ".table", "schema.", "db..table", "a.b.c.d", "nul\0part"] {
        assert_bulk_input(BulkInsertColumns::new(table, &["value"]));
    }

    let overlong = "x".repeat(129);
    assert_bulk_input(BulkInsertColumns::new(&overlong, &["value"]));
    assert_bulk_input(BulkInsertColumns::new("dbo.target", &[]));
    assert_bulk_input(BulkInsertColumns::new("dbo.target", &[""]));
    assert_bulk_input(BulkInsertColumns::new("dbo.target", &["nul\0column"]));
    assert_bulk_input(BulkInsertColumns::new("dbo.target", &[overlong.as_str()]));
    assert_bulk_input(BulkInsertColumns::new(
        "dbo.target",
        &["duplicate", "duplicate"],
    ));

    let too_many = vec!["value"; (u16::MAX as usize) + 1];
    assert_bulk_input(BulkInsertColumns::new("dbo.target", &too_many));
}

#[test]
fn metadata_count_order_and_canonical_names_are_exact() {
    let target = BulkInsertColumns::new("dbo.target", &["first", "second"])
        .expect("fixture identifiers are valid");
    let first = metadata_column(
        "first",
        flags(&[ColumnFlag::Updateable]),
        TypeInfo::FixedLen(FixedLenType::Int4),
    );
    let second = metadata_column(
        "second",
        flags(&[ColumnFlag::Updateable]),
        TypeInfo::FixedLen(FixedLenType::Int4),
    );

    assert_protocol(target.validate_metadata(0, None));
    assert_protocol(target.validate_metadata(2, Some(vec![first.clone(), second.clone()])));
    assert_protocol(target.validate_metadata(1, Some(vec![first.clone()])));
    assert_protocol(target.validate_metadata(1, Some(vec![second.clone(), first.clone()])));
    assert_protocol(target.validate_metadata(1, Some(vec![first.clone(), first.clone()])));

    let validated = target
        .validate_metadata(1, Some(vec![first, second]))
        .expect("exact metadata must be retained");
    assert_eq!(
        validated
            .iter()
            .map(|column| column.col_name.as_ref())
            .collect::<Vec<_>>(),
        ["first", "second"]
    );
}

#[test]
fn only_explicitly_writable_unrestricted_columns_are_accepted() {
    let target =
        BulkInsertColumns::new("dbo.target", &["value"]).expect("fixture identifier is valid");
    let int_type = TypeInfo::FixedLen(FixedLenType::Int4);

    for invalid_flags in [
        flags(&[]),
        flags(&[ColumnFlag::UpdateableUnknown]),
        flags(&[ColumnFlag::Updateable, ColumnFlag::Identity]),
        flags(&[ColumnFlag::Updateable, ColumnFlag::Computed]),
        flags(&[ColumnFlag::Updateable, ColumnFlag::FixedLenClrType]),
        flags(&[ColumnFlag::Updateable, ColumnFlag::SparseColumnSet]),
        flags(&[ColumnFlag::Updateable, ColumnFlag::Encrypted]),
        flags(&[ColumnFlag::Updateable, ColumnFlag::Hidden]),
    ] {
        assert_bulk_input(target.insert_query(&[metadata_column(
            "value",
            invalid_flags,
            int_type.clone(),
        )]));
    }

    let accepted = metadata_column(
        "value",
        flags(&[
            ColumnFlag::Updateable,
            ColumnFlag::Nullable,
            ColumnFlag::CaseSensitive,
            ColumnFlag::Key,
        ]),
        int_type,
    );
    assert_eq!(
        target
            .insert_query(&[accepted])
            .expect("non-restrictive metadata flags must remain supported"),
        "INSERT BULK [dbo].[target] ([value] int)"
    );
}

#[test]
fn supported_metadata_has_exact_checked_declarations() {
    let mut cases = vec![
        (TypeInfo::FixedLen(FixedLenType::Int1), "tinyint"),
        (TypeInfo::FixedLen(FixedLenType::Bit), "bit"),
        (TypeInfo::FixedLen(FixedLenType::Int2), "smallint"),
        (TypeInfo::FixedLen(FixedLenType::Int4), "int"),
        (TypeInfo::FixedLen(FixedLenType::Datetime4), "smalldatetime"),
        (TypeInfo::FixedLen(FixedLenType::Float4), "real"),
        (TypeInfo::FixedLen(FixedLenType::Money), "money"),
        (TypeInfo::FixedLen(FixedLenType::Datetime), "datetime"),
        (TypeInfo::FixedLen(FixedLenType::Float8), "float"),
        (TypeInfo::FixedLen(FixedLenType::Money4), "smallmoney"),
        (TypeInfo::FixedLen(FixedLenType::Int8), "bigint"),
        (variable_type(VarLenType::Bitn, 1), "bit"),
        (variable_type(VarLenType::Guid, 16), "uniqueidentifier"),
        (variable_type(VarLenType::Intn, 1), "tinyint"),
        (variable_type(VarLenType::Intn, 2), "smallint"),
        (variable_type(VarLenType::Intn, 4), "int"),
        (variable_type(VarLenType::Intn, 8), "bigint"),
        (variable_type(VarLenType::Floatn, 4), "real"),
        (variable_type(VarLenType::Floatn, 8), "float"),
        (variable_type(VarLenType::Money, 4), "smallmoney"),
        (variable_type(VarLenType::Money, 8), "money"),
        (variable_type(VarLenType::Datetimen, 4), "smalldatetime"),
        (variable_type(VarLenType::Datetimen, 8), "datetime"),
        (variable_type(VarLenType::BigVarBin, 8), "varbinary(8)"),
        (
            variable_type(VarLenType::BigVarBin, 8000),
            "varbinary(8000)",
        ),
        (
            variable_type(VarLenType::BigVarBin, 0xffff),
            "varbinary(max)",
        ),
        (variable_type(VarLenType::BigBinary, 8), "binary(8)"),
        (variable_type(VarLenType::BigBinary, 8000), "binary(8000)"),
        (variable_type(VarLenType::BigVarChar, 8), "varchar(8)"),
        (variable_type(VarLenType::BigVarChar, 8000), "varchar(8000)"),
        (
            variable_type(VarLenType::BigVarChar, 0xffff),
            "varchar(max)",
        ),
        (variable_type(VarLenType::BigChar, 8), "char(8)"),
        (variable_type(VarLenType::BigChar, 8000), "char(8000)"),
        (variable_type(VarLenType::NVarchar, 20), "nvarchar(10)"),
        (variable_type(VarLenType::NVarchar, 8000), "nvarchar(4000)"),
        (variable_type(VarLenType::NVarchar, 0xffff), "nvarchar(max)"),
        (variable_type(VarLenType::NChar, 20), "nchar(10)"),
        (variable_type(VarLenType::NChar, 8000), "nchar(4000)"),
        (
            TypeInfo::VarLenSizedPrecision {
                ty: VarLenType::Decimaln,
                size: 17,
                precision: 38,
                scale: 38,
            },
            "decimal(38,38)",
        ),
        (
            TypeInfo::VarLenSizedPrecision {
                ty: VarLenType::Numericn,
                size: 9,
                precision: 19,
                scale: 4,
            },
            "numeric(19,4)",
        ),
        (
            TypeInfo::Xml {
                schema: None,
                size: usize::MAX - 1,
            },
            "xml",
        ),
    ];

    #[cfg(feature = "tds73")]
    cases.extend([
        (variable_type(VarLenType::Daten, 3), "date"),
        (variable_type(VarLenType::Timen, 0), "time(0)"),
        (variable_type(VarLenType::Timen, 3), "time(3)"),
        (variable_type(VarLenType::Timen, 7), "time(7)"),
        (variable_type(VarLenType::Datetime2, 0), "datetime2(0)"),
        (variable_type(VarLenType::Datetime2, 3), "datetime2(3)"),
        (variable_type(VarLenType::Datetime2, 7), "datetime2(7)"),
        (
            variable_type(VarLenType::DatetimeOffsetn, 0),
            "datetimeoffset(0)",
        ),
        (
            variable_type(VarLenType::DatetimeOffsetn, 3),
            "datetimeoffset(3)",
        ),
        (
            variable_type(VarLenType::DatetimeOffsetn, 7),
            "datetimeoffset(7)",
        ),
    ]);

    for (ty, expected) in cases {
        assert_eq!(
            checked_bulk_type_declaration(&ty)
                .expect("supported metadata must have a total declaration"),
            expected
        );
    }
}

#[test]
fn invalid_or_unsupported_metadata_is_typed_and_never_panics() {
    let mut cases = vec![
        TypeInfo::FixedLen(FixedLenType::Null),
        variable_type(VarLenType::Bitn, 2),
        variable_type(VarLenType::Guid, 15),
        variable_type(VarLenType::Intn, 3),
        variable_type(VarLenType::Floatn, 5),
        variable_type(VarLenType::Money, 5),
        variable_type(VarLenType::Datetimen, 5),
        variable_type(VarLenType::BigVarBin, 0),
        variable_type(VarLenType::BigVarBin, 8001),
        variable_type(VarLenType::BigBinary, 0),
        variable_type(VarLenType::BigBinary, 0xffff),
        variable_type(VarLenType::BigVarChar, 0),
        variable_type(VarLenType::BigVarChar, 8001),
        variable_type(VarLenType::BigChar, 0),
        variable_type(VarLenType::BigChar, 0xffff),
        variable_type(VarLenType::NVarchar, 0),
        variable_type(VarLenType::NVarchar, 21),
        variable_type(VarLenType::NVarchar, 8002),
        variable_type(VarLenType::NChar, 0),
        variable_type(VarLenType::NChar, 21),
        variable_type(VarLenType::NChar, 0xffff),
        variable_type(VarLenType::Text, 10),
        variable_type(VarLenType::NText, 10),
        variable_type(VarLenType::Image, 10),
        variable_type(VarLenType::Udt, 10),
        variable_type(VarLenType::SSVariant, 10),
        variable_type(VarLenType::Decimaln, 5),
        variable_type(VarLenType::Numericn, 5),
        variable_type(VarLenType::Xml, 10),
        TypeInfo::VarLenSizedPrecision {
            ty: VarLenType::Decimaln,
            size: 5,
            precision: 0,
            scale: 0,
        },
        TypeInfo::VarLenSizedPrecision {
            ty: VarLenType::Decimaln,
            size: 17,
            precision: 39,
            scale: 0,
        },
        TypeInfo::VarLenSizedPrecision {
            ty: VarLenType::Numericn,
            size: 5,
            precision: 4,
            scale: 5,
        },
        TypeInfo::VarLenSizedPrecision {
            ty: VarLenType::Numericn,
            size: 13,
            precision: 19,
            scale: 4,
        },
        TypeInfo::VarLenSizedPrecision {
            ty: VarLenType::Intn,
            size: 5,
            precision: 4,
            scale: 0,
        },
    ];

    #[cfg(feature = "tds73")]
    cases.extend([
        variable_type(VarLenType::Daten, 2),
        variable_type(VarLenType::Timen, 8),
        variable_type(VarLenType::Datetime2, 8),
        variable_type(VarLenType::DatetimeOffsetn, 8),
    ]);

    for ty in cases {
        let outcome = catch_unwind(AssertUnwindSafe(|| checked_bulk_type_declaration(&ty)));
        let result = outcome.expect("unsupported metadata must not panic");
        assert_bulk_input(result);
    }
}
