from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_HOST, CONF_PORT

from . import DOMAIN
from .client import probe


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
                return self.async_create_entry(title="Espresso machine", data=user_input)
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=23): int,
        }))
