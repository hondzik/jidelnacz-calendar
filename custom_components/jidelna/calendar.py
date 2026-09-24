"""Platforma `calendar` — jeden kalendář integrace na `calendar_id`.

Kalendář agreguje všechny zapnuté strávníky přiřazené na daný `calendar_id`
(config/options flow umožňuje přiřadit víc strávníků do jednoho kalendáře,
např. sourozence do společného rodinného kalendáře) — při víc než jednom
strávníkovi se do názvu události doplní jméno.
"""

from __future__ import annotations

import datetime as dt
import logging

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CALENDAR_NAME,
    CONF_CALENDARS,
    CONF_DINERS,
    DINER_CALENDAR_ID,
    DINER_ENABLED,
    DINER_NAME,
)
from .coordinator import JidelnaCoordinator
from .events import TZ_PRAGUE, MealEvent

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: JidelnaCoordinator = entry.runtime_data
    calendars: dict = entry.options.get(CONF_CALENDARS, {})

    # Kalendář, na který už neukazuje žádný strávník (smazaný v options flow),
    # se z entity registry odstraní — jinak by po reloadu zůstal osiřelý.
    registry = er.async_get(hass)
    valid_unique_ids = {f"{entry.entry_id}_{calendar_id}" for calendar_id in calendars}
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.unique_id not in valid_unique_ids:
            registry.async_remove(entity_entry.entity_id)

    async_add_entities(
        JidelnaCalendar(coordinator, entry, calendar_id, cfg.get(CALENDAR_NAME, calendar_id))
        for calendar_id, cfg in calendars.items()
    )


class JidelnaCalendar(CoordinatorEntity[JidelnaCoordinator], CalendarEntity):
    """Read-only kalendář počítaný z dat jidelna.cz."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JidelnaCoordinator,
        entry: ConfigEntry,
        calendar_id: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._calendar_id = calendar_id
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{calendar_id}"

    @property
    def _diners(self) -> dict[str, dict]:
        return {
            uid: cfg
            for uid, cfg in self._entry.options.get(CONF_DINERS, {}).items()
            if cfg.get(DINER_ENABLED) and cfg.get(DINER_CALENDAR_ID) == self._calendar_id
        }

    def _all_events(self) -> list[CalendarEvent]:
        data = self.coordinator.data or {}
        diners = self._diners
        multi = len(diners) > 1
        events = [
            _to_calendar_event(cfg.get(DINER_NAME, uid) if multi else None, event)
            for uid, cfg in diners.items()
            for event in data.get(uid, {}).values()
        ]
        events.sort(key=lambda e: dt_util.as_utc(_as_datetime(e.start)))
        return events

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        upcoming = [e for e in self._all_events() if _as_datetime(e.end) >= now]
        return upcoming[0] if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[CalendarEvent]:
        return [
            e
            for e in self._all_events()
            if _as_datetime(e.end) >= start_date and _as_datetime(e.start) <= end_date
        ]


def _to_calendar_event(diner_name: str | None, event: MealEvent) -> CalendarEvent:
    summary = f"{event.summary} ({diner_name})" if diner_name else event.summary
    return CalendarEvent(
        start=event.start,
        end=event.end,
        summary=summary,
        description=event.description or None,
        location=event.location or None,
        uid=event.uid,
    )


def _as_datetime(value: dt.date | dt.datetime) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime.combine(value, dt.time.min, tzinfo=TZ_PRAGUE)
