from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import RestoreSensor, SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import Shot
from .entity import LevaDisplayEntity, LevaEntity
from .protocol import Field

DEVICE_CLASS = {"°C": SensorDeviceClass.TEMPERATURE, "bar": SensorDeviceClass.PRESSURE,
                "g": SensorDeviceClass.WEIGHT}
UNIT = {"ml": "mL", "ml/s": "mL/s"}
# Friendly names for the useful fields; these are enabled, the PID internals start disabled.
NAMES = {
    "temperature_pid1": "Boiler temperature",
    "setpoint_pid": "Setpoint",
    "heat_pid": "Heater output",
    "heat_pid_average": "Heater output (1 min average)",
    "pressure": "Pressure",
    "pressure_sp": "Target pressure",
    "weight": "Weight",
    "flow_rate_g_s": "Flow rate (scale)",
    "flow_rate_ml_s": "Flow rate (flow meter)",
    "volume": "Volume",
    "selected_profile": "Profile",
}
SKIP = {"profile_1", "profile_2", "profile_3", "profile_4"}  # slot names; the selected one is enough
MODES = {"PID": "on", "ECO": "eco", "OFF": "off", "CLD": "cold", "ATN": "autotune", None: "standby"}




@dataclass(frozen=True)
class ShotValue:
    key: str
    name: str
    value: Callable[[Shot], object]
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    field: str | None = None  # only when the XML has this field


SHOT_VALUES = (
    ShotValue("last_shot", "Last shot", lambda s: s.end, device_class=SensorDeviceClass.TIMESTAMP),
    ShotValue("last_shot_duration", "Last shot duration", lambda s: s.duration, "s", SensorDeviceClass.DURATION),
    ShotValue("last_shot_volume", "Last shot volume", lambda s: s.volume, "mL", field="volume"),
    ShotValue("last_shot_flow_rate", "Last shot flow rate", lambda s: s.flow_rate, "mL/s", field="volume"),
    ShotValue("last_shot_boiler_temperature", "Last shot boiler temperature", lambda s: s.boiler_temperature,
              "°C", SensorDeviceClass.TEMPERATURE, "temperature_pid1"),
    ShotValue("last_shot_weight", "Last shot weight", lambda s: s.weight, "g", SensorDeviceClass.WEIGHT, "weight"),
    ShotValue("last_shot_peak_pressure", "Last shot peak pressure", lambda s: s.peak_pressure, "bar",
              SensorDeviceClass.PRESSURE, "pressure"),
    ShotValue("last_shot_profile", "Last shot profile", lambda s: s.profile, field="selected_profile"),
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    leva = entry.runtime_data
    keys = {f.key for f in leva.fields}
    async_add_entities([ModeSensor(leva), ProgramSensor(leva), ProgramEndSensor(leva), AlertSensor(leva),
                        DisplaySensor(leva)] + [LevaSensor(leva, f) for f in leva.fields if f.key not in SKIP]
                       + [LastShotSensor(leva, d) for d in SHOT_VALUES if d.field is None or d.field in keys])


class LevaSensor(LevaEntity, SensorEntity):
    def __init__(self, leva, field: Field) -> None:
        super().__init__(leva, field.key, NAMES.get(field.key, field.name))
        self.key = field.key
        self._attr_entity_registry_enabled_default = field.key in NAMES
        self._attr_device_class = DEVICE_CLASS.get(field.unit)
        self._attr_native_unit_of_measurement = UNIT.get(field.unit, field.unit)
        if field.type != "string":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return self.values.get(self.key)


class ModeSensor(LevaEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(MODES.values())

    def __init__(self, leva) -> None:
        super().__init__(leva, "mode", "Mode")

    @property
    def native_value(self):
        return MODES.get(self.leva.frame.mode) if self.leva.frame else None


class ProgramSensor(LevaDisplayEntity, SensorEntity):
    """Title of the program counting down on the OLED (warm-up, flush, ...), `none` otherwise."""

    def __init__(self, leva) -> None:
        super().__init__(leva, "program", "Program")

    @property
    def native_value(self):
        d = self.leva.display
        return (d.program or "none").lower() if d else None


class ProgramEndSensor(LevaDisplayEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, leva) -> None:
        super().__init__(leva, "program_end", "Program end")

    @property
    def native_value(self):
        return self.leva.program_end


class AlertSensor(LevaDisplayEntity, SensorEntity):
    """Runtime alert shown on the OLED (refill, backfl, ...), `none` otherwise. Alerts blink, so
    each stays active for a few seconds after it was last seen; all active ones are listed."""

    _unrecorded_attributes = frozenset({"active"})

    def __init__(self, leva) -> None:
        super().__init__(leva, "alert", "Alert")

    @property
    def native_value(self):
        return self.leva.alerts[0].lower() if self.leva.alerts else "none"

    @property
    def extra_state_attributes(self):
        return {"active": [a.lower() for a in self.leva.alerts]}


class DisplaySensor(LevaDisplayEntity, SensorEntity):
    """OLED contents: title as state, the four lines as (unrecorded) attributes."""

    _attr_entity_registry_enabled_default = False
    _unrecorded_attributes = frozenset({"lines", "backlight"})

    def __init__(self, leva) -> None:
        super().__init__(leva, "display", "Display")

    @property
    def native_value(self):
        d = self.leva.display
        return (d.lines[0].strip() or None) if d else None

    @property
    def extra_state_attributes(self):
        d = self.leva.display
        return {"lines": list(d.lines), "backlight": d.backlight} if d else None


class LastShotSensor(LevaEntity, RestoreSensor):
    """One value of the last shot. Keeps it while the machine is off and across restarts."""

    def __init__(self, leva, desc: ShotValue) -> None:
        super().__init__(leva, desc.key, desc.name)
        self.desc = desc
        self._attr_device_class = desc.device_class
        self._attr_native_unit_of_measurement = desc.unit
        if desc.unit:
            self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = None

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        if self.leva.last_shot:
            self._attr_native_value = self.desc.value(self.leva.last_shot)
        elif (last := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = last.native_value
        self.async_on_remove(async_dispatcher_connect(self.hass, self.leva.shot_signal, self._on_shot))

    @callback
    def _on_shot(self) -> None:
        self._attr_native_value = self.desc.value(self.leva.last_shot)
        self.async_write_ha_state()
