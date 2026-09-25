"""Výpočet kalendářních událostí obědů z dat jidelna.cz + nastavení strávníka.

Čistá logika, bez importů `homeassistant.*` — testovatelné samostatně.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

try:
    from .jidelna_api import ALERGENY, Day, MenuVariant, select_meal
except ImportError:  # pragma: no cover — spuštěno jako bare modul v testech (bez balíčku)
    from jidelna_api import ALERGENY, Day, MenuVariant, select_meal

TZ_PRAGUE = ZoneInfo("Europe/Prague")

DURATION_ALL_DAY = "all_day"
DURATION_FIXED = "fixed"
DURATION_PER_DAY = "per_day"

WEEK_BOTH = "both"
WEEK_EVEN = "even"
WEEK_ODD = "odd"

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri")

ALLERGENS_HIDDEN = "hidden"
ALLERGENS_NUMBERS = "numbers"
ALLERGENS_NAMES = "names"

DEFAULT_EVENT_MINUTES = 30  # fallback délka, když "čas do" chybí/je neplatný


@dataclass
class DinerSettings:
    """Nastavení obsahu a rozvrhu události pro jednoho strávníka."""

    prefix: str = ""
    location: str = ""
    allergens: str = ALLERGENS_NAMES
    duration_mode: str = DURATION_ALL_DAY
    duration_minutes: int | None = None
    distinguish_weeks: bool = False
    # schedule[week_key][weekday] = {"from": "HH:MM", "to": "HH:MM" | None}
    schedule: dict[str, dict[str, dict[str, str | None]]] = field(default_factory=dict)


@dataclass
class MealEvent:
    uid: str
    start: dt.datetime | dt.date
    end: dt.datetime | dt.date
    all_day: bool
    summary: str
    description: str
    location: str


def build_event(day: Day, uid: str, settings: DinerSettings) -> MealEvent | None:
    """Sestaví událost pro daný den, nebo `None` (odhlášeno/nenabízí se/chybí rozvrh)."""
    selection = select_meal(day, uid)
    if selection.stav in ("odhlaseno", "nenabizi"):
        return None

    varianta = selection.varianta
    if varianta is None or not varianta.chody:
        return None

    summary = f"{settings.prefix}{_hlavni_jidlo(varianta)}"
    description = _describe(varianta, settings.allergens)
    event_uid = f"{uid}_{day.datum.isoformat()}"

    if settings.duration_mode == DURATION_ALL_DAY or day.datum.weekday() > 4:
        return MealEvent(
            uid=event_uid,
            start=day.datum,
            end=day.datum + dt.timedelta(days=1),
            all_day=True,
            summary=summary,
            description=description,
            location=settings.location,
        )

    times = _times_for(day.datum, settings)
    if not times or not times.get("from"):
        return None

    start_time = _parse_time(times["from"])
    start = dt.datetime.combine(day.datum, start_time, tzinfo=TZ_PRAGUE)

    if settings.duration_mode == DURATION_FIXED:
        end = start + dt.timedelta(minutes=settings.duration_minutes or 0)
    else:
        end_time = _parse_time(times["to"]) if times.get("to") else None
        end = (
            dt.datetime.combine(day.datum, end_time, tzinfo=TZ_PRAGUE)
            if end_time
            else start + dt.timedelta(minutes=DEFAULT_EVENT_MINUTES)
        )
        if end <= start:
            end = start + dt.timedelta(minutes=DEFAULT_EVENT_MINUTES)

    return MealEvent(
        uid=event_uid,
        start=start,
        end=end,
        all_day=False,
        summary=summary,
        description=description,
        location=settings.location,
    )


def _times_for(datum: dt.date, settings: DinerSettings) -> dict[str, str | None] | None:
    week_key = _week_key(datum, settings.distinguish_weeks)
    weekday = WEEKDAYS[datum.weekday()]
    return settings.schedule.get(week_key, {}).get(weekday)


def _week_key(datum: dt.date, distinguish: bool) -> str:
    if not distinguish:
        return WEEK_BOTH
    return WEEK_EVEN if datum.isocalendar().week % 2 == 0 else WEEK_ODD


def _parse_time(value: str) -> dt.time:
    hour, minute, *_rest = value.split(":")
    return dt.time(int(hour), int(minute))


def _hlavni_jidlo(varianta: MenuVariant) -> str:
    hlavni = next((c for c in varianta.chody if c.nazev == "Jídlo"), varianta.chody[0])
    return hlavni.jidlo


def _describe(varianta: MenuVariant, allergens: str = ALLERGENS_NAMES) -> str:
    lines = []
    for chod in varianta.chody:
        line = f"{chod.nazev}: {chod.jidlo}"
        if allergens == ALLERGENS_NUMBERS:
            popis = ", ".join(chod.alergeny)
        elif allergens == ALLERGENS_NAMES:
            popis = ", ".join(ALERGENY.get(a, a) for a in chod.alergeny)
        else:
            popis = ""
        if popis:
            line += f" ({popis})"
        lines.append(line)
    return "\n".join(lines)
