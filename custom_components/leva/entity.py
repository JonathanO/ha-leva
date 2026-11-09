from __future__ import annotations

from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import Leva


class LevaEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, leva: Leva, key: str, name: str) -> None:
        self.leva = leva
        self._attr_unique_id = f"{leva.entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = leva.device_info

    @property
    def available(self) -> bool:
        return self.leva.available and self.leva.frame is not None

    @property
    def values(self) -> dict:
        return self.leva.frame.values if self.leva.frame else {}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, self.leva.signal, self.async_write_ha_state))
