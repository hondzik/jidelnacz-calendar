"""Doménová služba `jidelna.refresh` — ruční okamžitá aktualizace jídelníčku."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, SERVICE_REFRESH

ATTR_CONFIG_ENTRY_ID = "config_entry_id"

SERVICE_REFRESH_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})


def async_register_services(hass: HomeAssistant) -> None:
    """Zaregistruje doménovou službu (no-op, pokud už existuje — víc účtů by se jinak přebíjelo)."""
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def _async_handle_refresh(call: ServiceCall) -> None:
        entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_found"
            )
        if entry.state != ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_loaded"
            )
        await entry.runtime_data.async_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, _async_handle_refresh, schema=SERVICE_REFRESH_SCHEMA
    )
