"""Diagnostics download (device page → Download diagnostics): firmware, live values and the
full setup menu (`MCu`), e.g. PID settings and pressure profile definitions."""
from __future__ import annotations

from dataclasses import asdict

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST

TRIES = 3


async def _complete_setup(client) -> tuple[list[str], bool, int]:
    """The module's Wi-Fi bridge drops a block of the dump whenever the link stalls (its
    send buffer is ~2.9 KB). Lost blocks only remove lines, so a complete dump is the same
    every time: repeat until two agree, else return the longest as possibly incomplete."""
    dumps: list[list[str]] = []
    for tries in range(1, TRIES + 1):
        dump = await client.dump_setup()
        if dump in dumps:
            return dump, True, tries
        dumps.append(dump)
    return max(dumps, key=len), False, TRIES


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    leva = entry.runtime_data
    try:
        setup, complete, tries = await _complete_setup(leva.client)
    except ConnectionError as err:
        setup, complete, tries = [f"unavailable: {err}"], False, 0
    return {
        "entry": async_redact_data(entry.data, {CONF_HOST}),
        "firmware": leva.xml,
        "fields": [asdict(f) for f in leva.fields],
        "frame": asdict(leva.frame) if leva.frame else None,
        "setup_complete": complete,
        "setup_tries": tries,
        "setup": setup,
    }
