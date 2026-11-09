"""Commands need leva!'s Setup > Wifi > Permissions > Serial menu enabled; without it the
module ignores them."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .entity import LevaEntity

BUTTONS = [("wake", "Wake", "MCa"), ("eco", "Eco", "MCe"), ("standby", "Standby", "MCE")]


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(LevaButton(entry.runtime_data, *b) for b in BUTTONS)


class LevaButton(LevaEntity, ButtonEntity):
    def __init__(self, leva, key: str, name: str, cmd: str) -> None:
        super().__init__(leva, key, name)
        self.cmd = cmd

    async def async_press(self) -> None:
        try:
            self.leva.client.send(self.cmd)
        except ConnectionError as err:
            raise HomeAssistantError(str(err)) from err
