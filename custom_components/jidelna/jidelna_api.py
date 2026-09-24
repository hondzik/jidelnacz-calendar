#!/usr/bin/env python3
"""Klient pro neoficiální REST API jidelna.cz.

Rekonstrukce podle `api/jidelna-cz-api.md` a prototypu `api/jidelna_obedy.py`.
Bez importů `homeassistant.*` — testovatelné a spustitelné samostatně. Spouštět
jako modul z kořene repa, NE přímou cestou k souboru — jinak Python přidá na
`sys.path` adresář `custom_components/jidelna`, kde je i náš `calendar.py`
(platforma `calendar`), a zastíní tak stdlib modul `calendar` pro cokoli
importované později (`requests`/`http.cookiejar` na něj narazí a spadne):

    JIDELNA_LOGIN=2803121 JIDELNA_HESLO=heslo python -m custom_components.jidelna.jidelna_api

Přihlašovací údaje se čtou jen z env proměnných, nikdy z argumentů příkazové
řádky ani ze zdrojáku.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from dataclasses import dataclass, field

import requests

BASE = "https://www.jidelna.cz/rest/u/c58zbtfnjz72h6t5nzfva9uzvbag8m"

CAST_DNE_OBED = "Oběd"
VYCHOZI_VARIANTA = "1"  # pravidlo jídelny: nevybráno = platí varianta 1

STAV_VYBRANO = "vybrano"
STAV_VYCHOZI = "vychozi"
STAV_ODHLASENO = "odhlaseno"
STAV_NENABIZI = "nenabizi"

ALERGENY = {
    "1": "obiloviny s lepkem",
    "2": "korýši",
    "3": "vejce",
    "4": "ryby",
    "5": "arašídy",
    "6": "sója",
    "7": "mléko",
    "8": "skořápkové plody",
    "9": "celer",
    "10": "hořčice",
    "11": "sezam",
    "12": "oxid siřičitý",
    "13": "vlčí bob",
    "14": "měkkýši",
}


class LoginError(RuntimeError):
    """Přihlášení selhalo (`stav != "ok"` v odpovědi)."""


class SessionExpired(RuntimeError):
    """Odpověď neobsahuje uživatelská data — session je neplatná.

    API v tomhle případě nevrací chybu ani jiný HTTP status, jen tiše
    vynechá klíč `objednavky` u každé části dne (viz `api/jidelna-cz-api.md`,
    „Bez platné session: tichý degradovaný režim").
    """


@dataclass
class Diner:
    """Strávník navázaný na účet."""

    uid: str  # idUzivatele (klíč mapy `ucty`/`objednavky`), jako string
    regc: str  # idZarizeni — interní ID jídelny
    konto: str | None = None
    jmeno: str | None = None  # doplní se přes fetch_diner_name(), API login ho nevrací


@dataclass
class Course:
    nazev: str
    jidlo: str
    alergeny: list[str] = field(default_factory=list)


@dataclass
class MenuVariant:
    id: str
    nazev: str
    chody: list[Course] = field(default_factory=list)


@dataclass
class MealPart:
    nazev: str
    od: str | None
    do: str | None
    objednavky: dict[str, dict] | None  # None = klíč chyběl v odpovědi (viz SessionExpired)
    menu: list[MenuVariant] = field(default_factory=list)


@dataclass
class Day:
    datum: dt.date
    casti: list[MealPart] = field(default_factory=list)


@dataclass
class MealSelection:
    stav: str  # STAV_VYBRANO | STAV_VYCHOZI | STAV_ODHLASENO | STAV_NENABIZI
    varianta: MenuVariant | None


def login(session: requests.Session, login_id: str, heslo: str) -> list[Diner]:
    """Přihlásí session a vrátí všechny strávníky navázané na účet.

    Iteruje celou mapu `ucet.ucty` — u účtu přihlášeného e-mailem (víc dětí
    na jednom účtu) může mít víc klíčů, ne jen ten první.

    Neúspěšné přihlášení posílá server s **HTTP 403** (ne 200, jak
    `api/jidelna-cz-api.md` jen odhaduje) — tělo je ale pořád validní JSON
    obálka (`{"stav": "chyba", "tag": "invalid_login", ...}`), proto se
    parsuje JSON PŘED voláním `raise_for_status()` (ověřeno živě
    2026-09-24: `curl` s neplatným loginem vrátí přesně tohle).
    """
    resp = session.post(
        f"{BASE}/login/jmenoheslo",
        data={"login": login_id, "heslo": heslo},
        timeout=15,
    )
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise

    if data.get("stav") != "ok":
        raise LoginError(f"{data.get('tag')}: {data.get('zprava')}")

    ucty = data["ucet"]["ucty"]
    return [
        Diner(uid=str(u["id"]), regc=str(u["regc"]), konto=u.get("kontoProObjednavani"))
        for u in ucty.values()
    ]


def fetch_diner_name(session: requests.Session, uid: str) -> str | None:
    """Zjistí jméno a příjmení strávníka z `/uzivatel/{uid}/info`.

    Endpoint vrací i citlivá data (banka, telefon, adresa) — z odpovědi se
    použije a nikam dál neukládá jen jméno/příjmení. Názvy polí `jmeno`/
    `prijmeni` jsou odhad (dokumentace API je neuvádí, protože obsahují PII a
    autor je záměrně nevypisoval) — ověřit proti reálné odpovědi. Vrací
    `None`, pokud se jméno nepodaří najít (config flow pak zobrazí jen ID).
    """
    resp = session.get(f"{BASE}/uzivatel/{uid}/info", timeout=15)
    resp.raise_for_status()
    data = resp.json()
    info = data.get("uzivatel", data)
    jmeno = info.get("jmeno")
    prijmeni = info.get("prijmeni")
    if not jmeno and not prijmeni:
        return None
    return " ".join(p for p in (jmeno, prijmeni) if p)


def fetch_days(session: requests.Session, regc: str, od: str, do: str) -> list[Day]:
    """Stáhne jídelníček pro danou jídelnu a rozsah dat (`YYYY-MM-DD`)."""
    resp = session.get(f"{BASE}/zarizeni/{regc}/dny/od/{od}/do/{do}", timeout=15)
    resp.raise_for_status()
    return [_parse_day(raw) for raw in resp.json()]


def _parse_day(raw: dict) -> Day:
    casti = []
    for c in raw["den"]["castiDne"]:
        menu = [
            MenuVariant(
                id=str(m["id"]),
                nazev=m["nazev"],
                chody=[
                    Course(ch["nazev"], ch["jidlo"], ch.get("alergeny", []))
                    for ch in m.get("chody", [])
                ],
            )
            for m in c.get("menu", [])
        ]
        casti.append(
            MealPart(
                nazev=c["nazev"],
                od=c.get("od"),
                do=c.get("do"),
                objednavky=c.get("objednavky"),
                menu=menu,
            )
        )
    return Day(datum=dt.date.fromisoformat(raw["datum"]), casti=casti)


def check_session(days: list[Day]) -> None:
    """Vyhodí `SessionExpired`, pokud jakékoli části dne chybí klíč `objednavky`."""
    for day in days:
        for cast in day.casti:
            if cast.objednavky is None:
                raise SessionExpired(day.datum.isoformat())


def fetch_days_with_relogin(
    session: requests.Session, login_id: str, heslo: str, regc: str, od: str, do: str
) -> list[Day]:
    """Stáhne jídelníček a při neplatné session se jednou přihlásí znovu."""
    days = fetch_days(session, regc, od, do)
    try:
        check_session(days)
    except SessionExpired:
        login(session, login_id, heslo)
        days = fetch_days(session, regc, od, do)
        check_session(days)
    return days


def select_meal(day: Day, uid: str, cast_dne: str = CAST_DNE_OBED) -> MealSelection:
    """Vrátí stav objednávky strávníka pro danou část dne (výchozí „Oběd").

    Odhlášený oběd (`stav == "Odhlaseno"`) NENÍ návrat k výchozí variantě —
    ten den oběd není (pravidlo z `api/jidelna_obedy.py`).
    """
    cast = next((c for c in day.casti if c.nazev == cast_dne), None)
    if cast is None:
        return MealSelection(STAV_NENABIZI, None)

    objednavka = (cast.objednavky or {}).get(uid)
    if objednavka is None:
        varianta = _najdi_variantu(cast, nazev=VYCHOZI_VARIANTA)
        return MealSelection(STAV_VYCHOZI, varianta)

    if objednavka.get("stav") == "Odhlaseno" or objednavka.get("mnozstvi") == 0:
        return MealSelection(STAV_ODHLASENO, None)

    varianta = _najdi_variantu(cast, id_menu=str(objednavka.get("idMenu")))
    return MealSelection(STAV_VYBRANO, varianta)


def _najdi_variantu(
    cast: MealPart, id_menu: str | None = None, nazev: str | None = None
) -> MenuVariant | None:
    for m in cast.menu:
        if id_menu is not None and m.id == str(id_menu):
            return m
        if nazev is not None and m.nazev == nazev:
            return m
    return None


def _parse_args(argv: list[str]) -> "argparse.Namespace":
    import argparse

    today = dt.date.today()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-from", default=today.isoformat())
    parser.add_argument("--date-to", default=(today + dt.timedelta(days=13)).isoformat())
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    login_id = os.environ.get("JIDELNA_LOGIN")
    heslo = os.environ.get("JIDELNA_HESLO")
    if not login_id or not heslo:
        print("chyba: nastav env proměnné JIDELNA_LOGIN a JIDELNA_HESLO", file=sys.stderr)
        return 1

    session = requests.Session()

    diners = login(session, login_id, heslo)
    for diner in diners:
        diner.jmeno = fetch_diner_name(session, diner.uid)
        print(f"strávník {diner.uid} ({diner.jmeno or '?'}), jídelna {diner.regc}, konto {diner.konto}")

        days = fetch_days_with_relogin(
            session, login_id, heslo, diner.regc, args.date_from, args.date_to
        )
        for day in days:
            selection = select_meal(day, diner.uid)
            popis = "?"
            if selection.varianta and selection.varianta.chody:
                hlavni = next(
                    (c for c in selection.varianta.chody if c.nazev == "Jídlo"),
                    selection.varianta.chody[0],
                )
                popis = f"[{selection.varianta.nazev}] {hlavni.jidlo}"
            znacka = {
                STAV_VYBRANO: "*",
                STAV_VYCHOZI: " ",
                STAV_ODHLASENO: "-",
                STAV_NENABIZI: "?",
            }[selection.stav]
            print(f"  {znacka} {day.datum}  {popis if selection.stav != STAV_ODHLASENO else 'odhlášeno'}")
        print()

    return 0


if __name__ == "__main__":  # pragma: no cover — jen entrypoint, testuje se main() přímo
    raise SystemExit(main(sys.argv[1:]))
