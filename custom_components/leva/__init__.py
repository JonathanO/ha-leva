"""leva! (ito) espresso machine controller."""
from __future__ import annotations

import logging
import os
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .client import Client, probe
from .protocol import BUILTIN, Field, Frame, fields_from_xml

_LOG = logging.getLogger(__name__)
DOMAIN = "leva"
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SELECT]
BUILTIN_NAMES = {"leva!-64EA1111", "leva!-65FCB201"}
EVENT_BREW_FINISHED = "leva_brew_finished"


def load_fields(xml_dir: str, name: str | None) -> list[Field]:
    """Fields from <config>/leva/<name>.xml if installed, else the built-in leva! 3.x table."""
    path = os.path.join(xml_dir, f"{name}.xml") if name else None
    if path and os.path.isfile(path):
        with open(path, encoding="latin-1") as f:
            fields = fields_from_xml(f.read())
        _LOG.info("Using %s (%d fields)", path, len(fields))
        return fields
    if name not in BUILTIN_NAMES:
        _LOG.warning("Firmware reports XML=%s; no %s found, using the built-in leva! 3.x layout. "
                     "Values may be wrong: copy the XML that came with your firmware there.", name, path)
    return BUILTIN


class Leva:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, fields: list[Field], xml: str | None) -> None:
        self.hass, self.entry, self.fields, self.xml = hass, entry, fields, xml
        self.signal = f"{DOMAIN}_{entry.entry_id}"
        self.frame: Frame | None = None
        self.available = False
        self._brew: dict | None = None
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
            manufacturer="Software & Circuits", model="ito / leva!", sw_version=xml)
        self.client = Client(entry.data[CONF_HOST], entry.data.get(CONF_PORT, 23), fields,
                             self._on_frame, self._on_stale, self._on_xml)

    @callback
    def _on_frame(self, fr: Frame) -> None:
        self._track_brew(fr)
        self.frame, self.available = fr, True
        async_dispatcher_send(self.hass, self.signal)

    @callback
    def _on_stale(self) -> None:
        if self.available:
            self.available = False
            async_dispatcher_send(self.hass, self.signal)

    @callback
    def _on_xml(self, name: str) -> None:
        if self.xml and name != self.xml:
            _LOG.info("Firmware changed (%s -> %s), reloading", self.xml, name)
            self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

    def _track_brew(self, fr: Frame) -> None:
        """Fire an event when the pump / brew program stops. Covers shots and flushes alike;
        filter on weight or duration in the automation."""
        if fr.brewing:
            if self._brew is None:
                self._brew = {"start": time.monotonic(), "peak": 0.0}
            self._brew["peak"] = max(self._brew["peak"], fr.values.get("pressure") or 0)
        elif self._brew:
            b, self._brew = self._brew, None
            self.hass.bus.async_fire(EVENT_BREW_FINISHED, {
                "entry_id": self.entry.entry_id,
                "duration": round(time.monotonic() - b["start"], 1),
                "weight": fr.values.get("weight"),
                "volume": fr.values.get("volume"),
                "peak_pressure": b["peak"],
                "profile": fr.values.get("selected_profile"),
            })


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host, port = entry.data[CONF_HOST], entry.data.get(CONF_PORT, 23)
    try:
        xml = await probe(host, port)
    except (OSError, TimeoutError) as err:
        raise ConfigEntryNotReady(f"ito module {host}:{port} not reachable: {err}") from err
    fields = await hass.async_add_executor_job(load_fields, hass.config.path(DOMAIN), xml)
    entry.runtime_data = leva = Leva(hass, entry, fields, xml)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, leva.client.run(), f"{DOMAIN} {host}")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
