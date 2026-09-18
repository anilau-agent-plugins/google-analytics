"""Load IANA timezones from the OS or the bundled first-party tzdata fallback."""

from __future__ import annotations

import zipfile
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BUNDLED_TZDATA_VERSION = "2026.2"
BUNDLED_TZDATA = Path(__file__).resolve().parent / "_vendor" / f"tzdata-{BUNDLED_TZDATA_VERSION}.zip"


def _system_zoneinfo(key: str) -> ZoneInfo:
    return ZoneInfo(key)


def _bundled_zoneinfo(key: str) -> ZoneInfo:
    member = f"zoneinfo/{key}"
    try:
        with zipfile.ZipFile(BUNDLED_TZDATA) as archive, archive.open(member) as source:
            return ZoneInfo.from_file(source, key=key)
    except (FileNotFoundError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        raise ZoneInfoNotFoundError(key) from exc


def load_timezone(key: str) -> ZoneInfo:
    """Prefer native/system data and fall back to the bundled IANA snapshot."""

    try:
        return _system_zoneinfo(key)
    except ZoneInfoNotFoundError:
        return _bundled_zoneinfo(key)
