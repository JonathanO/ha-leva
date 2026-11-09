"""Pressure profile. Selecting needs the Serial menu permission (see button.py)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity

from .entity import LevaEntity

SLOTS = ["profile_1", "profile_2", "profile_3", "profile_4"]


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([Profile(entry.runtime_data)])


class Profile(LevaEntity, SelectEntity):
    def __init__(self, leva) -> None:
        super().__init__(leva, "profile", "Profile")

    @property
    def options(self) -> list[str]:
        return [n for k in SLOTS if (n := self.values.get(k))]

    @property
    def available(self) -> bool:
        return super().available and bool(self.options)

    @property
    def current_option(self) -> str | None:
        cur = self.values.get("selected_profile")
        return cur if cur in self.options else None

    async def async_select_option(self, option: str) -> None:
        slot = next(i for i, k in enumerate(SLOTS, 1) if self.values.get(k) == option)
        # ponytail: assumes frame slot n == the firmware's "PROFILE n" function; check on hardware.
        self.send(f"MCcPROFILE {slot}")
