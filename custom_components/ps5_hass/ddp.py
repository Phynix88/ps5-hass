"""Sony's device discovery protocol (DDP): is the console on, in rest mode or off?

The console answers a UDP "SRCH" on port 9302 with "HTTP/1.1 200 Ok" when it is
on and "HTTP/1.1 620 Server Standby" in rest mode. No Home Assistant imports.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Literal

SRCH = b"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:00030010\n"

Power = Literal["on", "standby", "off"]


class _Probe(asyncio.DatagramProtocol):
    def __init__(self, answer: asyncio.Future[bytes]) -> None:
        self._answer = answer

    def datagram_received(self, data: bytes, addr: object) -> None:
        if not self._answer.done():
            self._answer.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self._answer.done():
            self._answer.set_exception(exc)


def _parse(data: bytes) -> Power:
    first = data.split(b"\n", 1)[0].strip()
    if first.startswith(b"HTTP/1.1 200"):
        return "on"
    if first.startswith(b"HTTP/1.1 620"):
        return "standby"
    return "off"


async def ddp_status(host: str, port: int = 9302, timeout: float = 2.0) -> Power:
    """Ask the console; never raises. No answer means off or unreachable."""
    loop = asyncio.get_running_loop()
    answer: asyncio.Future[bytes] = loop.create_future()
    transport = None
    try:
        transport, _ = await asyncio.wait_for(
            loop.create_datagram_endpoint(lambda: _Probe(answer), remote_addr=(host, port)),
            timeout,
        )
        transport.sendto(SRCH)
        return _parse(await asyncio.wait_for(answer, timeout))
    except (OSError, asyncio.TimeoutError):
        return "off"
    finally:
        if transport is not None:
            transport.close()
        if not answer.done():
            answer.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            if answer.done() and not answer.cancelled():
                answer.exception()
