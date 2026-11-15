from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass

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


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    leva = entry.runtime_data
    async_add_entities([ModeSensor(leva), ProgramSensor(leva), ProgramEndSensor(leva), AlertSensor(leva),
                        DisplaySensor(leva)] + [LevaSensor(leva, f) for f in leva.fields if f.key not in SKIP])


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
