"""Machine-readable JSON output with recursive secret redaction."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from . import __version__


SECRET_KEY = re.compile(
    r"(?:secret|token|password|authorization|credentials?|private[_-]?key)(?:value)?$|^(?:authorizationCode|codeVerifier|oauthState)$",
    re.IGNORECASE,
)
SECRET_TEXT = (
    re.compile(r"\bya29\.[A-Za-z0-9._-]+"),
    re.compile(r"\b1//[A-Za-z0-9._-]+"),
    re.compile(r"\bGOCSPX-[A-Za-z0-9_-]+"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
)


def redact(value: Any, key: str | None = None) -> Any:
    if key and SECRET_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        result = value
        for pattern in SECRET_TEXT:
            result = pattern.sub("[REDACTED]", result)
        return result
    return value


def envelope(
    command: str,
    *,
    ok: bool,
    status: str,
    data: Any = None,
    warnings: list[Any] | None = None,
    errors: list[Any] | None = None,
) -> dict[str, Any]:
    return redact(
        {
            "schemaVersion": 1,
            "cliVersion": __version__,
            "ok": ok,
            "command": command,
            "status": status,
            "data": {} if data is None else data,
            "warnings": warnings or [],
            "errors": errors or [],
        }
    )


def configure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass


def emit(payload: dict[str, Any]) -> None:
    safe = redact(payload)
    try:
        text = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
        print(text)
    except UnicodeEncodeError:
        print(json.dumps(safe, ensure_ascii=True, separators=(",", ":")))
