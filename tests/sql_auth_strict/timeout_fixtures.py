from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
import time


class HandshakeBlackhole:
    """Accept TCP connections but never answer the TDS pre-login handshake."""

    def __init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._handlers: set[asyncio.Task[None]] = set()
        self.accepted = asyncio.Event()

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        assert self._server is not None and self._server.sockets
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def open_connections(self) -> int:
        return len(self._writers)

    async def __aenter__(self) -> HandshakeBlackhole:
        self._server = await asyncio.start_server(self._accept, self.host, 0)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback,
    ) -> None:
        del exc_type, exc, traceback
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in tuple(self._writers):
            writer.close()
        for writer in tuple(self._writers):
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()
        if self._handlers:
            await asyncio.gather(*tuple(self._handlers))
        self._writers.clear()
        self._handlers.clear()

    async def _accept(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        handler = asyncio.current_task()
        assert handler is not None
        self._handlers.add(handler)
        self._writers.add(writer)
        self.accepted.set()
        try:
            await reader.read()
        finally:
            self._writers.discard(writer)
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()
            self._handlers.discard(handler)


async def wait_until(
    predicate: Callable[[], Awaitable[bool]],
    *,
    timeout: float = 3.0,
    interval: float = 0.01,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("bounded condition was not reached")
