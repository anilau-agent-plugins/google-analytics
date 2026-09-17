"""Bounded read-only Search Console sitemap and indexed-version diagnostics."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

from .artifact_store import ArtifactStore, canonical_json
from .auth import AuthService
from .contracts import validate_artifact_data
from .errors import AdvisorError, EXIT_CONFIGURATION, EXIT_INPUT, EXIT_NETWORK
from .measurement_policy import pii_issues
from .read_operation import ReadExecutor
from .report_analysis import redact_text
from .search_console import normalize_sites
from .search_console_indexing_renderer import render_search_console_indexing_report


SITEMAPS_LIST = "searchconsole.sitemaps.list"
SITEMAPS_GET = "searchconsole.sitemaps.get"
URL_INSPECT = "searchconsole.urlinspection.inspect"
DEFAULT_INSPECTION_URLS = 5
MAX_INSPECTION_URLS = 10
MAX_SITEMAP_ENTRIES = 1_000
MAX_PROVIDER_REFS = 50
MAX_RICH_GROUPS = 25
MAX_RICH_ITEMS = 50
MAX_RICH_ISSUES = 100
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SENSITIVE_QUERY_KEY = re.compile(
    r"(?:e-?mail|phone|mobile|full_?name|first_?name|last_?name|address|ssn|passport|"
    r"secret|token|password|credential|private[_-]?key)", re.I,
)
_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?:\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\bya29\.|\b1//|GOCSPX-|bearer\s+)", re.I,
)
_KNOWN_VERDICTS = {"PASS", "NEUTRAL", "FAIL", "VERDICT_UNSPECIFIED"}


def _hash_without(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: item for key, item in value.items() if key != field})).hexdigest()


def sitemap_snapshot_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "snapshotSha256")


def inspection_request_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "contentSha256")


def inspection_plan_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "planSha256")


def inspection_report_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "reportSha256")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", f"Could not read the {label} artifact.", EXIT_INPUT) from exc
    if not isinstance(value, dict):
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", f"The {label} artifact must be a JSON object.", EXIT_INPUT)
    return value


def _credential_fingerprint(profile_id: str, payload: dict[str, Any]) -> str:
    identity = payload.get("identity", {}) if isinstance(payload.get("identity"), dict) else {}
    raw = f"{profile_id}:{payload.get('clientRef', '')}:{identity.get('sub', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _bounded_text(value: Any, *, maximum: int = 2_048) -> str | None:
    if not isinstance(value, str) or not value or len(value) > maximum or _CONTROL.search(value):
        return None
    return value


def _normalized_host(hostname: str | None) -> str | None:
    if not hostname:
        return None
    try:
        return hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None


def _effective_port(parsed: Any) -> int | None:
    try:
        if parsed.port is not None:
            return parsed.port
    except ValueError as exc:
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "A selected URL contains an invalid port.", EXIT_INPUT) from exc
    return 443 if parsed.scheme.lower() == "https" else 80


def _url_identity(url: str) -> tuple[str, str, int | None, str, str]:
    parsed = urlsplit(url)
    return (
        parsed.scheme.lower(), _normalized_host(parsed.hostname) or "", _effective_port(parsed),
        parsed.path or "/", parsed.query,
    )


def _safe_sitemap_url(value: Any) -> str | None:
    text = _bounded_text(value)
    if text is None:
        return None
    try:
        parsed = urlsplit(text)
        _effective_port(parsed)
    except (ValueError, AdvisorError):
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not _normalized_host(parsed.hostname) or parsed.username is not None or parsed.password is not None or parsed.fragment:
        return None
    return text


def validate_inspection_url(url: str, site: str, property_type: str) -> dict[str, Any]:
    """Validate a target URL and prove membership in the exact selected property."""
    if not isinstance(url, str) or not url or len(url) > 2_048 or _CONTROL.search(url):
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "A selected URL is empty, oversized, or contains control characters.", EXIT_INPUT)
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "A selected URL is malformed.", EXIT_INPUT) from exc
    host = _normalized_host(parsed.hostname)
    if parsed.scheme.lower() not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "Inspection URLs must be absolute HTTP(S) URLs without userinfo or fragments.", EXIT_INPUT)
    _effective_port(parsed)
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if _SENSITIVE_QUERY_KEY.search(key) or _SENSITIVE_QUERY_VALUE.search(value):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "A selected URL query contains personal or secret-like data.", EXIT_INPUT)

    if property_type == "domain":
        domain = _normalized_host(site.removeprefix("sc-domain:"))
        member = bool(domain and (host == domain or host.endswith("." + domain)))
    elif property_type == "url_prefix":
        try:
            prefix = urlsplit(site)
        except ValueError as exc:
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_URL_OUTSIDE_PROPERTY", "The selected URL-prefix property is invalid.", EXIT_INPUT) from exc
        if prefix.query or prefix.fragment or prefix.username is not None or prefix.password is not None:
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_URL_OUTSIDE_PROPERTY", "The selected URL-prefix property is unsupported for safe inspection.", EXIT_INPUT)
        prefix_host = _normalized_host(prefix.hostname)
        member = (
            parsed.scheme.lower() == prefix.scheme.lower()
            and host == prefix_host
            and _effective_port(parsed) == _effective_port(prefix)
            and parsed.path.startswith(prefix.path)
        )
    else:
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_URL_OUTSIDE_PROPERTY", "URL Inspection supports only exact Domain and URL-prefix website properties.", EXIT_INPUT)
    if not member:
        raise AdvisorError(
            "SEARCH_CONSOLE_INSPECTION_URL_OUTSIDE_PROPERTY",
            "A selected URL is outside the exact Search Console property.",
            EXIT_INPUT,
            details={"url": url, "site": site, "propertyType": property_type, "networkRequestPerformed": False},
        )
    return {"url": url, "site": site, "propertyType": property_type, "member": True}


def _non_negative(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", f"Search Console returned an invalid {field} value.", EXIT_NETWORK)
    return value


def _normalize_sitemaps(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if not isinstance(payload, dict) or not isinstance(payload.get("sitemap", []), list):
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "Search Console returned an invalid sitemap response.", EXIT_NETWORK)
    raw_entries = payload.get("sitemap", [])
    limitations: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_entries[:MAX_SITEMAP_ENTRIES]):
        sitemap_path = _safe_sitemap_url(raw.get("path")) if isinstance(raw, dict) else None
        if not isinstance(raw, dict) or sitemap_path is None:
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", f"Sitemap entry {index} is invalid.", EXIT_NETWORK)
        contents: list[dict[str, Any]] = []
        source_contents = raw.get("contents", [])
        if not isinstance(source_contents, list):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "Search Console returned invalid sitemap contents.", EXIT_NETWORK)
        for content in source_contents[:50]:
            if not isinstance(content, dict):
                raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "Search Console returned an invalid sitemap content entry.", EXIT_NETWORK)
            contents.append({"type": _bounded_text(content.get("type"), maximum=128), "submitted": _non_negative(content.get("submitted", 0), "submitted")})
            if "indexed" in content:
                limitations.append({"code": "DEPRECATED_INDEXED_FIELD_IGNORED", "severity": "info", "message": "Google returned deprecated sitemap indexed data; it was ignored and no coverage ratio was calculated.", "evidenceRef": sitemap_path})
        if len(source_contents) > 50:
            limitations.append({"code": "SITEMAP_CONTENTS_TRUNCATED", "severity": "warning", "message": "A sitemap content summary exceeded the local 50-item bound.", "evidenceRef": sitemap_path})
        entries.append({
            "path": sitemap_path, "type": _bounded_text(raw.get("type"), maximum=128),
            "isPending": bool(raw.get("isPending", False)), "isSitemapsIndex": bool(raw.get("isSitemapsIndex", False)),
            "lastSubmitted": _bounded_text(raw.get("lastSubmitted")), "lastDownloaded": _bounded_text(raw.get("lastDownloaded")),
            "warnings": _non_negative(raw.get("warnings", 0), "warnings"),
            "errors": _non_negative(raw.get("errors", 0), "errors"), "contents": contents,
        })
    omitted = max(0, len(raw_entries) - MAX_SITEMAP_ENTRIES)
    if omitted:
        limitations.append({"code": "SITEMAP_ENTRIES_TRUNCATED", "severity": "warning", "message": f"The response exceeded 1,000 sitemap entries; {omitted} entries were omitted.", "evidenceRef": None})
    entries.sort(key=lambda item: item["path"])
    return entries, limitations, omitted


def _safe_provider_url(value: Any, limitations: list[dict[str, Any]], code: str) -> str | None:
    text = _bounded_text(value)
    if text is None:
        if value is not None:
            limitations.append({"code": code, "severity": "warning", "message": "A malformed or oversized provider URL was omitted.", "evidenceRef": None})
        return None
    clean, changed = redact_text(text)
    if changed:
        limitations.append({"code": "PROVIDER_URL_REDACTED", "severity": "info", "message": "A provider URL query or personal value was redacted before storage.", "evidenceRef": None})
    return clean


def _safe_provider_text(value: Any, limitations: list[dict[str, Any]], code: str, *, maximum: int = 2_048) -> str | None:
    text = _bounded_text(value, maximum=maximum)
    if text is None:
        if value is not None:
            limitations.append({"code": code, "severity": "warning", "message": "Malformed or oversized provider text was omitted.", "evidenceRef": None})
        return None
    clean, changed = redact_text(text)
    if changed:
        limitations.append({"code": "PROVIDER_TEXT_REDACTED", "severity": "info", "message": "Potential personal or secret-like provider text was redacted before storage.", "evidenceRef": None})
    return clean


def _crawl_time(value: Any, limitations: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    raw = _bounded_text(value, maximum=128)
    if raw is None:
        if value is not None:
            limitations.append({"code": "LAST_CRAWL_TIME_INVALID", "severity": "warning", "message": "Google returned a malformed lastCrawlTime value.", "evidenceRef": None})
        return None, None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except ValueError:
        limitations.append({"code": "LAST_CRAWL_TIME_INVALID", "severity": "warning", "message": "Google returned a malformed lastCrawlTime value.", "evidenceRef": None})
        return raw, None
    return raw, _utc(parsed)


def _normalize_amp(value: Any, limitations: list[dict[str, Any]]) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "Search Console returned invalid AMP data.", EXIT_NETWORK)
    last_crawl, last_crawl_utc = _crawl_time(value.get("lastCrawlTime"), limitations)
    return {
        "verdict": _safe_provider_text(value.get("verdict"), limitations, "AMP_VERDICT_INVALID", maximum=64),
        "ampUrl": _safe_provider_url(value.get("ampUrl"), limitations, "AMP_URL_INVALID"),
        "robotsTxtState": _safe_provider_text(value.get("robotsTxtState"), limitations, "AMP_ROBOTS_STATE_INVALID", maximum=64),
        "indexingState": _safe_provider_text(value.get("indexingState"), limitations, "AMP_INDEXING_STATE_INVALID", maximum=64),
        "ampIndexStatusVerdict": _safe_provider_text(value.get("ampIndexStatusVerdict"), limitations, "AMP_INDEX_VERDICT_INVALID", maximum=64),
        "lastCrawlTime": last_crawl, "lastCrawlTimeUtc": last_crawl_utc,
    }


def _normalize_mobile(value: Any, limitations: list[dict[str, Any]]) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "Search Console returned invalid deprecated mobile-usability data.", EXIT_NETWORK)
    issues = value.get("issues", [])
    if not isinstance(issues, list):
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "Search Console returned invalid deprecated mobile-usability issues.", EXIT_NETWORK)
    normalized = []
    for issue in issues[:100]:
        if not isinstance(issue, dict):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "A deprecated mobile-usability issue is invalid.", EXIT_NETWORK)
        normalized.append({
            "issueType": _safe_provider_text(issue.get("issueType"), limitations, "MOBILE_ISSUE_TYPE_INVALID", maximum=128),
            "severity": _safe_provider_text(issue.get("severity"), limitations, "MOBILE_ISSUE_SEVERITY_INVALID", maximum=64),
            "message": _safe_provider_text(issue.get("message"), limitations, "MOBILE_ISSUE_MESSAGE_INVALID"),
        })
    if len(issues) > 100:
        limitations.append({"code": "MOBILE_USABILITY_TRUNCATED", "severity": "warning", "message": "Deprecated mobile-usability evidence exceeded the 100-issue bound.", "evidenceRef": None})
    return {"deprecated": True, "verdict": _safe_provider_text(value.get("verdict"), limitations, "MOBILE_VERDICT_INVALID", maximum=64), "issues": normalized}


def _bounded_provider_urls(values: Any, limitations: list[dict[str, Any]], label: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", f"Search Console returned invalid {label} data.", EXIT_NETWORK)
    output = [item for raw in values[:MAX_PROVIDER_REFS] if (item := _safe_provider_url(raw, limitations, f"INVALID_{label.upper()}_URL"))]
    if len(values) > MAX_PROVIDER_REFS:
        limitations.append({"code": f"{label.upper()}_TRUNCATED", "severity": "warning", "message": f"Provider {label} references exceeded the 50-item bound.", "evidenceRef": None})
    return output


def _normalize_rich(value: Any, limitations: list[dict[str, Any]]) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "Search Console returned invalid rich-results data.", EXIT_NETWORK)
    groups = value.get("detectedItems", [])
    if not isinstance(groups, list):
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "Search Console returned invalid detected rich-result items.", EXIT_NETWORK)
    normalized_groups: list[dict[str, Any]] = []
    total_items = total_issues = 0
    for group in groups[:MAX_RICH_GROUPS]:
        if not isinstance(group, dict):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "A rich-result group is invalid.", EXIT_NETWORK)
        items = group.get("items", [])
        if not isinstance(items, list):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "A rich-result item collection is invalid.", EXIT_NETWORK)
        normalized_items = []
        for item in items:
            if total_items >= MAX_RICH_ITEMS:
                break
            if not isinstance(item, dict):
                raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "A rich-result item is invalid.", EXIT_NETWORK)
            issues = item.get("issues", [])
            if not isinstance(issues, list):
                raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "A rich-result issue collection is invalid.", EXIT_NETWORK)
            normalized_issues = []
            for issue in issues:
                if total_issues >= MAX_RICH_ISSUES:
                    break
                if not isinstance(issue, dict):
                    raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "A rich-result issue is invalid.", EXIT_NETWORK)
                message = _safe_provider_text(issue.get("issueMessage"), limitations, "RICH_RESULT_ISSUE_INVALID")
                severity = _safe_provider_text(issue.get("severity"), limitations, "RICH_RESULT_SEVERITY_INVALID", maximum=64)
                if message is None or severity is None:
                    limitations.append({"code": "RICH_RESULT_ISSUE_OMITTED", "severity": "warning", "message": "A malformed rich-result issue was omitted.", "evidenceRef": None})
                    continue
                normalized_issues.append({"issueMessage": message, "severity": severity})
                total_issues += 1
            normalized_items.append({"name": _safe_provider_text(item.get("name"), limitations, "RICH_RESULT_NAME_INVALID", maximum=512), "issues": normalized_issues})
            total_items += 1
        normalized_groups.append({"richResultType": _safe_provider_text(group.get("richResultType"), limitations, "RICH_RESULT_TYPE_INVALID", maximum=256), "items": normalized_items})
    if len(groups) > MAX_RICH_GROUPS or sum(len(group.get("items", [])) for group in groups if isinstance(group, dict)) > MAX_RICH_ITEMS or total_issues >= MAX_RICH_ISSUES:
        limitations.append({"code": "RICH_RESULTS_TRUNCATED", "severity": "warning", "message": "Rich-result evidence exceeded local safety bounds and was truncated.", "evidenceRef": None})
    return {"verdict": _safe_provider_text(value.get("verdict"), limitations, "RICH_RESULT_VERDICT_INVALID", maximum=64), "detectedItems": normalized_groups}


def _normalize_inspection(url: str, payload: Any, site: str, property_type: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("inspectionResult"), dict):
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "Search Console returned an invalid URL Inspection response.", EXIT_NETWORK)
    result = payload["inspectionResult"]
    index = result.get("indexStatusResult")
    if not isinstance(index, dict):
        raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "URL Inspection did not return a valid indexStatusResult.", EXIT_NETWORK)
    limitations: list[dict[str, Any]] = []
    verdict = _bounded_text(index.get("verdict"), maximum=64) or "VERDICT_UNSPECIFIED"
    if verdict not in _KNOWN_VERDICTS:
        limitations.append({"code": "UNKNOWN_PROVIDER_VERDICT", "severity": "warning", "message": f"Google returned an unknown verdict value: {verdict}.", "evidenceRef": url})
    google_canonical = _safe_provider_url(index.get("googleCanonical"), limitations, "INVALID_GOOGLE_CANONICAL")
    user_canonical = _safe_provider_url(index.get("userCanonical"), limitations, "INVALID_USER_CANONICAL")
    last_crawl, last_crawl_utc = _crawl_time(index.get("lastCrawlTime"), limitations)
    canonical_comparison = "unavailable"
    if google_canonical and user_canonical:
        canonical_comparison = "match" if google_canonical == user_canonical else "different"
    mobile = result.get("mobileUsabilityResult")
    if mobile is not None:
        limitations.append({"code": "MOBILE_USABILITY_DEPRECATED", "severity": "info", "message": "Google returned deprecated mobile usability evidence; it is preserved but not used for recommendations.", "evidenceRef": url})
    normalized = {
        "url": url, "providerVerdict": verdict,
        "coverageState": _safe_provider_text(index.get("coverageState"), limitations, "COVERAGE_STATE_INVALID", maximum=512),
        "robotsTxtState": _safe_provider_text(index.get("robotsTxtState"), limitations, "ROBOTS_STATE_INVALID", maximum=64),
        "indexingState": _safe_provider_text(index.get("indexingState"), limitations, "INDEXING_STATE_INVALID", maximum=64),
        "pageFetchState": _safe_provider_text(index.get("pageFetchState"), limitations, "PAGE_FETCH_STATE_INVALID", maximum=64),
        "lastCrawlTime": last_crawl, "lastCrawlTimeUtc": last_crawl_utc,
        "crawledAs": _safe_provider_text(index.get("crawledAs"), limitations, "CRAWLED_AS_INVALID", maximum=64),
        "googleCanonical": google_canonical, "userCanonical": user_canonical,
        "canonicalComparison": canonical_comparison,
        "googleCanonicalOutsideSelectedProperty": bool(google_canonical and not _provider_url_member(google_canonical, site, property_type)),
        "userCanonicalOutsideSelectedProperty": bool(user_canonical and not _provider_url_member(user_canonical, site, property_type)),
        "sitemaps": _bounded_provider_urls(index.get("sitemap"), limitations, "sitemaps"),
        "referringUrls": _bounded_provider_urls(index.get("referringUrls"), limitations, "referring_urls"),
        "ampResult": _normalize_amp(result.get("ampResult"), limitations),
        "richResultsResult": _normalize_rich(result.get("richResultsResult"), limitations),
        "mobileUsabilityResult": _normalize_mobile(mobile, limitations),
        "limitations": limitations,
    }
    return normalized


def _provider_url_member(url: str, site: str, property_type: str) -> bool:
    try:
        validate_inspection_url(url, site, property_type)
        return True
    except AdvisorError:
        return False


def _mapped_error(exc: AdvisorError) -> AdvisorError:
    mapping = {
        "API_DISABLED": ("SEARCH_CONSOLE_API_DISABLED", "The Search Console API is disabled."),
        "SCOPE_MISSING": ("SEARCH_CONSOLE_SCOPE_MISSING", "The authorization does not include Search Console read access."),
        "TOKEN_INVALID": ("SEARCH_CONSOLE_TOKEN_INVALID", "Google rejected the authorization token."),
        "RESOURCE_ACCESS_DENIED": ("SEARCH_CONSOLE_ACCESS_DENIED", "The selected account cannot read this Search Console resource."),
        "RESOURCE_NOT_FOUND": ("SEARCH_CONSOLE_SITEMAP_NOT_FOUND", "The selected Search Console resource was not found."),
        "QUOTA_LIMITED": ("SEARCH_CONSOLE_INSPECTION_QUOTA_EXCEEDED", "Search Console quota stopped this read-only operation."),
        "NETWORK_FAILURE": ("SEARCH_CONSOLE_INSPECTION_NETWORK_FAILURE", "The Search Console request failed without an automatic retry."),
    }
    if exc.code not in mapping:
        return exc
    code, message = mapping[exc.code]
    return AdvisorError(code, message, EXIT_NETWORK, retryable=False, details={**exc.details, "automaticRetryPerformed": False}, next_action="Check access and quota before creating a fresh plan.")


class SearchConsoleIndexingService:
    def __init__(self, *, auth: AuthService | None = None, transport: Any = None, now: Callable[[], datetime] | None = None) -> None:
        self.auth = auth or AuthService()
        self.transport = transport
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _authorized(self, profile_id: str) -> tuple[str, str, dict[str, Any]]:
        status = self.auth.status(profile_id)
        capability = status.get("capabilities", {}).get("searchConsole", {})
        if capability.get("status") != "ready":
            raise AdvisorError("SEARCH_CONSOLE_AUTHORIZATION_REQUIRED", "Search Console read-only authorization is required.", EXIT_CONFIGURATION)
        selected, token, payload = self.auth.access_token(profile_id)
        if selected != profile_id:
            raise AdvisorError("PROFILE_MISMATCH", "The authorization profile changed.", EXIT_CONFIGURATION)
        return selected, token, payload

    def _site(self, executor: ReadExecutor, site: str) -> dict[str, Any]:
        normalized = normalize_sites(executor.execute("searchconsole.sites.list").data)
        selected = next((item for item in normalized["sites"] if item.get("selectionKey") == site), None)
        if selected is None or not selected.get("dataReadable"):
            raise AdvisorError("SEARCH_CONSOLE_SITE_MISMATCH", "The exact readable Search Console property was not returned for this account.", EXIT_INPUT, details={"site": site})
        if selected.get("propertyType") not in {"domain", "url_prefix"}:
            raise AdvisorError("SEARCH_CONSOLE_SITE_MISMATCH", "This stage supports only Domain and URL-prefix website properties.", EXIT_INPUT)
        return selected

    def _snapshot(self, path: Path) -> dict[str, Any]:
        value = _load_json(path.resolve(), "sitemap snapshot")
        validate_artifact_data("search-console-sitemap-snapshot", value, path_label=str(path.resolve()))
        if value["snapshotSha256"] != sitemap_snapshot_sha256(value):
            raise AdvisorError("SEARCH_CONSOLE_SITEMAP_PARENT_MISMATCH", "The sitemap snapshot SHA-256 does not match its content.", EXIT_INPUT)
        return value

    def sitemaps(self, profile_id: str, site: str, project_root: Path, *, sitemap_index: str | None = None, snapshot_path: Path | None = None) -> dict[str, Any]:
        root = project_root.expanduser().resolve()
        if not root.is_dir():
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "The project root does not exist.", EXIT_INPUT)
        if bool(sitemap_index) != bool(snapshot_path):
            raise AdvisorError("SEARCH_CONSOLE_SITEMAP_PARENT_MISMATCH", "Nested sitemap listing requires both an exact sitemap index and its snapshot.", EXIT_INPUT)
        parent: dict[str, Any] | None = None
        if sitemap_index and snapshot_path:
            parent = self._snapshot(snapshot_path)
            expected = next((item for item in parent["entries"] if item["path"] == sitemap_index and item["isSitemapsIndex"]), None)
            if parent["profileId"] != profile_id or parent["site"] != site or parent["projectRoot"] != str(root) or expected is None:
                raise AdvisorError("SEARCH_CONSOLE_SITEMAP_PARENT_MISMATCH", "The requested sitemap index is not an exact index from this property's snapshot.", EXIT_INPUT)
        _, token, payload = self._authorized(profile_id)
        if parent and parent["credentialFingerprint"] != _credential_fingerprint(profile_id, payload):
            raise AdvisorError("SEARCH_CONSOLE_SITEMAP_PARENT_MISMATCH", "The authorization identity changed after the sitemap snapshot was created.", EXIT_INPUT)
        executor = ReadExecutor(token, transport=self.transport)
        identity = self._site(executor, site)
        try:
            response = executor.execute(SITEMAPS_LIST, resource=site, query={"sitemapIndex": sitemap_index} if sitemap_index else None)
        except AdvisorError as exc:
            raise _mapped_error(exc) from exc
        entries, limitations, omitted = _normalize_sitemaps(response.data)
        generated = self.now().astimezone(timezone.utc)
        snapshot = {
            "schemaVersion": 1, "artifactType": "search-console-sitemap-snapshot",
            "generatedAt": _utc(generated), "snapshotId": f"search-console-sitemap-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
            "snapshotSha256": "", "projectRoot": str(root), "profileId": profile_id,
            "credentialFingerprint": _credential_fingerprint(profile_id, payload), "site": site,
            "propertyIdentity": identity, "mode": "index-list" if sitemap_index else "root-list",
            "requestedSitemapIndex": sitemap_index, "requestedSitemap": None, "entries": entries,
            "totals": {"returned": len(entries), "omitted": omitted, "pending": sum(item["isPending"] for item in entries), "warnings": sum(item["warnings"] for item in entries), "errors": sum(item["errors"] for item in entries)},
            "limitations": limitations, "requestLedger": executor.ledger,
            "networkUsed": True, "mutationPerformed": False,
        }
        snapshot["snapshotSha256"] = sitemap_snapshot_sha256(snapshot)
        validate_artifact_data("search-console-sitemap-snapshot", snapshot)
        location = ArtifactStore(root).write_named_artifact("search-console-sitemaps", snapshot["snapshotId"], snapshot)
        return {"status": "partial" if limitations else "ready", "snapshot": snapshot, "artifact": location, "mutationPerformed": False}

    def sitemap(self, profile_id: str, site: str, sitemap: str, snapshot_path: Path, project_root: Path) -> dict[str, Any]:
        parent = self._snapshot(snapshot_path)
        if parent["profileId"] != profile_id or parent["site"] != site or not any(item["path"] == sitemap for item in parent["entries"]):
            raise AdvisorError("SEARCH_CONSOLE_SITEMAP_PARENT_MISMATCH", "The requested sitemap is not an exact path from this property's snapshot.", EXIT_INPUT)
        root = project_root.expanduser().resolve()
        if not root.is_dir() or str(root) != parent["projectRoot"]:
            raise AdvisorError("SEARCH_CONSOLE_SITEMAP_PARENT_MISMATCH", "The project root does not match the sitemap snapshot.", EXIT_INPUT)
        _, token, payload = self._authorized(profile_id)
        if parent["credentialFingerprint"] != _credential_fingerprint(profile_id, payload):
            raise AdvisorError("SEARCH_CONSOLE_SITEMAP_PARENT_MISMATCH", "The authorization identity changed after the sitemap snapshot was created.", EXIT_INPUT)
        executor = ReadExecutor(token, transport=self.transport)
        identity = self._site(executor, site)
        try:
            response = executor.execute(SITEMAPS_GET, resource=site, feedpath=sitemap)
        except AdvisorError as exc:
            raise _mapped_error(exc) from exc
        entries, limitations, omitted = _normalize_sitemaps({"sitemap": [response.data]})
        if entries[0]["path"] != sitemap:
            raise AdvisorError("SEARCH_CONSOLE_SITEMAP_PARENT_MISMATCH", "Google returned a different sitemap path than the exact requested snapshot entry.", EXIT_NETWORK)
        generated = self.now().astimezone(timezone.utc)
        value = {
            "schemaVersion": 1, "artifactType": "search-console-sitemap-snapshot", "generatedAt": _utc(generated),
            "snapshotId": f"search-console-sitemap-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}", "snapshotSha256": "",
            "projectRoot": str(root), "profileId": profile_id, "credentialFingerprint": _credential_fingerprint(profile_id, payload),
            "site": site, "propertyIdentity": identity, "mode": "get", "requestedSitemapIndex": None,
            "requestedSitemap": sitemap, "entries": entries,
            "totals": {"returned": len(entries), "omitted": omitted, "pending": sum(item["isPending"] for item in entries), "warnings": sum(item["warnings"] for item in entries), "errors": sum(item["errors"] for item in entries)},
            "limitations": limitations, "requestLedger": executor.ledger, "networkUsed": True, "mutationPerformed": False,
        }
        value["snapshotSha256"] = sitemap_snapshot_sha256(value)
        validate_artifact_data("search-console-sitemap-snapshot", value)
        location = ArtifactStore(root).write_named_artifact("search-console-sitemaps", value["snapshotId"], value)
        return {"status": "partial" if limitations else "ready", "snapshot": value, "artifact": location, "mutationPerformed": False}

    def catalog(self, profile_id: str, site: str) -> dict[str, Any]:
        selected, token, _ = self._authorized(profile_id)
        executor = ReadExecutor(token, transport=self.transport)
        identity = self._site(executor, site)
        return {
            "status": "ready", "profileId": selected, "site": identity,
            "defaults": {"maxUrls": DEFAULT_INSPECTION_URLS, "providerLanguageCode": "en-US"},
            "limits": {"maxUrls": MAX_INSPECTION_URLS, "automaticRetries": 0, "planLifetimeMinutes": 30},
            "limitations": ["URL Inspection reads Google's indexed version, not a live page test.", "A selected sample cannot describe site-wide indexing coverage.", "This workflow cannot request indexing."],
            "requestLedger": executor.ledger, "mutationPerformed": False,
        }

    def _request(self, path: Path) -> dict[str, Any]:
        request = _load_json(path.resolve(), "inspection request")
        validate_artifact_data("search-console-inspection-request", request, path_label=str(path.resolve()))
        if request["contentSha256"] != inspection_request_sha256(request):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "The inspection request SHA-256 does not match its content.", EXIT_INPUT)
        if pii_issues(request):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "The inspection request contains personal data.", EXIT_INPUT)
        return request

    def plan(self, profile_id: str, site: str, request_path: Path) -> dict[str, Any]:
        request = self._request(request_path)
        root = Path(request["projectRoot"]).expanduser().resolve()
        if not root.is_dir() or request["profileId"] != profile_id or request["site"] != site:
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "The CLI selection or project root does not match the immutable request.", EXIT_INPUT)
        _, token, payload = self._authorized(profile_id)
        executor = ReadExecutor(token, transport=self.transport)
        identity = self._site(executor, site)
        selections = []
        seen: set[tuple[str, str, int | None, str, str]] = set()
        for item in request["urls"]:
            url = item["url"]
            membership = validate_inspection_url(url, site, identity["propertyType"])
            key = _url_identity(url)
            if key in seen:
                raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "Duplicate inspection URLs after safe normalization are not allowed.", EXIT_INPUT)
            seen.add(key)
            source_artifact = item.get("sourceArtifact")
            if source_artifact:
                source_path = Path(source_artifact["path"]).expanduser().resolve()
                try:
                    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
                except OSError as exc:
                    raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "A referenced URL-selection source artifact cannot be read.", EXIT_INPUT) from exc
                if digest != source_artifact["sha256"]:
                    raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "A referenced URL-selection source artifact SHA-256 does not match.", EXIT_INPUT)
            selections.append({**item, "membership": membership})
        if request["requestedBudget"]["maxUrls"] != len(selections):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "The requested URL budget must equal the selected URL count.", EXIT_INPUT)
        if request.get("sitemapSnapshotRef"):
            ref = request["sitemapSnapshotRef"]
            snapshot = self._snapshot(Path(ref["path"]).expanduser().resolve())
            if (
                snapshot["snapshotSha256"] != ref["sha256"] or snapshot["site"] != site
                or snapshot["profileId"] != profile_id or snapshot["projectRoot"] != str(root)
                or snapshot["credentialFingerprint"] != _credential_fingerprint(profile_id, payload)
            ):
                raise AdvisorError("SEARCH_CONSOLE_SITEMAP_PARENT_MISMATCH", "The referenced sitemap snapshot does not match this request.", EXIT_INPUT)
        generated = self.now().astimezone(timezone.utc)
        plan = {
            "schemaVersion": 1, "artifactType": "search-console-inspection-plan", "generatedAt": _utc(generated),
            "expiresAt": _utc(generated + timedelta(minutes=30)),
            "planId": f"search-console-inspection-plan-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}", "planSha256": "",
            "projectRoot": str(root), "profileId": profile_id, "credentialFingerprint": _credential_fingerprint(profile_id, payload),
            "site": site, "propertyIdentity": identity, "request": request, "selections": selections,
            "operations": [{"operationId": URL_INSPECT, "url": item["url"], "order": index + 1} for index, item in enumerate(selections)],
            "budget": {"plannedUrlCalls": len(selections), "maxUrlCalls": MAX_INSPECTION_URLS, "automaticRetries": 0},
            "blockers": [], "limitations": ["URL Inspection returns Google's indexed version, not a live test.", "The selected sample cannot be extrapolated to the whole site."],
            "singleUse": True, "networkUsed": True, "inspectionDataRead": False, "mutationPerformed": False,
        }
        plan["planSha256"] = inspection_plan_sha256(plan)
        validate_artifact_data("search-console-inspection-plan", plan)
        location = ArtifactStore(root).write_named_artifact("search-console-inspection-plans", plan["planId"], plan)
        return {"status": "ready", "plan": plan, "artifact": location, "mutationPerformed": False}

    def _plan(self, path: Path, *, show: bool = False) -> dict[str, Any]:
        plan = _load_json(path.resolve(), "inspection plan")
        validate_artifact_data("search-console-inspection-plan", plan, path_label=str(path.resolve()))
        if plan["planSha256"] != inspection_plan_sha256(plan):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_PLAN_TAMPERED", "The inspection plan SHA-256 does not match its content.", EXIT_INPUT)
        if not show and self.now().astimezone(timezone.utc) > datetime.fromisoformat(plan["expiresAt"].replace("Z", "+00:00")):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_PLAN_EXPIRED", "The inspection plan expired; create a fresh plan.", EXIT_INPUT)
        if plan["blockers"] and not show:
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "The inspection plan has unresolved blockers.", EXIT_INPUT)
        return plan

    def show_plan(self, path: Path) -> dict[str, Any]:
        plan = self._plan(path, show=True)
        return {"status": "blocked" if plan["blockers"] else "ready", "planSha256": plan["planSha256"], "site": plan["site"], "urls": [{"url": item["url"], "reason": item["reason"], "sourceKind": item["sourceKind"]} for item in plan["selections"]], "budget": plan["budget"], "expiresAt": plan["expiresAt"], "limitations": plan["limitations"], "mutationPerformed": False}

    def run(self, path: Path) -> dict[str, Any]:
        plan = self._plan(path)
        store = ArtifactStore(Path(plan["projectRoot"]))
        if store.plan_was_consumed(plan["planSha256"]):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_PLAN_ALREADY_USED", "This single-use inspection plan has already started.", EXIT_INPUT)
        started = self.now().astimezone(timezone.utc)
        journal = {
            "schemaVersion": 1, "artifactType": "journal-entry", "generatedAt": _utc(started),
            "journalId": f"search-console-inspection-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
            "planId": plan["planId"], "planSha256": plan["planSha256"], "confirmationSha256": plan["planSha256"],
            "startedAt": _utc(started), "finishedAt": _utc(started), "status": "ambiguous", "requestIds": [],
            "readback": {"verified": False, "observedStateSha256": None, "message": "Read-only URL Inspection execution started; this immutable marker prevents plan reuse."},
            "operations": plan["operations"], "projectRoot": plan["projectRoot"], "profileId": plan["profileId"], "planPath": str(path.resolve()),
        }
        validate_artifact_data("journal-entry", journal)
        journal_location = store.write_journal(journal)
        _, token, payload = self._authorized(plan["profileId"])
        if _credential_fingerprint(plan["profileId"], payload) != plan["credentialFingerprint"]:
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_REQUEST_INVALID", "The authorization identity changed after planning.", EXIT_INPUT)
        executor = ReadExecutor(token, transport=self.transport)
        identity = self._site(executor, plan["site"])
        if identity != plan["propertyIdentity"]:
            raise AdvisorError("SEARCH_CONSOLE_SITE_MISMATCH", "The Search Console property identity or permission changed after planning.", EXIT_INPUT)
        results: list[dict[str, Any]] = []
        limitations = [{"code": "INDEXED_VERSION_ONLY", "severity": "info", "message": "URL Inspection describes Google's indexed version, not a live page test.", "evidenceRef": None}, {"code": "BOUNDED_SAMPLE", "severity": "warning", "message": "This small selected sample cannot describe site-wide indexing coverage.", "evidenceRef": None}]
        failure: dict[str, Any] | None = None
        for selection in plan["selections"]:
            try:
                response = executor.execute(URL_INSPECT, payload={"inspectionUrl": selection["url"], "siteUrl": plan["site"], "languageCode": plan["request"]["providerLanguageCode"]})
                normalized = _normalize_inspection(selection["url"], response.data, plan["site"], identity["propertyType"])
                normalized["reason"] = selection["reason"]
                normalized["sourceKind"] = selection["sourceKind"]
                normalized["requestId"] = response.request_id
                results.append(normalized)
                limitations.extend(normalized.pop("limitations"))
            except AdvisorError as exc:
                mapped = _mapped_error(exc)
                failure = {"code": mapped.code, "message": mapped.message, "url": selection["url"], "automaticRetryPerformed": False}
                limitations.append({"code": mapped.code, "severity": "warning", "message": mapped.message, "evidenceRef": selection["url"]})
                break
        status = "failed" if not results and failure else "partial" if failure else "ready"
        quality = "insufficient" if not results else "directional_only"
        facts, interpretations, recommendations, questions = self._evidence(results)
        generated = self.now().astimezone(timezone.utc)
        report = {
            "schemaVersion": 1, "artifactType": "search-console-inspection-report", "generatedAt": _utc(generated),
            "reportId": f"search-console-inspection-report-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}", "reportSha256": "",
            "sourcePlanId": plan["planId"], "sourcePlanSha256": plan["planSha256"], "profileId": plan["profileId"],
            "site": plan["site"], "propertyIdentity": plan["propertyIdentity"], "selectedUrls": [{"url": item["url"], "reason": item["reason"], "sourceKind": item["sourceKind"]} for item in plan["selections"]],
            "sitemapSnapshotRef": plan["request"].get("sitemapSnapshotRef"), "results": results,
            "requestLedger": executor.ledger, "budget": {"plannedUrlCalls": len(plan["selections"]), "requestsUsed": len(results) + (1 if failure else 0), "requestsRemaining": max(0, MAX_INSPECTION_URLS - len(results) - (1 if failure else 0)), "automaticRetries": 0},
            "facts": facts, "limitations": limitations, "interpretations": interpretations, "recommendations": recommendations,
            "questions": questions, "failure": failure, "status": status, "qualityTier": quality,
            "networkUsed": True, "mutationPerformed": False,
        }
        report["reportSha256"] = inspection_report_sha256(report)
        validate_artifact_data("search-console-inspection-report", report)
        location = store.write_named_artifact("search-console-inspection-reports", report["reportId"], report)
        return {"status": status, "report": report, "artifact": location, "journal": journal_location, "mutationPerformed": False}

    @staticmethod
    def _evidence(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        facts: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []
        for item in results:
            url = item["url"]
            facts.append({"factId": f"fact:index:{len(facts) + 1}", "statement": f"Google index verdict for {url}: {item['providerVerdict']}", "evidenceRefs": [url]})
            problem = verification = None
            if item.get("robotsTxtState") in {"DISALLOWED", "BLOCKED"}:
                problem, verification = "Google reports that robots rules block this selected URL.", "Review the exact robots rule, then create a separate website change plan if a correction is appropriate."
            elif item.get("indexingState") in {"BLOCKED_BY_META_TAG", "BLOCKED_BY_HTTP_HEADER"}:
                problem, verification = "Google reports a noindex-style block for this selected URL.", "Verify whether exclusion is intentional before planning any website change."
            elif item.get("pageFetchState") and item["pageFetchState"] not in {"SUCCESSFUL", "PAGE_FETCH_STATE_UNSPECIFIED"}:
                problem, verification = f"Google reports page fetch state {item['pageFetchState']} for this selected URL.", "Check the public HTTP response and server logs before planning a fix."
            elif item.get("canonicalComparison") == "different":
                problem, verification = "Google-selected and user-declared canonicals differ for this URL.", "Review duplicate intent and internal/canonical signals; a difference is not automatically an error."
            if problem:
                recommendations.append({"priority": 0, "problem": problem, "evidenceRefs": [url], "expectedBenefit": "Clarify whether this important URL can be represented correctly in Google Search.", "effort": "medium", "risk": "medium", "verification": verification, "requiresMutationWorkflow": True})
            if any(
                issue.get("severity") == "ERROR"
                for group in (item.get("richResultsResult") or {}).get("detectedItems", [])
                for rich_item in group.get("items", [])
                for issue in rich_item.get("issues", [])
            ):
                recommendations.append({"priority": 0, "problem": "Google reports a rich-result error for this selected URL.", "evidenceRefs": [url], "expectedBenefit": "Clarify whether this page is eligible for its intended rich result.", "effort": "medium", "risk": "medium", "verification": "Review the exact structured-data issue and validate a separately planned correction before deployment.", "requiresMutationWorkflow": True})
        for index, item in enumerate(recommendations[:5], start=1):
            item["priority"] = index
        interpretations = [{"interpretationId": "interpretation:index-sample", "statement": "These findings describe only the selected URLs and Google's last known indexed evidence; they do not establish site-wide coverage or causality.", "confidence": "directional", "evidenceRefs": [item["url"] for item in results]}] if results else []
        questions = [] if results else ["Should access, quota, and the exact selected property be checked before creating a fresh inspection plan?"]
        return facts, interpretations, recommendations[:5], questions

    def show(self, path: Path, language: str) -> dict[str, Any]:
        report = _load_json(path.resolve(), "inspection report")
        validate_artifact_data("search-console-inspection-report", report, path_label=str(path.resolve()))
        if report["reportSha256"] != inspection_report_sha256(report):
            raise AdvisorError("SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID", "The inspection report SHA-256 does not match its content.", EXIT_INPUT)
        selected = "en" if language == "auto" else language
        return {"status": report["status"], "report": report, "plain": render_search_console_indexing_report(report, selected if selected in {"ru", "en"} else "en"), "networkUsed": False, "mutationPerformed": False}
