"""Config flow a options flow pro jidelna.cz.

Krok 1 (`user`): přihlašovací údaje, ověřené živým loginem přes `jidelna_api`
(běží v executoru — blokující síťové I/O). Zároveň se zjistí všichni
strávníci navázaní na účet (`jidelna_api.login` iteruje celou mapu `ucty`) a
jejich jméno/příjmení (`fetch_diner_name`) a výchozí výdejní čas oběda
(`castiDne[].od`, jen jako návrh do formuláře — nic jiného z profilu se
neukládá, viz `jidelna_api.fetch_diner_name`).

Krok 2 (`diners`): checkboxy, které strávníky synchronizovat, + čas
aktualizace. Nově zapnutí strávníci pak projdou průvodcem `_DinerWizardMixin`
(sdílený s options flow) — kalendář, obsah události, délka/čas oběda podle
`CLAUDE.md`.

Options flow (menu): "diners" (zapnutí/vypnutí + průvodce pro nově zapnuté),
"diner_settings" (úprava už nakonfigurovaného strávníka), "schedule" (čas
aktualizace), "refresh_now" (okamžité spuštění přes službu `jidelna.refresh`).

Pozn.: importy z `homeassistant.*` nejsou testovatelné bez běžícího HA —
logika kroků je psaná podle vzoru `predistribuce-consumption-profiles`, ale
NENÍ ověřená proti reálné instanci (nejde ji tady spustit).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import requests
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult, section
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.util import slugify

from . import jidelna_api
from .const import (
    CALENDAR_NAME,
    CONF_CALENDARS,
    CONF_DINERS,
    CONF_HESLO,
    CONF_LOGIN,
    CONF_UPDATE_HOUR,
    CONF_UPDATE_MINUTE,
    DEFAULT_UPDATE_HOUR,
    DEFAULT_UPDATE_MINUTE,
    DINER_ALLERGENS,
    DINER_CALENDAR_ID,
    DINER_DISTINGUISH_WEEKS,
    DINER_DURATION_MINUTES,
    DINER_DURATION_MODE,
    DINER_ENABLED,
    DINER_LOCATION,
    DINER_NAME,
    DINER_PREFIX,
    DINER_REGC,
    DINER_SCHEDULE,
    DOMAIN,
    SERVICE_REFRESH,
)
from .events import (
    ALLERGENS_HIDDEN,
    ALLERGENS_NAMES,
    ALLERGENS_NUMBERS,
    DURATION_ALL_DAY,
    DURATION_FIXED,
    DURATION_PER_DAY,
    WEEK_BOTH,
    WEEK_EVEN,
    WEEK_ODD,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)

CONF_UPDATE_TIME = "update_time"

DURATION_OPTIONS = [DURATION_ALL_DAY, DURATION_FIXED, DURATION_PER_DAY]

ALLERGENS_OPTIONS = [ALLERGENS_HIDDEN, ALLERGENS_NUMBERS, ALLERGENS_NAMES]

WEEK_LABELS = {WEEK_BOTH: "Sudý/Lichý týden", WEEK_EVEN: "Sudý týden", WEEK_ODD: "Lichý týden"}
WEEKDAY_LABELS = {
    "mon": "Pondělí",
    "tue": "Úterý",
    "wed": "Středa",
    "thu": "Čtvrtek",
    "fri": "Pátek",
}

DEFAULT_OD = "11:30"


def _login_and_describe(login_id: str, heslo: str) -> dict[str, dict]:
    """Přihlásí a vrátí popis strávníků (jméno, jídelna, návrh výdejní doby).

    Volá se přes executor job. Návrh výdejní doby je best-effort — dnešní
    den nemusí mít oběd (víkend, prázdniny), pak se použije `DEFAULT_OD`.
    """
    session = requests.Session()

    diners = jidelna_api.login(session, login_id, heslo)
    today = dt.date.today().isoformat()
    result: dict[str, dict] = {}
    for diner in diners:
        jmeno = jidelna_api.fetch_diner_name(session, diner.uid)
        default_od = DEFAULT_OD
        try:
            for day in jidelna_api.fetch_days(session, diner.regc, today, today):
                for cast in day.casti:
                    if cast.nazev == jidelna_api.CAST_DNE_OBED and cast.od:
                        default_od = cast.od
        except requests.exceptions.RequestException:
            pass  # návrh výdejní doby je jen pro předvyplnění formuláře
        result[diner.uid] = {
            "name": jmeno or diner.uid,
            "regc": diner.regc,
            "default_od": default_od,
        }
    return result


def _login_only(login_id: str, heslo: str) -> None:
    session = requests.Session()
    jidelna_api.login(session, login_id, heslo)


def _split_time(value: str) -> tuple[int, int]:
    hour, minute, *_rest = value.split(":")
    return int(hour), int(minute)


def _hhmm(value: str) -> str:
    """Normalizuje čas selektoru (vždy vrací "HH:MM:SS") na "HH:MM"."""
    hour, minute = _split_time(value)
    return f"{hour:02d}:{minute:02d}"


def _merged_options(entry: config_entries.ConfigEntry, **updates: Any) -> dict[str, Any]:
    merged = dict(entry.options)
    merged.update(updates)
    return merged


def _week_section_schema(duration_mode: str) -> vol.Schema:
    fields: dict[Any, Any] = {}
    for day in WEEKDAYS:
        fields[vol.Optional(f"{day}_from")] = selector.TimeSelector()
        if duration_mode == DURATION_PER_DAY:
            fields[vol.Optional(f"{day}_to")] = selector.TimeSelector()
    return vol.Schema(fields)


class _DinerWizardMixin:
    """Kroky průvodce nastavením jednoho strávníka — sdílené config i options flow.

    Konzumující třída (`JidelnaConfigFlow`/`JidelnaOptionsFlow`) musí
    implementovat `_async_wizard_done()`, které se zavolá po projití všech
    strávníků ve frontě (`self._wizard_queue`).
    """

    hass: Any

    def _init_wizard(
        self,
        queue: list[str],
        diners_meta: dict[str, dict],
        calendars: dict[str, dict],
        existing: dict[str, dict] | None = None,
    ) -> None:
        self._wizard_queue: list[str] = list(queue)
        self._wizard_diners_meta = diners_meta
        self._wizard_calendars: dict[str, dict] = calendars
        self._wizard_existing: dict[str, dict] = existing or {}
        self._wizard_results: dict[str, dict] = {}
        self._draft: dict[str, Any] = {}

    @property
    def _current_uid(self) -> str:
        return self._wizard_queue[0]

    async def async_step_diner_calendar(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if not self._wizard_calendars:
            return await self.async_step_diner_calendar_new()
        return self.async_show_menu(
            step_id="diner_calendar",
            menu_options=["diner_calendar_new", "diner_calendar_pick"],
        )

    async def async_step_diner_calendar_new(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        meta_name = self._wizard_diners_meta[self._current_uid].get("name", self._current_uid)
        if user_input is not None:
            base = slugify(user_input["name"]) or "obedy"
            calendar_id = base
            i = 2
            while calendar_id in self._wizard_calendars:
                calendar_id = f"{base}_{i}"
                i += 1
            self._wizard_calendars[calendar_id] = {CALENDAR_NAME: user_input["name"]}
            self._draft = {DINER_CALENDAR_ID: calendar_id}
            return await self.async_step_diner_content()

        return self.async_show_form(
            step_id="diner_calendar_new",
            data_schema=vol.Schema({vol.Required("name", default=f"Obědy – {meta_name}"): str}),
        )

    async def async_step_diner_calendar_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._draft = {DINER_CALENDAR_ID: user_input[DINER_CALENDAR_ID]}
            return await self.async_step_diner_content()

        options = [
            {"value": cid, "label": cfg.get(CALENDAR_NAME, cid)}
            for cid, cfg in self._wizard_calendars.items()
        ]
        return self.async_show_form(
            step_id="diner_calendar_pick",
            data_schema=vol.Schema(
                {
                    vol.Required(DINER_CALENDAR_ID): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                }
            ),
        )

    async def async_step_diner_content(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._draft[DINER_PREFIX] = user_input.get(DINER_PREFIX, "")
            self._draft[DINER_LOCATION] = user_input.get(DINER_LOCATION, "")
            self._draft[DINER_ALLERGENS] = user_input[DINER_ALLERGENS]
            return await self.async_step_diner_duration()

        current = self._wizard_existing.get(self._current_uid, {})
        schema = vol.Schema(
            {
                vol.Optional(DINER_PREFIX, default=current.get(DINER_PREFIX, "Oběd: ")): str,
                vol.Optional(DINER_LOCATION, default=current.get(DINER_LOCATION, "")): str,
                vol.Required(
                    DINER_ALLERGENS, default=current.get(DINER_ALLERGENS, ALLERGENS_NAMES)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=ALLERGENS_OPTIONS,
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key=DINER_ALLERGENS,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="diner_content", data_schema=schema)

    async def async_step_diner_duration(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._draft[DINER_DURATION_MODE] = user_input[DINER_DURATION_MODE]
            if user_input[DINER_DURATION_MODE] == DURATION_ALL_DAY:
                self._draft[DINER_DISTINGUISH_WEEKS] = False
                self._draft[DINER_SCHEDULE] = {}
                self._draft[DINER_DURATION_MINUTES] = None
                return await self._wizard_finish_diner()
            return await self.async_step_diner_weeks()

        current = self._wizard_existing.get(self._current_uid, {})
        schema = vol.Schema(
            {
                vol.Required(
                    DINER_DURATION_MODE, default=current.get(DINER_DURATION_MODE, DURATION_ALL_DAY)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=DURATION_OPTIONS,
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key=DINER_DURATION_MODE,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="diner_duration", data_schema=schema)

    async def async_step_diner_weeks(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._draft[DINER_DISTINGUISH_WEEKS] = user_input[DINER_DISTINGUISH_WEEKS]
            return await self.async_step_diner_times()

        current = self._wizard_existing.get(self._current_uid, {})
        schema = vol.Schema(
            {
                vol.Required(
                    DINER_DISTINGUISH_WEEKS, default=current.get(DINER_DISTINGUISH_WEEKS, False)
                ): bool,
            }
        )
        return self.async_show_form(step_id="diner_weeks", data_schema=schema)

    async def async_step_diner_times(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        duration_mode = self._draft[DINER_DURATION_MODE]
        distinguish = self._draft[DINER_DISTINGUISH_WEEKS]
        week_keys = (WEEK_EVEN, WEEK_ODD) if distinguish else (WEEK_BOTH,)
        errors: dict[str, str] = {}

        if user_input is not None:
            if duration_mode == DURATION_FIXED:
                self._draft[DINER_DURATION_MINUTES] = user_input["duration_minutes"]

            schedule: dict[str, dict[str, dict[str, str | None]]] = {}
            for week_key in week_keys:
                section_data = user_input[week_key]
                days: dict[str, dict[str, str | None]] = {}
                for day in WEEKDAYS:
                    time_from_raw = section_data.get(f"{day}_from")
                    if not time_from_raw:
                        continue
                    time_from = _hhmm(time_from_raw)
                    item: dict[str, str | None] = {"from": time_from}
                    if duration_mode == DURATION_PER_DAY:
                        time_to_raw = section_data.get(f"{day}_to")
                        time_to = _hhmm(time_to_raw) if time_to_raw else None
                        if time_to and time_to <= time_from:
                            errors["base"] = "time_to_before_from"
                        item["to"] = time_to
                    days[day] = item
                schedule[week_key] = days

            if not errors:
                self._draft[DINER_SCHEDULE] = schedule
                if duration_mode == DURATION_FIXED:
                    return await self.async_step_diner_times_confirm()
                return await self._wizard_finish_diner()

        schema_dict: dict[Any, Any] = {}
        if duration_mode == DURATION_FIXED:
            schema_dict[vol.Required("duration_minutes")] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5, max=240, step=5, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX
                )
            )
        for week_key in week_keys:
            schema_dict[vol.Required(week_key)] = section(
                _week_section_schema(duration_mode), {"collapsed": False}
            )
        schema = vol.Schema(schema_dict)

        existing = self._wizard_existing.get(self._current_uid, {})
        suggested: dict[str, Any] = {}
        if duration_mode == DURATION_FIXED:
            suggested["duration_minutes"] = existing.get(DINER_DURATION_MINUTES, 30)
        existing_schedule = existing.get(DINER_SCHEDULE, {})
        default_od = self._wizard_diners_meta.get(self._current_uid, {}).get("default_od", DEFAULT_OD)
        for week_key in week_keys:
            week_days = existing_schedule.get(week_key, {})
            section_suggested: dict[str, str] = {}
            for day in WEEKDAYS:
                day_cfg = week_days.get(day)
                if day_cfg:
                    section_suggested[f"{day}_from"] = day_cfg.get("from", "")
                    if duration_mode == DURATION_PER_DAY and day_cfg.get("to"):
                        section_suggested[f"{day}_to"] = day_cfg["to"]
                else:
                    section_suggested[f"{day}_from"] = default_od
            suggested[week_key] = section_suggested

        return self.async_show_form(
            step_id="diner_times",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def async_step_diner_times_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return await self._wizard_finish_diner()

        duration = self._draft[DINER_DURATION_MINUTES] or 0
        lines = []
        for week_key, days in self._draft[DINER_SCHEDULE].items():
            for day, times in days.items():
                start = dt.datetime.strptime(times["from"], "%H:%M")
                end = start + dt.timedelta(minutes=duration)
                lines.append(
                    f"- {WEEKDAY_LABELS[day]} ({WEEK_LABELS[week_key]}): "
                    f"{times['from']}–{end.strftime('%H:%M')}"
                )
        return self.async_show_form(
            step_id="diner_times_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"prehled": "\n".join(lines) or "—"},
        )

    async def _wizard_finish_diner(self) -> FlowResult:
        uid = self._wizard_queue.pop(0)
        meta = self._wizard_diners_meta[uid]
        self._draft[DINER_NAME] = meta.get("name", uid)
        self._draft[DINER_REGC] = meta["regc"]
        self._draft[DINER_ENABLED] = True
        self._wizard_results[uid] = self._draft
        self._draft = {}
        if self._wizard_queue:
            return await self.async_step_diner_calendar()
        return await self._async_wizard_done()

    async def _async_wizard_done(self) -> FlowResult:
        raise NotImplementedError


class JidelnaConfigFlow(_DinerWizardMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Nastavení integrace: přihlášení -> výběr strávníků -> průvodce každým z nich."""

    VERSION = 1

    def __init__(self) -> None:
        self._login: str | None = None
        self._heslo: str | None = None
        self._diners_meta: dict[str, dict] = {}
        self._update_hour = DEFAULT_UPDATE_HOUR
        self._update_minute = DEFAULT_UPDATE_MINUTE

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._login = user_input[CONF_LOGIN]
            self._heslo = user_input[CONF_HESLO]
            try:
                self._diners_meta = await self.hass.async_add_executor_job(
                    _login_and_describe, self._login, self._heslo
                )
            except jidelna_api.LoginError:
                errors["base"] = "invalid_auth"
            except requests.exceptions.RequestException:
                errors["base"] = "cannot_connect"
            else:
                if not self._diners_meta:
                    errors["base"] = "no_diners_found"
                else:
                    await self.async_set_unique_id(self._login)
                    self._abort_if_unique_id_configured()
                    return await self.async_step_diners()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOGIN): str,
                    vol.Required(CONF_HESLO): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_diners(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = user_input["diners"]
            if not selected:
                errors["base"] = "no_diners_selected"
            else:
                self._update_hour, self._update_minute = _split_time(user_input[CONF_UPDATE_TIME])
                self._init_wizard(queue=list(selected), diners_meta=self._diners_meta, calendars={})
                return await self.async_step_diner_calendar()

        options = [
            {"value": uid, "label": f"{meta['name']} ({uid})"}
            for uid, meta in self._diners_meta.items()
        ]
        return self.async_show_form(
            step_id="diners",
            data_schema=vol.Schema(
                {
                    vol.Required("diners", default=list(self._diners_meta)): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, multiple=True, mode=selector.SelectSelectorMode.LIST
                        )
                    ),
                    vol.Required(
                        CONF_UPDATE_TIME,
                        default=f"{DEFAULT_UPDATE_HOUR:02d}:{DEFAULT_UPDATE_MINUTE:02d}:00",
                    ): selector.TimeSelector(),
                }
            ),
            errors=errors,
        )

    async def _async_wizard_done(self) -> FlowResult:
        return self.async_create_entry(
            title=f"Jídelna.cz ({self._login})",
            data={CONF_LOGIN: self._login, CONF_HESLO: self._heslo},
            options={
                CONF_UPDATE_HOUR: self._update_hour,
                CONF_UPDATE_MINUTE: self._update_minute,
                CONF_CALENDARS: self._wizard_calendars,
                CONF_DINERS: self._wizard_results,
            },
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    _login_only, reauth_entry.data[CONF_LOGIN], user_input[CONF_HESLO]
                )
            except jidelna_api.LoginError:
                errors["base"] = "invalid_auth"
            except requests.exceptions.RequestException:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry, data={**reauth_entry.data, CONF_HESLO: user_input[CONF_HESLO]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HESLO): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={"login": reauth_entry.data[CONF_LOGIN]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "JidelnaOptionsFlow":
        return JidelnaOptionsFlow()


class JidelnaOptionsFlow(_DinerWizardMixin, config_entries.OptionsFlow):
    """Menu: zapnutí/vypnutí strávníků, úprava nastavení, čas aktualizace, ruční refresh.

    `self.config_entry` se NEnastavuje v `__init__` — novější HA dodává
    `config_entry` automaticky přes base třídu; explicitní nastavení by
    shodilo options flow s 500 Internal Server Error při otevření (poučení
    z `predistribuce-consumption-profiles`).
    """

    def __init__(self) -> None:
        self._diners_meta: dict[str, dict] | None = None
        self._pending_diners_update: dict[str, dict] | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["diners", "diner_settings", "schedule", "refresh_now"],
        )

    async def _async_load_diners_meta(self) -> dict[str, dict] | None:
        if self._diners_meta is None:
            try:
                self._diners_meta = await self.hass.async_add_executor_job(
                    _login_and_describe,
                    self.config_entry.data[CONF_LOGIN],
                    self.config_entry.data[CONF_HESLO],
                )
            except (jidelna_api.LoginError, requests.exceptions.RequestException):
                return None
        return self._diners_meta

    async def async_step_diners(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        diners_meta = await self._async_load_diners_meta()
        if diners_meta is None:
            return self.async_abort(reason="cannot_connect")

        current: dict[str, dict] = self.config_entry.options.get(CONF_DINERS, {})
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = set(user_input["diners"])
            newly_enabled = [uid for uid in selected if not current.get(uid, {}).get(DINER_ENABLED)]

            merged_diners = dict(current)
            for uid in diners_meta:
                cfg = dict(merged_diners.get(uid, {}))
                cfg[DINER_ENABLED] = uid in selected
                merged_diners[uid] = cfg
            self._pending_diners_update = merged_diners

            if newly_enabled:
                self._init_wizard(
                    queue=newly_enabled,
                    diners_meta=diners_meta,
                    calendars=dict(self.config_entry.options.get(CONF_CALENDARS, {})),
                    existing=current,
                )
                return await self.async_step_diner_calendar()

            return self.async_create_entry(
                data=_merged_options(self.config_entry, **{CONF_DINERS: merged_diners})
            )

        options = [{"value": uid, "label": meta["name"]} for uid, meta in diners_meta.items()]
        return self.async_show_form(
            step_id="diners",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "diners",
                        default=[uid for uid, cfg in current.items() if cfg.get(DINER_ENABLED)],
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, multiple=True, mode=selector.SelectSelectorMode.LIST
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_diner_settings(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        current: dict[str, dict] = self.config_entry.options.get(CONF_DINERS, {})
        enabled = {uid: cfg for uid, cfg in current.items() if cfg.get(DINER_ENABLED)}
        if not enabled:
            return self.async_abort(reason="no_diners_configured")

        if user_input is not None:
            uid = user_input["diner"]
            diners_meta = {
                u: {"name": c.get(DINER_NAME, u), "regc": c[DINER_REGC], "default_od": DEFAULT_OD}
                for u, c in enabled.items()
            }
            self._init_wizard(
                queue=[uid],
                diners_meta=diners_meta,
                calendars=dict(self.config_entry.options.get(CONF_CALENDARS, {})),
                existing=current,
            )
            return await self.async_step_diner_calendar()

        options = [{"value": uid, "label": cfg.get(DINER_NAME, uid)} for uid, cfg in enabled.items()]
        return self.async_show_form(
            step_id="diner_settings",
            data_schema=vol.Schema(
                {
                    vol.Required("diner"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    ),
                }
            ),
        )

    async def async_step_schedule(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            hour, minute = _split_time(user_input[CONF_UPDATE_TIME])
            return self.async_create_entry(
                data=_merged_options(
                    self.config_entry, **{CONF_UPDATE_HOUR: hour, CONF_UPDATE_MINUTE: minute}
                )
            )

        current_hour = self.config_entry.options.get(CONF_UPDATE_HOUR, DEFAULT_UPDATE_HOUR)
        current_minute = self.config_entry.options.get(CONF_UPDATE_MINUTE, DEFAULT_UPDATE_MINUTE)
        return self.async_show_form(
            step_id="schedule",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_UPDATE_TIME, default=f"{current_hour:02d}:{current_minute:02d}:00"
                    ): selector.TimeSelector(),
                }
            ),
        )

    async def async_step_refresh_now(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self.hass.services.async_call(
                    DOMAIN,
                    SERVICE_REFRESH,
                    {"config_entry_id": self.config_entry.entry_id},
                    blocking=True,
                )
            except HomeAssistantError:
                errors["base"] = "refresh_failed"
            else:
                return self.async_abort(reason="refresh_done")

        return self.async_show_form(step_id="refresh_now", data_schema=vol.Schema({}), errors=errors)

    async def _async_wizard_done(self) -> FlowResult:
        diners = self._pending_diners_update or dict(self.config_entry.options.get(CONF_DINERS, {}))
        diners.update(self._wizard_results)
        return self.async_create_entry(
            data=_merged_options(
                self.config_entry, **{CONF_DINERS: diners, CONF_CALENDARS: self._wizard_calendars}
            )
        )
