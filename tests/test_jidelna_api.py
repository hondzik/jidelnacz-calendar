"""Testy pro `jidelna_api.py` — bez Home Assistantu, síť mockovaná přes requests_mock."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest
import requests

# Bare top-level import (ne přes balíček `custom_components.jidelna`) — tak jde
# tenhle test spustit i bez homeassistant nainstalovaného. Cesta se přidává až
# tady, ne přes `pyproject.toml`'s `pythonpath` — viz komentář tamtéž (kolize
# `custom_components/jidelna/calendar.py` se stdlib `calendar`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "jidelna"))

import jidelna_api as api  # noqa: E402

BASE = api.BASE


def _login_response(ucty):
    return {
        "stav": "ok",
        "tag": "login_success",
        "zprava": "Uživatel úspěšně přihlášen",
        "ucet": {"typ": "id", "login": "2803121", "ucty": ucty},
    }


def _diner_raw(uid="3632660", regc="28", konto="-820"):
    return {
        "id": int(uid),
        "regc": regc,
        "login": "2803121",
        "kontoProObjednavani": konto,
    }


def _day_raw(datum="2026-09-09", castiDne=None):
    return {"datum": datum, "den": {"castiDne": castiDne if castiDne is not None else []}}


def _cast_raw(nazev="Oběd", od="11:40", do="14:00", objednavky=None, menu=None, include_objednavky=True):
    cast = {"nazev": nazev, "od": od, "do": do, "menu": menu if menu is not None else []}
    if include_objednavky:
        cast["objednavky"] = objednavky if objednavky is not None else {}
    return cast


def _menu_raw(id_=12222, nazev="1", chody=None):
    return {"id": id_, "nazev": nazev, "chody": chody if chody is not None else []}


def _chod_raw(nazev="Jídlo", jidlo="Vepřová kýta ala bažant", alergeny=None):
    return {"nazev": nazev, "jidlo": jidlo, "alergeny": alergeny if alergeny is not None else []}


class TestLogin:
    def test_login_success_single_diner(self, requests_mock):
        requests_mock.post(
            f"{BASE}/login/jmenoheslo",
            json=_login_response({"3632660": _diner_raw()}),
        )
        session = requests.Session()
        diners = api.login(session, "2803121", "heslo")
        assert len(diners) == 1
        assert diners[0].uid == "3632660"
        assert diners[0].regc == "28"
        assert diners[0].konto == "-820"

    def test_login_iterates_all_diners_not_just_first(self, requests_mock):
        requests_mock.post(
            f"{BASE}/login/jmenoheslo",
            json=_login_response(
                {
                    "3632660": _diner_raw(uid="3632660"),
                    "3632661": _diner_raw(uid="3632661", regc="29"),
                }
            ),
        )
        session = requests.Session()
        diners = api.login(session, "mail@example.cz", "heslo")
        assert {d.uid for d in diners} == {"3632660", "3632661"}

    def test_login_failure_raises_login_error_on_http_403(self, requests_mock):
        """Ověřeno živě 2026-09-24: neplatné přihlášení chodí s HTTP 403, ne 200."""
        requests_mock.post(
            f"{BASE}/login/jmenoheslo",
            status_code=403,
            json={"stav": "chyba", "tag": "invalid_login", "zprava": "Nepodařilo se přihlásit uživatele"},
        )
        session = requests.Session()
        with pytest.raises(api.LoginError):
            api.login(session, "2803121", "spatne")

    def test_login_failure_raises_login_error_on_http_200(self, requests_mock):
        """Dokumentace API dřív odhadovala HTTP 200 i pro chybu — klient to má zvládnout taky."""
        requests_mock.post(
            f"{BASE}/login/jmenoheslo",
            json={"stav": "chyba", "tag": "login_failed", "zprava": "Špatné heslo"},
        )
        session = requests.Session()
        with pytest.raises(api.LoginError):
            api.login(session, "2803121", "spatne")

    def test_login_http_error_propagates(self, requests_mock):
        requests_mock.post(f"{BASE}/login/jmenoheslo", status_code=500)
        session = requests.Session()
        with pytest.raises(requests.RequestException):
            api.login(session, "2803121", "heslo")


class TestFetchDinerName:
    def test_returns_jmeno_prijmeni(self, requests_mock):
        requests_mock.get(
            f"{BASE}/uzivatel/3632660/info",
            json={"uzivatel": {"jmeno": "Anna", "prijmeni": "Nováková"}},
        )
        session = requests.Session()
        assert api.fetch_diner_name(session, "3632660") == "Anna Nováková"

    def test_flat_response_without_uzivatel_wrapper(self, requests_mock):
        requests_mock.get(
            f"{BASE}/uzivatel/3632660/info",
            json={"jmeno": "Petr", "prijmeni": "Svoboda"},
        )
        session = requests.Session()
        assert api.fetch_diner_name(session, "3632660") == "Petr Svoboda"

    def test_missing_name_fields_returns_none(self, requests_mock):
        requests_mock.get(f"{BASE}/uzivatel/3632660/info", json={"uzivatel": {}})
        session = requests.Session()
        assert api.fetch_diner_name(session, "3632660") is None

    def test_only_jmeno_present(self, requests_mock):
        requests_mock.get(f"{BASE}/uzivatel/3632660/info", json={"uzivatel": {"jmeno": "Anna"}})
        session = requests.Session()
        assert api.fetch_diner_name(session, "3632660") == "Anna"


class TestFetchDaysAndSession:
    def test_fetch_days_parses_menu_and_objednavky(self, requests_mock):
        raw = [
            _day_raw(
                castiDne=[
                    _cast_raw(
                        objednavky={"3632660": {"idUzivatele": 3632660, "idMenu": "12222", "stav": "Prihlaseno", "mnozstvi": 1}},
                        menu=[_menu_raw(chody=[_chod_raw(alergeny=["1", "7"])])],
                    )
                ]
            )
        ]
        requests_mock.get(f"{BASE}/zarizeni/28/dny/od/2026-09-09/do/2026-09-09", json=raw)
        session = requests.Session()
        days = api.fetch_days(session, "28", "2026-09-09", "2026-09-09")
        assert len(days) == 1
        assert days[0].datum == dt.date(2026, 9, 9)
        cast = days[0].casti[0]
        assert cast.objednavky == {"3632660": {"idUzivatele": 3632660, "idMenu": "12222", "stav": "Prihlaseno", "mnozstvi": 1}}
        assert cast.menu[0].chody[0].alergeny == ["1", "7"]

    def test_check_session_raises_when_objednavky_missing(self):
        day = api.Day(dt.date(2026, 9, 9), [api.MealPart("Oběd", "11:40", "14:00", objednavky=None)])
        with pytest.raises(api.SessionExpired):
            api.check_session([day])

    def test_check_session_passes_when_objednavky_present_even_if_empty(self):
        day = api.Day(dt.date(2026, 9, 9), [api.MealPart("Oběd", "11:40", "14:00", objednavky={})])
        api.check_session([day])  # nesmí vyhodit

    def test_fetch_days_with_relogin_recovers_from_expired_session(self, requests_mock):
        expired_raw = [_day_raw(castiDne=[_cast_raw(include_objednavky=False)])]
        valid_raw = [_day_raw(castiDne=[_cast_raw(objednavky={})])]
        requests_mock.get(
            f"{BASE}/zarizeni/28/dny/od/2026-09-09/do/2026-09-09",
            [{"json": expired_raw}, {"json": valid_raw}],
        )
        requests_mock.post(f"{BASE}/login/jmenoheslo", json=_login_response({"3632660": _diner_raw()}))
        session = requests.Session()
        days = api.fetch_days_with_relogin(session, "2803121", "heslo", "28", "2026-09-09", "2026-09-09")
        assert days[0].casti[0].objednavky == {}

    def test_fetch_days_with_relogin_raises_if_still_expired_after_relogin(self, requests_mock):
        expired_raw = [_day_raw(castiDne=[_cast_raw(include_objednavky=False)])]
        requests_mock.get(
            f"{BASE}/zarizeni/28/dny/od/2026-09-09/do/2026-09-09",
            json=expired_raw,
        )
        requests_mock.post(f"{BASE}/login/jmenoheslo", json=_login_response({"3632660": _diner_raw()}))
        session = requests.Session()
        with pytest.raises(api.SessionExpired):
            api.fetch_days_with_relogin(session, "2803121", "heslo", "28", "2026-09-09", "2026-09-09")


class TestSelectMeal:
    def test_vybrano_matches_by_id_menu_despite_type_mismatch(self):
        day = api.Day(
            dt.date(2026, 9, 9),
            [
                api.MealPart(
                    "Oběd",
                    "11:40",
                    "14:00",
                    objednavky={"3632660": {"idMenu": "12222", "stav": "Prihlaseno", "mnozstvi": 1}},
                    menu=[api.MenuVariant("12222", "1", [api.Course("Jídlo", "Guláš")])],
                )
            ],
        )
        selection = api.select_meal(day, "3632660")
        assert selection.stav == api.STAV_VYBRANO
        assert selection.varianta.chody[0].jidlo == "Guláš"

    def test_vychozi_when_no_objednavka_uses_variant_1(self):
        day = api.Day(
            dt.date(2026, 9, 9),
            [
                api.MealPart(
                    "Oběd",
                    "11:40",
                    "14:00",
                    objednavky={},
                    menu=[
                        api.MenuVariant("1", "1", [api.Course("Jídlo", "Svíčková")]),
                        api.MenuVariant("2", "2", [api.Course("Jídlo", "Rizoto")]),
                    ],
                )
            ],
        )
        selection = api.select_meal(day, "3632660")
        assert selection.stav == api.STAV_VYCHOZI
        assert selection.varianta.chody[0].jidlo == "Svíčková"

    def test_odhlaseno_by_stav_even_if_id_menu_still_present(self):
        day = api.Day(
            dt.date(2026, 9, 9),
            [
                api.MealPart(
                    "Oběd",
                    "11:40",
                    "14:00",
                    objednavky={"3632660": {"idMenu": "12222", "stav": "Odhlaseno", "mnozstvi": 0}},
                    menu=[api.MenuVariant("12222", "1", [api.Course("Jídlo", "Guláš")])],
                )
            ],
        )
        selection = api.select_meal(day, "3632660")
        assert selection.stav == api.STAV_ODHLASENO
        assert selection.varianta is None

    def test_odhlaseno_by_mnozstvi_zero_even_if_stav_missing(self):
        day = api.Day(
            dt.date(2026, 9, 9),
            [
                api.MealPart(
                    "Oběd",
                    "11:40",
                    "14:00",
                    objednavky={"3632660": {"idMenu": "12222", "mnozstvi": 0}},
                    menu=[api.MenuVariant("12222", "1", [api.Course("Jídlo", "Guláš")])],
                )
            ],
        )
        assert api.select_meal(day, "3632660").stav == api.STAV_ODHLASENO

    def test_nenabizi_when_cast_dne_missing(self):
        day = api.Day(dt.date(2026, 9, 9), [api.MealPart("Snídaně", "7:00", "8:00", objednavky={})])
        assert api.select_meal(day, "3632660").stav == api.STAV_NENABIZI

    def test_prazdne_chody_znamena_varianta_se_ten_den_nenabizi(self):
        day = api.Day(
            dt.date(2026, 9, 9),
            [
                api.MealPart(
                    "Oběd",
                    "11:40",
                    "14:00",
                    objednavky={},
                    menu=[api.MenuVariant("1", "1", chody=[])],
                )
            ],
        )
        selection = api.select_meal(day, "3632660")
        assert selection.stav == api.STAV_VYCHOZI
        assert selection.varianta.chody == []
