"""leva! (ito) espresso machine controller."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .client import Client, probe
from .protocol import Display, Field, Frame, fields_from_xml, parse_display

_LOG = logging.getLogger(__name__)
DOMAIN = "leva"
CONF_FIRMWARE = "firmware"  # XML name the firmware reported last (entry data)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]
EVENT_BREW_FINISHED = "leva_brew_finished"
DISPLAY_STALE_S = 10  # display entities go unavailable this long after the last display line
ALERT_HOLD_S = 10     # alerts blink on the OLED: keep one active this long after it was last seen
END_JITTER_S = 2      # program end moves only when the countdown disagrees by more than this
MIN_SHOT_S = 12       # shorter pump runs are flushes, purges or warm-up pulses (one is ~10.3 s)


@dataclass(frozen=True)
class Shot:
    """Summary of the last pump run that counted as a shot (see Leva._track_brew)."""

    end: datetime
    duration: float  # s
    volume: float | None  # mL of water through the flow meter
    flow_rate: float | None  # mL/s, volume / duration
    boiler_temperature: float | None  # °C, average while brewing
    weight: float | None  # g, scale reading when the pump stopped
    peak_pressure: float | None  # bar
    profile: str | None


def load_fields(path: str) -> list[Field] | None:
    """Fields from the Status Monitor XML the firmware names, None if it isn't installed."""
    if not os.path.isfile(path):
        return None
    with open(path, encoding="latin-1") as f:
        return fields_from_xml(f.read())


class Leva:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, fields: list[Field], xml: str) -> None:
        self.hass, self.entry, self.fields, self.xml = hass, entry, fields, xml
        self.signal = f"{DOMAIN}_{entry.entry_id}"
        self.display_signal = f"{self.signal}_display"
        self.shot_signal = f"{self.signal}_shot"
        self.frame: Frame | None = None
        self.available = False
        self._brew: dict | None = None
        self.last_shot: Shot | None = None  # since setup; the sensors restore older ones
        # From the OLED (display peeks): None while no display line arrived lately.
        self.display: Display | None = None
        self.program_end: datetime | None = None
        self.alerts: list[str] = []  # active, most recently seen first
        self._alert_seen: dict[str, float] = {}
        self._display_gone: CALLBACK_TYPE | None = None
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title,
            manufacturer="Software & Circuits", model="ito / leva!", sw_version=xml)
        self.client = Client(entry.data[CONF_HOST], entry.data.get(CONF_PORT, 23), xml, fields,
                             self._on_frame, self._on_stale, self._on_xml, self._on_display)

    @callback
    def _on_display(self, line: str) -> None:
        if (d := parse_display(line)) is None:
            return
        now = time.monotonic()
        for a in d.alerts:
            self._alert_seen[a] = now
        self._alert_seen = {a: t for a, t in self._alert_seen.items() if now - t < ALERT_HOLD_S}
        alerts = sorted(self._alert_seen, key=self._alert_seen.__getitem__, reverse=True)
        end = None if d.time_left is None else dt_util.utcnow() + timedelta(seconds=d.time_left)
        if end and self.program_end and abs((end - self.program_end).total_seconds()) <= END_JITTER_S:
            end = self.program_end
        changed = (d, alerts, end) != (self.display, self.alerts, self.program_end)
        self.display, self.alerts, self.program_end = d, alerts, end
        if self._display_gone:
            self._display_gone()
        self._display_gone = async_call_later(self.hass, DISPLAY_STALE_S, self._display_lost)
        if changed:
            async_dispatcher_send(self.hass, self.display_signal)

    @callback
    def _display_lost(self, _now=None) -> None:
        self._display_gone = None
        self.display, self.program_end, self.alerts, self._alert_seen = None, None, [], {}
        async_dispatcher_send(self.hass, self.display_signal)

    def shutdown(self) -> None:
        if self._display_gone:
            self._display_gone()
            self._display_gone = None

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
        _LOG.info("Firmware changed (%s -> %s), reloading", self.xml, name)
        self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, CONF_FIRMWARE: name})
        self.hass.config_entries.async_schedule_reload(self.entry.entry_id)

    def _track_brew(self, fr: Frame) -> None:
        """Fire an event when the pump / brew program stops, for shots, flushes and warm-up
        pulses alike; `shot` tells them apart. A shot also becomes `last_shot`."""
        v = fr.values
        if fr.brewing:
            if (b := self._brew) is None:
                b = self._brew = {"start": time.monotonic(), "peak": None, "temps": [], "program": None}
            if (p := v.get("pressure")) is not None:
                b["peak"] = max(b["peak"] or 0.0, p)
            if (t := v.get("temperature_pid1")) is not None:
                b["temps"].append(t)
            # Pump runs inside a run-menu program (warm-up pulses, flush, descale) are no shots.
            b["program"] = b["program"] or (self.display.program if self.display else None)
        elif self._brew:
            b, self._brew = self._brew, None
            duration = round(time.monotonic() - b["start"], 1)
            volume = v.get("volume")
            shot = duration >= MIN_SHOT_S and not b["program"]
            self.hass.bus.async_fire(EVENT_BREW_FINISHED, {
                "entry_id": self.entry.entry_id,
                "duration": duration,
                "weight": v.get("weight"),
                "volume": volume,
                "peak_pressure": b["peak"] or 0.0,
                "profile": v.get("selected_profile"),
                "shot": shot,
                "program": b["program"].lower() if b["program"] else None,
            })
            if shot:
                temps = b["temps"]
                self.last_shot = Shot(
                    end=dt_util.utcnow(), duration=duration, volume=volume,
                    flow_rate=round(volume / duration, 2) if volume is not None else None,
                    boiler_temperature=round(sum(temps) / len(temps), 1) if temps else None,
                    weight=v.get("weight"), peak_pressure=b["peak"],
                    profile=v.get("selected_profile"))
                async_dispatcher_send(self.hass, self.shot_signal)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host, port = entry.data[CONF_HOST], entry.data.get(CONF_PORT, 23)
    # The machine is often switched off: set up from the remembered firmware name and let
    # the client connect whenever it appears (it re-checks the name on every connection).
    if (xml := entry.data.get(CONF_FIRMWARE)) is None:  # not seen yet: ask the machine once
        try:
            xml = await probe(host, port)
        except (OSError, TimeoutError) as err:
            raise ConfigEntryNotReady(f"ito module {host}:{port} not reachable: {err}") from err
        if xml is None:
            raise ConfigEntryNotReady(f"ito module {host}:{port} did not report its firmware (MC?)")
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_FIRMWARE: xml})
    path = hass.config.path(DOMAIN, f"{xml}.xml")
    if (fields := await hass.async_add_executor_job(load_fields, path)) is None:
        # Settings > Repairs entry whose fix flow takes the upload (repairs.py).
        ir.async_create_issue(hass, DOMAIN, entry.entry_id, is_fixable=True, severity=ir.IssueSeverity.ERROR,
                              translation_key="missing_xml", translation_placeholders={"name": xml},
                              data={"entry_id": entry.entry_id, "name": xml})
        raise ConfigEntryError(f"Firmware {xml}: upload the {xml}.xml that came with your leva! download "
                               f"via Settings > Repairs or Reconfigure (or copy it to {path})")
    ir.async_delete_issue(hass, DOMAIN, entry.entry_id)
    entry.runtime_data = leva = Leva(hass, entry, fields, xml)
    entry.async_on_unload(leva.shutdown)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, leva.client.run(), f"{DOMAIN} {host}")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, entry.entry_id)
