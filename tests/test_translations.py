"""Konzistence `strings.json`/`translations/*.json` — bez HA, čisté JSON soubory.

`hassfest` (viz `.github/workflows/validate.yml`) ověří jen validitu JSON a schéma
proti jádru HA — nekontroluje, že `strings.json` (anglický zdroj) a jednotlivé
`translations/*.json` mají mezi sebou stejnou sadu klíčů. Chybějící klíč v jednom
z nich se pak projeví jako nepřeložený popisek v UI (viz nedávný bug s
`diner_content.data.allergens` chybějícím v jednom ze souborů).
"""

from __future__ import annotations

import json
from pathlib import Path

JIDELNA_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "jidelna"

STRINGS_PATH = JIDELNA_DIR / "strings.json"
TRANSLATIONS_DIR = JIDELNA_DIR / "translations"


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _key_paths(data: dict, prefix: str = "") -> set[str]:
    """Rekurzivně posbírá cesty ke všem listovým klíčům (hodnota není dict)."""
    paths: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            paths |= _key_paths(value, path)
        else:
            paths.add(path)
    return paths


def _translation_files() -> list[Path]:
    return sorted(TRANSLATIONS_DIR.glob("*.json"))


def test_all_translation_files_are_valid_json():
    for path in [STRINGS_PATH, *_translation_files()]:
        _load(path)  # vyhodí json.JSONDecodeError, pokud je soubor nevalidní


def test_translation_files_have_same_keys_as_strings_json():
    reference = _key_paths(_load(STRINGS_PATH))
    for path in _translation_files():
        keys = _key_paths(_load(path))
        missing = reference - keys
        extra = keys - reference
        assert not missing, f"{path.name} is missing keys present in strings.json: {sorted(missing)}"
        assert not extra, f"{path.name} has extra keys not present in strings.json: {sorted(extra)}"
