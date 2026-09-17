"""Immutable, bounded, read-only Google Search Console performance reports."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .artifact_store import ArtifactStore, canonical_json
from .auth import AuthService
from .contracts import validate_artifact_data
from .errors import AdvisorError, EXIT_CONFIGURATION, EXIT_INPUT, EXIT_NETWORK
from .measurement_policy import pii_issues
from .read_operation import ReadExecutor
from .report_analysis import redact_payload, redact_text
from .search_console import normalize_sites
from .search_console_report_renderer import render_search_console_report


PACIFIC_NAME = "America/Los_Angeles"
SEARCH_TYPES = ("web", "image", "video", "news", "discover", "googleNews")
PRESETS = ("overview", "queries", "pages", "devices", "countries", "search-appearance", "recent-hourly")
MAX_REQUESTS = 20
MAX_ROWS = 12_000
DEFAULT_ROW_LIMIT = 250
MAX_ROW_LIMIT = 1_000
MAX_PAGES = 2
MAX_APPEARANCE_DETAILS = 4
QUERY_OPERATION = "searchconsole.searchanalytics.query"


def _pacific_datetime(value: datetime) -> datetime:
    """Use system/tzdata when available and a dependency-free post-2007 US fallback otherwise."""
    try:
        return value.astimezone(ZoneInfo(PACIFIC_NAME))
    except ZoneInfoNotFoundError:
        utc_value = value.astimezone(timezone.utc)
        year = utc_value.year
        march_first = date(year, 3, 1)
        second_sunday = 1 + ((6 - march_first.weekday()) % 7) + 7
        november_first = date(year, 11, 1)
        first_sunday = 1 + ((6 - november_first.weekday()) % 7)
        dst_start = datetime(year, 3, second_sunday, 10, tzinfo=timezone.utc)
        dst_end = datetime(year, 11, first_sunday, 9, tzinfo=timezone.utc)
        offset = -7 if dst_start <= utc_value < dst_end else -8
        return utc_value.astimezone(timezone(timedelta(hours=offset), name="Pacific"))


def _hash_without(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: child for key, child in value.items() if key != field})).hexdigest()


def search_console_request_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "contentSha256")


def search_console_plan_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "planSha256")


def search_console_report_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "reportSha256")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdvisorError("SEARCH_CONSOLE_REPORT_ARTIFACT_INVALID", f"Could not read the {label} artifact.", EXIT_INPUT) from exc
    if not isinstance(value, dict):
        raise AdvisorError("SEARCH_CONSOLE_REPORT_ARTIFACT_INVALID", f"The {label} artifact must be a JSON object.", EXIT_INPUT)
    return value


def _credential_fingerprint(profile_id: str, payload: dict[str, Any]) -> str:
    identity = payload.get("identity", {}) if isinstance(payload.get("identity"), dict) else {}
    raw = f"{profile_id}:{payload.get('clientRef', '')}:{identity.get('sub', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _previous_year(start: date, end: date) -> tuple[date, date] | None:
    try:
        return start.replace(year=start.year - 1), end.replace(year=end.year - 1)
    except ValueError:
        return None


def resolve_search_console_periods(request: dict[str, Any], *, now: Callable[[], datetime]) -> tuple[list[dict[str, Any]], list[str]]:
    current = _pacific_datetime(now())
    today = current.date()
    period = request["period"]
    limitations: list[str] = []
    if request["dataState"] == "hourly_all":
        if period["mode"] == "explicit":
            start, end = date.fromisoformat(period["from"]), date.fromisoformat(period["to"])
        else:
            days = min(int(period["days"]), 3)
            end = today
            start = end - timedelta(days=days - 1)
        if (end - start).days + 1 > 3:
            raise AdvisorError("SEARCH_CONSOLE_REPORT_REQUEST_INVALID", "Hourly Search Console reports are limited to three Pacific days.", EXIT_INPUT)
        return [{"label": "current", "from": start.isoformat(), "to": end.isoformat(), "complete": False, "dataState": "hourly_all"}], limitations
    if period["mode"] == "explicit":
        start, end = date.fromisoformat(period["from"]), date.fromisoformat(period["to"])
    else:
        days = int(period["days"])
        end = today if request["dataState"] == "all" else today - timedelta(days=3)
        start = end - timedelta(days=days - 1)
    if start > end:
        raise AdvisorError("SEARCH_CONSOLE_REPORT_REQUEST_INVALID", "The report period starts after it ends.", EXIT_INPUT)
    if (end - start).days + 1 > 366:
        raise AdvisorError("SEARCH_CONSOLE_REPORT_REQUEST_INVALID", "Search Console reports are limited to 366 days.", EXIT_INPUT)
    if end > today:
        raise AdvisorError("SEARCH_CONSOLE_REPORT_REQUEST_INVALID", "The report period cannot include a future Pacific date.", EXIT_INPUT)
    complete = request["dataState"] == "final"
    periods = [{"label": "current", "from": start.isoformat(), "to": end.isoformat(), "complete": complete, "dataState": request["dataState"]}]
    length = (end - start).days + 1
    if "previous-period" in request["comparisons"]:
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=length - 1)
        periods.append({"label": "previous", "from": previous_start.isoformat(), "to": previous_end.isoformat(), "complete": complete, "dataState": request["dataState"]})
    if "previous-year" in request["comparisons"]:
        shifted = _previous_year(start, end)
        if shifted is None:
            limitations.append("The previous-year comparison was omitted because February 29 has no exact prior-year match.")
        else:
            periods.append({"label": "previous-year", "from": shifted[0].isoformat(), "to": shifted[1].isoformat(), "complete": complete, "dataState": request["dataState"]})
    if request["dataState"] == "all":
        limitations.append("The report may contain preliminary Search Console data and must preserve first_incomplete_date.")
    return periods, limitations


def _api_filters(filters: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"dimension": item["dimension"], "operator": item["operator"], "expression": item["expression"]}
        for item in filters
    ]


def _body(period: dict[str, Any], request: dict[str, Any], dimensions: list[str], *, row_limit: int) -> dict[str, Any]:
    value: dict[str, Any] = {
        "startDate": period["from"], "endDate": period["to"],
        "dimensions": dimensions, "type": request["searchType"],
        "dataState": request["dataState"], "aggregationType": "auto",
        "rowLimit": row_limit, "startRow": 0,
    }
    filters = _api_filters(request.get("filters", []))
    if filters:
        value["dimensionFilterGroups"] = [{"groupType": "and", "filters": filters}]
    if dimensions == ["page"]:
        value["aggregationType"] = "byPage"
    return value


def _query(query_id: str, preset: str, period: dict[str, Any], request: dict[str, Any], dimensions: list[str], *, row_limit: int = DEFAULT_ROW_LIMIT, max_pages: int = 1, role: str = "detail") -> dict[str, Any]:
    return {
        "queryId": query_id, "preset": preset, "periodLabel": period["label"],
        "operationId": QUERY_OPERATION, "dimensions": dimensions,
        "payload": _body(period, request, dimensions, row_limit=row_limit),
        "rowLimit": row_limit, "maxPages": max_pages, "role": role,
    }


def build_queries(request: dict[str, Any], periods: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, list[str]]:
    current = periods[0]
    queries = [_query("sc-availability-current", "availability", current, request, ["date"], row_limit=10, role="availability")]
    limitations: list[str] = []
    reserved_dynamic = 0
    overview_selected = "overview" in request["presets"]
    if request["dataState"] == "hourly_all":
        queries.append(_query("sc-recent-hourly-current", "recent-hourly", current, request, ["hour"], row_limit=DEFAULT_ROW_LIMIT, role="hourly"))
        return queries, 0, limitations
    for preset in request["presets"]:
        if preset == "overview":
            for period in periods:
                suffix = period["label"]
                queries.extend([
                    _query(f"sc-overview-total-{suffix}", "overview", period, request, [], row_limit=1, role="summary"),
                    _query(f"sc-overview-device-{suffix}", "devices", period, request, ["device"], role="breakdown"),
                    _query(f"sc-overview-country-{suffix}", "countries", period, request, ["country"], role="breakdown"),
                ])
            queries.extend([
                _query("sc-overview-trend-current", "overview", current, request, ["date"], role="trend"),
                _query("sc-overview-queries-current", "queries", current, request, ["query"], max_pages=2),
                _query("sc-overview-pages-current", "pages", current, request, ["page"], max_pages=2),
                _query("sc-overview-appearance-current", "search-appearance", current, request, ["searchAppearance"], role="appearance-discovery"),
            ])
            reserved_dynamic = max(reserved_dynamic, MAX_APPEARANCE_DETAILS)
        elif preset == "queries":
            if request["searchType"] in {"discover", "googleNews"}:
                limitations.append(f"queries is unavailable for searchType={request['searchType']} and was omitted.")
            else:
                queries.extend(_query(f"sc-queries-{item['label']}", preset, item, request, ["query"], max_pages=2) for item in periods)
        elif preset == "pages":
            queries.extend(_query(f"sc-pages-{item['label']}", preset, item, request, ["page"], max_pages=2) for item in periods)
        elif preset == "devices":
            queries.extend(_query(f"sc-devices-{item['label']}", preset, item, request, ["device"], role="breakdown") for item in periods)
        elif preset == "countries":
            queries.extend(_query(f"sc-countries-{item['label']}", preset, item, request, ["country"], role="breakdown") for item in periods)
        elif preset == "search-appearance":
            if overview_selected:
                continue
            queries.append(_query("sc-search-appearance-current", preset, current, request, ["searchAppearance"], role="appearance-discovery"))
            reserved_dynamic = max(reserved_dynamic, MAX_APPEARANCE_DETAILS)
    unique: dict[str, dict[str, Any]] = {item["queryId"]: item for item in queries}
    return list(unique.values()), reserved_dynamic, limitations


def _mapped_error(exc: AdvisorError) -> AdvisorError:
    mapping = {
        "API_DISABLED": ("SEARCH_CONSOLE_API_DISABLED", "The Search Console API is disabled."),
        "SCOPE_MISSING": ("SEARCH_CONSOLE_SCOPE_MISSING", "The authorization does not include Search Console read access."),
        "TOKEN_INVALID": ("SEARCH_CONSOLE_TOKEN_INVALID", "Google rejected the authorization token."),
        "RESOURCE_ACCESS_DENIED": ("SEARCH_CONSOLE_ACCESS_DENIED", "The selected account cannot read this Search Console property."),
        "RESOURCE_NOT_FOUND": ("SEARCH_CONSOLE_SITE_MISMATCH", "The selected Search Console property was not found."),
        "QUOTA_LIMITED": ("SEARCH_CONSOLE_QUOTA_EXCEEDED", "Search Console quota stopped this read-only report."),
        "NETWORK_FAILURE": ("SEARCH_CONSOLE_NETWORK_FAILURE", "The Search Console request failed without an automatic retry."),
    }
    if exc.code not in mapping:
        return exc
    code, message = mapping[exc.code]
    return AdvisorError(code, message, EXIT_NETWORK, retryable=False, details={**exc.details, "automaticRetryPerformed": False}, next_action="Create a fresh plan after checking access and quota status.")


def _number(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0:
        raise AdvisorError("SEARCH_CONSOLE_RESPONSE_INVALID", f"Search Console returned an invalid {name} value.", EXIT_NETWORK)
    return float(value)


def _normalize_dataset(query: dict[str, Any], response: dict[str, Any], *, search_type: str, truncated: bool) -> dict[str, Any]:
    if not isinstance(response, dict) or not isinstance(response.get("rows", []), list):
        raise AdvisorError("SEARCH_CONSOLE_RESPONSE_INVALID", "Search Console returned an invalid report response.", EXIT_NETWORK)
    dimensions = query["dimensions"]
    rows: list[dict[str, Any]] = []
    redactions = 0
    for item in response.get("rows", []):
        if not isinstance(item, dict) or not isinstance(item.get("keys", []), list) or len(item.get("keys", [])) != len(dimensions):
            raise AdvisorError("SEARCH_CONSOLE_RESPONSE_INVALID", "A Search Console row does not match the requested dimensions.", EXIT_NETWORK)
        dimension_values: dict[str, str] = {}
        for name, raw in zip(dimensions, item["keys"]):
            clean, changed = redact_text(str(raw))
            dimension_values[name] = clean
            redactions += int(changed)
        clicks = _number(item.get("clicks", 0), "clicks")
        impressions = _number(item.get("impressions", 0), "impressions")
        ctr = _number(item.get("ctr", 0), "ctr")
        if ctr > 1:
            raise AdvisorError("SEARCH_CONSOLE_RESPONSE_INVALID", "Search Console returned CTR outside the expected 0–1 range.", EXIT_NETWORK)
        metrics: dict[str, float | None] = {"clicks": clicks, "impressions": impressions, "ctr": ctr}
        metrics["position"] = None if search_type in {"discover", "googleNews"} or "position" not in item else _number(item["position"], "position")
        rows.append({"dimensions": dimension_values, "metrics": metrics})
    metadata = response.get("metadata", {}) if isinstance(response.get("metadata"), dict) else {}
    incomplete_date = metadata.get("first_incomplete_date")
    incomplete_hour = metadata.get("first_incomplete_hour")
    if rows and (incomplete_date or incomplete_hour):
        completeness = "preliminary"
    elif not rows:
        completeness = "empty"
    elif query["preset"] in {"queries", "pages", "search-appearance"} or truncated:
        completeness = "top_rows_only"
    else:
        completeness = "complete_summary"
    return {
        "datasetId": f"dataset:{query['queryId']}", "queryId": query["queryId"],
        "preset": query["preset"], "periodLabel": query["periodLabel"],
        "searchType": search_type, "dataState": query["payload"]["dataState"],
        "dimensions": dimensions, "aggregationType": query["payload"].get("aggregationType", "auto"),
        "responseAggregationType": response.get("responseAggregationType"),
        "metadata": {"firstIncompleteDate": incomplete_date, "firstIncompleteHour": incomplete_hour},
        "rows": rows, "returnedRows": len(rows), "truncated": truncated,
        "completeness": completeness, "privacyRedactions": redactions,
        "rowsSha256": hashlib.sha256(canonical_json(rows)).hexdigest(),
    }


def _comparison(current: float, previous: float) -> dict[str, Any]:
    if previous == 0:
        return {"absolute": current, "relative": None, "state": "from-zero" if current else "unchanged-zero"}
    return {"absolute": current - previous, "relative": (current - previous) / abs(previous), "state": "comparable"}


def _evidence(datasets: list[dict[str, Any]], limitations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    facts: list[dict[str, Any]] = []
    calculations: list[dict[str, Any]] = []
    interpretations: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    totals = {item["periodLabel"]: item for item in datasets if item["preset"] == "overview" and not item["dimensions"] and item["rows"]}
    current = totals.get("current")
    previous = totals.get("previous")
    if current:
        metrics = current["rows"][0]["metrics"]
        for name in ("clicks", "impressions", "ctr", "position"):
            value = metrics.get(name)
            if value is not None:
                facts.append({"factId": f"fact:search:{name}", "statement": f"Current {name}: {value}", "technicalName": name, "value": value, "evidenceRefs": [current["datasetId"]]})
        if previous:
            old = previous["rows"][0]["metrics"]
            for name in ("clicks", "impressions", "ctr"):
                calculations.append({"calculationId": f"calc:search:{name}", "name": f"Change in {name}", "formula": "(current-previous)/abs(previous)", "inputs": {"current": metrics[name], "previous": old[name]}, "result": _comparison(float(metrics[name]), float(old[name])), "evidenceRefs": [current["datasetId"], previous["datasetId"]]})
        interpretations.append({"interpretationId": "interpretation:overview", "statement": "The report describes organic Google visibility and clicks for the selected property; it does not prove why the metrics changed.", "confidence": "descriptive", "evidenceRefs": [current["datasetId"]]})
        if metrics["impressions"] >= 100 and metrics["ctr"] < 0.02:
            recommendations.append({"priority": 1, "problem": "Search visibility has at least 100 impressions but CTR is below the 2% review heuristic.", "evidenceRefs": [current["datasetId"]], "expectedBenefit": "Identify snippets or intent mismatches worth testing.", "effort": "medium", "risk": "low", "verification": "Review high-impression queries/pages, change only relevant titles or content, then compare a completed final period.", "requiresMutationWorkflow": True})
    quality_refs = sorted({item["evidenceRef"] for item in limitations if item.get("evidenceRef")})
    if quality_refs:
        recommendations.insert(0, {"priority": 1, "problem": "The report has completeness or privacy limitations.", "evidenceRefs": quality_refs, "expectedBenefit": "Avoid decisions that assume top rows are the full dataset.", "effort": "low", "risk": "low", "verification": "Use totals for scale and treat detailed rows as directional evidence.", "requiresMutationWorkflow": False})
    for index, item in enumerate(recommendations[:5], start=1):
        item["priority"] = index
    questions = [] if current else ["Does this property normally receive Google Search impressions in the selected period?"]
    return facts, calculations, interpretations, recommendations[:5], questions


class SearchConsoleReportService:
    def __init__(self, *, auth: AuthService | None = None, transport: Any = None, now: Callable[[], datetime] | None = None) -> None:
        self.auth = auth or AuthService()
        self.transport = transport
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _authorized(self, profile_id: str) -> tuple[str, str, dict[str, Any]]:
        status = self.auth.status(profile_id)
        capability = status.get("capabilities", {}).get("searchConsole", {})
        if capability.get("status") != "ready":
            raise AdvisorError("SEARCH_CONSOLE_AUTHORIZATION_REQUIRED", "Search Console read-only authorization is required.", EXIT_CONFIGURATION, next_action="Review auth consent-preview and upgrade this exact profile.")
        selected, token, payload = self.auth.access_token(profile_id)
        if selected != profile_id:
            raise AdvisorError("PROFILE_MISMATCH", "The authorization profile changed.", EXIT_CONFIGURATION)
        return selected, token, payload

    def _site(self, executor: ReadExecutor, site: str) -> dict[str, Any]:
        normalized = normalize_sites(executor.execute("searchconsole.sites.list").data)
        selected = next((item for item in normalized["sites"] if item.get("selectionKey") == site), None)
        if selected is None or not selected.get("dataReadable"):
            raise AdvisorError("SEARCH_CONSOLE_SITE_MISMATCH", "The exact readable Search Console property was not returned for this account.", EXIT_INPUT, details={"site": site})
        return selected

    def catalog(self, profile_id: str, site: str) -> dict[str, Any]:
        selected, token, _ = self._authorized(profile_id)
        executor = ReadExecutor(token, transport=self.transport)
        identity = self._site(executor, site)
        return {
            "profileId": selected, "site": identity, "presets": list(PRESETS),
            "searchTypes": list(SEARCH_TYPES), "dataStates": ["final", "all", "hourly_all"],
            "timezone": "America/Los_Angeles", "defaults": {"preset": "overview", "searchType": "web", "dataState": "final", "days": 28},
            "limits": {"maxRequests": MAX_REQUESTS, "maxRows": MAX_ROWS, "maxRowLimit": MAX_ROW_LIMIT, "maxPages": MAX_PAGES, "hourlyDays": 3},
            "limitations": ["Query/page/search appearance rows are top rows, not exhaustive.", "Anonymous queries are omitted from detail rows.", "GenAI, branded-query and platform-property UI reports are not exposed by this API contract."],
            "requestLedger": executor.ledger, "mutationPerformed": False,
        }

    def _load_request(self, path: Path) -> dict[str, Any]:
        request = _load_json(path.resolve(), "Search Console report request")
        validate_artifact_data("search-console-report-request", request, path_label=str(path.resolve()))
        if request["contentSha256"] != search_console_request_sha256(request):
            raise AdvisorError("SEARCH_CONSOLE_REPORT_REQUEST_TAMPERED", "The Search Console report request SHA-256 does not match its content.", EXIT_INPUT)
        if pii_issues(request):
            raise AdvisorError("SEARCH_CONSOLE_REPORT_REQUEST_INVALID", "The request contains personal data or a prohibited field.", EXIT_INPUT)
        _, redactions = redact_payload(request)
        if redactions:
            raise AdvisorError("SEARCH_CONSOLE_REPORT_REQUEST_INVALID", "The request contains secret-like or personal values.", EXIT_INPUT)
        return request

    def plan(self, profile_id: str, site: str, request_path: Path) -> dict[str, Any]:
        request = self._load_request(request_path)
        root = Path(request["projectRoot"]).expanduser().resolve()
        if not root.is_dir():
            raise AdvisorError("SEARCH_CONSOLE_REPORT_ARTIFACT_INVALID", "The report project root does not exist.", EXIT_INPUT)
        if request["profileId"] != profile_id or request["site"] != site:
            raise AdvisorError("SEARCH_CONSOLE_SITE_MISMATCH", "The CLI selection does not match the immutable request.", EXIT_INPUT)
        _, token, payload = self._authorized(profile_id)
        executor = ReadExecutor(token, transport=self.transport)
        identity = self._site(executor, site)
        periods, limitations = resolve_search_console_periods(request, now=self.now)
        queries, reserved_dynamic, query_limitations = build_queries(request, periods)
        limitations.extend(query_limitations)
        blockers: list[str] = []
        planned_requests = len(queries) + reserved_dynamic
        planned_rows = sum(item["rowLimit"] * item["maxPages"] for item in queries) + reserved_dynamic
        if planned_requests > MAX_REQUESTS:
            blockers.append(f"The selected presets require up to {planned_requests} requests; the product limit is {MAX_REQUESTS}.")
            queries = queries[:MAX_REQUESTS]
        if planned_rows > MAX_ROWS:
            blockers.append(f"The selected presets allow up to {planned_rows} rows; the product limit is {MAX_ROWS}.")
        generated = self.now().astimezone(timezone.utc)
        plan = {
            "schemaVersion": 1, "artifactType": "search-console-report-plan",
            "generatedAt": generated.isoformat().replace("+00:00", "Z"),
            "expiresAt": (generated + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            "planId": f"search-console-report-plan-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
            "planSha256": "", "projectRoot": str(root), "profileId": profile_id,
            "credentialFingerprint": _credential_fingerprint(profile_id, payload), "site": site,
            "propertyIdentity": identity, "request": request, "periods": periods, "queries": queries,
            "budget": {"maxRequests": MAX_REQUESTS, "maxRows": MAX_ROWS, "plannedRequests": planned_requests, "plannedRows": planned_rows, "reservedDynamicRequests": reserved_dynamic, "automaticRetries": 0},
            "blockers": sorted(set(blockers)), "limitations": sorted(set(limitations)),
            "networkUsed": True, "mutationPerformed": False,
        }
        plan["planSha256"] = search_console_plan_sha256(plan)
        validate_artifact_data("search-console-report-plan", plan)
        location = ArtifactStore(root).write_named_artifact("search-console-report-plans", plan["planId"], plan)
        return {"status": "blocked" if blockers else "ready", "plan": plan, "artifact": location, "mutationPerformed": False}

    def _load_plan(self, path: Path, *, allow_blocked: bool = False) -> dict[str, Any]:
        plan = _load_json(path.resolve(), "Search Console report plan")
        validate_artifact_data("search-console-report-plan", plan, path_label=str(path.resolve()))
        if plan["planSha256"] != search_console_plan_sha256(plan):
            raise AdvisorError("SEARCH_CONSOLE_REPORT_PLAN_TAMPERED", "The Search Console report plan SHA-256 does not match its content.", EXIT_INPUT)
        if self.now().astimezone(timezone.utc) > datetime.fromisoformat(plan["expiresAt"].replace("Z", "+00:00")):
            raise AdvisorError("SEARCH_CONSOLE_REPORT_PLAN_EXPIRED", "The Search Console report plan expired; create a fresh plan.", EXIT_INPUT)
        if plan["blockers"] and not allow_blocked:
            raise AdvisorError("SEARCH_CONSOLE_REPORT_PLAN_BLOCKED", "The Search Console report plan has unresolved blockers.", EXIT_INPUT, details={"blockers": plan["blockers"]})
        return plan

    def _fetch(self, executor: ReadExecutor, site: str, query: dict[str, Any], budget: dict[str, int]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        merged: dict[str, Any] = {"rows": []}
        evidence: list[dict[str, Any]] = []
        page_size = int(query["rowLimit"])
        for page in range(int(query["maxPages"])):
            if budget["requestsUsed"] >= MAX_REQUESTS:
                raise AdvisorError("SEARCH_CONSOLE_QUERY_BUDGET_EXCEEDED", "The Search Console request budget was exhausted.", EXIT_NETWORK)
            payload = deepcopy(query["payload"])
            payload["startRow"] = page * page_size
            response = executor.execute(QUERY_OPERATION, resource=site, payload=payload)
            budget["requestsUsed"] += 1
            rows = response.data.get("rows", []) if isinstance(response.data, dict) else None
            if not isinstance(rows, list):
                raise AdvisorError("SEARCH_CONSOLE_RESPONSE_INVALID", "Search Console returned an invalid rows collection.", EXIT_NETWORK)
            if budget["rowsUsed"] + len(rows) > MAX_ROWS:
                raise AdvisorError("SEARCH_CONSOLE_QUERY_BUDGET_EXCEEDED", "The Search Console row budget was exhausted.", EXIT_NETWORK)
            budget["rowsUsed"] += len(rows)
            merged["rows"].extend(rows)
            if isinstance(response.data, dict):
                for key in ("responseAggregationType", "metadata"):
                    if key in response.data:
                        merged[key] = response.data[key]
            evidence.append({"page": page + 1, "startRow": payload["startRow"], "rowLimit": page_size, "rowsReceived": len(rows), "requestId": response.request_id, "stopReason": "short-page" if len(rows) < page_size else "page-bound"})
            if len(rows) < page_size:
                break
        return merged, evidence

    def run(self, plan_path: Path) -> dict[str, Any]:
        plan = self._load_plan(plan_path)
        _, token, payload = self._authorized(plan["profileId"])
        if _credential_fingerprint(plan["profileId"], payload) != plan["credentialFingerprint"]:
            raise AdvisorError("SEARCH_CONSOLE_REPORT_CONTEXT_DRIFT", "The authorization identity changed after planning.", EXIT_INPUT)
        executor = ReadExecutor(token, transport=self.transport)
        if self._site(executor, plan["site"]) != plan["propertyIdentity"]:
            raise AdvisorError("SEARCH_CONSOLE_SITE_MISMATCH", "The Search Console property identity or permission changed after planning.", EXIT_INPUT)
        budget = {"requestsUsed": 0, "rowsUsed": 0}
        datasets: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        limitations: list[dict[str, Any]] = [
            {"code": "PLANNING_LIMITATION", "severity": "info", "message": item, "evidenceRef": None}
            for item in plan["limitations"]
        ]
        stopped = False
        for query in plan["queries"]:
            if stopped:
                break
            try:
                response, pages = self._fetch(executor, plan["site"], query, budget)
                truncated = bool(pages and pages[-1]["rowsReceived"] == query["rowLimit"] and len(pages) >= query["maxPages"])
                dataset = _normalize_dataset(query, response, search_type=plan["request"]["searchType"], truncated=truncated)
                datasets.append(dataset)
                safe_request, redactions = redact_payload(query["payload"])
                evidence.append({"queryId": query["queryId"], "operationId": QUERY_OPERATION, "requestFingerprint": hashlib.sha256(canonical_json(query["payload"])).hexdigest(), "request": safe_request, "pages": pages, "automaticRetries": 0})
                if redactions:
                    limitations.append({"code": "REQUEST_EVIDENCE_REDACTED", "severity": "info", "message": "Potentially sensitive request values were redacted from report evidence.", "evidenceRef": dataset["datasetId"]})
                if dataset["completeness"] == "top_rows_only":
                    limitations.append({"code": "TOP_ROWS_ONLY", "severity": "warning", "message": "This detailed dataset contains top rows and is not an exhaustive export.", "evidenceRef": dataset["datasetId"]})
                if dataset["completeness"] == "preliminary":
                    limitations.append({"code": "PRELIMINARY_DATA", "severity": "warning", "message": "This dataset includes preliminary data from the provider incomplete marker.", "evidenceRef": dataset["datasetId"]})
                if query["role"] == "availability" and not dataset["rows"]:
                    limitations.append({"code": "SEARCH_CONSOLE_DATA_UNAVAILABLE", "severity": "warning", "message": "The date-only availability probe returned no rows; detail queries were skipped.", "evidenceRef": dataset["datasetId"]})
                    stopped = True
                if query["role"] == "appearance-discovery" and dataset["rows"]:
                    remaining = min(MAX_APPEARANCE_DETAILS, MAX_REQUESTS - budget["requestsUsed"])
                    for index, row in enumerate(dataset["rows"][:remaining]):
                        appearance = row["dimensions"].get("searchAppearance")
                        if not appearance:
                            continue
                        dynamic = _query(f"{query['queryId']}-detail-{index + 1}", "search-appearance", plan["periods"][0], plan["request"], [], row_limit=1, role="appearance-detail")
                        groups = dynamic["payload"].setdefault("dimensionFilterGroups", [{"groupType": "and", "filters": []}])
                        groups[0]["filters"].append({"dimension": "searchAppearance", "operator": "equals", "expression": appearance})
                        detail_response, detail_pages = self._fetch(executor, plan["site"], dynamic, budget)
                        detail = _normalize_dataset(dynamic, detail_response, search_type=plan["request"]["searchType"], truncated=False)
                        detail["appearanceValue"] = appearance
                        datasets.append(detail)
                        safe_dynamic, dynamic_redactions = redact_payload(dynamic["payload"])
                        evidence.append({"queryId": dynamic["queryId"], "operationId": QUERY_OPERATION, "requestFingerprint": hashlib.sha256(canonical_json(dynamic["payload"])).hexdigest(), "request": safe_dynamic, "pages": detail_pages, "automaticRetries": 0, "providerDerivedFilter": True})
                        if dynamic_redactions:
                            limitations.append({"code": "REQUEST_EVIDENCE_REDACTED", "severity": "info", "message": "A provider-derived appearance value was redacted from report evidence.", "evidenceRef": detail["datasetId"]})
            except AdvisorError as exc:
                mapped = _mapped_error(exc)
                if not datasets:
                    raise mapped from exc
                limitations.append({"code": mapped.code, "severity": "warning", "message": mapped.message, "evidenceRef": datasets[-1]["datasetId"] if datasets else None})
                stopped = True
        facts, calculations, interpretations, recommendations, questions = _evidence(datasets, limitations)
        any_rows = any(item["rows"] for item in datasets if item["preset"] != "availability")
        preliminary = any(item["completeness"] == "preliminary" for item in datasets)
        warned = any(item["severity"] == "warning" for item in limitations)
        status = "empty" if not any_rows else "partial" if warned or stopped else "ready"
        quality = "insufficient" if not any_rows else "preliminary" if preliminary else "directional_only" if warned else "reliable_for_description"
        generated_time = self.now().astimezone(timezone.utc)
        report = {
            "schemaVersion": 1, "artifactType": "search-console-report",
            "generatedAt": generated_time.isoformat().replace("+00:00", "Z"),
            "reportId": f"search-console-report-{generated_time.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
            "reportSha256": "", "profileId": plan["profileId"], "site": plan["site"],
            "propertyIdentity": plan["propertyIdentity"], "timezone": "America/Los_Angeles",
            "status": status, "qualityTier": quality, "periods": plan["periods"],
            "datasets": datasets, "queries": evidence,
            "budget": {**plan["budget"], **budget, "requestsRemaining": MAX_REQUESTS - budget["requestsUsed"], "rowsRemaining": MAX_ROWS - budget["rowsUsed"]},
            "facts": facts, "calculations": calculations, "interpretations": interpretations,
            "limitations": limitations, "recommendations": recommendations, "questions": questions,
            "mutationPerformed": False,
        }
        report["reportSha256"] = search_console_report_sha256(report)
        validate_artifact_data("search-console-report", report)
        location = ArtifactStore(Path(plan["projectRoot"])).write_named_artifact("search-console-reports", report["reportId"], report)
        return {"status": status, "report": report, "artifact": location, "mutationPerformed": False}

    def show_plan(self, path: Path) -> dict[str, Any]:
        plan = self._load_plan(path, allow_blocked=True)
        return {"status": "blocked" if plan["blockers"] else "ready", "planSha256": plan["planSha256"], "site": plan["site"], "periods": plan["periods"], "queries": [{"preset": item["preset"], "periodLabel": item["periodLabel"], "dimensions": item["dimensions"], "rowLimit": item["rowLimit"], "maxPages": item["maxPages"]} for item in plan["queries"]], "budget": plan["budget"], "blockers": plan["blockers"], "limitations": plan["limitations"], "mutationPerformed": False}

    def show(self, path: Path, language: str) -> dict[str, Any]:
        report = _load_json(path.resolve(), "Search Console report")
        validate_artifact_data("search-console-report", report, path_label=str(path.resolve()))
        if report["reportSha256"] != search_console_report_sha256(report):
            raise AdvisorError("SEARCH_CONSOLE_REPORT_ARTIFACT_INVALID", "The Search Console report SHA-256 does not match its content.", EXIT_INPUT)
        selected = "en" if language == "auto" else language
        if selected not in {"ru", "en"}:
            selected = "en"
        return {"status": report["status"], "report": report, "plain": render_search_console_report(report, selected), "networkUsed": False, "mutationPerformed": False}
