"""Konstanty integrace jidelna.cz."""

DOMAIN = "jidelna"

# entry.data
CONF_LOGIN = "login"
CONF_HESLO = "heslo"

# entry.options
CONF_UPDATE_HOUR = "update_hour"
CONF_UPDATE_MINUTE = "update_minute"
CONF_DINERS = "diners"
CONF_CALENDARS = "calendars"

DEFAULT_UPDATE_HOUR = 5
DEFAULT_UPDATE_MINUTE = 0

STORAGE_VERSION = 1

# klíče uvnitř CONF_DINERS[uid]
DINER_NAME = "name"
DINER_REGC = "regc"
DINER_ENABLED = "enabled"
DINER_CALENDAR_ID = "calendar_id"
DINER_PREFIX = "prefix"
DINER_LOCATION = "location"
DINER_ALLERGENS = "allergens"
DINER_DURATION_MODE = "duration_mode"
DINER_DURATION_MINUTES = "duration_minutes"
DINER_DISTINGUISH_WEEKS = "distinguish_weeks"
DINER_SCHEDULE = "schedule"

# klíče uvnitř CONF_CALENDARS[calendar_id]
CALENDAR_NAME = "name"

SERVICE_REFRESH = "refresh"
