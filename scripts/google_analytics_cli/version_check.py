"""Optional, rate-limited version metadata check with no telemetry."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import __version__
from .errors import AdvisorError
from .http import JsonTransport
from .paths import runtime_paths


ENV_VERSION_URL = "GOOGLE_ANALYTICS_ADVISOR_VERSION_URL"
CHECK_INTERVAL = timedelta(days=30)
TRUSTED_HOSTS = {"anilau.com", "www.anilau.com", "raw.githubusercontent.com", "api.github.com"}


def _parse_version(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError("version must use numeric MAJOR.MINOR.PATCH format")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _state_path(env: dict[str, str] | None = None) -> Path:
    return runtime_paths(env=env)["state"] / "version-check.json"


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def set_disabled(disabled: bool, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    path = _state_path(env)
    state = _load(path)
    state["disabled"] = disabled
    _save(path, state)
    return {"disabled": disabled, "statePath": str(path)}


def check_version(
    *,
    endpoint: str | None = None,
    force: bool = False,
    env: dict[str, str] | None = None,
    now: datetime | None = None,
    transport: JsonTransport | None = None,
    trusted_hosts: set[str] | None = None,
) -> dict[str, Any]:
    environ = os.environ if env is None else env
    url = endpoint or environ.get(ENV_VERSION_URL)
    path = _state_path(environ)
    state = _load(path)
    if state.get("disabled"):
        return {"status": "disabled", "currentVersion": __version__, "networkUsed": False}
    if not url:
        return {
            "status": "not_configured",
            "currentVersion": __version__,
            "networkUsed": False,
            "configuration": ENV_VERSION_URL,
        }
    parsed = urlparse(url)
    allowed = TRUSTED_HOSTS if trusted_hosts is None else trusted_hosts
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in allowed:
        return {
            "status": "untrusted_endpoint",
            "currentVersion": __version__,
            "networkUsed": False,
            "endpoint": url,
        }
    current_time = now or datetime.now(timezone.utc)
    try:
        previous = datetime.fromisoformat(str(state.get("checkedAt", "")).replace("Z", "+00:00"))
    except ValueError:
        previous = None
    if not force and previous is not None and current_time - previous < CHECK_INTERVAL and state.get("result"):
        cached = dict(state["result"])
        cached.update({"cached": True, "networkUsed": False, "endpoint": url})
        return cached
    try:
        response = (transport or JsonTransport(timeout=5.0, max_response_bytes=64 * 1024)).request(
            "GET", url, max_attempts=1
        )
        manifest = response.data
        if not isinstance(manifest, dict) or manifest.get("product") != "google-analytics":
            raise ValueError("manifest product is invalid")
        latest = str(manifest.get("latestVersion", ""))
        _parse_version(latest)
        release_url = manifest.get("releaseUrl")
        if release_url is not None and urlparse(str(release_url)).scheme != "https":
            raise ValueError("releaseUrl must use HTTPS")
        result = {
            "status": "update_available" if _parse_version(latest) > _parse_version(__version__) else "up_to_date",
            "currentVersion": __version__,
            "latestVersion": latest,
            "releaseUrl": release_url,
            "cached": False,
            "networkUsed": True,
            "endpoint": url,
        }
        _save(path, {"disabled": False, "checkedAt": current_time.isoformat(), "result": result})
        return result
    except (AdvisorError, OSError, ValueError, TypeError) as exc:
        return {
            "status": "unavailable",
            "currentVersion": __version__,
            "networkUsed": True,
            "endpoint": url,
            "reason": type(exc).__name__,
        }
