"""Cross-platform source, state, cache, and project path policy."""

from __future__ import annotations

import os
import platform
from pathlib import Path

from .errors import AdvisorError, EXIT_CONFIGURATION


ENV_HOME = "GOOGLE_ANALYTICS_ADVISOR_HOME"


def source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _absolute_override(env: dict[str, str]) -> Path | None:
    raw = env.get(ENV_HOME)
    if raw is None:
        return None
    if not raw.strip():
        raise AdvisorError(
            "INVALID_RUNTIME_HOME",
            f"{ENV_HOME} must not be empty.",
            EXIT_CONFIGURATION,
            next_action=f"Unset {ENV_HOME} or set it to an absolute path.",
        )
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise AdvisorError(
            "INVALID_RUNTIME_HOME",
            f"{ENV_HOME} must be an absolute path.",
            EXIT_CONFIGURATION,
            details={"value": raw},
            next_action=f"Set {ENV_HOME} to an absolute path.",
        )
    return path.resolve()


def runtime_paths(
    *, env: dict[str, str] | None = None, system: str | None = None, home: Path | None = None
) -> dict[str, Path]:
    environ = os.environ if env is None else env
    override = _absolute_override(environ)
    if override is not None:
        return {"state": override / "state", "cache": override / "cache"}
    os_name = system or platform.system()
    user_home = home or Path.home()
    if os_name == "Windows":
        local = Path(environ.get("LOCALAPPDATA", user_home / "AppData" / "Local"))
        base = local / "Anilau" / "GoogleAnalyticsAdvisor"
        return {"state": base / "State", "cache": base / "Cache"}
    if os_name == "Darwin":
        return {
            "state": user_home / "Library" / "Application Support" / "Anilau" / "GoogleAnalyticsAdvisor",
            "cache": user_home / "Library" / "Caches" / "Anilau" / "GoogleAnalyticsAdvisor",
        }
    state = Path(environ.get("XDG_STATE_HOME", user_home / ".local" / "state"))
    cache = Path(environ.get("XDG_CACHE_HOME", user_home / ".cache"))
    return {
        "state": state / "anilau" / "google-analytics-advisor",
        "cache": cache / "anilau" / "google-analytics-advisor",
    }


def project_data_path(project_root: Path) -> Path:
    if not project_root.is_absolute():
        raise AdvisorError("INVALID_PROJECT_ROOT", "Project root must be absolute.", EXIT_CONFIGURATION)
    return project_root.resolve() / ".google-analytics-advisor"
