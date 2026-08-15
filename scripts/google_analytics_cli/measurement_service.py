"""Local-only Stage 6 measurement context, planning, approval, and migration."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_store import ArtifactStore, canonical_json, utc_now
from .contracts import validate_artifact, validate_artifact_data
from .errors import AdvisorError, EXIT_CONFIGURATION, EXIT_INPUT
from .event_catalog import VERIFIED_AT, SOURCE_URLS, catalog_class
from .measurement_policy import DEFINITION_LIMITS, evaluate_plan, pii_issues, plan_content_sha256
from .measurement_renderer import render_plan
from .paths import project_data_path
from .site_scanner import inspect_site


DEFAULT_CONSENT = {
    "analytics_storage": "policy-dependent", "ad_storage": "policy-dependent",
    "ad_user_data": "policy-dependent", "ad_personalization": "policy-dependent",
}
BOUNDARIES = {
    "ga4Changed": False, "gtmChanged": False, "siteChanged": False,
    "productionEventsSent": False, "mutationApprovalGranted": False,
}
ID_SAFE = re.compile(r"[^a-z0-9-]+")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_absolute():
        raise AdvisorError("INVALID_ARGUMENTS", f"{label} path must be absolute.", EXIT_INPUT)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdvisorError("INVALID_INPUT_FILE", f"Could not read {label} JSON.", EXIT_INPUT, details={"path": str(path), "reason": type(exc).__name__}) from exc
    if not isinstance(value, dict):
        raise AdvisorError("INVALID_INPUT_FILE", f"{label} JSON must be an object.", EXIT_INPUT)
    return value


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_id(value: str, fallback: str) -> str:
    normalized = ID_SAFE.sub("-", value.lower()).strip("-")
    if len(normalized) < 2:
        normalized = fallback
    return normalized[:64]


def _profile(project_root: Path, profile_ref: str, baseline: dict[str, Any] | None, answers: dict[str, Any]) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    candidate = Path(profile_ref)
    if candidate.is_absolute() and candidate.is_file():
        validate_artifact("project-profile", candidate)
        profile = _load_json(candidate, "project profile")
        if Path(profile["project"]["root"]).resolve() != project_root.resolve():
            raise AdvisorError("PROJECT_IDENTITY_MISMATCH", "The project profile root does not match --project-root.", EXIT_INPUT)
        profile_id = str(profile["project"]["id"])
        evidence = [{
            "kind": "config", "reference": str(candidate.resolve()), "sha256": _file_sha(candidate),
            "observedAt": utc_now(), "supports": "Selected project identity and GA4 resource binding.", "confidence": "confirmed",
        }]
        return profile, profile_id, evidence
    if baseline is not None and baseline.get("profileRef") != profile_ref:
        raise AdvisorError("PROJECT_IDENTITY_MISMATCH", "The selected authorization profile does not match the baseline profile.", EXIT_INPUT)
    analytics = (baseline or {}).get("targets", {})
    profile = {
        "profileRef": profile_ref,
        "project": {"id": _safe_id(str(answers.get("projectId", project_root.name)), "project"), "root": str(project_root.resolve())},
        "website": {"url": answers.get("websiteUrl")},
        "analytics": {"property": analytics.get("property"), "webStream": analytics.get("webStream"), "gtmContainer": analytics.get("gtmContainer")},
        "businessContext": {
            "timezone": answers.get("timezone"), "currency": answers.get("currency"),
            "outcomes": [item.get("name") for item in answers.get("outcomes", []) if isinstance(item, dict) and item.get("name")],
            "confirmedByUser": bool(answers.get("confirmedByUser")),
        },
    }
    evidence = [{
        "kind": "config", "reference": f"authorization-profile:{profile_ref}", "sha256": _sha(profile),
        "observedAt": utc_now(), "supports": "Selected local project and authorization-profile binding.", "confidence": "inferred",
    }]
    return profile, profile["project"]["id"], evidence


def _baseline_budgets(baseline: dict[str, Any] | None, baseline_path: Path | None) -> dict[str, int]:
    budgets = {"keyEvents": 0, **{key: 0 for key in DEFINITION_LIMITS}}
    if not baseline or not baseline_path:
        return budgets
    facts = baseline.get("facts", {})
    if isinstance(facts, dict):
        budgets["keyEvents"] = int(facts.get("keyEventCount", 0) or 0)
    root = baseline_path.parent.parent
    for ref in baseline.get("snapshots", []):
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            continue
        snapshot_path = root / ref["path"]
        try:
            snapshot = _load_json(snapshot_path.resolve(), "baseline snapshot")
        except AdvisorError:
            continue
        if snapshot.get("provider") != "analytics-admin":
            continue
        state = snapshot.get("state", {})
        for source, target in (("customDimensions", "event-dimension"), ("customMetrics", "event-metric")):
            items = state.get(source, {}).get("items", []) if isinstance(state, dict) else []
            budgets[target] = len(items) if isinstance(items, list) else 0
        dimensions = state.get("customDimensions", {}).get("items", []) if isinstance(state, dict) else []
        budgets["user-dimension"] = sum(1 for item in dimensions if isinstance(item, dict) and item.get("scope") == "USER")
        budgets["event-dimension"] = max(0, budgets["event-dimension"] - budgets["user-dimension"])
    return budgets


def _normalize_evidence(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        result.append({
            "kind": item.get("kind", "user-confirmed"), "reference": str(item.get("reference", "user answer")),
            "sha256": item.get("sha256"), "observedAt": item.get("observedAt", utc_now()),
            "supports": str(item.get("supports", "Business fact supplied for measurement design.")),
            "confidence": item.get("confidence", "confirmed"),
        })
    return result


class MeasurementService:
    def context(
        self, project_root: Path, profile_ref: str, *, baseline_path: Path | None,
        without_baseline: bool, answers_path: Path | None,
    ) -> dict[str, Any]:
        if not project_root.is_absolute() or not project_root.is_dir():
            raise AdvisorError("INVALID_PROJECT_ROOT", "Project root must be an existing absolute directory.", EXIT_INPUT)
        if bool(baseline_path) == bool(without_baseline):
            raise AdvisorError("INVALID_ARGUMENTS", "Choose exactly one of --baseline or --without-baseline.", EXIT_INPUT)
        answers = _load_json(answers_path, "measurement answers") if answers_path else {}
        unsafe = pii_issues(answers)
        if unsafe:
            raise AdvisorError("PII_BLOCKED", "Measurement answers contain PII-shaped data.", EXIT_INPUT, details={"issues": unsafe})
        baseline = None
        baseline_sha = None
        source_evidence: list[dict[str, Any]] = []
        limitations: list[str] = []
        if baseline_path:
            if not baseline_path.is_absolute():
                raise AdvisorError("INVALID_ARGUMENTS", "Baseline path must be absolute.", EXIT_INPUT)
            validate_artifact("baseline-report", baseline_path)
            baseline = _load_json(baseline_path, "baseline report")
            if Path(str(baseline["projectRoot"])).resolve() != project_root.resolve():
                raise AdvisorError("PROJECT_IDENTITY_MISMATCH", "The baseline project root does not match --project-root.", EXIT_INPUT)
            baseline_sha = _file_sha(baseline_path)
            source_evidence.append({
                "kind": "baseline", "reference": str(baseline_path.resolve()), "sha256": baseline_sha,
                "observedAt": baseline["generatedAt"], "supports": "Read-only GA4, GTM, and website baseline.", "confidence": "confirmed",
            })
            if baseline.get("completeness") != "complete":
                limitations.append("The selected baseline is partial; unresolved gaps are preserved in this plan.")
        else:
            limitations.append("No baseline was selected because this is an explicit new/unconnected setup path.")
        profile, profile_id, profile_evidence = _profile(project_root, profile_ref, baseline, answers)
        source_evidence.extend(profile_evidence)
        site = inspect_site(project_root)
        site_summary = {
            "publicIds": site.get("publicIds", []), "findings": site.get("findings", []),
            "truncated": bool(site.get("truncated")), "networkUsed": bool(site.get("networkUsed")),
        }
        site_sha = _sha(site)
        source_evidence.append({
            "kind": "source", "reference": str(project_root.resolve()), "sha256": site_sha,
            "observedAt": utc_now(), "supports": "Local static measurement-code evidence.", "confidence": "confirmed",
        })
        questions: list[str] = []
        if not answers.get("businessModel"):
            questions.append("What business model and primary customer journey should this measurement plan support?")
        if not answers.get("outcomes"):
            questions.append("Which completed actions are meaningful business outcomes, and where are they confirmed?")
        if not answers.get("events"):
            questions.append("Which GA4 events should represent the confirmed outcomes and diagnostic funnel steps?")
        consent = answers.get("consent", {})
        if not isinstance(consent, dict) or not consent.get("policyConfirmed"):
            questions.append("Which consent policy and Basic or Advanced Consent Mode has the owner confirmed?")
        generated_at = utc_now()
        context_id = f"measurement-context-{_stamp()}-{_sha([profile_id, baseline_sha, site_sha, answers, generated_at])[:12]}"
        context = {
            "schemaVersion": 1, "contextType": "measurement-context", "generatedAt": generated_at,
            "contextId": context_id, "projectRoot": str(project_root.resolve()), "profileId": profile_id,
            "profileRef": profile_ref, "projectProfile": profile, "projectProfileSha256": _sha(profile),
            "baseline": None if baseline is None else {
                "auditId": baseline["auditId"], "path": str(baseline_path.resolve()), "sha256": baseline_sha,
                "generatedAt": baseline["generatedAt"], "completeness": baseline["completeness"], "targets": baseline["targets"],
            },
            "site": site_summary, "sourceEvidence": source_evidence, "answers": answers,
            "budgetsUsed": _baseline_budgets(baseline, baseline_path), "openQuestions": questions,
            "limitations": limitations, "networkUsed": False, "mutationPerformed": False,
        }
        location = ArtifactStore(project_root.resolve()).write_measurement_context(context)
        return {"context": context, "artifact": location, "status": "action_required" if questions else "ready", "mutationPerformed": False}

    def draft(self, context_path: Path, output_dir: Path) -> dict[str, Any]:
        context = _load_json(context_path, "measurement context")
        if context.get("contextType") != "measurement-context" or context.get("schemaVersion") != 1:
            raise AdvisorError("INVALID_INPUT_FILE", "The input is not a supported measurement context.", EXIT_INPUT)
        project_root = Path(str(context.get("projectRoot", ""))).resolve()
        expected = project_data_path(project_root).resolve()
        if not output_dir.is_absolute() or output_dir.resolve() != expected:
            raise AdvisorError("INVALID_OUTPUT_DIRECTORY", "Measurement plans must stay in the project's protected data directory.", EXIT_CONFIGURATION, details={"expected": str(expected)})
        answers = context.get("answers", {})
        outcomes = self._outcomes(answers, context["sourceEvidence"])
        events = self._events(answers, outcomes, context["sourceEvidence"])
        consent = self._consent(answers)
        ecommerce = self._ecommerce(answers)
        identity = self._identity(answers)
        funnels = self._funnels(answers)
        questions = list(context.get("openQuestions", []))
        generated_at = utc_now()
        plan = {
            "schemaVersion": 2, "artifactType": "measurement-plan", "generatedAt": generated_at,
            "planId": f"measure-{_stamp()}-{_sha([context['contextId'], answers, generated_at])[:12]}",
            "projectProfileId": context["profileId"], "projectProfileSha256": context["projectProfileSha256"],
            "property": (context.get("baseline") or {}).get("targets", {}).get("property") or context["projectProfile"].get("analytics", {}).get("property"),
            "webStream": (context.get("baseline") or {}).get("targets", {}).get("webStream") or context["projectProfile"].get("analytics", {}).get("webStream"),
            "site": context["projectRoot"], "status": "draft", "supersedes": answers.get("supersedes"),
            "contentSha256": "0" * 64, "approvedAt": None, "approvalSha256": None,
            "sourceEvidence": context["sourceEvidence"],
            "propertyCapacity": {
                "keyEventsUsed": context["budgetsUsed"].get("keyEvents", 0),
                "eventDimensionsUsed": context["budgetsUsed"].get("event-dimension", 0),
                "userDimensionsUsed": context["budgetsUsed"].get("user-dimension", 0),
                "eventMetricsUsed": context["budgetsUsed"].get("event-metric", 0),
            },
            "businessContext": {
                "businessModel": str(answers.get("businessModel", "unknown")),
                "timezone": answers.get("timezone") or context["projectProfile"].get("businessContext", {}).get("timezone"),
                "currency": answers.get("currency") or context["projectProfile"].get("businessContext", {}).get("currency"),
                "confirmedByUser": bool(answers.get("confirmedByUser")),
            },
            "outcomes": outcomes, "events": events, "funnels": funnels, "ecommerce": ecommerce,
            "identity": identity, "consent": consent, "customDefinitions": self._custom_definitions(events, context["budgetsUsed"]),
            "verification": [check for event in events for check in event["verificationChecks"]],
            "assumptions": list(answers.get("assumptions", [])), "openQuestions": questions,
            "limitations": list(context.get("limitations", [])) + list(answers.get("limitations", [])),
            "stageBoundaries": dict(BOUNDARIES),
        }
        plan["contentSha256"] = plan_content_sha256(plan)
        evaluation = evaluate_plan(plan, context.get("budgetsUsed"))
        plan["status"] = "blocked" if evaluation["blockers"] else "draft"
        plan["contentSha256"] = plan_content_sha256(plan)
        validate_data = self._validate_in_memory(plan, evaluation, allow_blocked=True)
        validate_artifact_data("measurement-plan", plan)
        location = ArtifactStore(project_root).write_measurement_plan(plan)
        return {
            "plan": plan, "artifact": location, "rendered": render_plan(plan), "validation": validate_data,
            "approvalCommand": None if evaluation["blockers"] else f"measurement approve --input {location['path']} --confirm-sha256 {plan['contentSha256']}",
            "mutationPerformed": False,
        }

    def show(self, input_path: Path, output_format: str) -> dict[str, Any]:
        validate_artifact("measurement-plan", input_path)
        plan = _load_json(input_path, "measurement plan")
        data: dict[str, Any] = {"planId": plan["planId"], "schemaVersion": plan["schemaVersion"], "status": plan["status"], "mutationPerformed": False}
        if output_format == "plain":
            data["rendered"] = render_plan(plan)
        else:
            data["plan"] = plan
        if plan.get("schemaVersion") == 2:
            data["validation"] = evaluate_plan(plan)
        return data

    def approve(self, input_path: Path, confirmation: str) -> dict[str, Any]:
        validate_artifact("measurement-plan", input_path)
        draft = _load_json(input_path, "measurement plan")
        if draft.get("schemaVersion") != 2 or draft.get("status") != "draft":
            raise AdvisorError("PLAN_NOT_APPROVABLE", "Only an unblocked version 2 draft can be approved.", EXIT_INPUT)
        if confirmation != draft.get("contentSha256"):
            raise AdvisorError("CONFIRMATION_MISMATCH", "The confirmation SHA-256 does not match the immutable draft.", EXIT_INPUT)
        evaluation = evaluate_plan(draft)
        if evaluation["blockers"] or draft.get("openQuestions"):
            raise AdvisorError("PLAN_NOT_APPROVABLE", "The draft still has blocking issues or open questions.", EXIT_INPUT, details=evaluation)
        project_root = Path(str(draft["site"])).resolve()
        for evidence in draft["sourceEvidence"]:
            reference = evidence.get("reference")
            if evidence.get("kind") in {"baseline", "config"} and isinstance(reference, str):
                source = Path(reference)
                if source.is_absolute() and source.is_file() and _file_sha(source) != evidence.get("sha256"):
                    raise AdvisorError("STALE_PLAN", "A source artifact changed after the draft was created.", EXIT_INPUT, details={"source": reference})
            if evidence.get("kind") == "source" and isinstance(reference, str):
                source = Path(reference)
                if source.is_absolute() and source.is_dir() and _sha(inspect_site(source)) != evidence.get("sha256"):
                    raise AdvisorError("STALE_PLAN", "The local measurement source evidence changed after the draft was created.", EXIT_INPUT, details={"source": reference})
        approved = deepcopy(draft)
        approved["generatedAt"] = utc_now()
        approved["approvedAt"] = approved["generatedAt"]
        approved["approvalSha256"] = confirmation
        approved["supersedes"] = draft["planId"]
        approved["planId"] = f"measure-{_stamp()}-{_sha([draft['planId'], confirmation, approved['approvedAt']])[:12]}"
        approved["status"] = "approved"
        approved["contentSha256"] = plan_content_sha256(approved)
        final = evaluate_plan(approved)
        if final["blockers"]:
            raise AdvisorError("PLAN_NOT_APPROVABLE", "The approved revision failed final policy validation.", EXIT_INPUT, details=final)
        validate_artifact_data("measurement-plan", approved)
        location = ArtifactStore(project_root).write_measurement_plan(approved)
        return {"plan": approved, "artifact": location, "rendered": render_plan(approved), "approvedDraftSha256": confirmation, "mutationPerformed": False}

    def migrate(self, input_path: Path) -> dict[str, Any]:
        validate_artifact("measurement-plan", input_path)
        legacy = _load_json(input_path, "measurement plan")
        if legacy.get("schemaVersion") != 1:
            raise AdvisorError("MIGRATION_NOT_REQUIRED", "Only version 1 measurement plans need migration.", EXIT_INPUT)
        project_root = self._project_root_from_artifact(input_path)
        evidence = [{
            "kind": "config", "reference": str(input_path.resolve()), "sha256": _file_sha(input_path),
            "observedAt": legacy["generatedAt"], "supports": "Legacy measurement-plan input.", "confidence": "confirmed",
        }]
        outcomes = []
        events = []
        for index, old in enumerate(legacy["events"], 1):
            outcome_id = f"legacy-outcome-{index}"
            outcomes.append({
                "id": outcome_id, "name": old["businessOutcome"], "class": "primary" if old["keyEvent"] else "diagnostic",
                "businessMeaning": old["businessOutcome"], "owner": "unknown", "authoritativeState": old["evidence"],
                "sourceOfTruth": old["sourceOfTruth"] if old["sourceOfTruth"] in {"backend", "payment", "crm", "browser", "gtm"} else "unknown",
                "proxySignals": [], "decisionUse": "Must be reconfirmed during migration.", "confirmed": False, "evidence": evidence,
            })
            parameters = [{
                "name": item["name"], "meaning": item["meaning"], "source": "Must be reconfirmed", "type": "string",
                "required": item["required"], "scope": "event", "privacy": "unknown", "cardinality": "unknown",
                "reportingUse": None, "registration": "none",
            } for item in old["parameters"]]
            events.append({
                "name": old["name"], "catalogClass": catalog_class(old["name"]), "customJustification": "Migrated custom event; justification must be reconfirmed." if catalog_class(old["name"]) == "custom" else None,
                "businessOutcomeId": outcome_id, "businessMeaning": old["businessOutcome"], "trigger": old["evidence"],
                "authoritativeSource": outcomes[-1]["sourceOfTruth"], "collectionOwner": "unknown",
                "keyEventRecommendation": old["keyEvent"], "keyEventJustification": "Migrated recommendation; reconfirm before approval.",
                "parameters": parameters, "deduplication": legacy["ecommerce"]["deduplication"], "consentBehavior": "Must be reconfirmed",
                "implementationTargets": [], "verificationChecks": [{"event": old["name"], "method": "manual", "successCriterion": "Reconfirm the legacy verification rule.", "productionDataSent": False}],
                "evidence": evidence, "limitations": ["Migrated from schemaVersion 1."],
            })
        consent_old = legacy["consent"]
        generated_at = utc_now()
        plan = {
            "schemaVersion": 2, "artifactType": "measurement-plan", "generatedAt": generated_at,
            "planId": f"measure-{_stamp()}-{_sha([legacy, generated_at])[:12]}", "projectProfileId": "legacy-profile",
            "projectProfileSha256": legacy["projectProfileSha256"], "property": None, "webStream": None,
            "site": str(project_root), "status": "blocked",
            "supersedes": legacy["planId"] if re.fullmatch(r"measure-[A-Za-z0-9-]{12,128}", legacy["planId"]) else None,
            "contentSha256": "0" * 64,
            "approvedAt": None, "approvalSha256": None, "sourceEvidence": evidence,
            "propertyCapacity": {"keyEventsUsed": 0, "eventDimensionsUsed": 0, "userDimensionsUsed": 0, "eventMetricsUsed": 0},
            "businessContext": {"businessModel": "legacy-unconfirmed", "timezone": None, "currency": None, "confirmedByUser": False},
            "outcomes": outcomes, "events": events, "funnels": [],
            "ecommerce": {
                "enabled": legacy["ecommerce"]["enabled"], "reason": "Migrated from schemaVersion 1; reconfirm required.", "events": ["purchase"] if legacy["ecommerce"]["enabled"] else [],
                "itemIdentity": "Legacy items required" if legacy["ecommerce"]["itemsRequired"] else None,
                "valueRule": legacy["ecommerce"]["valueRule"], "currencySource": legacy["ecommerce"]["currencySource"],
                "multiCurrencyPolicy": None, "transactionIdSource": legacy["ecommerce"]["transactionIdSource"],
                "transactionIdUniqueness": None, "purchaseState": None, "refundState": None,
                "deduplication": legacy["ecommerce"]["deduplication"], "refundSemantics": None,
            },
            "identity": {"userIdPlanned": False, "userIdSource": None, "measurementProtocolPlanned": False, "clientSessionLinkage": None, "lateArrivalPolicy": None},
            "consent": {
                "mode": consent_old["mode"] if consent_old["mode"] in {"basic", "advanced"} else "unresolved",
                "policyConfirmed": False, "policySource": None, "cmp": None, "defaults": consent_old["defaults"],
                "regions": consent_old["regions"], "updateTrigger": consent_old["updateTrigger"], "persistence": consent_old["persistence"],
                "waitForUpdateMs": None, "revocationFlow": None, "legalGuarantee": False,
            },
            "customDefinitions": [], "verification": [check for event in events for check in event["verificationChecks"]],
            "assumptions": [], "openQuestions": ["Reconfirm business outcomes, ownership, privacy, consent, identity, and verification for schemaVersion 2."],
            "limitations": ["Migration preserves legacy intent but cannot infer missing Stage 6 evidence."], "stageBoundaries": dict(BOUNDARIES),
        }
        plan["contentSha256"] = plan_content_sha256(plan)
        validate_artifact_data("measurement-plan", plan)
        location = ArtifactStore(project_root).write_measurement_plan(plan)
        return {"plan": plan, "artifact": location, "rendered": render_plan(plan), "mutationPerformed": False}

    @staticmethod
    def _project_root_from_artifact(path: Path) -> Path:
        resolved = path.resolve()
        for parent in resolved.parents:
            if parent.name == ".google-analytics-advisor":
                return parent.parent
        raise AdvisorError("INVALID_INPUT_FILE", "Legacy migration input must be inside a project's .google-analytics-advisor directory.", EXIT_INPUT)

    @staticmethod
    def _outcomes(answers: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for index, item in enumerate(answers.get("outcomes", []), 1):
            if not isinstance(item, dict):
                continue
            result.append({
                "id": _safe_id(str(item.get("id", f"outcome-{index}")), f"outcome-{index}"),
                "name": str(item.get("name", f"Outcome {index}")), "class": item.get("class", "primary"),
                "businessMeaning": str(item.get("businessMeaning", item.get("name", "Unconfirmed business outcome"))),
                "owner": str(item.get("owner", "unknown")), "authoritativeState": str(item.get("authoritativeState", "Unknown")),
                "sourceOfTruth": item.get("sourceOfTruth", "unknown"), "proxySignals": list(item.get("proxySignals", [])),
                "decisionUse": str(item.get("decisionUse", "Decide whether the customer journey reaches this outcome.")),
                "confirmed": bool(item.get("confirmed")), "evidence": _normalize_evidence(item.get("evidence")) or evidence,
            })
        if not result:
            result.append({
                "id": "outcome-unconfirmed", "name": "Unconfirmed business outcome", "class": "primary",
                "businessMeaning": "The user still needs to define the meaningful completed result.", "owner": "unknown",
                "authoritativeState": "Unknown", "sourceOfTruth": "unknown", "proxySignals": [],
                "decisionUse": "Required before implementation.", "confirmed": False, "evidence": evidence,
            })
        return result

    @staticmethod
    def _events(answers: dict[str, Any], outcomes: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        valid_outcomes = {item["id"] for item in outcomes}
        result = []
        for index, item in enumerate(answers.get("events", []), 1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", f"event_{index}"))
            outcome_id = str(item.get("outcomeId", outcomes[0]["id"]))
            if outcome_id not in valid_outcomes:
                outcome_id = str(item.get("outcomeId"))
            parameters = []
            for parameter in item.get("parameters", []):
                if not isinstance(parameter, dict):
                    continue
                parameters.append({
                    "name": str(parameter.get("name", "parameter")), "meaning": str(parameter.get("meaning", "Unconfirmed meaning")),
                    "source": str(parameter.get("source", "Unconfirmed source")), "type": parameter.get("type", "string"),
                    "required": bool(parameter.get("required")), "scope": parameter.get("scope", "event"),
                    "privacy": parameter.get("privacy", "unknown"), "cardinality": parameter.get("cardinality", "unknown"),
                    "reportingUse": parameter.get("reportingUse"), "registration": parameter.get("registration", "none"),
                })
            checks = []
            for check in item.get("verification", []):
                if isinstance(check, dict):
                    checks.append({
                        "event": name, "method": check.get("method", "local-test"),
                        "successCriterion": str(check.get("successCriterion", "Observe one synthetic event with the planned parameters.")),
                        "productionDataSent": False,
                    })
            if not checks:
                checks = [{"event": name, "method": "local-test", "successCriterion": "Trigger the authoritative state once and observe one non-duplicated synthetic event.", "productionDataSent": False}]
            event_class = catalog_class(name)
            result.append({
                "name": name, "catalogClass": event_class,
                "customJustification": item.get("customJustification") if event_class == "custom" else None,
                "businessOutcomeId": outcome_id, "businessMeaning": str(item.get("businessMeaning", "Measure the linked business outcome.")),
                "trigger": str(item.get("trigger", "Unknown")), "authoritativeSource": item.get("sourceOfTruth", "unknown"),
                "collectionOwner": item.get("collectionOwner", "unknown"), "keyEventRecommendation": bool(item.get("keyEvent")),
                "keyEventJustification": str(item.get("keyEventJustification", "This decision must be confirmed against the business outcome.")),
                "parameters": parameters, "deduplication": str(item.get("deduplication", "One event per authoritative state transition.")),
                "consentBehavior": str(item.get("consentBehavior", "Follow the confirmed Consent Mode policy.")),
                "implementationTargets": list(item.get("implementationTargets", [])), "verificationChecks": checks,
                "evidence": _normalize_evidence(item.get("evidence")) or evidence, "limitations": list(item.get("limitations", [])),
            })
        if not result:
            result.append({
                "name": "measurement_outcome", "catalogClass": "custom", "customJustification": None,
                "businessOutcomeId": outcomes[0]["id"], "businessMeaning": "Unconfirmed event mapping.", "trigger": "Unknown",
                "authoritativeSource": "unknown", "collectionOwner": "unknown", "keyEventRecommendation": False,
                "keyEventJustification": "Cannot recommend until the outcome is confirmed.", "parameters": [],
                "deduplication": "Unknown", "consentBehavior": "Unknown", "implementationTargets": [],
                "verificationChecks": [{"event": "measurement_outcome", "method": "manual", "successCriterion": "Confirm the event design before implementation.", "productionDataSent": False}],
                "evidence": evidence, "limitations": ["Placeholder only; must not be implemented."],
            })
        return result

    @staticmethod
    def _consent(answers: dict[str, Any]) -> dict[str, Any]:
        item = answers.get("consent", {}) if isinstance(answers.get("consent"), dict) else {}
        defaults = item.get("defaults") if isinstance(item.get("defaults"), dict) else DEFAULT_CONSENT
        return {
            "mode": item.get("mode", "unresolved"), "policyConfirmed": bool(item.get("policyConfirmed")),
            "policySource": item.get("policySource"), "cmp": item.get("cmp"), "defaults": {key: defaults.get(key, "policy-dependent") for key in DEFAULT_CONSENT},
            "regions": list(item.get("regions", [])), "updateTrigger": item.get("updateTrigger"), "persistence": item.get("persistence"),
            "waitForUpdateMs": item.get("waitForUpdateMs"), "revocationFlow": item.get("revocationFlow"), "legalGuarantee": False,
        }

    @staticmethod
    def _ecommerce(answers: dict[str, Any]) -> dict[str, Any]:
        item = answers.get("ecommerce", {}) if isinstance(answers.get("ecommerce"), dict) else {}
        enabled = bool(item.get("enabled"))
        return {
            "enabled": enabled, "reason": str(item.get("reason", "The business model does not require ecommerce." if not enabled else "Ecommerce was confirmed by the project owner.")),
            "events": list(item.get("events", [])), "itemIdentity": item.get("itemIdentity"), "valueRule": item.get("valueRule"),
            "currencySource": item.get("currencySource"), "multiCurrencyPolicy": item.get("multiCurrencyPolicy"),
            "transactionIdSource": item.get("transactionIdSource"), "transactionIdUniqueness": item.get("transactionIdUniqueness"),
            "purchaseState": item.get("purchaseState"), "refundState": item.get("refundState"),
            "deduplication": item.get("deduplication"), "refundSemantics": item.get("refundSemantics"),
        }

    @staticmethod
    def _identity(answers: dict[str, Any]) -> dict[str, Any]:
        item = answers.get("identity", {}) if isinstance(answers.get("identity"), dict) else {}
        return {
            "userIdPlanned": bool(item.get("userIdPlanned")), "userIdSource": item.get("userIdSource"),
            "measurementProtocolPlanned": bool(item.get("measurementProtocolPlanned")),
            "clientSessionLinkage": item.get("clientSessionLinkage"), "lateArrivalPolicy": item.get("lateArrivalPolicy"),
        }

    @staticmethod
    def _funnels(answers: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for index, item in enumerate(answers.get("funnels", []), 1):
            if isinstance(item, dict):
                result.append({
                    "id": _safe_id(str(item.get("id", f"funnel-{index}")), f"funnel-{index}"),
                    "businessQuestion": str(item.get("businessQuestion", "Where do users stop before the primary outcome?")),
                    "steps": list(item.get("steps", [])), "open": bool(item.get("open", True)),
                    "directlyFollowed": bool(item.get("directlyFollowed", False)), "timeWindow": str(item.get("timeWindow", "30 days")),
                    "crossSession": bool(item.get("crossSession", True)), "authoritativeCompletion": str(item.get("authoritativeCompletion", "Unknown")),
                    "breakdownIntent": item.get("breakdownIntent"), "limitations": list(item.get("limitations", [])),
                    "stage10Ready": bool(item.get("stage10Ready", False)),
                })
        return result

    @staticmethod
    def _custom_definitions(events: list[dict[str, Any]], budgets: dict[str, int]) -> list[dict[str, Any]]:
        result = []
        seen: set[tuple[str, str]] = set()
        proposed = {key: 0 for key in DEFINITION_LIMITS}
        for event in events:
            for parameter in event["parameters"]:
                registration = parameter["registration"]
                key = (parameter["name"], registration)
                if registration not in DEFINITION_LIMITS or key in seen:
                    continue
                seen.add(key)
                proposed[registration] += 1
                result.append({
                    "parameter": parameter["name"], "registration": registration,
                    "reportingUse": parameter.get("reportingUse") or "Unconfirmed",
                    "cardinality": parameter["cardinality"] if parameter["cardinality"] in {"low", "medium", "high"} else "high",
                    "budgetAvailable": budgets.get(registration, 0) + proposed[registration] <= DEFINITION_LIMITS[registration],
                })
        return result

    @staticmethod
    def _validate_in_memory(plan: dict[str, Any], evaluation: dict[str, list[str]], *, allow_blocked: bool) -> dict[str, Any]:
        if evaluation["blockers"] and not allow_blocked:
            raise AdvisorError("PLAN_NOT_APPROVABLE", "Measurement plan failed policy validation.", EXIT_INPUT, details=evaluation)
        return {"validForDraft": True, "approvable": not evaluation["blockers"] and not plan["openQuestions"], **evaluation}


def reference_metadata() -> dict[str, Any]:
    return {"verifiedAt": VERIFIED_AT, "sources": list(SOURCE_URLS), "networkUsed": False}
