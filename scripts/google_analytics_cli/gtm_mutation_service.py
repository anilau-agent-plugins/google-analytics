"""Isolated, SHA-confirmed Google Tag Manager lifecycle for Stage 9."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from .artifact_store import ArtifactStore, canonical_json
from .auth import AuthService
from .contracts import validate_artifact_data
from .errors import AdvisorError, EXIT_CONFIGURATION, EXIT_INPUT, EXIT_NETWORK
from .gtm_renderer import render_plan
from .gtm_templates import build_entities
from .http import JsonResponse, JsonTransport
from .measurement_policy import approved_plan_is_valid, pii_issues
from .website_context import _project_evidence, load_context as load_site_context


GTM = "https://tagmanager.googleapis.com/tagmanager/v2"
RESOURCE = re.compile(r"^accounts/[A-Za-z0-9_-]+/containers/[A-Za-z0-9_-]+(?:/(?:workspaces|versions)/[A-Za-z0-9_-]+)?$")
WORKSPACE = re.compile(r"^accounts/[A-Za-z0-9_-]+/containers/[A-Za-z0-9_-]+/workspaces/[A-Za-z0-9_-]+$")
VERSION = re.compile(r"^accounts/[A-Za-z0-9_-]+/containers/[A-Za-z0-9_-]+/versions/[A-Za-z0-9_-]+$")
HEX64 = re.compile(r"^[a-f0-9]{64}$")
STAGES = {"WORKSPACE_CREATE", "WORKSPACE_SYNC", "ENTITY_BULK_UPDATE", "QUICK_PREVIEW", "VERSION_CREATE", "PUBLISH"}
RISK = {
    "WORKSPACE_CREATE": "REMOTE_CONFIG_CHANGE", "WORKSPACE_SYNC": "REMOTE_CONFIG_CHANGE",
    "ENTITY_BULK_UPDATE": "REMOTE_CONFIG_CHANGE", "QUICK_PREVIEW": "REMOTE_CONFIG_CHANGE",
    "VERSION_CREATE": "GTM_VERSION_CREATE", "PUBLISH": "GTM_PUBLISH",
}


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdvisorError("INVALID_INPUT_FILE", f"The {label} could not be read as JSON.", EXIT_INPUT) from exc
    if not isinstance(value, dict):
        raise AdvisorError("INVALID_INPUT_FILE", f"The {label} must be a JSON object.", EXIT_INPUT)
    return value


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def gtm_context_sha256(context: dict[str, Any]) -> str:
    value = dict(context)
    value["contextSha256"] = ""
    return _sha(value)


def gtm_plan_sha256(plan: dict[str, Any]) -> str:
    value = dict(plan)
    value.pop("planSha256", None)
    return _sha(value)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _container_of(resource: str) -> str:
    parts = resource.split("/")
    if len(parts) < 4:
        raise AdvisorError("INVALID_RESOURCE_NAME", "An exact GTM resource path is required.", EXIT_INPUT)
    return "/".join(parts[:4])


def _resource(value: Any, pattern: re.Pattern[str] = RESOURCE) -> str:
    text = str(value or "")
    if not pattern.fullmatch(text):
        raise AdvisorError("INVALID_RESOURCE_NAME", "An exact GTM resource path is required.", EXIT_INPUT, details={"resource": text[:256]})
    return text


def _contains(observed: Any, desired: Any) -> bool:
    if isinstance(desired, dict):
        return isinstance(observed, dict) and all(key in observed and _contains(observed[key], value) for key, value in desired.items())
    if isinstance(desired, list):
        return observed == desired
    return observed == desired


def _sync_clean(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("mergeConflict"):
        return False
    status = data.get("syncStatus", {})
    return not isinstance(status, dict) or not any(bool(status.get(key)) for key in ("mergeConflict", "syncError"))


class GtmMutationService:
    def __init__(
        self, *, auth: AuthService | None = None, transport: JsonTransport | None = None,
        now: Callable[[], datetime] | None = None, sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic, minimum_interval: float = 4.1,
    ) -> None:
        self.transport = transport or JsonTransport()
        self.auth = auth or AuthService(json_transport=self.transport)
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self.monotonic = monotonic
        self.minimum_interval = minimum_interval
        self._last_request: float | None = None

    def _request(self, method: str, path: str, token: str, *, payload: Any = None, query: dict[str, Any] | None = None, write: bool = False) -> JsonResponse:
        if not path.startswith("/") or ".." in path or "//" in path:
            raise AdvisorError("GTM_OPERATION_NOT_ALLOWED", "The GTM API path is not allowlisted.", EXIT_INPUT)
        now = self.monotonic()
        if self._last_request is not None:
            wait = self.minimum_interval - (now - self._last_request)
            if wait > 0:
                self.sleep(wait)
        url = GTM + path
        if query:
            url += "?" + urlencode([(key, value) for key, value in query.items() if value is not None])
        try:
            return self.transport.request(
                method, url, headers={"Authorization": f"Bearer {token}"}, payload=payload,
                max_attempts=1 if write else 3, retry_mode=None,
            )
        finally:
            self._last_request = self.monotonic()

    def _get(self, resource: str, token: str) -> tuple[dict[str, Any], list[str]]:
        response = self._request("GET", f"/{_resource(resource)}", token)
        if not isinstance(response.data, dict):
            raise AdvisorError("MALFORMED_HTTP_RESPONSE", "Google returned an invalid GTM resource.", EXIT_NETWORK)
        return response.data, [response.request_id] if response.request_id else []

    def _list(self, parent: str, collection: str, response_key: str, token: str) -> tuple[list[dict[str, Any]], list[str]]:
        values: list[dict[str, Any]] = []
        request_ids: list[str] = []
        page_token: str | None = None
        seen: set[str] = set()
        for _ in range(50):
            response = self._request("GET", f"/{_resource(parent)}/{collection}", token, query={"pageToken": page_token})
            data = response.data if isinstance(response.data, dict) else {}
            items = data.get(response_key, [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise AdvisorError("MALFORMED_HTTP_RESPONSE", "Google returned an invalid GTM resource list.", EXIT_NETWORK)
            values.extend(items)
            if len(values) > 10_000:
                raise AdvisorError("SNAPSHOT_INCOMPLETE", "The GTM resource list exceeded the safety bound.", EXIT_NETWORK)
            if response.request_id:
                request_ids.append(response.request_id)
            page_token = data.get("nextPageToken")
            if not page_token:
                return values, request_ids
            if not isinstance(page_token, str) or page_token in seen:
                raise AdvisorError("SNAPSHOT_INCOMPLETE", "GTM pagination did not complete safely.", EXIT_NETWORK)
            seen.add(page_token)
        raise AdvisorError("SNAPSHOT_INCOMPLETE", "GTM pagination exceeded the page bound.", EXIT_NETWORK)

    def _live(self, container: str, token: str) -> tuple[dict[str, Any], list[str]]:
        response = self._request("GET", f"/{_resource(container)}/versions/live", token)
        data = response.data if isinstance(response.data, dict) else {}
        return data, [response.request_id] if response.request_id else []

    def _workspace_state(self, workspace: str, token: str) -> tuple[dict[str, Any], list[str]]:
        workspace = _resource(workspace, WORKSPACE)
        current, ids = self._get(workspace, token)
        status_response = self._request("GET", f"/{workspace}/status", token)
        status = status_response.data if isinstance(status_response.data, dict) else {}
        if status_response.request_id:
            ids.append(status_response.request_id)
        tags, tag_ids = self._list(workspace, "tags", "tag", token)
        triggers, trigger_ids = self._list(workspace, "triggers", "trigger", token)
        variables, variable_ids = self._list(workspace, "variables", "variable", token)
        ids.extend(tag_ids + trigger_ids + variable_ids)
        return {"workspace": current, "status": status, "tags": tags, "triggers": triggers, "variables": variables}, ids

    def _container_state(self, container: str, token: str) -> tuple[dict[str, Any], list[str]]:
        current, ids = self._get(container, token)
        workspaces, workspace_ids = self._list(container, "workspaces", "workspace", token)
        live, live_ids = self._live(container, token)
        ids.extend(workspace_ids + live_ids)
        return {"container": current, "workspaces": workspaces, "liveVersion": live}, ids

    def _publish_state(self, version: str, token: str) -> tuple[dict[str, Any], list[str]]:
        version = _resource(version, VERSION)
        current, ids = self._get(version, token)
        live, live_ids = self._live(_container_of(version), token)
        ids.extend(live_ids)
        return {"version": current, "liveVersion": live}, ids

    def _state(self, stage: str, resource: str, token: str) -> tuple[dict[str, Any], list[str]]:
        if stage == "WORKSPACE_CREATE":
            return self._container_state(resource, token)
        if stage == "PUBLISH":
            return self._publish_state(resource, token)
        return self._workspace_state(resource, token)

    @staticmethod
    def _measurement(path: Path) -> dict[str, Any]:
        value = _load(path.resolve(), "measurement plan")
        validate_artifact_data("measurement-plan", value, path_label=str(path))
        if not approved_plan_is_valid(value):
            raise AdvisorError("MEASUREMENT_PLAN_NOT_APPROVED", "Stage 9 requires an untampered approved measurement-plan v2.", EXIT_INPUT)
        return value

    @staticmethod
    def _project_root(value: Any) -> Path:
        path = Path(str(value or "")).expanduser()
        if not path.is_absolute() or not path.exists() or not path.is_dir():
            raise AdvisorError("INVALID_PROJECT_ROOT", "GTM artifacts require an existing absolute project root.", EXIT_INPUT)
        return path.resolve()

    def context(self, profile_id: str, container: str, project_root: Path, measurement_path: Path, site_context_path: Path | None = None) -> dict[str, Any]:
        root = self._project_root(project_root)
        container = _resource(container)
        measurement = self._measurement(measurement_path)
        selected, token, _ = self.auth.access_token(profile_id)
        if selected != profile_id:
            raise AdvisorError("PROFILE_MISMATCH", "The authorization profile changed.", EXIT_CONFIGURATION)
        state, request_ids = self._container_state(container, token)
        usage = state.get("container", {}).get("usageContext", [])
        blockers: list[str] = []
        limitations = ["Compiler preview is not runtime browser verification."]
        if not isinstance(usage, list) or "web" not in {str(item).lower() for item in usage}:
            blockers.append("Only a GTM web container is supported in Stage 9.")
        site_ref: dict[str, Any] | None = None
        if site_context_path:
            site = load_site_context(site_context_path.resolve())
            if Path(site["projectRoot"]).resolve() != root:
                blockers.append("The website context belongs to another project root.")
            if site.get("measurementPlan", {}).get("contentSha256") != measurement.get("contentSha256"):
                blockers.append("The website context belongs to another measurement plan.")
            public_id = state.get("container", {}).get("publicId")
            if public_id and public_id not in site.get("analytics", {}).get("gtmIds", []):
                blockers.append("The selected GTM container is not confirmed in the website context.")
            if site.get("blockers"):
                blockers.append("The website context still contains unresolved blockers.")
            if _project_evidence(root)[2] != site.get("projectContentSha256"):
                blockers.append("The website source changed after the supplied website context was created.")
            site_ref = {"path": str(site_context_path.resolve()), "contextId": site["contextId"], "contextSha256": site["contextSha256"]}
        else:
            limitations.append("Website/dataLayer context was not supplied; entity, preview, version, and publish plans are blocked.")
        generated = self.now().astimezone(timezone.utc)
        context_id = f"gtm-context-{_stamp(generated)}-{uuid.uuid4().hex[:12]}"
        context = {
            "schemaVersion": 1, "artifactType": "gtm-context", "generatedAt": _utc(generated),
            "expiresAt": _utc(generated + timedelta(minutes=30)), "contextId": context_id, "contextSha256": "",
            "projectRoot": str(root), "profileId": profile_id, "container": container,
            "measurementPlan": {"path": str(measurement_path.resolve()), "planId": measurement["planId"], "contentSha256": measurement["contentSha256"]},
            "siteContext": site_ref, "remoteState": state, "blockers": sorted(set(blockers)),
            "limitations": sorted(set(limitations)), "networkUsed": True, "mutationPerformed": False,
        }
        context["contextSha256"] = gtm_context_sha256(context)
        validate_artifact_data("gtm-context", context)
        location = ArtifactStore(root).write_named_artifact("gtm-contexts", context_id, context)
        return {"status": "blocked" if blockers else "ready", "context": context, "artifact": location, "requestIds": sorted(set(request_ids)), "mutationPerformed": False}

    @staticmethod
    def _load_context(path: Path) -> dict[str, Any]:
        value = _load(path.resolve(), "GTM context")
        validate_artifact_data("gtm-context", value, path_label=str(path))
        if value.get("contextSha256") != gtm_context_sha256(value):
            raise AdvisorError("GTM_CONTEXT_TAMPERED", "The GTM context SHA-256 does not match its content.", EXIT_INPUT)
        return value

    @staticmethod
    def _request_contract(path: Path) -> dict[str, Any]:
        value = _load(path.resolve(), "GTM change request")
        validate_artifact_data("gtm-change-request", value, path_label=str(path))
        issues = pii_issues(value)
        if issues:
            raise AdvisorError(
                "GTM_REQUEST_PII_BLOCKED",
                "The GTM change request contains personal data or PII-bearing fields.",
                EXIT_INPUT,
                details={"issues": issues},
            )
        return value

    @staticmethod
    def _evidence(path_value: Any, expected_stage: str) -> dict[str, Any]:
        path = Path(str(path_value or "")).expanduser()
        if not path.is_absolute():
            raise AdvisorError("INVALID_GTM_EVIDENCE", "An absolute GTM journal path is required.", EXIT_INPUT)
        journal = _load(path.resolve(), "GTM evidence journal")
        validate_artifact_data("journal-entry", journal, path_label=str(path))
        if journal.get("schemaVersion") != 4 or journal.get("stage") != expected_stage or journal.get("status") != "applied" or not journal.get("readback", {}).get("verified"):
            raise AdvisorError("INVALID_GTM_EVIDENCE", f"A verified {expected_stage} journal is required.", EXIT_INPUT)
        return {"path": str(path.resolve()), "journalId": journal["journalId"], "planSha256": journal["planSha256"], "stage": expected_stage, "gtmEvidence": journal.get("gtmEvidence", {})}

    def _operation(self, request: dict[str, Any], measurement: dict[str, Any], current_state: dict[str, Any] | None = None) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
        stage = request["stage"]
        container = _resource(request["container"])
        evidence = None
        if stage == "WORKSPACE_CREATE":
            resource = container
            body = {"name": request["workspaceName"], "description": request.get("workspaceDescription", "")}
        elif stage == "WORKSPACE_SYNC":
            resource, body = _resource(request["workspace"], WORKSPACE), {}
        elif stage == "ENTITY_BULK_UPDATE":
            resource = _resource(request["workspace"], WORKSPACE)
            if current_state is None:
                body = {}
            else:
                changes, summary = build_entities(request["entities"], measurement, current_state)
                body = {"changes": changes, "entitySummary": summary}
        elif stage == "QUICK_PREVIEW":
            resource, body = _resource(request["workspace"], WORKSPACE), {}
        elif stage == "VERSION_CREATE":
            resource = _resource(request["workspace"], WORKSPACE)
            evidence = self._evidence(request["evidenceJournal"], "QUICK_PREVIEW")
            body = {"name": request["versionName"], "notes": request.get("versionNotes", "")}
        else:
            resource = _resource(request["version"], VERSION)
            evidence = self._evidence(request["evidenceJournal"], "VERSION_CREATE")
            if request["versionFingerprint"] != evidence.get("gtmEvidence", {}).get("versionFingerprint") or resource != evidence.get("gtmEvidence", {}).get("versionPath"):
                raise AdvisorError("GTM_VERSION_EVIDENCE_MISMATCH", "Publish must target the exact version created by the evidence journal.", EXIT_INPUT)
            runtime = request.get("runtimeEvidence")
            if not isinstance(runtime, dict) or runtime.get("confirmed") is not True or runtime.get("container") != container or not runtime.get("checks"):
                raise AdvisorError("GTM_RUNTIME_PREVIEW_REQUIRED", "Publish requires explicit runtime preview evidence for this container.", EXIT_INPUT)
            try:
                observed = datetime.fromisoformat(str(runtime.get("observedAt", "")).replace("Z", "+00:00"))
            except ValueError as exc:
                raise AdvisorError(
                    "INVALID_GTM_RUNTIME_PREVIEW",
                    "Runtime preview evidence must contain a valid ISO 8601 observedAt timestamp.",
                    EXIT_INPUT,
                ) from exc
            current = self.now().astimezone(timezone.utc)
            if observed > current + timedelta(minutes=5) or observed < current - timedelta(hours=24):
                raise AdvisorError(
                    "STALE_GTM_RUNTIME_PREVIEW",
                    "Runtime preview evidence must be from the last 24 hours and cannot be dated in the future.",
                    EXIT_INPUT,
                )
            evidence["runtimeEvidence"] = runtime
            body = {}
        if _container_of(resource) != container:
            raise AdvisorError("CONTAINER_MISMATCH", "The GTM resource belongs to another container.", EXIT_INPUT)
        return resource, body, evidence

    def plan(self, context_path: Path, request_path: Path) -> dict[str, Any]:
        context = self._load_context(context_path)
        if self.now().astimezone(timezone.utc) > datetime.fromisoformat(context["expiresAt"].replace("Z", "+00:00")):
            raise AdvisorError("GTM_CONTEXT_EXPIRED", "The GTM context expired; create a fresh context.", EXIT_INPUT)
        request = self._request_contract(request_path)
        root = self._project_root(request["projectRoot"])
        if str(root) != context["projectRoot"] or request["profileId"] != context["profileId"] or request["container"] != context["container"]:
            raise AdvisorError("GTM_CONTEXT_MISMATCH", "The GTM request does not match the selected context.", EXIT_INPUT)
        stage = request["stage"]
        if stage not in STAGES:
            raise AdvisorError("INVALID_GTM_STAGE", "The GTM lifecycle stage is not supported.", EXIT_INPUT)
        if stage not in {"WORKSPACE_CREATE", "WORKSPACE_SYNC"} and (context.get("blockers") or not context.get("siteContext")):
            raise AdvisorError("GTM_CONTEXT_BLOCKED", "Resolve the GTM/site context before configuring or publishing entities.", EXIT_INPUT, details={"blockers": context.get("blockers", [])})
        measurement_path = Path(context["measurementPlan"]["path"])
        measurement = self._measurement(measurement_path)
        if measurement["contentSha256"] != context["measurementPlan"]["contentSha256"]:
            raise AdvisorError("STALE_MEASUREMENT_PLAN", "The approved measurement plan changed.", EXIT_INPUT)
        resource, body, evidence = self._operation(request, measurement)
        selected, token, _ = self.auth.access_token(request["profileId"])
        if selected != request["profileId"]:
            raise AdvisorError("PROFILE_MISMATCH", "The authorization profile changed.", EXIT_CONFIGURATION)
        state, request_ids = self._state(stage, resource, token)
        if stage == "ENTITY_BULK_UPDATE":
            resource, body, evidence = self._operation(request, measurement, state)
        if stage == "VERSION_CREATE":
            preview = (evidence or {}).get("gtmEvidence", {})
            if preview.get("workspacePath") != resource or preview.get("workspaceFingerprint") != state.get("workspace", {}).get("fingerprint"):
                raise AdvisorError("STALE_GTM_PREVIEW", "The workspace changed after compiler preview; create a fresh preview plan.", EXIT_INPUT)
        if stage == "WORKSPACE_CREATE" and any(item.get("name") == body["name"] for item in state.get("workspaces", [])):
            raise AdvisorError("GTM_WORKSPACE_ALREADY_EXISTS", "A workspace with this name already exists; reuse its exact path or choose another name.", EXIT_INPUT)
        if stage != "WORKSPACE_CREATE" and state.get("status", {}).get("mergeConflict"):
            raise AdvisorError("GTM_MERGE_CONFLICT", "The workspace has merge conflicts. Stage 9 never resolves them automatically.", EXIT_INPUT)
        generated = self.now().astimezone(timezone.utc)
        state_sha = _sha(state)
        snapshot_id = f"gtm-snapshot-{_stamp(generated)}-{uuid.uuid4().hex[:12]}"
        snapshot = {
            "schemaVersion": 2, "artifactType": "snapshot", "generatedAt": _utc(generated), "snapshotId": snapshot_id,
            "provider": "tag-manager", "apiChannel": "v2", "profileId": request["profileId"], "resource": resource,
            "stateSha256": state_sha, "state": state, "requestIds": sorted(set(request_ids)), "complete": True,
        }
        validate_artifact_data("snapshot", snapshot)
        store = ArtifactStore(root)
        snapshot_location = store.write_mutation_snapshot(snapshot)
        operation_id = f"gtm-op-{uuid.uuid4().hex[:12]}"
        wire_body = {key: value for key, value in body.items() if key != "entitySummary"}
        operation = {
            "operationId": operation_id, "kind": stage, "apiVersion": "v2", "method": "POST", "resource": resource,
            "fieldMask": [], "body": wire_body, "rationale": request["rationale"],
            "expectedReadback": [stage.lower()], "create": stage in {"WORKSPACE_CREATE", "VERSION_CREATE"},
            "experimental": False, "before": {"stateSha256": state_sha},
        }
        plan_id = f"gtm-mutation-{_stamp(generated)}-{uuid.uuid4().hex[:12]}"
        plan = {
            "schemaVersion": 4, "artifactType": "mutation-plan", "generatedAt": _utc(generated),
            "expiresAt": _utc(generated + timedelta(minutes=30)), "planId": plan_id, "planSha256": "",
            "target": "tag-manager", "riskClass": RISK[stage], "stage": stage,
            "projectRoot": str(root), "profileId": request["profileId"],
            "measurementPlan": {"path": str(measurement_path.resolve()), "planId": measurement["planId"], "contentSha256": measurement["contentSha256"]},
            "gtmContext": {"path": str(context_path.resolve()), "contextId": context["contextId"], "contextSha256": context["contextSha256"], "container": context["container"]},
            "evidence": evidence, "preconditions": [{"operationId": operation_id, "resource": resource, "snapshotId": snapshot_id, "snapshotPath": snapshot_location["path"], "stateSha256": state_sha}],
            "operations": [operation], "expectedReadback": [f"{operation_id}:{stage.lower()}"],
            "executionPolicy": {"maxAttemptsPerWrite": 1, "stopOnUncertainResult": True, "automaticRollback": False, "automaticPublish": False},
        }
        plan["planSha256"] = gtm_plan_sha256(plan)
        validate_artifact_data("mutation-plan", plan)
        location = store.write_mutation_plan(plan)
        return {"status": "confirmation_required", "plan": plan, "artifact": location, "preview": render_plan(plan), "entitySummary": body.get("entitySummary", []), "mutationPerformed": False}

    @staticmethod
    def _validate_plan(plan: dict[str, Any]) -> None:
        if plan.get("schemaVersion") != 4 or plan.get("artifactType") != "mutation-plan" or plan.get("target") != "tag-manager" or plan.get("stage") not in STAGES:
            raise AdvisorError("INVALID_MUTATION_PLAN", "Unsupported Stage 9 mutation plan.", EXIT_INPUT)
        if not HEX64.fullmatch(str(plan.get("planSha256", ""))) or plan["planSha256"] != gtm_plan_sha256(plan):
            raise AdvisorError("MUTATION_PLAN_TAMPERED", "The mutation plan SHA-256 does not match its content.", EXIT_INPUT)
        validate_artifact_data("mutation-plan", plan)
        operations, preconditions = plan.get("operations"), plan.get("preconditions")
        expected_policy = {"maxAttemptsPerWrite": 1, "stopOnUncertainResult": True, "automaticRollback": False, "automaticPublish": False}
        if not isinstance(operations, list) or len(operations) != 1 or not isinstance(preconditions, list) or len(preconditions) != 1 or plan.get("executionPolicy") != expected_policy:
            raise AdvisorError("INVALID_MUTATION_PLAN", "A GTM plan must contain exactly one lifecycle operation.", EXIT_INPUT)
        operation, precondition = operations[0], preconditions[0]
        if operation.get("kind") != plan["stage"] or operation.get("apiVersion") != "v2" or operation.get("method") != "POST" or operation.get("operationId") != precondition.get("operationId") or operation.get("resource") != precondition.get("resource"):
            raise AdvisorError("INVALID_MUTATION_PLAN", "The GTM operation is not bound to its precondition.", EXIT_INPUT)

    def show(self, plan_path: Path) -> dict[str, Any]:
        plan = _load(plan_path.resolve(), "mutation plan")
        self._validate_plan(plan)
        return {"status": "confirmation_required", "plan": plan, "preview": render_plan(plan), "mutationPerformed": False}

    def _wire(self, plan: dict[str, Any], token: str) -> JsonResponse:
        stage = plan["stage"]
        operation = plan["operations"][0]
        resource, body = operation["resource"], operation["body"]
        if stage == "WORKSPACE_CREATE":
            path, payload, query = f"/{resource}/workspaces", body, None
        elif stage == "WORKSPACE_SYNC":
            path, payload, query = f"/{resource}:sync", None, None
        elif stage == "ENTITY_BULK_UPDATE":
            path, payload, query = f"/{resource}/bulk_update", body, None
        elif stage == "QUICK_PREVIEW":
            path, payload, query = f"/{resource}:quick_preview", None, None
        elif stage == "VERSION_CREATE":
            path, payload, query = f"/{resource}:create_version", body, None
        else:
            fingerprint = plan.get("evidence", {}).get("gtmEvidence", {}).get("versionFingerprint")
            if not isinstance(fingerprint, str) or not fingerprint:
                raise AdvisorError("INVALID_GTM_EVIDENCE", "The publish plan lacks an exact version fingerprint.", EXIT_INPUT)
            path, payload, query = f"/{resource}:publish", None, {"fingerprint": fingerprint}
        return self._request("POST", path, token, payload=payload, query=query, write=True)

    def _readback(self, plan: dict[str, Any], response: Any, token: str) -> tuple[bool, dict[str, Any], list[str]]:
        stage, resource, body = plan["stage"], plan["operations"][0]["resource"], plan["operations"][0]["body"]
        data = response if isinstance(response, dict) else {}
        ids: list[str] = []
        evidence: dict[str, Any] = {}
        if stage == "WORKSPACE_CREATE":
            path = str(data.get("path", ""))
            if not WORKSPACE.fullmatch(path):
                return False, {"workspacePath": None}, ids
            observed, ids = self._get(path, token)
            verified = observed.get("name") == body.get("name") and _container_of(path) == resource
            evidence = {"workspacePath": path, "workspaceFingerprint": observed.get("fingerprint")}
        elif stage == "WORKSPACE_SYNC":
            observed, ids = self._workspace_state(resource, token)
            verified = _sync_clean(data) and not observed.get("status", {}).get("mergeConflict")
            evidence = {"workspacePath": resource, "syncClean": verified, "workspaceFingerprint": observed.get("workspace", {}).get("fingerprint")}
        elif stage == "ENTITY_BULK_UPDATE":
            observed, ids = self._workspace_state(resource, token)
            desired_changes = body.get("changes", [])
            verified = True
            matched: list[dict[str, str]] = []
            for change in desired_changes:
                kind = next((value for value in ("variable", "trigger", "tag") if value in change), None)
                desired = dict(change.get(kind, {})) if kind else {}
                desired.pop({"variable": "variableId", "trigger": "triggerId", "tag": "tagId"}.get(str(kind), ""), None)
                desired.pop("fingerprint", None)
                collection = observed.get({"variable": "variables", "trigger": "triggers", "tag": "tags"}.get(str(kind), ""), [])
                match = next((item for item in collection if item.get("name") == desired.get("name") and _contains(item, desired)), None)
                verified = verified and match is not None
                if match:
                    matched.append({"entityKind": str(kind), "name": str(match.get("name")), "path": str(match.get("path", "")), "fingerprint": str(match.get("fingerprint", ""))})
            verified = verified and not observed.get("status", {}).get("mergeConflict")
            evidence = {"workspacePath": resource, "entitiesVerified": verified, "entities": matched, "workspaceFingerprint": observed.get("workspace", {}).get("fingerprint")}
        elif stage == "QUICK_PREVIEW":
            observed, ids = self._workspace_state(resource, token)
            version = data.get("containerVersion", {}) if isinstance(data.get("containerVersion"), dict) else {}
            verified = data.get("compilerError") is False and _sync_clean(data) and not observed.get("status", {}).get("mergeConflict")
            evidence = {"workspacePath": resource, "workspaceFingerprint": observed.get("workspace", {}).get("fingerprint"), "compilerPreviewVerified": verified, "runtimePreviewVerified": False, "previewVersionPath": version.get("path"), "previewVersionFingerprint": version.get("fingerprint")}
        elif stage == "VERSION_CREATE":
            version = data.get("containerVersion", {}) if isinstance(data.get("containerVersion"), dict) else {}
            version_path, new_workspace = str(version.get("path", "")), str(data.get("newWorkspacePath", ""))
            if not VERSION.fullmatch(version_path) or not WORKSPACE.fullmatch(new_workspace):
                return False, {"versionPath": version_path or None, "newWorkspacePath": new_workspace or None}, ids
            observed_version, version_ids = self._get(version_path, token)
            observed_workspace, workspace_ids = self._get(new_workspace, token)
            ids.extend(version_ids + workspace_ids)
            verified = data.get("compilerError") is False and _sync_clean(data) and observed_version.get("name") == body.get("name") and _container_of(new_workspace) == _container_of(resource)
            evidence = {"versionPath": version_path, "versionFingerprint": observed_version.get("fingerprint"), "newWorkspacePath": new_workspace, "newWorkspaceFingerprint": observed_workspace.get("fingerprint"), "compilerVerified": verified}
        else:
            live, ids = self._live(_container_of(resource), token)
            response_version = data.get("containerVersion", {}) if isinstance(data.get("containerVersion"), dict) else {}
            expected_fingerprint = plan.get("evidence", {}).get("gtmEvidence", {}).get("versionFingerprint")
            verified = data.get("compilerError") is False and live.get("path") == resource and live.get("fingerprint") == expected_fingerprint and response_version.get("path", resource) == resource
            before_snapshot = _load(Path(plan["preconditions"][0]["snapshotPath"]), "GTM precondition snapshot")
            evidence = {"versionPath": resource, "versionFingerprint": live.get("fingerprint"), "previousLiveVersion": before_snapshot.get("state", {}).get("liveVersion", {}).get("path"), "publishedVersion": live.get("path"), "published": verified}
        return verified, evidence, ids

    def apply(self, plan_path: Path, confirmation: str) -> dict[str, Any]:
        plan_path = plan_path.resolve()
        plan = _load(plan_path, "mutation plan")
        self._validate_plan(plan)
        if confirmation != plan["planSha256"]:
            raise AdvisorError("MUTATION_CONFIRMATION_MISMATCH", "The exact mutation-plan SHA-256 was not confirmed.", EXIT_INPUT)
        now = self.now().astimezone(timezone.utc)
        if now > datetime.fromisoformat(plan["expiresAt"].replace("Z", "+00:00")):
            raise AdvisorError("MUTATION_PLAN_EXPIRED", "The mutation plan expired; create a fresh plan.", EXIT_INPUT)
        root = self._project_root(plan["projectRoot"])
        store = ArtifactStore(root)
        if store.plan_was_consumed(plan["planSha256"]):
            raise AdvisorError("MUTATION_PLAN_REPLAYED", "This mutation plan has already been consumed.", EXIT_INPUT)
        context = self._load_context(Path(plan["gtmContext"]["path"]))
        if context["contextSha256"] != plan["gtmContext"]["contextSha256"]:
            raise AdvisorError("STALE_GTM_CONTEXT", "The GTM context changed.", EXIT_INPUT)
        if now > datetime.fromisoformat(context["expiresAt"].replace("Z", "+00:00")):
            raise AdvisorError("GTM_CONTEXT_EXPIRED", "The GTM context expired; create a fresh context and plan.", EXIT_INPUT)
        if isinstance(context.get("siteContext"), dict):
            site = load_site_context(Path(context["siteContext"]["path"]))
            if site.get("contextSha256") != context["siteContext"].get("contextSha256"):
                raise AdvisorError("STALE_SITE_CONTEXT", "The website/dataLayer context changed.", EXIT_INPUT)
            if _project_evidence(root)[2] != site.get("projectContentSha256"):
                raise AdvisorError("STALE_SITE_CONTEXT", "The website source changed after GTM planning.", EXIT_INPUT)
        measurement = self._measurement(Path(plan["measurementPlan"]["path"]))
        if measurement["contentSha256"] != plan["measurementPlan"]["contentSha256"]:
            raise AdvisorError("STALE_MEASUREMENT_PLAN", "The approved measurement plan changed.", EXIT_INPUT)
        if isinstance(plan.get("evidence"), dict):
            expected_stage = str(plan["evidence"].get("stage", ""))
            refreshed = self._evidence(plan["evidence"].get("path"), expected_stage)
            if refreshed["journalId"] != plan["evidence"].get("journalId") or refreshed["planSha256"] != plan["evidence"].get("planSha256") or refreshed["gtmEvidence"] != plan["evidence"].get("gtmEvidence"):
                raise AdvisorError("STALE_GTM_EVIDENCE", "The GTM lifecycle evidence changed.", EXIT_INPUT)
        selected, token, _ = self.auth.access_token(plan["profileId"])
        if selected != plan["profileId"]:
            raise AdvisorError("PROFILE_MISMATCH", "The authorization profile changed.", EXIT_CONFIGURATION)
        operation, precondition = plan["operations"][0], plan["preconditions"][0]
        current, current_ids = self._state(plan["stage"], operation["resource"], token)
        if _sha(current) != precondition["stateSha256"]:
            raise AdvisorError("STALE_PRECONDITION", "GTM changed after preview; create a fresh plan.", EXIT_INPUT)
        started = _utc(now)
        request_ids = list(current_ids)
        attempted = False
        write_returned = False
        evidence: dict[str, Any] = {}
        try:
            attempted = True
            response = self._wire(plan, token)
            write_returned = True
            if response.request_id:
                request_ids.append(response.request_id)
            verified, evidence, read_ids = self._readback(plan, response.data, token)
            request_ids.extend(read_ids)
            status = "applied" if verified else "ambiguous"
            error_code = None if verified else "INCOMPLETE_GTM_READBACK"
        except AdvisorError as exc:
            status_code = exc.details.get("status") if isinstance(exc.details, dict) else None
            ambiguous = write_returned or exc.code == "AMBIGUOUS_NETWORK_FAILURE" or status_code in {408, 429, 500, 502, 503, 504}
            status = "ambiguous" if ambiguous else "failed"
            error_code = exc.code
            evidence = {"readbackError": exc.code}
        finished = _utc(self.now())
        operation_result = {"operationId": operation["operationId"], "status": status, "resource": operation["resource"], "verified": status == "applied", "errorCode": error_code}
        journal = {
            "schemaVersion": 4, "artifactType": "journal-entry", "generatedAt": finished,
            "journalId": f"gtm-journal-{_stamp(self.now())}-{uuid.uuid4().hex[:12]}", "planId": plan["planId"],
            "planSha256": plan["planSha256"], "confirmationSha256": confirmation, "startedAt": started, "finishedAt": finished,
            "status": status, "stage": plan["stage"], "requestIds": sorted(set(request_ids)), "operations": [operation_result],
            "readback": {"verified": status == "applied", "observedStateSha256": _sha(evidence), "message": "The exact GTM result matched independent readback." if status == "applied" else "The GTM result requires read-only reconciliation; no retry was attempted."},
            "gtmEvidence": evidence, "projectRoot": str(root), "profileId": plan["profileId"], "planPath": str(plan_path),
        }
        validate_artifact_data("journal-entry", journal)
        location = store.write_journal(journal)
        return {"status": status, "journal": journal, "artifact": location, "mutationPerformed": attempted}

    def reconcile(self, journal_path: Path) -> dict[str, Any]:
        journal = _load(journal_path.resolve(), "GTM journal")
        if journal.get("schemaVersion") != 4 or journal.get("status") not in {"ambiguous", "partial"}:
            raise AdvisorError("RECONCILIATION_NOT_ALLOWED", "Only ambiguous or partial Stage 9 journals can be reconciled.", EXIT_INPUT)
        plan = _load(Path(journal["planPath"]), "mutation plan")
        self._validate_plan(plan)
        _, token, _ = self.auth.access_token(journal["profileId"])
        state, request_ids = self._state(plan["stage"], plan["operations"][0]["resource"], token)
        return {"status": "reconciled_read_only", "stage": plan["stage"], "stateSha256": _sha(state), "requestIds": sorted(set(request_ids)), "mutationPerformed": False, "note": "No write was retried. Review this observation before creating a new plan."}
