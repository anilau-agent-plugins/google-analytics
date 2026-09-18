"""Resumable full-picture orchestration over the existing read-only source services."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .advisor_assessment_policy import (
    DOMAIN_ORDER,
    STEP_ORDER,
    artifact_ref,
    assessment_checkpoint_sha256,
    assessment_plan_sha256,
    assessment_report_sha256,
    assessment_request_sha256,
    domain,
    file_sha256,
    initial_domains,
    normalized_recommendation,
    parse_time,
    prioritize_recommendations,
    read_json,
    resolve_project_path,
    update_domain,
)
from .advisor_assessment_renderer import render_assessment
from .artifact_store import ArtifactStore
from .baseline_audit import BaselineService
from .contracts import validate_artifact_data
from .cross_source_service import CrossSourceService, cross_source_request_sha256
from .errors import AdvisorError, EXIT_INPUT
from .report_service import ReportService, report_request_sha256
from .search_console_report_service import SearchConsoleReportService, search_console_request_sha256


HARD_FAILURE_PARTS = ("TAMPERED", "CONTEXT_DRIFT", "PROFILE_MISMATCH", "SITE_MISMATCH", "ARTIFACT_INVALID")
SITE_SUFFIXES = {".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".vue", ".php", ".blade.php", ".json"}


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _failure(exc: AdvisorError) -> dict[str, Any]:
    return {"code": exc.code, "message": exc.message, "retryable": bool(exc.retryable)}


def _internal_hash(value: dict[str, Any]) -> str | None:
    for field in ("reportSha256", "checkpointSha256", "planSha256", "contentSha256", "snapshotSha256", "resultSha256"):
        if isinstance(value.get(field), str):
            return value[field]
    return None


def _source_ref(path: Path, value: dict[str, Any]) -> dict[str, Any]:
    return artifact_ref(path, value, next((field for field in ("reportSha256", "checkpointSha256", "planSha256", "contentSha256") if field in value), None))


class AdvisorAssessmentService:
    def __init__(
        self, *, report_service: Any = None, search_console_service: Any = None,
        cross_source_service: Any = None, baseline_service: Any = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.report_service = report_service or ReportService()
        self.search_console_service = search_console_service or SearchConsoleReportService()
        self.cross_source_service = cross_source_service or CrossSourceService()
        self.baseline_service = baseline_service or BaselineService()
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _load_request(self, path: Path) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        request = read_json(resolved, "advisor assessment request")
        if request.get("contentSha256") != assessment_request_sha256(request):
            raise AdvisorError("ADVISOR_REQUEST_TAMPERED", "The advisor request SHA-256 does not match its content.", EXIT_INPUT)
        validate_artifact_data("advisor-assessment-request", request, path_label=str(resolved))
        root = Path(request["projectRoot"]).expanduser().resolve()
        if not root.is_dir():
            raise AdvisorError("ADVISOR_ARTIFACT_INVALID", "The selected project root does not exist.", EXIT_INPUT)
        return request

    def _measurement(self, request: dict[str, Any], root: Path) -> tuple[Path | None, dict[str, Any] | None]:
        if not request.get("measurementPlanRef"):
            return None, None
        path = resolve_project_path(root, request["measurementPlanRef"], "measurementPlanRef")
        value = read_json(path, "measurement plan")
        validate_artifact_data("measurement-plan", value, path_label=str(path))
        if value.get("schemaVersion") != 2 or value.get("status") != "approved":
            raise AdvisorError("ADVISOR_MEASUREMENT_PLAN_REQUIRED", "Business interpretation requires an approved measurement-plan v2.", EXIT_INPUT)
        if value.get("property") not in {None, request["property"]}:
            raise AdvisorError("ADVISOR_CONTEXT_DRIFT", "The measurement plan belongs to another GA4 property.", EXIT_INPUT)
        return path, value

    def _site_changed_after(self, root: Path, generated_at: datetime) -> bool:
        checked = 0
        evidence_root = root / ".google-analytics-advisor"
        for path in root.rglob("*"):
            if checked >= 10000:
                return True
            try:
                resolved = path.resolve()
                if not path.is_file() or evidence_root == resolved or evidence_root in resolved.parents or ".git" in resolved.parts:
                    continue
                suffix = ".blade.php" if path.name.endswith(".blade.php") else path.suffix.casefold()
                if suffix not in SITE_SUFFIXES:
                    continue
                checked += 1
                if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) > generated_at:
                    return True
            except OSError:
                return True
        return False

    def _baseline(self, request: dict[str, Any], root: Path) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None, str]:
        if not request.get("baselineRef"):
            return "planned", None, None, "No matching baseline was supplied; a bounded fresh baseline is planned."
        path = resolve_project_path(root, request["baselineRef"], "baselineRef")
        value = read_json(path, "baseline report")
        validate_artifact_data("baseline-report", value, path_label=str(path))
        targets = value.get("targets", {})
        exact = (
            value.get("profileRef") == request["profileId"]
            and targets.get("property") == request["property"]
            and targets.get("webStream") == request["webStream"]
            and targets.get("gtmContainer") == request.get("gtmContainer")
        )
        generated = parse_time(value["generatedAt"])
        known = parse_time(request["knownChangesAfter"]) if request.get("knownChangesAfter") else None
        stale = (
            not exact
            or self.now().astimezone(timezone.utc) - generated > timedelta(hours=24)
            or (known is not None and known > generated)
            or self._site_changed_after(root, generated)
        )
        ref = _source_ref(path, value)
        if stale:
            return "planned", value, ref, "The supplied baseline is stale or does not match the exact current context; a fresh baseline is planned."
        return "reused", value, ref, "The supplied exact baseline passed the 24-hour and known-change freshness checks."

    def _write_request(self, store: ArtifactStore, folder: str, value: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
        location = store.write_named_artifact(folder, value["requestId"], value)
        path = Path(location["path"])
        return path, _source_ref(path, value)

    def _child_plan_ref(self, result: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        plan = result["plan"]
        path = Path(result["artifact"]["path"])
        return plan, _source_ref(path, plan)

    def _resume_steps(self, checkpoint: dict[str, Any]) -> dict[str, dict[str, Any]]:
        reusable: dict[str, dict[str, Any]] = {}
        for step in checkpoint["steps"]:
            if step.get("state") not in {"completed", "reused"} or not isinstance(step.get("resultRef"), dict):
                continue
            ref = step["resultRef"]
            path = Path(str(ref.get("path", ""))).resolve()
            if not path.is_file() or file_sha256(path) != ref.get("fileSha256"):
                raise AdvisorError("ADVISOR_CHECKPOINT_TAMPERED", "A completed checkpoint artifact changed.", EXIT_INPUT)
            value = read_json(path, f"{step['stepId']} result")
            if _internal_hash(value) and _internal_hash(value) != ref.get("artifactSha256"):
                raise AdvisorError("ADVISOR_CHECKPOINT_TAMPERED", "A completed checkpoint artifact hash changed.", EXIT_INPUT)
            reusable[step["stepId"]] = step
        return reusable

    def _load_checkpoint(
        self, path: Path, *, verify_chain: bool = True, visited: set[Path] | None = None,
    ) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        seen = visited if visited is not None else set()
        if resolved in seen:
            raise AdvisorError("ADVISOR_CHECKPOINT_TAMPERED", "The checkpoint chain contains a cycle.", EXIT_INPUT)
        seen.add(resolved)
        value = read_json(resolved, "advisor checkpoint")
        if value.get("checkpointSha256") != assessment_checkpoint_sha256(value):
            raise AdvisorError("ADVISOR_CHECKPOINT_TAMPERED", "The checkpoint SHA-256 does not match its content.", EXIT_INPUT)
        validate_artifact_data("advisor-assessment-checkpoint", value, path_label=str(resolved))
        if verify_chain and value.get("previousCheckpoint"):
            previous_ref = value["previousCheckpoint"]
            previous_path = Path(previous_ref["path"]).resolve()
            if not previous_path.is_file() or file_sha256(previous_path) != previous_ref.get("fileSha256"):
                raise AdvisorError("ADVISOR_CHECKPOINT_TAMPERED", "The checkpoint chain changed.", EXIT_INPUT)
            previous = self._load_checkpoint(previous_path, verify_chain=True, visited=seen)
            if previous.get("checkpointSha256") != previous_ref.get("artifactSha256") or previous["sequence"] + 1 != value["sequence"]:
                raise AdvisorError("ADVISOR_CHECKPOINT_TAMPERED", "The checkpoint chain is not contiguous.", EXIT_INPUT)
        return value

    def _verified_child_plan_path(self, step: dict[str, Any]) -> Path:
        ref = step.get("planRef")
        if not isinstance(ref, dict):
            raise AdvisorError("ADVISOR_PLAN_TAMPERED", "A planned source step has no child-plan reference.", EXIT_INPUT)
        path = Path(str(ref.get("path", ""))).resolve()
        if not path.is_file() or file_sha256(path) != ref.get("fileSha256"):
            raise AdvisorError("ADVISOR_PLAN_TAMPERED", "A source child plan changed after advisor planning.", EXIT_INPUT)
        value = read_json(path, f"{step['stepId']} child plan")
        if _internal_hash(value) != ref.get("artifactSha256"):
            raise AdvisorError("ADVISOR_PLAN_TAMPERED", "A source child-plan hash changed after advisor planning.", EXIT_INPUT)
        return path

    def plan(self, request_path: Path, *, resume_checkpoint: Path | None = None) -> dict[str, Any]:
        request_path = request_path.expanduser().resolve()
        request = self._load_request(request_path)
        root = Path(request["projectRoot"]).resolve()
        store = ArtifactStore(root)
        measurement_path, measurement = self._measurement(request, root)
        ecommerce = bool(measurement and measurement.get("ecommerce", {}).get("enabled"))
        baseline_state, _, baseline_ref, baseline_reason = self._baseline(request, root)
        reusable: dict[str, dict[str, Any]] = {}
        resume_ref = None
        fixed_periods = None
        if resume_checkpoint:
            checkpoint = self._load_checkpoint(resume_checkpoint)
            if checkpoint["requestId"] != request["requestId"] or checkpoint["identities"] != {
                "profileId": request["profileId"], "property": request["property"],
                "webStream": request["webStream"], "site": request.get("site"),
                "verifiedOrigin": request["verifiedOrigin"],
            }:
                raise AdvisorError("ADVISOR_CONTEXT_DRIFT", "The checkpoint belongs to another assessment context.", EXIT_INPUT)
            reusable = self._resume_steps(checkpoint)
            fixed_periods = checkpoint["periods"]
            resume_ref = _source_ref(Path(resume_checkpoint).resolve(), checkpoint)
            if "baseline" in reusable:
                baseline_state, baseline_ref = "reused", reusable["baseline"]["resultRef"]
                baseline_reason = "The completed exact baseline is reused from the verified checkpoint."

        search_capability = request.get("capabilities", {}).get("searchConsole", "ready")
        search_status = search_capability.get("status") if isinstance(search_capability, dict) else search_capability
        search_available = bool(request.get("site")) and search_status == "ready"
        domains = initial_domains(measurement_ready=measurement is not None, ecommerce=ecommerce, search_console=search_available, baseline_state=baseline_state)
        stamp = self.now().astimezone(timezone.utc)
        steps: dict[str, dict[str, Any]] = {}
        limitations: list[str] = []
        expiries: list[datetime] = []
        planning_network = False

        if baseline_state == "reused" and baseline_ref:
            steps["baseline"] = {"stepId": "baseline", "state": "reused", "reason": baseline_reason, "resultRef": baseline_ref, "budget": {"networkCalls": 0}}
        else:
            steps["baseline"] = {"stepId": "baseline", "state": "planned", "reason": baseline_reason, "inputs": {"profileId": request["profileId"], "property": request["property"], "webStream": request["webStream"], "gtmContainer": request.get("gtmContainer"), "projectRoot": str(root)}, "budget": {"boundedBy": "existing-baseline-client-limits"}}

        search_periods = None
        if "search-console" in reusable:
            steps["search-console"] = {**deepcopy(reusable["search-console"]), "state": "reused", "reason": "The completed exact Search Console report is reused from the verified checkpoint."}
            search_periods = fixed_periods
        elif search_available:
            search_request = {
                "schemaVersion": 1, "artifactType": "search-console-report-request", "createdAt": _utc(stamp),
                "requestId": f"advisor-sc-{stamp.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
                "projectRoot": str(root), "profileId": request["profileId"], "site": request["site"],
                "question": request["question"], "language": request["language"],
                "period": {"mode": "explicit", "days": None, "from": fixed_periods[0]["from"], "to": fixed_periods[0]["to"]} if fixed_periods else deepcopy(request["period"]),
                "comparisons": request["comparisons"], "presets": ["overview", "pages", "devices"],
                "searchType": "web", "dataState": "final", "filters": [], "contentSha256": "",
            }
            search_request["contentSha256"] = search_console_request_sha256(search_request)
            search_request_path, search_request_ref = self._write_request(store, "advisor-source-requests", search_request)
            try:
                result = self.search_console_service.plan(request["profileId"], request["site"], search_request_path)
                planning_network = True
                search_plan, search_plan_ref = self._child_plan_ref(result)
                search_periods = search_plan["periods"]
                expiries.append(parse_time(search_plan["expiresAt"]))
                state = "planned" if result.get("status") == "ready" else "blocked"
                steps["search-console"] = {"stepId": "search-console", "state": state, "reason": "A bounded finalized Search Console source plan is ready." if state == "planned" else "The Search Console source plan has blockers.", "requestRef": search_request_ref, "planRef": search_plan_ref, "budget": search_plan.get("budget", {}), "blockers": search_plan.get("blockers", [])}
            except AdvisorError as exc:
                planning_network = True
                steps["search-console"] = {"stepId": "search-console", "state": "blocked", "reason": exc.message, "requestRef": search_request_ref, "failure": _failure(exc), "budget": {"maxRequests": 20, "maxRows": 12000}}
                update_domain(domains, "search-console-performance", "blocked", exc.message)
        else:
            reason = "No exact readable Search Console property is selected; GA4 can continue independently."
            steps["search-console"] = {"stepId": "search-console", "state": "unavailable", "reason": reason, "budget": {"networkCalls": 0}}

        if "ga4" in reusable:
            steps["ga4"] = {**deepcopy(reusable["ga4"]), "state": "reused", "reason": "The completed exact GA4 report is reused from the verified checkpoint."}
            ga_periods = fixed_periods
        else:
            aligned = search_periods or fixed_periods
            ga_period = {"mode": "explicit", "days": None, "from": aligned[0]["from"], "to": aligned[0]["to"]} if aligned else deepcopy(request["period"])
            presets = ["overview", "acquisition", "landing", "content", "device", "geo", "events", "key-events"]
            if ecommerce:
                presets.append("ecommerce")
            if search_available:
                presets.extend(["google-organic-overview", "google-organic-landing", "google-organic-device"])
            ga_request = {
                "schemaVersion": 1, "artifactType": "report-request", "createdAt": _utc(stamp),
                "requestId": f"advisor-ga4-{stamp.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
                "projectRoot": str(root), "profileId": request["profileId"], "property": request["property"],
                "webStream": request["webStream"] if search_available else None,
                "question": request["question"], "language": request["language"], "period": ga_period,
                "comparisons": request["comparisons"], "presets": presets, "includeToday": False,
                "experimentalFunnel": False, "alphaDisclosureAccepted": False,
                "baselineRef": baseline_ref["path"] if baseline_state == "reused" and baseline_ref else None,
                "measurementPlanRef": str(measurement_path) if measurement_path else None,
                "contentSha256": "",
            }
            ga_request["contentSha256"] = report_request_sha256(ga_request)
            ga_request_path, ga_request_ref = self._write_request(store, "advisor-source-requests", ga_request)
            try:
                result = self.report_service.plan(request["profileId"], request["property"], ga_request_path)
                planning_network = True
                ga_plan, ga_plan_ref = self._child_plan_ref(result)
                ga_periods = ga_plan["periods"]
                expiries.append(parse_time(ga_plan["expiresAt"]))
                state = "planned" if result.get("status") == "ready" else "blocked"
                steps["ga4"] = {"stepId": "ga4", "state": state, "reason": "A bounded GA4 source plan is ready." if state == "planned" else "The GA4 source plan has blockers.", "requestRef": ga_request_ref, "planRef": ga_plan_ref, "budget": {"maxQueries": 24, "plannedQueries": len(ga_plan.get("queries", [])), "maxRowsPerQuery": 1000, "maxPagesPerQuery": 4}, "blockers": ga_plan.get("blockers", [])}
            except AdvisorError as exc:
                planning_network = True
                ga_periods = aligned or []
                steps["ga4"] = {"stepId": "ga4", "state": "blocked", "reason": exc.message, "requestRef": ga_request_ref, "failure": _failure(exc), "budget": {"maxQueries": 24, "maxRowsPerQuery": 1000}}
                for key in ("overview", "acquisition", "landing-and-content", "device-and-geo", "events-and-key-events"):
                    update_domain(domains, key, "blocked", exc.message)

        periods = fixed_periods or (ga_periods if ga_periods else search_periods)
        if not periods:
            raise AdvisorError("ADVISOR_PLAN_BLOCKED", "No source planner returned exact report periods.", EXIT_INPUT)
        period_labels = {item.get("label") for item in periods}
        both = (
            steps["ga4"]["state"] in {"planned", "reused"}
            and steps["search-console"]["state"] in {"planned", "reused"}
            and {"current", "previous"}.issubset(period_labels)
        )
        if "cross-source" in reusable:
            steps["cross-source"] = {**deepcopy(reusable["cross-source"]), "state": "reused", "reason": "The completed exact local cross-source report is reused from the verified checkpoint."}
        elif both:
            steps["cross-source"] = {"stepId": "cross-source", "state": "planned", "reason": "Local exact-only cross-source analysis will run after both source reports.", "budget": {"networkCalls": 0, "maxPageMappings": 500}}
        else:
            steps["cross-source"] = {"stepId": "cross-source", "state": "not_applicable", "reason": "Both exact source reports and matching current/previous periods are required for cross-source analysis.", "budget": {"networkCalls": 0}}
            update_domain(domains, "cross-source-context", "not_applicable", steps["cross-source"]["reason"])
        steps["synthesis"] = {"stepId": "synthesis", "state": "planned", "reason": "Deterministic local synthesis runs after the available source steps.", "budget": {"networkCalls": 0}}

        blockers = []
        if not any(steps[key]["state"] in {"planned", "reused"} for key in ("ga4", "search-console")):
            blockers.append("Neither GA4 nor Search Console has a runnable or reusable performance source.")
        expires = min(expiries) if expiries else stamp + timedelta(minutes=30)
        plan = {
            "schemaVersion": 1, "artifactType": "advisor-assessment-plan", "generatedAt": _utc(stamp),
            "expiresAt": _utc(expires), "planId": f"advisor-plan-{stamp.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
            "planSha256": "", "projectRoot": str(root), "request": request,
            "requestRef": {"artifactType": "advisor-assessment-request", "path": str(request_path), "fileSha256": file_sha256(request_path), "artifactSha256": request["contentSha256"]},
            "identities": {"profileId": request["profileId"], "property": request["property"], "webStream": request["webStream"], "site": request.get("site"), "verifiedOrigin": request["verifiedOrigin"]},
            "periods": periods, "steps": [steps[key] for key in STEP_ORDER], "domains": domains,
            "budget": {"execution": "sequential", "sources": {key: steps[key].get("budget", {}) for key in ("baseline", "ga4", "search-console", "cross-source")}, "automaticFullSuiteRetry": False},
            "resumeFrom": resume_ref, "blockers": blockers, "limitations": limitations,
            "planningNetworkUsed": planning_network, "mutationPerformed": False,
        }
        plan["planSha256"] = assessment_plan_sha256(plan)
        validate_artifact_data("advisor-assessment-plan", plan)
        location = store.write_named_artifact("advisor-assessment-plans", plan["planId"], plan)
        return {"status": "blocked" if blockers else "ready", "plan": plan, "artifact": location, "mutationPerformed": False}

    def _load_plan(self, path: Path, *, allow_blocked: bool = False) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        plan = read_json(resolved, "advisor assessment plan")
        if plan.get("planSha256") != assessment_plan_sha256(plan):
            raise AdvisorError("ADVISOR_PLAN_TAMPERED", "The advisor plan SHA-256 does not match its content.", EXIT_INPUT)
        validate_artifact_data("advisor-assessment-plan", plan, path_label=str(resolved))
        if self.now().astimezone(timezone.utc) > parse_time(plan["expiresAt"]):
            raise AdvisorError("ADVISOR_PLAN_EXPIRED", "The advisor plan expired; create a fresh or resumed plan.", EXIT_INPUT)
        request_path = Path(plan["requestRef"]["path"]).resolve()
        if not request_path.is_file() or file_sha256(request_path) != plan["requestRef"]["fileSha256"]:
            raise AdvisorError("ADVISOR_REQUEST_TAMPERED", "The advisor request changed after planning.", EXIT_INPUT)
        current = self._load_request(request_path)
        if current != plan["request"]:
            raise AdvisorError("ADVISOR_CONTEXT_DRIFT", "The advisor request content changed after planning.", EXIT_INPUT)
        if plan["blockers"] and not allow_blocked:
            raise AdvisorError("ADVISOR_PLAN_BLOCKED", "The advisor plan has unresolved blockers.", EXIT_INPUT, details={"blockers": plan["blockers"]})
        return plan

    def _checkpoint(
        self, plan: dict[str, Any], steps: list[dict[str, Any]], domains: list[dict[str, Any]],
        budget: dict[str, Any], previous: dict[str, Any] | None, status: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        generated = self.now().astimezone(timezone.utc)
        sequence = int(previous["sequence"]) + 1 if previous else 1
        previous_ref = None
        if previous:
            previous_path = Path(previous["_path"])
            previous_ref = _source_ref(previous_path, previous)
        checkpoint = {
            "schemaVersion": 1, "artifactType": "advisor-assessment-checkpoint", "generatedAt": _utc(generated),
            "checkpointId": f"advisor-checkpoint-{generated.strftime('%Y%m%dT%H%M%SZ')}-{sequence:02d}-{uuid.uuid4().hex[:8]}",
            "checkpointSha256": "", "sequence": sequence, "projectRoot": plan["projectRoot"],
            "requestId": plan["request"]["requestId"], "planId": plan["planId"], "planSha256": plan["planSha256"],
            "previousCheckpoint": previous_ref, "identities": plan["identities"], "periods": plan["periods"],
            "steps": deepcopy(steps), "domains": deepcopy(domains), "budget": deepcopy(budget),
            "status": status, "networkUsed": any(item.get("state") == "completed" and item.get("stepId") in {"baseline", "ga4", "search-console"} for item in steps),
            "mutationPerformed": False,
        }
        checkpoint["checkpointSha256"] = assessment_checkpoint_sha256(checkpoint)
        validate_artifact_data("advisor-assessment-checkpoint", checkpoint)
        location = ArtifactStore(Path(plan["projectRoot"])).write_named_artifact("advisor-assessment-checkpoints", checkpoint["checkpointId"], checkpoint)
        checkpoint["_path"] = location["path"]
        return checkpoint, location

    def _step_result(self, step: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
        ref = step.get("resultRef")
        if not isinstance(ref, dict):
            raise AdvisorError("ADVISOR_CHECKPOINT_TAMPERED", "A reused step has no result reference.", EXIT_INPUT)
        path = Path(ref["path"]).resolve()
        if not path.is_file() or file_sha256(path) != ref.get("fileSha256"):
            raise AdvisorError("ADVISOR_CHECKPOINT_TAMPERED", "A reused result changed.", EXIT_INPUT)
        value = read_json(path, f"{step['stepId']} result")
        if _internal_hash(value) and _internal_hash(value) != ref.get("artifactSha256"):
            raise AdvisorError("ADVISOR_CHECKPOINT_TAMPERED", "A reused result internal hash changed.", EXIT_INPUT)
        return path, value

    def _mark_ga4_domains(self, domains: list[dict[str, Any]], report: dict[str, Any]) -> None:
        presets = {item.get("preset") for item in report.get("datasets", [])}
        mapping = {
            "overview": {"overview"}, "acquisition": {"acquisition"},
            "landing-and-content": {"landing", "content"}, "device-and-geo": {"device", "geo"},
            "events-and-key-events": {"events", "key-events"}, "ecommerce": {"ecommerce"},
        }
        for key, expected in mapping.items():
            current = next(item for item in domains if item["domainId"] == key)
            if current["state"] == "not_applicable":
                continue
            found = sorted(expected & presets)
            update_domain(domains, key, "checked" if found else "unavailable", "The GA4 source report contains the required bounded dataset(s)." if found else "The GA4 source report did not contain an applicable dataset.", [f"ga4:dataset:{name}" for name in found])

    def _write_cross_request(self, plan: dict[str, Any], ga_path: Path, ga: dict[str, Any], sc_path: Path, sc: dict[str, Any], baseline_ref: dict[str, Any] | None) -> Path:
        generated = self.now().astimezone(timezone.utc)
        periods = {item["label"]: {"from": item["from"], "to": item["to"]} for item in plan["periods"] if item["label"] in {"current", "previous"}}
        request = {
            "schemaVersion": 1, "artifactType": "cross-source-analysis-request", "createdAt": _utc(generated),
            "requestId": f"cross-source-request-advisor-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
            "projectRoot": plan["projectRoot"], "profileId": plan["identities"]["profileId"],
            "property": plan["identities"]["property"], "webStream": plan["identities"]["webStream"],
            "site": plan["identities"]["site"], "question": plan["request"]["question"], "language": plan["request"]["language"],
            "ga4Report": {"path": str(ga_path), "fileSha256": file_sha256(ga_path), "artifactSha256": ga["reportSha256"]},
            "searchConsoleReport": {"path": str(sc_path), "fileSha256": file_sha256(sc_path), "artifactSha256": sc["reportSha256"]},
            "analyses": ["overview-handoff", "landing-opportunities", "device-context"],
            "expectedPeriods": periods, "verifiedOrigin": plan["identities"]["verifiedOrigin"],
            "baselineRef": baseline_ref["path"] if baseline_ref else None,
            "measurementPlanRef": plan["request"].get("measurementPlanRef"), "inspectionReportRef": None,
            "contentSha256": "",
        }
        request["contentSha256"] = cross_source_request_sha256(request)
        path, _ = self._write_request(ArtifactStore(Path(plan["projectRoot"])), "advisor-source-requests", request)
        return path

    def _evidence_items(self, value: dict[str, Any], source: str, field: str) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for index, item in enumerate(value.get(field, [])):
            current = dict(item) if isinstance(item, dict) else {"statement": str(item)}
            refs = current.get("evidenceRefs") if isinstance(current.get("evidenceRefs"), list) else []
            current["evidenceRefs"] = [ref if str(ref).startswith(("ga4:", "search-console:", "cross-source:", "baseline:")) else f"{source}:{ref}" for ref in refs]
            if not current["evidenceRefs"]:
                current["evidenceRefs"] = [f"{source}:{field}:{index + 1}"]
            output.append(current)
        return output

    def _synthesize(self, plan: dict[str, Any], steps: list[dict[str, Any]], domains: list[dict[str, Any]], latest: dict[str, Any]) -> dict[str, Any]:
        sources: dict[str, Any] = {}
        documents: dict[str, dict[str, Any]] = {}
        for step in steps:
            if step.get("state") not in {"completed", "reused"} or not step.get("resultRef"):
                continue
            path, value = self._step_result(step)
            documents[step["stepId"]] = value
            sources[step["stepId"]] = {**step["resultRef"], "status": value.get("status", value.get("completeness", "ready")), "qualityTier": value.get("qualityTier"), "generatedAt": value.get("generatedAt")}

        facts: list[dict[str, Any]] = []
        calculations: list[dict[str, Any]] = []
        interpretations: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        limitations: list[dict[str, Any]] = []
        recommendation_candidates: list[dict[str, Any]] = []
        questions: list[str] = []
        for step_id, source in (("ga4", "ga4"), ("search-console", "search-console"), ("cross-source", "cross-source")):
            value = documents.get(step_id)
            if not value:
                continue
            facts.extend(self._evidence_items(value, source, "facts"))
            calculations.extend(self._evidence_items(value, source, "calculations"))
            interpretations.extend(self._evidence_items(value, source, "interpretations"))
            findings.extend(self._evidence_items(value, source, "findings"))
            for index, item in enumerate(value.get("limitations", [])):
                current = dict(item) if isinstance(item, dict) else {"message": str(item)}
                current.setdefault("source", source)
                current.setdefault("evidenceRef", f"{source}:limitation:{index + 1}")
                limitations.append(current)
            for item in value.get("recommendations", []):
                normalized = normalized_recommendation(item, source)
                if normalized:
                    recommendation_candidates.append(normalized)
            questions.extend(str(item) for item in value.get("questions", []) if item)

        baseline = documents.get("baseline")
        if baseline:
            for index, item in enumerate(baseline.get("findings", [])):
                current = dict(item) if isinstance(item, dict) else {"statement": str(item)}
                current.setdefault("statement", current.get("message", "Baseline finding"))
                current["evidenceRefs"] = [f"baseline:finding:{current.get('code', index + 1)}"]
                findings.append(current)
            for index, item in enumerate(baseline.get("limitations", [])):
                limitations.append({"source": "baseline", "code": item.get("code", "BASELINE_LIMITATION") if isinstance(item, dict) else "BASELINE_LIMITATION", "message": item.get("message", str(item)) if isinstance(item, dict) else str(item), "evidenceRef": f"baseline:limitation:{index + 1}"})
            for item in baseline.get("recommendations", []):
                normalized = normalized_recommendation(item, "baseline")
                if normalized:
                    recommendation_candidates.append(normalized)

        if not plan["request"].get("measurementPlanRef"):
            questions.insert(0, "Which measured event is the authoritative completed business outcome?")
        questions = list(dict.fromkeys(questions))[:3]
        recommendations = prioritize_recommendations(recommendation_candidates)
        checked = sum(item["state"] == "checked" for item in domains)
        failed_sources = [item for item in steps if item.get("stepId") in {"baseline", "ga4", "search-console"} and item.get("state") in {"blocked", "failed"}]
        performance_sources = [key for key in ("ga4", "search-console") if key in documents]
        language = plan["request"].get("language", "en")
        ru = language == "ru"
        if not performance_sources:
            status, quality = "blocked", "insufficient"
            diagnosis = (
                "Надёжная картина не получена: оба источника данных о результатах недоступны."
                if ru else "A reliable picture could not be produced because both performance-data sources were unavailable."
            )
        elif failed_sources or any(item["state"] in {"blocked", "unavailable", "stale", "not_checked"} for item in domains):
            status, quality = "partial", "directional_only"
            diagnosis = (
                "Получена частичная картина: доступные источники проверены, но некоторые выводы ограничены пробелами или качеством измерений."
                if ru else "A partial picture was produced: available sources were checked, but some conclusions are limited by evidence or measurement gaps."
            )
        else:
            status = "ready"
            source_qualities = {value.get("qualityTier") for value in documents.values()}
            quality = "directional_only" if any(item in {"directional_only", "insufficient", "preliminary"} for item in source_qualities) else "reliable_for_description"
            diagnosis = (
                "GA4, Search Console и измерительный контекст объединены в одну проверяемую картину сайта."
                if ru else "GA4, Search Console, and measurement context were combined into one verifiable website picture."
            )
        reasons = [f"Checked {checked} of {len(domains)} domains."]
        if failed_sources:
            reasons.append("Some source steps were blocked or failed; their dependent conclusions were not invented.")
        if any(item.get("source") == "cross-source" for item in limitations):
            reasons.append("GA4 and Search Console keep separate definitions and timezone limitations.")
        if recommendations:
            first = recommendations[0]
            if first["requiresMutationWorkflow"]:
                safe_next = (
                    f"Подготовить отдельный безопасный план для рекомендации №1: {first['problem']}"
                    if ru else f"Prepare a separate safe plan for recommendation 1: {first['problem']}"
                )
            else:
                safe_next = (
                    f"Проверить рекомендацию №1 без изменений: {first['verification']}"
                    if ru else f"Verify recommendation 1 without changes: {first['verification']}"
                )
        elif questions:
            safe_next = f"Уточнить бизнес-контекст: {questions[0]}" if ru else f"Clarify the business context: {questions[0]}"
        else:
            safe_next = (
                "Сохранить текущую настройку и повторить те же ограниченные проверки после следующего полного периода."
                if ru else "Keep the current setup and repeat the same bounded checks after the next complete period."
            )
        generated = self.now().astimezone(timezone.utc)
        latest_path = Path(latest["_path"])
        report = {
            "schemaVersion": 1, "artifactType": "advisor-assessment-report", "generatedAt": _utc(generated),
            "reportId": f"advisor-report-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
            "reportSha256": "", "requestId": plan["request"]["requestId"], "planId": plan["planId"],
            "projectRoot": plan["projectRoot"], "language": language, "status": status, "qualityTier": quality,
            "identities": plan["identities"], "periods": plan["periods"], "sources": sources,
            "domains": domains, "diagnosis": diagnosis, "confidence": {"level": quality, "reasons": reasons},
            "facts": facts, "calculations": calculations, "interpretations": interpretations,
            "findings": findings, "limitations": limitations, "recommendations": recommendations,
            "questions": questions, "safeNextStep": safe_next,
            "latestCheckpoint": _source_ref(latest_path, latest),
            "networkUsed": any(key in documents and steps[STEP_ORDER.index(key)].get("state") == "completed" for key in ("baseline", "ga4", "search-console")),
            "mutationPerformed": False,
        }
        report["reportSha256"] = assessment_report_sha256(report)
        validate_artifact_data("advisor-assessment-report", report)
        return report

    def run(self, plan_path: Path) -> dict[str, Any]:
        plan = self._load_plan(plan_path)
        steps = deepcopy(plan["steps"])
        for step in steps:
            if step["state"] == "planned":
                step["state"] = "pending"
            elif step["state"] in {"not_applicable", "unavailable"}:
                step["state"] = "skipped"
        domains = deepcopy(plan["domains"])
        budget = deepcopy(plan["budget"])
        previous: dict[str, Any] | None = None
        latest_location: dict[str, Any] | None = None

        def checkpoint(status: str = "running") -> None:
            nonlocal previous, latest_location
            previous, latest_location = self._checkpoint(plan, steps, domains, budget, previous, status)

        def execute_source(step_id: str, callback: Callable[[], dict[str, Any]]) -> tuple[Path | None, dict[str, Any] | None]:
            step = next(item for item in steps if item["stepId"] == step_id)
            if step["state"] == "reused":
                path, value = self._step_result(step)
                checkpoint()
                return path, value
            if step["state"] != "pending":
                checkpoint()
                return None, None
            try:
                result = callback()
                value = result.get("report") or result.get("audit")
                location = result.get("artifact")
                if not isinstance(value, dict) or not isinstance(location, dict) or not location.get("path"):
                    raise AdvisorError("ADVISOR_SOURCE_INVALID", f"The {step_id} service returned no immutable result artifact.", EXIT_INPUT)
                path = Path(location["path"])
                step["state"] = "completed"
                step["reason"] = f"The {step_id} step completed with immutable evidence."
                step["resultRef"] = _source_ref(path, value)
                checkpoint()
                return path, value
            except AdvisorError as exc:
                step["state"] = "failed"
                step["reason"] = exc.message
                step["failure"] = _failure(exc)
                checkpoint("blocked" if any(part in exc.code for part in HARD_FAILURE_PARTS) else "partial")
                if any(part in exc.code for part in HARD_FAILURE_PARTS):
                    raise
                return None, None

        baseline_step = next(item for item in steps if item["stepId"] == "baseline")
        baseline_path, baseline = execute_source("baseline", lambda: self.baseline_service.audit(
            plan["identities"]["profileId"], Path(plan["projectRoot"]),
            property_name=plan["identities"]["property"], stream_name=plan["identities"]["webStream"],
            gtm_container=plan["request"].get("gtmContainer"), experimental_alpha=False,
        ))
        if baseline:
            update_domain(domains, "tag-and-gtm-route", "checked", "The bounded baseline inspected the site and selected Google resources.", [f"baseline:{baseline.get('auditId')}"])
            update_domain(domains, "consent-and-coverage", "checked", "Consent and collection coverage evidence is recorded in the baseline.", [f"baseline:{baseline.get('auditId')}"])
        else:
            update_domain(domains, "tag-and-gtm-route", "blocked", baseline_step["reason"])
            update_domain(domains, "consent-and-coverage", "blocked", baseline_step["reason"])

        ga_step = next(item for item in steps if item["stepId"] == "ga4")
        ga_path, ga = execute_source("ga4", lambda: self.report_service.run(self._verified_child_plan_path(ga_step)))
        if ga:
            self._mark_ga4_domains(domains, ga)
            update_domain(domains, "business-goal-and-outcomes", "checked" if plan["request"].get("measurementPlanRef") else "unavailable", "Approved measurement meaning is combined with the GA4 evidence." if plan["request"].get("measurementPlanRef") else "Traffic is descriptive because no approved outcome mapping is available.", ["ga4:property-context"])
        else:
            for key in ("overview", "acquisition", "landing-and-content", "device-and-geo", "events-and-key-events"):
                update_domain(domains, key, "blocked", ga_step["reason"])

        sc_step = next(item for item in steps if item["stepId"] == "search-console")
        sc_path, sc = execute_source("search-console", lambda: self.search_console_service.run(self._verified_child_plan_path(sc_step)))
        if sc:
            update_domain(domains, "search-console-performance", "checked", "Finalized bounded Search Console evidence was collected.", [f"search-console:{sc.get('reportId')}"])
        elif sc_step["state"] == "failed":
            update_domain(domains, "search-console-performance", "blocked", sc_step["reason"])

        cross_step = next(item for item in steps if item["stepId"] == "cross-source")
        cross_path = None
        cross = None
        if cross_step["state"] == "reused":
            cross_path, cross = execute_source("cross-source", lambda: {})
        elif cross_step["state"] == "pending" and ga_path and ga and sc_path and sc:
            cross_request = self._write_cross_request(plan, ga_path, ga, sc_path, sc, _source_ref(baseline_path, baseline) if baseline_path and baseline else None)
            def run_cross() -> dict[str, Any]:
                planned = self.cross_source_service.plan(cross_request)
                if planned.get("status") != "ready":
                    raise AdvisorError("ADVISOR_CROSS_SOURCE_BLOCKED", "The local cross-source plan has blockers.", EXIT_INPUT, details={"blockers": planned.get("plan", {}).get("blockers", [])})
                return self.cross_source_service.run(Path(planned["artifact"]["path"]))
            cross_path, cross = execute_source("cross-source", run_cross)
        else:
            cross_step["state"] = "skipped"
            cross_step["reason"] = "Cross-source analysis was skipped because both compatible source reports were not available."
            checkpoint("partial")
        if cross:
            update_domain(domains, "cross-source-context", "checked", "Exact-only local GA4/Search Console analysis completed.", [f"cross-source:{cross.get('reportId')}"])
        elif cross_step["state"] in {"failed", "skipped"}:
            update_domain(domains, "cross-source-context", "unavailable", cross_step["reason"])

        update_domain(domains, "data-quality-and-quota", "checked", "Source quality, completeness and quota evidence were preserved in the source artifacts.", ["advisor:source-registry"])
        update_domain(domains, "unanswered-business-questions", "checked", "Remaining business questions are explicitly recorded in the final report.", ["advisor:questions"])
        synth = next(item for item in steps if item["stepId"] == "synthesis")
        synth["state"] = "completed"
        synth["reason"] = "Deterministic local synthesis completed without a remote mutation."
        checkpoint("ready")
        assert previous is not None and latest_location is not None
        report = self._synthesize(plan, steps, domains, previous)
        report_location = ArtifactStore(Path(plan["projectRoot"])).write_named_artifact("advisor-assessment-reports", report["reportId"], report)
        status = report["status"]
        return {"status": status, "report": report, "artifact": report_location, "checkpoint": latest_location, "networkUsed": report["networkUsed"], "mutationPerformed": False}

    def resume_plan(self, checkpoint_path: Path) -> dict[str, Any]:
        checkpoint = self._load_checkpoint(checkpoint_path)
        root = Path(checkpoint["projectRoot"])
        original_plan_path = ArtifactStore(root).root / "advisor-assessment-plans" / f"{checkpoint['planId']}.json"
        original_plan = read_json(original_plan_path, "original advisor plan")
        validate_artifact_data("advisor-assessment-plan", original_plan, path_label=str(original_plan_path))
        if original_plan.get("planSha256") != checkpoint["planSha256"] or original_plan.get("planSha256") != assessment_plan_sha256(original_plan):
            raise AdvisorError("ADVISOR_CHECKPOINT_TAMPERED", "The checkpoint no longer matches its original immutable plan.", EXIT_INPUT)
        return self.plan(Path(original_plan["requestRef"]["path"]), resume_checkpoint=checkpoint_path)

    def show_plan(self, path: Path) -> dict[str, Any]:
        plan = self._load_plan(path, allow_blocked=True)
        return {"status": "blocked" if plan["blockers"] else "ready", "planSha256": plan["planSha256"], "expiresAt": plan["expiresAt"], "identities": plan["identities"], "periods": plan["periods"], "steps": plan["steps"], "domains": plan["domains"], "budget": plan["budget"], "blockers": plan["blockers"], "limitations": plan["limitations"], "mutationPerformed": False}

    def show(self, path: Path, language: str) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        report = read_json(resolved, "advisor assessment report")
        if report.get("reportSha256") != assessment_report_sha256(report):
            raise AdvisorError("ADVISOR_REPORT_TAMPERED", "The advisor report SHA-256 does not match its content.", EXIT_INPUT)
        validate_artifact_data("advisor-assessment-report", report, path_label=str(resolved))
        selected = report.get("language", "en") if language == "auto" else language
        if selected not in {"ru", "en"}:
            selected = "en"
        return {"status": report["status"], "report": report, "plain": render_assessment(report, selected), "networkUsed": False, "mutationPerformed": False}
