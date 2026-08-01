#!/usr/bin/env python3
"""Real-process production framework matrix orchestrator."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
import hashlib
import http.client
import importlib
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
from typing import Any, Protocol, Sequence

import httpx
import psutil


SCHEMA_VERSION = 1
WORKER_COUNTS = (1, 2, 4, 8)
REQUIRED_OPERATIONS = 1_000
LARGE_OPERATIONS = 10_000
EXTENDED_OPERATIONS = 99_999
MAX_OPERATIONS = 99_999
MIN_SQL_BIGINT = -(2**63)
MAX_SQL_BIGINT = 2**63 - 1
MAX_HTTP_STREAM_ROWS = 10_000
MAX_NDJSON_LINE_BYTES = 256
EXPECTED_STREAM_BUFFER_ROWS = 8
NATIVE_ASGI_EXECUTION_MODEL = (
    "native ASGI: concurrent requests on persistent event loop"
)
FLASK_WSGI_EXECUTION_MODEL = (
    "Flask WSGI: async view, occupied WSGI worker/thread"
)
ADAPTED_FLASK_EXECUTION_MODEL = (
    "Flask via WsgiToAsgi: persistent ASGI loop, thread-sensitive WSGI "
    "serialization per process"
)
EXECUTION_MODEL_LABELS = (
    NATIVE_ASGI_EXECUTION_MODEL,
    FLASK_WSGI_EXECUTION_MODEL,
    ADAPTED_FLASK_EXECUTION_MODEL,
)
FORBIDDEN_EXECUTION_MODEL_CLAIMS = (
    "equivalent",
    "fully async flask",
    "fully asynchronous flask",
    "native asgi flask",
    "same throughput",
    "true-async flask",
)
GUNICORN_WINDOWS_REASON = "Gunicorn is not supported on Windows"
UVLOOP_WINDOWS_REASON = "uvloop is not supported on Windows"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SQL_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
SQL_SERVER_HOST_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:[\]_-]{0,252}")
OBSERVER_CONTEXT_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
MAX_SQL_SERVER_APPLICATION_NAME = 128
SQL_DELAY_MILLISECONDS = frozenset(
    {0, 50, 100, 200, 250, 500, 1_000, 2_000, 5_000}
)
ACQUIRE_TIMEOUT_MILLISECONDS = frozenset({100, 250, 500, 1_000, 5_000})
OBSERVER_SESSION_SQL = """
SELECT
    CAST(s.program_name AS NVARCHAR(128)) AS application_name,
    s.host_process_id,
    s.session_id,
    CASE WHEN r.session_id IS NULL THEN 0 ELSE 1 END AS has_request,
    s.context_info
FROM sys.dm_exec_sessions AS s
LEFT JOIN sys.dm_exec_requests AS r ON r.session_id = s.session_id
WHERE s.is_user_process = 1
  AND LEFT(s.program_name, LEN(@P1)) = @P1
  AND s.program_name <> @P2
""".strip()


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


def execution_model_labels() -> tuple[str, str, str]:
    """Return only the three approved measured execution-model labels."""

    return EXECUTION_MODEL_LABELS


def validate_execution_model_language(
    statements: Sequence[str],
) -> tuple[str, ...]:
    """Reject language that claims unmeasured framework equivalence."""

    if isinstance(statements, (str, bytes)) or not statements:
        raise RunnerConfigurationError(
            "execution-model language must be a non-empty string sequence"
        )
    validated: list[str] = []
    for statement in statements:
        if not isinstance(statement, str) or not statement.strip():
            raise RunnerConfigurationError(
                "execution-model language contains an invalid statement"
            )
        normalized = " ".join(statement.casefold().split())
        if any(
            forbidden in normalized
            for forbidden in FORBIDDEN_EXECUTION_MODEL_CLAIMS
        ):
            raise WorkerEvidenceError(
                "forbidden execution-model claim was generated"
            )
        validated.append(statement)
    return tuple(validated)


@dataclass(frozen=True, slots=True)
class LoopbackJsonResponse:
    """Bounded status-aware JSON evidence from one loopback request."""

    status_code: int
    payload: dict[str, object]
    elapsed_seconds: float

    def to_record(self) -> dict[str, object]:
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "payload": self.payload,
            "status_code": self.status_code,
        }


@dataclass(frozen=True, slots=True)
class FullNdjsonObservation:
    status_code: int
    content_type: str
    row_count: int
    value_digest: str
    bytes_received: int
    first_data_seconds: float
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class EarlyCloseNdjsonObservation:
    status_code: int
    content_type: str
    requested_rows: int
    prefix_rows: int
    prefix_digest: str
    bytes_received: int
    first_data_seconds: float
    close_seconds: float


@dataclass(slots=True, repr=False)
class RawLoopbackRequest:
    """One raw request whose peer remains connected until explicit close."""

    _reader: asyncio.StreamReader = field(repr=False)
    _writer: asyncio.StreamWriter = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def __repr__(self) -> str:
        return f"RawLoopbackRequest(closed={self._closed})"

    async def close(self, *, timeout_seconds: float) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise RunnerConfigurationError(
                "raw HTTP close timeout must be a finite positive number"
            )
        if self._closed:
            return
        self._closed = True
        self._writer.close()
        try:
            await asyncio.wait_for(
                self._writer.wait_closed(),
                timeout=timeout_seconds,
            )
        except (OSError, TimeoutError):
            self._writer.transport.abort()
            raise HttpProbeError("raw loopback HTTP close failed") from None


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


def require_harness_controlled_graceful_shutdown(
    *,
    returncode: int,
    graceful_stop: bool,
    forced_cleanup: bool,
) -> None:
    """Reject PASS evidence unless the harness requested a clean shutdown."""

    accepted_returncodes = {0}
    if os.name == "posix":
        accepted_returncodes.add(-signal.SIGTERM)
    if (
        isinstance(returncode, bool)
        or not isinstance(returncode, int)
        or not isinstance(graceful_stop, bool)
        or not isinstance(forced_cleanup, bool)
        or returncode not in accepted_returncodes
        or not graceful_stop
        or forced_cleanup
    ):
        raise WorkerEvidenceError(
            "production framework evidence requires harness-controlled "
            "graceful shutdown"
        )


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


@dataclass(frozen=True, slots=True)
class NativeScalingProfileResult:
    profile: ProcessProfile
    evidence: NativeScalingEvidence
    port: int
    launch_attempts: int
    sanitized_command: tuple[str, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]
    sessions_after: int

    def to_record(self) -> dict[str, object]:
        record = self.profile.to_record()
        record.update(self.evidence.to_record())
        record.update(
            {
                "descendant_pids": list(self.descendant_pids),
                "forced_cleanup": self.forced_cleanup,
                "graceful_stop": self.graceful_stop,
                "launch_attempts": self.launch_attempts,
                "listening_sockets_after": list(self.listening_sockets_after),
                "manager_pid": self.manager_pid,
                "port": self.port,
                "returncode": self.returncode,
                "sanitized_command": list(self.sanitized_command),
                "sessions_after": self.sessions_after,
                "shutdown_pids": list(self.shutdown_pids),
                "status": "PASS",
            }
        )
        return record


@dataclass(frozen=True, slots=True)
class FlaskWsgiProfileResult:
    profile: ProcessProfile
    scaling_evidence: NativeScalingEvidence
    wsgi_evidence: FlaskWsgiWaveEvidence
    port: int
    launch_attempts: int
    sanitized_command: tuple[str, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]
    sessions_after: int

    def to_record(self) -> dict[str, object]:
        require_harness_controlled_graceful_shutdown(
            returncode=self.returncode,
            graceful_stop=self.graceful_stop,
            forced_cleanup=self.forced_cleanup,
        )
        record = self.profile.to_record()
        record.update(self.scaling_evidence.to_record())
        record.update(self.wsgi_evidence.to_record())
        record.update(
            {
                "descendant_pids": list(self.descendant_pids),
                "forced_cleanup": self.forced_cleanup,
                "graceful_stop": self.graceful_stop,
                "launch_attempts": self.launch_attempts,
                "listening_sockets_after": list(
                    self.listening_sockets_after
                ),
                "manager_pid": self.manager_pid,
                "maximum_active_requests": max(
                    worker["maximum_active_requests"]
                    for worker in self.wsgi_evidence.worker_execution
                ),
                "port": self.port,
                "returncode": self.returncode,
                "sanitized_command": list(self.sanitized_command),
                "sessions_after": self.sessions_after,
                "shutdown_pids": list(self.shutdown_pids),
                "status": "PASS",
            }
        )
        return record


@dataclass(frozen=True, slots=True)
class AdaptedFlaskProfileResult:
    profile: ProcessProfile
    scaling_evidence: NativeScalingEvidence
    adapted_evidence: AdaptedFlaskScalingEvidence
    port: int
    launch_attempts: int
    sanitized_command: tuple[str, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]
    sessions_after: int

    def to_record(self) -> dict[str, object]:
        require_harness_controlled_graceful_shutdown(
            returncode=self.returncode,
            graceful_stop=self.graceful_stop,
            forced_cleanup=self.forced_cleanup,
        )
        record = self.profile.to_record()
        record.update(self.scaling_evidence.to_record())
        record.update(self.adapted_evidence.to_record())
        record.update(
            {
                "descendant_pids": list(self.descendant_pids),
                "forced_cleanup": self.forced_cleanup,
                "graceful_stop": self.graceful_stop,
                "launch_attempts": self.launch_attempts,
                "listening_sockets_after": list(
                    self.listening_sockets_after
                ),
                "manager_pid": self.manager_pid,
                "port": self.port,
                "returncode": self.returncode,
                "sanitized_command": list(self.sanitized_command),
                "sessions_after": self.sessions_after,
                "shutdown_pids": list(self.shutdown_pids),
                "status": "PASS",
            }
        )
        return record


@dataclass(frozen=True, slots=True)
class NativeConcurrencyScenarioResult:
    profile_id: str
    evidence: NativeConcurrencyEvidence
    ready_pids: tuple[int, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]
    sessions_after: int

    def to_record(self) -> dict[str, object]:
        record = self.evidence.to_record()
        record.update(
            {
                "descendant_pids": list(self.descendant_pids),
                "forced_cleanup": self.forced_cleanup,
                "graceful_stop": self.graceful_stop,
                "listening_sockets_after": list(self.listening_sockets_after),
                "manager_pid": self.manager_pid,
                "profile_id": self.profile_id,
                "ready_pids": list(self.ready_pids),
                "returncode": self.returncode,
                "sessions_after": self.sessions_after,
                "shutdown_pids": list(self.shutdown_pids),
            }
        )
        return record


@dataclass(frozen=True, slots=True)
class FlaskGatherScenarioResult:
    profile_id: str
    evidence: FlaskGatherEvidence
    ready_pids: tuple[int, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]
    sessions_after: int

    def to_record(self) -> dict[str, object]:
        require_harness_controlled_graceful_shutdown(
            returncode=self.returncode,
            graceful_stop=self.graceful_stop,
            forced_cleanup=self.forced_cleanup,
        )
        record = self.evidence.to_record()
        record.update(
            {
                "descendant_pids": list(self.descendant_pids),
                "forced_cleanup": self.forced_cleanup,
                "graceful_stop": self.graceful_stop,
                "listening_sockets_after": list(
                    self.listening_sockets_after
                ),
                "manager_pid": self.manager_pid,
                "profile_id": self.profile_id,
                "ready_pids": list(self.ready_pids),
                "returncode": self.returncode,
                "sessions_after": self.sessions_after,
                "shutdown_pids": list(self.shutdown_pids),
            }
        )
        return record


@dataclass(frozen=True, slots=True)
class AdaptedFlaskSerializationScenarioResult:
    profile_id: str
    evidence: AdaptedFlaskSerializationEvidence
    ready_pids: tuple[int, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]
    sessions_after: int

    def to_record(self) -> dict[str, object]:
        require_harness_controlled_graceful_shutdown(
            returncode=self.returncode,
            graceful_stop=self.graceful_stop,
            forced_cleanup=self.forced_cleanup,
        )
        record = self.evidence.to_record()
        record.update(
            {
                "descendant_pids": list(self.descendant_pids),
                "forced_cleanup": self.forced_cleanup,
                "graceful_stop": self.graceful_stop,
                "listening_sockets_after": list(
                    self.listening_sockets_after
                ),
                "manager_pid": self.manager_pid,
                "profile_id": self.profile_id,
                "ready_pids": list(self.ready_pids),
                "returncode": self.returncode,
                "sessions_after": self.sessions_after,
                "shutdown_pids": list(self.shutdown_pids),
            }
        )
        return record


@dataclass(frozen=True, slots=True)
class NativeDisconnectScenarioResult:
    profile_id: str
    evidence: DisconnectEvidence
    ready_pids: tuple[int, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]
    sessions_after: int

    def to_record(self) -> dict[str, object]:
        record = self.evidence.to_record()
        record.update(
            {
                "descendant_pids": list(self.descendant_pids),
                "forced_cleanup": self.forced_cleanup,
                "graceful_stop": self.graceful_stop,
                "listening_sockets_after": list(self.listening_sockets_after),
                "manager_pid": self.manager_pid,
                "profile_id": self.profile_id,
                "ready_pids": list(self.ready_pids),
                "returncode": self.returncode,
                "sessions_after": self.sessions_after,
                "shutdown_pids": list(self.shutdown_pids),
            }
        )
        return record


@dataclass(frozen=True, slots=True)
class NativeGracefulQueryScenarioResult:
    profile_id: str
    evidence: GracefulQueryEvidence
    ready_pids: tuple[int, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]
    sessions_after: int

    def to_record(self) -> dict[str, object]:
        record = self.evidence.to_record()
        record.update(
            {
                "descendant_pids": list(self.descendant_pids),
                "forced_cleanup": self.forced_cleanup,
                "graceful_stop": self.graceful_stop,
                "listening_sockets_after": list(self.listening_sockets_after),
                "manager_pid": self.manager_pid,
                "profile_id": self.profile_id,
                "ready_pids": list(self.ready_pids),
                "returncode": self.returncode,
                "sessions_after": self.sessions_after,
                "shutdown_pids": list(self.shutdown_pids),
            }
        )
        return record


@dataclass(frozen=True, slots=True)
class NativeGracefulTransactionScenarioResult:
    profile_id: str
    evidence: GracefulTransactionEvidence
    ready_pids: tuple[int, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]
    sessions_after: int

    def to_record(self) -> dict[str, object]:
        record = self.evidence.to_record()
        record.update(
            {
                "descendant_pids": list(self.descendant_pids),
                "forced_cleanup": self.forced_cleanup,
                "graceful_stop": self.graceful_stop,
                "listening_sockets_after": list(self.listening_sockets_after),
                "manager_pid": self.manager_pid,
                "profile_id": self.profile_id,
                "ready_pids": list(self.ready_pids),
                "returncode": self.returncode,
                "sessions_after": self.sessions_after,
                "shutdown_pids": list(self.shutdown_pids),
            }
        )
        return record


@dataclass(frozen=True, slots=True)
class NativeSaturationScenarioResult:
    profile_id: str
    evidence: NativeSaturationEvidence
    ready_pids: tuple[int, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]
    sessions_after: int

    def to_record(self) -> dict[str, object]:
        record = self.evidence.to_record()
        record.update(
            {
                "descendant_pids": list(self.descendant_pids),
                "forced_cleanup": self.forced_cleanup,
                "graceful_stop": self.graceful_stop,
                "listening_sockets_after": list(self.listening_sockets_after),
                "manager_pid": self.manager_pid,
                "profile_id": self.profile_id,
                "ready_pids": list(self.ready_pids),
                "returncode": self.returncode,
                "sessions_after": self.sessions_after,
                "shutdown_pids": list(self.shutdown_pids),
            }
        )
        return record


@dataclass(frozen=True, slots=True)
class NativeStreamingScenarioResult:
    profile_id: str
    evidence: NativeStreamingEvidence
    ready_pids: tuple[int, ...]
    shutdown_pids: tuple[int, ...]
    manager_pid: int
    descendant_pids: tuple[int, ...]
    returncode: int
    graceful_stop: bool
    forced_cleanup: bool
    listening_sockets_after: tuple[int, ...]
    sessions_after: int

    def to_record(self) -> dict[str, object]:
        record = self.evidence.to_record()
        record.update(
            {
                "descendant_pids": list(self.descendant_pids),
                "forced_cleanup": self.forced_cleanup,
                "graceful_stop": self.graceful_stop,
                "listening_sockets_after": list(self.listening_sockets_after),
                "manager_pid": self.manager_pid,
                "profile_id": self.profile_id,
                "ready_pids": list(self.ready_pids),
                "returncode": self.returncode,
                "sessions_after": self.sessions_after,
                "shutdown_pids": list(self.shutdown_pids),
            }
        )
        return record


class ObserverResultSource(Protocol):
    async def query(
        self,
        sql: str,
        params: list[object] | None = None,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class SqlAuthObserverSettings:
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.host, str)
            or SQL_SERVER_HOST_PATTERN.fullmatch(self.host) is None
        ):
            raise RunnerConfigurationError("observer host is invalid")
        if (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65_535
        ):
            raise RunnerConfigurationError("observer port is invalid")
        for field_name in ("database", "username"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or SQL_IDENTIFIER_PATTERN.fullmatch(value) is None
            ):
                raise RunnerConfigurationError(
                    f"observer {field_name} is invalid"
                )
        if (
            not isinstance(self.password, str)
            or not self.password
            or "\0" in self.password
        ):
            raise RunnerConfigurationError("observer password is invalid")


@dataclass(frozen=True, slots=True)
class ObserverApplicationSample:
    application_name: str
    host_process_ids: tuple[int, ...]
    sessions: int
    requests: int

    def to_record(self) -> dict[str, object]:
        return {
            "application_name": self.application_name,
            "host_process_ids": list(self.host_process_ids),
            "requests": self.requests,
            "sessions": self.sessions,
        }


@dataclass(frozen=True, slots=True)
class SqlObserverSample:
    applications: tuple[ObserverApplicationSample, ...]
    current_sessions: int
    current_requests: int
    maximum_sessions: int
    maximum_requests: int
    request_context_tokens: tuple[str, ...] = field(repr=False)

    def to_record(self) -> dict[str, object]:
        return {
            "applications": [
                application.to_record() for application in self.applications
            ],
            "current_requests": self.current_requests,
            "current_sessions": self.current_sessions,
            "maximum_requests": self.maximum_requests,
            "maximum_sessions": self.maximum_sessions,
            "request_context_token_sha256": [
                hashlib.sha256(token.encode("ascii")).hexdigest()
                for token in self.request_context_tokens
            ],
        }


@dataclass(frozen=True, slots=True)
class ObservedRequestWave:
    responses: tuple[LoopbackJsonResponse, ...]
    observer_sample: SqlObserverSample


@dataclass(frozen=True, slots=True)
class NativeScalingEvidence:
    """Validated cross-process evidence for one native FastAPI profile."""

    profile_id: str
    ready_pids: tuple[int, ...]
    worker_records: tuple[dict[str, object], ...]
    parameter_values: tuple[int, ...]
    pool_max_per_worker: int
    global_connection_budget: int
    maximum_aggregate_sql_sessions: int
    maximum_simultaneous_sql_requests: int

    def to_record(self) -> dict[str, object]:
        return {
            "global_connection_budget": self.global_connection_budget,
            "maximum_aggregate_sql_sessions": (
                self.maximum_aggregate_sql_sessions
            ),
            "maximum_simultaneous_sql_requests": (
                self.maximum_simultaneous_sql_requests
            ),
            "parameter_values": list(self.parameter_values),
            "pool_max_per_worker": self.pool_max_per_worker,
            "profile_id": self.profile_id,
            "ready_pids": list(self.ready_pids),
            "worker_records": [dict(record) for record in self.worker_records],
        }


@dataclass(frozen=True, slots=True)
class NativeConcurrencyEvidence:
    sequential_seconds: float
    concurrent_seconds: float
    health_seconds: float
    sql_delay_seconds: float
    maximum_simultaneous_sql_requests: int
    pool_active_after: int
    pool_pending_after: int

    def to_record(self) -> dict[str, object]:
        return {
            "concurrent_seconds": self.concurrent_seconds,
            "health_seconds": self.health_seconds,
            "maximum_simultaneous_sql_requests": (
                self.maximum_simultaneous_sql_requests
            ),
            "pool_active_after": self.pool_active_after,
            "pool_pending_after": self.pool_pending_after,
            "sequential_seconds": self.sequential_seconds,
            "sql_delay_seconds": self.sql_delay_seconds,
            "status": "PASS",
            "values_exact": True,
        }


@dataclass(frozen=True, slots=True)
class FlaskWsgiWaveEvidence:
    profile_id: str
    execution_model: str
    values: tuple[int, ...]
    thread_limit_per_worker: int
    worker_execution: tuple[dict[str, int], ...]
    wave_seconds: float
    sql_delay_seconds: float
    maximum_aggregate_sql_sessions: int
    maximum_simultaneous_sql_requests: int
    queued_request_proven: bool

    def to_record(self) -> dict[str, object]:
        return {
            "execution_model": self.execution_model,
            "loop_tokens_distinct_per_worker": True,
            "maximum_aggregate_sql_sessions": (
                self.maximum_aggregate_sql_sessions
            ),
            "maximum_simultaneous_sql_requests": (
                self.maximum_simultaneous_sql_requests
            ),
            "profile_id": self.profile_id,
            "queued_request_proven": self.queued_request_proven,
            "request_count": len(self.values),
            "sql_delay_seconds": self.sql_delay_seconds,
            "status": "PASS",
            "thread_limit_per_worker": self.thread_limit_per_worker,
            "values": list(self.values),
            "wave_seconds": self.wave_seconds,
            "worker_execution": [
                dict(record) for record in self.worker_execution
            ],
        }


@dataclass(frozen=True, slots=True)
class FlaskGatherEvidence:
    sequential_seconds: float
    concurrent_seconds: float
    outer_response_seconds: float
    sql_delay_seconds: float
    loop_token: int
    maximum_simultaneous_sql_requests: int
    pool_active_after: int
    pool_pending_after: int

    def to_record(self) -> dict[str, object]:
        return {
            "concurrent_seconds": self.concurrent_seconds,
            "execution_model": FLASK_WSGI_EXECUTION_MODEL,
            "loop_token": self.loop_token,
            "maximum_simultaneous_sql_requests": (
                self.maximum_simultaneous_sql_requests
            ),
            "outer_response_seconds": self.outer_response_seconds,
            "pool_active_after": self.pool_active_after,
            "pool_pending_after": self.pool_pending_after,
            "sequential_seconds": self.sequential_seconds,
            "sql_delay_seconds": self.sql_delay_seconds,
            "status": "PASS",
            "values_exact": True,
            "wsgi_request_slots": 1,
        }


@dataclass(frozen=True, slots=True)
class AdaptedFlaskScalingEvidence:
    profile_id: str
    persistent_worker_loops: tuple[dict[str, int], ...]
    maximum_aggregate_sql_sessions: int
    maximum_simultaneous_sql_requests: int

    def to_record(self) -> dict[str, object]:
        return {
            "execution_model": ADAPTED_FLASK_EXECUTION_MODEL,
            "maximum_aggregate_sql_sessions": (
                self.maximum_aggregate_sql_sessions
            ),
            "maximum_serialized_wsgi_calls_per_process": 1,
            "maximum_simultaneous_sql_requests": (
                self.maximum_simultaneous_sql_requests
            ),
            "persistent_worker_loops": [
                dict(record) for record in self.persistent_worker_loops
            ],
            "profile_id": self.profile_id,
            "status": "PASS",
        }


@dataclass(frozen=True, slots=True)
class AdaptedFlaskSerializationEvidence:
    values: tuple[int, ...]
    sequential_seconds: float
    concurrent_seconds: float
    sql_delay_seconds: float
    loop_token: int
    maximum_simultaneous_sql_requests: int
    pool_active_after: int
    pool_pending_after: int

    def to_record(self) -> dict[str, object]:
        return {
            "concurrent_seconds": self.concurrent_seconds,
            "execution_model": ADAPTED_FLASK_EXECUTION_MODEL,
            "loop_token": self.loop_token,
            "maximum_simultaneous_sql_requests": (
                self.maximum_simultaneous_sql_requests
            ),
            "multi_process_scaling": "additional worker processes only",
            "pool_active_after": self.pool_active_after,
            "pool_pending_after": self.pool_pending_after,
            "sequential_seconds": self.sequential_seconds,
            "serialization_ratio": (
                self.concurrent_seconds / self.sequential_seconds
            ),
            "sql_delay_seconds": self.sql_delay_seconds,
            "status": "PASS",
            "values": list(self.values),
            "wsgi_calls_per_process": 1,
        }


@dataclass(frozen=True, slots=True)
class DisconnectEvidence:
    context_token_sha256: str
    sql_requests_after: int
    pool_active_after: int
    pool_pending_after: int
    recovery_value: int

    def to_record(self) -> dict[str, object]:
        return {
            "connection_replaced": True,
            "context_token_sha256": self.context_token_sha256,
            "pool_active_after": self.pool_active_after,
            "pool_pending_after": self.pool_pending_after,
            "recovery_value": self.recovery_value,
            "sql_observed_before_close": True,
            "sql_requests_after": self.sql_requests_after,
            "status": "PASS",
            "transport": "raw-tcp-client-close",
        }


@dataclass(frozen=True, slots=True)
class GracefulQueryEvidence:
    context_token_sha256: str
    response_session_id: int
    response_value: int
    sql_delay_seconds: float

    def to_record(self) -> dict[str, object]:
        return {
            "context_token_sha256": self.context_token_sha256,
            "response_completed_after_signal": True,
            "response_session_id": self.response_session_id,
            "response_value": self.response_value,
            "sql_delay_seconds": self.sql_delay_seconds,
            "sql_observed_before_signal": True,
            "status": "PASS",
        }


@dataclass(frozen=True, slots=True)
class GracefulTransactionEvidence:
    context_token_sha256: str
    item_id: int
    outcome: str
    response_session_id: int
    durable_result: str

    def to_record(self) -> dict[str, object]:
        return {
            "context_token_sha256": self.context_token_sha256,
            "durable_result": self.durable_result,
            "item_id": self.item_id,
            "outcome": self.outcome,
            "response_completed_after_signal": True,
            "response_session_id": self.response_session_id,
            "sql_observed_before_signal": True,
            "status": "PASS",
            "transaction_holding_before_signal": True,
            "transaction_settled": True,
        }


@dataclass(frozen=True, slots=True)
class NativeSaturationEvidence:
    pool_max_per_worker: int
    admitted_holders: int
    admitted_waiters: int
    admission_capacity: int
    rejected_requests: int
    acquire_timeouts: int
    maximum_sql_sessions: int
    maximum_sql_requests: int
    observed_active_connections: int
    observed_pending_gets: int
    pool_get_timed_out_delta: int
    pool_active_after: int
    pool_pending_after: int
    admission_active_after: int
    recovery_value: int

    def to_record(self) -> dict[str, object]:
        return {
            "acquire_timeouts": self.acquire_timeouts,
            "admission_active_after": self.admission_active_after,
            "admission_capacity": self.admission_capacity,
            "admitted_holders": self.admitted_holders,
            "admitted_waiters": self.admitted_waiters,
            "maximum_sql_requests": self.maximum_sql_requests,
            "maximum_sql_sessions": self.maximum_sql_sessions,
            "observed_active_connections": (
                self.observed_active_connections
            ),
            "observed_pending_gets": self.observed_pending_gets,
            "pool_active_after": self.pool_active_after,
            "pool_get_timed_out_delta": self.pool_get_timed_out_delta,
            "pool_max_per_worker": self.pool_max_per_worker,
            "pool_pending_after": self.pool_pending_after,
            "recovery_value": self.recovery_value,
            "rejected_requests": self.rejected_requests,
            "status": "PASS",
        }


@dataclass(frozen=True, slots=True)
class NativeStreamingEvidence:
    driver_buffer_rows: int
    full_rows: int
    full_value_digest: str
    full_bytes_received: int
    full_first_data_seconds: float
    full_elapsed_seconds: float
    full_pool_active_after: int
    full_pool_pending_after: int
    early_requested_rows: int
    early_prefix_rows: int
    early_prefix_digest: str
    early_bytes_received: int
    early_first_data_seconds: float
    early_close_seconds: float
    early_sql_requests_after: int
    early_pool_active_after: int
    early_pool_pending_after: int
    rss_start_bytes: int
    rss_peak_bytes: int
    rss_end_bytes: int
    rss_growth_limit_bytes: int
    recovery_value: int

    def to_record(self) -> dict[str, object]:
        return {
            "driver_buffer_rows": self.driver_buffer_rows,
            "early_bytes_received": self.early_bytes_received,
            "early_client_closed": True,
            "early_close_seconds": self.early_close_seconds,
            "early_first_data_seconds": self.early_first_data_seconds,
            "early_pool_active_after": self.early_pool_active_after,
            "early_pool_pending_after": self.early_pool_pending_after,
            "early_prefix_digest": self.early_prefix_digest,
            "early_prefix_rows": self.early_prefix_rows,
            "early_requested_rows": self.early_requested_rows,
            "early_sql_requests_after": self.early_sql_requests_after,
            "full_bytes_received": self.full_bytes_received,
            "full_elapsed_seconds": self.full_elapsed_seconds,
            "full_first_data_seconds": self.full_first_data_seconds,
            "full_pool_active_after": self.full_pool_active_after,
            "full_pool_pending_after": self.full_pool_pending_after,
            "full_rows": self.full_rows,
            "full_value_digest": self.full_value_digest,
            "incremental_first_data": True,
            "recovery_value": self.recovery_value,
            "rss_end_bytes": self.rss_end_bytes,
            "rss_growth_bytes": self.rss_peak_bytes - self.rss_start_bytes,
            "rss_growth_limit_bytes": self.rss_growth_limit_bytes,
            "rss_peak_bytes": self.rss_peak_bytes,
            "rss_start_bytes": self.rss_start_bytes,
            "status": "PASS",
        }


def _observer_context_token(value: object) -> str | None:
    if isinstance(value, bytes):
        raw = value.rstrip(b"\0")
        if not raw:
            return None
        try:
            token = raw.decode("ascii")
        except UnicodeDecodeError:
            return "<noncanonical>"
    elif isinstance(value, str):
        token = value.rstrip("\0")
    else:
        return None
    if not token:
        return None
    if OBSERVER_CONTEXT_TOKEN_PATTERN.fullmatch(token) is None:
        return "<noncanonical>"
    return token


@dataclass(slots=True)
class SqlServerObserver:
    """Aggregate only this run's worker DMV state without retaining SQL text."""

    source: ObserverResultSource = field(repr=False)
    worker_prefix: str
    observer_application_name: str
    poll_interval_seconds: float = 0.025
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    sleep: Callable[[float], Awaitable[None]] = field(
        default=asyncio.sleep,
        repr=False,
    )
    maximum_sessions: int = field(default=0, init=False)
    maximum_requests: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        for field_name in ("worker_prefix", "observer_application_name"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or APPLICATION_DIRECTORY_PATTERN.fullmatch(value) is None
                or len(value) > MAX_SQL_SERVER_APPLICATION_NAME
            ):
                raise RunnerConfigurationError(
                    f"observer {field_name.replace('_', ' ')} is invalid"
                )
        if self.worker_prefix == self.observer_application_name:
            raise RunnerConfigurationError(
                "observer application name must differ from the worker prefix"
            )
        if (
            isinstance(self.poll_interval_seconds, bool)
            or not isinstance(self.poll_interval_seconds, (int, float))
            or not math.isfinite(self.poll_interval_seconds)
            or self.poll_interval_seconds <= 0
        ):
            raise RunnerConfigurationError(
                "observer poll interval must be a finite positive number"
            )

    async def sample(self) -> SqlObserverSample:
        worker_prefix = f"{self.worker_prefix}-"
        result = await self.source.query(
            OBSERVER_SESSION_SQL,
            [worker_prefix, self.observer_application_name],
        )
        rows = result.all()
        if not isinstance(rows, list):
            raise WorkerEvidenceError("SQL observer returned malformed rows")

        session_ids: dict[str, set[int]] = {}
        request_ids: dict[str, set[int]] = {}
        process_ids: dict[str, set[int]] = {}
        context_tokens: set[str] = set()
        try:
            for row in rows:
                application_name = str(row["application_name"])
                if (
                    not application_name.startswith(worker_prefix)
                    or application_name == self.observer_application_name
                ):
                    continue
                session_id = int(row["session_id"])
                host_process_id = int(row["host_process_id"])
                session_ids.setdefault(application_name, set()).add(session_id)
                process_ids.setdefault(application_name, set()).add(
                    host_process_id
                )
                if int(row["has_request"]):
                    request_ids.setdefault(application_name, set()).add(
                        session_id
                    )
                    token = _observer_context_token(row.get("context_info"))
                    if token is not None:
                        context_tokens.add(token)
        except (KeyError, TypeError, ValueError):
            raise WorkerEvidenceError(
                "SQL observer returned malformed worker identity data"
            ) from None

        applications = tuple(
            ObserverApplicationSample(
                application_name=application_name,
                host_process_ids=tuple(sorted(process_ids[application_name])),
                sessions=len(sessions),
                requests=len(request_ids.get(application_name, set())),
            )
            for application_name, sessions in sorted(session_ids.items())
        )
        current_sessions = sum(application.sessions for application in applications)
        current_requests = sum(application.requests for application in applications)
        self.maximum_sessions = max(self.maximum_sessions, current_sessions)
        self.maximum_requests = max(self.maximum_requests, current_requests)
        return SqlObserverSample(
            applications=applications,
            current_sessions=current_sessions,
            current_requests=current_requests,
            maximum_sessions=self.maximum_sessions,
            maximum_requests=self.maximum_requests,
            request_context_tokens=tuple(sorted(context_tokens)),
        )

    async def wait_for_zero_sessions(
        self,
        *,
        timeout_seconds: float,
    ) -> SqlObserverSample:
        return await self._wait_for_sample(
            lambda sample: sample.current_sessions == 0,
            timeout_seconds=timeout_seconds,
            timeout_message=(
                "worker SQL sessions did not reach zero within the bound"
            ),
        )

    async def wait_for_zero_requests(
        self,
        *,
        timeout_seconds: float,
    ) -> SqlObserverSample:
        return await self._wait_for_sample(
            lambda sample: sample.current_requests == 0,
            timeout_seconds=timeout_seconds,
            timeout_message=(
                "worker SQL requests did not reach zero within the bound"
            ),
        )

    async def wait_for_context_token(
        self,
        token: str,
        *,
        present: bool,
        timeout_seconds: float,
    ) -> SqlObserverSample:
        if (
            not isinstance(token, str)
            or OBSERVER_CONTEXT_TOKEN_PATTERN.fullmatch(token) is None
        ):
            raise RunnerConfigurationError("observer context token is invalid")
        if not isinstance(present, bool):
            raise RunnerConfigurationError(
                "observer context-token presence must be boolean"
            )
        return await self._wait_for_sample(
            lambda sample: (token in sample.request_context_tokens) is present,
            timeout_seconds=timeout_seconds,
            timeout_message=(
                "worker SQL context token did not reach the expected state "
                "within the bound"
            ),
        )

    async def wait_for_minimum_requests(
        self,
        minimum_requests: int,
        *,
        timeout_seconds: float,
    ) -> SqlObserverSample:
        if (
            isinstance(minimum_requests, bool)
            or not isinstance(minimum_requests, int)
            or minimum_requests <= 0
        ):
            raise RunnerConfigurationError(
                "observer minimum requests must be a positive integer"
            )
        return await self._wait_for_sample(
            lambda sample: sample.current_requests >= minimum_requests,
            timeout_seconds=timeout_seconds,
            timeout_message=(
                "worker SQL requests did not reach the required minimum "
                "within the bound"
            ),
        )

    async def wait_for_ready_workers(
        self,
        ready_records: Sequence[Mapping[str, object]],
        *,
        timeout_seconds: float,
    ) -> SqlObserverSample:
        expected = frozenset(_ready_worker_map(ready_records))

        def every_worker_is_visible(sample: SqlObserverSample) -> bool:
            observed = {
                application.application_name
                for application in sample.applications
            }
            if not observed.issubset(expected):
                raise WorkerEvidenceError(
                    "SQL observer found an unexpected worker identity"
                )
            return observed == expected

        return await self._wait_for_sample(
            every_worker_is_visible,
            timeout_seconds=timeout_seconds,
            timeout_message=(
                "not every ready worker SQL session appeared within the bound"
            ),
        )

    async def _wait_for_sample(
        self,
        predicate: Callable[[SqlObserverSample], bool],
        *,
        timeout_seconds: float,
        timeout_message: str,
    ) -> SqlObserverSample:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise RunnerConfigurationError(
                "observer timeout must be a finite positive number"
            )
        deadline = self.clock() + timeout_seconds
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise ReadinessTimeoutError(timeout_message)
            try:
                sample = await asyncio.wait_for(
                    self.sample(),
                    timeout=remaining,
                )
            except TimeoutError:
                raise ReadinessTimeoutError(timeout_message) from None
            if self.clock() > deadline:
                raise ReadinessTimeoutError(timeout_message)
            if predicate(sample):
                return sample
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise ReadinessTimeoutError(timeout_message)
            await self.sleep(min(self.poll_interval_seconds, remaining))


def shared_listener_sql_request_minimum(
    profile: ProcessProfile,
    *,
    request_count: int,
) -> int:
    """Select a meaningful minimum without assuming perfect worker dispatch."""

    allowed_families = {
        "flask-asgi-uvicorn-asyncio",
        "flask-asgi-uvicorn-uvloop",
        "flask-gunicorn-gthread",
        "flask-gunicorn-sync",
    }
    if (
        profile.family not in allowed_families
        or not profile.applicable
        or profile.workers <= 0
        or profile.threads_per_worker <= 0
    ):
        raise RunnerConfigurationError(
            "shared-listener SQL observation requires a Flask profile"
        )
    if (
        isinstance(request_count, bool)
        or not isinstance(request_count, int)
        or request_count <= 0
    ):
        raise RunnerConfigurationError(
            "shared-listener request count must be a positive integer"
        )
    if profile.workers == 1:
        return min(request_count, profile.threads_per_worker)
    return 1


async def await_observed_request_wave(
    observer: SqlServerObserver,
    request_tasks: Sequence[asyncio.Task[LoopbackJsonResponse]],
    *,
    minimum_requests: int,
    timeout_seconds: float,
) -> ObservedRequestWave:
    """Await SQL observation and HTTP settlement without hiding either error."""

    if (
        not request_tasks
        or any(not isinstance(task, asyncio.Task) for task in request_tasks)
        or len({id(task) for task in request_tasks}) != len(request_tasks)
    ):
        raise RunnerConfigurationError(
            "observed request wave requires distinct asyncio tasks"
        )
    if (
        isinstance(minimum_requests, bool)
        or not isinstance(minimum_requests, int)
        or minimum_requests <= 0
    ):
        raise RunnerConfigurationError(
            "observed request minimum must be a positive integer"
        )
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise RunnerConfigurationError(
            "observed request timeout must be a finite positive number"
        )

    deadline = time.monotonic() + timeout_seconds
    observation_task = asyncio.create_task(
        observer.wait_for_minimum_requests(
            minimum_requests,
            timeout_seconds=timeout_seconds,
        )
    )
    response_future = asyncio.gather(*request_tasks)
    try:
        done, _ = await asyncio.wait(
            (observation_task, response_future),
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise ReadinessTimeoutError(
                "observed HTTP request wave did not settle within the bound"
            )

        if response_future.done():
            responses = response_future.result()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError(
                    "SQL observation did not settle within the wave bound"
                )
            if not observation_task.done():
                try:
                    observer_sample = await asyncio.wait_for(
                        observation_task,
                        timeout=remaining,
                    )
                except TimeoutError:
                    raise ReadinessTimeoutError(
                        "SQL observation did not settle within the wave bound"
                    ) from None
            else:
                observer_sample = observation_task.result()
            return ObservedRequestWave(
                responses=tuple(responses),
                observer_sample=observer_sample,
            )

        observer_sample = observation_task.result()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReadinessTimeoutError(
                "HTTP responses did not settle within the wave bound"
            )
        try:
            responses = await asyncio.wait_for(
                asyncio.shield(response_future),
                timeout=remaining,
            )
        except TimeoutError:
            raise ReadinessTimeoutError(
                "HTTP responses did not settle within the wave bound"
            ) from None
        return ObservedRequestWave(
            responses=tuple(responses),
            observer_sample=observer_sample,
        )
    finally:
        if not observation_task.done():
            observation_task.cancel()
        if not response_future.done():
            response_future.cancel()
        for task in request_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(
            observation_task,
            response_future,
            *request_tasks,
            return_exceptions=True,
        )


def _ready_worker_map(
    ready_records: Sequence[Mapping[str, object]],
) -> dict[str, int]:
    if not ready_records:
        raise WorkerEvidenceError(
            "worker SQL sessions do not match ready records"
        )
    expected: dict[str, int] = {}
    try:
        for record in ready_records:
            if record["phase"] != "ready":
                raise ValueError
            application_name = record["worker_application_name"]
            pid = record["pid"]
            if (
                not isinstance(application_name, str)
                or APPLICATION_DIRECTORY_PATTERN.fullmatch(application_name) is None
                or not isinstance(pid, int)
                or isinstance(pid, bool)
                or pid <= 0
                or application_name in expected
                or application_name != f"{application_name.rsplit('-', 1)[0]}-{pid}"
            ):
                raise ValueError
            expected[application_name] = pid
    except (KeyError, TypeError, ValueError):
        raise WorkerEvidenceError(
            "worker SQL sessions do not match ready records"
        ) from None
    return expected


def reconcile_worker_sessions(
    sample: SqlObserverSample,
    ready_records: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, int], ...]:
    expected = _ready_worker_map(ready_records)

    observed_names = {
        application.application_name for application in sample.applications
    }
    if observed_names != set(expected):
        raise WorkerEvidenceError(
            "worker SQL sessions do not match ready records"
        )
    return tuple(sorted(expected.items()))


def _evidence_records_by_pid(
    records: Sequence[Mapping[str, object]],
    *,
    expected_pids: tuple[int, ...],
    record_name: str,
) -> dict[int, Mapping[str, object]]:
    selected: dict[int, Mapping[str, object]] = {}
    try:
        for record in records:
            pid = record["pid"]
            if (
                isinstance(pid, bool)
                or not isinstance(pid, int)
                or pid <= 0
                or pid in selected
            ):
                raise ValueError
            selected[pid] = record
    except (KeyError, TypeError, ValueError):
        raise WorkerEvidenceError(f"{record_name} worker evidence is malformed") from None
    if tuple(sorted(selected)) != expected_pids:
        raise WorkerEvidenceError(
            f"{record_name} worker evidence does not match ready PIDs"
        )
    return selected


def _finite_monotonic(record: Mapping[str, object], name: str) -> float:
    value = record.get(name)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise WorkerEvidenceError("worker pool lifecycle evidence is malformed")
    return float(value)


def _nonnegative_metric(record: Mapping[str, object], name: str) -> int:
    value = record.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WorkerEvidenceError("worker pool metrics are malformed")
    return value


def _validate_process_scaling_evidence(
    *,
    config: RunnerConfig,
    profile: ProcessProfile,
    ready_records: Sequence[Mapping[str, object]],
    package_records: Sequence[Mapping[str, object]],
    principal_records: Sequence[Mapping[str, object]],
    pool_records: Sequence[Mapping[str, object]],
    parameter_payloads: Sequence[Mapping[str, object]],
    expected_values: Sequence[int],
    expected_principal: str,
    observer_sample: SqlObserverSample,
    allowed_families: frozenset[str],
) -> NativeScalingEvidence:
    """Fail closed unless every worker/process/pool/SQL layer reconciles."""

    if (
        config.database_mode != "sql_auth"
        or profile.database_mode != "sql_auth"
        or not profile.applicable
        or profile.family not in allowed_families
        or profile.workers not in WORKER_COUNTS
        or config.global_connection_budget % profile.workers
    ):
        raise RunnerConfigurationError(
            "process scaling evidence requires an applicable SQL-auth profile"
        )
    if (
        not isinstance(expected_principal, str)
        or SQL_IDENTIFIER_PATTERN.fullmatch(expected_principal) is None
    ):
        raise RunnerConfigurationError("expected SQL principal is invalid")
    if (
        not expected_values
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in expected_values
        )
    ):
        raise RunnerConfigurationError("expected parameter values are invalid")

    ready_map = _ready_worker_map(ready_records)
    expected_pids = tuple(sorted(ready_map.values()))
    if len(expected_pids) != profile.workers:
        raise WorkerEvidenceError("ready worker count does not match the profile")
    ready_by_pid = _evidence_records_by_pid(
        ready_records,
        expected_pids=expected_pids,
        record_name="ready",
    )
    package_by_pid = _evidence_records_by_pid(
        package_records,
        expected_pids=expected_pids,
        record_name="package",
    )
    principal_by_pid = _evidence_records_by_pid(
        principal_records,
        expected_pids=expected_pids,
        record_name="principal",
    )
    pool_by_pid = _evidence_records_by_pid(
        pool_records,
        expected_pids=expected_pids,
        record_name="pool",
    )
    pool_max = config.global_connection_budget // profile.workers
    pool_identities: set[str] = set()
    worker_records: list[dict[str, object]] = []

    for pid in expected_pids:
        ready = ready_by_pid[pid]
        package = package_by_pid[pid]
        principal = principal_by_pid[pid]
        pool_record = pool_by_pid[pid]
        application_name = next(
            name for name, ready_pid in ready_map.items() if ready_pid == pid
        )
        if (
            ready.get("candidate_sha") != config.candidate_sha
            or ready.get("wheel_filename") != config.wheel.name
            or ready.get("wheel_sha256") != config.wheel_sha256
            or ready.get("pool_max_per_worker") != pool_max
            or ready.get("pool_created_pid") != pid
        ):
            raise WorkerEvidenceError("worker ready provenance is inconsistent")
        process_started = _finite_monotonic(
            ready,
            "process_started_monotonic",
        )
        pool_created = _finite_monotonic(ready, "pool_created_monotonic")
        pool_connected = _finite_monotonic(
            ready,
            "pool_connected_monotonic",
        )
        if not process_started <= pool_created <= pool_connected:
            raise WorkerEvidenceError("worker pool lifecycle ordering is invalid")
        pool_identity = ready.get("pool_identity")
        if (
            not isinstance(pool_identity, str)
            or not pool_identity
            or pool_identity in pool_identities
        ):
            raise WorkerEvidenceError("worker pool identity is invalid")
        pool_identities.add(pool_identity)

        import_path = package.get("fastmssql_import_path")
        if (
            package.get("candidate_sha") != config.candidate_sha
            or package.get("wheel_filename") != config.wheel.name
            or package.get("wheel_sha256") != config.wheel_sha256
            or not isinstance(import_path, str)
            or not import_path
        ):
            raise WorkerEvidenceError("worker package provenance is inconsistent")
        session_id = principal.get("session_id")
        if (
            principal.get("application_name") != application_name
            or principal.get("principal") != expected_principal
            or isinstance(session_id, bool)
            or not isinstance(session_id, int)
            or session_id <= 0
        ):
            raise WorkerEvidenceError("worker SQL principal evidence is inconsistent")

        if pool_record.get("application_name") != application_name:
            raise WorkerEvidenceError("worker pool identity is inconsistent")
        pool = pool_record.get("pool")
        admission = pool_record.get("admission")
        operations = pool_record.get("operations")
        if (
            not isinstance(pool, Mapping)
            or not isinstance(admission, Mapping)
            or not isinstance(operations, Mapping)
        ):
            raise WorkerEvidenceError("worker pool evidence is malformed")
        connections = _nonnegative_metric(pool, "connections")
        idle_connections = _nonnegative_metric(pool, "idle_connections")
        active_connections = _nonnegative_metric(pool, "active_connections")
        pending_gets = _nonnegative_metric(pool, "pending_gets")
        if (
            pool.get("max_size") != pool_max
            or connections > pool_max
            or idle_connections > connections
            or active_connections != connections - idle_connections
            or active_connections != 0
            or pending_gets != 0
            or admission.get("active") != 0
            or admission.get("capacity") != pool_max * 2
        ):
            raise WorkerEvidenceError("worker pool did not settle within its bounds")
        worker_records.append(
            {
                "application_name": application_name,
                "fastmssql_import_path": import_path,
                "pid": pid,
                "pool_connected_monotonic": pool_connected,
                "pool_created_monotonic": pool_created,
                "pool_created_pid": pid,
                "pool_identity": pool_identity,
                "principal": expected_principal,
                "process_started_monotonic": process_started,
            }
        )

    if len(parameter_payloads) != len(expected_values):
        raise WorkerEvidenceError("parameter wave response count is inconsistent")
    parameter_values: list[int] = []
    for payload, expected_value in zip(
        parameter_payloads,
        expected_values,
        strict=True,
    ):
        value = payload.get("value")
        session_id = payload.get("session_id")
        if (
            value != expected_value
            or isinstance(session_id, bool)
            or not isinstance(session_id, int)
            or session_id <= 0
        ):
            raise WorkerEvidenceError("parameter wave returned inconsistent SQL data")
        parameter_values.append(value)

    reconcile_worker_sessions(observer_sample, ready_records)
    if observer_sample.maximum_sessions > config.global_connection_budget:
        raise WorkerEvidenceError("observed SQL sessions exceeded the global connection budget")
    if observer_sample.maximum_requests <= 0:
        raise WorkerEvidenceError("parameter wave had no observed SQL request")
    return NativeScalingEvidence(
        profile_id=profile.id,
        ready_pids=expected_pids,
        worker_records=tuple(worker_records),
        parameter_values=tuple(parameter_values),
        pool_max_per_worker=pool_max,
        global_connection_budget=config.global_connection_budget,
        maximum_aggregate_sql_sessions=observer_sample.maximum_sessions,
        maximum_simultaneous_sql_requests=observer_sample.maximum_requests,
    )


def validate_native_scaling_evidence(
    *,
    config: RunnerConfig,
    profile: ProcessProfile,
    ready_records: Sequence[Mapping[str, object]],
    package_records: Sequence[Mapping[str, object]],
    principal_records: Sequence[Mapping[str, object]],
    pool_records: Sequence[Mapping[str, object]],
    parameter_payloads: Sequence[Mapping[str, object]],
    expected_values: Sequence[int],
    expected_principal: str,
    observer_sample: SqlObserverSample,
) -> NativeScalingEvidence:
    return _validate_process_scaling_evidence(
        config=config,
        profile=profile,
        ready_records=ready_records,
        package_records=package_records,
        principal_records=principal_records,
        pool_records=pool_records,
        parameter_payloads=parameter_payloads,
        expected_values=expected_values,
        expected_principal=expected_principal,
        observer_sample=observer_sample,
        allowed_families=frozenset(
            {
                "fastapi-gunicorn-uvicorn-worker",
                "fastapi-uvicorn-asyncio",
                "fastapi-uvicorn-uvloop",
            }
        ),
    )


def validate_flask_scaling_evidence(
    *,
    config: RunnerConfig,
    profile: ProcessProfile,
    ready_records: Sequence[Mapping[str, object]],
    package_records: Sequence[Mapping[str, object]],
    principal_records: Sequence[Mapping[str, object]],
    pool_records: Sequence[Mapping[str, object]],
    parameter_payloads: Sequence[Mapping[str, object]],
    expected_values: Sequence[int],
    expected_principal: str,
    observer_sample: SqlObserverSample,
) -> NativeScalingEvidence:
    return _validate_process_scaling_evidence(
        config=config,
        profile=profile,
        ready_records=ready_records,
        package_records=package_records,
        principal_records=principal_records,
        pool_records=pool_records,
        parameter_payloads=parameter_payloads,
        expected_values=expected_values,
        expected_principal=expected_principal,
        observer_sample=observer_sample,
        allowed_families=frozenset(
            {
                "flask-asgi-uvicorn-asyncio",
                "flask-asgi-uvicorn-uvloop",
                "flask-gunicorn-gthread",
                "flask-gunicorn-sync",
            }
        ),
    )


def validate_native_concurrency_evidence(
    *,
    sequential_payloads: Sequence[Mapping[str, object]],
    concurrent_payloads: Sequence[Mapping[str, object]],
    expected_values: Sequence[int],
    sequential_seconds: float,
    concurrent_seconds: float,
    health_seconds: float,
    sql_delay_ms: int,
    observer_sample: SqlObserverSample,
    pool_record: Mapping[str, object],
) -> NativeConcurrencyEvidence:
    """Validate same-run overlap without encoding an absolute throughput gate."""

    if sql_delay_ms not in SQL_DELAY_MILLISECONDS or sql_delay_ms <= 0:
        raise RunnerConfigurationError(
            "native concurrency requires a positive allowlisted SQL delay"
        )
    if (
        not expected_values
        or len(sequential_payloads) != len(expected_values)
        or len(concurrent_payloads) != len(expected_values)
    ):
        raise WorkerEvidenceError("native concurrency response count is inconsistent")
    for payloads in (sequential_payloads, concurrent_payloads):
        for payload, expected_value in zip(
            payloads,
            expected_values,
            strict=True,
        ):
            session_id = payload.get("session_id")
            if (
                payload.get("value") != expected_value
                or payload.get("delay_ms") != sql_delay_ms
                or isinstance(session_id, bool)
                or not isinstance(session_id, int)
                or session_id <= 0
            ):
                raise WorkerEvidenceError(
                    "native concurrency returned inconsistent SQL data"
                )
    for name, value in (
        ("sequential", sequential_seconds),
        ("concurrent", concurrent_seconds),
        ("health", health_seconds),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or (name != "health" and value == 0)
        ):
            raise WorkerEvidenceError(
                "native concurrency timing evidence is malformed"
            )
    if concurrent_seconds >= sequential_seconds * 0.70:
        raise WorkerEvidenceError(
            "concurrent SQL wave did not beat its same-run sequential baseline"
        )
    sql_delay_seconds = sql_delay_ms / 1_000
    if health_seconds >= sql_delay_seconds:
        raise WorkerEvidenceError(
            "native event-loop health request did not remain responsive"
        )
    if observer_sample.maximum_requests <= 1:
        raise WorkerEvidenceError(
            "SQL Server did not observe overlapping native requests"
        )
    pool = pool_record.get("pool")
    if not isinstance(pool, Mapping):
        raise WorkerEvidenceError("native concurrency pool evidence is malformed")
    active = _nonnegative_metric(pool, "active_connections")
    pending = _nonnegative_metric(pool, "pending_gets")
    connections = _nonnegative_metric(pool, "connections")
    max_size = _nonnegative_metric(pool, "max_size")
    if active != 0 or pending != 0 or max_size <= 0 or connections > max_size:
        raise WorkerEvidenceError(
            "native concurrency pool did not settle within its bounds"
        )
    return NativeConcurrencyEvidence(
        sequential_seconds=float(sequential_seconds),
        concurrent_seconds=float(concurrent_seconds),
        health_seconds=float(health_seconds),
        sql_delay_seconds=sql_delay_seconds,
        maximum_simultaneous_sql_requests=observer_sample.maximum_requests,
        pool_active_after=active,
        pool_pending_after=pending,
    )


def validate_flask_wsgi_wave_evidence(
    *,
    config: RunnerConfig,
    profile: ProcessProfile,
    ready_records: Sequence[Mapping[str, object]],
    response_payloads: Sequence[Mapping[str, object]],
    settled_records: Sequence[Mapping[str, object]],
    expected_values: Sequence[int],
    wave_seconds: float,
    sql_delay_ms: int,
    observer_sample: SqlObserverSample,
) -> FlaskWsgiWaveEvidence:
    """Validate real WSGI occupancy without implying ASGI concurrency."""

    expected_worker_class = {
        "flask-gunicorn-sync": ("sync", 1),
        "flask-gunicorn-gthread": ("gthread", 4),
    }.get(profile.family)
    if (
        config.database_mode != "sql_auth"
        or profile.database_mode != "sql_auth"
        or not profile.applicable
        or profile.server != "gunicorn"
        or expected_worker_class is None
        or (profile.worker_class, profile.threads_per_worker)
        != expected_worker_class
        or profile.workers not in WORKER_COUNTS
        or config.global_connection_budget % profile.workers
    ):
        raise RunnerConfigurationError(
            "Flask WSGI evidence requires an applicable SQL-auth profile"
        )
    if sql_delay_ms not in SQL_DELAY_MILLISECONDS or sql_delay_ms <= 0:
        raise RunnerConfigurationError(
            "Flask WSGI evidence requires a positive allowlisted SQL delay"
        )
    if (
        isinstance(wave_seconds, bool)
        or not isinstance(wave_seconds, (int, float))
        or not math.isfinite(wave_seconds)
        or wave_seconds <= 0
    ):
        raise WorkerEvidenceError("Flask WSGI timing evidence is malformed")
    if (
        not expected_values
        or len(response_payloads) != len(expected_values)
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in expected_values
        )
    ):
        raise WorkerEvidenceError(
            "Flask WSGI response count is inconsistent"
        )

    ready_map = _ready_worker_map(ready_records)
    expected_pids = tuple(sorted(ready_map.values()))
    if len(expected_pids) != profile.workers:
        raise WorkerEvidenceError(
            "Flask WSGI ready worker count is inconsistent"
        )
    settled_by_pid = _evidence_records_by_pid(
        settled_records,
        expected_pids=expected_pids,
        record_name="Flask WSGI settled",
    )
    reconcile_worker_sessions(observer_sample, ready_records)

    thread_limit = profile.threads_per_worker
    required_payload_keys = {
        "async_thread_token",
        "delay_ms",
        "execution_model",
        "loop_token",
        "pid",
        "request_sequence",
        "session_id",
        "value",
        "wsgi_active_requests",
        "wsgi_maximum_active_requests",
        "wsgi_thread_token",
    }
    loop_tokens: set[tuple[int, int]] = set()
    request_sequences: set[tuple[int, int]] = set()
    response_counts = {pid: 0 for pid in expected_pids}
    maximum_response_active = {pid: 0 for pid in expected_pids}
    response_thread_tokens = {pid: set() for pid in expected_pids}
    values: list[int] = []

    for payload, expected_value in zip(
        response_payloads,
        expected_values,
        strict=True,
    ):
        if set(payload) != required_payload_keys:
            raise WorkerEvidenceError(
                "Flask WSGI response evidence is malformed"
            )
        pid = payload.get("pid")
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid not in response_counts
        ):
            raise WorkerEvidenceError(
                "Flask WSGI response has an unexpected worker PID"
            )
        integer_fields = (
            "async_thread_token",
            "loop_token",
            "request_sequence",
            "session_id",
            "wsgi_active_requests",
            "wsgi_maximum_active_requests",
            "wsgi_thread_token",
        )
        if any(
            isinstance(payload.get(name), bool)
            or not isinstance(payload.get(name), int)
            or int(payload[name]) <= 0
            for name in integer_fields
        ):
            raise WorkerEvidenceError(
                "Flask WSGI response evidence is malformed"
            )
        if (
            payload.get("execution_model") != FLASK_WSGI_EXECUTION_MODEL
            or payload.get("delay_ms") != sql_delay_ms
            or payload.get("value") != expected_value
        ):
            raise WorkerEvidenceError(
                "Flask WSGI response returned inconsistent SQL data"
            )
        active = int(payload["wsgi_active_requests"])
        maximum_active = int(payload["wsgi_maximum_active_requests"])
        if active > thread_limit or not active <= maximum_active <= thread_limit:
            if profile.worker_class == "gthread":
                raise WorkerEvidenceError(
                    "gthread WSGI concurrency exceeded four threads"
                )
            raise WorkerEvidenceError(
                "sync WSGI concurrency exceeded one request slot"
            )
        loop_key = (pid, int(payload["loop_token"]))
        if loop_key in loop_tokens:
            raise WorkerEvidenceError(
                "Flask per-request event loops are not distinct"
            )
        loop_tokens.add(loop_key)
        sequence_key = (pid, int(payload["request_sequence"]))
        if sequence_key in request_sequences:
            raise WorkerEvidenceError(
                "Flask WSGI request sequence is not unique"
            )
        request_sequences.add(sequence_key)
        response_counts[pid] += 1
        maximum_response_active[pid] = max(
            maximum_response_active[pid],
            maximum_active,
        )
        response_thread_tokens[pid].add(
            int(payload["wsgi_thread_token"])
        )
        if len(response_thread_tokens[pid]) > thread_limit:
            raise WorkerEvidenceError(
                "Flask WSGI response used too many worker threads"
            )
        values.append(expected_value)

    required_settled_keys = {
        "active_other_requests",
        "completed_requests",
        "execution_model",
        "maximum_active_requests",
        "pid",
        "wsgi_thread_count",
    }
    worker_execution: list[dict[str, int]] = []
    for pid in expected_pids:
        settled = settled_by_pid[pid]
        if set(settled) != required_settled_keys:
            raise WorkerEvidenceError(
                "Flask WSGI settled evidence is malformed"
            )
        maximum_active = settled.get("maximum_active_requests")
        thread_count = settled.get("wsgi_thread_count")
        completed = settled.get("completed_requests")
        malformed = (
            settled.get("execution_model") != FLASK_WSGI_EXECUTION_MODEL
            or settled.get("active_other_requests") != 0
            or isinstance(maximum_active, bool)
            or not isinstance(maximum_active, int)
            or isinstance(thread_count, bool)
            or not isinstance(thread_count, int)
            or isinstance(completed, bool)
            or not isinstance(completed, int)
        )
        if response_counts[pid] == 0:
            malformed = malformed or (
                maximum_active != 0
                or thread_count != 0
                or completed != 0
            )
        else:
            malformed = malformed or (
                not 1 <= maximum_active <= thread_limit
                or not maximum_active <= thread_count <= thread_limit
                or completed < response_counts[pid]
                or maximum_active < maximum_response_active[pid]
            )
        if malformed:
            if (
                profile.worker_class == "gthread"
                and isinstance(maximum_active, int)
                and not isinstance(maximum_active, bool)
                and maximum_active > 4
            ):
                raise WorkerEvidenceError(
                    "gthread WSGI concurrency exceeded four threads"
                )
            raise WorkerEvidenceError(
                "Flask WSGI worker did not settle within its request limit"
            )
        worker_execution.append(
            {
                "maximum_active_requests": maximum_active,
                "pid": pid,
                "wsgi_thread_count": thread_count,
            }
        )

    if (
        observer_sample.maximum_sessions > config.global_connection_budget
        or observer_sample.current_sessions > config.global_connection_budget
    ):
        raise WorkerEvidenceError(
            "Flask WSGI SQL sessions exceeded the global connection budget"
        )
    maximum_request_slots = profile.workers * thread_limit
    if not 1 <= observer_sample.maximum_requests <= maximum_request_slots:
        raise WorkerEvidenceError(
            "Flask WSGI SQL request concurrency exceeded its worker slots"
        )

    queued_request_proven = profile.workers == 1
    if queued_request_proven:
        expected_request_count = thread_limit + 1
        if (
            len(response_payloads) != expected_request_count
            or observer_sample.maximum_requests != thread_limit
            or worker_execution[0]["maximum_active_requests"] != thread_limit
            or float(wave_seconds) < (sql_delay_ms / 1_000) * 1.5
        ):
            raise WorkerEvidenceError(
                "Flask WSGI one-worker queue was not demonstrated"
            )

    return FlaskWsgiWaveEvidence(
        profile_id=profile.id,
        execution_model=FLASK_WSGI_EXECUTION_MODEL,
        values=tuple(values),
        thread_limit_per_worker=thread_limit,
        worker_execution=tuple(worker_execution),
        wave_seconds=float(wave_seconds),
        sql_delay_seconds=sql_delay_ms / 1_000,
        maximum_aggregate_sql_sessions=observer_sample.maximum_sessions,
        maximum_simultaneous_sql_requests=observer_sample.maximum_requests,
        queued_request_proven=queued_request_proven,
    )


def validate_flask_gather_evidence(
    *,
    response: LoopbackJsonResponse,
    state_record: Mapping[str, object],
    observer_sample: SqlObserverSample,
    pool_record: Mapping[str, object],
    sql_delay_ms: int,
) -> FlaskGatherEvidence:
    """Prove same-view SQL overlap while exactly one WSGI slot is held."""

    if sql_delay_ms not in SQL_DELAY_MILLISECONDS or sql_delay_ms <= 0:
        raise RunnerConfigurationError(
            "Flask gather evidence requires a positive allowlisted SQL delay"
        )
    required_payload_keys = {
        "async_thread_token",
        "concurrent",
        "concurrent_seconds",
        "execution_model",
        "loop_token",
        "pid",
        "request_sequence",
        "sequential",
        "sequential_seconds",
        "wsgi_active_requests",
        "wsgi_maximum_active_requests",
        "wsgi_thread_token",
    }
    payload = response.payload
    if response.status_code != 200 or set(payload) != required_payload_keys:
        raise WorkerEvidenceError("Flask gather response evidence is malformed")
    if (
        payload.get("execution_model") != FLASK_WSGI_EXECUTION_MODEL
        or payload.get("sequential") != [0, 1, 2, 3]
        or payload.get("concurrent") != [0, 1, 2, 3]
        or payload.get("wsgi_active_requests") != 1
        or payload.get("wsgi_maximum_active_requests") != 1
    ):
        raise WorkerEvidenceError(
            "Flask gather did not remain inside one WSGI request slot"
        )
    for name in (
        "async_thread_token",
        "loop_token",
        "pid",
        "request_sequence",
        "wsgi_thread_token",
    ):
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise WorkerEvidenceError(
                "Flask gather execution identity is malformed"
            )
    sequential_seconds = payload.get("sequential_seconds")
    concurrent_seconds = payload.get("concurrent_seconds")
    for value in (
        sequential_seconds,
        concurrent_seconds,
        response.elapsed_seconds,
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise WorkerEvidenceError("Flask gather timing evidence is malformed")
    if float(concurrent_seconds) >= float(sequential_seconds) * 0.70:
        raise WorkerEvidenceError(
            "Flask same-view concurrent SQL did not beat its sequential baseline"
        )
    sql_delay_seconds = sql_delay_ms / 1_000
    if (
        float(sequential_seconds) < sql_delay_seconds * 3.0
        or float(concurrent_seconds) < sql_delay_seconds * 0.75
        or response.elapsed_seconds
        < (float(sequential_seconds) + float(concurrent_seconds)) * 0.90
    ):
        raise WorkerEvidenceError(
            "Flask gather timings do not prove the configured SQL waits"
        )

    required_state_keys = {
        "active_other_requests",
        "completed_requests",
        "execution_model",
        "maximum_active_requests",
        "pid",
        "wsgi_thread_count",
    }
    completed = state_record.get("completed_requests")
    if (
        set(state_record) != required_state_keys
        or state_record.get("execution_model") != FLASK_WSGI_EXECUTION_MODEL
        or state_record.get("pid") != payload.get("pid")
        or state_record.get("active_other_requests") != 0
        or state_record.get("maximum_active_requests") != 1
        or state_record.get("wsgi_thread_count") != 1
        or isinstance(completed, bool)
        or not isinstance(completed, int)
        or completed < int(payload["request_sequence"])
    ):
        raise WorkerEvidenceError(
            "Flask gather WSGI request slot did not settle"
        )
    if observer_sample.maximum_requests != 4:
        raise WorkerEvidenceError(
            "SQL Server did not observe four same-view Flask requests"
        )
    pool = pool_record.get("pool")
    if not isinstance(pool, Mapping):
        raise WorkerEvidenceError("Flask gather pool evidence is malformed")
    active = _nonnegative_metric(pool, "active_connections")
    pending = _nonnegative_metric(pool, "pending_gets")
    connections = _nonnegative_metric(pool, "connections")
    idle = _nonnegative_metric(pool, "idle_connections")
    max_size = _nonnegative_metric(pool, "max_size")
    if (
        active != 0
        or pending != 0
        or connections > max_size
        or idle != connections
        or max_size < 4
    ):
        raise WorkerEvidenceError(
            "Flask gather pool did not settle after same-view concurrency"
        )
    return FlaskGatherEvidence(
        sequential_seconds=float(sequential_seconds),
        concurrent_seconds=float(concurrent_seconds),
        outer_response_seconds=float(response.elapsed_seconds),
        sql_delay_seconds=sql_delay_seconds,
        loop_token=int(payload["loop_token"]),
        maximum_simultaneous_sql_requests=observer_sample.maximum_requests,
        pool_active_after=active,
        pool_pending_after=pending,
    )


def validate_adapted_flask_scaling_evidence(
    *,
    config: RunnerConfig,
    profile: ProcessProfile,
    ready_records: Sequence[Mapping[str, object]],
    first_loop_records: Sequence[Mapping[str, object]],
    second_loop_records: Sequence[Mapping[str, object]],
    response_payloads: Sequence[Mapping[str, object]],
    settled_records: Sequence[Mapping[str, object]],
    observer_sample: SqlObserverSample,
) -> AdaptedFlaskScalingEvidence:
    """Validate persistent ASGI loops and one serialized WSGI lane per PID."""

    if (
        config.database_mode != "sql_auth"
        or profile.database_mode != "sql_auth"
        or profile not in adapted_flask_profiles(config.platform_system)
        or config.global_connection_budget % profile.workers
    ):
        raise RunnerConfigurationError(
            "adapted Flask evidence requires an applicable SQL-auth profile"
        )
    ready_map = _ready_worker_map(ready_records)
    expected_pids = tuple(sorted(ready_map.values()))
    if len(expected_pids) != profile.workers:
        raise WorkerEvidenceError(
            "adapted Flask ready worker count is inconsistent"
        )
    first_by_pid = _evidence_records_by_pid(
        first_loop_records,
        expected_pids=expected_pids,
        record_name="adapted Flask first loop",
    )
    second_by_pid = _evidence_records_by_pid(
        second_loop_records,
        expected_pids=expected_pids,
        record_name="adapted Flask second loop",
    )
    settled_by_pid = _evidence_records_by_pid(
        settled_records,
        expected_pids=expected_pids,
        record_name="adapted Flask settled",
    )
    reconcile_worker_sessions(observer_sample, ready_records)

    loop_keys = {
        "async_thread_token",
        "execution_model",
        "loop_id",
        "loop_token",
        "pid",
        "request_sequence",
        "value",
        "wsgi_active_requests",
        "wsgi_maximum_active_requests",
        "wsgi_thread_token",
    }
    persistent_worker_loops: list[dict[str, int]] = []
    persistent_by_pid: dict[int, dict[str, int]] = {}
    for pid in expected_pids:
        first = first_by_pid[pid]
        second = second_by_pid[pid]
        for record in (first, second):
            if (
                set(record) != loop_keys
                or record.get("execution_model")
                != ADAPTED_FLASK_EXECUTION_MODEL
                or record.get("value") != 17
                or record.get("wsgi_active_requests") != 1
                or record.get("wsgi_maximum_active_requests") != 1
            ):
                raise WorkerEvidenceError(
                    "adapted Flask loop evidence is malformed"
                )
            for name in (
                "async_thread_token",
                "loop_id",
                "loop_token",
                "pid",
                "request_sequence",
                "wsgi_thread_token",
            ):
                value = record.get(name)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value <= 0
                ):
                    raise WorkerEvidenceError(
                        "adapted Flask loop identity is malformed"
                    )
        if (
            second["request_sequence"] <= first["request_sequence"]
            or any(
                first[name] != second[name]
                for name in (
                    "async_thread_token",
                    "loop_id",
                    "loop_token",
                    "wsgi_thread_token",
                )
            )
        ):
            raise WorkerEvidenceError(
                "adapted Flask persistent ASGI loop changed within one worker"
            )
        persistent = {
            "async_thread_token": int(first["async_thread_token"]),
            "loop_token": int(first["loop_token"]),
            "pid": pid,
            "wsgi_thread_token": int(first["wsgi_thread_token"]),
        }
        persistent_by_pid[pid] = persistent
        persistent_worker_loops.append(persistent)

    response_keys = {
        "async_thread_token",
        "delay_ms",
        "execution_model",
        "loop_token",
        "pid",
        "request_sequence",
        "session_id",
        "value",
        "wsgi_active_requests",
        "wsgi_maximum_active_requests",
        "wsgi_thread_token",
    }
    for payload in response_payloads:
        pid = payload.get("pid")
        if (
            set(payload) != response_keys
            or isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid not in persistent_by_pid
            or payload.get("execution_model")
            != ADAPTED_FLASK_EXECUTION_MODEL
            or payload.get("wsgi_active_requests") != 1
            or payload.get("wsgi_maximum_active_requests") != 1
            or payload.get("loop_token")
            != persistent_by_pid[pid]["loop_token"]
            or payload.get("async_thread_token")
            != persistent_by_pid[pid]["async_thread_token"]
            or payload.get("wsgi_thread_token")
            != persistent_by_pid[pid]["wsgi_thread_token"]
            or payload.get("delay_ms") not in SQL_DELAY_MILLISECONDS
            or payload.get("delay_ms") == 0
            or isinstance(payload.get("value"), bool)
            or not isinstance(payload.get("value"), int)
            or isinstance(payload.get("session_id"), bool)
            or not isinstance(payload.get("session_id"), int)
            or int(payload["session_id"]) <= 0
        ):
            raise WorkerEvidenceError(
                "adapted Flask serialized response evidence is inconsistent"
            )

    state_keys = {
        "active_other_requests",
        "completed_requests",
        "execution_model",
        "maximum_active_requests",
        "pid",
        "wsgi_thread_count",
    }
    for pid in expected_pids:
        state = settled_by_pid[pid]
        completed = state.get("completed_requests")
        if (
            set(state) != state_keys
            or state.get("execution_model")
            != ADAPTED_FLASK_EXECUTION_MODEL
            or state.get("active_other_requests") != 0
            or state.get("maximum_active_requests") != 1
            or state.get("wsgi_thread_count") != 1
            or isinstance(completed, bool)
            or not isinstance(completed, int)
            or completed < int(second_by_pid[pid]["request_sequence"])
        ):
            raise WorkerEvidenceError(
                "adapted Flask WSGI lane did not settle"
            )
    if (
        observer_sample.maximum_sessions > config.global_connection_budget
        or observer_sample.current_sessions > config.global_connection_budget
    ):
        raise WorkerEvidenceError(
            "adapted Flask SQL sessions exceeded the global budget"
        )
    if not 1 <= observer_sample.maximum_requests <= profile.workers:
        raise WorkerEvidenceError(
            "adapted Flask exceeded one serialized SQL request per process"
        )
    return AdaptedFlaskScalingEvidence(
        profile_id=profile.id,
        persistent_worker_loops=tuple(persistent_worker_loops),
        maximum_aggregate_sql_sessions=observer_sample.maximum_sessions,
        maximum_simultaneous_sql_requests=observer_sample.maximum_requests,
    )


def validate_adapted_flask_serialization_evidence(
    *,
    sequential_payloads: Sequence[Mapping[str, object]],
    concurrent_payloads: Sequence[Mapping[str, object]],
    expected_values: Sequence[int],
    sequential_seconds: float,
    concurrent_seconds: float,
    state_record: Mapping[str, object],
    observer_sample: SqlObserverSample,
    pool_record: Mapping[str, object],
    sql_delay_ms: int,
) -> AdaptedFlaskSerializationEvidence:
    """Prove thread-sensitive serialization for one adapted Flask worker."""

    if sql_delay_ms not in SQL_DELAY_MILLISECONDS or sql_delay_ms <= 0:
        raise RunnerConfigurationError(
            "adapted Flask serialization requires an allowlisted SQL delay"
        )
    if (
        len(expected_values) != 4
        or len(sequential_payloads) != 4
        or len(concurrent_payloads) != 4
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in expected_values
        )
    ):
        raise WorkerEvidenceError(
            "adapted Flask serialization response count is inconsistent"
        )
    response_keys = {
        "async_thread_token",
        "delay_ms",
        "execution_model",
        "loop_token",
        "pid",
        "request_sequence",
        "session_id",
        "value",
        "wsgi_active_requests",
        "wsgi_maximum_active_requests",
        "wsgi_thread_token",
    }
    loop_tokens: set[int] = set()
    async_thread_tokens: set[int] = set()
    wsgi_thread_tokens: set[int] = set()
    pids: set[int] = set()
    request_sequences: set[int] = set()
    for payloads in (sequential_payloads, concurrent_payloads):
        for payload, expected_value in zip(
            payloads,
            expected_values,
            strict=True,
        ):
            if (
                set(payload) != response_keys
                or payload.get("execution_model")
                != ADAPTED_FLASK_EXECUTION_MODEL
                or payload.get("delay_ms") != sql_delay_ms
                or payload.get("value") != expected_value
                or payload.get("wsgi_active_requests") != 1
                or payload.get("wsgi_maximum_active_requests") != 1
            ):
                raise WorkerEvidenceError(
                    "adapted Flask serialization response is inconsistent"
                )
            for name in (
                "async_thread_token",
                "loop_token",
                "pid",
                "request_sequence",
                "session_id",
                "wsgi_thread_token",
            ):
                value = payload.get(name)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or value <= 0
                ):
                    raise WorkerEvidenceError(
                        "adapted Flask serialization identity is malformed"
                    )
            loop_tokens.add(int(payload["loop_token"]))
            async_thread_tokens.add(int(payload["async_thread_token"]))
            wsgi_thread_tokens.add(int(payload["wsgi_thread_token"]))
            pids.add(int(payload["pid"]))
            sequence = int(payload["request_sequence"])
            if sequence in request_sequences:
                raise WorkerEvidenceError(
                    "adapted Flask request sequence is not unique"
                )
            request_sequences.add(sequence)
    if (
        len(loop_tokens) != 1
        or len(async_thread_tokens) != 1
        or len(wsgi_thread_tokens) != 1
        or len(pids) != 1
    ):
        raise WorkerEvidenceError(
            "adapted Flask did not preserve one serialized worker lane"
        )
    for value in (sequential_seconds, concurrent_seconds):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise WorkerEvidenceError(
                "adapted Flask serialization timing is malformed"
            )
    sql_delay_seconds = sql_delay_ms / 1_000
    ratio = float(concurrent_seconds) / float(sequential_seconds)
    if (
        float(sequential_seconds) < sql_delay_seconds * 4 * 0.75
        or float(concurrent_seconds) < sql_delay_seconds * 4 * 0.75
        or not 0.75 <= ratio <= 1.35
    ):
        raise WorkerEvidenceError(
            "adapted Flask thread-sensitive serialization timing is inconsistent"
        )
    state_keys = {
        "active_other_requests",
        "completed_requests",
        "execution_model",
        "maximum_active_requests",
        "pid",
        "wsgi_thread_count",
    }
    completed = state_record.get("completed_requests")
    if (
        set(state_record) != state_keys
        or state_record.get("execution_model")
        != ADAPTED_FLASK_EXECUTION_MODEL
        or state_record.get("pid") != next(iter(pids))
        or state_record.get("active_other_requests") != 0
        or state_record.get("maximum_active_requests") != 1
        or state_record.get("wsgi_thread_count") != 1
        or isinstance(completed, bool)
        or not isinstance(completed, int)
        or completed < max(request_sequences)
    ):
        raise WorkerEvidenceError(
            "adapted Flask serialized WSGI lane did not settle"
        )
    if observer_sample.maximum_requests != 1:
        raise WorkerEvidenceError(
            "adapted Flask exposed parallel SQL requests in one process"
        )
    pool = pool_record.get("pool")
    if not isinstance(pool, Mapping):
        raise WorkerEvidenceError(
            "adapted Flask serialization pool evidence is malformed"
        )
    active = _nonnegative_metric(pool, "active_connections")
    pending = _nonnegative_metric(pool, "pending_gets")
    connections = _nonnegative_metric(pool, "connections")
    idle = _nonnegative_metric(pool, "idle_connections")
    max_size = _nonnegative_metric(pool, "max_size")
    if (
        active != 0
        or pending != 0
        or connections > max_size
        or idle != connections
        or max_size <= 0
    ):
        raise WorkerEvidenceError(
            "adapted Flask serialization pool did not settle"
        )
    return AdaptedFlaskSerializationEvidence(
        values=tuple(expected_values),
        sequential_seconds=float(sequential_seconds),
        concurrent_seconds=float(concurrent_seconds),
        sql_delay_seconds=sql_delay_seconds,
        loop_token=next(iter(loop_tokens)),
        maximum_simultaneous_sql_requests=observer_sample.maximum_requests,
        pool_active_after=active,
        pool_pending_after=pending,
    )


def validate_disconnect_evidence(
    *,
    token: str,
    observed_sample: SqlObserverSample,
    settled_sample: SqlObserverSample,
    before_pool: Mapping[str, object],
    after_pool: Mapping[str, object],
    recovery_payload: Mapping[str, object],
) -> DisconnectEvidence:
    """Validate real-peer cancellation and physical connection replacement."""

    if (
        not isinstance(token, str)
        or OBSERVER_CONTEXT_TOKEN_PATTERN.fullmatch(token) is None
    ):
        raise RunnerConfigurationError("disconnect context token is invalid")
    if (
        token not in observed_sample.request_context_tokens
        or observed_sample.current_requests <= 0
    ):
        raise WorkerEvidenceError(
            "disconnect SQL request was not observed before client close"
        )
    if (
        token in settled_sample.request_context_tokens
        or settled_sample.current_requests != 0
    ):
        raise WorkerEvidenceError(
            "disconnect SQL request did not settle after client close"
        )
    before_created = _nonnegative_metric(before_pool, "connections_created")
    after_created = _nonnegative_metric(after_pool, "connections_created")
    before_broken = _nonnegative_metric(
        before_pool,
        "connections_closed_broken",
    )
    after_broken = _nonnegative_metric(
        after_pool,
        "connections_closed_broken",
    )
    if (
        after_created != before_created + 1
        or after_broken != before_broken + 1
    ):
        raise WorkerEvidenceError(
            "disconnect replacement counters are inconsistent"
        )
    active = _nonnegative_metric(after_pool, "active_connections")
    pending = _nonnegative_metric(after_pool, "pending_gets")
    connections = _nonnegative_metric(after_pool, "connections")
    max_size = _nonnegative_metric(after_pool, "max_size")
    if active != 0 or pending != 0 or connections > max_size:
        raise WorkerEvidenceError(
            "disconnect recovery pool did not settle within its bounds"
        )
    session_id = recovery_payload.get("session_id")
    if (
        recovery_payload.get("value") != 36
        or isinstance(session_id, bool)
        or not isinstance(session_id, int)
        or session_id <= 0
    ):
        raise WorkerEvidenceError("disconnect recovery query is inconsistent")
    return DisconnectEvidence(
        context_token_sha256=hashlib.sha256(token.encode("ascii")).hexdigest(),
        sql_requests_after=settled_sample.current_requests,
        pool_active_after=active,
        pool_pending_after=pending,
        recovery_value=36,
    )


def validate_graceful_query_evidence(
    *,
    token: str,
    observed_sample: SqlObserverSample,
    response_payload: Mapping[str, object],
    expected_value: int,
    sql_delay_ms: int,
) -> GracefulQueryEvidence:
    """Bind an active identified SQL request to its post-signal response."""

    if (
        not isinstance(token, str)
        or OBSERVER_CONTEXT_TOKEN_PATTERN.fullmatch(token) is None
    ):
        raise RunnerConfigurationError("graceful query context token is invalid")
    if (
        token not in observed_sample.request_context_tokens
        or observed_sample.current_requests <= 0
    ):
        raise WorkerEvidenceError(
            "graceful query token was not active before the shutdown signal"
        )
    if (
        isinstance(expected_value, bool)
        or not isinstance(expected_value, int)
        or not MIN_SQL_BIGINT <= expected_value <= MAX_SQL_BIGINT
        or sql_delay_ms not in SQL_DELAY_MILLISECONDS
        or sql_delay_ms <= 0
    ):
        raise RunnerConfigurationError(
            "graceful query value or SQL delay is invalid"
        )
    session_id = response_payload.get("session_id")
    if (
        response_payload.get("value") != expected_value
        or response_payload.get("delay_ms") != sql_delay_ms
        or isinstance(session_id, bool)
        or not isinstance(session_id, int)
        or session_id <= 0
    ):
        raise WorkerEvidenceError(
            "graceful query response is inconsistent"
        )
    try:
        serialized_response = json.dumps(
            dict(response_payload),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        raise WorkerEvidenceError(
            "graceful query response is not serializable"
        ) from None
    if token in serialized_response:
        raise WorkerEvidenceError(
            "graceful query token appeared in the HTTP response"
        )
    return GracefulQueryEvidence(
        context_token_sha256=hashlib.sha256(token.encode("ascii")).hexdigest(),
        response_session_id=session_id,
        response_value=expected_value,
        sql_delay_seconds=sql_delay_ms / 1_000,
    )


def validate_graceful_transaction_evidence(
    *,
    token: str,
    item_id: int,
    outcome: str,
    holding_record: Mapping[str, object],
    settled_record: Mapping[str, object],
    observed_sample: SqlObserverSample,
    response_payload: Mapping[str, object],
    durable_rows: Sequence[Mapping[str, object]],
) -> GracefulTransactionEvidence:
    """Validate structural phases and SQL durability for one shutdown case."""

    if (
        outcome not in {"commit", "rollback"}
        or isinstance(item_id, bool)
        or not isinstance(item_id, int)
        or not 1 <= item_id <= MAX_SQL_BIGINT
        or token != f"transaction:{item_id}:{outcome}"
        or OBSERVER_CONTEXT_TOKEN_PATTERN.fullmatch(token) is None
    ):
        raise RunnerConfigurationError(
            "graceful transaction identity is invalid"
        )
    if (
        token not in observed_sample.request_context_tokens
        or observed_sample.current_requests <= 0
    ):
        raise WorkerEvidenceError(
            "graceful transaction token was not active before the shutdown signal"
        )
    token_sha256 = hashlib.sha256(token.encode("ascii")).hexdigest()
    for expected_phase, record in (
        ("holding", holding_record),
        ("settled", settled_record),
    ):
        pid = record.get("pid")
        if (
            record.get("context_token_sha256") != token_sha256
            or record.get("item_id") != item_id
            or record.get("outcome") != outcome
            or record.get("phase") != "transaction"
            or record.get("transaction_phase") != expected_phase
            or isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
            or not isinstance(record.get("run_id"), str)
            or not isinstance(record.get("worker_application_name"), str)
        ):
            raise WorkerEvidenceError(
                "graceful transaction phase evidence is inconsistent"
            )
    if (
        holding_record.get("pid") != settled_record.get("pid")
        or holding_record.get("run_id") != settled_record.get("run_id")
        or holding_record.get("worker_application_name")
        != settled_record.get("worker_application_name")
    ):
        raise WorkerEvidenceError(
            "graceful transaction phase identities do not reconcile"
        )
    session_id = response_payload.get("session_id")
    if (
        response_payload.get("item_id") != item_id
        or response_payload.get("outcome") != outcome
        or isinstance(session_id, bool)
        or not isinstance(session_id, int)
        or session_id <= 0
    ):
        raise WorkerEvidenceError(
            "graceful transaction response is inconsistent"
        )
    if isinstance(durable_rows, (str, bytes)):
        raise WorkerEvidenceError(
            "graceful transaction durable outcome is malformed"
        )
    normalized_rows = tuple(durable_rows)
    if outcome == "commit":
        durable_result = "row-present"
        durable_matches = (
            len(normalized_rows) == 1
            and normalized_rows[0].get("value") == "transaction"
        )
    else:
        durable_result = "row-absent"
        durable_matches = not normalized_rows
    if not durable_matches:
        raise WorkerEvidenceError(
            "graceful transaction durable outcome is inconsistent"
        )
    try:
        public_inputs = json.dumps(
            {
                "holding": dict(holding_record),
                "response": dict(response_payload),
                "settled": dict(settled_record),
            },
            sort_keys=True,
        )
    except (TypeError, ValueError):
        raise WorkerEvidenceError(
            "graceful transaction evidence is not serializable"
        ) from None
    if token in public_inputs:
        raise WorkerEvidenceError(
            "graceful transaction token appeared in public evidence"
        )
    return GracefulTransactionEvidence(
        context_token_sha256=token_sha256,
        item_id=item_id,
        outcome=outcome,
        response_session_id=session_id,
        durable_result=durable_result,
    )


def validate_native_saturation_evidence(
    *,
    expected_pid: int,
    expected_application_name: str,
    holder_values: Sequence[int],
    waiter_values: Sequence[int],
    excess_values: Sequence[int],
    recovery_value: int,
    acquire_timeout_ms: int,
    busy_sample: SqlObserverSample,
    baseline_pool_record: Mapping[str, object],
    saturated_pool_record: Mapping[str, object],
    settled_pool_record: Mapping[str, object],
    holder_responses: Sequence[LoopbackJsonResponse],
    waiter_responses: Sequence[LoopbackJsonResponse],
    rejection_responses: Sequence[LoopbackJsonResponse],
    recovery_response: LoopbackJsonResponse,
) -> NativeSaturationEvidence:
    """Prove exact application admission and driver pool saturation bounds."""

    pool_max = len(holder_values)
    all_values = (
        *holder_values,
        *waiter_values,
        *excess_values,
        recovery_value,
    )
    if (
        isinstance(expected_pid, bool)
        or not isinstance(expected_pid, int)
        or expected_pid <= 0
        or not isinstance(expected_application_name, str)
        or APPLICATION_DIRECTORY_PATTERN.fullmatch(
            expected_application_name
        )
        is None
        or pool_max <= 0
        or len(waiter_values) != pool_max
        or not excess_values
        or acquire_timeout_ms not in ACQUIRE_TIMEOUT_MILLISECONDS
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not MIN_SQL_BIGINT <= value <= MAX_SQL_BIGINT
            for value in all_values
        )
        or len(set(all_values)) != len(all_values)
    ):
        raise RunnerConfigurationError(
            "native saturation identity or workload is invalid"
        )
    if (
        len(holder_responses) != pool_max
        or len(waiter_responses) != pool_max
        or len(rejection_responses) != len(excess_values)
    ):
        raise WorkerEvidenceError(
            "native saturation response count is inconsistent"
        )

    applications = busy_sample.applications
    if (
        len(applications) != 1
        or applications[0].application_name != expected_application_name
        or len(applications[0].host_process_ids) != 1
        or applications[0].host_process_ids[0] not in {0, expected_pid}
        or applications[0].sessions != pool_max
        or applications[0].requests != pool_max
        or busy_sample.current_sessions != pool_max
        or busy_sample.current_requests != pool_max
        or busy_sample.maximum_sessions != pool_max
        or busy_sample.maximum_requests != pool_max
        or busy_sample.request_context_tokens
    ):
        raise WorkerEvidenceError(
            "native saturation SQL bounds are inconsistent"
        )

    def sections(
        record: Mapping[str, object],
    ) -> tuple[Mapping[str, object], Mapping[str, object]]:
        pool = record.get("pool")
        admission = record.get("admission")
        if (
            record.get("pid") != expected_pid
            or not isinstance(pool, Mapping)
            or not isinstance(admission, Mapping)
        ):
            raise WorkerEvidenceError(
                "native saturation pool evidence is malformed"
            )
        return pool, admission

    baseline_pool, baseline_admission = sections(baseline_pool_record)
    saturated_pool, saturated_admission = sections(saturated_pool_record)
    settled_pool, settled_admission = sections(settled_pool_record)
    admission_capacity = pool_max * 2
    baseline_connections = _nonnegative_metric(
        baseline_pool,
        "connections",
    )
    baseline_idle = _nonnegative_metric(
        baseline_pool,
        "idle_connections",
    )
    baseline_timed_out = _nonnegative_metric(
        baseline_pool,
        "get_timed_out",
    )
    saturated_connections = _nonnegative_metric(
        saturated_pool,
        "connections",
    )
    saturated_idle = _nonnegative_metric(
        saturated_pool,
        "idle_connections",
    )
    saturated_active = _nonnegative_metric(
        saturated_pool,
        "active_connections",
    )
    saturated_pending = _nonnegative_metric(
        saturated_pool,
        "pending_gets",
    )
    settled_connections = _nonnegative_metric(
        settled_pool,
        "connections",
    )
    settled_idle = _nonnegative_metric(
        settled_pool,
        "idle_connections",
    )
    settled_active = _nonnegative_metric(
        settled_pool,
        "active_connections",
    )
    settled_pending = _nonnegative_metric(
        settled_pool,
        "pending_gets",
    )
    settled_timed_out = _nonnegative_metric(
        settled_pool,
        "get_timed_out",
    )
    if (
        baseline_pool.get("max_size") != pool_max
        or baseline_connections > pool_max
        or baseline_idle != baseline_connections
        or _nonnegative_metric(baseline_pool, "active_connections") != 0
        or _nonnegative_metric(baseline_pool, "pending_gets") != 0
        or baseline_admission.get("active") != 0
        or baseline_admission.get("capacity") != admission_capacity
        or baseline_admission.get("rejected") != 0
        or saturated_pool.get("max_size") != pool_max
        or saturated_connections != pool_max
        or saturated_idle != 0
        or saturated_active != pool_max
        or saturated_pending != pool_max
        or _nonnegative_metric(saturated_pool, "get_timed_out")
        != baseline_timed_out
        or saturated_admission.get("active") != admission_capacity
        or saturated_admission.get("capacity") != admission_capacity
        or saturated_admission.get("rejected") != 0
        or settled_pool.get("max_size") != pool_max
        or settled_connections > pool_max
        or settled_idle != settled_connections
        or settled_active != 0
        or settled_pending != 0
        or settled_timed_out - baseline_timed_out != pool_max
        or settled_admission.get("active") != 0
        or settled_admission.get("capacity") != admission_capacity
        or settled_admission.get("rejected") != len(excess_values)
    ):
        raise WorkerEvidenceError(
            "native saturation bounds are inconsistent"
        )

    holder_session_ids: set[int] = set()
    for response, expected_value in zip(
        holder_responses,
        holder_values,
        strict=True,
    ):
        session_id = response.payload.get("session_id")
        if (
            response.status_code != 200
            or set(response.payload) != {"session_id", "value"}
            or response.payload.get("value") != expected_value
            or isinstance(session_id, bool)
            or not isinstance(session_id, int)
            or session_id <= 0
            or session_id in holder_session_ids
        ):
            raise WorkerEvidenceError(
                "native saturation holder response is inconsistent"
            )
        holder_session_ids.add(session_id)

    timeout_payload = {
        "error": "pool_acquire_timeout",
        "operation": "query",
        "phase": "acquire",
        "retryable": True,
    }
    if any(
        response.status_code != 504
        or response.payload != timeout_payload
        for response in waiter_responses
    ):
        raise WorkerEvidenceError(
            "native saturation acquire timeout is inconsistent"
        )
    rejection_limit_seconds = acquire_timeout_ms / 1_000
    if any(
        response.status_code != 503
        or response.payload != {"error": "saturated"}
        or response.elapsed_seconds >= rejection_limit_seconds
        for response in rejection_responses
    ):
        raise WorkerEvidenceError(
            "native saturation rejection is inconsistent"
        )
    recovery_session_id = recovery_response.payload.get("session_id")
    if (
        recovery_response.status_code != 200
        or set(recovery_response.payload) != {"session_id", "value"}
        or recovery_response.payload.get("value") != recovery_value
        or isinstance(recovery_session_id, bool)
        or not isinstance(recovery_session_id, int)
        or recovery_session_id <= 0
    ):
        raise WorkerEvidenceError(
            "native saturation recovery response is inconsistent"
        )
    return NativeSaturationEvidence(
        pool_max_per_worker=pool_max,
        admitted_holders=pool_max,
        admitted_waiters=pool_max,
        admission_capacity=admission_capacity,
        rejected_requests=len(excess_values),
        acquire_timeouts=pool_max,
        maximum_sql_sessions=busy_sample.maximum_sessions,
        maximum_sql_requests=busy_sample.maximum_requests,
        observed_active_connections=saturated_active,
        observed_pending_gets=saturated_pending,
        pool_get_timed_out_delta=settled_timed_out - baseline_timed_out,
        pool_active_after=settled_active,
        pool_pending_after=settled_pending,
        admission_active_after=int(settled_admission["active"]),
        recovery_value=recovery_value,
    )


def validate_native_streaming_evidence(
    *,
    expected_pid: int,
    pool_max: int,
    full_observation: FullNdjsonObservation,
    early_observation: EarlyCloseNdjsonObservation,
    driver_buffer_rows: int,
    rss_start_bytes: int,
    rss_peak_bytes: int,
    rss_end_bytes: int,
    rss_growth_limit_bytes: int,
    full_settled_pool_record: Mapping[str, object],
    early_settled_pool_record: Mapping[str, object],
    early_settled_sample: SqlObserverSample,
    recovery_value: int,
    recovery_response: LoopbackJsonResponse,
) -> NativeStreamingEvidence:
    """Validate incremental delivery, bounded RSS and early-close recovery."""

    if (
        isinstance(expected_pid, bool)
        or not isinstance(expected_pid, int)
        or expected_pid <= 0
        or isinstance(pool_max, bool)
        or not isinstance(pool_max, int)
        or pool_max <= 0
        or driver_buffer_rows != EXPECTED_STREAM_BUFFER_ROWS
        or isinstance(recovery_value, bool)
        or not isinstance(recovery_value, int)
        or not MIN_SQL_BIGINT <= recovery_value <= MAX_SQL_BIGINT
    ):
        raise RunnerConfigurationError(
            "native streaming identity, buffer or recovery value is invalid"
        )
    if (
        full_observation.status_code != 200
        or not full_observation.content_type.lower().startswith(
            "application/x-ndjson"
        )
        or not 2 <= full_observation.row_count <= MAX_HTTP_STREAM_ROWS
        or SHA256_PATTERN.fullmatch(full_observation.value_digest) is None
        or full_observation.bytes_received <= 0
        or not 0 <= full_observation.first_data_seconds
        < full_observation.elapsed_seconds
        or early_observation.status_code != 200
        or not early_observation.content_type.lower().startswith(
            "application/x-ndjson"
        )
        or not 2 <= early_observation.requested_rows
        <= MAX_HTTP_STREAM_ROWS
        or not 1 <= early_observation.prefix_rows
        < early_observation.requested_rows
        or SHA256_PATTERN.fullmatch(early_observation.prefix_digest) is None
        or early_observation.bytes_received <= 0
        or not 0 <= early_observation.first_data_seconds
        < early_observation.close_seconds
    ):
        raise WorkerEvidenceError(
            "native streaming HTTP evidence is inconsistent"
        )
    if (
        any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            for value in (
                rss_start_bytes,
                rss_peak_bytes,
                rss_end_bytes,
                rss_growth_limit_bytes,
            )
        )
        or rss_peak_bytes < max(rss_start_bytes, rss_end_bytes)
        or rss_peak_bytes - rss_start_bytes > rss_growth_limit_bytes
    ):
        raise WorkerEvidenceError(
            "native streaming RSS evidence exceeded its bound"
        )

    def settled_pool(
        record: Mapping[str, object],
    ) -> tuple[int, int]:
        pool = record.get("pool")
        admission = record.get("admission")
        if (
            record.get("pid") != expected_pid
            or not isinstance(pool, Mapping)
            or not isinstance(admission, Mapping)
        ):
            raise WorkerEvidenceError(
                "native streaming pool evidence is malformed"
            )
        connections = _nonnegative_metric(pool, "connections")
        idle = _nonnegative_metric(pool, "idle_connections")
        active = _nonnegative_metric(pool, "active_connections")
        pending = _nonnegative_metric(pool, "pending_gets")
        if (
            pool.get("max_size") != pool_max
            or connections > pool_max
            or idle != connections
            or active != 0
            or pending != 0
            or admission.get("active") != 0
            or admission.get("capacity") != pool_max * 2
        ):
            raise WorkerEvidenceError(
                "native streaming pool did not settle within its bounds"
            )
        return active, pending

    full_active, full_pending = settled_pool(full_settled_pool_record)
    early_active, early_pending = settled_pool(early_settled_pool_record)
    if (
        early_settled_sample.current_requests != 0
        or early_settled_sample.maximum_requests < 1
        or early_settled_sample.maximum_requests > pool_max
        or early_settled_sample.current_sessions > pool_max
        or early_settled_sample.maximum_sessions > pool_max
        or early_settled_sample.request_context_tokens
    ):
        raise WorkerEvidenceError(
            "native streaming early-close SQL did not settle"
        )
    recovery_session_id = recovery_response.payload.get("session_id")
    if (
        recovery_response.status_code != 200
        or set(recovery_response.payload) != {"session_id", "value"}
        or recovery_response.payload.get("value") != recovery_value
        or isinstance(recovery_session_id, bool)
        or not isinstance(recovery_session_id, int)
        or recovery_session_id <= 0
    ):
        raise WorkerEvidenceError(
            "native streaming recovery response is inconsistent"
        )
    return NativeStreamingEvidence(
        driver_buffer_rows=driver_buffer_rows,
        full_rows=full_observation.row_count,
        full_value_digest=full_observation.value_digest,
        full_bytes_received=full_observation.bytes_received,
        full_first_data_seconds=full_observation.first_data_seconds,
        full_elapsed_seconds=full_observation.elapsed_seconds,
        full_pool_active_after=full_active,
        full_pool_pending_after=full_pending,
        early_requested_rows=early_observation.requested_rows,
        early_prefix_rows=early_observation.prefix_rows,
        early_prefix_digest=early_observation.prefix_digest,
        early_bytes_received=early_observation.bytes_received,
        early_first_data_seconds=early_observation.first_data_seconds,
        early_close_seconds=early_observation.close_seconds,
        early_sql_requests_after=early_settled_sample.current_requests,
        early_pool_active_after=early_active,
        early_pool_pending_after=early_pending,
        rss_start_bytes=rss_start_bytes,
        rss_peak_bytes=rss_peak_bytes,
        rss_end_bytes=rss_end_bytes,
        rss_growth_limit_bytes=rss_growth_limit_bytes,
        recovery_value=recovery_value,
    )


def create_observer_connection(
    settings: SqlAuthObserverSettings,
    *,
    application_name: str,
) -> Any:
    """Construct one independent connection from the installed wheel lazily."""

    if (
        not isinstance(application_name, str)
        or APPLICATION_DIRECTORY_PATTERN.fullmatch(application_name) is None
        or len(application_name) > MAX_SQL_SERVER_APPLICATION_NAME
    ):
        raise RunnerConfigurationError("observer application name is invalid")
    driver = importlib.import_module("fastmssql")
    return driver.Connection(
        server=settings.host,
        port=settings.port,
        database=settings.database,
        username=settings.username,
        password=settings.password,
        application_name=application_name,
        ssl_config=driver.SslConfig.development(),
        pool_config=driver.PoolConfig(
            max_size=1,
            min_idle=0,
            max_lifetime_secs=None,
            idle_timeout_secs=None,
            connection_timeout_secs=5,
            retry_connection=False,
        ),
        lifecycle_config=driver.LifecycleConfig(
            shutdown_timeout_secs=10,
            force_timeout_secs=5,
        ),
        timeout_config=driver.TimeoutConfig(
            connect_timeout_secs=10,
            acquire_timeout_secs=5,
            operation_timeout_secs=10,
            transaction_timeout_secs=10,
            rollback_timeout_secs=5,
        ),
    )


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


def native_fastapi_profiles(
    platform_name: str,
) -> tuple[ProcessProfile, ...]:
    """Select every applicable native FastAPI SQL-auth scaling profile."""

    profiles = tuple(
        profile
        for profile in expand_profiles(
            platform_name,
            database_mode="sql_auth",
        )
        if profile.family.startswith("fastapi-") and profile.applicable
    )
    expected_families = {"fastapi-uvicorn-asyncio"}
    if normalize_platform(platform_name) != "Windows":
        expected_families.update(
            {
                "fastapi-uvicorn-uvloop",
                "fastapi-gunicorn-uvicorn-worker",
            }
        )
    if (
        {profile.family for profile in profiles} != expected_families
        or {profile.workers for profile in profiles} != set(WORKER_COUNTS)
    ):
        raise RunnerConfigurationError(
            "native FastAPI profile selection is incomplete"
        )
    return profiles


def flask_wsgi_profiles(
    platform_name: str,
) -> tuple[ProcessProfile, ...]:
    """Select every real Gunicorn Flask WSGI SQL-auth profile on POSIX."""

    if normalize_platform(platform_name) == "Windows":
        return ()
    profiles = tuple(
        profile
        for profile in expand_profiles(
            platform_name,
            database_mode="sql_auth",
        )
        if profile.family
        in {"flask-gunicorn-gthread", "flask-gunicorn-sync"}
        and profile.applicable
    )
    if (
        {profile.family for profile in profiles}
        != {"flask-gunicorn-gthread", "flask-gunicorn-sync"}
        or {profile.workers for profile in profiles} != set(WORKER_COUNTS)
        or len(profiles) != 2 * len(WORKER_COUNTS)
    ):
        raise RunnerConfigurationError(
            "Flask WSGI profile selection is incomplete"
        )
    return profiles


def adapted_flask_profiles(
    platform_name: str,
) -> tuple[ProcessProfile, ...]:
    """Select every applicable standalone-Uvicorn adapted Flask profile."""

    platform_system = normalize_platform(platform_name)
    profiles = tuple(
        profile
        for profile in expand_profiles(
            platform_name,
            database_mode="sql_auth",
        )
        if profile.family.startswith("flask-asgi-uvicorn-")
        and profile.applicable
    )
    expected_families = {"flask-asgi-uvicorn-asyncio"}
    if platform_system != "Windows":
        expected_families.add("flask-asgi-uvicorn-uvloop")
    if (
        {profile.family for profile in profiles} != expected_families
        or {profile.workers for profile in profiles} != set(WORKER_COUNTS)
        or len(profiles) != len(expected_families) * len(WORKER_COUNTS)
    ):
        raise RunnerConfigurationError(
            "adapted Flask profile selection is incomplete"
        )
    return profiles


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
    sql_auth_settings: SqlAuthObserverSettings | None = None,
    table_name: str | None = None,
    sql_delay_ms: int = 0,
    acquire_timeout_ms: int = 5_000,
) -> dict[str, str]:
    """Build one closed profile environment without persisting credentials."""

    if profile.database_mode != config.database_mode:
        raise RunnerConfigurationError(
            "profile database mode does not match the runner configuration"
        )
    if not profile.applicable:
        raise RunnerConfigurationError(
            "cannot build an environment for a not-applicable profile"
        )
    if APPLICATION_DIRECTORY_PATTERN.fullmatch(run_id) is None:
        raise RunnerConfigurationError("framework run ID is unsafe")
    if sql_delay_ms not in SQL_DELAY_MILLISECONDS:
        raise RunnerConfigurationError("SQL delay is outside the closed allowlist")
    if acquire_timeout_ms not in ACQUIRE_TIMEOUT_MILLISECONDS:
        raise RunnerConfigurationError(
            "acquire timeout is outside the closed allowlist"
        )
    if config.database_mode == "offline":
        if sql_auth_settings is not None or table_name is not None or sql_delay_ms != 0:
            raise RunnerConfigurationError(
                "offline profile environment forbids SQL-auth settings"
            )
        selected_table = f"framework_items_{config.candidate_sha[:12]}"
    elif config.database_mode == "sql_auth":
        if sql_auth_settings is None:
            raise RunnerConfigurationError(
                "SQL-auth profile environment requires database settings"
            )
        if (
            not isinstance(table_name, str)
            or SQL_IDENTIFIER_PATTERN.fullmatch(table_name) is None
        ):
            raise RunnerConfigurationError("SQL-auth profile table name is invalid")
        selected_table = table_name
    else:
        raise RunnerConfigurationError("database mode must be sql_auth or offline")
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
    overrides = {
        "FASTMSSQL_FRAMEWORK_DATABASE_MODE": config.database_mode,
        "FASTMSSQL_FRAMEWORK_WORKER_COUNT": str(profile.workers),
        "FASTMSSQL_FRAMEWORK_GLOBAL_CONNECTION_BUDGET": str(
            config.global_connection_budget
        ),
        "FASTMSSQL_FRAMEWORK_APPLICATION_NAME": application_name,
        "FASTMSSQL_FRAMEWORK_RUN_ID": run_id,
        "FASTMSSQL_FRAMEWORK_RUN_ROOT": str(config.run_root),
        "FASTMSSQL_FRAMEWORK_ARTIFACT_DIR": str(resolved_artifacts),
        "FASTMSSQL_FRAMEWORK_TABLE": selected_table,
        "FASTMSSQL_FRAMEWORK_SQL_DELAY_MS": str(sql_delay_ms),
        "FASTMSSQL_FRAMEWORK_ACQUIRE_TIMEOUT_MS": str(acquire_timeout_ms),
        "FASTMSSQL_FRAMEWORK_CANDIDATE_SHA": config.candidate_sha,
        "FASTMSSQL_FRAMEWORK_WHEEL_FILENAME": config.wheel.name,
        "FASTMSSQL_FRAMEWORK_WHEEL_SHA256": config.wheel_sha256,
        "FASTMSSQL_FRAMEWORK_GUNICORN_LIFECYCLE_OWNER": lifecycle_owner,
    }
    if sql_auth_settings is not None:
        overrides.update(
            {
                "FASTMSSQL_SQL_AUTH_HOST": sql_auth_settings.host,
                "FASTMSSQL_SQL_AUTH_PORT": str(sql_auth_settings.port),
                "FASTMSSQL_SQL_AUTH_DATABASE": sql_auth_settings.database,
                "FASTMSSQL_SQL_AUTH_OWNER_USER": sql_auth_settings.username,
                "FASTMSSQL_SQL_AUTH_OWNER_PASSWORD": sql_auth_settings.password,
            }
        )
    return build_child_environment(overrides)


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

            accepted_signal_exit = (
                requested_graceful_stop
                and os.name == "posix"
                and self._process.returncode == -signal.SIGTERM
            )
            outcome = await self._finalize(
                graceful_stop=(
                    requested_graceful_stop
                    and not forced_cleanup
                    and (
                        self._process.returncode == 0
                        or accepted_signal_exit
                    )
                ),
                forced_cleanup=forced_cleanup,
            )
            self._requested_stop_exit_accepted = (
                outcome.returncode == 0 or accepted_signal_exit
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


async def wait_for_transaction_phase_record(
    supervisor: ProcessSupervisor,
    *,
    directory: Path,
    expected_run_id: str,
    expected_pid: int,
    expected_worker_application_name: str,
    item_id: int,
    outcome: str,
    transaction_phase: str,
    timeout_seconds: float,
) -> dict[str, object]:
    """Wait for one exact, fresh, privacy-safe transaction phase record."""

    if (
        not directory.is_dir()
        or APPLICATION_DIRECTORY_PATTERN.fullmatch(expected_run_id) is None
        or APPLICATION_DIRECTORY_PATTERN.fullmatch(
            expected_worker_application_name
        )
        is None
        or isinstance(expected_pid, bool)
        or not isinstance(expected_pid, int)
        or expected_pid <= 0
        or isinstance(item_id, bool)
        or not isinstance(item_id, int)
        or not 1 <= item_id <= MAX_SQL_BIGINT
        or outcome not in {"commit", "rollback"}
        or transaction_phase not in {"holding", "settled"}
    ):
        raise RunnerConfigurationError(
            "transaction phase wait identity is invalid"
        )
    token = f"transaction:{item_id}:{outcome}"
    token_sha256 = hashlib.sha256(token.encode("ascii")).hexdigest()
    path = directory / (
        f"transaction-{transaction_phase}-{outcome}-"
        f"{expected_pid}-{item_id}.json"
    )
    selected: dict[str, object] | None = None

    def record_is_ready() -> bool:
        nonlocal selected
        if not path.is_file():
            return False
        if path.stat().st_mtime_ns <= supervisor.started_wall_time_ns:
            raise WorkerEvidenceError(
                "transaction phase record predates the supervised process"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise WorkerEvidenceError(
                "transaction phase record is not valid JSON"
            ) from error
        if not isinstance(payload, dict):
            raise WorkerEvidenceError(
                "transaction phase record must be a JSON object"
            )
        if (
            payload.get("context_token_sha256") != token_sha256
            or payload.get("item_id") != item_id
            or payload.get("outcome") != outcome
            or payload.get("phase") != "transaction"
            or payload.get("pid") != expected_pid
            or payload.get("run_id") != expected_run_id
            or payload.get("transaction_phase") != transaction_phase
            or payload.get("worker_application_name")
            != expected_worker_application_name
        ):
            raise WorkerEvidenceError(
                "transaction phase record is inconsistent"
            )
        if token in json.dumps(payload, sort_keys=True):
            raise WorkerEvidenceError(
                "transaction phase record exposed its raw context token"
            )
        selected = dict(payload)
        return True

    if not record_is_ready():
        await supervisor.wait_for_readiness(
            record_is_ready,
            timeout_seconds=timeout_seconds,
        )
    if selected is None:
        raise WorkerEvidenceError(
            "transaction phase wait completed without a record"
        )
    return selected


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
MAX_WORKER_PROBE_CONCURRENCY = 16


def _validate_http_probe(
    port: int,
    path: str,
    timeout_seconds: float,
) -> None:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
        raise RunnerConfigurationError("port must be between 1 and 65,535")
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or "\r" in path
        or "\n" in path
        or " " in path
    ):
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


async def http_request_json(
    port: int,
    path: str,
    *,
    method: str = "GET",
    expected_statuses: Sequence[int] = (200,),
    timeout_seconds: float = 1.0,
    context_token: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LoopbackJsonResponse:
    """Issue one hard-bounded loopback request without retaining its URL."""

    _validate_http_probe(port, path, timeout_seconds)
    if method not in {"GET", "POST"}:
        raise RunnerConfigurationError("HTTP probe method is unsupported")
    if context_token is not None and (
        not isinstance(context_token, str)
        or OBSERVER_CONTEXT_TOKEN_PATTERN.fullmatch(context_token) is None
    ):
        raise RunnerConfigurationError("HTTP probe context token is invalid")
    if (
        isinstance(expected_statuses, (str, bytes))
        or not expected_statuses
        or any(
            isinstance(status, bool)
            or not isinstance(status, int)
            or not 100 <= status <= 599
            for status in expected_statuses
        )
        or len(set(expected_statuses)) != len(expected_statuses)
    ):
        raise RunnerConfigurationError("HTTP probe expected statuses are invalid")

    async def request() -> LoopbackJsonResponse:
        started = time.monotonic()
        headers = {
            "Accept": "application/json",
            "Connection": "close",
        }
        if context_token is not None:
            headers["X-FastMssql-Context-Token"] = context_token
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout=timeout_seconds,
                transport=transport,
                trust_env=False,
            ) as client:
                async with client.stream(
                    method,
                    path,
                    headers=headers,
                ) as response:
                    if response.status_code not in expected_statuses:
                        raise HttpProbeError(
                            "loopback HTTP probe returned status "
                            f"{response.status_code}"
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_HTTP_PROBE_BYTES:
                            raise HttpProbeError(
                                "loopback HTTP response exceeded its byte limit"
                            )
        except HttpProbeError:
            raise
        except (httpx.HTTPError, TimeoutError):
            raise HttpProbeError("loopback HTTP probe failed") from None

        try:
            payload = json.loads(body)
        except (UnicodeError, json.JSONDecodeError):
            raise HttpProbeError(
                "loopback HTTP response is not valid JSON"
            ) from None
        if not isinstance(payload, dict):
            raise HttpProbeError("loopback HTTP response must be a JSON object")
        return LoopbackJsonResponse(
            status_code=response.status_code,
            payload=payload,
            elapsed_seconds=max(0.0, time.monotonic() - started),
        )

    try:
        return await asyncio.wait_for(request(), timeout=timeout_seconds + 0.5)
    except HttpProbeError:
        raise
    except TimeoutError:
        raise HttpProbeError("loopback HTTP probe failed") from None


async def open_raw_http_request(
    port: int,
    path: str,
    *,
    timeout_seconds: float = 1.0,
) -> RawLoopbackRequest:
    """Send one raw GET and retain the socket without reading its response."""

    _validate_http_probe(port, path, timeout_seconds)
    try:
        request_target = path.encode("ascii")
    except UnicodeEncodeError:
        raise RunnerConfigurationError("HTTP probe path must be ASCII") from None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=timeout_seconds,
        )
        writer.write(
            b"GET "
            + request_target
            + b" HTTP/1.1\r\n"
            + f"Host: 127.0.0.1:{port}\r\n".encode("ascii")
            + b"Accept: application/json\r\n"
            + b"Connection: close\r\n\r\n"
        )
        await asyncio.wait_for(writer.drain(), timeout=timeout_seconds)
    except (OSError, TimeoutError):
        if "writer" in locals():
            writer.close()
        raise HttpProbeError("raw loopback HTTP request failed") from None
    return RawLoopbackRequest(_reader=reader, _writer=writer)


def _ndjson_stream_value(line: str, *, expected_value: int) -> int:
    try:
        encoded = line.encode("ascii")
    except UnicodeEncodeError:
        raise HttpProbeError("NDJSON stream line is not ASCII") from None
    if not encoded or len(encoded) > MAX_NDJSON_LINE_BYTES:
        raise HttpProbeError("NDJSON stream line exceeded its byte limit")
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        raise HttpProbeError("NDJSON stream line is not valid JSON") from None
    expected = {
        "result_set": 0,
        "row": {"value": expected_value},
    }
    if payload != expected:
        raise HttpProbeError("NDJSON stream row is out of order or malformed")
    return expected_value


async def consume_ndjson_stream(
    port: int,
    path: str,
    *,
    expected_rows: int,
    timeout_seconds: float,
) -> FullNdjsonObservation:
    """Consume one real NDJSON response under one wall-clock deadline."""

    _validate_http_probe(port, path, timeout_seconds)
    try:
        return await asyncio.wait_for(
            _consume_ndjson_stream(
                port,
                path,
                expected_rows=expected_rows,
                timeout_seconds=timeout_seconds,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        raise HttpProbeError(
            "NDJSON stream exceeded its global deadline"
        ) from None


async def _consume_ndjson_stream(
    port: int,
    path: str,
    *,
    expected_rows: int,
    timeout_seconds: float,
) -> FullNdjsonObservation:
    """Implement incremental NDJSON consumption for the bounded wrapper."""

    _validate_http_probe(port, path, timeout_seconds)
    if (
        isinstance(expected_rows, bool)
        or not isinstance(expected_rows, int)
        or not 2 <= expected_rows <= MAX_HTTP_STREAM_ROWS
    ):
        raise RunnerConfigurationError(
            "full NDJSON row count must be between 2 and 10,000"
        )
    started = time.monotonic()
    first_data_seconds: float | None = None
    row_count = 0
    bytes_received = 0
    digest = hashlib.sha256()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "GET",
                f"http://127.0.0.1:{port}{path}",
                headers={
                    "Accept": "application/x-ndjson",
                    "Connection": "close",
                },
            ) as response:
                content_type = response.headers.get("content-type", "")
                if response.status_code != 200:
                    raise HttpProbeError(
                        "NDJSON stream returned an unexpected status"
                    )
                if not content_type.lower().startswith(
                    "application/x-ndjson"
                ):
                    raise HttpProbeError(
                        "NDJSON stream returned an unexpected content type"
                    )
                async for line in response.aiter_lines():
                    expected_value = row_count + 1
                    value = _ndjson_stream_value(
                        line,
                        expected_value=expected_value,
                    )
                    row_count += 1
                    encoded_value = f"{value}\n".encode("ascii")
                    digest.update(encoded_value)
                    bytes_received += len(line.encode("ascii")) + 1
                    if first_data_seconds is None:
                        first_data_seconds = max(
                            0.0,
                            time.monotonic() - started,
                        )
                    if row_count > expected_rows:
                        raise HttpProbeError(
                            "NDJSON stream returned excess rows"
                        )
        elapsed_seconds = max(0.0, time.monotonic() - started)
    except HttpProbeError:
        raise
    except httpx.HTTPError:
        raise HttpProbeError("NDJSON stream request failed") from None
    if row_count != expected_rows or first_data_seconds is None:
        raise HttpProbeError("NDJSON stream row count is inconsistent")
    if first_data_seconds >= elapsed_seconds:
        raise HttpProbeError("NDJSON stream was not delivered incrementally")
    return FullNdjsonObservation(
        status_code=200,
        content_type=content_type,
        row_count=row_count,
        value_digest=digest.hexdigest(),
        bytes_received=bytes_received,
        first_data_seconds=first_data_seconds,
        elapsed_seconds=elapsed_seconds,
    )


async def read_ndjson_prefix_and_close(
    port: int,
    path: str,
    *,
    requested_rows: int,
    prefix_rows: int,
    timeout_seconds: float,
    before_read: Callable[[], Awaitable[None]] | None = None,
) -> EarlyCloseNdjsonObservation:
    """Read and close an NDJSON prefix under one wall-clock deadline."""

    _validate_http_probe(port, path, timeout_seconds)
    try:
        return await asyncio.wait_for(
            _read_ndjson_prefix_and_close(
                port,
                path,
                requested_rows=requested_rows,
                prefix_rows=prefix_rows,
                timeout_seconds=timeout_seconds,
                before_read=before_read,
            ),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        raise HttpProbeError(
            "early-close NDJSON stream exceeded its global deadline"
        ) from None


async def _read_ndjson_prefix_and_close(
    port: int,
    path: str,
    *,
    requested_rows: int,
    prefix_rows: int,
    timeout_seconds: float,
    before_read: Callable[[], Awaitable[None]] | None = None,
) -> EarlyCloseNdjsonObservation:
    """Implement prefix consumption for the bounded public wrapper."""

    _validate_http_probe(port, path, timeout_seconds)
    if (
        isinstance(requested_rows, bool)
        or not isinstance(requested_rows, int)
        or not 2 <= requested_rows <= MAX_HTTP_STREAM_ROWS
        or isinstance(prefix_rows, bool)
        or not isinstance(prefix_rows, int)
        or not 1 <= prefix_rows < requested_rows
    ):
        raise RunnerConfigurationError(
            "early-close NDJSON row bounds are invalid"
        )
    started = time.monotonic()
    first_data_seconds: float | None = None
    observed_rows = 0
    bytes_received = 0
    digest = hashlib.sha256()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "GET",
                f"http://127.0.0.1:{port}{path}",
                headers={
                    "Accept": "application/x-ndjson",
                    "Connection": "close",
                },
            ) as response:
                content_type = response.headers.get("content-type", "")
                if response.status_code != 200:
                    raise HttpProbeError(
                        "early-close NDJSON stream returned an unexpected status"
                    )
                if not content_type.lower().startswith(
                    "application/x-ndjson"
                ):
                    raise HttpProbeError(
                        "early-close NDJSON stream returned an unexpected "
                        "content type"
                    )
                if before_read is not None:
                    await before_read()
                async for line in response.aiter_lines():
                    expected_value = observed_rows + 1
                    value = _ndjson_stream_value(
                        line,
                        expected_value=expected_value,
                    )
                    observed_rows += 1
                    digest.update(f"{value}\n".encode("ascii"))
                    bytes_received += len(line.encode("ascii")) + 1
                    if first_data_seconds is None:
                        first_data_seconds = max(
                            0.0,
                            time.monotonic() - started,
                        )
                    if observed_rows == prefix_rows:
                        break
        close_seconds = max(0.0, time.monotonic() - started)
    except HttpProbeError:
        raise
    except httpx.HTTPError:
        raise HttpProbeError(
            "early-close NDJSON stream request failed"
        ) from None
    if observed_rows != prefix_rows or first_data_seconds is None:
        raise HttpProbeError(
            "early-close NDJSON prefix count is inconsistent"
        )
    if first_data_seconds >= close_seconds:
        raise HttpProbeError(
            "early-close NDJSON prefix was not delivered incrementally"
        )
    return EarlyCloseNdjsonObservation(
        status_code=200,
        content_type=content_type,
        requested_rows=requested_rows,
        prefix_rows=observed_rows,
        prefix_digest=digest.hexdigest(),
        bytes_received=bytes_received,
        first_data_seconds=first_data_seconds,
        close_seconds=close_seconds,
    )


def process_rss_bytes(pids: tuple[int, ...]) -> int:
    """Return aggregate RSS for one exact live worker PID set."""

    if (
        not pids
        or len(set(pids)) != len(pids)
        or any(
            isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0
            for pid in pids
        )
    ):
        raise RunnerConfigurationError("RSS worker PID set is invalid")
    try:
        rss = sum(psutil.Process(pid).memory_info().rss for pid in pids)
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess) as error:
        raise ProcessSupervisorError(
            "worker RSS could not be sampled"
        ) from error
    if isinstance(rss, bool) or not isinstance(rss, int) or rss <= 0:
        raise ProcessSupervisorError("worker RSS sample is invalid")
    return rss


async def monitor_process_rss(
    pids: tuple[int, ...],
    *,
    stop: asyncio.Event,
    poll_interval_seconds: float,
    rss_sampler: Callable[[tuple[int, ...]], int] = process_rss_bytes,
) -> int:
    """Record peak aggregate RSS until the caller signals completion."""

    if (
        isinstance(poll_interval_seconds, bool)
        or not isinstance(poll_interval_seconds, (int, float))
        or not math.isfinite(poll_interval_seconds)
        or poll_interval_seconds <= 0
    ):
        raise RunnerConfigurationError(
            "RSS poll interval must be a finite positive number"
        )
    peak = 0
    while True:
        current = rss_sampler(pids)
        if (
            isinstance(current, bool)
            or not isinstance(current, int)
            or current <= 0
        ):
            raise ProcessSupervisorError("worker RSS sample is invalid")
        peak = max(peak, current)
        if stop.is_set():
            return peak
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=poll_interval_seconds,
            )
        except TimeoutError:
            continue


async def wait_for_pool_settlement(
    port: int,
    *,
    expected_pid: int,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.025,
    request_json: Callable[..., Awaitable[LoopbackJsonResponse]] | None = None,
) -> dict[str, object]:
    """Poll privacy-safe pool state until no request or waiter remains."""

    _validate_http_probe(port, "/pool", timeout_seconds)
    if (
        isinstance(expected_pid, bool)
        or not isinstance(expected_pid, int)
        or expected_pid <= 0
    ):
        raise RunnerConfigurationError("expected pool worker PID is invalid")
    if (
        isinstance(poll_interval_seconds, bool)
        or not isinstance(poll_interval_seconds, (int, float))
        or not math.isfinite(poll_interval_seconds)
        or poll_interval_seconds <= 0
    ):
        raise RunnerConfigurationError(
            "pool settlement poll interval must be a finite positive number"
        )
    requester = http_request_json if request_json is None else request_json
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReadinessTimeoutError(
                "worker pool did not settle within the bound"
            )
        try:
            response = await asyncio.wait_for(
                requester(
                    port,
                    "/pool",
                    expected_statuses=(200,),
                    timeout_seconds=remaining,
                ),
                timeout=remaining,
            )
        except TimeoutError:
            raise ReadinessTimeoutError(
                "worker pool did not settle within the bound"
            ) from None
        if not isinstance(response, LoopbackJsonResponse):
            raise WorkerEvidenceError("pool settlement response is malformed")
        payload = response.payload
        pool = payload.get("pool")
        admission = payload.get("admission")
        if (
            payload.get("pid") != expected_pid
            or not isinstance(pool, Mapping)
            or not isinstance(admission, Mapping)
        ):
            raise WorkerEvidenceError("pool settlement evidence is malformed")
        active = _nonnegative_metric(pool, "active_connections")
        pending = _nonnegative_metric(pool, "pending_gets")
        admission_active = admission.get("active")
        if (
            isinstance(admission_active, bool)
            or not isinstance(admission_active, int)
            or admission_active < 0
        ):
            raise WorkerEvidenceError("pool settlement evidence is malformed")
        if active == 0 and pending == 0 and admission_active == 0:
            return dict(payload)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReadinessTimeoutError(
                "worker pool did not settle within the bound"
            )
        await asyncio.sleep(min(poll_interval_seconds, remaining))


async def wait_for_saturation_state(
    port: int,
    *,
    expected_pid: int,
    pool_max: int,
    expected_waiters: int,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.025,
    request_json: Callable[..., Awaitable[LoopbackJsonResponse]] | None = None,
) -> dict[str, object]:
    """Poll until both application admission and pool acquisition are full."""

    _validate_http_probe(port, "/pool", timeout_seconds)
    if (
        isinstance(expected_pid, bool)
        or not isinstance(expected_pid, int)
        or expected_pid <= 0
        or isinstance(pool_max, bool)
        or not isinstance(pool_max, int)
        or pool_max <= 0
        or isinstance(expected_waiters, bool)
        or not isinstance(expected_waiters, int)
        or expected_waiters <= 0
    ):
        raise RunnerConfigurationError(
            "saturation pool identity or bounds are invalid"
        )
    if (
        isinstance(poll_interval_seconds, bool)
        or not isinstance(poll_interval_seconds, (int, float))
        or not math.isfinite(poll_interval_seconds)
        or poll_interval_seconds <= 0
    ):
        raise RunnerConfigurationError(
            "saturation poll interval must be a finite positive number"
        )
    requester = http_request_json if request_json is None else request_json
    admission_capacity = pool_max + expected_waiters
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReadinessTimeoutError(
                "worker pool did not reach saturation within the bound"
            )
        try:
            response = await asyncio.wait_for(
                requester(
                    port,
                    "/pool",
                    expected_statuses=(200,),
                    timeout_seconds=remaining,
                ),
                timeout=remaining,
            )
        except TimeoutError:
            raise ReadinessTimeoutError(
                "worker pool did not reach saturation within the bound"
            ) from None
        if not isinstance(response, LoopbackJsonResponse):
            raise WorkerEvidenceError(
                "pool saturation response is malformed"
            )
        payload = response.payload
        pool = payload.get("pool")
        admission = payload.get("admission")
        if (
            payload.get("pid") != expected_pid
            or not isinstance(pool, Mapping)
            or not isinstance(admission, Mapping)
        ):
            raise WorkerEvidenceError(
                "pool saturation evidence is malformed"
            )
        connections = _nonnegative_metric(pool, "connections")
        idle = _nonnegative_metric(pool, "idle_connections")
        active = _nonnegative_metric(pool, "active_connections")
        pending = _nonnegative_metric(pool, "pending_gets")
        admission_active = admission.get("active")
        capacity = admission.get("capacity")
        rejected = admission.get("rejected")
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            for value in (admission_active, capacity, rejected)
        ):
            raise WorkerEvidenceError(
                "pool saturation evidence is malformed"
            )
        if (
            pool.get("max_size") != pool_max
            or connections > pool_max
            or idle > connections
            or active != connections - idle
            or active > pool_max
            or pending > expected_waiters
            or capacity != admission_capacity
            or admission_active > admission_capacity
            or rejected != 0
        ):
            raise WorkerEvidenceError(
                "pool saturation exceeded its configured bounds"
            )
        if (
            connections == pool_max
            and idle == 0
            and active == pool_max
            and pending == expected_waiters
            and admission_active == admission_capacity
        ):
            return dict(payload)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReadinessTimeoutError(
                "worker pool did not reach saturation within the bound"
            )
        await asyncio.sleep(min(poll_interval_seconds, remaining))


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
    _validate_http_probe(port, path, timeout_seconds)
    return await asyncio.wait_for(
        asyncio.to_thread(
            _http_get_json_sync,
            port,
            path,
            timeout_seconds,
        ),
        timeout=timeout_seconds + 0.5,
    )


async def collect_worker_payloads(
    port: int,
    path: str,
    *,
    expected_pids: Sequence[int],
    timeout_seconds: float,
    request_json: Callable[..., Awaitable[dict[str, object]]] | None = None,
    monotonic_counter: str | None = None,
) -> tuple[dict[str, object], ...]:
    """Reach every expected worker through bounded load-balancer dispatch."""

    _validate_http_probe(port, path, timeout_seconds)
    if (
        not expected_pids
        or any(
            isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0
            for pid in expected_pids
        )
        or len(set(expected_pids)) != len(expected_pids)
    ):
        raise RunnerConfigurationError("expected worker PIDs are invalid")
    if monotonic_counter is not None and (
        not isinstance(monotonic_counter, str)
        or not monotonic_counter.isidentifier()
        or monotonic_counter.startswith("_")
    ):
        raise RunnerConfigurationError(
            "worker evidence monotonic counter is invalid"
        )
    requester = http_get_json if request_json is None else request_json
    expected = frozenset(expected_pids)
    observed: dict[int, dict[str, object]] = {}
    deadline = time.monotonic() + timeout_seconds
    while observed.keys() != expected:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReadinessTimeoutError(
                "not every expected worker returned evidence within the bound"
            )
        probe_tasks = tuple(
            asyncio.create_task(
                requester(port, path, timeout_seconds=remaining)
            )
            for _ in range(
                min(len(expected), MAX_WORKER_PROBE_CONCURRENCY)
            )
        )
        try:
            payloads = await asyncio.wait_for(
                asyncio.gather(*probe_tasks),
                timeout=remaining,
            )
        except TimeoutError:
            raise ReadinessTimeoutError(
                "not every expected worker returned evidence within the bound"
            ) from None
        finally:
            for task in probe_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*probe_tasks, return_exceptions=True)
        for payload in payloads:
            if not isinstance(payload, dict):
                raise WorkerEvidenceError("worker HTTP evidence is malformed")
            pid = payload.get("pid")
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                raise WorkerEvidenceError(
                    "worker HTTP evidence has an invalid PID"
                )
            if pid not in expected:
                raise WorkerEvidenceError(
                    "worker HTTP evidence has an unexpected worker PID"
                )
            counter = None
            if monotonic_counter is not None:
                counter = payload.get(monotonic_counter)
                if (
                    isinstance(counter, bool)
                    or not isinstance(counter, int)
                    or counter <= 0
                ):
                    raise WorkerEvidenceError(
                        "worker HTTP monotonic counter is malformed"
                    )
            prior = observed.get(pid)
            if prior is not None and prior != payload:
                if monotonic_counter is None:
                    raise WorkerEvidenceError(
                        "worker HTTP evidence changed for one PID"
                    )
                prior_counter = prior[monotonic_counter]
                prior_stable = {
                    key: value
                    for key, value in prior.items()
                    if key != monotonic_counter
                }
                payload_stable = {
                    key: value
                    for key, value in payload.items()
                    if key != monotonic_counter
                }
                if prior_stable != payload_stable:
                    raise WorkerEvidenceError(
                        "worker HTTP stable evidence changed for one PID"
                    )
                if counter > prior_counter:
                    observed[pid] = dict(payload)
            elif prior is None:
                observed[pid] = dict(payload)
    return tuple(observed[pid] for pid in sorted(observed))


async def run_native_scaling_profile(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    repository_root: Path,
    run_id: str,
    policy: SupervisorPolicy,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    wave_values: Sequence[int],
    sql_delay_ms: int = 250,
) -> NativeScalingProfileResult:
    """Run one exact-worker native FastAPI SQL-auth scaling profile."""

    if (
        config.database_mode != "sql_auth"
        or profile.database_mode != "sql_auth"
        or profile not in native_fastapi_profiles(config.platform_system)
    ):
        raise RunnerConfigurationError(
            "native scaling requires a selected SQL-auth FastAPI profile"
        )
    if (
        not wave_values
        or len(wave_values) > config.global_connection_budget
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in wave_values
        )
    ):
        raise RunnerConfigurationError("native scaling wave values are invalid")

    artifact_directory = (
        config.run_root / "worker-records" / profile.id
    ).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
        sql_auth_settings=sql_auth_settings,
        table_name=table_name,
        sql_delay_ms=sql_delay_ms,
    )
    worker_prefix = environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"]
    observer_application_name = f"{worker_prefix}-observer"
    if len(observer_application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise RunnerConfigurationError(
            "generated observer application name is too long"
        )
    observer_connection = create_observer_connection(
        sql_auth_settings,
        application_name=observer_application_name,
    )
    await observer_connection.connect(validate=True)

    async def execute() -> NativeScalingProfileResult:
        observer = SqlServerObserver(
            source=observer_connection,
            worker_prefix=worker_prefix,
            observer_application_name=observer_application_name,
            poll_interval_seconds=policy.poll_interval_seconds,
        )

        async def worker_is_ready(port: int) -> bool:
            try:
                payload = await http_get_json(
                    port,
                    "/ready",
                    timeout_seconds=0.5,
                )
            except HttpProbeError:
                return False
            return payload.get("state") == "ready"

        launch = await launch_with_port_retry(
            command_builder=lambda port: build_server_command(
                config,
                profile,
                port=port,
            ),
            cwd=isolated.root,
            environment=environment,
            readiness_probe=worker_is_ready,
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
            ready_pids = tuple(
                sorted(int(record["pid"]) for record in ready_records)
            )
            package_records = await collect_worker_payloads(
                launch.port,
                "/package",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            validated_packages = tuple(
                validate_package_record(
                    config,
                    isolated,
                    package,
                    repository_root=repository_root,
                )
                for package in package_records
            )
            principal_records = await collect_worker_payloads(
                launch.port,
                "/principal",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            await observer.wait_for_ready_workers(
                ready_records,
                timeout_seconds=policy.startup_timeout_seconds,
            )

            wave_timeout = max(5.0, sql_delay_ms / 1_000 + 2.0)
            wave_tasks = tuple(
                asyncio.create_task(
                    http_request_json(
                        launch.port,
                        f"/wait/{value}",
                        expected_statuses=(200,),
                        timeout_seconds=wave_timeout,
                    )
                )
                for value in wave_values
            )
            observed_wave = await await_observed_request_wave(
                observer,
                wave_tasks,
                minimum_requests=min(2, len(wave_tasks)),
                timeout_seconds=wave_timeout,
            )
            wave_responses = observed_wave.responses

            pool_records = await collect_worker_payloads(
                launch.port,
                "/pool",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            observer_sample = await observer.sample()
            evidence = validate_native_scaling_evidence(
                config=config,
                profile=profile,
                ready_records=ready_records,
                package_records=validated_packages,
                principal_records=principal_records,
                pool_records=pool_records,
                parameter_payloads=tuple(
                    response.payload for response in wave_responses
                ),
                expected_values=wave_values,
                expected_principal=sql_auth_settings.username,
                observer_sample=observer_sample,
            )
            outcome = await supervisor.stop()

        shutdown_records = supervisor.read_worker_records(
            directory=artifact_directory,
            phase="shutdown",
            expected_run_id=run_id,
            expected_count=profile.workers,
        )
        shutdown_pids = tuple(
            sorted(int(record["pid"]) for record in shutdown_records)
        )
        if evidence.ready_pids != shutdown_pids:
            raise WorkerEvidenceError(
                "ready and shutdown worker PIDs do not reconcile"
            )
        listening_sockets_after = (
            (launch.port,)
            if await loopback_port_is_listening(launch.port)
            else ()
        )
        if listening_sockets_after:
            raise ProcessSupervisorError(
                "loopback listener survived supervised shutdown"
            )
        zero_sample = await observer.wait_for_zero_sessions(
            timeout_seconds=policy.graceful_timeout_seconds,
        )
        verify_isolated_application(isolated)
        return NativeScalingProfileResult(
            profile=profile,
            evidence=evidence,
            port=launch.port,
            launch_attempts=launch.attempts,
            sanitized_command=launch.sanitized_command,
            shutdown_pids=shutdown_pids,
            manager_pid=outcome.pid,
            descendant_pids=outcome.descendant_pids,
            returncode=outcome.returncode,
            graceful_stop=outcome.graceful_stop,
            forced_cleanup=outcome.forced_cleanup,
            listening_sockets_after=listening_sockets_after,
            sessions_after=zero_sample.current_sessions,
        )

    try:
        result = await execute()
    except BaseException as operation_error:
        try:
            await observer_connection.disconnect()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "native scaling profile and observer cleanup both failed",
                [operation_error, cleanup_error],
            ) from None
        raise
    await observer_connection.disconnect()
    return result


async def run_native_scaling_matrix(
    config: RunnerConfig,
    *,
    repository_root: Path,
    source_directory: Path,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    policy: SupervisorPolicy | None = None,
) -> tuple[NativeScalingProfileResult, ...]:
    """Run all applicable native FastAPI server/count combinations once."""

    if config.database_mode != "sql_auth":
        raise RunnerConfigurationError(
            "native scaling matrix requires SQL-auth database mode"
        )
    selected_policy = policy or SupervisorPolicy()
    isolated = prepare_isolated_application(
        config,
        source_directory=source_directory,
        directory_name="native-scaling-application",
    )
    results: list[NativeScalingProfileResult] = []
    for index, profile in enumerate(
        native_fastapi_profiles(config.platform_system),
        start=1,
    ):
        wave_size = max(2, profile.workers)
        if wave_size > config.global_connection_budget:
            raise RunnerConfigurationError(
                "native scaling wave exceeds the global connection budget"
            )
        wave_values = tuple(
            731_000 + index * 100 + offset
            for offset in range(1, wave_size + 1)
        )
        results.append(
            await run_native_scaling_profile(
                config,
                isolated,
                profile,
                repository_root=repository_root,
                run_id=f"nscale-{config.candidate_sha[:8]}-{index}",
                policy=selected_policy,
                sql_auth_settings=sql_auth_settings,
                table_name=table_name,
                wave_values=wave_values,
            )
        )
    verify_isolated_application(isolated)
    return tuple(results)


async def run_flask_wsgi_profile(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    repository_root: Path,
    run_id: str,
    policy: SupervisorPolicy,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    wave_values: Sequence[int],
    sql_delay_ms: int = 250,
) -> FlaskWsgiProfileResult:
    """Run one real Gunicorn Flask profile and prove its WSGI limits."""

    if (
        config.database_mode != "sql_auth"
        or profile.database_mode != "sql_auth"
        or profile not in flask_wsgi_profiles(config.platform_system)
    ):
        raise RunnerConfigurationError(
            "Flask WSGI profile requires selected POSIX SQL-auth Gunicorn"
        )
    if (
        not wave_values
        or len(wave_values) > config.global_connection_budget
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in wave_values
        )
    ):
        raise RunnerConfigurationError("Flask WSGI wave values are invalid")

    artifact_directory = (
        config.run_root / "worker-records" / profile.id
    ).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
        sql_auth_settings=sql_auth_settings,
        table_name=table_name,
        sql_delay_ms=sql_delay_ms,
    )
    worker_prefix = environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"]
    observer_application_name = f"{worker_prefix}-observer"
    if len(observer_application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise RunnerConfigurationError(
            "generated observer application name is too long"
        )
    observer_connection = create_observer_connection(
        sql_auth_settings,
        application_name=observer_application_name,
    )
    await observer_connection.connect(validate=True)

    async def execute() -> FlaskWsgiProfileResult:
        observer = SqlServerObserver(
            source=observer_connection,
            worker_prefix=worker_prefix,
            observer_application_name=observer_application_name,
            poll_interval_seconds=policy.poll_interval_seconds,
        )

        async def worker_is_ready(port: int) -> bool:
            try:
                payload = await http_get_json(
                    port,
                    "/ready",
                    timeout_seconds=0.5,
                )
            except HttpProbeError:
                return False
            return payload.get("state") == "ready"

        launch = await launch_with_port_retry(
            command_builder=lambda port: build_server_command(
                config,
                profile,
                port=port,
            ),
            cwd=isolated.root,
            environment=environment,
            readiness_probe=worker_is_ready,
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
            ready_pids = tuple(
                sorted(int(record["pid"]) for record in ready_records)
            )
            package_records = await collect_worker_payloads(
                launch.port,
                "/package",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            validated_packages = tuple(
                validate_package_record(
                    config,
                    isolated,
                    package,
                    repository_root=repository_root,
                )
                for package in package_records
            )
            principal_records = await collect_worker_payloads(
                launch.port,
                "/principal",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            await observer.wait_for_ready_workers(
                ready_records,
                timeout_seconds=policy.startup_timeout_seconds,
            )

            wave_timeout = max(
                5.0,
                (len(wave_values) * sql_delay_ms) / 1_000 + 2.0,
            )
            wave_started = time.perf_counter()
            wave_tasks = tuple(
                asyncio.create_task(
                    http_request_json(
                        launch.port,
                        f"/execution/wait/{value}",
                        expected_statuses=(200,),
                        timeout_seconds=wave_timeout,
                    )
                )
                for value in wave_values
            )
            minimum_requests = shared_listener_sql_request_minimum(
                profile,
                request_count=len(wave_tasks),
            )
            observed_wave = await await_observed_request_wave(
                observer,
                wave_tasks,
                minimum_requests=minimum_requests,
                timeout_seconds=wave_timeout,
            )
            wave_responses = observed_wave.responses
            wave_seconds = time.perf_counter() - wave_started

            settled_records = await collect_worker_payloads(
                launch.port,
                "/execution/state",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            pool_records = await collect_worker_payloads(
                launch.port,
                "/pool",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            observer_sample = await observer.sample()
            response_payloads = tuple(
                response.payload for response in wave_responses
            )
            scaling_evidence = validate_flask_scaling_evidence(
                config=config,
                profile=profile,
                ready_records=ready_records,
                package_records=validated_packages,
                principal_records=principal_records,
                pool_records=pool_records,
                parameter_payloads=response_payloads,
                expected_values=wave_values,
                expected_principal=sql_auth_settings.username,
                observer_sample=observer_sample,
            )
            wsgi_evidence = validate_flask_wsgi_wave_evidence(
                config=config,
                profile=profile,
                ready_records=ready_records,
                response_payloads=response_payloads,
                settled_records=settled_records,
                expected_values=wave_values,
                wave_seconds=wave_seconds,
                sql_delay_ms=sql_delay_ms,
                observer_sample=observer_sample,
            )
            outcome = await supervisor.stop()
            require_harness_controlled_graceful_shutdown(
                returncode=outcome.returncode,
                graceful_stop=outcome.graceful_stop,
                forced_cleanup=outcome.forced_cleanup,
            )

        shutdown_records = supervisor.read_worker_records(
            directory=artifact_directory,
            phase="shutdown",
            expected_run_id=run_id,
            expected_count=profile.workers,
        )
        shutdown_pids = tuple(
            sorted(int(record["pid"]) for record in shutdown_records)
        )
        if scaling_evidence.ready_pids != shutdown_pids:
            raise WorkerEvidenceError(
                "Flask WSGI ready and shutdown worker PIDs do not reconcile"
            )
        listening_sockets_after = (
            (launch.port,)
            if await loopback_port_is_listening(launch.port)
            else ()
        )
        if listening_sockets_after:
            raise ProcessSupervisorError(
                "Flask WSGI loopback listener survived shutdown"
            )
        zero_sample = await observer.wait_for_zero_sessions(
            timeout_seconds=policy.graceful_timeout_seconds,
        )
        verify_isolated_application(isolated)
        return FlaskWsgiProfileResult(
            profile=profile,
            scaling_evidence=scaling_evidence,
            wsgi_evidence=wsgi_evidence,
            port=launch.port,
            launch_attempts=launch.attempts,
            sanitized_command=launch.sanitized_command,
            shutdown_pids=shutdown_pids,
            manager_pid=outcome.pid,
            descendant_pids=outcome.descendant_pids,
            returncode=outcome.returncode,
            graceful_stop=outcome.graceful_stop,
            forced_cleanup=outcome.forced_cleanup,
            listening_sockets_after=listening_sockets_after,
            sessions_after=zero_sample.current_sessions,
        )

    try:
        result = await execute()
    except BaseException as operation_error:
        try:
            await observer_connection.disconnect()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "Flask WSGI profile and observer cleanup both failed",
                [operation_error, cleanup_error],
            ) from None
        raise
    await observer_connection.disconnect()
    return result


async def run_flask_wsgi_matrix(
    config: RunnerConfig,
    *,
    repository_root: Path,
    source_directory: Path,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    policy: SupervisorPolicy | None = None,
) -> tuple[FlaskWsgiProfileResult, ...]:
    """Run every POSIX Gunicorn sync/gthread worker-count profile."""

    if config.database_mode != "sql_auth":
        raise RunnerConfigurationError(
            "Flask WSGI matrix requires SQL-auth database mode"
        )
    selected_policy = policy or SupervisorPolicy()
    isolated = prepare_isolated_application(
        config,
        source_directory=source_directory,
        directory_name="flask-wsgi-application",
    )
    results: list[FlaskWsgiProfileResult] = []
    for index, profile in enumerate(
        flask_wsgi_profiles(config.platform_system),
        start=1,
    ):
        if profile.workers == 1:
            wave_size = profile.threads_per_worker + 1
        elif profile.worker_class == "sync":
            wave_size = max(2, profile.workers)
        else:
            wave_size = min(
                config.global_connection_budget,
                profile.workers * profile.threads_per_worker,
            )
        if wave_size > config.global_connection_budget:
            raise RunnerConfigurationError(
                "Flask WSGI wave exceeds the global connection budget"
            )
        wave_values = tuple(
            753_000 + index * 100 + offset
            for offset in range(1, wave_size + 1)
        )
        results.append(
            await run_flask_wsgi_profile(
                config,
                isolated,
                profile,
                repository_root=repository_root,
                run_id=f"fwsgi-{config.candidate_sha[:8]}-{index}",
                policy=selected_policy,
                sql_auth_settings=sql_auth_settings,
                table_name=table_name,
                wave_values=wave_values,
            )
        )
    verify_isolated_application(isolated)
    return tuple(results)


async def run_adapted_flask_profile(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    repository_root: Path,
    run_id: str,
    policy: SupervisorPolicy,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    wave_values: Sequence[int],
    sql_delay_ms: int = 250,
) -> AdaptedFlaskProfileResult:
    """Run one Uvicorn-adapted Flask profile with persistent-loop proof."""

    if (
        config.database_mode != "sql_auth"
        or profile.database_mode != "sql_auth"
        or profile not in adapted_flask_profiles(config.platform_system)
    ):
        raise RunnerConfigurationError(
            "adapted Flask profile requires selected SQL-auth Uvicorn"
        )
    if (
        not wave_values
        or len(wave_values) > config.global_connection_budget
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in wave_values
        )
    ):
        raise RunnerConfigurationError(
            "adapted Flask wave values are invalid"
        )
    artifact_directory = (
        config.run_root / "worker-records" / profile.id
    ).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
        sql_auth_settings=sql_auth_settings,
        table_name=table_name,
        sql_delay_ms=sql_delay_ms,
    )
    worker_prefix = environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"]
    observer_application_name = f"{worker_prefix}-observer"
    if len(observer_application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise RunnerConfigurationError(
            "generated observer application name is too long"
        )
    observer_connection = create_observer_connection(
        sql_auth_settings,
        application_name=observer_application_name,
    )
    await observer_connection.connect(validate=True)

    async def execute() -> AdaptedFlaskProfileResult:
        observer = SqlServerObserver(
            source=observer_connection,
            worker_prefix=worker_prefix,
            observer_application_name=observer_application_name,
            poll_interval_seconds=policy.poll_interval_seconds,
        )

        async def worker_is_ready(port: int) -> bool:
            try:
                payload = await http_get_json(
                    port,
                    "/ready",
                    timeout_seconds=0.5,
                )
            except HttpProbeError:
                return False
            return payload.get("state") == "ready"

        launch = await launch_with_port_retry(
            command_builder=lambda port: build_server_command(
                config,
                profile,
                port=port,
            ),
            cwd=isolated.root,
            environment=environment,
            readiness_probe=worker_is_ready,
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
            ready_pids = tuple(
                sorted(int(record["pid"]) for record in ready_records)
            )
            package_records = await collect_worker_payloads(
                launch.port,
                "/package",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            validated_packages = tuple(
                validate_package_record(
                    config,
                    isolated,
                    package,
                    repository_root=repository_root,
                )
                for package in package_records
            )
            principal_records = await collect_worker_payloads(
                launch.port,
                "/principal",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            await observer.wait_for_ready_workers(
                ready_records,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            first_loop_records = await collect_worker_payloads(
                launch.port,
                "/loop",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
                monotonic_counter="request_sequence",
            )
            second_loop_records = await collect_worker_payloads(
                launch.port,
                "/loop",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
                monotonic_counter="request_sequence",
            )

            wave_timeout = max(
                5.0,
                (len(wave_values) * sql_delay_ms) / 1_000 + 2.0,
            )
            wave_tasks = tuple(
                asyncio.create_task(
                    http_request_json(
                        launch.port,
                        f"/execution/wait/{value}",
                        expected_statuses=(200,),
                        timeout_seconds=wave_timeout,
                    )
                )
                for value in wave_values
            )
            minimum_requests = shared_listener_sql_request_minimum(
                profile,
                request_count=len(wave_tasks),
            )
            observed_wave = await await_observed_request_wave(
                observer,
                wave_tasks,
                minimum_requests=minimum_requests,
                timeout_seconds=wave_timeout,
            )
            wave_responses = observed_wave.responses

            settled_records = await collect_worker_payloads(
                launch.port,
                "/execution/state",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            pool_records = await collect_worker_payloads(
                launch.port,
                "/pool",
                expected_pids=ready_pids,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            observer_sample = await observer.sample()
            response_payloads = tuple(
                response.payload for response in wave_responses
            )
            scaling_evidence = validate_flask_scaling_evidence(
                config=config,
                profile=profile,
                ready_records=ready_records,
                package_records=validated_packages,
                principal_records=principal_records,
                pool_records=pool_records,
                parameter_payloads=response_payloads,
                expected_values=wave_values,
                expected_principal=sql_auth_settings.username,
                observer_sample=observer_sample,
            )
            adapted_evidence = validate_adapted_flask_scaling_evidence(
                config=config,
                profile=profile,
                ready_records=ready_records,
                first_loop_records=first_loop_records,
                second_loop_records=second_loop_records,
                response_payloads=response_payloads,
                settled_records=settled_records,
                observer_sample=observer_sample,
            )
            outcome = await supervisor.stop()
            require_harness_controlled_graceful_shutdown(
                returncode=outcome.returncode,
                graceful_stop=outcome.graceful_stop,
                forced_cleanup=outcome.forced_cleanup,
            )

        shutdown_records = supervisor.read_worker_records(
            directory=artifact_directory,
            phase="shutdown",
            expected_run_id=run_id,
            expected_count=profile.workers,
        )
        shutdown_pids = tuple(
            sorted(int(record["pid"]) for record in shutdown_records)
        )
        if scaling_evidence.ready_pids != shutdown_pids:
            raise WorkerEvidenceError(
                "adapted Flask ready and shutdown PIDs do not reconcile"
            )
        listening_sockets_after = (
            (launch.port,)
            if await loopback_port_is_listening(launch.port)
            else ()
        )
        if listening_sockets_after:
            raise ProcessSupervisorError(
                "adapted Flask loopback listener survived shutdown"
            )
        zero_sample = await observer.wait_for_zero_sessions(
            timeout_seconds=policy.graceful_timeout_seconds,
        )
        verify_isolated_application(isolated)
        return AdaptedFlaskProfileResult(
            profile=profile,
            scaling_evidence=scaling_evidence,
            adapted_evidence=adapted_evidence,
            port=launch.port,
            launch_attempts=launch.attempts,
            sanitized_command=launch.sanitized_command,
            shutdown_pids=shutdown_pids,
            manager_pid=outcome.pid,
            descendant_pids=outcome.descendant_pids,
            returncode=outcome.returncode,
            graceful_stop=outcome.graceful_stop,
            forced_cleanup=outcome.forced_cleanup,
            listening_sockets_after=listening_sockets_after,
            sessions_after=zero_sample.current_sessions,
        )

    try:
        result = await execute()
    except BaseException as operation_error:
        try:
            await observer_connection.disconnect()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "adapted Flask profile and observer cleanup both failed",
                [operation_error, cleanup_error],
            ) from None
        raise
    await observer_connection.disconnect()
    return result


async def run_adapted_flask_matrix(
    config: RunnerConfig,
    *,
    repository_root: Path,
    source_directory: Path,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    policy: SupervisorPolicy | None = None,
) -> tuple[AdaptedFlaskProfileResult, ...]:
    """Run every applicable adapted-Flask loop/worker-count profile."""

    if config.database_mode != "sql_auth":
        raise RunnerConfigurationError(
            "adapted Flask matrix requires SQL-auth database mode"
        )
    selected_policy = policy or SupervisorPolicy()
    isolated = prepare_isolated_application(
        config,
        source_directory=source_directory,
        directory_name="adapted-flask-application",
    )
    results: list[AdaptedFlaskProfileResult] = []
    for index, profile in enumerate(
        adapted_flask_profiles(config.platform_system),
        start=1,
    ):
        wave_size = max(2, profile.workers)
        if wave_size > config.global_connection_budget:
            raise RunnerConfigurationError(
                "adapted Flask wave exceeds the global connection budget"
            )
        wave_values = tuple(
            762_000 + index * 100 + offset
            for offset in range(1, wave_size + 1)
        )
        results.append(
            await run_adapted_flask_profile(
                config,
                isolated,
                profile,
                repository_root=repository_root,
                run_id=f"fasgi-{config.candidate_sha[:8]}-{index}",
                policy=selected_policy,
                sql_auth_settings=sql_auth_settings,
                table_name=table_name,
                wave_values=wave_values,
            )
        )
    verify_isolated_application(isolated)
    return tuple(results)


async def run_adapted_flask_serialization_scenario(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    run_id: str,
    policy: SupervisorPolicy,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    values: Sequence[int],
    sql_delay_ms: int = 250,
) -> AdaptedFlaskSerializationScenarioResult:
    """Measure one WsgiToAsgi thread-sensitive lane under concurrent clients."""

    expected_profile = next(
        (
            candidate
            for candidate in adapted_flask_profiles(config.platform_system)
            if candidate.family == "flask-asgi-uvicorn-asyncio"
            and candidate.workers == 1
        ),
        None,
    )
    if (
        config.database_mode != "sql_auth"
        or profile != expected_profile
        or len(values) != 4
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in values
        )
        or sql_delay_ms not in SQL_DELAY_MILLISECONDS
        or sql_delay_ms <= 0
    ):
        raise RunnerConfigurationError(
            "adapted Flask serialization requires one asyncio worker and four values"
        )
    artifact_directory = (
        config.run_root / "worker-records" / "adapted-serialization"
    ).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
        sql_auth_settings=sql_auth_settings,
        table_name=table_name,
        sql_delay_ms=sql_delay_ms,
    )
    worker_prefix = environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"]
    observer_application_name = f"{worker_prefix}-observer"
    if len(observer_application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise RunnerConfigurationError(
            "generated observer application name is too long"
        )
    observer_connection = create_observer_connection(
        sql_auth_settings,
        application_name=observer_application_name,
    )
    await observer_connection.connect(validate=True)

    async def execute() -> AdaptedFlaskSerializationScenarioResult:
        observer = SqlServerObserver(
            source=observer_connection,
            worker_prefix=worker_prefix,
            observer_application_name=observer_application_name,
            poll_interval_seconds=policy.poll_interval_seconds,
        )

        async def worker_is_ready(port: int) -> bool:
            try:
                payload = await http_get_json(
                    port,
                    "/ready",
                    timeout_seconds=0.5,
                )
            except HttpProbeError:
                return False
            return payload.get("state") == "ready"

        launch = await launch_with_port_retry(
            command_builder=lambda port: build_server_command(
                config,
                profile,
                port=port,
            ),
            cwd=isolated.root,
            environment=environment,
            readiness_probe=worker_is_ready,
            policy=policy,
        )
        supervisor = launch.supervisor
        request_timeout = max(
            8.0,
            len(values) * sql_delay_ms / 1_000 + 3.0,
        )
        async with supervisor:
            ready_records = await supervisor.wait_for_worker_records(
                directory=artifact_directory,
                phase="ready",
                expected_run_id=run_id,
                expected_count=1,
            )
            ready_pids = tuple(
                sorted(int(record["pid"]) for record in ready_records)
            )
            await observer.wait_for_ready_workers(
                ready_records,
                timeout_seconds=policy.startup_timeout_seconds,
            )

            sequential_started = time.perf_counter()
            sequential_responses = tuple(
                [
                    await http_request_json(
                        launch.port,
                        f"/execution/wait/{value}",
                        expected_statuses=(200,),
                        timeout_seconds=request_timeout,
                    )
                    for value in values
                ]
            )
            sequential_seconds = time.perf_counter() - sequential_started

            concurrent_started = time.perf_counter()
            concurrent_tasks = tuple(
                asyncio.create_task(
                    http_request_json(
                        launch.port,
                        f"/execution/wait/{value}",
                        expected_statuses=(200,),
                        timeout_seconds=request_timeout,
                    )
                )
                for value in values
            )
            observed_wave = await await_observed_request_wave(
                observer,
                concurrent_tasks,
                minimum_requests=1,
                timeout_seconds=request_timeout,
            )
            concurrent_responses = observed_wave.responses
            busy_sample = observed_wave.observer_sample
            concurrent_seconds = time.perf_counter() - concurrent_started

            state_response = await http_request_json(
                launch.port,
                "/execution/state",
                expected_statuses=(200,),
                timeout_seconds=request_timeout,
            )
            pool_response = await http_request_json(
                launch.port,
                "/pool",
                expected_statuses=(200,),
                timeout_seconds=request_timeout,
            )
            evidence = validate_adapted_flask_serialization_evidence(
                sequential_payloads=tuple(
                    response.payload for response in sequential_responses
                ),
                concurrent_payloads=tuple(
                    response.payload for response in concurrent_responses
                ),
                expected_values=values,
                sequential_seconds=sequential_seconds,
                concurrent_seconds=concurrent_seconds,
                state_record=state_response.payload,
                observer_sample=busy_sample,
                pool_record=pool_response.payload,
                sql_delay_ms=sql_delay_ms,
            )
            outcome = await supervisor.stop()
            require_harness_controlled_graceful_shutdown(
                returncode=outcome.returncode,
                graceful_stop=outcome.graceful_stop,
                forced_cleanup=outcome.forced_cleanup,
            )

        shutdown_records = supervisor.read_worker_records(
            directory=artifact_directory,
            phase="shutdown",
            expected_run_id=run_id,
            expected_count=1,
        )
        shutdown_pids = tuple(
            sorted(int(record["pid"]) for record in shutdown_records)
        )
        if ready_pids != shutdown_pids:
            raise WorkerEvidenceError(
                "adapted serialization ready and shutdown PIDs do not reconcile"
            )
        listening_sockets_after = (
            (launch.port,)
            if await loopback_port_is_listening(launch.port)
            else ()
        )
        if listening_sockets_after:
            raise ProcessSupervisorError(
                "adapted serialization listener survived shutdown"
            )
        zero_sample = await observer.wait_for_zero_sessions(
            timeout_seconds=policy.graceful_timeout_seconds,
        )
        verify_isolated_application(isolated)
        return AdaptedFlaskSerializationScenarioResult(
            profile_id=profile.id,
            evidence=evidence,
            ready_pids=ready_pids,
            shutdown_pids=shutdown_pids,
            manager_pid=outcome.pid,
            descendant_pids=outcome.descendant_pids,
            returncode=outcome.returncode,
            graceful_stop=outcome.graceful_stop,
            forced_cleanup=outcome.forced_cleanup,
            listening_sockets_after=listening_sockets_after,
            sessions_after=zero_sample.current_sessions,
        )

    try:
        result = await execute()
    except BaseException as operation_error:
        try:
            await observer_connection.disconnect()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "adapted serialization and observer cleanup both failed",
                [operation_error, cleanup_error],
            ) from None
        raise
    await observer_connection.disconnect()
    return result


async def run_flask_gather_scenario(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    run_id: str,
    policy: SupervisorPolicy,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    sql_delay_ms: int = 250,
) -> FlaskGatherScenarioResult:
    """Prove four concurrent SQL waits inside one real Flask WSGI request."""

    expected_profile = next(
        (
            candidate
            for candidate in flask_wsgi_profiles(config.platform_system)
            if candidate.family == "flask-gunicorn-sync"
            and candidate.workers == 1
        ),
        None,
    )
    if (
        config.database_mode != "sql_auth"
        or profile != expected_profile
        or sql_delay_ms not in SQL_DELAY_MILLISECONDS
        or sql_delay_ms <= 0
    ):
        raise RunnerConfigurationError(
            "Flask gather requires one SQL-auth Gunicorn sync worker"
        )
    artifact_directory = (
        config.run_root / "worker-records" / "flask-gather"
    ).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
        sql_auth_settings=sql_auth_settings,
        table_name=table_name,
        sql_delay_ms=sql_delay_ms,
    )
    worker_prefix = environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"]
    observer_application_name = f"{worker_prefix}-observer"
    if len(observer_application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise RunnerConfigurationError(
            "generated observer application name is too long"
        )
    observer_connection = create_observer_connection(
        sql_auth_settings,
        application_name=observer_application_name,
    )
    await observer_connection.connect(validate=True)

    async def execute() -> FlaskGatherScenarioResult:
        observer = SqlServerObserver(
            source=observer_connection,
            worker_prefix=worker_prefix,
            observer_application_name=observer_application_name,
            poll_interval_seconds=policy.poll_interval_seconds,
        )

        async def worker_is_ready(port: int) -> bool:
            try:
                payload = await http_get_json(
                    port,
                    "/ready",
                    timeout_seconds=0.5,
                )
            except HttpProbeError:
                return False
            return payload.get("state") == "ready"

        launch = await launch_with_port_retry(
            command_builder=lambda port: build_server_command(
                config,
                profile,
                port=port,
            ),
            cwd=isolated.root,
            environment=environment,
            readiness_probe=worker_is_ready,
            policy=policy,
        )
        supervisor = launch.supervisor
        request_timeout = max(8.0, sql_delay_ms / 1_000 * 6 + 2.0)
        async with supervisor:
            ready_records = await supervisor.wait_for_worker_records(
                directory=artifact_directory,
                phase="ready",
                expected_run_id=run_id,
                expected_count=1,
            )
            ready_pids = tuple(
                sorted(int(record["pid"]) for record in ready_records)
            )
            await observer.wait_for_ready_workers(
                ready_records,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            gather_task = asyncio.create_task(
                http_request_json(
                    launch.port,
                    "/gather",
                    expected_statuses=(200,),
                    timeout_seconds=request_timeout,
                )
            )
            observed_wave = await await_observed_request_wave(
                observer,
                (gather_task,),
                minimum_requests=4,
                timeout_seconds=request_timeout,
            )
            (response,) = observed_wave.responses
            busy_sample = observed_wave.observer_sample

            state_response = await http_request_json(
                launch.port,
                "/execution/state",
                expected_statuses=(200,),
                timeout_seconds=request_timeout,
            )
            pool_response = await http_request_json(
                launch.port,
                "/pool",
                expected_statuses=(200,),
                timeout_seconds=request_timeout,
            )
            evidence = validate_flask_gather_evidence(
                response=response,
                state_record=state_response.payload,
                observer_sample=busy_sample,
                pool_record=pool_response.payload,
                sql_delay_ms=sql_delay_ms,
            )
            outcome = await supervisor.stop()
            require_harness_controlled_graceful_shutdown(
                returncode=outcome.returncode,
                graceful_stop=outcome.graceful_stop,
                forced_cleanup=outcome.forced_cleanup,
            )

        shutdown_records = supervisor.read_worker_records(
            directory=artifact_directory,
            phase="shutdown",
            expected_run_id=run_id,
            expected_count=1,
        )
        shutdown_pids = tuple(
            sorted(int(record["pid"]) for record in shutdown_records)
        )
        if ready_pids != shutdown_pids:
            raise WorkerEvidenceError(
                "Flask gather ready and shutdown worker PIDs do not reconcile"
            )
        listening_sockets_after = (
            (launch.port,)
            if await loopback_port_is_listening(launch.port)
            else ()
        )
        if listening_sockets_after:
            raise ProcessSupervisorError(
                "Flask gather listener survived supervised shutdown"
            )
        zero_sample = await observer.wait_for_zero_sessions(
            timeout_seconds=policy.graceful_timeout_seconds,
        )
        verify_isolated_application(isolated)
        return FlaskGatherScenarioResult(
            profile_id=profile.id,
            evidence=evidence,
            ready_pids=ready_pids,
            shutdown_pids=shutdown_pids,
            manager_pid=outcome.pid,
            descendant_pids=outcome.descendant_pids,
            returncode=outcome.returncode,
            graceful_stop=outcome.graceful_stop,
            forced_cleanup=outcome.forced_cleanup,
            listening_sockets_after=listening_sockets_after,
            sessions_after=zero_sample.current_sessions,
        )

    try:
        result = await execute()
    except BaseException as operation_error:
        try:
            await observer_connection.disconnect()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "Flask gather scenario and observer cleanup both failed",
                [operation_error, cleanup_error],
            ) from None
        raise
    await observer_connection.disconnect()
    return result


async def run_native_concurrency_scenario(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    run_id: str,
    policy: SupervisorPolicy,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    values: Sequence[int],
    sql_delay_ms: int = 250,
) -> NativeConcurrencyScenarioResult:
    """Measure same-server sequential/concurrent waits and event-loop health."""

    expected_profile = next(
        (
            candidate
            for candidate in native_fastapi_profiles(config.platform_system)
            if candidate.family == "fastapi-uvicorn-asyncio"
            and candidate.workers == 1
        ),
        None,
    )
    if (
        config.database_mode != "sql_auth"
        or profile != expected_profile
        or len(values) < 2
        or len(values) > config.global_connection_budget
    ):
        raise RunnerConfigurationError(
            "native concurrency requires one SQL-auth Uvicorn asyncio worker"
        )
    artifact_directory = (
        config.run_root / "worker-records" / "native-concurrency"
    ).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
        sql_auth_settings=sql_auth_settings,
        table_name=table_name,
        sql_delay_ms=sql_delay_ms,
    )
    worker_prefix = environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"]
    observer_application_name = f"{worker_prefix}-observer"
    if len(observer_application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise RunnerConfigurationError(
            "generated observer application name is too long"
        )
    observer_connection = create_observer_connection(
        sql_auth_settings,
        application_name=observer_application_name,
    )
    await observer_connection.connect(validate=True)

    async def execute() -> NativeConcurrencyScenarioResult:
        observer = SqlServerObserver(
            source=observer_connection,
            worker_prefix=worker_prefix,
            observer_application_name=observer_application_name,
            poll_interval_seconds=policy.poll_interval_seconds,
        )

        async def worker_is_ready(port: int) -> bool:
            try:
                payload = await http_get_json(
                    port,
                    "/ready",
                    timeout_seconds=0.5,
                )
            except HttpProbeError:
                return False
            return payload.get("state") == "ready"

        launch = await launch_with_port_retry(
            command_builder=lambda port: build_server_command(
                config,
                profile,
                port=port,
            ),
            cwd=isolated.root,
            environment=environment,
            readiness_probe=worker_is_ready,
            policy=policy,
        )
        supervisor = launch.supervisor
        request_timeout = max(5.0, sql_delay_ms / 1_000 + 2.0)
        async with supervisor:
            ready_records = await supervisor.wait_for_worker_records(
                directory=artifact_directory,
                phase="ready",
                expected_run_id=run_id,
                expected_count=1,
            )
            ready_pids = tuple(
                sorted(int(record["pid"]) for record in ready_records)
            )
            await observer.wait_for_ready_workers(
                ready_records,
                timeout_seconds=policy.startup_timeout_seconds,
            )

            sequential_started = time.perf_counter()
            sequential_responses = tuple(
                [
                    await http_request_json(
                        launch.port,
                        f"/wait/{value}",
                        expected_statuses=(200,),
                        timeout_seconds=request_timeout,
                    )
                    for value in values
                ]
            )
            sequential_seconds = time.perf_counter() - sequential_started

            concurrent_started = time.perf_counter()
            concurrent_tasks = tuple(
                asyncio.create_task(
                    http_request_json(
                        launch.port,
                        f"/wait/{value}",
                        expected_statuses=(200,),
                        timeout_seconds=request_timeout,
                    )
                )
                for value in values
            )
            try:
                busy_sample = await observer.wait_for_minimum_requests(
                    2,
                    timeout_seconds=request_timeout,
                )
                health_started = time.perf_counter()
                health = await http_request_json(
                    launch.port,
                    "/ready",
                    expected_statuses=(200,),
                    timeout_seconds=request_timeout,
                )
                health_seconds = time.perf_counter() - health_started
                if health.payload.get("state") != "ready":
                    raise WorkerEvidenceError(
                        "native event-loop health response is inconsistent"
                    )
                concurrent_responses = await asyncio.wait_for(
                    asyncio.gather(*concurrent_tasks),
                    timeout=request_timeout,
                )
                concurrent_seconds = time.perf_counter() - concurrent_started
            finally:
                for task in concurrent_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*concurrent_tasks, return_exceptions=True)

            pool_response = await http_request_json(
                launch.port,
                "/pool",
                expected_statuses=(200,),
                timeout_seconds=request_timeout,
            )
            if pool_response.payload.get("pid") != ready_pids[0]:
                raise WorkerEvidenceError(
                    "native concurrency pool response PID is inconsistent"
                )
            evidence = validate_native_concurrency_evidence(
                sequential_payloads=tuple(
                    response.payload for response in sequential_responses
                ),
                concurrent_payloads=tuple(
                    response.payload for response in concurrent_responses
                ),
                expected_values=values,
                sequential_seconds=sequential_seconds,
                concurrent_seconds=concurrent_seconds,
                health_seconds=health_seconds,
                sql_delay_ms=sql_delay_ms,
                observer_sample=busy_sample,
                pool_record=pool_response.payload,
            )
            outcome = await supervisor.stop()

        shutdown_records = supervisor.read_worker_records(
            directory=artifact_directory,
            phase="shutdown",
            expected_run_id=run_id,
            expected_count=1,
        )
        shutdown_pids = tuple(
            sorted(int(record["pid"]) for record in shutdown_records)
        )
        if ready_pids != shutdown_pids:
            raise WorkerEvidenceError(
                "ready and shutdown worker PIDs do not reconcile"
            )
        listening_sockets_after = (
            (launch.port,)
            if await loopback_port_is_listening(launch.port)
            else ()
        )
        if listening_sockets_after:
            raise ProcessSupervisorError(
                "loopback listener survived supervised shutdown"
            )
        zero_sample = await observer.wait_for_zero_sessions(
            timeout_seconds=policy.graceful_timeout_seconds,
        )
        verify_isolated_application(isolated)
        return NativeConcurrencyScenarioResult(
            profile_id=profile.id,
            evidence=evidence,
            ready_pids=ready_pids,
            shutdown_pids=shutdown_pids,
            manager_pid=outcome.pid,
            descendant_pids=outcome.descendant_pids,
            returncode=outcome.returncode,
            graceful_stop=outcome.graceful_stop,
            forced_cleanup=outcome.forced_cleanup,
            listening_sockets_after=listening_sockets_after,
            sessions_after=zero_sample.current_sessions,
        )

    try:
        result = await execute()
    except BaseException as operation_error:
        try:
            await observer_connection.disconnect()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "native concurrency scenario and observer cleanup both failed",
                [operation_error, cleanup_error],
            ) from None
        raise
    await observer_connection.disconnect()
    return result


async def run_native_disconnect_scenario(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    run_id: str,
    policy: SupervisorPolicy,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    token: str,
) -> NativeDisconnectScenarioResult:
    """Close a real TCP peer only after its identifiable SQL wait appears."""

    if (
        config.database_mode != "sql_auth"
        or profile not in native_fastapi_profiles(config.platform_system)
        or profile.server != "uvicorn"
        or profile.workers != 1
    ):
        raise RunnerConfigurationError(
            "native disconnect requires one SQL-auth Uvicorn worker"
        )
    if (
        not isinstance(token, str)
        or OBSERVER_CONTEXT_TOKEN_PATTERN.fullmatch(token) is None
    ):
        raise RunnerConfigurationError("disconnect context token is invalid")
    artifact_directory = (
        config.run_root / "worker-records" / "native-disconnect"
    ).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
        sql_auth_settings=sql_auth_settings,
        table_name=table_name,
    )
    worker_prefix = environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"]
    observer_application_name = f"{worker_prefix}-observer"
    if len(observer_application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise RunnerConfigurationError(
            "generated observer application name is too long"
        )
    observer_connection = create_observer_connection(
        sql_auth_settings,
        application_name=observer_application_name,
    )
    await observer_connection.connect(validate=True)

    async def execute() -> NativeDisconnectScenarioResult:
        observer = SqlServerObserver(
            source=observer_connection,
            worker_prefix=worker_prefix,
            observer_application_name=observer_application_name,
            poll_interval_seconds=policy.poll_interval_seconds,
        )

        async def worker_is_ready(port: int) -> bool:
            try:
                payload = await http_get_json(
                    port,
                    "/ready",
                    timeout_seconds=0.5,
                )
            except HttpProbeError:
                return False
            return payload.get("state") == "ready"

        launch = await launch_with_port_retry(
            command_builder=lambda port: build_server_command(
                config,
                profile,
                port=port,
            ),
            cwd=isolated.root,
            environment=environment,
            readiness_probe=worker_is_ready,
            policy=policy,
        )
        supervisor = launch.supervisor
        scenario_timeout = 10.0
        async with supervisor:
            ready_records = await supervisor.wait_for_worker_records(
                directory=artifact_directory,
                phase="ready",
                expected_run_id=run_id,
                expected_count=1,
            )
            ready_pids = tuple(
                sorted(int(record["pid"]) for record in ready_records)
            )
            await observer.wait_for_ready_workers(
                ready_records,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            before_response = await http_request_json(
                launch.port,
                "/pool",
                expected_statuses=(200,),
                timeout_seconds=scenario_timeout,
            )
            if before_response.payload.get("pid") != ready_pids[0]:
                raise WorkerEvidenceError(
                    "disconnect pool response PID is inconsistent"
                )
            before_pool = before_response.payload.get("pool")
            if not isinstance(before_pool, Mapping):
                raise WorkerEvidenceError(
                    "disconnect pool evidence is malformed"
                )

            raw_request = await open_raw_http_request(
                launch.port,
                f"/cancel/{token}",
                timeout_seconds=scenario_timeout,
            )
            try:
                observed_sample = await observer.wait_for_context_token(
                    token,
                    present=True,
                    timeout_seconds=scenario_timeout,
                )
            finally:
                await raw_request.close(timeout_seconds=scenario_timeout)
            settled_sample = await observer.wait_for_context_token(
                token,
                present=False,
                timeout_seconds=scenario_timeout,
            )
            await wait_for_pool_settlement(
                launch.port,
                expected_pid=ready_pids[0],
                timeout_seconds=scenario_timeout,
                poll_interval_seconds=policy.poll_interval_seconds,
            )
            recovery = await http_request_json(
                launch.port,
                "/value/36",
                expected_statuses=(200,),
                timeout_seconds=scenario_timeout,
            )
            after_payload = await wait_for_pool_settlement(
                launch.port,
                expected_pid=ready_pids[0],
                timeout_seconds=scenario_timeout,
                poll_interval_seconds=policy.poll_interval_seconds,
            )
            after_pool = after_payload.get("pool")
            if not isinstance(after_pool, Mapping):
                raise WorkerEvidenceError(
                    "disconnect recovery pool evidence is malformed"
                )
            evidence = validate_disconnect_evidence(
                token=token,
                observed_sample=observed_sample,
                settled_sample=settled_sample,
                before_pool=before_pool,
                after_pool=after_pool,
                recovery_payload=recovery.payload,
            )
            outcome = await supervisor.stop()

        if token in outcome.stdout or token in outcome.stderr:
            raise WorkerEvidenceError(
                "disconnect context token appeared in server diagnostics"
            )
        shutdown_records = supervisor.read_worker_records(
            directory=artifact_directory,
            phase="shutdown",
            expected_run_id=run_id,
            expected_count=1,
        )
        shutdown_pids = tuple(
            sorted(int(record["pid"]) for record in shutdown_records)
        )
        if ready_pids != shutdown_pids:
            raise WorkerEvidenceError(
                "ready and shutdown worker PIDs do not reconcile"
            )
        listening_sockets_after = (
            (launch.port,)
            if await loopback_port_is_listening(launch.port)
            else ()
        )
        if listening_sockets_after:
            raise ProcessSupervisorError(
                "loopback listener survived supervised shutdown"
            )
        zero_sample = await observer.wait_for_zero_sessions(
            timeout_seconds=policy.graceful_timeout_seconds,
        )
        verify_isolated_application(isolated)
        return NativeDisconnectScenarioResult(
            profile_id=profile.id,
            evidence=evidence,
            ready_pids=ready_pids,
            shutdown_pids=shutdown_pids,
            manager_pid=outcome.pid,
            descendant_pids=outcome.descendant_pids,
            returncode=outcome.returncode,
            graceful_stop=outcome.graceful_stop,
            forced_cleanup=outcome.forced_cleanup,
            listening_sockets_after=listening_sockets_after,
            sessions_after=zero_sample.current_sessions,
        )

    try:
        result = await execute()
    except BaseException as operation_error:
        try:
            await observer_connection.disconnect()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "native disconnect scenario and observer cleanup both failed",
                [operation_error, cleanup_error],
            ) from None
        raise
    await observer_connection.disconnect()
    return result


async def run_native_graceful_query_scenario(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    run_id: str,
    policy: SupervisorPolicy,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    token: str,
    value: int,
    sql_delay_ms: int,
) -> NativeGracefulQueryScenarioResult:
    """Signal one POSIX Uvicorn process while identified SQL is in flight."""

    expected_profile = next(
        (
            candidate
            for candidate in native_fastapi_profiles(config.platform_system)
            if candidate.family == "fastapi-uvicorn-asyncio"
            and candidate.workers == 1
        ),
        None,
    )
    if (
        os.name != "posix"
        or config.database_mode != "sql_auth"
        or profile != expected_profile
    ):
        raise RunnerConfigurationError(
            "graceful query requires one POSIX SQL-auth Uvicorn asyncio worker"
        )
    if (
        not isinstance(token, str)
        or OBSERVER_CONTEXT_TOKEN_PATTERN.fullmatch(token) is None
        or isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_SQL_BIGINT <= value <= MAX_SQL_BIGINT
        or sql_delay_ms not in SQL_DELAY_MILLISECONDS
        or sql_delay_ms <= 0
    ):
        raise RunnerConfigurationError(
            "graceful query token, value or SQL delay is invalid"
        )
    artifact_directory = (
        config.run_root / "worker-records" / "native-graceful-query"
    ).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
        sql_auth_settings=sql_auth_settings,
        table_name=table_name,
        sql_delay_ms=sql_delay_ms,
    )
    worker_prefix = environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"]
    observer_application_name = f"{worker_prefix}-observer"
    if len(observer_application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise RunnerConfigurationError(
            "generated observer application name is too long"
        )
    observer_connection = create_observer_connection(
        sql_auth_settings,
        application_name=observer_application_name,
    )
    await observer_connection.connect(validate=True)

    async def execute() -> NativeGracefulQueryScenarioResult:
        observer = SqlServerObserver(
            source=observer_connection,
            worker_prefix=worker_prefix,
            observer_application_name=observer_application_name,
            poll_interval_seconds=policy.poll_interval_seconds,
        )

        async def worker_is_ready(port: int) -> bool:
            try:
                payload = await http_get_json(
                    port,
                    "/ready",
                    timeout_seconds=0.5,
                )
            except HttpProbeError:
                return False
            return payload.get("state") == "ready"

        launch = await launch_with_port_retry(
            command_builder=lambda port: build_server_command(
                config,
                profile,
                port=port,
            ),
            cwd=isolated.root,
            environment=environment,
            readiness_probe=worker_is_ready,
            policy=policy,
        )
        supervisor = launch.supervisor
        scenario_timeout = max(5.0, sql_delay_ms / 1_000 + 3.0)
        async with supervisor:
            ready_records = await supervisor.wait_for_worker_records(
                directory=artifact_directory,
                phase="ready",
                expected_run_id=run_id,
                expected_count=1,
            )
            ready_pids = tuple(
                sorted(int(record["pid"]) for record in ready_records)
            )
            await observer.wait_for_ready_workers(
                ready_records,
                timeout_seconds=policy.startup_timeout_seconds,
            )

            response_task = asyncio.create_task(
                http_request_json(
                    launch.port,
                    f"/wait/{value}",
                    expected_statuses=(200,),
                    timeout_seconds=scenario_timeout,
                    context_token=token,
                )
            )
            await asyncio.sleep(0)
            stop_task: asyncio.Task[ProcessOutcome] | None = None
            try:
                observed_sample = await observer.wait_for_context_token(
                    token,
                    present=True,
                    timeout_seconds=scenario_timeout,
                )
                stop_task = asyncio.create_task(supervisor.stop())
                response = await response_task
                outcome = await stop_task
            finally:
                pending_tasks = tuple(
                    task
                    for task in (response_task, stop_task)
                    if task is not None and not task.done()
                )
                for task in pending_tasks:
                    task.cancel()
                if pending_tasks:
                    await asyncio.gather(
                        *pending_tasks,
                        return_exceptions=True,
                    )

            evidence = validate_graceful_query_evidence(
                token=token,
                observed_sample=observed_sample,
                response_payload=response.payload,
                expected_value=value,
                sql_delay_ms=sql_delay_ms,
            )
            if (
                outcome.returncode not in {0, -signal.SIGTERM}
                or not outcome.graceful_stop
                or outcome.forced_cleanup
            ):
                raise WorkerEvidenceError(
                    "graceful query server did not exit normally"
                )

        if token in outcome.stdout or token in outcome.stderr:
            raise WorkerEvidenceError(
                "graceful query token appeared in server diagnostics"
            )
        shutdown_records = supervisor.read_worker_records(
            directory=artifact_directory,
            phase="shutdown",
            expected_run_id=run_id,
            expected_count=1,
        )
        shutdown_pids = tuple(
            sorted(int(record["pid"]) for record in shutdown_records)
        )
        if ready_pids != shutdown_pids:
            raise WorkerEvidenceError(
                "graceful query ready and shutdown worker PIDs do not reconcile"
            )
        listening_sockets_after = (
            (launch.port,)
            if await loopback_port_is_listening(launch.port)
            else ()
        )
        if listening_sockets_after:
            raise ProcessSupervisorError(
                "loopback listener survived graceful query shutdown"
            )
        zero_sample = await observer.wait_for_zero_sessions(
            timeout_seconds=policy.graceful_timeout_seconds,
        )
        verify_isolated_application(isolated)
        return NativeGracefulQueryScenarioResult(
            profile_id=profile.id,
            evidence=evidence,
            ready_pids=ready_pids,
            shutdown_pids=shutdown_pids,
            manager_pid=outcome.pid,
            descendant_pids=outcome.descendant_pids,
            returncode=outcome.returncode,
            graceful_stop=outcome.graceful_stop,
            forced_cleanup=outcome.forced_cleanup,
            listening_sockets_after=listening_sockets_after,
            sessions_after=zero_sample.current_sessions,
        )

    try:
        result = await execute()
    except BaseException as operation_error:
        try:
            await observer_connection.disconnect()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "graceful query scenario and observer cleanup both failed",
                [operation_error, cleanup_error],
            ) from None
        raise
    await observer_connection.disconnect()
    return result


async def run_native_graceful_transaction_scenario(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    run_id: str,
    policy: SupervisorPolicy,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    item_id: int,
    outcome: str,
    sql_delay_ms: int,
) -> NativeGracefulTransactionScenarioResult:
    """Signal one POSIX Uvicorn process during a pinned transaction."""

    expected_profile = next(
        (
            candidate
            for candidate in native_fastapi_profiles(config.platform_system)
            if candidate.family == "fastapi-uvicorn-asyncio"
            and candidate.workers == 1
        ),
        None,
    )
    if (
        os.name != "posix"
        or config.database_mode != "sql_auth"
        or profile != expected_profile
    ):
        raise RunnerConfigurationError(
            "graceful transaction requires one POSIX SQL-auth Uvicorn "
            "asyncio worker"
        )
    if (
        not isinstance(table_name, str)
        or SQL_IDENTIFIER_PATTERN.fullmatch(table_name) is None
        or isinstance(item_id, bool)
        or not isinstance(item_id, int)
        or not 1 <= item_id <= MAX_SQL_BIGINT
        or outcome not in {"commit", "rollback"}
        or sql_delay_ms not in SQL_DELAY_MILLISECONDS
        or sql_delay_ms <= 0
    ):
        raise RunnerConfigurationError(
            "graceful transaction table, identity, outcome or SQL delay is "
            "invalid"
        )

    token = f"transaction:{item_id}:{outcome}"
    artifact_directory = (
        config.run_root
        / "worker-records"
        / f"native-graceful-transaction-{outcome}"
    ).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
        sql_auth_settings=sql_auth_settings,
        table_name=table_name,
        sql_delay_ms=sql_delay_ms,
    )
    worker_prefix = environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"]
    observer_application_name = f"{worker_prefix}-observer"
    if len(observer_application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise RunnerConfigurationError(
            "generated observer application name is too long"
        )

    qualified_table = f"dbo.[{table_name}]"
    drop_sql = f"DROP TABLE IF EXISTS {qualified_table}"
    create_sql = (
        f"CREATE TABLE {qualified_table} ("
        "[id] BIGINT NOT NULL PRIMARY KEY, "
        "[value] NVARCHAR(64) NOT NULL)"
    )
    durable_sql = (
        f"SELECT [value] AS [value] FROM {qualified_table} "
        "WHERE [id] = @P1"
    )
    observer_connection = create_observer_connection(
        sql_auth_settings,
        application_name=observer_application_name,
    )
    await observer_connection.connect(validate=True)

    async def execute() -> NativeGracefulTransactionScenarioResult:
        observer = SqlServerObserver(
            source=observer_connection,
            worker_prefix=worker_prefix,
            observer_application_name=observer_application_name,
            poll_interval_seconds=policy.poll_interval_seconds,
        )

        async def worker_is_ready(port: int) -> bool:
            try:
                payload = await http_get_json(
                    port,
                    "/ready",
                    timeout_seconds=0.5,
                )
            except HttpProbeError:
                return False
            return payload.get("state") == "ready"

        launch = await launch_with_port_retry(
            command_builder=lambda port: build_server_command(
                config,
                profile,
                port=port,
            ),
            cwd=isolated.root,
            environment=environment,
            readiness_probe=worker_is_ready,
            policy=policy,
        )
        supervisor = launch.supervisor
        scenario_timeout = max(5.0, sql_delay_ms / 1_000 + 3.0)
        async with supervisor:
            ready_records = await supervisor.wait_for_worker_records(
                directory=artifact_directory,
                phase="ready",
                expected_run_id=run_id,
                expected_count=1,
            )
            ready_pids = tuple(
                sorted(int(record["pid"]) for record in ready_records)
            )
            await observer.wait_for_ready_workers(
                ready_records,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            worker_application_name = ready_records[0].get(
                "worker_application_name"
            )
            if (
                not isinstance(worker_application_name, str)
                or APPLICATION_DIRECTORY_PATTERN.fullmatch(
                    worker_application_name
                )
                is None
            ):
                raise WorkerEvidenceError(
                    "graceful transaction worker application name is invalid"
                )

            response_task = asyncio.create_task(
                http_request_json(
                    launch.port,
                    f"/transaction/{item_id}?outcome={outcome}",
                    method="POST",
                    expected_statuses=(200,),
                    timeout_seconds=scenario_timeout,
                )
            )
            await asyncio.sleep(0)
            stop_task: asyncio.Task[ProcessOutcome] | None = None
            try:
                holding_record = await wait_for_transaction_phase_record(
                    supervisor,
                    directory=artifact_directory,
                    expected_run_id=run_id,
                    expected_pid=ready_pids[0],
                    expected_worker_application_name=(
                        worker_application_name
                    ),
                    item_id=item_id,
                    outcome=outcome,
                    transaction_phase="holding",
                    timeout_seconds=scenario_timeout,
                )
                observed_sample = await observer.wait_for_context_token(
                    token,
                    present=True,
                    timeout_seconds=scenario_timeout,
                )
                stop_task = asyncio.create_task(supervisor.stop())
                response = await response_task
                outcome_record = await stop_task
            finally:
                pending_tasks = tuple(
                    task
                    for task in (response_task, stop_task)
                    if task is not None and not task.done()
                )
                for task in pending_tasks:
                    task.cancel()
                if pending_tasks:
                    await asyncio.gather(
                        *pending_tasks,
                        return_exceptions=True,
                    )

            if (
                outcome_record.returncode not in {0, -signal.SIGTERM}
                or not outcome_record.graceful_stop
                or outcome_record.forced_cleanup
            ):
                raise WorkerEvidenceError(
                    "graceful transaction server did not exit normally"
                )

        if token in outcome_record.stdout or token in outcome_record.stderr:
            raise WorkerEvidenceError(
                "graceful transaction token appeared in server diagnostics"
            )
        shutdown_records = supervisor.read_worker_records(
            directory=artifact_directory,
            phase="shutdown",
            expected_run_id=run_id,
            expected_count=1,
        )
        shutdown_pids = tuple(
            sorted(int(record["pid"]) for record in shutdown_records)
        )
        if ready_pids != shutdown_pids:
            raise WorkerEvidenceError(
                "graceful transaction ready and shutdown worker PIDs do not "
                "reconcile"
            )
        settled_record = await wait_for_transaction_phase_record(
            supervisor,
            directory=artifact_directory,
            expected_run_id=run_id,
            expected_pid=ready_pids[0],
            expected_worker_application_name=worker_application_name,
            item_id=item_id,
            outcome=outcome,
            transaction_phase="settled",
            timeout_seconds=scenario_timeout,
        )
        listening_sockets_after = (
            (launch.port,)
            if await loopback_port_is_listening(launch.port)
            else ()
        )
        if listening_sockets_after:
            raise ProcessSupervisorError(
                "loopback listener survived graceful transaction shutdown"
            )
        zero_sample = await observer.wait_for_zero_sessions(
            timeout_seconds=policy.graceful_timeout_seconds,
        )
        durable_result = await observer_connection.query(
            durable_sql,
            [item_id],
        )
        durable_rows = tuple(durable_result.all())
        evidence = validate_graceful_transaction_evidence(
            token=token,
            item_id=item_id,
            outcome=outcome,
            holding_record=holding_record,
            settled_record=settled_record,
            observed_sample=observed_sample,
            response_payload=response.payload,
            durable_rows=durable_rows,
        )
        verify_isolated_application(isolated)
        return NativeGracefulTransactionScenarioResult(
            profile_id=profile.id,
            evidence=evidence,
            ready_pids=ready_pids,
            shutdown_pids=shutdown_pids,
            manager_pid=outcome_record.pid,
            descendant_pids=outcome_record.descendant_pids,
            returncode=outcome_record.returncode,
            graceful_stop=outcome_record.graceful_stop,
            forced_cleanup=outcome_record.forced_cleanup,
            listening_sockets_after=listening_sockets_after,
            sessions_after=zero_sample.current_sessions,
        )

    async def cleanup() -> tuple[BaseException, ...]:
        errors: list[BaseException] = []
        try:
            await observer_connection.execute(drop_sql)
        except BaseException as error:
            errors.append(error)
        try:
            await observer_connection.disconnect()
        except BaseException as error:
            errors.append(error)
        return tuple(errors)

    try:
        await observer_connection.execute(drop_sql)
        await observer_connection.execute(create_sql)
        result = await execute()
    except BaseException as operation_error:
        cleanup_errors = await cleanup()
        if cleanup_errors:
            raise BaseExceptionGroup(
                "graceful transaction scenario and cleanup failed",
                [operation_error, *cleanup_errors],
            ) from None
        raise
    cleanup_errors = await cleanup()
    if len(cleanup_errors) == 1:
        raise cleanup_errors[0]
    if cleanup_errors:
        raise BaseExceptionGroup(
            "graceful transaction cleanup failed",
            list(cleanup_errors),
        )
    return result


async def run_native_saturation_scenario(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    run_id: str,
    policy: SupervisorPolicy,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    holder_values: Sequence[int],
    waiter_values: Sequence[int],
    excess_values: Sequence[int],
    recovery_value: int,
    sql_delay_ms: int,
    acquire_timeout_ms: int,
) -> NativeSaturationScenarioResult:
    """Saturate one worker's app admission and FastMssql pool exactly."""

    expected_profile = next(
        (
            candidate
            for candidate in native_fastapi_profiles(config.platform_system)
            if candidate.family == "fastapi-uvicorn-asyncio"
            and candidate.workers == 1
        ),
        None,
    )
    pool_max = config.global_connection_budget
    all_values = (
        *holder_values,
        *waiter_values,
        *excess_values,
        recovery_value,
    )
    if (
        config.database_mode != "sql_auth"
        or profile != expected_profile
        or pool_max <= 0
        or len(holder_values) != pool_max
        or len(waiter_values) != pool_max
        or not excess_values
        or sql_delay_ms not in SQL_DELAY_MILLISECONDS
        or sql_delay_ms <= 0
        or acquire_timeout_ms not in ACQUIRE_TIMEOUT_MILLISECONDS
        or acquire_timeout_ms >= sql_delay_ms
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not MIN_SQL_BIGINT <= value <= MAX_SQL_BIGINT
            for value in all_values
        )
        or len(set(all_values)) != len(all_values)
    ):
        raise RunnerConfigurationError(
            "native saturation profile, workload or timeout is invalid"
        )

    artifact_directory = (
        config.run_root / "worker-records" / "native-saturation"
    ).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
        sql_auth_settings=sql_auth_settings,
        table_name=table_name,
        sql_delay_ms=sql_delay_ms,
        acquire_timeout_ms=acquire_timeout_ms,
    )
    worker_prefix = environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"]
    observer_application_name = f"{worker_prefix}-observer"
    if len(observer_application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise RunnerConfigurationError(
            "generated observer application name is too long"
        )
    observer_connection = create_observer_connection(
        sql_auth_settings,
        application_name=observer_application_name,
    )
    await observer_connection.connect(validate=True)

    async def execute() -> NativeSaturationScenarioResult:
        observer = SqlServerObserver(
            source=observer_connection,
            worker_prefix=worker_prefix,
            observer_application_name=observer_application_name,
            poll_interval_seconds=policy.poll_interval_seconds,
        )

        async def worker_is_ready(port: int) -> bool:
            try:
                payload = await http_get_json(
                    port,
                    "/ready",
                    timeout_seconds=0.5,
                )
            except HttpProbeError:
                return False
            return payload.get("state") == "ready"

        launch = await launch_with_port_retry(
            command_builder=lambda port: build_server_command(
                config,
                profile,
                port=port,
            ),
            cwd=isolated.root,
            environment=environment,
            readiness_probe=worker_is_ready,
            policy=policy,
        )
        supervisor = launch.supervisor
        scenario_timeout = max(
            5.0,
            sql_delay_ms / 1_000 + 3.0,
            acquire_timeout_ms / 1_000 + 3.0,
        )
        request_tasks: list[asyncio.Task[LoopbackJsonResponse]] = []
        async with supervisor:
            ready_records = await supervisor.wait_for_worker_records(
                directory=artifact_directory,
                phase="ready",
                expected_run_id=run_id,
                expected_count=1,
            )
            ready_pids = tuple(
                sorted(int(record["pid"]) for record in ready_records)
            )
            await observer.wait_for_ready_workers(
                ready_records,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            worker_application_name = ready_records[0].get(
                "worker_application_name"
            )
            if (
                not isinstance(worker_application_name, str)
                or APPLICATION_DIRECTORY_PATTERN.fullmatch(
                    worker_application_name
                )
                is None
            ):
                raise WorkerEvidenceError(
                    "native saturation worker application name is invalid"
                )
            baseline_response = await http_request_json(
                launch.port,
                "/pool",
                expected_statuses=(200,),
                timeout_seconds=scenario_timeout,
            )

            try:
                holder_tasks = tuple(
                    asyncio.create_task(
                        http_request_json(
                            launch.port,
                            f"/saturated/{value}",
                            expected_statuses=(200,),
                            timeout_seconds=scenario_timeout,
                        )
                    )
                    for value in holder_values
                )
                request_tasks.extend(holder_tasks)
                await asyncio.sleep(0)
                busy_sample = await observer.wait_for_minimum_requests(
                    pool_max,
                    timeout_seconds=scenario_timeout,
                )

                waiter_tasks = tuple(
                    asyncio.create_task(
                        http_request_json(
                            launch.port,
                            f"/saturated/{value}",
                            expected_statuses=(504,),
                            timeout_seconds=scenario_timeout,
                        )
                    )
                    for value in waiter_values
                )
                request_tasks.extend(waiter_tasks)
                await asyncio.sleep(0)
                saturation_timeout = max(
                    policy.poll_interval_seconds * 2,
                    acquire_timeout_ms / 1_000 * 0.8,
                )
                saturated_pool_record = await wait_for_saturation_state(
                    launch.port,
                    expected_pid=ready_pids[0],
                    pool_max=pool_max,
                    expected_waiters=pool_max,
                    timeout_seconds=saturation_timeout,
                    poll_interval_seconds=policy.poll_interval_seconds,
                )

                rejection_tasks = tuple(
                    asyncio.create_task(
                        http_request_json(
                            launch.port,
                            f"/saturated/{value}",
                            expected_statuses=(503,),
                            timeout_seconds=scenario_timeout,
                        )
                    )
                    for value in excess_values
                )
                request_tasks.extend(rejection_tasks)
                rejection_responses = tuple(
                    await asyncio.gather(*rejection_tasks)
                )
                waiter_responses = tuple(
                    await asyncio.gather(*waiter_tasks)
                )
                holder_responses = tuple(
                    await asyncio.gather(*holder_tasks)
                )
                await wait_for_pool_settlement(
                    launch.port,
                    expected_pid=ready_pids[0],
                    timeout_seconds=scenario_timeout,
                    poll_interval_seconds=policy.poll_interval_seconds,
                )
                recovery_response = await http_request_json(
                    launch.port,
                    f"/saturated/{recovery_value}",
                    expected_statuses=(200,),
                    timeout_seconds=scenario_timeout,
                )
                settled_pool_record = await wait_for_pool_settlement(
                    launch.port,
                    expected_pid=ready_pids[0],
                    timeout_seconds=scenario_timeout,
                    poll_interval_seconds=policy.poll_interval_seconds,
                )
                evidence = validate_native_saturation_evidence(
                    expected_pid=ready_pids[0],
                    expected_application_name=worker_application_name,
                    holder_values=holder_values,
                    waiter_values=waiter_values,
                    excess_values=excess_values,
                    recovery_value=recovery_value,
                    acquire_timeout_ms=acquire_timeout_ms,
                    busy_sample=busy_sample,
                    baseline_pool_record=baseline_response.payload,
                    saturated_pool_record=saturated_pool_record,
                    settled_pool_record=settled_pool_record,
                    holder_responses=holder_responses,
                    waiter_responses=waiter_responses,
                    rejection_responses=rejection_responses,
                    recovery_response=recovery_response,
                )
                outcome_record = await supervisor.stop()
            finally:
                pending_tasks = tuple(
                    task for task in request_tasks if not task.done()
                )
                for task in pending_tasks:
                    task.cancel()
                if pending_tasks:
                    await asyncio.gather(
                        *pending_tasks,
                        return_exceptions=True,
                    )

        if (
            outcome_record.returncode not in {0, -signal.SIGTERM}
            or not outcome_record.graceful_stop
            or outcome_record.forced_cleanup
        ):
            raise WorkerEvidenceError(
                "native saturation server did not stop cleanly"
            )
        shutdown_records = supervisor.read_worker_records(
            directory=artifact_directory,
            phase="shutdown",
            expected_run_id=run_id,
            expected_count=1,
        )
        shutdown_pids = tuple(
            sorted(int(record["pid"]) for record in shutdown_records)
        )
        if ready_pids != shutdown_pids:
            raise WorkerEvidenceError(
                "native saturation ready and shutdown worker PIDs do not "
                "reconcile"
            )
        listening_sockets_after = (
            (launch.port,)
            if await loopback_port_is_listening(launch.port)
            else ()
        )
        if listening_sockets_after:
            raise ProcessSupervisorError(
                "loopback listener survived native saturation shutdown"
            )
        zero_sample = await observer.wait_for_zero_sessions(
            timeout_seconds=policy.graceful_timeout_seconds,
        )
        verify_isolated_application(isolated)
        return NativeSaturationScenarioResult(
            profile_id=profile.id,
            evidence=evidence,
            ready_pids=ready_pids,
            shutdown_pids=shutdown_pids,
            manager_pid=outcome_record.pid,
            descendant_pids=outcome_record.descendant_pids,
            returncode=outcome_record.returncode,
            graceful_stop=outcome_record.graceful_stop,
            forced_cleanup=outcome_record.forced_cleanup,
            listening_sockets_after=listening_sockets_after,
            sessions_after=zero_sample.current_sessions,
        )

    try:
        result = await execute()
    except BaseException as operation_error:
        try:
            await observer_connection.disconnect()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "native saturation scenario and observer cleanup both failed",
                [operation_error, cleanup_error],
            ) from None
        raise
    await observer_connection.disconnect()
    return result


async def run_native_streaming_scenario(
    config: RunnerConfig,
    isolated: IsolatedApplication,
    profile: ProcessProfile,
    *,
    run_id: str,
    policy: SupervisorPolicy,
    sql_auth_settings: SqlAuthObserverSettings,
    table_name: str,
    full_rows: int,
    early_rows: int,
    early_prefix_rows: int,
    recovery_value: int,
    rss_growth_limit_bytes: int,
) -> NativeStreamingScenarioResult:
    """Prove full and early-close ResultStream delivery through real HTTP."""

    expected_profile = next(
        (
            candidate
            for candidate in native_fastapi_profiles(config.platform_system)
            if candidate.family == "fastapi-uvicorn-asyncio"
            and candidate.workers == 1
        ),
        None,
    )
    if (
        config.database_mode != "sql_auth"
        or profile != expected_profile
        or isinstance(full_rows, bool)
        or not isinstance(full_rows, int)
        or not 2 <= full_rows <= MAX_HTTP_STREAM_ROWS
        or isinstance(early_rows, bool)
        or not isinstance(early_rows, int)
        or not 2 <= early_rows <= MAX_HTTP_STREAM_ROWS
        or isinstance(early_prefix_rows, bool)
        or not isinstance(early_prefix_rows, int)
        or not 1 <= early_prefix_rows < early_rows
        or isinstance(recovery_value, bool)
        or not isinstance(recovery_value, int)
        or not MIN_SQL_BIGINT <= recovery_value <= MAX_SQL_BIGINT
        or isinstance(rss_growth_limit_bytes, bool)
        or not isinstance(rss_growth_limit_bytes, int)
        or rss_growth_limit_bytes <= 0
    ):
        raise RunnerConfigurationError(
            "native streaming profile, rows, recovery or RSS bound is invalid"
        )

    artifact_directory = (
        config.run_root / "worker-records" / "native-streaming"
    ).resolve()
    environment = build_profile_environment(
        config,
        isolated,
        profile,
        run_id=run_id,
        artifact_directory=artifact_directory,
        sql_auth_settings=sql_auth_settings,
        table_name=table_name,
    )
    worker_prefix = environment["FASTMSSQL_FRAMEWORK_APPLICATION_NAME"]
    observer_application_name = f"{worker_prefix}-observer"
    if len(observer_application_name) > MAX_SQL_SERVER_APPLICATION_NAME:
        raise RunnerConfigurationError(
            "generated observer application name is too long"
        )
    observer_connection = create_observer_connection(
        sql_auth_settings,
        application_name=observer_application_name,
    )
    await observer_connection.connect(validate=True)

    async def execute() -> NativeStreamingScenarioResult:
        observer = SqlServerObserver(
            source=observer_connection,
            worker_prefix=worker_prefix,
            observer_application_name=observer_application_name,
            poll_interval_seconds=policy.poll_interval_seconds,
        )

        async def worker_is_ready(port: int) -> bool:
            try:
                payload = await http_get_json(
                    port,
                    "/ready",
                    timeout_seconds=0.5,
                )
            except HttpProbeError:
                return False
            return payload.get("state") == "ready"

        launch = await launch_with_port_retry(
            command_builder=lambda port: build_server_command(
                config,
                profile,
                port=port,
            ),
            cwd=isolated.root,
            environment=environment,
            readiness_probe=worker_is_ready,
            policy=policy,
        )
        supervisor = launch.supervisor
        scenario_timeout = max(15.0, policy.graceful_timeout_seconds)
        async with supervisor:
            ready_records = await supervisor.wait_for_worker_records(
                directory=artifact_directory,
                phase="ready",
                expected_run_id=run_id,
                expected_count=1,
            )
            ready_pids = tuple(
                sorted(int(record["pid"]) for record in ready_records)
            )
            await observer.wait_for_ready_workers(
                ready_records,
                timeout_seconds=policy.startup_timeout_seconds,
            )
            rss_start_bytes = process_rss_bytes(ready_pids)
            rss_stop = asyncio.Event()
            rss_monitor = asyncio.create_task(
                monitor_process_rss(
                    ready_pids,
                    stop=rss_stop,
                    poll_interval_seconds=policy.poll_interval_seconds,
                )
            )
            try:
                full_observation = await consume_ndjson_stream(
                    launch.port,
                    f"/stream?rows={full_rows}",
                    expected_rows=full_rows,
                    timeout_seconds=scenario_timeout,
                )
                full_settled_pool_record = await wait_for_pool_settlement(
                    launch.port,
                    expected_pid=ready_pids[0],
                    timeout_seconds=scenario_timeout,
                    poll_interval_seconds=policy.poll_interval_seconds,
                )

                early_active_sample: SqlObserverSample | None = None

                async def observe_early_sql() -> None:
                    nonlocal early_active_sample
                    early_active_sample = await observer.wait_for_minimum_requests(
                        1,
                        timeout_seconds=scenario_timeout,
                    )

                early_observation = await read_ndjson_prefix_and_close(
                    launch.port,
                    f"/stream?rows={early_rows}",
                    requested_rows=early_rows,
                    prefix_rows=early_prefix_rows,
                    timeout_seconds=scenario_timeout,
                    before_read=observe_early_sql,
                )
                if early_active_sample is None:
                    raise WorkerEvidenceError(
                        "early-close stream was not observed in SQL Server"
                    )
                early_settled_sample = await observer.wait_for_zero_requests(
                    timeout_seconds=scenario_timeout,
                )
                early_settled_pool_record = await wait_for_pool_settlement(
                    launch.port,
                    expected_pid=ready_pids[0],
                    timeout_seconds=scenario_timeout,
                    poll_interval_seconds=policy.poll_interval_seconds,
                )
                recovery_response = await http_request_json(
                    launch.port,
                    f"/value/{recovery_value}",
                    expected_statuses=(200,),
                    timeout_seconds=scenario_timeout,
                )
                await wait_for_pool_settlement(
                    launch.port,
                    expected_pid=ready_pids[0],
                    timeout_seconds=scenario_timeout,
                    poll_interval_seconds=policy.poll_interval_seconds,
                )
            except BaseException as operation_error:
                rss_stop.set()
                try:
                    await rss_monitor
                except BaseException as monitor_error:
                    raise BaseExceptionGroup(
                        "native streaming operation and RSS monitor failed",
                        [operation_error, monitor_error],
                    ) from None
                raise
            rss_stop.set()
            rss_peak_bytes = await rss_monitor
            rss_end_bytes = process_rss_bytes(ready_pids)
            rss_peak_bytes = max(
                rss_start_bytes,
                rss_peak_bytes,
                rss_end_bytes,
            )
            evidence = validate_native_streaming_evidence(
                expected_pid=ready_pids[0],
                pool_max=config.global_connection_budget,
                full_observation=full_observation,
                early_observation=early_observation,
                driver_buffer_rows=EXPECTED_STREAM_BUFFER_ROWS,
                rss_start_bytes=rss_start_bytes,
                rss_peak_bytes=rss_peak_bytes,
                rss_end_bytes=rss_end_bytes,
                rss_growth_limit_bytes=rss_growth_limit_bytes,
                full_settled_pool_record=full_settled_pool_record,
                early_settled_pool_record=early_settled_pool_record,
                early_settled_sample=early_settled_sample,
                recovery_value=recovery_value,
                recovery_response=recovery_response,
            )
            outcome_record = await supervisor.stop()

        if (
            outcome_record.returncode not in {0, -signal.SIGTERM}
            or not outcome_record.graceful_stop
            or outcome_record.forced_cleanup
        ):
            raise WorkerEvidenceError(
                "native streaming server did not stop cleanly"
            )
        shutdown_records = supervisor.read_worker_records(
            directory=artifact_directory,
            phase="shutdown",
            expected_run_id=run_id,
            expected_count=1,
        )
        shutdown_pids = tuple(
            sorted(int(record["pid"]) for record in shutdown_records)
        )
        if ready_pids != shutdown_pids:
            raise WorkerEvidenceError(
                "native streaming ready and shutdown worker PIDs do not "
                "reconcile"
            )
        listening_sockets_after = (
            (launch.port,)
            if await loopback_port_is_listening(launch.port)
            else ()
        )
        if listening_sockets_after:
            raise ProcessSupervisorError(
                "loopback listener survived native streaming shutdown"
            )
        zero_sample = await observer.wait_for_zero_sessions(
            timeout_seconds=policy.graceful_timeout_seconds,
        )
        verify_isolated_application(isolated)
        return NativeStreamingScenarioResult(
            profile_id=profile.id,
            evidence=evidence,
            ready_pids=ready_pids,
            shutdown_pids=shutdown_pids,
            manager_pid=outcome_record.pid,
            descendant_pids=outcome_record.descendant_pids,
            returncode=outcome_record.returncode,
            graceful_stop=outcome_record.graceful_stop,
            forced_cleanup=outcome_record.forced_cleanup,
            listening_sockets_after=listening_sockets_after,
            sessions_after=zero_sample.current_sessions,
        )

    try:
        result = await execute()
    except BaseException as operation_error:
        try:
            await observer_connection.disconnect()
        except BaseException as cleanup_error:
            raise BaseExceptionGroup(
                "native streaming scenario and observer cleanup both failed",
                [operation_error, cleanup_error],
            ) from None
        raise
    await observer_connection.disconnect()
    return result


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
