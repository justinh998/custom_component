"""DKB Integration"""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN

LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up DKB integration from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Set up the sensor platform
    sensor_coordinator = await get_sensor_coordinator(hass, entry)
    await sensor_coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN]["sensor_coordinator"] = sensor_coordinator

    # Forward the setup to the sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop("sensor_coordinator")

    return unload_ok

async def get_sensor_coordinator(hass: HomeAssistant, entry: ConfigEntry) -> DataUpdateCoordinator:
    """Get the data update coordinator for the sensor platform."""

    async def async_update_data():
        """Fetch data from API endpoint."""
        try:
            from fints.client import FinTS3PinTanClient
            from fints.utils import minimal_interactive_cli_bootstrap

            config = hass.data[DOMAIN]['config']
            blz = config[CONF_BLZ]
            login = config[CONF_USERNAME]
            pin = config[CONF_PASSWORD]
            iban = config.get(CONF_IBAN)

            # FinTS3PinTanClient initialisieren
            f = FinTS3PinTanClient(
                blz,
                login,
                pin,
                'https://banking-dkb.s-fints-pt-dkb.de/fints30',
                product_id='6151256F3D4F9975B877BD4A2'
            )

            minimal_interactive_cli_bootstrap(f)

            with f:
                if f.init_tan_response:
                    ask_for_tan(f.init_tan_response, f)

                accounts = f.get_sepa_accounts()
                if isinstance(accounts, NeedTANResponse):
                    accounts = ask_for_tan(accounts, f)

                if iban:
                    accounts = [acc for acc in accounts if acc.iban == iban]

                data = {}
                for account in accounts:
                    account_data = {
                        "balance": get_balance(account, f),
                        "balance_with_pending": get_balance_with(account, f),
                        "transactions": get_last_10_transactions(account, f),
                    }
                    data[account.iban] = account_data

                return data

        except Exception as err:
            raise UpdateFailed(f"Error fetching data: {err}")

    return DataUpdateCoordinator(
        hass,
        LOGGER,
        name="DKB sensor",
        update_method=async_update_data,
        update_interval=timedelta(minutes=5),  # Setzen Sie das Intervall auf einen gewünschten Wert
    )

def ask_for_tan(response, f):
    """Funktion zum Abfragen der TAN."""
    print("A TAN is required")
    print(response.challenge)
    tan = input('Please enter TAN:')
    return f.send_tan(response, tan)