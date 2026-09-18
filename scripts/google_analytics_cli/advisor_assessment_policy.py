"""Deterministic policy helpers for the resumable full-picture advisor."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_store import canonical_json
from .errors import AdvisorError, EXIT_INPUT


DOMAIN_ORDER = (
    "resource-and-period",
    "business-goal-and-outcomes",
    "tag-and-gtm-route",
    "consent-and-coverage",
    "overview",
    "acquisition",
    "landing-and-content",
    "device-and-geo",
    "events-and-key-events",
    "ecommerce",
    "search-console-performance",
    "cross-source-context",
    "data-quality-and-quota",
    "unanswered-business-questions",
)
DOMAIN_STATES = {"checked", "not_applicable", "unavailable", "blocked", "stale", "not_checked"}
STEP_ORDER = ("baseline", "ga4", "search-console", "cross-source", "synthesis")
RECOMMENDATION_CATEGORIES = {"measurement", "technical-seo", "content", "ux/conversion", "investigation"}


def content_sha256(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: item for key, item in value.items() if key != field})).hexdigest()


def assessment_request_sha256(value: dict[str, Any]) -> str:
    return content_sha256(value, "contentSha256")


def assessment_plan_sha256(value: dict[str, Any]) -> str:
    return content_sha256(value, "planSha256")


def assessment_checkpoint_sha256(value: dict[str, Any]) -> str:
    return content_sha256(value, "checkpointSha256")


def assessment_report_sha256(value: dict[str, Any]) -> str:
    return content_sha256(value, "reportSha256")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdvisorError("ADVISOR_ARTIFACT_INVALID", f"Could not read the {label}.", EXIT_INPUT) from exc
    if not isinstance(value, dict):
        raise AdvisorError("ADVISOR_ARTIFACT_INVALID", f"The {label} must be a JSON object.", EXIT_INPUT)
    return value


def resolve_project_path(root: Path, value: str, label: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise AdvisorError("ADVISOR_ARTIFACT_INVALID", f"{label} must resolve inside the selected project.", EXIT_INPUT) from exc
    if not resolved.is_file():
        raise AdvisorError("ADVISOR_ARTIFACT_INVALID", f"{label} must be a file.", EXIT_INPUT)
    return resolved


def artifact_ref(path: Path, value: dict[str, Any], internal_field: str | None = None) -> dict[str, Any]:
    internal = value.get(internal_field) if internal_field else None
    return {
        "artifactType": value.get("artifactType"),
        "path": str(path.resolve()),
        "fileSha256": file_sha256(path),
        "artifactSha256": internal if isinstance(internal, str) else file_sha256(path),
    }


def domain(domain_id: str, state: str, reason: str, evidence_refs: list[str] | None = None) -> dict[str, Any]:
    if domain_id not in DOMAIN_ORDER or state not in DOMAIN_STATES or not reason:
        raise ValueError("invalid advisor domain state")
    return {"domainId": domain_id, "state": state, "reason": reason, "evidenceRefs": evidence_refs or []}


def initial_domains(*, measurement_ready: bool, ecommerce: bool, search_console: bool, baseline_state: str) -> list[dict[str, Any]]:
    baseline_reason = {
        "planned": "A bounded fresh baseline is planned.",
        "reused": "A matching baseline passed the freshness policy.",
        "stale": "The selected baseline is stale and a fresh baseline is planned.",
        "blocked": "Measurement reliability cannot be checked with the available context.",
    }.get(baseline_state, "Measurement reliability has not been checked.")
    values = {
        "resource-and-period": ("checked", "Exact resources and report periods are fixed by the immutable plan."),
        "business-goal-and-outcomes": ("not_checked" if measurement_ready else "unavailable", "An approved measurement plan is available." if measurement_ready else "No approved measurement plan maps authoritative business outcomes."),
        "tag-and-gtm-route": ("not_checked", baseline_reason),
        "consent-and-coverage": ("not_checked", baseline_reason),
        "overview": ("not_checked", "The GA4 overview source step has not run."),
        "acquisition": ("not_checked", "The GA4 acquisition source step has not run."),
        "landing-and-content": ("not_checked", "Landing and content source steps have not run."),
        "device-and-geo": ("not_checked", "Device and geography source steps have not run."),
        "events-and-key-events": ("not_checked", "Event source steps have not run."),
        "ecommerce": ("not_checked" if ecommerce else "not_applicable", "Approved ecommerce measurement is enabled." if ecommerce else "Ecommerce is not enabled by the approved measurement plan."),
        "search-console-performance": ("not_checked" if search_console else "unavailable", "The exact Search Console source step has not run." if search_console else "No exact readable Search Console property is selected."),
        "cross-source-context": ("not_checked" if search_console else "not_applicable", "Cross-source analysis is pending both exact source reports." if search_console else "Cross-source analysis requires an exact Search Console property."),
        "data-quality-and-quota": ("not_checked", "Source quality and quota evidence are collected during execution."),
        "unanswered-business-questions": ("not_checked", "Remaining questions are determined after evidence collection."),
    }
    return [domain(key, *values[key]) for key in DOMAIN_ORDER]


def update_domain(domains: list[dict[str, Any]], domain_id: str, state: str, reason: str, evidence_refs: list[str] | None = None) -> None:
    replacement = domain(domain_id, state, reason, evidence_refs)
    for index, item in enumerate(domains):
        if item.get("domainId") == domain_id:
            domains[index] = replacement
            return
    raise ValueError(f"unknown domain {domain_id}")


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def recommendation_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("category", "investigation")), " ".join(str(item.get("problem", "")).casefold().split()))


def normalized_recommendation(item: dict[str, Any], source: str) -> dict[str, Any] | None:
    problem = item.get("problem") or item.get("message")
    if not isinstance(problem, str) or not problem.strip():
        return None
    category = str(item.get("category") or ("measurement" if source in {"baseline", "ga4"} else "investigation"))
    if category not in RECOMMENDATION_CATEGORIES:
        category = "investigation"
    refs = item.get("evidenceRefs") if isinstance(item.get("evidenceRefs"), list) else []
    refs = [ref if str(ref).startswith(("ga4:", "search-console:", "cross-source:", "baseline:")) else f"{source}:{ref}" for ref in refs]
    if not refs:
        refs = [f"{source}:recommendation"]
    return {
        "recommendationId": hashlib.sha256(f"{category}:{problem}".encode("utf-8")).hexdigest()[:16],
        "problem": problem.strip(),
        "category": category,
        "evidenceRefs": sorted(set(str(ref) for ref in refs)),
        "expectedBenefit": item.get("expectedBenefit") or "Improve the quality of the next evidence-based decision; no result is guaranteed.",
        "effort": item.get("effort") or "medium",
        "risk": item.get("risk") or "low",
        "verification": item.get("verification") or item.get("safeNextStep") or "Repeat the same bounded checks after the action.",
        "requiresMutationWorkflow": bool(item.get("requiresMutationWorkflow", item.get("mutationRequired", False))),
        "source": source,
    }


def prioritize_recommendations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank = {"measurement": 0, "ux/conversion": 1, "technical-seo": 2, "content": 3, "investigation": 4}
    confidence = {"high": 0, "confirmed": 0, "medium": 1, "directional": 2, "low": 3}
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = recommendation_key(item)
        if key not in unique:
            unique[key] = item
        else:
            unique[key]["evidenceRefs"] = sorted(set(unique[key]["evidenceRefs"] + item["evidenceRefs"]))
    ordered = sorted(unique.values(), key=lambda item: (
        rank.get(str(item.get("category")), 9),
        confidence.get(str(item.get("confidence", "medium")), 1),
        1 if item.get("risk") == "high" else 0,
        str(item.get("recommendationId")),
    ))[:5]
    for index, item in enumerate(ordered, 1):
        item["priority"] = index
    return ordered
