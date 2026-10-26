"""Fantasy team abbreviation -> manager name, from managers.json (git-ignored; see managers.example.json)."""

from __future__ import annotations

import json
import logging

from app.config import ROOT_DIR

log = logging.getLogger(__name__)
MANAGERS_FILE = ROOT_DIR / "managers.json"


def load() -> dict[str, str]:
    """abbrev -> manager name; empty if the file is missing or invalid."""
    try:
        data = json.loads(MANAGERS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        log.warning("managers.json is not valid JSON: %s", e)
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, str) and v}


def manager_for(abbrev: str) -> str | None:
    return load().get(abbrev)
