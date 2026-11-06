"""Sony's status query (DDP) against a UDP fake on 127.0.0.1."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncGenerator, Callable

import pytest

from custom_components.ps5_hass.ddp import ddp_status

ON = b"HTTP/1.1 200 Ok\nhost-type:PS5\nsystem-version:13600007\n"
STANDBY = b"HTTP/1.1 620 Server Standby\nhost-type:PS5\n"


class _Fake(asyncio.DatagramProtocol):
    def __init__(self, answer: bytes | None) -> None:
        self.answer = answer
        self.queries: list[bytes] = []

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.queries.append(data)
        if self.answer is not None:
            self.transport.sendto(self.answer, addr)


@pytest.fixture
async def udp(socket_enabled: None) -> AsyncGenerator[Callable]:
    servers = []

    async def make(answer: bytes | None) -> tuple[int, _Fake]:
        loop = asyncio.get_running_loop()
        transport, proto = await loop.create_datagram_endpoint(
            lambda: _Fake(answer), local_addr=("127.0.0.1", 0))
        servers.append(transport)
        return transport.get_extra_info("sockname")[1], proto

    yield make
    for t in servers:
        t.close()


async def test_on_200(udp) -> None:
    port, proto = await udp(ON)
    assert await ddp_status("127.0.0.1", port, timeout=1) == "on"
    assert proto.queries[0].startswith(b"SRCH * HTTP/1.1\n")
    assert b"device-discovery-protocol-version:00030010" in proto.queries[0]


async def test_standby_620(udp) -> None:
    port, _ = await udp(STANDBY)
    assert await ddp_status("127.0.0.1", port, timeout=1) == "standby"


async def test_timeout_off(udp) -> None:
    port, _ = await udp(None)
    assert await ddp_status("127.0.0.1", port, timeout=0.3) == "off"


async def test_garbage_off(udp) -> None:
    port, _ = await udp(b"\xff\xfe nonsense")
    assert await ddp_status("127.0.0.1", port, timeout=1) == "off"


async def test_unresolvable_host_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # DNS is disabled in HA tests; a failed lookup raises socket.gaierror.
    loop = asyncio.get_running_loop()

    async def fail(*args, **kwargs):
        raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(loop, "create_datagram_endpoint", fail)
    assert await ddp_status("no-such-host.invalid", 9302, timeout=0.5) == "off"
