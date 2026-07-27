from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
RESPONSE_SOURCE = (
    ROOT / "vendor" / "tiberius" / "src" / "tds" / "stream" / "response.rs"
)
TOKEN_SOURCE = (
    ROOT / "vendor" / "tiberius" / "src" / "tds" / "stream" / "token.rs"
)
CONNECTION_SOURCE = (
    ROOT / "vendor" / "tiberius" / "src" / "client" / "connection.rs"
)
FORBIDDEN_TRACE_PAYLOADS = (
    "?return_value",
    "?meta",
    "info.message",
    "err.message",
    "hex_dump",
    "dbg!",
    "procedure",
)


def _read_required(path: Path) -> str:
    assert path.is_file(), f"missing privacy-contract source: {path}"
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


def _macro_calls(source: str) -> list[str]:
    calls: list[str] = []
    macro_pattern = re.compile(
        r"\b(?:event|trace|debug|info|warn|error)!\s*\("
    )

    for match in macro_pattern.finditer(source):
        start = match.start()
        index = match.end() - 1
        depth = 0
        quote: str | None = None
        escaped = False

        while index < len(source):
            character = source[index]
            if quote is not None:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
            elif character in {'"', "'"}:
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    calls.append(source[start : index + 1])
                    break
            index += 1

    return calls


def _assert_manual_redacted_debug(source: str, type_name: str) -> None:
    declaration = re.search(
        (
            rf"(?P<attributes>(?:\s*#\s*\[[^\]]*\])*)"
            rf"\s*pub\s+(?:enum|struct)\s+{re.escape(type_name)}\b"
        ),
        source,
        re.DOTALL,
    )
    assert declaration is not None, f"missing public {type_name}"
    assert not re.search(
        r"#\s*\[\s*derive\s*\([^\]]*\bDebug\b",
        declaration.group("attributes"),
        re.DOTALL,
    ), f"{type_name} must not derive value-bearing Debug"

    implementation = re.search(
        rf"impl\s+(?:(?:std::)?fmt::)?Debug\s+for\s+{re.escape(type_name)}\b",
        source,
    )
    assert implementation is not None, f"{type_name} needs a manual Debug implementation"
    block = _rust_block(source, implementation.group(0))
    for private_field in (
        "self.message",
        "self.server",
        "self.procedure",
        "self.name",
        "self.value",
    ):
        assert private_field not in block, (
            f"{type_name} Debug exposes value-bearing field {private_field}"
        )


def test_tib_result_010_response_debug_is_manual_and_value_redacted() -> None:
    response = _read_required(RESPONSE_SOURCE)

    for type_name in (
        "ResponseEvent",
        "ResponseInfo",
        "ResponseReturnValue",
    ):
        _assert_manual_redacted_debug(response, type_name)


def test_tib_result_010_token_tracing_contains_no_response_payloads() -> None:
    token_source = _read_required(TOKEN_SOURCE)
    trace_calls = "\n".join(_macro_calls(token_source))

    for forbidden in FORBIDDEN_TRACE_PAYLOADS:
        assert forbidden not in trace_calls
    for forbidden in (
        '"{}", change',
        "ack.prog_name",
        '"{} version {}"',
    ):
        assert forbidden not in trace_calls


def test_tib_result_010_connection_debug_reports_buffer_length_only() -> None:
    connection = _read_required(CONNECTION_SOURCE)
    debug_impl = _rust_block(connection, "Debug for Connection")
    debug_buffer = _rust_block(connection, "fn debug_buffer")

    assert '.field("buf_len", &self.buf.len())' in debug_impl
    assert '.field("buf",' not in debug_impl
    assert "hex_dump" not in debug_impl

    assert "buf_len" in debug_buffer
    assert "hex_dump" not in debug_buffer
    assert "dbg!" not in debug_buffer
