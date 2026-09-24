# Jídelna.cz → Home Assistant

[Česká verze](README.md)

A Home Assistant integration that downloads ordered lunches from the Czech school-canteen
service [jidelna.cz](https://www.jidelna.cz) and exposes them as **calendar entities**. Each
diner linked to the account can be synced independently — which calendar it goes to, what the
event content looks like, and how the lunch time/duration is computed (all-day, a fixed
duration, or a per-weekday schedule with odd/even week distinction).

The integration relies on jidelna.cz's unofficial REST API — see
[`api/jidelna-cz-api.md`](api/jidelna-cz-api.md) (in Czech) for the technical writeup.

## Installation

### HACS

1. HACS → Integrations → the three-dot menu → Custom repositories.
2. Add `https://github.com/hondzik/jidelnacz-calendar` as type "Integration".
3. Install "Jídelna.cz" and restart Home Assistant.

### Manual

Copy `custom_components/jidelna` to `<config>/custom_components/jidelna` and restart
Home Assistant.

## Setup

Settings → Devices & services → Add integration → "Jídelna.cz".

1. Enter your jidelna.cz login (diner ID or the main account e-mail) and password.
2. Choose which diners linked to the account to sync, and the daily update time.
3. For each selected diner, go through the wizard:
   - **Calendar entity** — create a new one, or assign an existing one (e.g. a shared
     calendar for siblings).
   - **Event content** — an optional meal-name prefix and a location.
   - **Lunch duration** — all-day event / a fixed duration for every day / a different
     duration each day.
   - **Lunch time** (unless all-day) — optionally distinguish odd/even weeks, a "from" time
     for each weekday (and, for "different each day", a "to" time — with a fixed duration
     it's computed automatically).

Later changes (enabling/disabling a diner, editing the schedule, the update time, a manual
"Refresh now") live in the integration's options flow.

## Notes

- Read-only — the integration never places or cancels an order.
- The jidelna.cz login session lasts ~24 hours; the integration re-authenticates automatically
  when needed.
- Up to a year of past events is retained, even outside the currently fetched date range.
