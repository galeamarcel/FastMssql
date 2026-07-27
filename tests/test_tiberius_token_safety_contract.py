from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKEN_TYPE = (
    ROOT / "vendor" / "tiberius" / "src" / "tds" / "codec" / "token"
    / "token_type.rs"
)
TOKEN_STREAM = (
    ROOT / "vendor" / "tiberius" / "src" / "tds" / "stream" / "token.rs"
)
TYPE_INFO = (
    ROOT / "vendor" / "tiberius" / "src" / "tds" / "codec" / "type_info.rs"
)
FORBIDDEN_PANIC_MACROS = (
    "panic!(",
    "todo!(",
    "unimplemented!(",
    "unreachable!(",
)


def _read_required(path: Path) -> str:
    assert path.is_file(), f"missing vendored decoder source: {path}"
    return path.read_text(encoding="utf-8")


def _rust_block(source: str, signature: str) -> str:
    signature_start = source.find(signature)
    assert signature_start >= 0, f"missing Rust signature: {signature}"
    block_start = source.find("{", signature_start)
    assert block_start >= 0, f"missing Rust body: {signature}"

    depth = 0
    for index in range(block_start, len(source)):
        character = source[index]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[block_start : index + 1]

    raise AssertionError(f"unterminated Rust body: {signature}")


def _assert_no_panic_macros(source: str) -> None:
    for forbidden in FORBIDDEN_PANIC_MACROS:
        assert forbidden not in source


def test_tib_safe_003_recognizes_tabname_token() -> None:
    token_type = _read_required(TOKEN_TYPE)

    assert "TableName = 0xA4" in token_type
    assert "ColInfo = 0xA5" in token_type


def test_tib_safe_003_dispatch_consumes_browse_payloads_without_panics() -> None:
    token_stream = _read_required(TOKEN_STREAM)
    dispatch = _rust_block(token_stream, "pub fn try_unfold")

    assert "TokenType::TableName =>" in dispatch
    assert "TokenType::ColInfo =>" in dispatch
    assert dispatch.count("consume_ushort_payload") == 2
    _assert_no_panic_macros(dispatch)


def test_tib_safe_003_rejects_unsupported_type_info_without_panics() -> None:
    type_info = _read_required(TYPE_INFO)
    decode = _rust_block(type_info, "pub(crate) async fn decode")

    assert "VarLenType::Udt" in decode
    assert "VarLenType::SSVariant" in decode
    _assert_no_panic_macros(decode)
