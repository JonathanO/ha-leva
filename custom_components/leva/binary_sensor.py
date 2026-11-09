from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import LevaEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([Brewing(entry.runtime_data)])


class Brewing(LevaEntity, BinarySensorEntity):
    """`*` in the frame: pump or brew program running (shots, flushes, water through the wand)."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, leva) -> None:
        super().__init__(leva, "brewing", "Brewing")

    @property
    def is_on(self) -> bool | None:
        return self.leva.frame.brewing if self.leva.frame else None
