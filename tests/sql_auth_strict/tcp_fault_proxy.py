from __future__ import annotations

import asyncio
from contextlib import suppress


class DownstreamGateProxy:
    """Transparent TCP proxy that can withhold server-to-client bytes."""

    def __init__(self, target_host: str, target_port: int) -> None:
        self._target_host = target_host
        self._target_port = target_port
        self._server: asyncio.AbstractServer | None = None
        self._downstream_gate = asyncio.Event()
        self._downstream_gate.set()
        self._downstream_held = asyncio.Event()
        self._handlers: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._unexpected: list[BaseException] = []
        self._aborting = False
        self.accepted_connections = 0

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("proxy is not started")
        return int(self._server.sockets[0].getsockname()[1])

    async def __aenter__(self) -> DownstreamGateProxy:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("proxy is already started")
        self._server = await asyncio.start_server(
            self._accept,
            host=self.host,
            port=0,
        )

    async def _accept(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        handler = asyncio.current_task()
        if handler is None:
            raise RuntimeError("proxy handler has no asyncio task")
        self._handlers.add(handler)
        self.accepted_connections += 1
        server_writer: asyncio.StreamWriter | None = None

        try:
            server_reader, server_writer = await asyncio.open_connection(
                self._target_host,
                self._target_port,
            )
            self._writers.update((client_writer, server_writer))
            upstream = asyncio.create_task(
                self._relay(client_reader, server_writer, downstream=False)
            )
            downstream = asyncio.create_task(
                self._relay(server_reader, client_writer, downstream=True)
            )
            try:
                await asyncio.gather(upstream, downstream)
            finally:
                upstream.cancel()
                downstream.cancel()
                await asyncio.gather(
                    upstream,
                    downstream,
                    return_exceptions=True,
                )
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as error:
            if not self._aborting:
                self._unexpected.append(error)
        finally:
            for writer in (client_writer, server_writer):
                if writer is None:
                    continue
                self._writers.discard(writer)
                writer.close()
                with suppress(ConnectionError, OSError):
                    await writer.wait_closed()
            self._handlers.discard(handler)

    async def _relay(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        downstream: bool,
    ) -> None:
        while data := await reader.read(64 * 1024):
            if downstream and not self._downstream_gate.is_set():
                self._downstream_held.set()
                await self._downstream_gate.wait()
            writer.write(data)
            await writer.drain()

    def pause_downstream(self) -> None:
        self._downstream_held.clear()
        self._downstream_gate.clear()

    def resume_downstream(self) -> None:
        self._downstream_gate.set()

    async def wait_until_downstream_held(
        self,
        timeout: float = 2.0,
    ) -> None:
        await asyncio.wait_for(self._downstream_held.wait(), timeout)

    async def abort_connections(self) -> None:
        handlers = tuple(self._handlers)
        self._aborting = True
        try:
            for handler in handlers:
                handler.cancel()
            for writer in tuple(self._writers):
                writer.transport.abort()
            if handlers:
                await asyncio.gather(*handlers, return_exceptions=True)
        finally:
            self._aborting = False

    async def close(self) -> None:
        if self._server is not None:
            server = self._server
            self._server = None
            server.close()
            await self.abort_connections()
            await server.wait_closed()
        else:
            await self.abort_connections()
        self.resume_downstream()
        if self._unexpected:
            rendered = " | ".join(
                f"{type(error).__name__}: {error}"
                for error in self._unexpected
            )
            raise AssertionError(f"unexpected proxy failure: {rendered}")
