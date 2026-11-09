"""Fix flow for the "firmware XML missing" repair issue: upload the file, reload the entry."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow

from .config_flow import XML, upload


class MissingXml(RepairsFlow):
    def __init__(self, entry_id: str, name: str) -> None:
        self.entry_id, self.name = entry_id, name

    async def async_step_init(self, user_input=None):
        return await self.async_step_upload()  # init receives the issue data, not form input

    async def async_step_upload(self, user_input=None):
        errors = {}
        if user_input is not None and await upload(self.hass, user_input, errors):
            self.hass.config_entries.async_schedule_reload(self.entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="upload", errors=errors, description_placeholders={"name": self.name},
                                    data_schema=vol.Schema({vol.Required("xml"): XML}))


async def async_create_fix_flow(hass, issue_id: str, data: dict) -> RepairsFlow:
    return MissingXml(data["entry_id"], data["name"])
