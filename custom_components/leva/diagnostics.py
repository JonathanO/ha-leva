"""Diagnostics download (device page → Download diagnostics): firmware, live values and the
full setup menu (`MCu`), e.g. PID settings and pressure profile definitions."""
from __future__ import annotations

from dataclasses import asdict

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    leva = entry.runtime_data
    try:
        setup = await leva.client.dump_setup()
    except ConnectionError as err:
        setup = [f"unavailable: {err}"]
    return {
        "entry": async_redact_data(entry.data, {CONF_HOST}),
        "firmware": leva.xml,
        "fields": [asdict(f) for f in leva.fields],
        "frame": asdict(leva.frame) if leva.frame else None,
        "setup": setup,
    }
