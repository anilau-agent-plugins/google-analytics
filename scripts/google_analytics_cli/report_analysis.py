"""Normalization, privacy, quality, evidence, and recommendations for GA4 reports."""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .artifact_store import canonical_json
from .report_periods import comparison


EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE = re.compile(r"(?<!\w)\+?[0-9][0-9 ()-]{8,}[0-9](?!\w)")
CREDENTIAL = re.compile(r"(?:ya29\.|1//|GOCSPX-|bearer\s+)[A-Za-z0-9._/-]+", re.I)
INTEGER_TYPES = {"INTEGER", "TYPE_INTEGER", "METRIC_TYPE_INTEGER"}
FLOAT_TYPES = {"FLOAT", "TYPE_FLOAT", "METRIC_TYPE_FLOAT", "SECONDS", "MILLISECONDS", "CURRENCY"}


def redact_text(value: str) -> tuple[str, bool]:
    original = value
    value = EMAIL.sub("[redacted]", value)
    value = PHONE.sub("[redacted]", value)
    value = CREDENTIAL.sub("[redacted]", value)
    if "://" in value:
        try:
            parsed = urlsplit(value)
            if parsed.query or parsed.fragment:
                value = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        except ValueError:
            value = "[redacted]"
    return value, value != original


def redact_payload(value: Any) -> tuple[Any, int]:
    count = 0
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, child in value.items():
            output[key], incoming = redact_payload(child)
            count += incoming
        return output, count
    if isinstance(value, list):
        output_list = []
        for child in value:
            clean, incoming = redact_payload(child)
            output_list.append(clean)
            count += incoming
        return output_list, count
    if isinstance(value, str):
        clean, changed = redact_text(value)
        return clean, int(changed)
    return value, 0


def _typed(raw: str, metric_type: str) -> int | float | str | None:
    if raw == "":
        return None
    try:
        if metric_type in INTEGER_TYPES:
            return int(raw)
        if metric_type in FLOAT_TYPES or metric_type.startswith("TYPE_"):
            return float(raw)
    except ValueError:
        return raw
    return raw


def response_quality(response: dict[str, Any], *, realtime: bool = False, funnel: bool = False, truncated: bool = False, incomplete: bool = False) -> dict[str, Any]:
    metadata = response.get("metadata", {}) if isinstance(response.get("metadata"), dict) else {}
    sampling = []
    for item in metadata.get("samplingMetadatas", []) if isinstance(metadata.get("samplingMetadatas", []), list) else []:
        try:
            read, space = int(item.get("samplesReadCount", 0)), int(item.get("samplingSpaceSize", 0))
        except (TypeError, ValueError):
            read, space = 0, 0
        sampling.append({"samplesReadCount": read, "samplingSpaceSize": space, "ratio": (read / space) if space else None})
    restrictions = []
    schema = metadata.get("schemaRestrictionResponse", {}) if isinstance(metadata.get("schemaRestrictionResponse"), dict) else {}
    for item in schema.get("activeMetricRestrictions", []) if isinstance(schema.get("activeMetricRestrictions", []), list) else []:
        restrictions.append({"metricName": item.get("metricName"), "restrictedMetricTypes": list(item.get("restrictedMetricTypes", []))})
    return {
        "sampled": "unknown" if realtime else bool(sampling),
        "samplingByDateRange": sampling,
        "subjectToThresholding": "unknown" if realtime or funnel else bool(metadata.get("subjectToThresholding", False)),
        "dataLossFromOtherRow": "unknown" if realtime or funnel else bool(metadata.get("dataLossFromOtherRow", False)),
        "schemaRestrictions": restrictions,
        "emptyReason": metadata.get("emptyReason"),
        "quota": response.get("propertyQuota") if isinstance(response.get("propertyQuota"), dict) else None,
        "truncated": truncated,
        "incompletePeriod": incomplete,
        "privacyRedactions": 0,
    }


def normalize_dataset(query: dict[str, Any], response: dict[str, Any], *, truncated: bool = False, incomplete: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source = response
    if query["operationId"] == "data.report.funnel" and isinstance(response.get("funnelTable"), dict):
        source = dict(response["funnelTable"])
        source["propertyQuota"] = response.get("propertyQuota")
    dimension_headers = [str(item.get("name", "")) for item in source.get("dimensionHeaders", [])]
    metric_headers = [(str(item.get("name", "")), str(item.get("type", ""))) for item in source.get("metricHeaders", [])]
    rows = []
    redactions = 0
    for row in source.get("rows", []) if isinstance(source.get("rows", []), list) else []:
        dimensions: dict[str, Any] = {}
        for index, header in enumerate(dimension_headers):
            raw = str((row.get("dimensionValues", [{}]) + [{}] * len(dimension_headers))[index].get("value", ""))
            dimensions[header], changed = redact_text(raw)
            redactions += int(changed)
        metrics: dict[str, Any] = {}
        for index, (header, metric_type) in enumerate(metric_headers):
            raw = str((row.get("metricValues", [{}]) + [{}] * len(metric_headers))[index].get("value", ""))
            metrics[header] = {"raw": raw, "value": _typed(raw, metric_type), "type": metric_type}
        rows.append({"dimensions": dimensions, "metrics": metrics})
    quality = response_quality(
        source, realtime=query["operationId"] == "data.report.realtime",
        funnel=query["operationId"] == "data.report.funnel", truncated=truncated, incomplete=incomplete,
    )
    quality["privacyRedactions"] = redactions
    dataset = {
        "datasetId": f"dataset:{query['queryId']}", "queryId": query["queryId"], "preset": query["preset"],
        "dimensionHeaders": dimension_headers,
        "metricHeaders": [{"name": name, "type": metric_type} for name, metric_type in metric_headers],
        "rows": rows, "totals": source.get("totals", []), "rowCount": int(source.get("rowCount", len(rows))),
        "returnedRows": len(rows), "quality": quality,
    }
    dataset["rowsSha256"] = hashlib.sha256(canonical_json(rows)).hexdigest()
    limitations = quality_limitations(dataset)
    return dataset, limitations


def quality_limitations(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    quality = dataset["quality"]
    query_id = dataset["queryId"]
    items: list[dict[str, Any]] = []
    def add(kind: str, severity: str, message: str) -> None:
        items.append({"type": kind, "severity": severity, "queryId": query_id, "message": message})
    if quality["sampled"] is True:
        add("sampling", "warning", "Google used a sample of events for this dataset; exact totals may differ.")
    if quality["sampled"] == "unknown":
        add("sampling", "info", "This API response does not expose sampling metadata.")
    if quality["subjectToThresholding"] is True:
        add("thresholding", "warning", "This query is subject to privacy thresholds; this does not prove that a particular row was removed.")
    if quality["subjectToThresholding"] == "unknown":
        add("thresholding", "info", "This API response does not expose thresholding metadata.")
    if quality["dataLossFromOtherRow"] is True:
        add("cardinality", "warning", "Some high-cardinality values were grouped into the (other) row.")
    if quality["schemaRestrictions"]:
        add("restricted-metric", "critical", "One or more metrics are restricted; returned zeros must not be treated as business absence.")
    if quality["emptyReason"]:
        add("empty", "warning", f"Google returned no usable rows: {quality['emptyReason']}")
    if quality["truncated"]:
        add("truncation", "warning", "The dataset exceeded the product row/page bound and is incomplete.")
    if quality["incompletePeriod"]:
        add("incomplete-period", "warning", "The current period includes an unfinished property-local day.")
    if quality["privacyRedactions"]:
        add("privacy", "warning", "Potential personal or secret-like values were redacted before storage and display.")
    return items


def small_data_limitations(datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark comparisons that are too small for confident rate or trend advice."""
    items: list[dict[str, Any]] = []
    for dataset in datasets:
        if dataset["preset"] not in {"overview", "key-events"}:
            continue
        metric_names = ("sessions", "activeUsers") if dataset["preset"] == "overview" else ("keyEvents",)
        floor = 100 if dataset["preset"] == "overview" else 20
        observed: list[float] = []
        for row in dataset["rows"]:
            for metric_name in metric_names:
                value = row["metrics"].get(metric_name, {}).get("value")
                if isinstance(value, (int, float)):
                    observed.append(float(value))
                    break
        if observed and min(observed) < floor:
            label = "sessions/users" if dataset["preset"] == "overview" else "key events"
            items.append({
                "type": "small-data", "severity": "warning", "queryId": dataset["queryId"],
                "message": f"At least one compared period has fewer than {floor} {label}; rates and changes are directional only.",
            })
    return items


def overall_quality(datasets: list[dict[str, Any]], limitations: list[dict[str, Any]]) -> str:
    if datasets and all(item["preset"] == "realtime" for item in datasets):
        return "diagnostic_only"
    if not any(item.get("rows") for item in datasets):
        return "insufficient"
    if any(item["preset"] == "funnel-experimental" for item in datasets):
        return "directional_only"
    if any(item.get("severity") in {"warning", "critical"} for item in limitations):
        return "directional_only"
    return "reliable_for_description"


def build_evidence(datasets: list[dict[str, Any]], *, measurement_plan_ref: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    facts: list[dict[str, Any]] = []
    calculations: list[dict[str, Any]] = []
    interpretations: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    questions: list[str] = []
    overview = next((item for item in datasets if item["preset"] == "overview"), None)
    if overview:
        by_period: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(overview["rows"]):
            period = str(row["dimensions"].get("dateRange", index if len(overview["rows"]) > 1 else 0))
            by_period[period] = row
        current = by_period.get("0") or (overview["rows"][0] if overview["rows"] else None)
        previous = by_period.get("1")
        if current:
            for metric, payload in current["metrics"].items():
                fact_id = f"fact:overview:{metric}:current"
                facts.append({"factId": fact_id, "statement": f"Current {metric}: {payload['raw']}", "technicalName": metric, "value": payload["value"], "evidenceRefs": [overview["datasetId"]]})
                if previous and metric in previous["metrics"]:
                    result = comparison(payload["value"] if isinstance(payload["value"], (int, float)) else None, previous["metrics"][metric]["value"] if isinstance(previous["metrics"][metric]["value"], (int, float)) else None)
                    calculations.append({"calculationId": f"calc:period:{metric}", "name": f"Change in {metric}", "formula": "(current-previous)/abs(previous)", "inputs": {"current": payload["value"], "previous": previous["metrics"][metric]["value"]}, "result": result, "evidenceRefs": [overview["datasetId"]]})
    if any(item["preset"] == "realtime" for item in datasets):
        interpretations.append({"interpretationId": "interpretation:realtime", "status": "supported", "statement": "Realtime data is diagnostic only and does not establish durable collection completeness.", "evidenceRefs": [item["datasetId"] for item in datasets if item["preset"] == "realtime"], "caveats": ["Last 30/60 minutes only"]})
    if not measurement_plan_ref:
        questions.append("Which measured events are confirmed business outcomes rather than proxy interactions?")
    else:
        key_dataset = next((item for item in datasets if item["preset"] == "key-events"), None)
        if key_dataset and key_dataset["rows"] and all((row["metrics"].get("keyEvents", {}).get("value") or 0) == 0 for row in key_dataset["rows"]):
            recommendations.append({"priority": 1, "problem": "No key events appeared in the selected data.", "evidenceRefs": [key_dataset["datasetId"]], "expectedBenefit": "Verify whether outcome reporting is technically complete.", "effort": "medium", "risk": "low", "verification": "Compare a controlled completed outcome with DebugView and the authoritative backend record.", "requiresMutationWorkflow": True})
    return facts, calculations, interpretations, recommendations, questions


def quality_recommendation(limitations: list[dict[str, Any]], datasets: list[dict[str, Any]]) -> dict[str, Any] | None:
    affected = [item for item in limitations if item.get("severity") in {"warning", "critical"}]
    if not affected:
        return None
    available = {item["datasetId"] for item in datasets}
    evidence = sorted({f"dataset:{item['queryId']}" for item in affected if f"dataset:{item.get('queryId')}" in available})
    if not evidence:
        return None
    return {"priority": 1, "problem": "The report has data-quality limitations that weaken detailed comparisons.", "evidenceRefs": evidence, "expectedBenefit": "Prevent decisions based on incomplete or restricted evidence.", "effort": "low", "risk": "low", "verification": "Resolve or explicitly accept the listed limitations, then rerun the same periods and presets.", "requiresMutationWorkflow": False}
