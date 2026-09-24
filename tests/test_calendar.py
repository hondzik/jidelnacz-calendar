"""Testy pro `calendar.py` — přes `pytest-homeassistant-custom-component`.

`coordinator.data` se nastavuje ručně (bez skutečného stažení) — coordinator
samotný má vlastní testy v `test_coordinator.py`.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jidelna.calendar import JidelnaCalendar, async_setup_entry
from custom_components.jidelna.const import (
    CALENDAR_NAME,
    CONF_CALENDARS,
    CONF_DINERS,
    CONF_HESLO,
    CONF_LOGIN,
    DINER_CALENDAR_ID,
    DINER_ENABLED,
    DINER_NAME,
    DOMAIN,
)
from custom_components.jidelna.coordinator import JidelnaCoordinator
from custom_components.jidelna.events import MealEvent

TZ = dt_util.get_time_zone("Europe/Prague")


def _entry(hass: HomeAssistant, diners: dict, calendars: dict) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LOGIN: "2803121", CONF_HESLO: "heslo"},
        options={CONF_DINERS: diners, CONF_CALENDARS: calendars},
    )
    entry.add_to_hass(hass)
    return entry


def _make_coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> JidelnaCoordinator:
    with patch(
        "custom_components.jidelna.coordinator.async_track_time_change",
        return_value=lambda: None,
    ):
        return JidelnaCoordinator(hass, entry)


def _event(uid: str, day: dt.date, summary: str) -> MealEvent:
    return MealEvent(
        uid=uid,
        start=day,
        end=day + dt.timedelta(days=1),
        all_day=True,
        summary=summary,
        description="",
        location="",
    )


class TestSingleDiner:
    async def test_all_events_and_next_event(self, hass: HomeAssistant):
        diners = {
            "3632660": {DINER_ENABLED: True, DINER_CALENDAR_ID: "obedy_anna", DINER_NAME: "Anna"}
        }
        entry = _entry(hass, diners, {"obedy_anna": {CALENDAR_NAME: "Obědy – Anna"}})
        coordinator = _make_coordinator(hass, entry)

        past = dt.date.today() - dt.timedelta(days=5)
        future = dt.date.today() + dt.timedelta(days=1)
        coordinator.data = {
            "3632660": {
                "a": _event("a", past, "Minulý oběd"),
                "b": _event("b", future, "Budoucí oběd"),
            }
        }

        calendar = JidelnaCalendar(coordinator, entry, "obedy_anna", "Obědy – Anna")
        assert calendar.event.summary == "Budoucí oběd"

    async def test_disabled_diner_is_excluded(self, hass: HomeAssistant):
        diners = {
            "3632660": {DINER_ENABLED: False, DINER_CALENDAR_ID: "obedy_anna", DINER_NAME: "Anna"}
        }
        entry = _entry(hass, diners, {"obedy_anna": {CALENDAR_NAME: "Obědy – Anna"}})
        coordinator = _make_coordinator(hass, entry)
        coordinator.data = {"3632660": {"a": _event("a", dt.date.today(), "X")}}

        calendar = JidelnaCalendar(coordinator, entry, "obedy_anna", "Obědy – Anna")
        assert calendar.event is None


class TestMultiDinerCalendar:
    async def test_summary_prefixed_with_diner_name_when_shared(self, hass: HomeAssistant):
        diners = {
            "3632660": {DINER_ENABLED: True, DINER_CALENDAR_ID: "rodina", DINER_NAME: "Anna"},
            "3632661": {DINER_ENABLED: True, DINER_CALENDAR_ID: "rodina", DINER_NAME: "Petr"},
        }
        entry = _entry(hass, diners, {"rodina": {CALENDAR_NAME: "Rodina"}})
        coordinator = _make_coordinator(hass, entry)
        today = dt.date.today()
        coordinator.data = {
            "3632660": {"a": _event("a", today, "Guláš")},
            "3632661": {"b": _event("b", today, "Rizoto")},
        }

        calendar = JidelnaCalendar(coordinator, entry, "rodina", "Rodina")
        summaries = {e.summary for e in calendar._all_events()}
        assert summaries == {"Guláš (Anna)", "Rizoto (Petr)"}


class TestGetEvents:
    async def test_filters_events_outside_range(self, hass: HomeAssistant):
        diners = {
            "3632660": {DINER_ENABLED: True, DINER_CALENDAR_ID: "obedy_anna", DINER_NAME: "Anna"}
        }
        entry = _entry(hass, diners, {"obedy_anna": {CALENDAR_NAME: "Obědy – Anna"}})
        coordinator = _make_coordinator(hass, entry)
        near = dt.date.today()
        far = dt.date.today() + dt.timedelta(days=30)
        coordinator.data = {
            "3632660": {"a": _event("a", near, "Blízko"), "b": _event("b", far, "Daleko")}
        }
        calendar = JidelnaCalendar(coordinator, entry, "obedy_anna", "Obědy – Anna")

        start = dt_util.start_of_local_day()
        end = start + dt.timedelta(days=7)
        events = await calendar.async_get_events(hass, start, end)
        assert [e.summary for e in events] == ["Blízko"]


class TestSetupEntry:
    async def test_removes_stale_calendar_entities(self, hass: HomeAssistant):
        diners = {
            "3632660": {DINER_ENABLED: True, DINER_CALENDAR_ID: "obedy_anna", DINER_NAME: "Anna"}
        }
        entry = _entry(hass, diners, {"obedy_anna": {CALENDAR_NAME: "Obědy – Anna"}})
        coordinator = _make_coordinator(hass, entry)
        coordinator.data = {}
        entry.runtime_data = coordinator

        registry = er.async_get(hass)
        stale = registry.async_get_or_create(
            "calendar", DOMAIN, f"{entry.entry_id}_smazany_kalendar", config_entry=entry
        )

        added = []
        await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

        assert registry.async_get(stale.entity_id) is None
        assert {e._calendar_id for e in added} == {"obedy_anna"}
