"""Deterministic offline GA4/Search Console cross-source analysis."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .artifact_store import ArtifactStore, canonical_json
from .contracts import validate_artifact_data
from .cross_source_policy import (
    DEVICE_VALUES, MAPPING_REVISION, MAX_PAGE_MAPPINGS, MAX_UNMATCHED_SAMPLE,
    date_range_label, metric_value, normalize_ga4_landing, normalize_search_console_page,
    origin_belongs_to_site, safe_origin, source_filter_is_closed, variant_key,
)
from .cross_source_renderer import render_cross_source_report
from .errors import AdvisorError, EXIT_INPUT
from .report_service import report_sha256
from .search_console_report_service import search_console_report_sha256


ANALYSIS_PRESETS = {
    "overview-handoff": ("google-organic-overview", "overview"),
    "landing-opportunities": ("google-organic-landing", "pages"),
    "device-context": ("google-organic-device", "devices"),
}


def _hash_without(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: child for key, child in value.items() if key != field})).hexdigest()


def cross_source_request_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "contentSha256")


def cross_source_plan_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "planSha256")


def cross_source_report_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "reportSha256")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdvisorError("CROSS_SOURCE_ARTIFACT_INVALID", f"Could not read the {label} artifact.", EXIT_INPUT) from exc
    if not isinstance(value, dict):
        raise AdvisorError("CROSS_SOURCE_ARTIFACT_INVALID", f"The {label} artifact must be a JSON object.", EXIT_INPUT)
    return value


def _inside(root: Path, value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise AdvisorError("CROSS_SOURCE_ARTIFACT_INVALID", f"{label} must be an absolute path.", EXIT_INPUT)
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise AdvisorError("CROSS_SOURCE_ARTIFACT_INVALID", f"{label} must stay inside the selected project.", EXIT_INPUT)
    return resolved


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _period_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("label")): item for item in report.get("periods", []) if isinstance(item, dict)}


def _dataset(report: dict[str, Any], preset: str, period: str | None = None) -> list[dict[str, Any]]:
    result = [item for item in report.get("datasets", []) if item.get("preset") == preset]
    if period is not None:
        result = [item for item in result if item.get("periodLabel") == period]
    return result


def _metric_bundle(row: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: metric_value(row, name) for name in names}


def _change(current: float | int | None, previous: float | int | None) -> dict[str, Any] | None:
    if current is None or previous is None:
        return None
    absolute = current - previous
    return {
        "current": current, "previous": previous, "absolute": absolute,
        "relative": None if previous == 0 else absolute / abs(previous),
        "state": "from-zero" if previous == 0 and current != 0 else "unchanged-zero" if previous == 0 else "comparable",
    }


def _direction(change: dict[str, Any] | None) -> str | None:
    if not change:
        return None
    value = change["absolute"]
    return "up" if value > 0 else "down" if value < 0 else "flat"


def _source_ref(root: Path, ref: dict[str, Any], artifact_type: str) -> tuple[Path, dict[str, Any]]:
    path = _inside(root, ref["path"], f"{artifact_type} path")
    if _file_sha(path) != ref["fileSha256"]:
        raise AdvisorError("CROSS_SOURCE_SOURCE_TAMPERED", f"The {artifact_type} file hash changed.", EXIT_INPUT)
    artifact = _load_json(path, artifact_type)
    validate_artifact_data(artifact_type, artifact, path_label=str(path))
    internal = report_sha256(artifact) if artifact_type == "report" else search_console_report_sha256(artifact)
    if artifact.get("reportSha256") != internal or ref["artifactSha256"] != internal:
        raise AdvisorError("CROSS_SOURCE_SOURCE_TAMPERED", f"The {artifact_type} internal hash does not match.", EXIT_INPUT)
    return path, artifact


def _supporting_evidence(root: Path, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    specs = (
        ("baselineRef", "baseline-report"),
        ("measurementPlanRef", "measurement-plan"),
        ("inspectionReportRef", "search-console-inspection-report"),
    )
    result: dict[str, Any] = {}
    authoritative = False
    for field, artifact_type in specs:
        value = request.get(field)
        if not value:
            continue
        path = _inside(root, value, field)
        artifact = _load_json(path, field)
        validate_artifact_data(artifact_type, artifact, path_label=str(path))
        if artifact.get("profileId") not in {None, request["profileId"]}:
            raise AdvisorError("CROSS_SOURCE_CONTEXT_DRIFT", f"{field} belongs to another profile.", EXIT_INPUT)
        if artifact.get("property") not in {None, request["property"]}:
            raise AdvisorError("CROSS_SOURCE_CONTEXT_DRIFT", f"{field} belongs to another GA4 property.", EXIT_INPUT)
        if artifact_type == "search-console-inspection-report" and artifact.get("site") != request["site"]:
            raise AdvisorError("CROSS_SOURCE_CONTEXT_DRIFT", "inspectionReportRef belongs to another Search Console property.", EXIT_INPUT)
        if artifact_type == "measurement-plan":
            if artifact.get("schemaVersion") != 2 or artifact.get("status") != "approved":
                raise AdvisorError("CROSS_SOURCE_CONTEXT_DRIFT", "measurementPlanRef must be an approved measurement-plan v2.", EXIT_INPUT)
            authoritative = any(
                item.get("confirmed") is True and item.get("sourceOfTruth") not in {None, "unknown", "browser", "gtm"}
                for item in artifact.get("outcomes", [])
            )
        result[field] = {"path": str(path), "fileSha256": _file_sha(path), "artifactType": artifact_type}
    return result, authoritative


def _source_identity(request: dict[str, Any], ga4: dict[str, Any], search: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if ga4.get("profileId") != request["profileId"] or search.get("profileId") != request["profileId"]:
        blockers.append("The source reports do not use the requested authorization profile.")
    if ga4.get("property") != request["property"]:
        blockers.append("The GA4 report belongs to another property.")
    if search.get("site") != request["site"]:
        blockers.append("The Search Console report belongs to another exact property identity.")
    stream = ga4.get("propertyContext", {}).get("webStream") or {}
    if stream.get("name") != request["webStream"] or stream.get("defaultOrigin") != request["verifiedOrigin"]:
        blockers.append("The GA4 report was not produced for the requested web stream and verified origin.")
    if not origin_belongs_to_site(request["verifiedOrigin"], request["site"]):
        blockers.append("The verified web-stream origin does not belong to the selected Search Console property.")
    return blockers


def _period_blockers(request: dict[str, Any], ga4: dict[str, Any], search: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    expected = request["expectedPeriods"]
    for label in ("current", "previous"):
        wanted = expected[label]
        for source_name, periods in (("GA4", _period_map(ga4)), ("Search Console", _period_map(search))):
            actual = periods.get(label)
            if not actual or actual.get("from") != wanted["from"] or actual.get("to") != wanted["to"]:
                blockers.append(f"{source_name} {label} date labels do not match the request.")
            elif not actual.get("complete"):
                blockers.append(f"{source_name} {label} period is incomplete.")
    return blockers


class CrossSourceService:
    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _load_request(self, path: Path) -> dict[str, Any]:
        request = _load_json(path.resolve(), "cross-source request")
        validate_artifact_data("cross-source-analysis-request", request, path_label=str(path.resolve()))
        if request["contentSha256"] != cross_source_request_sha256(request):
            raise AdvisorError("CROSS_SOURCE_REQUEST_TAMPERED", "The cross-source request SHA-256 does not match its content.", EXIT_INPUT)
        return request

    def plan(self, request_path: Path) -> dict[str, Any]:
        request = self._load_request(request_path)
        root = Path(request["projectRoot"]).expanduser().resolve()
        if not root.is_dir():
            raise AdvisorError("CROSS_SOURCE_ARTIFACT_INVALID", "The selected project root does not exist.", EXIT_INPUT)
        _, ga4 = _source_ref(root, request["ga4Report"], "report")
        _, search = _source_ref(root, request["searchConsoleReport"], "search-console-report")
        supporting, authoritative_outcomes = _supporting_evidence(root, request)
        blockers = _source_identity(request, ga4, search) + _period_blockers(request, ga4, search)
        if ga4.get("status") in {"blocked", "failed"} or search.get("status") == "failed":
            blockers.append("At least one source report is not usable for cross-source analysis.")
        origin = safe_origin(request["verifiedOrigin"])
        if origin != request["verifiedOrigin"]:
            blockers.append("verifiedOrigin is not the canonical safe web-stream origin.")
        required: list[str] = []
        for analysis in request["analyses"]:
            ga_preset, sc_preset = ANALYSIS_PRESETS[analysis]
            required.extend((f"ga4:{ga_preset}", f"search-console:{sc_preset}"))
            if not _dataset(ga4, ga_preset):
                blockers.append(f"The GA4 source report is missing preset {ga_preset}.")
            if not _dataset(search, sc_preset):
                blockers.append(f"The Search Console source report is missing preset {sc_preset}.")
        stream_id = request["webStream"].rsplit("/", 1)[-1]
        for preset in {ANALYSIS_PRESETS[item][0] for item in request["analyses"]}:
            query = next((item for item in ga4.get("queries", []) if item.get("preset") == preset), None)
            if not query or not source_filter_is_closed(query, stream_id):
                blockers.append(f"GA4 preset {preset} does not preserve the exact stream/google/organic filters.")
        if search.get("schemaVersion") != 2 and "landing-opportunities" in request["analyses"]:
            blockers.append("Landing-page analysis requires Search Console report schema v2 query-ambiguity metadata.")
        for dataset in search.get("datasets", []):
            if dataset.get("preset") in {ANALYSIS_PRESETS[item][1] for item in request["analyses"]}:
                if dataset.get("searchType") != "web" or dataset.get("dataState") != "final":
                    blockers.append("Cross-source Search Console datasets must use searchType=web and dataState=final.")
        ga_tz = str(ga4.get("timezone") or "")
        boundary = "exact" if ga_tz == "America/Los_Angeles" else "date_labels_only"
        limitations: list[dict[str, Any]] = []
        if boundary != "exact":
            limitations.append({"code": "TIMEZONE_BOUNDARIES_DIFFER", "source": "cross-source", "severity": "warning", "message": "GA4 property days and Search Console Pacific days have different absolute boundaries."})
        limitations.extend({"code": "GA4_SOURCE_LIMITATION", "source": "ga4", "severity": item.get("severity", "info"), "message": item.get("message", str(item))} for item in ga4.get("limitations", []))
        limitations.extend({"code": item.get("code", "SEARCH_CONSOLE_SOURCE_LIMITATION"), "source": "search-console", "severity": item.get("severity", "info"), "message": item.get("message", str(item))} for item in search.get("limitations", []))
        generated = self.now().astimezone(timezone.utc)
        plan = {
            "schemaVersion": 1, "artifactType": "cross-source-analysis-plan", "generatedAt": generated.isoformat().replace("+00:00", "Z"),
            "planId": f"cross-source-plan-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}", "planSha256": "", "projectRoot": str(root), "request": request,
            "sources": {"ga4": {"reportId": ga4["reportId"], "reportSha256": ga4["reportSha256"], "fileSha256": request["ga4Report"]["fileSha256"], "property": ga4["property"], "webStream": request["webStream"], "timezone": ga_tz, "status": ga4.get("status"), "qualityTier": ga4.get("qualityTier")}, "searchConsole": {"reportId": search["reportId"], "reportSha256": search["reportSha256"], "fileSha256": request["searchConsoleReport"]["fileSha256"], "site": search["site"], "timezone": search["timezone"], "status": search.get("status"), "qualityTier": search.get("qualityTier")}, "supportingEvidence": supporting, "authoritativeOutcomesAvailable": authoritative_outcomes},
            "analyses": request["analyses"], "expectedDatasets": sorted(set(required)), "periods": request["expectedPeriods"], "boundaryAlignment": boundary,
            "mappingPolicy": {"revision": MAPPING_REVISION, "exactOnly": True, "queryStringsStored": False, "fuzzyMatching": False, "clicksEqualSessions": False},
            "rowBounds": {"maxPageMappings": MAX_PAGE_MAPPINGS, "maxUnmatchedSamplePerSource": MAX_UNMATCHED_SAMPLE},
            "blockers": sorted(set(blockers)), "limitations": limitations, "networkUsed": False, "mutationPerformed": False,
        }
        plan["planSha256"] = cross_source_plan_sha256(plan)
        validate_artifact_data("cross-source-analysis-plan", plan)
        location = ArtifactStore(root).write_named_artifact("cross-source-analysis-plans", plan["planId"], plan)
        return {"status": "blocked" if blockers else "ready", "plan": plan, "artifact": location, "networkUsed": False, "mutationPerformed": False}

    def _load_plan(self, path: Path, *, allow_blocked: bool = False) -> dict[str, Any]:
        plan = _load_json(path.resolve(), "cross-source plan")
        validate_artifact_data("cross-source-analysis-plan", plan, path_label=str(path.resolve()))
        if plan["planSha256"] != cross_source_plan_sha256(plan):
            raise AdvisorError("CROSS_SOURCE_PLAN_TAMPERED", "The cross-source plan SHA-256 does not match its content.", EXIT_INPUT)
        if plan["blockers"] and not allow_blocked:
            raise AdvisorError("CROSS_SOURCE_PLAN_BLOCKED", "The cross-source plan has unresolved blockers.", EXIT_INPUT, details={"blockers": plan["blockers"]})
        return plan

    def _fresh_sources(self, plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        root = Path(plan["projectRoot"])
        _, ga4 = _source_ref(root, plan["request"]["ga4Report"], "report")
        _, search = _source_ref(root, plan["request"]["searchConsoleReport"], "search-console-report")
        if ga4["reportSha256"] != plan["sources"]["ga4"]["reportSha256"] or search["reportSha256"] != plan["sources"]["searchConsole"]["reportSha256"]:
            raise AdvisorError("CROSS_SOURCE_SOURCE_TAMPERED", "A source report changed after planning.", EXIT_INPUT)
        supporting, authoritative = _supporting_evidence(root, plan["request"])
        if supporting != plan["sources"].get("supportingEvidence", {}) or authoritative != plan["sources"].get("authoritativeOutcomesAvailable", False):
            raise AdvisorError("CROSS_SOURCE_SOURCE_TAMPERED", "Supporting evidence changed after planning.", EXIT_INPUT)
        return ga4, search

    def _page_mappings(self, ga4: dict[str, Any], search: dict[str, Any], origin: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
        ga_rows: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        for dataset in _dataset(ga4, "google-organic-landing"):
            for row in dataset.get("rows", []):
                ga_rows.append((normalize_ga4_landing(str(row.get("dimensions", {}).get("landingPage", "")), origin), row, date_range_label(row)))
        sc_rows: list[tuple[dict[str, Any], dict[str, Any], str]] = []
        for period in ("current", "previous"):
            for dataset in _dataset(search, "pages", period):
                for row in dataset.get("rows", []):
                    quality = row.get("dimensionQuality", {}).get("page", {})
                    sc_rows.append((normalize_search_console_page(str(row.get("dimensions", {}).get("page", "")), quality, origin), row, period))
        ga_by_key: dict[str, list[int]] = defaultdict(list)
        sc_by_key: dict[str, list[int]] = defaultdict(list)
        for index, (norm, _, _) in enumerate(ga_rows):
            if norm["key"]:
                ga_by_key[norm["key"]].append(index)
        for index, (norm, _, _) in enumerate(sc_rows):
            if norm["key"]:
                sc_by_key[norm["key"]].append(index)
        ga_variant_keys = {variant_key(item[0].get("path")) for item in ga_rows if item[0].get("path")}
        sc_variant_keys = {variant_key(item[0].get("path")) for item in sc_rows if item[0].get("path")}
        mappings: list[dict[str, Any]] = []
        used_ga: set[int] = set()
        used_sc: set[int] = set()
        for key in sorted(set(ga_by_key) | set(sc_by_key)):
            ga_indices, sc_indices = ga_by_key.get(key, []), sc_by_key.get(key, [])
            if len(ga_indices) > 2 or len(sc_indices) > 2:
                state = "canonical_ambiguous"
            elif ga_indices and sc_indices:
                state = "exact"
            else:
                state = "ga4_only" if ga_indices else "search_console_only"
                path_variant = variant_key((ga_rows[ga_indices[0]] if ga_indices else sc_rows[sc_indices[0]])[0].get("path"))
                if (ga_indices and path_variant in sc_variant_keys) or (sc_indices and path_variant in ga_variant_keys):
                    state = "path_variant"
            entry = {"mappingId": f"page:{len(mappings)+1}", "state": state, "key": key, "path": url_path(key), "searchConsole": {}, "ga4": {}, "evidenceRefs": []}
            for index in ga_indices:
                used_ga.add(index)
                _, row, period = ga_rows[index]
                entry["ga4"][period] = _metric_bundle(row, ("sessions", "engagedSessions", "engagementRate", "keyEvents", "sessionKeyEventRate"))
                entry["evidenceRefs"].append("ga4:dataset:query-google-organic-landing")
            for index in sc_indices:
                used_sc.add(index)
                _, row, period = sc_rows[index]
                entry["searchConsole"][period] = _metric_bundle(row, ("clicks", "impressions", "ctr", "position"))
                entry["evidenceRefs"].append(f"search-console:dataset:sc-pages-{period}")
            mappings.append(entry)
        for source, rows, used, opposite in (("ga4", ga_rows, used_ga, sc_variant_keys), ("searchConsole", sc_rows, used_sc, ga_variant_keys)):
            for index, (norm, row, period) in enumerate(rows):
                if index in used:
                    continue
                state = norm["state"]
                if state == "candidate":
                    state = "path_variant" if variant_key(norm.get("path")) in opposite else ("ga4_only" if source == "ga4" else "search_console_only")
                entry = {"mappingId": f"page:{len(mappings)+1}", "state": state, "key": norm.get("key"), "path": norm.get("path"), "reason": norm.get("reason"), "searchConsole": {}, "ga4": {}, "evidenceRefs": []}
                if source == "ga4":
                    entry["ga4"][period] = _metric_bundle(row, ("sessions", "engagedSessions", "engagementRate", "keyEvents", "sessionKeyEventRate"))
                    entry["evidenceRefs"].append("ga4:dataset:query-google-organic-landing")
                else:
                    entry["searchConsole"][period] = _metric_bundle(row, ("clicks", "impressions", "ctr", "position"))
                    entry["evidenceRefs"].append(f"search-console:dataset:sc-pages-{period}")
                mappings.append(entry)
        mappings = mappings[:MAX_PAGE_MAPPINGS]
        return mappings, dict(Counter(item["state"] for item in mappings))

    def _device_mappings(self, ga4: dict[str, Any], search: dict[str, Any]) -> list[dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for dataset in _dataset(ga4, "google-organic-device"):
            for row in dataset.get("rows", []):
                device = str(row.get("dimensions", {}).get("deviceCategory", "")).casefold()
                key = device if device in DEVICE_VALUES else f"unmapped:{device or 'empty'}"
                item = result.setdefault(key, {"device": key, "ga4": {}, "searchConsole": {}, "evidenceRefs": []})
                item["ga4"][date_range_label(row)] = _metric_bundle(row, ("sessions", "engagedSessions", "engagementRate", "keyEvents", "sessionKeyEventRate"))
                item["evidenceRefs"].append("ga4:dataset:query-google-organic-device")
        for period in ("current", "previous"):
            for dataset in _dataset(search, "devices", period):
                for row in dataset.get("rows", []):
                    device = str(row.get("dimensions", {}).get("device", "")).casefold()
                    key = device if device in DEVICE_VALUES else f"unmapped:{device or 'empty'}"
                    item = result.setdefault(key, {"device": key, "ga4": {}, "searchConsole": {}, "evidenceRefs": []})
                    item["searchConsole"][period] = _metric_bundle(row, ("clicks", "impressions", "ctr", "position"))
                    item["evidenceRefs"].append(f"search-console:dataset:sc-devices-{period}")
        return [result[key] for key in sorted(result)]

    def run(self, plan_path: Path) -> dict[str, Any]:
        plan = self._load_plan(plan_path)
        ga4, search = self._fresh_sources(plan)
        pages, counts = self._page_mappings(ga4, search, plan["request"]["verifiedOrigin"]) if "landing-opportunities" in plan["analyses"] else ([], {})
        devices = self._device_mappings(ga4, search) if "device-context" in plan["analyses"] else []
        facts: list[dict[str, Any]] = []
        calculations: list[dict[str, Any]] = []
        interpretations: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []
        sc_current = next((d for d in _dataset(search, "overview", "current") if not d.get("dimensions") and d.get("rows")), None)
        sc_previous = next((d for d in _dataset(search, "overview", "previous") if not d.get("dimensions") and d.get("rows")), None)
        ga_overview = next((d for d in _dataset(ga4, "google-organic-overview") if d.get("rows")), None)
        sc_click_change = None
        ga_session_change = None
        if sc_current:
            row = sc_current["rows"][0]
            for metric in ("impressions", "clicks", "ctr", "position"):
                value = metric_value(row, metric)
                if value is not None:
                    facts.append({"factId": f"fact:search-console:{metric}", "source": "search-console", "statement": f"Current Search Console {metric}: {value}", "value": value, "evidenceRefs": [f"search-console:{sc_current['datasetId']}"]})
                    if sc_previous:
                        result = _change(value, metric_value(sc_previous["rows"][0], metric))
                        if result:
                            calculations.append({"calculationId": f"calculation:search-console:{metric}", "source": "search-console", "name": f"Search Console {metric} change", "result": result, "evidenceRefs": [f"search-console:{sc_current['datasetId']}", f"search-console:{sc_previous['datasetId']}"]})
                            if metric == "clicks":
                                sc_click_change = result
        if ga_overview:
            current = next((row for row in ga_overview["rows"] if date_range_label(row) == "current"), ga_overview["rows"][0])
            previous = next((row for row in ga_overview["rows"] if date_range_label(row) == "previous"), None)
            for metric in ("sessions", "engagedSessions", "engagementRate", "keyEvents", "sessionKeyEventRate", "totalRevenue"):
                value = metric_value(current, metric)
                if value is not None:
                    facts.append({"factId": f"fact:ga4:{metric}", "source": "ga4", "statement": f"Current GA4 google / organic {metric}: {value}", "value": value, "evidenceRefs": [f"ga4:{ga_overview['datasetId']}"]})
                    result = _change(value, metric_value(previous, metric)) if previous else None
                    if result:
                        calculations.append({"calculationId": f"calculation:ga4:{metric}", "source": "ga4", "name": f"GA4 google / organic {metric} change", "result": result, "evidenceRefs": [f"ga4:{ga_overview['datasetId']}"]})
                        if metric == "sessions":
                            ga_session_change = result
        if _direction(sc_click_change) and _direction(ga_session_change) and _direction(sc_click_change) != _direction(ga_session_change):
            finding = {"findingId": "finding:source-directions-diverge", "category": "measurement", "statement": "Search Console clicks and GA4 google / organic sessions moved in different directions; this is a diagnostic signal, not proof of a cause.", "confidence": "directional", "evidenceRefs": ["calculation:search-console:clicks", "calculation:ga4:sessions"]}
            findings.append(finding)
            recommendations.append({"priority": 1, "problem": "Search visibility/click evidence and measured on-site sessions moved differently.", "category": "measurement", "expectedBenefit": "Checking tags, consent, redirects, attribution, and timezone boundaries can identify whether measurement coverage changed.", "effort": "medium", "risk": "low", "verification": "Repeat both finalized source reports for matching date labels after diagnostics.", "requiresMutationWorkflow": False, "evidenceRefs": finding["evidenceRefs"]})
        if pages:
            exact = counts.get("exact", 0)
            interpretations.append({"interpretationId": "interpretation:mapping", "source": "cross-source", "statement": f"{exact} page mappings were exact; clicks and sessions remain separate source-specific measures.", "confidence": "descriptive" if exact else "limited", "evidenceRefs": ["cross-source:mapping-summary"]})
            candidates = [item for item in pages if item["state"] == "exact" and (item.get("searchConsole", {}).get("current", {}).get("impressions") or 0) >= 100 and (item.get("searchConsole", {}).get("current", {}).get("ctr") or 0) < 0.02]
            if candidates:
                finding = {"findingId": "finding:low-ctr-review", "category": "content", "statement": f"{len(candidates)} exactly matched pages meet the local 100-impression and below-2%-CTR review heuristic.", "confidence": "directional", "evidenceRefs": [item["mappingId"] for item in candidates[:10]]}
                findings.append(finding)
                recommendations.append({"priority": 1, "problem": "Some visible pages have low click-through rates under the local review heuristic.", "category": "content", "expectedBenefit": "Better alignment of title, snippet and search intent may improve qualified clicks; no result is guaranteed.", "effort": "medium", "risk": "low", "verification": "Compare Search Console CTR for the same finalized period after changes.", "requiresMutationWorkflow": True, "evidenceRefs": finding["evidenceRefs"]})
            measurement_gaps = [item for item in pages if item["state"] == "exact" and (item.get("searchConsole", {}).get("current", {}).get("clicks") or 0) > 0 and not (item.get("ga4", {}).get("current", {}).get("sessions") or 0)]
            if measurement_gaps:
                finding = {"findingId": "finding:clicks-without-measured-sessions", "category": "measurement", "statement": f"{len(measurement_gaps)} exactly mapped pages have Search Console clicks but no measured GA4 sessions in the bounded source rows.", "confidence": "directional", "evidenceRefs": [item["mappingId"] for item in measurement_gaps[:10]]}
                findings.append(finding)
                recommendations.append({"priority": len(recommendations) + 1, "problem": "Some clicked pages have no corresponding measured GA4 sessions in the bounded evidence.", "category": "measurement", "expectedBenefit": "A focused tag, consent, redirect, and landing-page check may improve measurement confidence.", "effort": "medium", "risk": "low", "verification": "Validate collection locally and compare fresh finalized source reports; do not interpret the difference as lost visits.", "requiresMutationWorkflow": True, "evidenceRefs": finding["evidenceRefs"]})
        ambiguous = sum(value for key, value in counts.items() if key not in {"exact"})
        if ambiguous:
            findings.append({"findingId": "finding:url-mapping", "category": "data_quality", "statement": f"{ambiguous} page rows were not automatically joined because exact safe mapping was unavailable.", "confidence": "high", "evidenceRefs": ["cross-source:mapping-summary"]})
            recommendations.append({"priority": len(recommendations) + 1, "problem": "Some landing-page evidence is unmatched or ambiguous.", "category": "measurement", "expectedBenefit": "Checking redirects, canonicals, consent and tag coverage can make later comparisons more trustworthy.", "effort": "medium", "risk": "low", "verification": "Create fresh source reports and compare the exact-mapping share.", "requiresMutationWorkflow": True, "evidenceRefs": ["cross-source:mapping-summary"]})
        if devices:
            interpretations.append({"interpretationId": "interpretation:device-context", "source": "cross-source", "statement": f"Device context is available for {len(devices)} normalized device values; source-specific shares remain separate and do not prove causality.", "confidence": "descriptive", "evidenceRefs": ["cross-source:device-mappings"]})
        interpretations.append({"interpretationId": "interpretation:metric-boundary", "source": "cross-source", "statement": "Search Console clicks and GA4 sessions use different definitions; their difference is diagnostic, not a count of lost visits.", "confidence": "high", "evidenceRefs": ["cross-source:source-contracts"]})
        for index, item in enumerate(recommendations[:5], start=1):
            item["priority"] = index
        limitations = list(plan["limitations"])
        limitations.append({"code": "METRIC_DEFINITIONS_DIFFER", "source": "cross-source", "severity": "info", "message": "Search Console clicks are not GA4 sessions, and GA4 google / organic does not identify a specific Search Console search surface."})
        quality = "insufficient" if not facts and not pages and not devices else "directional_only" if limitations or ambiguous else "reliable_for_description"
        status = "empty" if quality == "insufficient" else "partial" if quality == "directional_only" else "ready"
        generated = self.now().astimezone(timezone.utc)
        report = {
            "schemaVersion": 1, "artifactType": "cross-source-analysis-report", "generatedAt": generated.isoformat().replace("+00:00", "Z"),
            "reportId": f"cross-source-report-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}", "reportSha256": "", "status": status, "qualityTier": quality,
            "sources": plan["sources"], "periods": plan["periods"], "boundaryAlignment": plan["boundaryAlignment"],
            "mappingSummary": {"revision": MAPPING_REVISION, "counts": counts, "total": len(pages), "maxRows": MAX_PAGE_MAPPINGS, "unmatchedSampleBound": MAX_UNMATCHED_SAMPLE},
            "pageMappings": pages, "deviceMappings": devices, "facts": facts, "calculations": calculations, "interpretations": interpretations, "findings": findings,
            "limitations": limitations, "recommendations": recommendations[:5], "questions": [] if facts else ["Are finalized Search Console and GA4 data normally available for this site and period?"],
            "safeNextStep": "Review the highest-priority evidence-backed recommendation; any GA4, Search Console, GTM or website change requires its own plan and confirmation.",
            "networkUsed": False, "mutationPerformed": False,
        }
        report["reportSha256"] = cross_source_report_sha256(report)
        validate_artifact_data("cross-source-analysis-report", report)
        location = ArtifactStore(Path(plan["projectRoot"])).write_named_artifact("cross-source-analysis-reports", report["reportId"], report)
        return {"status": status, "report": report, "artifact": location, "networkUsed": False, "mutationPerformed": False}

    def show_plan(self, path: Path) -> dict[str, Any]:
        plan = self._load_plan(path, allow_blocked=True)
        return {"status": "blocked" if plan["blockers"] else "ready", "planSha256": plan["planSha256"], "sources": plan["sources"], "periods": plan["periods"], "analyses": plan["analyses"], "boundaryAlignment": plan["boundaryAlignment"], "mappingPolicy": plan["mappingPolicy"], "blockers": plan["blockers"], "limitations": plan["limitations"], "networkUsed": False, "mutationPerformed": False}

    def show(self, path: Path, language: str) -> dict[str, Any]:
        report = _load_json(path.resolve(), "cross-source report")
        validate_artifact_data("cross-source-analysis-report", report, path_label=str(path.resolve()))
        if report["reportSha256"] != cross_source_report_sha256(report):
            raise AdvisorError("CROSS_SOURCE_REPORT_TAMPERED", "The cross-source report SHA-256 does not match its content.", EXIT_INPUT)
        selected = "en" if language == "auto" else language
        if selected not in {"ru", "en"}:
            selected = "en"
        return {"status": report["status"], "report": report, "plain": render_cross_source_report(report, selected), "networkUsed": False, "mutationPerformed": False}


def url_path(key: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(key).path or "/"
