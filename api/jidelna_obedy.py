#!/usr/bin/env python3
"""Zjištění vybraného oběda z jidelna.cz (read-only).

Pravidlo jídelny: pokud strávník nic nevybere, platí varianta 1.
Odhlášený oběd (stav Odhlaseno) ale NENÍ návrat k variantě 1 — ten den oběd není.
"""

import os
import sys
from datetime import date, timedelta

import requests

BASE = "https://www.jidelna.cz/rest/u/c58zbtfnjz72h6t5nzfva9uzvbag8m"
CAST_DNE = "Oběd"
VYCHOZI_VARIANTA = "1"          # co platí, když si strávník nevybere


class SessionExpired(RuntimeError):
    """Odpověď neobsahuje uživatelská data — session je neplatná.

    API v tomhle případě nevrací chybu, jen tiše vynechá klíč `objednavky`.
    """


class Jidelna:
    def __init__(self, login, heslo):
        self.login_id = login
        self.heslo = heslo
        self.s = requests.Session()
        self.uid = None
        self.dev = None

    def prihlas(self):
        r = self.s.post(f"{BASE}/login/jmenoheslo",
                        data={"login": self.login_id, "heslo": self.heslo},
                        timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("stav") != "ok":
            raise RuntimeError(f"Login selhal: {data.get('tag')} {data.get('zprava')}")
        ucty = data["ucet"]["ucty"]
        self.uid = next(iter(ucty))           # pro víc strávníků iterovat všechny
        self.dev = ucty[self.uid]["regc"]
        return data["ucet"]["ucty"][self.uid].get("kontoProObjednavani")

    def dny(self, od, do):
        r = self.s.get(f"{BASE}/zarizeni/{self.dev}/dny/od/{od}/do/{do}", timeout=15)
        r.raise_for_status()
        return r.json()

    def dny_s_reloginem(self, od, do):
        """Načte data a při neplatné session se jednou přihlásí znovu."""
        data = self.dny(od, do)
        try:
            zkontroluj_session(data)
        except SessionExpired:
            self.prihlas()
            data = self.dny(od, do)
            zkontroluj_session(data)
        return data


def zkontroluj_session(dny):
    """Bez platné session API vrátí 200 a jídelníček, jen bez klíče `objednavky`."""
    for den in dny:
        for cast in den["den"]["castiDne"]:
            if "objednavky" not in cast:
                raise SessionExpired(den["datum"])


def vyber(den, uid, cast_dne=CAST_DNE):
    """Vrátí (stav, popis) pro daný den.

    stav: "vybrano" | "vychozi" | "odhlaseno" | "nenabizi"
    """
    cast = next((c for c in den["den"]["castiDne"] if c["nazev"] == cast_dne), None)
    if cast is None:
        return "nenabizi", None

    objednavka = cast["objednavky"].get(uid)       # klíč mapy je string

    if objednavka is None:
        # strávník nic nevybral → platí výchozí varianta
        varianta = najdi_variantu(cast, nazev=VYCHOZI_VARIANTA)
        return "vychozi", popis(varianta)

    if objednavka["stav"] == "Odhlaseno" or objednavka["mnozstvi"] == 0:
        return "odhlaseno", None

    varianta = najdi_variantu(cast, id_menu=objednavka["idMenu"])
    return "vybrano", popis(varianta)


def najdi_variantu(cast, id_menu=None, nazev=None):
    for m in cast["menu"]:
        if id_menu is not None and str(m["id"]) == str(id_menu):   # id je int, idMenu string
            return m
        if nazev is not None and m["nazev"] == nazev:
            return m
    return None


def popis(varianta):
    if varianta is None or not varianta["chody"]:
        return None
    hlavni = next((c for c in varianta["chody"] if c["nazev"] == "Jídlo"), None)
    text = hlavni["jidlo"] if hlavni else varianta["chody"][0]["jidlo"]
    return f"[{varianta['nazev']}] {text}"


def main():
    login = os.environ.get("JIDELNA_LOGIN")
    heslo = os.environ.get("JIDELNA_HESLO")
    if not login or not heslo:
        sys.exit("Nastav JIDELNA_LOGIN a JIDELNA_HESLO")

    j = Jidelna(login, heslo)
    konto = j.prihlas()

    od = date.today()
    do = od + timedelta(days=13)
    dny = j.dny_s_reloginem(od.isoformat(), do.isoformat())

    print(f"konto: {konto}\n")
    for den in dny:
        stav, text = vyber(den, j.uid)
        znacka = {"vybrano": "*", "vychozi": " ", "odhlaseno": "-", "nenabizi": "?"}[stav]
        print(f"{znacka} {den['datum']}  {text or stav}")


if __name__ == "__main__":
    main()
