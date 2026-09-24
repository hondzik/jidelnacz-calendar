"""Coordinator pro jidelna.cz: stahuje jídelníček a udržuje historii událostí.

Vyžaduje běžící Home Assistant (importuje `homeassistant.*`) — na rozdíl od
`jidelna_api.py`/`events.py` není testovatelný samostatně.
"""

from __future__ import annotations

import datetime as dt
import logging

import requests

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import jidelna_api
from .const import (
    CONF_DINERS,
    CONF_HESLO,
    CONF_LOGIN,
    CONF_UPDATE_HOUR,
    CONF_UPDATE_MINUTE,
    DEFAULT_UPDATE_HOUR,
    DEFAULT_UPDATE_MINUTE,
    DINER_DISTINGUISH_WEEKS,
    DINER_DURATION_MINUTES,
    DINER_DURATION_MODE,
    DINER_ENABLED,
    DINER_LOCATION,
    DINER_PREFIX,
    DINER_REGC,
    DINER_SCHEDULE,
    DOMAIN,
    STORAGE_VERSION,
)
from .events import DURATION_ALL_DAY, DinerSettings, MealEvent, build_event

_LOGGER = logging.getLogger(__name__)

FETCH_DAYS_BACK = 7
FETCH_DAYS_FORWARD = 14
HISTORY_MAX_AGE_DAYS = 365


class JidelnaCoordinator(DataUpdateCoordinator[dict[str, dict[str, MealEvent]]]):
    """Stahuje jídelníček a počítá události; historie se drží v `Store`.

    Neběží na `update_interval` (relativní interval by časem ujížděl) —
    spouští se v uživatelem zadaný čas přes `async_track_time_change`, stejně
    jako `PreDistribuceCoordinator` v referenční integraci.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self.entry = entry
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}")
        unsub = async_track_time_change(
            hass,
            self._handle_scheduled_run,
            hour=entry.options.get(CONF_UPDATE_HOUR, DEFAULT_UPDATE_HOUR),
            minute=entry.options.get(CONF_UPDATE_MINUTE, DEFAULT_UPDATE_MINUTE),
            second=0,
        )
        entry.async_on_unload(unsub)

    async def _handle_scheduled_run(self, _now: dt.datetime) -> None:
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, dict[str, MealEvent]]:
        fresh = await self.hass.async_add_executor_job(self._fetch_and_build)
        return await self._async_merge_and_store(fresh)

    def _fetch_and_build(self) -> dict[str, dict[str, dict]]:
        """Přihlásí se a stáhne jídelníček pro všechny zapnuté strávníky.

        Strávníci se stejnou jídelnou (`regc`) sdílí jedno stažení dnů —
        stejná session/přihlášení platí pro celý účet.
        """
        session = requests.Session()

        login_id = self.entry.data[CONF_LOGIN]
        heslo = self.entry.data[CONF_HESLO]
        try:
            jidelna_api.login(session, login_id, heslo)
        except jidelna_api.LoginError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except requests.exceptions.RequestException as err:
            raise UpdateFailed(f"nelze se připojit k jidelna.cz: {err}") from err

        od = (dt.date.today() - dt.timedelta(days=FETCH_DAYS_BACK)).isoformat()
        do = (dt.date.today() + dt.timedelta(days=FETCH_DAYS_FORWARD)).isoformat()

        diners_cfg: dict = self.entry.options.get(CONF_DINERS, {})
        days_by_regc: dict[str, list[jidelna_api.Day]] = {}
        result: dict[str, dict[str, dict]] = {}

        for uid, cfg in diners_cfg.items():
            if not cfg.get(DINER_ENABLED):
                continue
            regc = cfg[DINER_REGC]
            if regc not in days_by_regc:
                try:
                    days_by_regc[regc] = jidelna_api.fetch_days_with_relogin(
                        session, login_id, heslo, regc, od, do
                    )
                except jidelna_api.SessionExpired as err:
                    raise UpdateFailed(f"session zůstala neplatná i po re-loginu: {err}") from err
                except requests.exceptions.RequestException as err:
                    raise UpdateFailed(f"nelze stáhnout jídelníček: {err}") from err

            settings = _settings_from_config(cfg)
            events: dict[str, dict] = {}
            for day in days_by_regc[regc]:
                event = build_event(day, uid, settings)
                if event is not None:
                    events[event.uid] = _event_to_dict(event)
            result[uid] = events

        return result

    async def _async_merge_and_store(
        self, fresh: dict[str, dict[str, dict]]
    ) -> dict[str, dict[str, MealEvent]]:
        """Sloučí čerstvě stažené dny s uloženou historií a ořízne staré záznamy.

        Historie mimo aktuálně stahovaný rozsah (`FETCH_DAYS_BACK`/`_FORWARD`)
        se zachovává, aby v kalendáři zůstaly i starší obědy — jinak by
        `Store` nebyl potřeba, stačilo by vracet jen čerstvá data.
        """
        stored = await self._store.async_load() or {}
        events: dict[str, dict[str, dict]] = stored.get("events", {})

        for uid, new_events in fresh.items():
            events.setdefault(uid, {}).update(new_events)

        cutoff = dt.date.today() - dt.timedelta(days=HISTORY_MAX_AGE_DAYS)
        for uid in list(events):
            events[uid] = {k: v for k, v in events[uid].items() if _event_date(v) >= cutoff}

        await self._store.async_save({"events": events})
        return {
            uid: {event_uid: _event_from_dict(event_uid, v) for event_uid, v in evs.items()}
            for uid, evs in events.items()
        }


def _settings_from_config(cfg: dict) -> DinerSettings:
    return DinerSettings(
        prefix=cfg.get(DINER_PREFIX, ""),
        location=cfg.get(DINER_LOCATION, ""),
        duration_mode=cfg.get(DINER_DURATION_MODE, DURATION_ALL_DAY),
        duration_minutes=cfg.get(DINER_DURATION_MINUTES),
        distinguish_weeks=cfg.get(DINER_DISTINGUISH_WEEKS, False),
        schedule=cfg.get(DINER_SCHEDULE, {}),
    )


def _event_to_dict(event: MealEvent) -> dict:
    return {
        "start": event.start.isoformat(),
        "end": event.end.isoformat(),
        "all_day": event.all_day,
        "summary": event.summary,
        "description": event.description,
        "location": event.location,
    }


def _event_from_dict(event_uid: str, data: dict) -> MealEvent:
    all_day = data["all_day"]
    if all_day:
        start = dt.date.fromisoformat(data["start"])
        end = dt.date.fromisoformat(data["end"])
    else:
        start = dt.datetime.fromisoformat(data["start"])
        end = dt.datetime.fromisoformat(data["end"])
    return MealEvent(
        uid=event_uid,
        start=start,
        end=end,
        all_day=all_day,
        summary=data["summary"],
        description=data["description"],
        location=data["location"],
    )


def _event_date(data: dict) -> dt.date:
    return dt.date.fromisoformat(data["start"][:10])
