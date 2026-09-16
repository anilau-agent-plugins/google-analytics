"""Read-only Search Console site discovery with exact property identities."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .auth import AuthService
from .errors import AdvisorError, EXIT_CONFIGURATION, EXIT_NETWORK
from .read_operation import ReadExecutor


_PERMISSIONS = {
    "siteowner": "owner",
    "sitefulluser": "full",
    "siterestricteduser": "restricted",
    "siteunverifieduser": "unverified",
    "sitepermissionlevelunspecified": "unknown",
}
_READABLE = {"owner", "full", "restricted"}
_DOMAIN_PROPERTY = re.compile(r"^sc-domain:[^\s/:]+(?:\.[^\s/:]+)+$", re.IGNORECASE)


def _permission(value: Any) -> tuple[str, str | None]:
    raw = value if isinstance(value, str) and value else None
    key = re.sub(r"[^a-z]", "", raw.lower()) if raw else ""
    return _PERMISSIONS.get(key, "unknown"), raw


def _property_type(site_url: Any) -> str:
    if not isinstance(site_url, str) or not site_url:
        return "unknown"
    if _DOMAIN_PROPERTY.fullmatch(site_url):
        return "domain"
    try:
        parsed = urllib.parse.urlsplit(site_url)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
        ):
            return "url_prefix"
    except ValueError:
        pass
    return "unknown"


def normalize_sites(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AdvisorError(
            "SEARCH_CONSOLE_RESPONSE_INVALID",
            "Search Console returned an invalid sites response.",
            EXIT_NETWORK,
        )
    entries = payload.get("siteEntry", [])
    if not isinstance(entries, list):
        raise AdvisorError(
            "SEARCH_CONSOLE_RESPONSE_INVALID",
            "Search Console returned an invalid siteEntry collection.",
            EXIT_NETWORK,
        )
    sites: list[dict[str, Any]] = []
    limitations: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            sites.append({
                "siteUrl": None,
                "propertyType": "unknown",
                "permissionLevel": "unknown",
                "providerPermissionLevel": None,
                "dataReadable": False,
                "selectionKey": None,
            })
            limitations.append({
                "code": "SEARCH_CONSOLE_SITE_ENTRY_INVALID",
                "message": "One Search Console site entry was not an object and cannot be selected.",
                "entryIndex": index,
            })
            continue
        site_url = entry.get("siteUrl") if isinstance(entry.get("siteUrl"), str) else None
        property_type = _property_type(site_url)
        permission, raw_permission = _permission(entry.get("permissionLevel"))
        sites.append({
            "siteUrl": site_url,
            "propertyType": property_type,
            "permissionLevel": permission,
            "providerPermissionLevel": raw_permission,
            "dataReadable": permission in _READABLE and property_type != "unknown",
            "selectionKey": site_url if property_type != "unknown" else None,
        })
        if property_type == "unknown":
            limitations.append({
                "code": "SEARCH_CONSOLE_PROPERTY_IDENTITY_UNKNOWN",
                "message": "A Search Console property has an unsupported identity format and was not auto-selected.",
                "siteUrl": site_url,
            })
        if permission == "unknown":
            limitations.append({
                "code": "SEARCH_CONSOLE_PERMISSION_UNKNOWN",
                "message": "A Search Console permission value is unknown and was treated as not readable.",
                "siteUrl": site_url,
                "providerPermissionLevel": raw_permission,
            })
        elif permission == "unverified":
            limitations.append({
                "code": "SEARCH_CONSOLE_PROPERTY_UNVERIFIED",
                "message": "The connected account is not verified for this Search Console property.",
                "siteUrl": site_url,
            })
    sites.sort(key=lambda item: (str(item.get("siteUrl") or ""), str(item.get("providerPermissionLevel") or "")))
    counts = {
        "total": len(sites),
        "readable": sum(1 for item in sites if item["dataReadable"]),
        "unverified": sum(1 for item in sites if item["permissionLevel"] == "unverified"),
        "unknown": sum(
            1 for item in sites
            if item["permissionLevel"] == "unknown" or item["propertyType"] == "unknown"
        ),
    }
    if not sites:
        limitations.append({
            "code": "NO_SEARCH_CONSOLE_SITES_VISIBLE",
            "message": (
                "No Search Console properties were returned. This response alone cannot distinguish "
                "between no configured properties and no access for this account."
            ),
        })
        status = "action_required"
    elif counts["readable"] == 0:
        status = "action_required"
    elif limitations:
        status = "partial"
    else:
        status = "ready"
    return {"status": status, "sites": sites, "counts": counts, "limitations": limitations}


def _search_console_error(exc: AdvisorError) -> AdvisorError:
    mapping = {
        "API_DISABLED": (
            "SEARCH_CONSOLE_API_DISABLED",
            "The Search Console API is disabled in the user's Google Cloud project.",
            "Enable only searchconsole.googleapis.com in the selected user-owned project, then retry.",
        ),
        "SCOPE_MISSING": (
            "SEARCH_CONSOLE_SCOPE_MISSING",
            "Google rejected the Search Console read scope.",
            "Review the scope diff and run a safe authorization upgrade for this profile.",
        ),
        "TOKEN_INVALID": (
            "SEARCH_CONSOLE_TOKEN_INVALID",
            "Google rejected the authorization token used for Search Console.",
            "Reauthorize the selected profile without changing its Google account.",
        ),
        "RESOURCE_ACCESS_DENIED": (
            "SEARCH_CONSOLE_ACCESS_DENIED",
            "The connected Google account or Workspace policy denied Search Console discovery.",
            "Check the selected Google account and Workspace policy; do not broaden scopes automatically.",
        ),
        "QUOTA_LIMITED": (
            "SEARCH_CONSOLE_QUOTA_LIMITED",
            "Search Console quota limited the discovery request.",
            "Wait before retrying; do not start an automatic retry loop.",
        ),
    }
    mapped = mapping.get(exc.code)
    if mapped is None:
        return exc
    code, message, next_action = mapped
    return AdvisorError(
        code, message, exc.exit_code, retryable=exc.retryable,
        details={**exc.details, "providerOperation": "searchconsole.sites.list"},
        next_action=next_action,
    )


class SearchConsoleService:
    def __init__(self, *, auth: AuthService | None = None) -> None:
        self.auth = auth or AuthService()

    def sites(self, profile_id: str | None) -> dict[str, Any]:
        status = self.auth.status(profile_id)
        capability = status.get("capabilities", {}).get("searchConsole", {})
        if capability.get("status") != "ready":
            raise AdvisorError(
                "SEARCH_CONSOLE_AUTHORIZATION_REQUIRED",
                "This authorization profile does not yet include Search Console read-only access.",
                EXIT_CONFIGURATION,
                details={
                    "profileId": status.get("profileId"),
                    "missingScopes": capability.get("missingScopes", []),
                    "networkRequestPerformed": False,
                },
                next_action="Review auth consent-preview and run auth upgrade for this exact profile.",
            )
        selected, token, _payload = self.auth.access_token(status["profileId"])
        executor = ReadExecutor(token, transport=self.auth.json_transport)
        try:
            response = executor.execute("searchconsole.sites.list")
        except AdvisorError as exc:
            raise _search_console_error(exc) from exc
        normalized = normalize_sites(response.data)
        return {
            "profileId": selected,
            **normalized,
            "requestLedger": executor.ledger,
            "networkUsed": True,
            "mutationPerformed": False,
        }
