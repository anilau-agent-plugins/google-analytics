"""Fail-closed business, privacy, cardinality, and measurement-plan rules."""

from __future__ import annotations

import re
from typing import Any

from .artifact_store import canonical_json
from .event_catalog import event_name_issues, parameter_name_issues


EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PII_KEYS = re.compile(r"^(?:e-?mail|phone|mobile|full_?name|first_?name|last_?name|address|ssn|passport|latitude|longitude)$", re.I)
PROXY_SOURCES = {"browser", "gtm"}
STRONG_SOURCES = {"backend", "payment", "crm", "application"}
KEY_EVENT_LIMIT = 30
DEFINITION_LIMITS = {"event-dimension": 50, "user-dimension": 25, "event-metric": 50}
REQUIRED_EVENT_PARAMETERS = {"purchase": {"transaction_id", "items"}, "refund": {"transaction_id"}}


def _scan_pii(value: Any, path: str = "$") -> list[str]:
    issues: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if PII_KEYS.fullmatch(str(key)):
                issues.append(f"{path}.{key}: PII-bearing field names are not allowed in a measurement artifact.")
            issues.extend(_scan_pii(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_scan_pii(child, f"{path}[{index}]"))
    elif isinstance(value, str) and EMAIL.search(value):
        issues.append(f"{path}: an email-shaped value is not allowed in a measurement artifact.")
    return issues


def pii_issues(value: Any) -> list[str]:
    return sorted(set(_scan_pii(value)))


def plan_content_sha256(plan: dict[str, Any]) -> str:
    import hashlib

    content = {key: value for key, value in plan.items() if key not in {"contentSha256", "approvedAt", "approvalSha256"}}
    return hashlib.sha256(canonical_json(content)).hexdigest()


def evaluate_plan(plan: dict[str, Any], budgets: dict[str, int] | None = None) -> dict[str, list[str]]:
    blockers = _scan_pii(plan)
    warnings: list[str] = []
    if plan.get("schemaVersion") != 2:
        return {"blockers": blockers, "warnings": warnings}

    outcomes = {item.get("id"): item for item in plan.get("outcomes", []) if isinstance(item, dict)}
    event_names: set[str] = set()
    key_events = 0
    definition_counts = {key: 0 for key in DEFINITION_LIMITS}
    registered: set[tuple[str, str]] = set()
    for event in plan.get("events", []):
        if not isinstance(event, dict):
            continue
        name = str(event.get("name", ""))
        event_class = str(event.get("catalogClass", "custom"))
        blockers.extend(f"events.{name}: {item}" for item in event_name_issues(name, event_class))
        if name in event_names:
            blockers.append(f"events.{name}: duplicate event name.")
        event_names.add(name)
        outcome = outcomes.get(event.get("businessOutcomeId"))
        if outcome is None:
            blockers.append(f"events.{name}: referenced business outcome does not exist.")
        if event_class == "custom" and not event.get("customJustification"):
            blockers.append(f"events.{name}: custom event requires a justification.")
        if not event.get("trigger") or not event.get("verificationChecks"):
            blockers.append(f"events.{name}: exact trigger and at least one verification check are required.")
        if event.get("authoritativeSource") == "unknown" or event.get("collectionOwner") == "unknown":
            blockers.append(f"events.{name}: authoritative source and collection owner must be known.")
        if event.get("keyEventRecommendation"):
            key_events += 1
            if event.get("authoritativeSource") in PROXY_SOURCES and outcome and outcome.get("sourceOfTruth") in STRONG_SOURCES:
                blockers.append(f"events.{name}: a proxy cannot be a key event while a stronger business source is available.")
        parameters = event.get("parameters", [])
        if len(parameters) > 25:
            blockers.append(f"events.{name}: more than 25 event parameters are planned.")
        parameter_names: set[str] = set()
        for parameter in parameters:
            parameter_name = str(parameter.get("name", ""))
            if parameter_name in parameter_names:
                blockers.append(f"events.{name}.parameters.{parameter_name}: duplicate parameter name.")
            parameter_names.add(parameter_name)
            registration = str(parameter.get("registration", "none"))
            blockers.extend(f"events.{name}.parameters.{parameter_name}: {item}" for item in parameter_name_issues(parameter_name, registration))
            if parameter.get("privacy") in {"pii", "sensitive", "unknown"}:
                blockers.append(f"events.{name}.parameters.{parameter_name}: privacy classification is not safe for GA4.")
            if registration != "none":
                if not parameter.get("reportingUse"):
                    blockers.append(f"events.{name}.parameters.{parameter_name}: custom registration needs a reporting use case.")
                if parameter.get("cardinality") in {"high", "unique", "unknown"}:
                    blockers.append(f"events.{name}.parameters.{parameter_name}: high, unique, or unknown cardinality cannot be registered.")
                key = (parameter_name, registration)
                if key not in registered and registration in definition_counts:
                    definition_counts[registration] += 1
                    registered.add(key)
            elif parameter.get("cardinality") == "high":
                warnings.append(f"events.{name}.parameters.{parameter_name}: high cardinality may limit reporting usefulness.")
        missing_required = REQUIRED_EVENT_PARAMETERS.get(name, set()) - parameter_names
        if missing_required:
            blockers.append(f"events.{name}: missing prescribed parameter(s): {', '.join(sorted(missing_required))}.")
        if "value" in parameter_names and "currency" not in parameter_names:
            blockers.append(f"events.{name}: currency is required when value is planned.")

    capacity = plan.get("propertyCapacity", {})
    used = budgets if budgets is not None else {
        "keyEvents": capacity.get("keyEventsUsed", 0),
        "event-dimension": capacity.get("eventDimensionsUsed", 0),
        "user-dimension": capacity.get("userDimensionsUsed", 0),
        "event-metric": capacity.get("eventMetricsUsed", 0),
    }
    if used.get("keyEvents", 0) + key_events > KEY_EVENT_LIMIT:
        blockers.append("The proposed key events exceed the remaining standard-property budget of 30.")
    for kind, limit in DEFINITION_LIMITS.items():
        if used.get(kind, 0) + definition_counts[kind] > limit:
            blockers.append(f"The proposed {kind} registrations exceed the standard-property budget of {limit}.")
    for definition in plan.get("customDefinitions", []):
        if isinstance(definition, dict) and not definition.get("budgetAvailable", True):
            blockers.append(f"customDefinitions.{definition.get('parameter')}: no remaining property budget.")

    ecommerce = plan.get("ecommerce", {})
    if ecommerce.get("enabled"):
        required = ("currencySource", "valueRule", "transactionIdSource", "transactionIdUniqueness", "purchaseState", "deduplication")
        for field in required:
            if not ecommerce.get(field):
                blockers.append(f"ecommerce.{field}: required when ecommerce is enabled.")
        if "purchase" not in event_names:
            blockers.append("Enabled ecommerce requires a planned purchase event.")
        missing_ecommerce_events = set(ecommerce.get("events", [])) - event_names
        if missing_ecommerce_events:
            blockers.append(f"ecommerce.events: unplanned event names: {', '.join(sorted(missing_ecommerce_events))}.")
    identity = plan.get("identity", {})
    if identity.get("measurementProtocolPlanned") and (not identity.get("clientSessionLinkage") or not identity.get("lateArrivalPolicy")):
        blockers.append("Measurement Protocol requires client/session linkage and a late-arrival policy.")
    if identity.get("userIdPlanned") and not identity.get("userIdSource"):
        blockers.append("User-ID requires a confirmed pseudonymous internal source.")
    if identity.get("userIdPlanned") and re.search(r"(?:email|phone|name|address)", str(identity.get("userIdSource", "")), re.I):
        blockers.append("User-ID cannot be based on email, phone, name, or address.")
    if any(event.get("collectionOwner") in {"backend-mp", "crm-mp", "payment-webhook-mp"} for event in plan.get("events", [])) and not identity.get("measurementProtocolPlanned"):
        blockers.append("A server-side collection owner requires an explicit Measurement Protocol plan.")
    consent = plan.get("consent", {})
    if consent.get("mode") == "unresolved" or not consent.get("policyConfirmed"):
        blockers.append("Consent policy and implementation mode must be confirmed before approval.")
    if not consent.get("updateTrigger") or not consent.get("persistence") or not consent.get("revocationFlow"):
        blockers.append("Consent update, persistence, and revocation behavior must be defined.")
    if consent.get("policyConfirmed") and "policy-dependent" in consent.get("defaults", {}).values():
        blockers.append("Confirmed consent policy requires explicit granted or denied defaults.")
    if not plan.get("businessContext", {}).get("confirmedByUser"):
        blockers.append("The project owner must confirm the business context before approval.")
    for outcome in outcomes.values():
        if not outcome.get("confirmed") or outcome.get("sourceOfTruth") == "unknown":
            blockers.append(f"outcomes.{outcome.get('id')}: business meaning and source of truth must be confirmed.")
    if plan.get("status") == "approved" and plan.get("openQuestions"):
        blockers.append("An approved plan cannot contain open questions.")
    for funnel in plan.get("funnels", []):
        missing_steps = set(funnel.get("steps", [])) - event_names
        if missing_steps:
            blockers.append(f"funnels.{funnel.get('id')}: steps reference unknown events: {', '.join(sorted(missing_steps))}.")
        if funnel.get("authoritativeCompletion") not in funnel.get("steps", []):
            blockers.append(f"funnels.{funnel.get('id')}: authoritative completion must be one of the funnel steps.")
    if plan.get("contentSha256") and plan["contentSha256"] != plan_content_sha256(plan):
        blockers.append("contentSha256 does not match canonical plan content.")
    return {"blockers": sorted(set(blockers)), "warnings": sorted(set(warnings))}
