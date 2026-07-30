#!/usr/bin/env python3
"""Real-process production framework matrix orchestrator."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
import hashlib
import http.client
import inspect
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Sequence

import psutil


SCHEMA_VERSION = 1
WORKER_COUNTS = (1, 2, 4, 8)
REQUIRED_OPERATIONS = 1_000
LARGE_OPERATIONS = 10_000
EXTENDED_OPERATIONS = 99_999
MAX_OPERATIONS = 99_999
GUNICORN_WINDOWS_REASON = "Gunicorn is not supported on Windows"
UVLOOP_WINDOWS_REASON = "uvloop is not supported on Windows"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class RunnerConfigurationError(ValueError):
    """A privacy-safe command-line or profile configuration failure."""


class ProcessSupervisorError(RuntimeError):
    """A privacy-safe external-process supervision failure."""


class ReadinessTimeoutError(ProcessSupervisorError):
    """A supervised process did not become ready before its deadline."""


class WorkerEvidenceError(ProcessSupervisorError):
    """Worker evidence was stale, malformed or unrelated to this launch."""


class ProcessExitedError(ProcessSupervisorError):
    """A child exited before or during an expected successful lifecycle."""

    def __init__(
        self,
        outcome: ProcessOutcome,
    ) -> None:
        super().__init__(
            f"supervised process {outcome.pid} exited with code {outcome.returncode}"
        )
        self.outcome = outcome
        self.returncode = outcome.returncode
        self.stdout = outcome.stdout
        self.stderr = outcome.stderr


class ForcedProcessCleanupError(ProcessSupervisorError):
    """Graceful process teardown failed and force was required."""

    def __init__(self, outcome: ProcessOutcome) -> None:
        super().__init__(f"supervised process {outcome.pid} required forced cleanup")
        self.outcome = outcome


class PortCollisionError(ProcessSupervisorError):
    """Every bounded launch attempt encountered a loopback port collision."""

    def __init__(self, ports: Sequence[int]) -> None:
        self.ports = tuple(ports)
        self.attempts = len(self.ports)
        super().__init__(f"loopback launch exhausted {self.attempts} port attempts")


class CandidateProvenanceError(ProcessSupervisorError):
    """The supplied wheel or a worker import cannot prove its provenance."""


class IsolatedApplicationError(ProcessSupervisorError):
    """The copied process application is stale, incomplete or modified."""


class HttpProbeError(ProcessSupervisorError):
    """A bounded loopback HTTP probe failed or returned invalid data."""


@dataclass(frozen=True, slots=True)
class SupervisorPolicy:
    startup_timeout_seconds: float = 30.0
    graceful_timeout_seconds: float = 20.0
    force_timeout_seconds: float = 5.0
    poll_interval_seconds: float = 0.025
    port_retry_attempts: int = 3
    maximum_capture_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        for field_name in (
            "startup_timeout_seconds",
            "graceful_timeout_seconds",
            "force_timeout_seconds",
            "poll_interval_seconds",
        ):
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise RunnerConfigurationError(
                    f"{field_name} must be a finite positive number"
                )
        if (
            isinstance(self.port_retry_attempts, bool)
            or not isinstance(self.port_retry_attempts, int)
            or not 1 <= self.port_retry_attempts <= 10
        ):
            raise RunnerConfigurationError(
                "port retry attempts must be between 1 and 10"
            )
        if (
            isinstance(self.maximum_capture_bytes, bool)
            or not isinstance(self.maximum_capture_bytes, int)
            or self.maximum_capture_bytes < 1_024
        ):
            raise RunnerConfigurationError(
                "maximum capture bytes must be at least 1,024"
            )


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    pid: int
    returncode: int
    stdout: str
    stderr: str
    output_truncated: bool
    descendant_pids: tuple[int, ...]
    graceful_stop: bool
    forced_cleanup: bool


@dataclass(frozen=True, slots=True)
class ServerLaunch:
    supervisor: ProcessSupervisor
    port: int
    attempts: int
    sanitized_command: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IsolatedApplication:
    root: Path
    package_directory: Path
    manifest_path: Path
    candidate_sha: str
    wheel_filename: str
    wheel_sha256: str
    source_sha256: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class OfflineSmokeResult:
    profile_id: str
    port: int
    launch_attempts: int
    sanitized_command: tuple[str, ...]
    package: dict[str, object]
    ready_pids: tuple[int, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]

    def to_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(slots=True)
class _BoundedCapture:
    maximum_bytes: int
    data: bytearray
    truncated: bool = False

    @classmethod
    def create(cls, maximum_bytes: int) -> _BoundedCapture:
        return cls(maximum_bytes=maximum_bytes, data=bytearray())

    async def drain(self, reader: asyncio.StreamReader) -> None:
        while chunk := await reader.read(65_536):
            remaining = self.maximum_bytes - len(self.data)
            if remaining > 0:
                self.data.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self.truncated = True

    def text(self) -> str:
        return bytes(self.data).decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class ProfileFamily:
    family: str
    app_factory: str
    server: str
    loop: str
    worker_class: str
    threads_per_worker: int
    preload_app: bool = False


PROFILE_FAMILIES = (
    ProfileFamily(
        family="fastapi-gunicorn-uvicorn-worker",
        app_factory="production_framework.app:create_fastapi_app",
        server="gunicorn",
        loop="auto",
        worker_class="uvicorn_worker.UvicornWorker",
        threads_per_worker=1,
    ),
    ProfileFamily(
        family="fastapi-uvicorn-asyncio",
        app_factory="production_framework.app:create_fastapi_app",
        server="uvicorn",
        loop="asyncio",
        worker_class="uvicorn",
        threads_per_worker=1,
    ),
    ProfileFamily(
        family="fastapi-uvicorn-uvloop",
        app_factory="production_framework.app:create_fastapi_app",
        server="uvicorn",
        loop="uvloop",
        worker_class="uvicorn",
        threads_per_worker=1,
    ),
    ProfileFamily(
        family="flask-asgi-uvicorn-asyncio",
        app_factory="production_framework.app:create_adapted_flask_app",
        server="uvicorn",
        loop="asyncio",
        worker_class="uvicorn",
        threads_per_worker=1,
    ),
    ProfileFamily(
        family="flask-asgi-uvicorn-uvloop",
        app_factory="production_framework.app:create_adapted_flask_app",
        server="uvicorn",
        loop="uvloop",
        worker_class="uvicorn",
        threads_per_worker=1,
    ),
    ProfileFamily(
        family="flask-gunicorn-gthread",
        app_factory="production_framework.app:create_flask_app()",
        server="gunicorn",
        loop="flask",
        worker_class="gthread",
        threads_per_worker=4,
    ),
    ProfileFamily(
        family="flask-gunicorn-sync",
        app_factory="production_framework.app:create_flask_app()",
        server="gunicorn",
        loop="flask",
        worker_class="sync",
        threads_per_worker=1,
    ),
)


@dataclass(frozen=True, slots=True)
class ProcessProfile:
    id: str
    family: str
    app_factory: str
    server: str
    loop: str
    worker_class: str
    workers: int
    threads_per_worker: int
    preload_app: bool
    transport: str
    database_mode: str
    platform_system: str
    applicable: bool
    status: str
    not_applicable_reason: str | None

    def to_record(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    candidate_sha: str
    wheel: Path
    wheel_sha256: str
    venv_python: Path
    run_root: Path
    output: Path
    database_mode: str
    global_connection_budget: int
    operations: tuple[int, ...]
    allow_extended: bool
    platform_system: str


def normalize_platform(platform_name: str) -> str:
    normalized = platform_name.lower()
    if normalized.startswith("linux"):
        return "Linux"
    if normalized == "darwin":
        return "Darwin"
    if normalized in {"win32", "cygwin", "windows"}:
        return "Windows"
    raise RunnerConfigurationError(
        f"unsupported production framework platform {platform_name!r}"
    )


def _not_applicable_reason(
    family: ProfileFamily,
    platform_system: str,
) -> str | None:
    if platform_system != "Windows":
        return None
    if family.server == "gunicorn":
        return GUNICORN_WINDOWS_REASON
    if family.loop == "uvloop":
        return UVLOOP_WINDOWS_REASON
    return None


def expand_profiles(
    platform_name: str,
    *,
    database_mode: str,
) -> tuple[ProcessProfile, ...]:
    if database_mode not in {"sql_auth", "offline"}:
        raise RunnerConfigurationError("database mode must be sql_auth or offline")
    platform_system = normalize_platform(platform_name)
    profiles: list[ProcessProfile] = []
    for family in PROFILE_FAMILIES:
        reason = _not_applicable_reason(family, platform_system)
        for workers in WORKER_COUNTS:
            applicable = reason is None
            profiles.append(
                ProcessProfile(
                    id=f"{family.family}-w{workers}",
                    family=family.family,
                    app_factory=family.app_factory,
                    server=family.server,
                    loop=family.loop,
                    worker_class=family.worker_class,
                    workers=workers,
                    threads_per_worker=family.threads_per_worker,
                    preload_app=family.preload_app,
                    transport="tcp-http-1.1",
                    database_mode=database_mode,
                    platform_system=platform_system,
                    applicable=applicable,
                    status="PENDING" if applicable else "N/A",
                    not_applicable_reason=reason,
                )
            )
    return tuple(sorted(profiles, key=lambda profile: profile.id))


def representative_profiles(
    platform_name: str,
) -> dict[str, str | None]:
    platform_system = normalize_platform(platform_name)
    native = (
        "fastapi-uvicorn-asyncio-w4"
        if platform_system == "Windows"
        else "fastapi-uvicorn-uvloop-w4"
    )
    adapted = (
        "flask-asgi-uvicorn-asyncio-w4"
        if platform_system == "Windows"
        else "flask-asgi-uvicorn-uvloop-w4"
    )
    gunicorn_only = platform_system != "Windows"
    return {
        "adapted_asgi": adapted,
        "flask_gthread": ("flask-gunicorn-gthread-w4" if gunicorn_only else None),
        "flask_sync": ("flask-gunicorn-sync-w4" if gunicorn_only else None),
        "gunicorn_asgi": (
            "fastapi-gunicorn-uvicorn-worker-w4" if gunicorn_only else None
        ),
        "load": native,
        "native_asgi": native,
    }


def offline_smoke_profiles(
    platform_name: str,
) -> tuple[ProcessProfile, ...]:
    platform_system = normalize_platform(platform_name)
    required_families = {
        "fastapi-uvicorn-asyncio",
    }
    if platform_system != "Windows":
        required_families.update(
            {
                "fastapi-uvicorn-uvloop",
                "fastapi-gunicorn-uvicorn-worker",
                "flask-gunicorn-sync",
                "flask-gunicorn-gthread",
            }
        )
    profiles = expand_profiles(
        platform_name,
        database_mode="offline",
    )
    selected = tuple(
        profile
        for profile in profiles
        if profile.workers == 1 and profile.family in required_families
    )
    if {profile.family for profile in selected} != required_families or any(
        not profile.applicable for profile in selected
    ):
        raise RunnerConfigurationError("offline smoke profile selection is incomplete")
    return selected


def profiles_json(profiles: Sequence[ProcessProfile]) -> str:
    records = [
        profile.to_record()
        for profile in sorted(profiles, key=lambda profile: profile.id)
    ]
    return json.dumps(records, sort_keys=True, separators=(",", ":"))


def validate_operations(
    operations: int,
    *,
    allow_extended: bool,
) -> int:
    if not 1 <= operations <= MAX_OPERATIONS:
        raise RunnerConfigurationError("operations must be between 1 and 99,999")
    if operations == EXTENDED_OPERATIONS and not allow_extended:
        raise RunnerConfigurationError("99,999 operations require --allow-extended")
    if operations > REQUIRED_OPERATIONS and not allow_extended:
        raise RunnerConfigurationError(
            "operations above 1,000 require --allow-extended"
        )
    return operations


def build_server_command(
    config: RunnerConfig,
    profile: ProcessProfile,
    *,
    port: int,
) -> list[str]:
    """Build one shell-free server command for an applicable profile."""

    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
        raise RunnerConfigurationError("port must be between 1 and 65,535")
    if not profile.applicable:
        raise RunnerConfigurationError(
            "cannot build a command for a not-applicable profile"
        )
    if profile.database_mode != config.database_mode:
        raise RunnerConfigurationError(
            "profile database mode does not match the runner configuration"
        )
    if profile.preload_app:
        raise RunnerConfigurationError(
            "preloaded applications are forbidden by worker-local pool ownership"
        )

    executable = str(config.venv_python)
    if profile.server == "uvicorn":
        if (
            profile.worker_class != "uvicorn"
            or profile.loop not in {"asyncio", "uvloop"}
            or profile.threads_per_worker != 1
        ):
            raise RunnerConfigurationError("invalid Uvicorn process profile")
        return [
            executable,
            "-m",
            "uvicorn",
            profile.app_factory,
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            str(profile.workers),
            "--loop",
            profile.loop,
            "--timeout-graceful-shutdown",
            "20",
            "--timeout-worker-healthcheck",
            "5",
            "--no-access-log",
        ]

    if profile.server != "gunicorn":
        raise RunnerConfigurationError("unsupported production framework server")
    if profile.worker_class not in {
        "uvicorn_worker.UvicornWorker",
        "sync",
        "gthread",
    }:
        raise RunnerConfigurationError("invalid Gunicorn worker class")
    if profile.worker_class == "gthread":
        if profile.threads_per_worker != 4:
            raise RunnerConfigurationError(
                "Gunicorn gthread profiles require exactly four threads"
            )
    elif profile.threads_per_worker != 1:
        raise RunnerConfigurationError(
            "non-gthread Gunicorn profiles require one thread per worker"
        )

    command = [
        executable,
        "-m",
        "gunicorn",
        profile.app_factory,
        "--config",
        "python:production_framework.gunicorn_conf",
        "--bind",
        f"127.0.0.1:{port}",
        "--workers",
        str(profile.workers),
        "-k",
        profile.worker_class,
    ]
    if profile.worker_class == "gthread":
        command.extend(["--threads", str(profile.threads_per_worker)])
    return command


APPLICATION_SOURCE_FILES = (
    "__init__.py",
    "app.py",
    "gunicorn_conf.py",
)
APPLICATION_DIRECTORY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
WHEEL_FILENAME_PATTERN = re.compile(
    r"fastmssql-(?P<version>[0-9]+\.[0-9]+\.[0-9]+)-.+\.whl"
)
FASTMSSQL_IMPORT_PROVENANCE_SOURCE = "fastmssql.__file__"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1_048_576):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, sort_keys=True, separators=(",", ":"))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _verify_candidate_wheel(config: RunnerConfig) -> None:
    if not config.wheel.is_file():
        raise CandidateProvenanceError("candidate wheel does not exist")
    if WHEEL_FILENAME_PATTERN.fullmatch(config.wheel.name) is None:
        raise CandidateProvenanceError(
            "candidate wheel filename is not a FastMssql wheel"
        )
    if _sha256_file(config.wheel) != config.wheel_sha256:
        raise CandidateProvenanceError("candidate wheel SHA-256 mismatch")


def verify_isolated_application(
    isolated: IsolatedApplication,
) -> None:
    if not isolated.root.is_dir() or isolated.root.is_symlink():
        raise IsolatedApplicationError("isolated application root is missing or unsafe")
    expected_files = {
        "application-manifest.json",
        *(f"production_framework/{name}" for name in APPLICATION_SOURCE_FILES),
    }
    actual_files: set[str] = set()
    for path in isolated.root.rglob("*"):
        if path.is_symlink():
            raise IsolatedApplicationError(
                "isolated application contains a symbolic link"
            )
        if path.is_file():
            actual_files.add(path.relative_to(isolated.root).as_posix())
    if actual_files != expected_files:
        raise IsolatedApplicationError(
            "isolated application file set does not match its closed contract"
        )
    try:
        manifest = json.loads(isolated.manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise IsolatedApplicationError(
            "isolated application manifest is invalid"
        ) from error
    expected_manifest = {
        "candidate_sha": isolated.candidate_sha,
        "source_sha256": dict(isolated.source_sha256),
        "wheel_filename": isolated.wheel_filename,
        "wheel_sha256": isolated.wheel_sha256,
    }
    if manifest != expected_manifest:
        raise IsolatedApplicationError("isolated application manifest mismatch")
    for name, expected_hash in isolated.source_sha256:
        copied = isolated.package_directory / name
        if _sha256_file(copied) != expected_hash:
            raise IsolatedApplicationError("isolated application source hash mismatch")


def prepare_isolated_application(
    config: RunnerConfig,
    *,
    source_directory: Path,
    directory_name: str,
) -> IsolatedApplication:
    _verify_candidate_wheel(config)
    if APPLICATION_DIRECTORY_PATTERN.fullmatch(directory_name) is None:
        raise IsolatedApplicationError("isolated application directory name is unsafe")
    source_root = source_directory.resolve()
    if not source_root.is_dir() or source_root.is_symlink():
        raise IsolatedApplicationError(
            "application source directory is missing or unsafe"
        )
    source_hashes: list[tuple[str, str]] = []
    for name in APPLICATION_SOURCE_FILES:
        source = source_root / name
        if not source.is_file() or source.is_symlink():
            raise IsolatedApplicationError(
                f"required application source {name} is missing or unsafe"
            )
        source_hashes.append((name, _sha256_file(source)))

    destination = (config.run_root / directory_name).resolve()
    if not destination.is_relative_to(config.run_root):
        raise IsolatedApplicationError(
            "isolated application destination escapes the run root"
        )
    if destination.exists():
        raise IsolatedApplicationError(
            "isolated application destination already exists"
        )

    temporary_root = Path(
        tempfile.mkdtemp(
            prefix=f".{directory_name}-",
            dir=config.run_root,
        )
    )
    try:
        package_directory = temporary_root / "production_framework"
        package_directory.mkdir()
        for name, _ in source_hashes:
            shutil.copyfile(
                source_root / name,
                package_directory / name,
            )
        _write_atomic_json(
            temporary_root / "application-manifest.json",
            {
                "candidate_sha": config.candidate_sha,
                "source_sha256": dict(source_hashes),
                "wheel_filename": config.wheel.name,
                "wheel_sha256": config.wheel_sha256,
            },
        )
        os.replace(temporary_root, destination)
    finally:
        if temporary_root.exists():
            shutil.rmtree(temporary_root)

    isolated = IsolatedApplication(
        root=destination,
        package_directory=destination / "production_framework",
        manifest_path=destination / "application-manifest.json",
        candidate_sha=config.candidate_sha,
        wheel_filename=config.wheel.name,
        wheel_sha256=config.wheel_sha256,
        source_sha256=tuple(source_hashes),
    )
    verify_isolated_application(isolated)
    return isolated


def validate_package_record(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    record: Mapping[str, object],
    *,
    repository_root: Path,
) -> dict[str, object]:
    required = {
        "candidate_sha",
        "cwd",
        "distribution_direct_url",
        "distribution_version",
        "fastmssql_import_path",
        "pid",
        "ppid",
        "python_executable",
        "sys_path",
        "wheel_filename",
        "wheel_sha256",
    }
    if not required.issubset(record):
        raise CandidateProvenanceError(
            "package record is missing required provenance fields"
        )
    if record["candidate_sha"] != config.candidate_sha:
        raise CandidateProvenanceError("candidate SHA mismatch")
    if record["wheel_filename"] != config.wheel.name:
        raise CandidateProvenanceError("wheel filename mismatch")
    if record["wheel_sha256"] != config.wheel_sha256:
        raise CandidateProvenanceError("wheel hash mismatch")
    direct_url = record["distribution_direct_url"]
    if not isinstance(direct_url, dict):
        raise CandidateProvenanceError("wheel origin must be a JSON object")
    if "dir_info" in direct_url:
        raise CandidateProvenanceError("editable origin is forbidden")
    if "vcs_info" in direct_url:
        raise CandidateProvenanceError("VCS wheel origin is forbidden")
    if (
        set(direct_url) != {"archive_info", "url"}
        or not isinstance(direct_url["archive_info"], dict)
        or direct_url["url"] != config.wheel.as_uri()
    ):
        raise CandidateProvenanceError("wheel origin mismatch")
    wheel_match = WHEEL_FILENAME_PATTERN.fullmatch(config.wheel.name)
    if wheel_match is None:
        raise CandidateProvenanceError("wheel filename is invalid")
    if record["distribution_version"] != wheel_match.group("version"):
        raise CandidateProvenanceError("distribution version mismatch")
    if record["python_executable"] != str(config.venv_python):
        raise CandidateProvenanceError("Python executable mismatch")

    worker_cwd = Path(str(record["cwd"])).resolve()
    if worker_cwd != isolated.root:
        raise CandidateProvenanceError("worker CWD mismatch")
    repository = repository_root.resolve()
    repository_source = (repository / "python").resolve()
    import_path = Path(str(record["fastmssql_import_path"])).resolve()
    if import_path.is_relative_to(repository_source):
        raise CandidateProvenanceError(
            f"source import from {FASTMSSQL_IMPORT_PROVENANCE_SOURCE} is forbidden"
        )
    venv_root = config.venv_python.parent.parent.resolve()
    if not import_path.is_relative_to(venv_root) or not {
        "site-packages",
        "dist-packages",
    }.intersection(import_path.parts):
        raise CandidateProvenanceError(
            "source import does not resolve under isolated site-packages"
        )

    sys_path = record["sys_path"]
    if (
        not isinstance(sys_path, list)
        or not sys_path
        or any(not isinstance(entry, str) for entry in sys_path)
    ):
        raise CandidateProvenanceError("worker sys.path is invalid")
    normalized_sys_path = [Path(entry or worker_cwd).resolve() for entry in sys_path]
    if any(
        entry == repository_source or entry.is_relative_to(repository_source)
        for entry in normalized_sys_path
    ):
        raise CandidateProvenanceError("source sys.path is forbidden")
    if repository in normalized_sys_path:
        raise CandidateProvenanceError("repository sys.path is forbidden")
    if isolated.root not in normalized_sys_path:
        raise CandidateProvenanceError(
            "isolated application root is absent from worker sys.path"
        )
    if not any(
        import_path.is_relative_to(entry)
        for entry in normalized_sys_path
        if "site-packages" in entry.parts or "dist-packages" in entry.parts
    ):
        raise CandidateProvenanceError(
            "isolated site-packages is absent from worker sys.path"
        )
    for field_name in ("pid", "ppid"):
        value = record[field_name]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise CandidateProvenanceError(f"worker {field_name} is invalid")
    return dict(record)


INHERITED_ENVIRONMENT_KEYS = (
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "PATH",
    "PATHEXT",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)
FORBIDDEN_CHILD_ENVIRONMENT_KEYS = frozenset({"PYTHONHOME", "PYTHONPATH"})
ENVIRONMENT_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CREDENTIAL_ARGUMENTS = frozenset(
    {
        "--connection-string",
        "--credential",
        "--password",
        "--token",
    }
)
CREDENTIAL_ENVIRONMENT_NAME = re.compile(
    r"(?:^|_)(?:CREDENTIAL|PASSWORD|SECRET|TOKEN)(?:$|_)",
    re.IGNORECASE,
)
INLINE_CREDENTIAL = re.compile(
    r"(?i)\b(credential|password|pwd|secret|token)\s*=\s*([^;\s]+)"
)


def _credential_values(environment: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                value
                for name, value in environment.items()
                if value and CREDENTIAL_ENVIRONMENT_NAME.search(name)
            },
            key=len,
            reverse=True,
        )
    )


def redact(value: str, credentials: Sequence[str] = ()) -> str:
    """Remove known and inline credential values from persisted diagnostics."""

    for credential in sorted(
        {credential for credential in credentials if credential},
        key=len,
        reverse=True,
    ):
        value = value.replace(credential, "<redacted>")
        for prefix_length in range(
            min(len(credential) - 1, len(value)),
            0,
            -1,
        ):
            prefix = credential[:prefix_length]
            if value.endswith(prefix):
                value = f"{value[:-prefix_length]}<redacted>"
                break
    return INLINE_CREDENTIAL.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        value,
    )


def build_child_environment(
    overrides: Mapping[str, str],
    *,
    parent_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Construct a closed child environment without inheriting PYTHONPATH."""

    source = os.environ if parent_environment is None else parent_environment
    environment = {
        name: source[name] for name in INHERITED_ENVIRONMENT_KEYS if name in source
    }
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    for name, value in overrides.items():
        if (
            ENVIRONMENT_KEY_PATTERN.fullmatch(name) is None
            or name in FORBIDDEN_CHILD_ENVIRONMENT_KEYS
        ):
            raise RunnerConfigurationError(
                "child environment contains a forbidden setting"
            )
        if not isinstance(value, str) or "\0" in value:
            raise RunnerConfigurationError(
                f"child environment setting {name} is invalid"
            )
        environment[name] = value
    return environment


def build_profile_environment(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    run_id: str,
    artifact_directory: Path,
) -> dict[str, str]:
    """Build one closed, credential-free offline worker environment."""

    if config.database_mode != "offline" or profile.database_mode != "offline":
        raise RunnerConfigurationError(
            "offline profile environment requires offline database mode"
        )
    if not profile.applicable:
        raise RunnerConfigurationError(
            "cannot build an environment for a not-applicable profile"
        )
    if APPLICATION_DIRECTORY_PATTERN.fullmatch(run_id) is None:
        raise RunnerConfigurationError("framework run ID is unsafe")
    verify_isolated_application(isolated)
    if not isolated.root.is_relative_to(config.run_root):
        raise RunnerConfigurationError("isolated application is outside the run root")
    resolved_artifacts = artifact_directory.resolve()
    if not resolved_artifacts.is_relative_to(config.run_root):
        raise RunnerConfigurationError(
            "worker artifact directory is outside the run root"
        )
    if resolved_artifacts.exists():
        if not resolved_artifacts.is_dir() or any(resolved_artifacts.iterdir()):
            raise RunnerConfigurationError(
                "worker artifact directory must be new or empty"
            )
    else:
        resolved_artifacts.mkdir(parents=True)

    lifecycle_owner = (
        "gunicorn"
        if profile.server == "gunicorn" and profile.worker_class in {"sync", "gthread"}
        else "asgi"
    )
    application_name = f"fm-{run_id}-{profile.family}"
    if len(application_name) > 128:
        raise RunnerConfigurationError(
            "generated framework application name is too long"
        )
    return build_child_environment(
        {
            "FASTMSSQL_FRAMEWORK_DATABASE_MODE": "offline",
            "FASTMSSQL_FRAMEWORK_WORKER_COUNT": str(profile.workers),
            "FASTMSSQL_FRAMEWORK_GLOBAL_CONNECTION_BUDGET": str(
                config.global_connection_budget
            ),
            "FASTMSSQL_FRAMEWORK_APPLICATION_NAME": application_name,
            "FASTMSSQL_FRAMEWORK_RUN_ID": run_id,
            "FASTMSSQL_FRAMEWORK_RUN_ROOT": str(config.run_root),
            "FASTMSSQL_FRAMEWORK_ARTIFACT_DIR": str(resolved_artifacts),
            "FASTMSSQL_FRAMEWORK_TABLE": (
                f"framework_items_{config.candidate_sha[:12]}"
            ),
            "FASTMSSQL_FRAMEWORK_SQL_DELAY_MS": "0",
            "FASTMSSQL_FRAMEWORK_CANDIDATE_SHA": config.candidate_sha,
            "FASTMSSQL_FRAMEWORK_WHEEL_FILENAME": config.wheel.name,
            "FASTMSSQL_FRAMEWORK_WHEEL_SHA256": config.wheel_sha256,
            "FASTMSSQL_FRAMEWORK_GUNICORN_LIFECYCLE_OWNER": lifecycle_owner,
        }
    )


def sanitize_command(command: Sequence[str]) -> tuple[str, ...]:
    """Validate and return a persistence-safe command summary."""

    sanitized_command: list[str] = []
    for token in command:
        if not isinstance(token, str) or not token or "\0" in token:
            raise RunnerConfigurationError(
                "server command contains an invalid argument"
            )
        option = token.split("=", maxsplit=1)[0].lower()
        if option in CREDENTIAL_ARGUMENTS:
            raise RunnerConfigurationError(
                "credentials must never be passed on the server command line"
            )
        sanitized_command.append(token)
    if not sanitized_command:
        raise RunnerConfigurationError("server command must not be empty")
    return tuple(sanitized_command)


def _subprocess_group_options() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def _await_capture_task(task: asyncio.Task[None]) -> None:
    await asyncio.shield(task)


class ProcessSupervisor:
    """Own one external server process tree and all of its diagnostics."""

    def __init__(
        self,
        *,
        process: asyncio.subprocess.Process,
        command: tuple[str, ...],
        cwd: Path,
        policy: SupervisorPolicy,
        started_wall_time_ns: int,
        stdout_capture: _BoundedCapture,
        stderr_capture: _BoundedCapture,
        stdout_task: asyncio.Task[None],
        stderr_task: asyncio.Task[None],
        redaction_values: tuple[str, ...],
    ) -> None:
        self._process = process
        self.sanitized_command = command
        self.cwd = cwd
        self.policy = policy
        self.started_wall_time_ns = started_wall_time_ns
        self._stdout_capture = stdout_capture
        self._stderr_capture = stderr_capture
        self._capture_tasks = (stdout_task, stderr_task)
        self._redaction_values = redaction_values
        self._descendant_identities: dict[int, float] = {}
        self._outcome: ProcessOutcome | None = None
        self._requested_stop_exit_accepted = False
        self._stop_lock = asyncio.Lock()

    @classmethod
    async def start(
        cls,
        command: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        policy: SupervisorPolicy,
    ) -> ProcessSupervisor:
        safe_command = sanitize_command(command)
        resolved_cwd = cwd.resolve()
        if not resolved_cwd.is_dir():
            raise RunnerConfigurationError(
                "supervised process working directory does not exist"
            )
        if not environment:
            raise RunnerConfigurationError(
                "supervised process environment must be explicit"
            )
        started_wall_time_ns = time.time_ns()
        process = await asyncio.create_subprocess_exec(
            *safe_command,
            cwd=str(resolved_cwd),
            env=dict(environment),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_subprocess_group_options(),
        )
        if process.pid == os.getpid():
            raise ProcessSupervisorError(
                "supervised process unexpectedly reused the runner PID"
            )
        if process.stdout is None or process.stderr is None:
            process.kill()
            await asyncio.wait_for(
                process.wait(),
                timeout=policy.force_timeout_seconds,
            )
            raise ProcessSupervisorError("supervised process streams were not captured")
        stdout_capture = _BoundedCapture.create(policy.maximum_capture_bytes)
        stderr_capture = _BoundedCapture.create(policy.maximum_capture_bytes)
        stdout_task = asyncio.create_task(stdout_capture.drain(process.stdout))
        stderr_task = asyncio.create_task(stderr_capture.drain(process.stderr))
        return cls(
            process=process,
            command=safe_command,
            cwd=resolved_cwd,
            policy=policy,
            started_wall_time_ns=started_wall_time_ns,
            stdout_capture=stdout_capture,
            stderr_capture=stderr_capture,
            stdout_task=stdout_task,
            stderr_task=stderr_task,
            redaction_values=_credential_values(environment),
        )

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def is_running(self) -> bool:
        return self._process.returncode is None

    @property
    def outcome(self) -> ProcessOutcome | None:
        return self._outcome

    def descendant_pids(self) -> tuple[int, ...]:
        try:
            descendants = psutil.Process(self.pid).children(recursive=True)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            descendants = []
        except psutil.AccessDenied as error:
            raise ProcessSupervisorError(
                "access denied while enumerating process descendants"
            ) from error
        for descendant in descendants:
            try:
                self._descendant_identities[descendant.pid] = descendant.create_time()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied as error:
                raise ProcessSupervisorError(
                    "access denied while identifying a process descendant"
                ) from error
        return tuple(sorted(self._descendant_identities))

    def _matching_process(
        self,
        pid: int,
        expected_create_time: float,
    ) -> psutil.Process | None:
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - expected_create_time) > 0.01:
                return None
            if not process.is_running():
                return None
            if process.status() == psutil.STATUS_ZOMBIE:
                return None
            return process
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None
        except psutil.AccessDenied as error:
            raise ProcessSupervisorError(
                "access denied while verifying a process descendant"
            ) from error

    def _alive_descendants(self) -> list[psutil.Process]:
        alive: list[psutil.Process] = []
        for pid, create_time in self._descendant_identities.items():
            process = self._matching_process(pid, create_time)
            if process is not None:
                alive.append(process)
        return alive

    async def _wait_for_descendants(
        self,
        timeout_seconds: float,
    ) -> list[psutil.Process]:
        alive = self._alive_descendants()
        if not alive:
            return []
        _, remaining = await asyncio.wait_for(
            asyncio.to_thread(
                psutil.wait_procs,
                alive,
                timeout=timeout_seconds,
            ),
            timeout=timeout_seconds + 0.5,
        )
        return [
            process
            for process in remaining
            if self._matching_process(
                process.pid,
                self._descendant_identities[process.pid],
            )
            is not None
        ]

    async def _finish_capture(self) -> None:
        async def finish() -> None:
            async with asyncio.TaskGroup() as task_group:
                for task in self._capture_tasks:
                    task_group.create_task(_await_capture_task(task))

        try:
            await asyncio.wait_for(
                finish(),
                timeout=self.policy.force_timeout_seconds,
            )
        except TimeoutError as error:
            for task in self._capture_tasks:
                task.cancel()
            await asyncio.gather(*self._capture_tasks, return_exceptions=True)
            raise ProcessSupervisorError(
                "supervised process streams did not close before deadline"
            ) from error

    async def _finalize(
        self,
        *,
        graceful_stop: bool,
        forced_cleanup: bool,
    ) -> ProcessOutcome:
        if self._outcome is not None:
            return self._outcome
        if self._process.returncode is None:
            raise ProcessSupervisorError("cannot finalize a running supervised process")
        await self._finish_capture()
        self._outcome = ProcessOutcome(
            pid=self.pid,
            returncode=self._process.returncode,
            stdout=redact(
                self._stdout_capture.text(),
                self._redaction_values,
            ),
            stderr=redact(
                self._stderr_capture.text(),
                self._redaction_values,
            ),
            output_truncated=(
                self._stdout_capture.truncated or self._stderr_capture.truncated
            ),
            descendant_pids=tuple(sorted(self._descendant_identities)),
            graceful_stop=graceful_stop,
            forced_cleanup=forced_cleanup,
        )
        return self._outcome

    async def _force_process_tree(self) -> None:
        if os.name == "posix":
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError as error:
                raise ProcessSupervisorError(
                    "permission denied while force-killing process group"
                ) from error
        for descendant in reversed(self._alive_descendants()):
            try:
                descendant.kill()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied as error:
                raise ProcessSupervisorError(
                    "access denied while force-killing process descendant"
                ) from error
        if self._process.returncode is None:
            self._process.kill()
        if self._process.returncode is None:
            await asyncio.wait_for(
                self._process.wait(),
                timeout=self.policy.force_timeout_seconds,
            )
        alive = await self._wait_for_descendants(
            self.policy.force_timeout_seconds,
        )
        if alive:
            raise ProcessSupervisorError("process descendants survived forced cleanup")

    async def _settle_unexpected_exit(self) -> ProcessOutcome:
        self.descendant_pids()
        forced_cleanup = bool(self._alive_descendants())
        if forced_cleanup:
            await self._force_process_tree()
        return await self._finalize(
            graceful_stop=False,
            forced_cleanup=forced_cleanup,
        )

    async def wait_for_readiness(
        self,
        probe: Callable[[], bool | Awaitable[bool]],
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        timeout = (
            self.policy.startup_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise RunnerConfigurationError(
                "readiness timeout must be a finite positive number"
            )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            self.descendant_pids()
            if self._process.returncode is not None:
                outcome = await self._settle_unexpected_exit()
                raise ProcessExitedError(outcome)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ReadinessTimeoutError(
                    f"supervised process {self.pid} readiness timed out"
                )
            readiness = probe()
            if inspect.isawaitable(readiness):
                readiness = await asyncio.wait_for(
                    readiness,
                    timeout=remaining,
                )
            if readiness:
                if self._process.returncode is not None:
                    outcome = await self._settle_unexpected_exit()
                    raise ProcessExitedError(outcome)
                return
            await asyncio.sleep(min(self.policy.poll_interval_seconds, remaining))

    def _validated_worker_records(
        self,
        *,
        directory: Path,
        phase: str,
        expected_run_id: str,
        expected_count: int,
    ) -> tuple[dict[str, object], ...] | None:
        paths = sorted(directory.glob(f"{phase}-*.json"))
        if len(paths) > expected_count:
            raise WorkerEvidenceError(
                f"too many {phase} worker records for supervised process"
            )
        records: list[dict[str, object]] = []
        for path in paths:
            if path.stat().st_mtime_ns <= self.started_wall_time_ns:
                raise WorkerEvidenceError(
                    f"{phase} record predates the supervised process"
                )
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise WorkerEvidenceError(
                    f"{phase} worker record is not valid JSON"
                ) from error
            if not isinstance(payload, dict):
                raise WorkerEvidenceError(
                    f"{phase} worker record must be a JSON object"
                )
            if payload.get("phase") != phase:
                raise WorkerEvidenceError(f"{phase} worker record has the wrong phase")
            if payload.get("run_id") != expected_run_id:
                raise WorkerEvidenceError(f"{phase} worker record has the wrong run ID")
            pid = payload.get("pid")
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                raise WorkerEvidenceError(f"{phase} worker record has an invalid PID")
            records.append(payload)
        if len(records) < expected_count:
            return None
        pids = [record["pid"] for record in records]
        if len(set(pids)) != len(pids):
            raise WorkerEvidenceError(f"{phase} worker records contain duplicate PIDs")
        allowed_pids = {self.pid, *self.descendant_pids()}
        if not set(pids).issubset(allowed_pids):
            raise WorkerEvidenceError(
                f"{phase} worker record is outside the supervised process tree"
            )
        return tuple(sorted(records, key=lambda record: int(record["pid"])))

    async def wait_for_worker_records(
        self,
        *,
        directory: Path,
        phase: str,
        expected_run_id: str,
        expected_count: int,
        timeout_seconds: float | None = None,
    ) -> tuple[dict[str, object], ...]:
        if not directory.is_dir():
            raise RunnerConfigurationError("worker record directory does not exist")
        if phase not in {"ready", "shutdown"}:
            raise RunnerConfigurationError(
                "worker record phase must be ready or shutdown"
            )
        if (
            isinstance(expected_count, bool)
            or not isinstance(expected_count, int)
            or expected_count <= 0
        ):
            raise RunnerConfigurationError(
                "expected worker record count must be positive"
            )
        selected: tuple[dict[str, object], ...] | None = None

        def records_are_ready() -> bool:
            nonlocal selected
            selected = self._validated_worker_records(
                directory=directory,
                phase=phase,
                expected_run_id=expected_run_id,
                expected_count=expected_count,
            )
            return selected is not None

        await self.wait_for_readiness(
            records_are_ready,
            timeout_seconds=timeout_seconds,
        )
        if selected is None:
            raise ProcessSupervisorError(
                "worker readiness completed without selected records"
            )
        return selected

    def read_worker_records(
        self,
        *,
        directory: Path,
        phase: str,
        expected_run_id: str,
        expected_count: int,
    ) -> tuple[dict[str, object], ...]:
        selected = self._validated_worker_records(
            directory=directory,
            phase=phase,
            expected_run_id=expected_run_id,
            expected_count=expected_count,
        )
        if selected is None:
            raise WorkerEvidenceError(
                f"expected exactly {expected_count} {phase} worker records"
            )
        return selected

    def _request_graceful_stop(self) -> None:
        if self._process.returncode is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except PermissionError as error:
                raise ProcessSupervisorError(
                    "permission denied while stopping process group"
                ) from error
            return
        selected = getattr(signal, "CTRL_BREAK_EVENT", None)
        if selected is None:
            self._process.terminate()
        else:
            self._process.send_signal(selected)

    async def stop(self) -> ProcessOutcome:
        async with self._stop_lock:
            if self._outcome is not None:
                if self._outcome.forced_cleanup:
                    raise ForcedProcessCleanupError(self._outcome)
                if (
                    self._outcome.returncode != 0
                    and not self._requested_stop_exit_accepted
                ):
                    raise ProcessExitedError(self._outcome)
                return self._outcome

            self.descendant_pids()
            forced_cleanup = False
            requested_graceful_stop = self._process.returncode is None
            if requested_graceful_stop:
                self._request_graceful_stop()
                try:
                    await asyncio.wait_for(
                        self._process.wait(),
                        timeout=self.policy.graceful_timeout_seconds,
                    )
                except TimeoutError:
                    forced_cleanup = True
                    await self._force_process_tree()
            else:
                forced_cleanup = bool(self._alive_descendants())
                if forced_cleanup:
                    await self._force_process_tree()

            if not forced_cleanup:
                alive = await self._wait_for_descendants(
                    self.policy.force_timeout_seconds,
                )
                if alive:
                    forced_cleanup = True
                    await self._force_process_tree()

            outcome = await self._finalize(
                graceful_stop=(
                    requested_graceful_stop
                    and os.name == "posix"
                    and not forced_cleanup
                    and self._process.returncode == 0
                ),
                forced_cleanup=forced_cleanup,
            )
            self._requested_stop_exit_accepted = outcome.returncode == 0 or (
                requested_graceful_stop
                and os.name == "posix"
                and outcome.returncode == -signal.SIGTERM
            )
            if forced_cleanup:
                raise ForcedProcessCleanupError(outcome)
            if outcome.returncode != 0 and not self._requested_stop_exit_accepted:
                raise ProcessExitedError(outcome)
            return outcome

    async def __aenter__(self) -> ProcessSupervisor:
        return self

    async def __aexit__(
        self,
        exception_type,
        exception,
        traceback,
    ) -> bool:
        del exception_type, traceback
        if self._outcome is not None:
            return False
        cleanup = asyncio.create_task(self.stop())
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise
        except BaseException as cleanup_error:
            if exception is not None:
                raise BaseExceptionGroup(
                    "process body and cleanup both failed",
                    [exception, cleanup_error],
                ) from None
            raise
        return False


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def loopback_port_is_listening(port: int) -> bool:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
        raise RunnerConfigurationError("port must be between 1 and 65,535")
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=0.2,
        )
    except (OSError, TimeoutError):
        return False
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=0.2)
    except (OSError, TimeoutError):
        return True
    return True


def _is_port_collision(error: ProcessExitedError) -> bool:
    output = f"{error.stdout}\n{error.stderr}".lower()
    return any(
        marker in output
        for marker in (
            "address already in use",
            "connection in use",
            "only one usage of each socket address",
        )
    )


async def launch_with_port_retry(
    *,
    command_builder: Callable[[int], Sequence[str]],
    cwd: Path,
    environment: Mapping[str, str],
    readiness_probe: Callable[[int], bool | Awaitable[bool]],
    policy: SupervisorPolicy,
    port_allocator: Callable[[], int] = reserve_loopback_port,
) -> ServerLaunch:
    attempted_ports: list[int] = []
    for attempt in range(1, policy.port_retry_attempts + 1):
        port = port_allocator()
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65_535
        ):
            raise RunnerConfigurationError("port allocator returned an invalid port")
        attempted_ports.append(port)
        supervisor = await ProcessSupervisor.start(
            command_builder(port),
            cwd=cwd,
            environment=environment,
            policy=policy,
        )
        try:
            await supervisor.wait_for_readiness(
                lambda: readiness_probe(port),
            )
        except ProcessExitedError as error:
            if error.outcome.forced_cleanup:
                raise ForcedProcessCleanupError(error.outcome) from error
            if not _is_port_collision(error):
                raise
            if attempt == policy.port_retry_attempts:
                raise PortCollisionError(attempted_ports) from error
            continue
        except BaseException:
            if supervisor.outcome is None:
                cleanup = asyncio.create_task(supervisor.stop())
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise
            raise
        return ServerLaunch(
            supervisor=supervisor,
            port=port,
            attempts=attempt,
            sanitized_command=supervisor.sanitized_command,
        )
    raise PortCollisionError(attempted_ports)


MAX_HTTP_PROBE_BYTES = 1_048_576


def _http_get_json_sync(
    port: int,
    path: str,
    timeout_seconds: float,
) -> dict[str, object]:
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        port,
        timeout=timeout_seconds,
    )
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "application/json",
                "Connection": "close",
                "Host": "127.0.0.1",
            },
        )
        response = connection.getresponse()
        body = response.read(MAX_HTTP_PROBE_BYTES + 1)
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        raise HttpProbeError("loopback HTTP probe failed") from error
    finally:
        connection.close()
    if response.status != 200:
        raise HttpProbeError(f"loopback HTTP probe returned status {response.status}")
    if len(body) > MAX_HTTP_PROBE_BYTES:
        raise HttpProbeError("loopback HTTP response exceeded its byte limit")
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise HttpProbeError("loopback HTTP response is not valid JSON") from error
    if not isinstance(payload, dict):
        raise HttpProbeError("loopback HTTP response must be a JSON object")
    return payload


async def http_get_json(
    port: int,
    path: str,
    *,
    timeout_seconds: float = 1.0,
) -> dict[str, object]:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
        raise RunnerConfigurationError("port must be between 1 and 65,535")
    if not path.startswith("/") or "\r" in path or "\n" in path or " " in path:
        raise RunnerConfigurationError("HTTP probe path is unsafe")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise RunnerConfigurationError(
            "HTTP probe timeout must be a finite positive number"
        )
    return await asyncio.wait_for(
        asyncio.to_thread(
            _http_get_json_sync,
            port,
            path,
            timeout_seconds,
        ),
        timeout=timeout_seconds + 0.5,
    )


async def run_offline_smoke_profile(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    repository_root: Path,
    run_id: str,
    policy: SupervisorPolicy,
) -> OfflineSmokeResult:
    artifact_directory = (config.run_root / "worker-records" / profile.id).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
    )
    observed_package: dict[str, object] | None = None

    async def package_is_ready(port: int) -> bool:
        nonlocal observed_package
        try:
            observed_package = await http_get_json(
                port,
                "/package",
                timeout_seconds=0.5,
            )
        except HttpProbeError:
            return False
        return True

    launch = await launch_with_port_retry(
        command_builder=lambda port: build_server_command(
            config,
            profile,
            port=port,
        ),
        cwd=isolated.root,
        environment=environment,
        readiness_probe=package_is_ready,
        policy=policy,
    )
    supervisor = launch.supervisor
    async with supervisor:
        ready_records = await supervisor.wait_for_worker_records(
            directory=artifact_directory,
            phase="ready",
            expected_run_id=run_id,
            expected_count=profile.workers,
        )
        package = observed_package
        if package is None:
            package = await http_get_json(
                launch.port,
                "/package",
                timeout_seconds=1.0,
            )
        package = validate_package_record(
            config,
            isolated,
            package,
            repository_root=repository_root,
        )
        ready_pids = tuple(sorted(int(record["pid"]) for record in ready_records))
        if int(package["pid"]) not in ready_pids:
            raise CandidateProvenanceError(
                "package response PID is absent from worker readiness"
            )
        outcome = await supervisor.stop()

    shutdown_records = supervisor.read_worker_records(
        directory=artifact_directory,
        phase="shutdown",
        expected_run_id=run_id,
        expected_count=profile.workers,
    )
    shutdown_pids = tuple(sorted(int(record["pid"]) for record in shutdown_records))
    if ready_pids != shutdown_pids:
        raise WorkerEvidenceError("ready and shutdown worker PIDs do not reconcile")
    listening_sockets_after = (
        (launch.port,) if await loopback_port_is_listening(launch.port) else ()
    )
    if listening_sockets_after:
        raise ProcessSupervisorError("loopback listener survived supervised shutdown")
    return OfflineSmokeResult(
        profile_id=profile.id,
        port=launch.port,
        launch_attempts=launch.attempts,
        sanitized_command=launch.sanitized_command,
        package=package,
        ready_pids=ready_pids,
        shutdown_pids=shutdown_pids,
        manager_pid=outcome.pid,
        descendant_pids=outcome.descendant_pids,
        returncode=outcome.returncode,
        graceful_stop=outcome.graceful_stop,
        forced_cleanup=outcome.forced_cleanup,
        listening_sockets_after=listening_sockets_after,
    )


async def run_offline_smoke_matrix(
    config: RunnerConfig,
    *,
    repository_root: Path,
    source_directory: Path,
    policy: SupervisorPolicy | None = None,
) -> tuple[OfflineSmokeResult, ...]:
    if config.database_mode != "offline":
        raise RunnerConfigurationError(
            "offline smoke matrix requires offline database mode"
        )
    selected_policy = policy or SupervisorPolicy()
    isolated = prepare_isolated_application(
        config,
        source_directory=source_directory,
        directory_name="offline-smoke-application",
    )
    results: list[OfflineSmokeResult] = []
    for index, profile in enumerate(
        offline_smoke_profiles(config.platform_system),
        start=1,
    ):
        results.append(
            await run_offline_smoke_profile(
                config,
                isolated,
                profile,
                repository_root=repository_root,
                run_id=f"offline-{config.candidate_sha[:8]}-{index}",
                policy=selected_policy,
            )
        )
    verify_isolated_application(isolated)
    return tuple(results)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the installed-wheel production framework matrix",
    )
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--wheel-sha256", required=True)
    parser.add_argument("--venv-python", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--database-mode",
        required=True,
        choices=("sql_auth", "offline"),
    )
    parser.add_argument(
        "--global-connection-budget",
        required=True,
        type=int,
    )
    parser.add_argument("--operations", action="append", type=int)
    parser.add_argument("--allow-extended", action="store_true")
    return parser


def parse_cli(arguments: Sequence[str]) -> RunnerConfig:
    namespace = _parser().parse_args(arguments)
    if SHA_PATTERN.fullmatch(namespace.candidate_sha) is None:
        raise RunnerConfigurationError(
            "candidate SHA must be exactly 40 lowercase hexadecimal characters"
        )
    if SHA256_PATTERN.fullmatch(namespace.wheel_sha256) is None:
        raise RunnerConfigurationError(
            "wheel SHA-256 must be exactly 64 lowercase hexadecimal characters"
        )
    wheel = namespace.wheel.resolve()
    if not wheel.is_file():
        raise RunnerConfigurationError("candidate wheel does not exist")
    venv_python = Path(os.path.abspath(namespace.venv_python))
    if not venv_python.is_file():
        raise RunnerConfigurationError(
            "isolated virtual-environment Python does not exist"
        )
    run_root = namespace.run_root.resolve()
    if not run_root.is_dir():
        raise RunnerConfigurationError("run root must be an existing directory")
    output = namespace.output.resolve()
    if not output.is_relative_to(run_root):
        raise RunnerConfigurationError("output must be contained by the run root")
    if output.exists():
        raise RunnerConfigurationError("output artifact already exists")
    global_budget = namespace.global_connection_budget
    if global_budget <= 0 or any(global_budget % workers for workers in WORKER_COUNTS):
        raise RunnerConfigurationError(
            "global connection budget must be positive and divide 1/2/4/8 workers"
        )
    requested = namespace.operations or [REQUIRED_OPERATIONS]
    if len(set(requested)) != len(requested):
        raise RunnerConfigurationError("duplicate operation profile")
    operations = tuple(
        validate_operations(
            operation_count,
            allow_extended=namespace.allow_extended,
        )
        for operation_count in requested
    )
    if tuple(sorted(operations)) != operations:
        raise RunnerConfigurationError("operation profiles must be in increasing order")
    return RunnerConfig(
        candidate_sha=namespace.candidate_sha,
        wheel=wheel,
        wheel_sha256=namespace.wheel_sha256,
        venv_python=venv_python,
        run_root=run_root,
        output=output,
        database_mode=namespace.database_mode,
        global_connection_budget=global_budget,
        operations=operations,
        allow_extended=namespace.allow_extended,
        platform_system=normalize_platform(sys.platform),
    )


def main(arguments: Sequence[str] | None = None) -> int:
    config = parse_cli(sys.argv[1:] if arguments is None else arguments)
    if config.database_mode != "offline":
        raise RunnerConfigurationError("SQL-auth process execution is not implemented")
    _verify_candidate_wheel(config)
    repository_root = Path(__file__).resolve().parents[2]
    results = asyncio.run(
        run_offline_smoke_matrix(
            config,
            repository_root=repository_root,
            source_directory=repository_root / "tests/production_framework",
        )
    )
    config.output.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic_json(
        config.output,
        {
            "candidate_sha": config.candidate_sha,
            "database_mode": config.database_mode,
            "overall": "PASS",
            "platform_system": config.platform_system,
            "profiles": [
                result.to_record()
                for result in sorted(results, key=lambda result: result.profile_id)
            ],
            "schema_version": SCHEMA_VERSION,
            "violations": [],
            "wheel": {
                "filename": config.wheel.name,
                "sha256": config.wheel_sha256,
            },
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
