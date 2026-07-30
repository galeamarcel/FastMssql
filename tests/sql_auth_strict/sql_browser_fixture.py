from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import socket
from typing import Literal


SQL_BROWSER_PORT = 1434
SQL_BROWSER_RESPONSE_TYPE = 0x05
SQL_BROWSER_MAX_PAYLOAD = 1_024
FixtureMode = Literal[
    "valid",
    "silent",
    "malformed",
    "wrong_source",
    "refused_tcp",
]


@dataclass(frozen=True)
class SqlBrowserSnapshot:
    datagrams: int
    valid_requests: int
    invalid_requests: int
    responses: int
    source_rejection_probes: int
    shutdowns: int
    requests: tuple[bytes, ...]


class _SqlBrowserProtocol(asyncio.DatagramProtocol):
    def __init__(self, fixture: "SqlBrowserFixture") -> None:
        self.fixture = fixture

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        if not isinstance(transport, asyncio.DatagramTransport):
            raise TypeError("SQL Browser fixture requires a datagram transport")
        self.fixture._transport = transport
        self.fixture._ready.set()

    def datagram_received(
        self,
        data: bytes,
        address: tuple[str, int],
    ) -> None:
        self.fixture._receive(data, address)

    def error_received(self, error: Exception) -> None:
        self.fixture._protocol_errors.append(error)
        self.fixture._request_changed.set()

    def connection_lost(self, error: Exception | None) -> None:
        if error is not None:
            self.fixture._protocol_errors.append(error)
        self.fixture._shutdowns += 1
        if not self.fixture._closed.done():
            self.fixture._closed.set_result(None)


class SqlBrowserFixture:
    """Deterministic loopback SSRP responder for the Linux Docker SQL Server.

    This fixture emulates only the SQL Browser lookup. The discovered TCP
    endpoint is the real SQL Server container port, so authentication, TLS,
    TDS login and every SQL operation still execute against SQL Server.
    """

    def __init__(
        self,
        *,
        host: str,
        tcp_port: int,
        instance_name: str = "FASTMSSQL",
        mode: FixtureMode = "valid",
        malformed_response: bytes | None = None,
    ) -> None:
        address = ipaddress.ip_address(host)
        if not address.is_loopback or address.version != 4:
            raise ValueError(
                "SQL Browser fixture host must be one numeric IPv4 loopback address"
            )
        encoded_instance = instance_name.encode("utf-8")
        if (
            not encoded_instance
            or b"\x00" in encoded_instance
            or len(encoded_instance) > 32
        ):
            raise ValueError("fixture instance name violates the SQLR wire contract")
        if not 1 <= tcp_port <= 65_535:
            raise ValueError("fixture TCP port must be between 1 and 65535")
        if mode == "malformed" and malformed_response is None:
            raise ValueError("malformed mode requires an explicit response")
        if mode != "malformed" and malformed_response is not None:
            raise ValueError("explicit malformed response requires malformed mode")

        self.host = host
        self.tcp_port = tcp_port
        self.instance_name = instance_name
        self.mode = mode
        self.malformed_response = malformed_response
        self.expected_request = b"\x04" + encoded_instance + b"\x00"
        self._transport: asyncio.DatagramTransport | None = None
        self._ready = asyncio.Event()
        self._request_changed = asyncio.Event()
        self._closed: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._protocol_errors: list[Exception] = []
        self._requests: list[bytes] = []
        self._valid_requests = 0
        self._invalid_requests = 0
        self._responses = 0
        self._source_rejection_probes = 0
        self._shutdowns = 0
        self._refused_socket: socket.socket | None = None

    @staticmethod
    def response_for_port(port: int) -> bytes:
        if not 1 <= port <= 65_535:
            raise ValueError("SQL Browser response port must be valid")
        payload = (
            b"InstanceName;FASTMSSQL;IsClustered;No;tcp;"
            + str(port).encode("ascii")
            + b";"
        )
        if len(payload) > SQL_BROWSER_MAX_PAYLOAD:
            raise AssertionError("fixture response exceeded the SQLR payload bound")
        return (
            bytes((SQL_BROWSER_RESPONSE_TYPE,))
            + len(payload).to_bytes(2, "little")
            + payload
        )

    @property
    def selected_tcp_port(self) -> int:
        if self._refused_socket is None:
            return self.tcp_port
        return int(self._refused_socket.getsockname()[1])

    @property
    def response(self) -> bytes:
        if self.mode == "malformed":
            assert self.malformed_response is not None
            return self.malformed_response
        return self.response_for_port(self.selected_tcp_port)

    async def start(self) -> "SqlBrowserFixture":
        if self._transport is not None:
            raise RuntimeError("SQL Browser fixture is already started")
        if self.mode == "refused_tcp":
            refused = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            refused.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            refused.bind((self.host, 0))
            self._refused_socket = refused

        loop = asyncio.get_running_loop()
        try:
            await loop.create_datagram_endpoint(
                lambda: _SqlBrowserProtocol(self),
                local_addr=(self.host, SQL_BROWSER_PORT),
                family=socket.AF_INET,
            )
        except OSError as error:
            if self._refused_socket is not None:
                self._refused_socket.close()
                self._refused_socket = None
            raise RuntimeError(
                f"cannot bind deterministic SQL Browser fixture at "
                f"{self.host}:{SQL_BROWSER_PORT}/udp: {error}"
            ) from error
        await asyncio.wait_for(self._ready.wait(), timeout=1.0)
        return self

    def _receive(self, data: bytes, address: tuple[str, int]) -> None:
        self._requests.append(bytes(data))
        if data != self.expected_request:
            self._invalid_requests += 1
            self._request_changed.set()
            return

        self._valid_requests += 1
        self._request_changed.set()
        if self.mode == "silent":
            return
        if self.mode == "wrong_source":
            wrong_source = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                wrong_source.bind((self.host, 0))
                wrong_source.sendto(self.response, address)
            finally:
                wrong_source.close()
            self._source_rejection_probes += 1
            return

        if self._transport is None:
            raise RuntimeError("SQL Browser fixture received before readiness")
        self._transport.sendto(self.response, address)
        self._responses += 1

    async def wait_for_requests(
        self,
        count: int,
        *,
        timeout: float = 2.0,
    ) -> SqlBrowserSnapshot:
        if count < 0:
            raise ValueError("request count cannot be negative")

        async def wait() -> None:
            while self._valid_requests < count:
                self._request_changed.clear()
                if self._valid_requests >= count:
                    break
                await self._request_changed.wait()

        await asyncio.wait_for(wait(), timeout=timeout)
        return self.snapshot()

    def snapshot(self) -> SqlBrowserSnapshot:
        return SqlBrowserSnapshot(
            datagrams=len(self._requests),
            valid_requests=self._valid_requests,
            invalid_requests=self._invalid_requests,
            responses=self._responses,
            source_rejection_probes=self._source_rejection_probes,
            shutdowns=self._shutdowns,
            requests=tuple(self._requests),
        )

    async def close(self) -> None:
        transport = self._transport
        self._transport = None
        if transport is not None:
            transport.close()
            await asyncio.wait_for(asyncio.shield(self._closed), timeout=1.0)
        if self._refused_socket is not None:
            self._refused_socket.close()
            self._refused_socket = None
        if self._protocol_errors:
            rendered = ", ".join(
                type(error).__name__ for error in self._protocol_errors
            )
            raise RuntimeError(
                f"SQL Browser fixture transport reported errors: {rendered}"
            )

    async def __aenter__(self) -> "SqlBrowserFixture":
        return await self.start()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
        await self.close()
