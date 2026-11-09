from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import voluptuous as vol
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.selector import FileSelector, FileSelectorConfig

from . import DOMAIN
from .client import probe
from .protocol import fields_from_xml

XML = FileSelector(FileSelectorConfig(accept=".xml,text/xml,application/xml"))


def _save_xml(hass, file_id: str) -> None:
    """Store an uploaded Status Monitor XML as config/leva/<its name>. The integration only
    loads the file whose name the firmware reports, so a wrong file is simply never used."""
    with process_uploaded_file(hass, file_id) as path:
        name, text = path.name, path.read_text("latin-1")
    if not name.endswith(".xml"):
        raise ValueError
    try:
        if not fields_from_xml(text):
            raise ValueError
    except ET.ParseError as err:
        raise ValueError from err
    os.makedirs(hass.config.path(DOMAIN), exist_ok=True)
    with open(hass.config.path(DOMAIN, name), "w", encoding="latin-1") as f:
        f.write(text)


async def upload(hass, user_input: dict, errors: dict) -> bool:
    """Save user_input["xml"] (and remove it from user_input); on failure set the form error."""
    try:
        await hass.async_add_executor_job(_save_xml, hass, user_input.pop("xml"))
    except ValueError:
        errors["xml"] = "invalid_xml"
        return False
    return True


class LevaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._async_abort_entries_match({CONF_HOST: user_input[CONF_HOST]})
            try:
                await probe(user_input[CONF_HOST], user_input[CONF_PORT])
            except (OSError, TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                if await upload(self.hass, user_input, errors):
                    return self.async_create_entry(title="Espresso machine", data=user_input)
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=23): int,
            vol.Required("xml"): XML,
        }))

    async def async_step_reconfigure(self, user_input=None):
        """Upload the XML for new firmware."""
        errors = {}
        if user_input is not None and await upload(self.hass, user_input, errors):
            return self.async_update_reload_and_abort(self._get_reconfigure_entry())
        return self.async_show_form(step_id="reconfigure", errors=errors,
                                    data_schema=vol.Schema({vol.Required("xml"): XML}))
