from __future__ import annotations

from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _cargo_lock_packages() -> list[dict[str, object]]:
    with (ROOT / "Cargo.lock").open("rb") as lock_file:
        return tomllib.load(lock_file)["package"]


def _versions(package_name: str) -> list[tuple[int, int, int]]:
    versions: list[tuple[int, int, int]] = []
    for package in _cargo_lock_packages():
        if package["name"] != package_name:
            continue
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", package["version"])
        assert match is not None, package
        versions.append(tuple(int(part) for part in match.groups()))
    return versions


def test_unused_quinn_protocol_dependency_is_absent() -> None:
    with (ROOT / "Cargo.toml").open("rb") as manifest_file:
        direct_dependencies = tomllib.load(manifest_file)["dependencies"]

    assert "quinn-proto" not in direct_dependencies
    assert not _versions("quinn-proto")


def test_tls_dependency_versions_clear_known_rustsec_floors() -> None:
    aws_lc_sys = _versions("aws-lc-sys")
    rustls_webpki = _versions("rustls-webpki")

    assert all(version >= (0, 39, 0) for version in aws_lc_sys)
    assert rustls_webpki
    assert all(version >= (0, 103, 13) for version in rustls_webpki)


def test_unmaintained_rustls_pemfile_is_absent() -> None:
    assert not _versions("rustls-pemfile")
