"""Immutable local website patch planning, one-shot apply, and readback."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .artifact_store import ArtifactStore, canonical_json
from .contracts import validate_artifact_data
from .errors import AdvisorError, EXIT_CONFIGURATION, EXIT_INPUT
from .ga4_mutation_service import mutation_plan_sha256
from .measurement_policy import plan_content_sha256
from .site_scanner import inspect_site
from .website_context import _project_evidence, build_context, load_context
from .website_patch import sha256_bytes, simulate_patch
from .website_policy import validate_request, validate_simulated_outputs
from .website_renderer import render_plan


HEX64 = set("0123456789abcdef")


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdvisorError("INVALID_INPUT_FILE", f"The {label} could not be read as JSON.", EXIT_INPUT) from exc
    if not isinstance(value, dict):
        raise AdvisorError("INVALID_INPUT_FILE", f"The {label} must be a JSON object.", EXIT_INPUT)
    return value


def _approved_measurement(path: Path, binding: dict[str, Any]) -> dict[str, Any]:
    value = _load(path, "measurement plan")
    validate_artifact_data("measurement-plan", value, path_label=str(path))
    if (
        value.get("schemaVersion") != 2 or value.get("status") != "approved"
        or value.get("planId") != binding.get("planId") or value.get("contentSha256") != binding.get("contentSha256")
        or value.get("contentSha256") != plan_content_sha256(value) or value.get("approvalSha256") != value.get("contentSha256")
    ):
        raise AdvisorError("MEASUREMENT_PLAN_TAMPERED", "The approved measurement plan binding is stale or invalid.", EXIT_INPUT)
    return value


def _utc(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class WebsiteService:
    def __init__(self, *, now: Callable[[], datetime] | None = None, replace: Callable[[Path, Path], None] | None = None) -> None:
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.replace = replace or (lambda source, destination: os.replace(source, destination))

    def context(self, project_root: Path, measurement_plan: Path, baseline: Path | None = None) -> dict[str, Any]:
        return build_context(project_root, measurement_plan, baseline)

    def plan(self, context_path: Path, changes_path: Path, patch_path: Path) -> dict[str, Any]:
        context_path = context_path.resolve()
        context = load_context(context_path)
        root = Path(context["projectRoot"]).resolve()
        _, _, current_content_sha, _ = _project_evidence(root)
        if current_content_sha != context["projectContentSha256"]:
            raise AdvisorError("WEBSITE_CONTEXT_STALE", "Project integration files changed after context discovery.", EXIT_INPUT)
        measurement_path = Path(context["measurementPlan"]["path"]).resolve()
        measurement = _approved_measurement(measurement_path, context["measurementPlan"])
        request = _load(changes_path.resolve(), "website change request")
        validate_artifact_data("website-change-request", request, path_label=str(changes_path))
        commands = validate_request(request, context, measurement)
        try:
            raw_patch = patch_path.resolve().read_bytes()
        except OSError as exc:
            raise AdvisorError("INVALID_PATCH", "The unified diff could not be read.", EXIT_INPUT) from exc
        simulations = simulate_patch(root, raw_patch)
        validate_simulated_outputs(request["route"], simulations, context)
        if all(item["beforeSha256"] == item["afterSha256"] for item in simulations):
            return {"status": "no_op", "plan": None, "artifact": None, "mutationPerformed": False}
        patch_sha = sha256_bytes(raw_patch)
        store = ArtifactStore(root)
        patch_location = store.write_content_artifact("patches", patch_sha, raw_patch, suffix=".patch")
        generated = self.now().astimezone(timezone.utc)
        operations: list[dict[str, Any]] = []
        preconditions: list[dict[str, Any]] = []
        for index, item in enumerate(simulations, 1):
            operation_id = f"file-{index}-{uuid.uuid4().hex[:8]}"
            operations.append({
                "operationId": operation_id, "kind": "FILE_CREATE" if item["create"] else "FILE_PATCH",
                "method": "FILE_PATCH", "resource": item["path"], "fieldMask": [], "body": {},
                "rationale": "Install the approved website measurement implementation without production deployment.",
                "expectedReadback": [item["afterSha256"]], "create": item["create"], "experimental": False,
                "before": {"sha256": item["beforeSha256"], "size": item["beforeSize"], "encoding": item["encoding"], "newline": item["newline"]},
            })
            preconditions.append({
                "operationId": operation_id, "resource": item["path"],
                "snapshotId": f"file-state-{item['beforeSha256'][:16]}", "stateSha256": item["beforeSha256"],
            })
        plan_id = f"website-mutation-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
        plan: dict[str, Any] = {
            "schemaVersion": 3, "artifactType": "mutation-plan", "generatedAt": _utc(generated),
            "expiresAt": _utc(generated + timedelta(minutes=30)), "planId": plan_id, "planSha256": "",
            "target": "website", "riskClass": "LOCAL_CODE_CHANGE", "projectRoot": str(root),
            "contextId": context["contextId"], "contextSha256": context["contextSha256"],
            "measurementPlan": context["measurementPlan"], "patchPath": patch_location["path"],
            "patchSha256": patch_sha, "patchFormat": "unified-diff-v1", "route": request["route"],
            "preconditions": preconditions, "operations": operations,
            "expectedReadback": [f"{item['path']}:{item['afterSha256']}" for item in simulations],
            "verificationCommands": commands, "deploymentApproved": False,
            "executionPolicy": {"maxApplies": 1, "stopOnStale": True, "allowDelete": False, "allowRename": False, "safeRecovery": True},
        }
        plan["planSha256"] = mutation_plan_sha256(plan)
        validate_artifact_data("mutation-plan", plan)
        location = store.write_mutation_plan(plan)
        return {"status": "confirmation_required", "plan": plan, "artifact": location, "preview": render_plan(plan), "mutationPerformed": False}

    @staticmethod
    def _validate_plan(plan: dict[str, Any]) -> None:
        if plan.get("schemaVersion") != 3 or plan.get("artifactType") != "mutation-plan" or plan.get("target") != "website":
            raise AdvisorError("INVALID_MUTATION_PLAN", "Unsupported website mutation plan.", EXIT_INPUT)
        expected = mutation_plan_sha256(plan)
        digest = plan.get("planSha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in HEX64 for char in digest) or digest != expected:
            raise AdvisorError("MUTATION_PLAN_TAMPERED", "The website plan SHA-256 does not match its content.", EXIT_INPUT)
        validate_artifact_data("mutation-plan", plan)
        if plan.get("executionPolicy") != {"maxApplies": 1, "stopOnStale": True, "allowDelete": False, "allowRename": False, "safeRecovery": True} or plan.get("deploymentApproved") is not False:
            raise AdvisorError("INVALID_MUTATION_PLAN", "The Stage 8 execution policy is incomplete.", EXIT_INPUT)

    def show(self, plan_path: Path) -> dict[str, Any]:
        plan = _load(plan_path.resolve(), "website mutation plan")
        self._validate_plan(plan)
        return {"status": "confirmation_required", "plan": plan, "preview": render_plan(plan), "mutationPerformed": False}

    def apply(self, plan_path: Path, confirmation: str) -> dict[str, Any]:
        plan_path = plan_path.resolve()
        plan = _load(plan_path, "website mutation plan")
        self._validate_plan(plan)
        if confirmation != plan["planSha256"]:
            raise AdvisorError("CONFIRMATION_MISMATCH", "The exact website plan SHA-256 was not confirmed.", EXIT_INPUT)
        now = self.now().astimezone(timezone.utc)
        expires = datetime.fromisoformat(plan["expiresAt"].replace("Z", "+00:00"))
        if now >= expires:
            raise AdvisorError("MUTATION_PLAN_EXPIRED", "The website mutation plan expired; create a fresh context and plan.", EXIT_INPUT)
        root = Path(plan["projectRoot"]).resolve()
        store = ArtifactStore(root)
        if store.plan_was_consumed(plan["planSha256"]):
            raise AdvisorError("MUTATION_PLAN_REPLAYED", "This one-shot website plan was already consumed.", EXIT_INPUT)
        patch_path = Path(plan["patchPath"]).resolve()
        try:
            raw_patch = patch_path.read_bytes()
        except OSError as exc:
            raise AdvisorError("PATCH_ARTIFACT_MISSING", "The immutable patch artifact is unavailable.", EXIT_INPUT) from exc
        if sha256_bytes(raw_patch) != plan["patchSha256"]:
            raise AdvisorError("PATCH_ARTIFACT_TAMPERED", "The patch artifact SHA-256 changed.", EXIT_INPUT)
        context_path = store.root / "website-contexts" / f"{plan['contextId']}.json"
        context = load_context(context_path)
        if context["contextSha256"] != plan["contextSha256"]:
            raise AdvisorError("WEBSITE_CONTEXT_TAMPERED", "The website context binding changed.", EXIT_INPUT)
        _approved_measurement(Path(plan["measurementPlan"]["path"]), plan["measurementPlan"])
        _, _, current_content_sha, _ = _project_evidence(root)
        if current_content_sha != context["projectContentSha256"]:
            raise AdvisorError("WEBSITE_CONTEXT_STALE", "Project integration files changed after planning.", EXIT_INPUT)
        simulations = simulate_patch(root, raw_patch)
        operation_by_path = {item["resource"]: item for item in plan["operations"]}
        if set(operation_by_path) != {item["path"] for item in simulations}:
            raise AdvisorError("MUTATION_PLAN_TAMPERED", "Patch targets do not match the mutation plan.", EXIT_INPUT)
        for item in simulations:
            operation = operation_by_path[item["path"]]
            if operation["before"]["sha256"] != item["beforeSha256"] or operation["expectedReadback"] != [item["afterSha256"]]:
                raise AdvisorError("PATCH_PRECONDITION_FAILED", "A target file changed after planning.", EXIT_INPUT, details={"path": item["path"]})
        started = _utc(now)
        written: list[dict[str, Any]] = []
        operation_results: list[dict[str, Any]] = []
        status = "applied"
        error_message: str | None = None
        try:
            for item in simulations:
                target: Path = item["target"]
                temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.gaa.tmp")
                try:
                    with temporary.open("xb") as handle:
                        handle.write(item["after"])
                        handle.flush()
                        os.fsync(handle.fileno())
                    self.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
                written.append(item)
                operation_results.append({"path": item["path"], "status": "applied", "beforeSha256": item["beforeSha256"], "afterSha256": item["afterSha256"], "recovered": False})
        except Exception as exc:
            status = "failed"
            error_message = type(exc).__name__
            recovery_safe = True
            for item in reversed(written):
                target = item["target"]
                try:
                    current = target.read_bytes()
                    if sha256_bytes(current) != item["afterSha256"]:
                        recovery_safe = False
                        continue
                    if item["create"]:
                        target.unlink()
                    else:
                        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.gaa.restore")
                        with temporary.open("xb") as handle:
                            handle.write(item["before"])
                            handle.flush(); os.fsync(handle.fileno())
                        self.replace(temporary, target)
                        temporary.unlink(missing_ok=True)
                    for result in operation_results:
                        if result["path"] == item["path"]:
                            result["status"] = "recovered"; result["recovered"] = True
                except OSError:
                    recovery_safe = False
            if not recovery_safe:
                status = "partial"
        observed = []
        verified = status == "applied"
        for item in simulations:
            target = item["target"]
            current_sha = sha256_bytes(target.read_bytes()) if target.exists() else sha256_bytes(b"")
            observed.append({"path": item["path"], "sha256": current_sha, "expectedSha256": item["afterSha256"]})
            if current_sha != item["afterSha256"]:
                verified = False
        if status == "applied" and not verified:
            status = "partial"
        scan = inspect_site(root) if status == "applied" else {"findings": [], "publicIds": [], "truncated": False, "networkUsed": False, "codeExecuted": False}
        finished = _utc(self.now().astimezone(timezone.utc))
        observed_hash = hashlib.sha256(canonical_json(observed)).hexdigest()
        journal_id = f"website-journal-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
        journal = {
            "schemaVersion": 3, "artifactType": "journal-entry", "generatedAt": finished,
            "journalId": journal_id, "planId": plan["planId"], "planSha256": plan["planSha256"],
            "confirmationSha256": confirmation, "startedAt": started, "finishedAt": finished, "status": status,
            "requestIds": [], "readback": {"verified": verified, "observedStateSha256": observed_hash, "message": "Every planned file matched its expected SHA-256." if verified else "One or more file hashes did not match the approved plan."},
            "operations": operation_results, "projectRoot": str(root), "planPath": str(plan_path),
            "staticReadback": {"files": observed, "siteScan": {"publicIds": scan["publicIds"], "findings": scan["findings"], "truncated": scan["truncated"], "networkUsed": False, "codeExecuted": False}, "error": error_message},
            "verificationCommands": [{**command, "status": "pending", "executed": False} for command in plan.get("verificationCommands", [])],
            "deploymentPerformed": False, "productionEventsSent": False,
        }
        validate_artifact_data("journal-entry", journal)
        location = store.write_journal(journal)
        result_status = "pending_gtm_configuration" if status == "applied" and plan.get("route") == "gtm" else status
        return {"status": result_status, "journal": journal, "artifact": location, "mutationPerformed": bool(written), "deploymentPerformed": False}

    def verify(self, journal_path: Path) -> dict[str, Any]:
        journal = _load(journal_path.resolve(), "website journal")
        validate_artifact_data("journal-entry", journal, path_label=str(journal_path))
        if journal.get("schemaVersion") != 3:
            raise AdvisorError("INVALID_JOURNAL", "Only Stage 8 website journals can be verified here.", EXIT_INPUT)
        root = Path(journal["projectRoot"]).resolve()
        files = journal.get("staticReadback", {}).get("files", [])
        current = []
        verified = True
        for item in files:
            path = root.joinpath(*Path(item["path"]).parts)
            digest = sha256_bytes(path.read_bytes()) if path.exists() else sha256_bytes(b"")
            current.append({"path": item["path"], "sha256": digest, "expectedSha256": item["expectedSha256"]})
            verified = verified and digest == item["expectedSha256"]
        return {"status": "applied" if verified else "partial", "verified": verified, "files": current, "networkUsed": False, "codeExecuted": False, "deploymentPerformed": False}

    def reconcile(self, journal_path: Path) -> dict[str, Any]:
        result = self.verify(journal_path)
        result["reconciliationOnly"] = True
        result["mutationPerformed"] = False
        return result
