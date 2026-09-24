"""Setup integrace jidelna.cz."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import JidelnaCoordinator
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["calendar"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = JidelnaCoordinator(hass, entry)
    entry.runtime_data = coordinator

    await coordinator.async_config_entry_first_refresh()

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Service je doménový (ne per-entry) — registrace je no-op, pokud už existuje
    # (víc účtů by se jinak přebíjelo).
    async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Změna v options flow (nový strávník, kalendář, rozvrh...) → reload entry."""
    await hass.config_entries.async_reload(entry.entry_id)
