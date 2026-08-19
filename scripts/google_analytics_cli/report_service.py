"""Immutable, bounded, read-only GA4 reporting workflow."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .artifact_store import ArtifactStore, canonical_json, utc_now
from .auth import AuthService
from .contracts import validate_artifact_data
from .errors import AdvisorError, EXIT_CONFIGURATION, EXIT_INPUT
from .measurement_policy import pii_issues
from .pagination import collect_offsets
from .read_operation import ReadExecutor
from .report_analysis import build_evidence, normalize_dataset, overall_quality, quality_recommendation, redact_payload, small_data_limitations
from .report_periods import resolve_periods
from .report_renderer import render_report
from .report_templates import CORE_TEMPLATES, DISCOVERY_REVISION, REALTIME_DIMENSIONS, REALTIME_METRICS, custom_template, supported_catalog


def _hash_without(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: child for key, child in value.items() if key != field})).hexdigest()


def report_request_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "contentSha256")


def report_plan_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "planSha256")


def report_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "reportSha256")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdvisorError("REPORT_ARTIFACT_INVALID", f"Could not read the {label} artifact.", EXIT_INPUT) from exc
    if not isinstance(value, dict):
        raise AdvisorError("REPORT_ARTIFACT_INVALID", f"The {label} artifact must be a JSON object.", EXIT_INPUT)
    return value


def _absolute(path_value: str | None, root: Path, label: str) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise AdvisorError("REPORT_ARTIFACT_INVALID", f"{label} must be an absolute path.", EXIT_INPUT)
    resolved = path.resolve()
    if root != resolved and root not in resolved.parents:
        raise AdvisorError("REPORT_ARTIFACT_INVALID", f"{label} must stay inside the selected project.", EXIT_INPUT)
    return resolved


def _compatibility_ok(response: dict[str, Any], dimensions: list[str], metrics: list[str]) -> tuple[bool, list[str]]:
    problems: list[str] = []
    dimension_results = response.get("dimensionCompatibilities", [])
    metric_results = response.get("metricCompatibilities", [])
    found_dimensions = {item.get("dimensionMetadata", {}).get("apiName") for item in dimension_results if isinstance(item, dict)}
    found_metrics = {item.get("metricMetadata", {}).get("apiName") for item in metric_results if isinstance(item, dict)}
    for item in dimension_results:
        if item.get("compatibility") != "COMPATIBLE":
            problems.append(str(item.get("dimensionMetadata", {}).get("apiName", "unknown dimension")))
    for item in metric_results:
        if item.get("compatibility") != "COMPATIBLE":
            problems.append(str(item.get("metricMetadata", {}).get("apiName", "unknown metric")))
    problems.extend(item for item in dimensions if item not in found_dimensions)
    problems.extend(item for item in metrics if item not in found_metrics)
    return not problems, sorted(set(problems))


def _quota_low(response: dict[str, Any]) -> bool:
    quota = response.get("propertyQuota")
    if not isinstance(quota, dict):
        return False
    for key in ("tokensPerHour", "tokensPerProjectPerHour"):
        status = quota.get(key)
        if isinstance(status, dict):
            try:
                if int(status.get("remaining", 100)) < 100:
                    return True
            except (TypeError, ValueError):
                continue
    return False


class ReportService:
    def __init__(
        self, *, auth: AuthService | None = None, transport: Any = None,
        env: dict[str, str] | None = None, now: Callable[[], datetime] | None = None,
    ) -> None:
        self.auth = auth or AuthService()
        self.transport = transport
        self.env = env if env is not None else os.environ
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _executor(self, token: str) -> ReadExecutor:
        return ReadExecutor(token, transport=self.transport)

    def catalog(self, profile_id: str, property_name: str) -> dict[str, Any]:
        selected, token, _ = self.auth.access_token(profile_id)
        if selected != profile_id:
            raise AdvisorError("PROFILE_MISMATCH", "The authorization profile changed.", EXIT_CONFIGURATION)
        executor = self._executor(token)
        metadata_response = executor.execute("data.metadata.get", resource=property_name)
        metadata = metadata_response.data or {}
        dimensions = [item.get("apiName") for item in metadata.get("dimensions", []) if isinstance(item, dict) and item.get("apiName")]
        metrics = [item.get("apiName") for item in metadata.get("metrics", []) if isinstance(item, dict) and item.get("apiName")]
        restricted = [{"apiName": item.get("apiName"), "blockedReasons": item.get("blockedReasons", [])} for item in metadata.get("metrics", []) if isinstance(item, dict) and item.get("blockedReasons")]
        return {"profileId": selected, "property": property_name, "catalog": supported_catalog(), "availableDimensions": sorted(dimensions), "availableMetrics": sorted(metrics), "restrictedMetrics": restricted, "requestId": metadata_response.request_id, "mutationPerformed": False}

    def _load_request(self, path: Path) -> dict[str, Any]:
        request = _load_json(path.resolve(), "report request")
        validate_artifact_data("report-request", request, path_label=str(path.resolve()))
        if request["contentSha256"] != report_request_sha256(request):
            raise AdvisorError("REPORT_REQUEST_TAMPERED", "The report request SHA-256 does not match its content.", EXIT_INPUT)
        issues = pii_issues(request)
        if issues:
            raise AdvisorError("REPORT_PRIVACY_REDACTED", "The report request contains personal data or PII-bearing fields.", EXIT_INPUT, details={"issues": issues})
        _, redactions = redact_payload(request)
        if redactions:
            raise AdvisorError("REPORT_PRIVACY_REDACTED", "The report request contains personal or secret-like values.", EXIT_INPUT)
        return request

    def _project_evidence(self, request: dict[str, Any], root: Path) -> tuple[str | None, str | None, dict[str, Any] | None]:
        baseline_path = _absolute(request.get("baselineRef"), root, "baselineRef")
        measurement_path = _absolute(request.get("measurementPlanRef"), root, "measurementPlanRef")
        if baseline_path:
            baseline = _load_json(baseline_path, "baseline report")
            validate_artifact_data("baseline-report", baseline, path_label=str(baseline_path))
        measurement = None
        if measurement_path:
            measurement = _load_json(measurement_path, "measurement plan")
            validate_artifact_data("measurement-plan", measurement, path_label=str(measurement_path))
            if measurement.get("schemaVersion") != 2 or measurement.get("status") != "approved":
                raise AdvisorError("REPORT_MEASUREMENT_PLAN_REQUIRED", "Reporting business meaning requires an approved measurement-plan v2.", EXIT_INPUT)
            if measurement.get("property") not in {None, request["property"]}:
                raise AdvisorError("REPORT_CONTEXT_DRIFT", "The measurement plan belongs to another GA4 property.", EXIT_INPUT)
        return str(baseline_path) if baseline_path else None, str(measurement_path) if measurement_path else None, measurement

    def plan(self, profile_id: str, property_name: str, request_path: Path) -> dict[str, Any]:
        request = self._load_request(request_path)
        root = Path(request["projectRoot"]).expanduser().resolve()
        if not root.is_dir():
            raise AdvisorError("REPORT_ARTIFACT_INVALID", "The report project root does not exist.", EXIT_INPUT)
        if request["profileId"] != profile_id or request["property"] != property_name:
            raise AdvisorError("REPORT_CONTEXT_DRIFT", "The CLI selection does not match the immutable report request.", EXIT_INPUT)
        baseline_ref, measurement_ref, measurement = self._project_evidence(request, root)
        selected, token, _ = self.auth.access_token(profile_id)
        if selected != profile_id:
            raise AdvisorError("PROFILE_MISMATCH", "The authorization profile changed.", EXIT_CONFIGURATION)
        executor = self._executor(token)
        property_response = executor.execute("admin.property.get", resource=property_name)
        property_data = property_response.data or {}
        property_timezone = str(property_data.get("timeZone") or "")
        if not property_timezone:
            raise AdvisorError("REPORT_CONTEXT_DRIFT", "The selected property did not return a timezone.", EXIT_INPUT)
        currency = property_data.get("currencyCode")
        metadata_response = executor.execute("data.metadata.get", resource=property_name)
        metadata = metadata_response.data or {}
        dimensions_meta = {str(item.get("apiName")): item for item in metadata.get("dimensions", []) if isinstance(item, dict) and item.get("apiName")}
        metrics_meta = {str(item.get("apiName")): item for item in metadata.get("metrics", []) if isinstance(item, dict) and item.get("apiName")}
        periods, limitations = resolve_periods(request, property_timezone, now=self.now)
        blockers: list[str] = []
        queries: list[dict[str, Any]] = []
        date_ranges = [{"name": item["label"], "startDate": item["from"], "endDate": item["to"]} for item in periods]

        def add_core(preset: str, template: Any, dimension_filter: dict[str, Any] | None = None) -> None:
            dimensions = [item for item in template.dimensions if item in dimensions_meta]
            metrics = [item for item in template.metrics if item in metrics_meta and not metrics_meta[item].get("blockedReasons")]
            missing_required = [item for item in template.required_dimensions if item not in dimensions] + [item for item in template.required_metrics if item not in metrics]
            unavailable = [item for item in (*template.dimensions, *template.metrics) if item not in (*dimensions, *metrics)]
            if unavailable:
                limitations.append(f"{preset}: unavailable or restricted fields were omitted: {', '.join(sorted(unavailable))}.")
            if missing_required:
                blockers.append(f"{preset}: required fields are unavailable or restricted: {', '.join(sorted(missing_required))}.")
                return
            payload: dict[str, Any] = {"dateRanges": date_ranges, "dimensions": [{"name": item} for item in dimensions], "metrics": [{"name": item} for item in metrics], "metricAggregations": ["TOTAL"], "limit": str(min(250, template.max_rows)), "offset": "0", "returnPropertyQuota": True}
            if dimension_filter:
                payload["dimensionFilter"] = dimension_filter
            compatibility = executor.execute("data.compatibility.check", resource=property_name, payload={key: payload[key] for key in ("dimensions", "metrics", "dimensionFilter") if key in payload}).data or {}
            compatible, problems = _compatibility_ok(compatibility, dimensions, metrics)
            if not compatible:
                blockers.append(f"{preset}: Data API marked fields incompatible or omitted them: {', '.join(problems)}.")
                return
            queries.append({"queryId": f"query-{preset}", "preset": preset, "operationId": "data.report.run", "apiChannel": "v1beta", "payload": payload, "maxPages": min(4, max(1, (template.max_rows + 249) // 250)), "maxRows": template.max_rows, "compatibility": "compatible"})

        for preset in request["presets"]:
            if preset in CORE_TEMPLATES:
                if preset == "ecommerce" and (measurement is None or not measurement.get("ecommerce", {}).get("enabled")):
                    blockers.append("ecommerce: an approved ecommerce measurement plan is required.")
                else:
                    add_core(preset, CORE_TEMPLATES[preset])
            elif preset == "custom-core":
                if not request.get("customCore"):
                    blockers.append("custom-core: customCore configuration is required.")
                else:
                    template, filter_value = custom_template(request["customCore"])
                    add_core(preset, template, filter_value)
            elif preset == "realtime":
                dimensions = ["eventName"] if "eventName" in REALTIME_DIMENSIONS else []
                metrics = [item for item in ("activeUsers", "eventCount") if item in REALTIME_METRICS]
                queries.append({"queryId": "query-realtime", "preset": preset, "operationId": "data.report.realtime", "apiChannel": "v1beta", "payload": {"dimensions": [{"name": item} for item in dimensions], "metrics": [{"name": item} for item in metrics], "minuteRanges": [{"name": "last-30-minutes", "startMinutesAgo": 29, "endMinutesAgo": 0}], "limit": "250", "returnPropertyQuota": True}, "maxPages": 1, "maxRows": 250, "compatibility": "closed-registry"})
            elif preset == "funnel-experimental":
                enabled = self.env.get("GOOGLE_ANALYTICS_ADVISOR_EXPERIMENTAL_FUNNELS") == "1"
                funnels = measurement.get("funnels", []) if measurement else []
                ready = next((item for item in funnels if item.get("stage10Ready")), None)
                if not enabled or not request.get("experimentalFunnel") or not request.get("alphaDisclosureAccepted"):
                    blockers.append("funnel-experimental: feature flag and accepted alpha disclosure are required.")
                elif not ready:
                    blockers.append("funnel-experimental: an approved stage10Ready funnel is required.")
                elif len(ready.get("steps", [])) > 8:
                    blockers.append("funnel-experimental: at most 8 approved steps are supported.")
                else:
                    steps = [{"name": name, "filterExpression": {"funnelFieldFilter": {"fieldName": "eventName", "stringFilter": {"matchType": "EXACT", "value": name, "caseSensitive": True}}}} for name in ready["steps"]]
                    queries.append({"queryId": "query-funnel-experimental", "preset": preset, "operationId": "data.report.funnel", "apiChannel": "v1alpha", "payload": {"dateRanges": date_ranges, "funnel": {"isOpenFunnel": bool(ready.get("open")), "steps": steps}, "limit": "250", "returnPropertyQuota": True}, "maxPages": 1, "maxRows": 250, "compatibility": "experimental-contract"})
        if len(queries) > 24:
            blockers.append("The report suite exceeds the product request bound of 24 queries.")
        generated = self.now().astimezone(timezone.utc)
        metadata_hash = hashlib.sha256(canonical_json(metadata)).hexdigest()
        plan = {
            "schemaVersion": 1, "artifactType": "report-plan", "generatedAt": generated.isoformat().replace("+00:00", "Z"),
            "expiresAt": (generated + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            "planId": f"report-plan-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}", "planSha256": "",
            "projectRoot": str(root), "profileId": profile_id, "property": property_name, "request": request,
            "metadata": {"contentSha256": metadata_hash, "requestId": metadata_response.request_id, "discoveryRevision": DISCOVERY_REVISION, "dimensions": sorted(dimensions_meta), "metrics": sorted(metrics_meta)},
            "propertyContext": {"timeZone": property_timezone, "currencyCode": currency, "baselineRef": baseline_ref, "measurementPlanRef": measurement_ref, "qualityTier": "business-context" if measurement else "descriptive", "language": request["language"]},
            "periods": periods, "queries": queries, "blockers": sorted(set(blockers)), "limitations": sorted(set(limitations)), "networkUsed": True, "mutationPerformed": False,
        }
        plan["planSha256"] = report_plan_sha256(plan)
        validate_artifact_data("report-plan", plan)
        location = ArtifactStore(root).write_named_artifact("report-plans", plan["planId"], plan)
        return {"status": "blocked" if blockers else "ready", "plan": plan, "artifact": location, "mutationPerformed": False}

    def _load_plan(self, path: Path) -> dict[str, Any]:
        plan = _load_json(path.resolve(), "report plan")
        validate_artifact_data("report-plan", plan, path_label=str(path.resolve()))
        if plan["planSha256"] != report_plan_sha256(plan):
            raise AdvisorError("REPORT_PLAN_TAMPERED", "The report plan SHA-256 does not match its content.", EXIT_INPUT)
        if self.now().astimezone(timezone.utc) > datetime.fromisoformat(plan["expiresAt"].replace("Z", "+00:00")):
            raise AdvisorError("REPORT_PLAN_EXPIRED", "The report plan expired; create a fresh plan.", EXIT_INPUT)
        if plan["blockers"]:
            raise AdvisorError("REPORT_PLAN_BLOCKED", "The report plan has unresolved blockers.", EXIT_INPUT, details={"blockers": plan["blockers"]})
        return plan

    def run(self, plan_path: Path) -> dict[str, Any]:
        plan = self._load_plan(plan_path)
        selected, token, _ = self.auth.access_token(plan["profileId"])
        if selected != plan["profileId"]:
            raise AdvisorError("PROFILE_MISMATCH", "The authorization profile changed.", EXIT_CONFIGURATION)
        executor = self._executor(token)
        property_data = executor.execute("admin.property.get", resource=plan["property"]).data or {}
        if property_data.get("timeZone") != plan["propertyContext"]["timeZone"] or property_data.get("currencyCode") != plan["propertyContext"]["currencyCode"]:
            raise AdvisorError("REPORT_CONTEXT_DRIFT", "The property timezone or currency changed after planning.", EXIT_INPUT)
        metadata_response = executor.execute("data.metadata.get", resource=plan["property"])
        if hashlib.sha256(canonical_json(metadata_response.data or {})).hexdigest() != plan["metadata"]["contentSha256"]:
            raise AdvisorError("REPORT_CONTEXT_DRIFT", "The property reporting metadata changed after planning.", EXIT_INPUT)
        datasets: list[dict[str, Any]] = []
        query_evidence: list[dict[str, Any]] = []
        limitations = [{"type": "planning", "severity": "info", "queryId": None, "message": message} for message in plan["limitations"]]
        quota_stopped = False
        for query in plan["queries"]:
            if quota_stopped:
                limitations.append({"type": "quota", "severity": "warning", "queryId": query["queryId"], "message": "This optional query was skipped by the quota safety floor."})
                continue
            payload = deepcopy(query["payload"])
            request_ids: list[str] = []
            if query["operationId"] == "data.report.run":
                def fetch(offset: int, limit: int) -> dict[str, Any]:
                    current_payload = deepcopy(payload)
                    current_payload["offset"], current_payload["limit"] = str(offset), str(limit)
                    response = executor.execute(query["operationId"], resource=plan["property"], payload=current_payload)
                    if response.request_id:
                        request_ids.append(response.request_id)
                    return response.data or {}
                response_data = collect_offsets(fetch, page_size=250, max_pages=query["maxPages"], max_rows=query["maxRows"])
                truncated = bool(response_data.get("truncated"))
            else:
                response = executor.execute(query["operationId"], resource=plan["property"], payload=payload)
                response_data = response.data or {}
                if response.request_id:
                    request_ids.append(response.request_id)
                row_source = response_data.get("funnelTable", response_data) if isinstance(response_data, dict) else {}
                truncated = int(row_source.get("rowCount", len(row_source.get("rows", [])))) > query["maxRows"]
            dataset, incoming = normalize_dataset(query, response_data, truncated=truncated, incomplete=not plan["periods"][0]["complete"])
            datasets.append(dataset)
            limitations.extend(incoming)
            clean_payload, redactions = redact_payload(payload)
            if redactions:
                limitations.append({"type": "privacy", "severity": "warning", "queryId": query["queryId"], "message": "Potential personal or secret-like request values were redacted from evidence."})
            query_evidence.append({"queryId": query["queryId"], "preset": query["preset"], "provider": "analytics-data", "method": query["operationId"], "apiChannel": query["apiChannel"], "requestIds": request_ids, "request": clean_payload, "returnPropertyQuota": True, "responseQuality": dataset["quality"]})
            quota_stopped = _quota_low(response_data)
        limitations.extend(small_data_limitations(datasets))
        facts, calculations, interpretations, recommendations, questions = build_evidence(datasets, measurement_plan_ref=plan["propertyContext"]["measurementPlanRef"])
        quality_item = quality_recommendation(limitations, datasets)
        if quality_item:
            recommendations.insert(0, quality_item)
        for index, item in enumerate(recommendations, start=1):
            item["priority"] = index
        quality_tier = overall_quality(datasets, limitations)
        if not any(dataset["rows"] for dataset in datasets):
            status = "empty"
        elif any(item.get("severity") in {"warning", "critical"} for item in limitations):
            status = "partial"
        else:
            status = "ready"
        generated_time = self.now()
        generated = generated_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        report = {
            "schemaVersion": 2, "artifactType": "report", "generatedAt": generated,
            "reportId": f"report-{generated_time.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}", "reportSha256": "",
            "profileId": plan["profileId"], "property": plan["property"], "timezone": plan["propertyContext"]["timeZone"], "currency": plan["propertyContext"]["currencyCode"],
            "status": status, "qualityTier": quality_tier, "periods": plan["periods"], "propertyContext": plan["propertyContext"],
            "datasets": datasets, "queries": query_evidence, "facts": facts, "calculations": calculations,
            "interpretations": interpretations, "limitations": limitations, "recommendations": recommendations[:5], "questions": questions,
        }
        report["reportSha256"] = report_sha256(report)
        validate_artifact_data("report", report)
        location = ArtifactStore(Path(plan["projectRoot"])).write_named_artifact("reports", report["reportId"], report)
        return {"status": status, "report": report, "artifact": location, "mutationPerformed": False}

    def show_plan(self, path: Path) -> dict[str, Any]:
        plan = self._load_plan(path)
        return {"status": "blocked" if plan["blockers"] else "ready", "planSha256": plan["planSha256"], "property": plan["property"], "periods": plan["periods"], "queries": [{"preset": item["preset"], "apiChannel": item["apiChannel"], "maxRows": item["maxRows"]} for item in plan["queries"]], "blockers": plan["blockers"], "limitations": plan["limitations"], "mutationPerformed": False}

    def show(self, path: Path, language: str) -> dict[str, Any]:
        report = _load_json(path.resolve(), "report")
        validate_artifact_data("report", report, path_label=str(path.resolve()))
        if report.get("schemaVersion") == 2 and report.get("reportSha256") != report_sha256(report):
            raise AdvisorError("REPORT_ARTIFACT_INVALID", "The report SHA-256 does not match its content.", EXIT_INPUT)
        selected_language = report.get("propertyContext", {}).get("language", "en") if language == "auto" else language
        if selected_language not in {"ru", "en"}:
            selected_language = "en"
        return {"status": report.get("status", "ready"), "report": report, "plain": render_report(report, selected_language), "networkUsed": False, "mutationPerformed": False}
