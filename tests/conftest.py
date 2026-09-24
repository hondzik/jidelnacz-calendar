"""Fixtures pro testy vyžadující Home Assistant (`config_flow.py`, `coordinator.py`,
`calendar.py`).

`jidelna_api.py`/`events.py` testy (`test_jidelna_api.py`/`test_events.py`) homeassistant
nepotřebují a importují je jako čisté top-level moduly (viz `pyproject.toml`). Tyto testy
naopak importují integraci jako běžný balíček `custom_components.jidelna` přes skutečný HA
loader/`ConfigEntry`.

Poznámka: `homeassistant` core (a tedy `pytest-homeassistant-custom-component`) importuje na
modulové úrovni `fcntl`, který na Windows neexistuje — tyhle testy proto na nativním Windows
Pythonu vůbec NEJDOU spustit/sesbírat (import selže dřív, než se stihne cokoliv testovat).
Spouštět je jde jen na Linuxu/macOS — lokálně přes WSL, v CI běží na `ubuntu-latest`
(viz `.github/workflows/validate.yml`), takže tam to funguje bez omezení.
"""

from __future__ import annotations

import pytest

try:
    import pytest_homeassistant_custom_component  # noqa: F401

    pytest_plugins = "pytest_homeassistant_custom_component"
except ImportError:
    # Balíček (a tedy fixtura `hass`) není na nativním Windows Pythonu vůbec
    # nainstalovatelný (viz docstring výše) — bez tohohle guardu by `pytest
    # tests/test_jidelna_api.py tests/test_events.py` (spustitelné i tam) spadlo
    # už při načítání conftestu, přestože tyhle dva soubory `hass` vůbec nepoužívají.
    pytest_plugins = []


@pytest.fixture(autouse=True)
def auto_ha_fixtures(request):
    """Integrace nemá žádnou závislost na `recorder` (na rozdíl od PREdistribuce) —
    stačí `enable_custom_integrations`, aby HA loader našel `custom_components/jidelna`.

    Fixtura se dotahuje líně (`getfixturevalue`), jen když test skutečně používá
    `hass` — jinak by autouse vynucoval existenci `enable_custom_integrations` i pro
    `test_jidelna_api.py`/`test_events.py`, které HA vůbec nenačítají.
    """
    if "hass" in request.fixturenames:
        request.getfixturevalue("enable_custom_integrations")
    yield
