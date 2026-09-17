"""Strict URL, origin, period, and source-filter policy for local cross-source analysis."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


MAPPING_REVISION = "cross-source-url-v1"
MAX_PAGE_MAPPINGS = 500
MAX_UNMATCHED_SAMPLE = 25
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
DEVICE_VALUES = {"desktop", "mobile", "tablet"}


def safe_origin(value: str) -> str | None:
    if CONTROL.search(value) or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
    except (ValueError, UnicodeError):
        return None
    if port is not None and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), host, "", "", ""))


def origin_belongs_to_site(origin: str, site: str) -> bool:
    normalized = safe_origin(origin)
    if normalized is None:
        return False
    origin_host = urlsplit(normalized).hostname or ""
    if site.lower().startswith("sc-domain:"):
        domain = site.split(":", 1)[1].encode("idna").decode("ascii").lower().rstrip(".")
        return origin_host == domain or origin_host.endswith("." + domain)
    try:
        parsed_site = urlsplit(site)
    except ValueError:
        return False
    site_origin = safe_origin(urlunsplit((parsed_site.scheme, parsed_site.netloc, "", "", "")))
    return site_origin == normalized and (urlsplit(origin).path or "/").startswith(parsed_site.path or "/")


def normalize_search_console_page(value: str, quality: dict[str, Any], expected_origin: str) -> dict[str, Any]:
    base = {"sourceValue": value, "key": None, "path": None, "state": "invalid_or_redacted", "reason": "invalid_url"}
    if CONTROL.search(value) or len(value) > 4096 or quality.get("redacted"):
        base["reason"] = "redacted_or_unsafe"
        return base
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return base
        origin = safe_origin(urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
    except ValueError:
        return base
    if origin != expected_origin:
        return {**base, "state": "origin_mismatch", "reason": "origin_mismatch", "path": parsed.path or "/"}
    query_present = quality.get("queryPresent", "unknown")
    if query_present is not False or quality.get("queryRemoved") or quality.get("fragmentRemoved"):
        return {**base, "state": "query_ambiguous", "reason": "query_or_fragment_not_joined", "path": parsed.path or "/"}
    path = parsed.path or "/"
    key = expected_origin + path
    return {**base, "key": key, "path": path, "state": "candidate", "reason": None}


def normalize_ga4_landing(value: str, expected_origin: str) -> dict[str, Any]:
    base = {"sourceValue": value, "key": None, "path": None, "state": "invalid_or_redacted", "reason": "invalid_landing_page"}
    if not value or value == "(not set)" or len(value) > 2048 or CONTROL.search(value) or not value.startswith("/"):
        return base
    if "?" in value or "#" in value or "://" in value:
        return base
    return {**base, "key": expected_origin + value, "path": value, "state": "candidate", "reason": None}


def variant_key(path: str | None) -> str | None:
    if path is None:
        return None
    return path.casefold().rstrip("/") or "/"


def metric_value(row: dict[str, Any], name: str) -> float | int | None:
    value = row.get("metrics", {}).get(name)
    if isinstance(value, dict):
        value = value.get("value")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def date_range_label(row: dict[str, Any], fallback: str = "current") -> str:
    value = str(row.get("dimensions", {}).get("dateRange", ""))
    return {"0": "current", "1": "previous", "2": "previous-year"}.get(value, fallback)


def source_filter_is_closed(query: dict[str, Any], stream_id: str) -> bool:
    found: list[tuple[str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            field = value.get("fieldName")
            string_filter = value.get("stringFilter")
            if isinstance(field, str) and isinstance(string_filter, dict) and string_filter.get("matchType") == "EXACT":
                found.append((field, str(string_filter.get("value", ""))))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(query.get("request", {}).get("dimensionFilter", {}))
    return sorted(found) == sorted([
        ("streamId", stream_id), ("sessionSource", "google"), ("sessionMedium", "organic"),
    ])
