"""Safe import and protected persistence for user-owned Desktop OAuth clients."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .auth_state import AuthStateStore
from .errors import AdvisorError, EXIT_INPUT
from .secret_store import SecretStore


MAX_CLIENT_FILE = 64 * 1024
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _is_reparse_or_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.stat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _validate(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise AdvisorError("OAUTH_CLIENT_INVALID", "OAuth client JSON must contain an object.", EXIT_INPUT)
    if "web" in value or "installed" not in value:
        raise AdvisorError(
            "OAUTH_CLIENT_TYPE_UNSUPPORTED",
            "Create and download a Google OAuth client with application type Desktop app.",
            EXIT_INPUT,
        )
    installed = value["installed"]
    if not isinstance(installed, dict):
        raise AdvisorError("OAUTH_CLIENT_INVALID", "The Desktop OAuth client block is invalid.", EXIT_INPUT)
    required = ("client_id", "client_secret", "project_id")
    if any(not isinstance(installed.get(key), str) or not installed[key].strip() for key in required):
        raise AdvisorError("OAUTH_CLIENT_INVALID", "The Desktop OAuth client is missing required fields.", EXIT_INPUT)
    if not installed["client_id"].endswith(".apps.googleusercontent.com"):
        raise AdvisorError("OAUTH_CLIENT_INVALID", "The OAuth client ID is not a Google Desktop client ID.", EXIT_INPUT)
    if installed.get("auth_uri") not in (None, AUTH_URI, "https://accounts.google.com/o/oauth2/v2/auth"):
        raise AdvisorError("OAUTH_CLIENT_INVALID", "The OAuth authorization endpoint is unexpected.", EXIT_INPUT)
    if installed.get("token_uri") not in (None, TOKEN_URI):
        raise AdvisorError("OAUTH_CLIENT_INVALID", "The OAuth token endpoint is unexpected.", EXIT_INPUT)
    return {key: installed[key].strip() for key in required}


def import_client(path: Path, *, secrets: SecretStore, state: AuthStateStore) -> dict[str, Any]:
    absolute = path.expanduser()
    if not absolute.is_absolute():
        raise AdvisorError("OAUTH_CLIENT_INVALID", "OAuth client path must be absolute.", EXIT_INPUT)
    try:
        before = absolute.stat()
        if not absolute.is_file() or _is_reparse_or_link(absolute) or before.st_size > MAX_CLIENT_FILE:
            raise AdvisorError("OAUTH_CLIENT_INVALID", "OAuth client file is unsafe or too large.", EXIT_INPUT)
        raw = absolute.read_bytes()
        after = absolute.stat()
    except OSError as exc:
        raise AdvisorError("OAUTH_CLIENT_INVALID", "OAuth client file could not be read.", EXIT_INPUT,
                           details={"reason": type(exc).__name__}) from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise AdvisorError("OAUTH_CLIENT_INVALID", "OAuth client file changed during import.", EXIT_INPUT)
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdvisorError("OAUTH_CLIENT_INVALID", "OAuth client file is not valid UTF-8 JSON.", EXIT_INPUT) from exc
    client = _validate(parsed)
    client_ref = "client-" + hashlib.sha256(client["client_id"].encode("utf-8")).hexdigest()[:16]
    fingerprint = hashlib.sha256(raw).hexdigest()
    protected = json.dumps({"schemaVersion": 1, **client}, separators=(",", ":")).encode("utf-8")
    secrets.put(f"oauth-client:{client_ref}", protected)
    timestamp = datetime.now(timezone.utc).isoformat()

    def save(index: dict[str, Any]) -> None:
        previous = index["clients"].get(client_ref, {})
        index["clients"][client_ref] = {
            "clientRef": client_ref,
            "projectId": client["project_id"],
            "maskedClientId": "…" + client["client_id"][-24:],
            "fingerprint": fingerprint,
            "createdAt": previous.get("createdAt", timestamp),
            "updatedAt": timestamp,
        }

    state.update(save)
    return {
        "clientRef": client_ref,
        "projectId": client["project_id"],
        "maskedClientId": "…" + client["client_id"][-24:],
        "fingerprint": fingerprint,
        "sourceDeleted": False,
    }


def load_client(client_ref: str, *, secrets: SecretStore) -> dict[str, str]:
    try:
        value = json.loads(secrets.get(f"oauth-client:{client_ref}").decode("utf-8"))
        return _validate({"installed": value})
    except AdvisorError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise AdvisorError("SECRET_STORE_CORRUPT", "The protected OAuth client is damaged.", EXIT_INPUT) from exc
