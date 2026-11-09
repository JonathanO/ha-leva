"""Connection to the ito module on TCP 23. No Home Assistant imports.

Behaviour of the module, largely learned from leva2mqtt's captures:
- the ESP8266 bridges port 23 to the ATmega UART; the report mode (`MCr` frames vs `MC@`
  display frames) is global for all connections
- `MCr` sent in the first seconds after power-on is lost (only `$id: leva!` heartbeats come):
  re-send it while no frames arrive
- after a power cut the module never closes the socket: a read timeout is the only sign
- at most 4 connections, each with a 5 min inactivity timeout: always close before reconnecting
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from .protocol import Field, Frame, parse

_LOG = logging.getLogger(__name__)

WATCHDOG_S = 5      # no line at all for this long -> connection is dead
STALE_S = 3         # no frame for this long -> data unavailable
RESEND_S = 5        # re-send MCr this often while no frames arrive
FOREIGN_S = 120     # ...but this rarely while display frames show another client wants them
KEEPALIVE_S = 60
RETRY_S = 10


async def _open(host: str, port: int):
    return await asyncio.wait_for(asyncio.open_connection(host, port), 5)


async def probe(host: str, port: int) -> str | None:
    """Return the XML name the firmware reports for `MC?`. Raises OSError/TimeoutError if
    the module is unreachable; None if it answers but sends no name (e.g. right after boot)."""
    reader, writer = await _open(host, port)
    try:
        writer.write(b"MC?\r\n")
        await writer.drain()
        end = time.monotonic() + 5
        while (left := end - time.monotonic()) > 0:
            try:
                line = await asyncio.wait_for(reader.readline(), left)
            except TimeoutError:
                break
            if not line:
                break
            if line.startswith(b"XML="):
                return line[4:].decode("latin-1").strip()
        return None
    finally:
        writer.close()


class Client:
    def __init__(self, host: str, port: int, fields: list[Field],
                 on_frame: Callable[[Frame], None], on_stale: Callable[[], None],
                 on_xml: Callable[[str], None]) -> None:
        self.host, self.port, self.fields = host, port, fields
        self._on_frame, self._on_stale, self._on_xml = on_frame, on_stale, on_xml
        self._writer: asyncio.StreamWriter | None = None

    def send(self, cmd: str) -> None:
        if self._writer is None:
            raise ConnectionError("not connected to the ito module")
        self._writer.write(cmd.encode("latin-1") + b"\r\n")

    async def run(self) -> None:
        while True:
            try:
                await self._session()
            except (OSError, TimeoutError) as err:
                _LOG.debug("ito %s: %r", self.host, err)
            finally:
                if self._writer:
                    self._writer.close()
                self._writer = None
                self._on_stale()
            await asyncio.sleep(RETRY_S)

    async def _session(self) -> None:
        reader, self._writer = await _open(self.host, self.port)
        now = time.monotonic()
        self.send("MC?")
        self.send("MCr")
        last_mcr = last_tx = now
        last_frame = last_display = 0.0
        while True:
            raw = await asyncio.wait_for(reader.readline(), WATCHDOG_S)
            if not raw:
                raise ConnectionError("closed by module")
            line = raw.decode("latin-1").rstrip("\r\n")
            now = time.monotonic()
            if line.startswith("{"):
                if frame := parse(line, self.fields):
                    last_frame = now
                    self._on_frame(frame)
            elif line.startswith("<"):
                last_display = now
            elif line.startswith("XML="):
                self._on_xml(line[4:].strip())

            if now - last_frame > STALE_S:
                self._on_stale()
                # Display frames = someone (Virtual Display / Companion app) switched the
                # global mode; don't fight them every 5 s.
                wait = FOREIGN_S if now - last_display < 2 else RESEND_S
                if now - last_mcr > wait:
                    self.send("MCr")
                    last_mcr = last_tx = now
            if now - last_tx > KEEPALIVE_S:
                self.send("")
                last_tx = now
