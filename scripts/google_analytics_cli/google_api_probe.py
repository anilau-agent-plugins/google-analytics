"""Minimal read-only connectivity probes for Stage 4 authorization diagnostics."""

from __future__ import annotations

import json
from typing import Any

from .errors import AdvisorError
from .http import JsonTransport
from .oauth import USERINFO_ENDPOINT


ADMIN_SUMMARIES = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=1"
GTM_ACCOUNTS = "https://tagmanager.googleapis.com/tagmanager/v2/accounts?pageSize=1"


def _classification(exc: AdvisorError) -> dict[str, Any]:
    status = exc.details.get("status")
    body = str(exc.details.get("body", ""))
    reason = None
    try:
        parsed = json.loads(body)
        details = parsed.get("error", {}).get("details", []) if isinstance(parsed, dict) else []
        for item in details:
            if isinstance(item, dict) and item.get("reason"):
                reason = item["reason"]
                break
        if reason is None and isinstance(parsed, dict):
            reason = parsed.get("error", {}).get("status")
    except (json.JSONDecodeError, AttributeError):
        pass
    if reason in {"SERVICE_DISABLED", "ACCESS_NOT_CONFIGURED"} or "SERVICE_DISABLED" in body:
        state = "api_disabled"
    elif reason in {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "INSUFFICIENT_AUTHENTICATION_SCOPES"} or "insufficientPermissions" in body:
        state = "scope_missing"
    elif reason in {"ORG_POLICY_VIOLATION", "ADMIN_POLICY_ENFORCED", "DOMAIN_POLICY"}:
        state = "admin_policy"
    elif status == 401:
        state = "token_invalid"
    elif status == 403:
        state = "access_denied"
    elif status == 429:
        state = "quota_limited"
    else:
        state = "unavailable"
    return {"status": state, "httpStatus": status, "reason": reason or exc.code}


def _get(transport: JsonTransport, url: str, token: str) -> Any:
    return transport.request(
        "GET", url, headers={"Authorization": f"Bearer {token}"}, max_attempts=1
    ).data


def run_probes(access_token: str, *, transport: JsonTransport | None = None) -> dict[str, Any]:
    http = transport or JsonTransport(timeout=15.0, max_response_bytes=1024 * 1024)
    result: dict[str, Any] = {"readOnly": True, "identity": {}, "analyticsAdmin": {}, "tagManager": {}, "analyticsData": {}}
    try:
        identity = _get(http, USERINFO_ENDPOINT, access_token)
        result["identity"] = {
            "status": "ready" if isinstance(identity, dict) and identity.get("sub") and identity.get("email") else "invalid_response",
            "email": identity.get("email") if isinstance(identity, dict) else None,
            "emailVerified": bool(identity.get("email_verified", False)) if isinstance(identity, dict) else False,
        }
    except AdvisorError as exc:
        result["identity"] = _classification(exc)

    property_name = None
    try:
        admin = _get(http, ADMIN_SUMMARIES, access_token)
        summaries = admin.get("accountSummaries", []) if isinstance(admin, dict) else []
        for account in summaries:
            properties = account.get("propertySummaries", []) if isinstance(account, dict) else []
            if properties and isinstance(properties[0], dict):
                property_name = properties[0].get("property")
                break
        result["analyticsAdmin"] = {"status": "ready", "resourceAvailable": bool(summaries)}
    except AdvisorError as exc:
        result["analyticsAdmin"] = _classification(exc)

    try:
        gtm = _get(http, GTM_ACCOUNTS, access_token)
        accounts = gtm.get("account", []) if isinstance(gtm, dict) else []
        result["tagManager"] = {"status": "ready", "resourceAvailable": bool(accounts)}
    except AdvisorError as exc:
        result["tagManager"] = _classification(exc)

    if property_name and isinstance(property_name, str) and property_name.startswith("properties/"):
        try:
            _get(http, f"https://analyticsdata.googleapis.com/v1beta/{property_name}/metadata", access_token)
            result["analyticsData"] = {"status": "ready", "resourceAvailable": True}
        except AdvisorError as exc:
            result["analyticsData"] = _classification(exc)
    else:
        result["analyticsData"] = {"status": "not_verifiable_no_property", "resourceAvailable": False}

    states = [value.get("status") for key, value in result.items() if key != "readOnly" and isinstance(value, dict)]
    result["status"] = "ready" if all(item in {"ready", "not_verifiable_no_property"} for item in states) else "degraded"
    return result
