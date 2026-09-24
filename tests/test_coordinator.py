"""Testy pro `coordinator.py` — přes `pytest-homeassistant-custom-component`.

`Store` (`async_load`/`async_save`) je v tomhle testovacím frameworku
automaticky mockovaný na `hass_storage` (in-memory dict) — netřeba nic
zvlášť nastavovat, testy jen ověřují výsledný obsah.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
import requests
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jidelna import jidelna_api
from custom_components.jidelna.const import (
    CONF_DINERS,
    CONF_HESLO,
    CONF_LOGIN,
    DINER_DURATION_MODE,
    DINER_ENABLED,
    DINER_REGC,
    DINER_SCHEDULE,
    DOMAIN,
)
from custom_components.jidelna.coordinator import JidelnaCoordinator
from custom_components.jidelna.events import DURATION_ALL_DAY


def _entry(diners: dict) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LOGIN: "2803121", CONF_HESLO: "heslo"},
        options={CONF_DINERS: diners},
    )


def _make_coordinator(hass: HomeAssistant, entry: MockConfigEntry) -> JidelnaCoordinator:
    entry.add_to_hass(hass)
    with patch(
        "custom_components.jidelna.coordinator.async_track_time_change",
        return_value=lambda: None,
    ):
        return JidelnaCoordinator(hass, entry)


def _day(datum: str, jidlo: str = "Guláš") -> jidelna_api.Day:
    return jidelna_api.Day(
        dt.date.fromisoformat(datum),
        [
            jidelna_api.MealPart(
                "Oběd",
                "11:40",
                "14:00",
                objednavky={},
                menu=[
                    jidelna_api.MenuVariant(
                        "1", "1", [jidelna_api.Course("Jídlo", jidlo)]
                    )
                ],
            )
        ],
    )


DINERS_CFG = {
    "3632660": {
        DINER_ENABLED: True,
        DINER_REGC: "28",
        DINER_DURATION_MODE: DURATION_ALL_DAY,
        DINER_SCHEDULE: {},
    },
    "3632661": {
        DINER_ENABLED: False,
        DINER_REGC: "28",
        DINER_DURATION_MODE: DURATION_ALL_DAY,
        DINER_SCHEDULE: {},
    },
}


class TestFetchAndBuild:
    async def test_skips_disabled_diners_and_builds_events(self, hass: HomeAssistant):
        coordinator = _make_coordinator(hass, _entry(DINERS_CFG))
        with (
            patch("custom_components.jidelna.coordinator.jidelna_api.login"),
            patch(
                "custom_components.jidelna.coordinator.jidelna_api.fetch_days_with_relogin",
                return_value=[_day("2026-09-09")],
            ) as mock_fetch,
        ):
            result = coordinator._fetch_and_build()

        assert set(result) == {"3632660"}
        assert mock_fetch.call_count == 1  # jedno stažení pro sdílené regc
        events = result["3632660"]
        assert len(events) == 1
        event = next(iter(events.values()))
        assert event["summary"] == "Guláš"
        assert event["all_day"] is True

    async def test_login_error_raises_auth_failed(self, hass: HomeAssistant):
        coordinator = _make_coordinator(hass, _entry(DINERS_CFG))
        with patch(
            "custom_components.jidelna.coordinator.jidelna_api.login",
            side_effect=jidelna_api.LoginError("bad"),
        ):
            with pytest.raises(ConfigEntryAuthFailed):
                coordinator._fetch_and_build()

    async def test_login_connection_error_raises_update_failed(self, hass: HomeAssistant):
        coordinator = _make_coordinator(hass, _entry(DINERS_CFG))
        with patch(
            "custom_components.jidelna.coordinator.jidelna_api.login",
            side_effect=requests.exceptions.ConnectionError("boom"),
        ):
            with pytest.raises(UpdateFailed):
                coordinator._fetch_and_build()

    async def test_session_expired_after_relogin_raises_update_failed(
        self, hass: HomeAssistant
    ):
        coordinator = _make_coordinator(hass, _entry(DINERS_CFG))
        with (
            patch("custom_components.jidelna.coordinator.jidelna_api.login"),
            patch(
                "custom_components.jidelna.coordinator.jidelna_api.fetch_days_with_relogin",
                side_effect=jidelna_api.SessionExpired("2026-09-09"),
            ),
        ):
            with pytest.raises(UpdateFailed):
                coordinator._fetch_and_build()


class TestMergeAndStore:
    async def test_merges_fresh_with_existing_history(self, hass: HomeAssistant):
        coordinator = _make_coordinator(hass, _entry(DINERS_CFG))
        old_event = {
            "start": "2026-01-05",
            "end": "2026-01-06",
            "all_day": True,
            "summary": "Stará polévka",
            "description": "",
            "location": "",
        }
        await coordinator._store.async_save(
            {"events": {"3632660": {"3632660_2026-01-05": old_event}}}
        )

        fresh = {
            "3632660": {
                "3632660_2026-09-09": {
                    "start": "2026-09-09",
                    "end": "2026-09-10",
                    "all_day": True,
                    "summary": "Guláš",
                    "description": "",
                    "location": "",
                }
            }
        }
        merged = await coordinator._async_merge_and_store(fresh)

        assert set(merged["3632660"]) == {"3632660_2026-01-05", "3632660_2026-09-09"}

    async def test_prunes_events_older_than_one_year(self, hass: HomeAssistant):
        coordinator = _make_coordinator(hass, _entry(DINERS_CFG))
        too_old = (dt.date.today() - dt.timedelta(days=400)).isoformat()
        old_event = {
            "start": too_old,
            "end": too_old,
            "all_day": True,
            "summary": "Dávno",
            "description": "",
            "location": "",
        }
        await coordinator._store.async_save(
            {"events": {"3632660": {f"3632660_{too_old}": old_event}}}
        )

        merged = await coordinator._async_merge_and_store({"3632660": {}})

        assert merged["3632660"] == {}
