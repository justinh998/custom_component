"""Config flow for DKB integration."""
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
import homeassistant.helpers.config_validation as cv

from .const import CONF_BLZ, CONF_IBAN, DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BLZ): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_IBAN, default=""): cv.string,
    }
)


class DkbFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for DKB integration."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by the user."""
        errors = {}

        if user_input is not None:
            try:
                info = user_input.copy()
                await self.async_set_unique_id(info[CONF_BLZ])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="DKB", data=info)
            except Exception:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )
async_setup_entry = DkbFlowHandler.async_setup_entry