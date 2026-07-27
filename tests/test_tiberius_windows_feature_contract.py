from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_AUTH_PARSER_TEST_FILES = (
    ROOT / "vendor/tiberius/src/client/config/ado_net.rs",
    ROOT / "vendor/tiberius/src/client/config/jdbc.rs",
)


def test_windows_auth_parser_tests_share_the_winauth_api_gate() -> None:
    required_gate = '#[cfg(all(windows, feature = "winauth"))]'

    for source_path in WINDOWS_AUTH_PARSER_TEST_FILES:
        source = source_path.read_text(encoding="utf-8")
        for test_name in (
            "parsing_sspi_authentication",
            "parsing_windows_authentication",
        ):
            assert f"{required_gate}\n    fn {test_name}(" in source, (
                f"{source_path.name}:{test_name} references Windows-only "
                "AuthMethod APIs and must share their winauth feature gate"
            )
            assert f"#[cfg(windows)]\n    fn {test_name}(" not in source
