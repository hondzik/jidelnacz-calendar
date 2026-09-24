"""Testy pro `events.py` — čistá logika, bez Home Assistantu."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

# Bare top-level import — viz komentář v `test_jidelna_api.py` (kolize
# `custom_components/jidelna/calendar.py` se stdlib `calendar`, proto se
# tahle cesta nedává do `pyproject.toml`'s `pythonpath` globálně).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "jidelna"))

import events as ev  # noqa: E402
from jidelna_api import Course, Day, MealPart, MenuVariant  # noqa: E402

TZ = ZoneInfo("Europe/Prague")


def _variant(chody=None):
    return MenuVariant(
        "12222",
        "1",
        chody
        if chody is not None
        else [
            Course("Polévka", "Slepičí", ["9"]),
            Course("Jídlo", "Vepřová kýta ala bažant", ["1", "12"]),
            Course("Nápoj", "voda", ["7"]),
        ],
    )


def _day(datum, objednavky=None, menu=None, od="11:40", do="14:00"):
    return Day(
        datum,
        [
            MealPart(
                "Oběd",
                od,
                do,
                objednavky=objednavky if objednavky is not None else {},
                menu=menu if menu is not None else [_variant()],
            )
        ],
    )


UID = "3632660"


def _schedule(week=ev.WEEK_BOTH, **weekdays):
    return {week: weekdays}


class TestAllDay:
    def test_all_day_mode_produces_all_day_event(self):
        day = _day(dt.date(2026, 9, 9))  # středa
        settings = ev.DinerSettings(duration_mode=ev.DURATION_ALL_DAY, prefix="Oběd: ")
        event = ev.build_event(day, UID, settings)
        assert event.all_day is True
        assert event.start == dt.date(2026, 9, 9)
        assert event.end == dt.date(2026, 9, 10)
        assert event.summary == "Oběd: Vepřová kýta ala bažant"

    def test_weekend_is_always_all_day_regardless_of_mode(self):
        saturday = dt.date(2026, 9, 12)
        day = _day(saturday)
        settings = ev.DinerSettings(
            duration_mode=ev.DURATION_FIXED,
            duration_minutes=30,
            schedule=_schedule(sat={"from": "11:00"}),
        )
        event = ev.build_event(day, UID, settings)
        assert event.all_day is True


class TestFixedDuration:
    def test_fixed_duration_computes_end_from_minutes(self):
        day = _day(dt.date(2026, 9, 9))
        settings = ev.DinerSettings(
            duration_mode=ev.DURATION_FIXED,
            duration_minutes=45,
            schedule=_schedule(wed={"from": "11:40"}),
        )
        event = ev.build_event(day, UID, settings)
        assert event.all_day is False
        assert event.start == dt.datetime(2026, 9, 9, 11, 40, tzinfo=TZ)
        assert event.end == dt.datetime(2026, 9, 9, 12, 25, tzinfo=TZ)

    def test_missing_schedule_entry_returns_none(self):
        day = _day(dt.date(2026, 9, 9))
        settings = ev.DinerSettings(duration_mode=ev.DURATION_FIXED, duration_minutes=30, schedule={})
        assert ev.build_event(day, UID, settings) is None


class TestPerDayDuration:
    def test_per_day_uses_explicit_to_time(self):
        day = _day(dt.date(2026, 9, 9))
        settings = ev.DinerSettings(
            duration_mode=ev.DURATION_PER_DAY,
            schedule=_schedule(wed={"from": "11:40", "to": "12:10"}),
        )
        event = ev.build_event(day, UID, settings)
        assert event.start == dt.datetime(2026, 9, 9, 11, 40, tzinfo=TZ)
        assert event.end == dt.datetime(2026, 9, 9, 12, 10, tzinfo=TZ)

    def test_per_day_invalid_to_before_from_falls_back_to_default(self):
        day = _day(dt.date(2026, 9, 9))
        settings = ev.DinerSettings(
            duration_mode=ev.DURATION_PER_DAY,
            schedule=_schedule(wed={"from": "11:40", "to": "10:00"}),
        )
        event = ev.build_event(day, UID, settings)
        assert event.end == dt.datetime(2026, 9, 9, 12, 10, tzinfo=TZ)


class TestEvenOddWeeks:
    def test_distinguish_weeks_picks_even_or_odd_schedule(self):
        # 2026-09-09 je ISO týden 37 (lichý)
        odd_day = dt.date(2026, 9, 9)
        even_day = dt.date(2026, 9, 16)
        settings = ev.DinerSettings(
            duration_mode=ev.DURATION_PER_DAY,
            distinguish_weeks=True,
            schedule={
                ev.WEEK_ODD: {"wed": {"from": "11:00", "to": "11:30"}},
                ev.WEEK_EVEN: {"wed": {"from": "12:00", "to": "12:30"}},
            },
        )
        odd_event = ev.build_event(_day(odd_day), UID, settings)
        even_event = ev.build_event(_day(even_day), UID, settings)
        assert odd_event.start.time() == dt.time(11, 0)
        assert even_event.start.time() == dt.time(12, 0)


class TestSkippedEvents:
    def test_odhlaseno_produces_no_event(self):
        day = _day(
            dt.date(2026, 9, 9),
            objednavky={UID: {"idMenu": "12222", "stav": "Odhlaseno", "mnozstvi": 0}},
        )
        settings = ev.DinerSettings(duration_mode=ev.DURATION_ALL_DAY)
        assert ev.build_event(day, UID, settings) is None

    def test_empty_chody_produces_no_event(self):
        day = _day(dt.date(2026, 9, 9), menu=[MenuVariant("1", "1", [])])
        settings = ev.DinerSettings(duration_mode=ev.DURATION_ALL_DAY)
        assert ev.build_event(day, UID, settings) is None


class TestDescriptionAndUid:
    def test_description_lists_all_courses_with_allergens(self):
        day = _day(dt.date(2026, 9, 9))
        settings = ev.DinerSettings(duration_mode=ev.DURATION_ALL_DAY)
        event = ev.build_event(day, UID, settings)
        assert "Polévka: Slepičí (celer)" in event.description
        assert "Nápoj: voda (mléko)" in event.description

    def test_uid_is_stable_per_diner_and_date(self):
        day = _day(dt.date(2026, 9, 9))
        settings = ev.DinerSettings(duration_mode=ev.DURATION_ALL_DAY)
        event = ev.build_event(day, UID, settings)
        assert event.uid == f"{UID}_2026-09-09"

    def test_location_is_passed_through(self):
        day = _day(dt.date(2026, 9, 9))
        settings = ev.DinerSettings(duration_mode=ev.DURATION_ALL_DAY, location="ZŠ XY")
        event = ev.build_event(day, UID, settings)
        assert event.location == "ZŠ XY"
