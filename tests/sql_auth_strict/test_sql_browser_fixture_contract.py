from __future__ import annotations

import asyncio
import socket
import time

import pytest

from sql_auth_strict.sql_browser_fixture import SqlBrowserFixture


@pytest.mark.asyncio
async def test_refused_tcp_mode_produces_an_immediate_connection_refusal() -> None:
    fixture = SqlBrowserFixture(
        host="127.0.0.1",
        tcp_port=1433,
        mode="refused_tcp",
    )

    async with fixture:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setblocking(False)
        started = time.monotonic()
        try:
            with pytest.raises(ConnectionRefusedError):
                await asyncio.wait_for(
                    asyncio.get_running_loop().sock_connect(
                        probe,
                        (fixture.host, fixture.selected_tcp_port),
                    ),
                    timeout=0.5,
                )
        finally:
            probe.close()

        assert time.monotonic() - started < 0.5
