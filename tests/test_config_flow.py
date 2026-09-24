"""Testy pro `config_flow.py` — přes `pytest-homeassistant-custom-component`.

Síťové volání (`_login_and_describe`/`_login_only`) je vždy mockované na úrovni
modulu `custom_components.jidelna.config_flow` — samotný `jidelna_api.py` má
vlastní testy bez HA (`test_jidelna_api.py`).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jidelna import jidelna_api
from custom_components.jidelna.const import (
    CONF_CALENDARS,
    CONF_DINERS,
    CONF_HESLO,
    CONF_LOGIN,
    CONF_UPDATE_HOUR,
    DINER_CALENDAR_ID,
    DINER_DURATION_MODE,
    DINER_ENABLED,
    DINER_SCHEDULE,
    DOMAIN,
)
from custom_components.jidelna.events import DURATION_ALL_DAY, DURATION_FIXED

DINERS_META = {
    "3632660": {"name": "Anna Nováková", "regc": "28", "default_od": "11:40"},
}

DINERS_META_TWO = {
    "3632660": {"name": "Anna Nováková", "regc": "28", "default_od": "11:40"},
    "3632661": {"name": "Petr Novák", "regc": "28", "default_od": "12:00"},
}


async def _complete_all_day_diner_wizard(hass: HomeAssistant, flow_id: str, manager=None) -> dict:
    manager = manager or hass.config_entries.flow
    result = await manager.async_configure(flow_id, {"name": "Obědy – Anna"})
    assert result["step_id"] == "diner_content"
    result = await manager.async_configure(flow_id, {})
    assert result["step_id"] == "diner_duration"
    result = await manager.async_configure(
        flow_id, {DINER_DURATION_MODE: DURATION_ALL_DAY, "distinguish_weeks": False}
    )
    return result


class TestUserStep:
    async def test_success_flow_creates_entry(self, hass: HomeAssistant):
        with patch(
            "custom_components.jidelna.config_flow._login_and_describe",
            return_value=DINERS_META,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_LOGIN: "2803121", CONF_HESLO: "heslo"}
            )
            assert result["step_id"] == "diners"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"diners": ["3632660"], "update_time": "05:00:00"},
            )
            # Žádný kalendář ještě neexistuje -> menu se přeskočí, rovnou formulář "nová entita".
            assert result["type"] == "form"
            assert result["step_id"] == "diner_calendar_new"

            result = await _complete_all_day_diner_wizard(hass, result["flow_id"])

        assert result["type"] == "create_entry"
        assert result["data"] == {CONF_LOGIN: "2803121", CONF_HESLO: "heslo"}
        diners = result["options"][CONF_DINERS]
        assert diners["3632660"][DINER_ENABLED] is True
        assert diners["3632660"][DINER_DURATION_MODE] == DURATION_ALL_DAY
        assert result["options"][CONF_UPDATE_HOUR] == 5
        assert len(result["options"][CONF_CALENDARS]) == 1

    async def test_invalid_auth_shows_error(self, hass: HomeAssistant):
        with patch(
            "custom_components.jidelna.config_flow._login_and_describe",
            side_effect=jidelna_api.LoginError("login_failed"),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_LOGIN: "2803121", CONF_HESLO: "spatne"}
            )
        assert result["errors"] == {"base": "invalid_auth"}

    async def test_no_diners_found_shows_error(self, hass: HomeAssistant):
        with patch(
            "custom_components.jidelna.config_flow._login_and_describe", return_value={}
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_LOGIN: "2803121", CONF_HESLO: "heslo"}
            )
        assert result["errors"] == {"base": "no_diners_found"}

    async def test_duplicate_account_aborts(self, hass: HomeAssistant):
        MockConfigEntry(domain=DOMAIN, unique_id="2803121", data={}).add_to_hass(hass)
        with patch(
            "custom_components.jidelna.config_flow._login_and_describe",
            return_value=DINERS_META,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_LOGIN: "2803121", CONF_HESLO: "heslo"}
            )
        assert result["type"] == "abort"
        assert result["reason"] == "already_configured"


class TestDinersStep:
    async def test_no_diners_selected_shows_error(self, hass: HomeAssistant):
        with patch(
            "custom_components.jidelna.config_flow._login_and_describe",
            return_value=DINERS_META,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_LOGIN: "2803121", CONF_HESLO: "heslo"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"diners": [], "update_time": "05:00:00"}
            )
        assert result["errors"] == {"base": "no_diners_selected"}


class TestFixedDurationWizard:
    async def test_fixed_duration_goes_through_times_and_confirm(self, hass: HomeAssistant):
        with patch(
            "custom_components.jidelna.config_flow._login_and_describe",
            return_value=DINERS_META,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_LOGIN: "2803121", CONF_HESLO: "heslo"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"diners": ["3632660"], "update_time": "05:00:00"}
            )
            # Žádný kalendář ještě neexistuje -> menu se přeskočí, rovnou formulář "nová entita".
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"name": "Obědy – Anna"}
            )
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {DINER_DURATION_MODE: DURATION_FIXED, "distinguish_weeks": False},
            )
            assert result["step_id"] == "diner_times"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    "duration_minutes": 30,
                    "both": {"mon_from": "11:40", "wed_from": "11:40"},
                },
            )
            assert result["step_id"] == "diner_times_confirm"
            assert "11:40" in result["description_placeholders"]["prehled"]
            assert "12:10" in result["description_placeholders"]["prehled"]

            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

        assert result["type"] == "create_entry"
        schedule = result["options"][CONF_DINERS]["3632660"][DINER_SCHEDULE]
        assert schedule["both"]["mon"] == {"from": "11:40"}


class TestSharedCalendar:
    async def test_second_diner_can_pick_existing_calendar(self, hass: HomeAssistant):
        with patch(
            "custom_components.jidelna.config_flow._login_and_describe",
            return_value=DINERS_META_TWO,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_LOGIN: "2803121", CONF_HESLO: "heslo"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"diners": ["3632660", "3632661"], "update_time": "05:00:00"},
            )
            # Žádný kalendář ještě neexistuje -> menu se přeskočí, rovnou formulář "nová entita".
            result = await _complete_all_day_diner_wizard(hass, result["flow_id"])
            assert result["type"] == "menu"
            assert result["step_id"] == "diner_calendar"

            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"next_step_id": "diner_calendar_pick"}
            )
            calendar_id = list(result["data_schema"].schema[DINER_CALENDAR_ID].config["options"])[0][
                "value"
            ]
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {DINER_CALENDAR_ID: calendar_id}
            )
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {DINER_DURATION_MODE: DURATION_ALL_DAY, "distinguish_weeks": False}
            )

        assert result["type"] == "create_entry"
        assert len(result["options"][CONF_CALENDARS]) == 1
        diners = result["options"][CONF_DINERS]
        assert diners["3632660"][DINER_CALENDAR_ID] == diners["3632661"][DINER_CALENDAR_ID]


class TestReauth:
    async def test_reauth_success(self, hass: HomeAssistant):
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="2803121",
            data={CONF_LOGIN: "2803121", CONF_HESLO: "stare_heslo"},
            options={},
        )
        entry.add_to_hass(hass)

        with patch("custom_components.jidelna.config_flow._login_only", return_value=None):
            result = await entry.start_reauth_flow(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_HESLO: "nove_heslo"}
            )

        assert result["type"] == "abort"
        assert result["reason"] == "reauth_successful"
        assert entry.data[CONF_HESLO] == "nove_heslo"

    async def test_reauth_invalid_auth_shows_error(self, hass: HomeAssistant):
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="2803121",
            data={CONF_LOGIN: "2803121", CONF_HESLO: "stare_heslo"},
            options={},
        )
        entry.add_to_hass(hass)

        with patch(
            "custom_components.jidelna.config_flow._login_only",
            side_effect=jidelna_api.LoginError("nope"),
        ):
            result = await entry.start_reauth_flow(hass)
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_HESLO: "spatne"}
            )

        assert result["errors"] == {"base": "invalid_auth"}


class TestOptionsFlow:
    def _make_entry(self, hass: HomeAssistant) -> MockConfigEntry:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="2803121",
            data={CONF_LOGIN: "2803121", CONF_HESLO: "heslo"},
            options={
                CONF_UPDATE_HOUR: 5,
                "update_minute": 0,
                CONF_CALENDARS: {"obedy_anna": {"name": "Obědy – Anna"}},
                CONF_DINERS: {
                    "3632660": {
                        "name": "Anna Nováková",
                        "regc": "28",
                        DINER_ENABLED: True,
                        DINER_CALENDAR_ID: "obedy_anna",
                        DINER_DURATION_MODE: DURATION_ALL_DAY,
                        DINER_SCHEDULE: {},
                    }
                },
            },
        )
        entry.add_to_hass(hass)
        return entry

    async def test_init_shows_menu(self, hass: HomeAssistant):
        entry = self._make_entry(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "menu"
        assert set(result["menu_options"]) == {
            "diners",
            "diner_settings",
            "schedule",
            "refresh_now",
        }

    async def test_schedule_updates_time(self, hass: HomeAssistant):
        entry = self._make_entry(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "schedule"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"update_time": "06:30:00"}
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_UPDATE_HOUR] == 6

    async def test_diners_step_no_new_enabled_creates_entry_directly(
        self, hass: HomeAssistant
    ):
        entry = self._make_entry(hass)
        with patch(
            "custom_components.jidelna.config_flow._login_and_describe",
            return_value=DINERS_META,
        ):
            result = await hass.config_entries.options.async_init(entry.entry_id)
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"next_step_id": "diners"}
            )
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"diners": ["3632660"]}
            )
        assert result["type"] == "create_entry"

    async def test_diners_step_newly_enabled_goes_through_wizard(
        self, hass: HomeAssistant
    ):
        entry = self._make_entry(hass)
        with patch(
            "custom_components.jidelna.config_flow._login_and_describe",
            return_value=DINERS_META_TWO,
        ):
            result = await hass.config_entries.options.async_init(entry.entry_id)
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"next_step_id": "diners"}
            )
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"diners": ["3632660", "3632661"]}
            )
            assert result["type"] == "menu"
            assert result["step_id"] == "diner_calendar"
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"next_step_id": "diner_calendar_new"}
            )
            result = await _complete_all_day_diner_wizard(
                hass, result["flow_id"], manager=hass.config_entries.options
            )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_DINERS]["3632661"][DINER_ENABLED] is True

    async def test_diner_settings_no_diners_aborts(self, hass: HomeAssistant):
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="2803121",
            data={CONF_LOGIN: "2803121", CONF_HESLO: "heslo"},
            options={CONF_DINERS: {}},
        )
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "diner_settings"}
        )
        assert result["type"] == "abort"
        assert result["reason"] == "no_diners_configured"

    async def test_refresh_now_calls_service(self, hass: HomeAssistant):
        entry = self._make_entry(hass)
        # `hass.services.async_call` je na instanci read-only (slot) — patchuje se na třídě.
        with patch(
            "homeassistant.core.ServiceRegistry.async_call"
        ) as mock_call:
            result = await hass.config_entries.options.async_init(entry.entry_id)
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"next_step_id": "refresh_now"}
            )
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {}
            )
        assert result["type"] == "abort"
        assert result["reason"] == "refresh_done"
        mock_call.assert_called_once()
