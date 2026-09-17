"""Local safety artifacts for the Google UI GA4/Search Console linking workflow."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .artifact_store import ArtifactStore, canonical_json
from .contracts import validate_artifact_data
from .cross_source_policy import safe_origin
from .errors import AdvisorError, EXIT_INPUT
from .search_console_link_renderer import render_link_plan, render_link_result


PLAN_TTL = timedelta(minutes=30)
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
READBACK_KEYS = {
    "observedAt", "uiState", "ga4LinkTableVerified", "searchConsoleAssociationVerified",
    "pairMatched", "message", "integratedDataState", "browserInteractionRecorded",
}
OUTCOMES = {"created", "already_linked_exact", "cancelled", "blocked", "ambiguous", "failed"}
UI_STATES = {"linked_created", "exact_existing", "cancelled", "blocked", "failed", "ambiguous"}
DATA_STATES = {"not_checked", "pending_expected", "available", "unavailable_after_delay"}
EXCLUSIONS = [
    "DELETE_EXISTING_LINK", "RECREATE_LINK", "VERIFY_SEARCH_CONSOLE_OWNERSHIP",
    "PUBLISH_SEARCH_CONSOLE_COLLECTION", "MANAGE_USERS",
]


def _hash_without(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(canonical_json({key: child for key, child in value.items() if key != field})).hexdigest()


def link_request_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "contentSha256")


def link_plan_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "planSha256")


def link_result_sha256(value: dict[str, Any]) -> str:
    return _hash_without(value, "resultSha256")


def web_stream_fingerprint(value: dict[str, Any]) -> str:
    selected = {key: value.get(key) for key in ("name", "displayName", "type", "defaultUri", "measurementId")}
    return hashlib.sha256(canonical_json(selected)).hexdigest()


def search_console_property_fingerprint(value: dict[str, Any]) -> str:
    selected = {key: value.get(key) for key in ("selectionKey", "propertyType", "permissionLevel", "providerPermissionLevel")}
    return hashlib.sha256(canonical_json(selected)).hexdigest()


def _parse_time(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise AdvisorError("SEARCH_CONSOLE_LINK_ARTIFACT_INVALID", f"{label} is not a valid timestamp.", EXIT_INPUT) from exc


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdvisorError("SEARCH_CONSOLE_LINK_ARTIFACT_INVALID", f"Could not read the {label} artifact.", EXIT_INPUT) from exc
    if not isinstance(value, dict):
        raise AdvisorError("SEARCH_CONSOLE_LINK_ARTIFACT_INVALID", f"The {label} artifact must be a JSON object.", EXIT_INPUT)
    return value


def _inside(root: Path, path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if resolved != root and root not in resolved.parents:
        raise AdvisorError("SEARCH_CONSOLE_LINK_ARTIFACT_INVALID", f"{label} must stay inside the selected project.", EXIT_INPUT)
    return resolved


def _property_type_matches(selection_key: str, property_type: str) -> bool:
    if property_type == "domain":
        return selection_key.lower().startswith("sc-domain:") and "/" not in selection_key.split(":", 1)[-1]
    if property_type != "url_prefix":
        return False
    try:
        parsed = urlsplit(selection_key)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname) and parsed.username is None and parsed.password is None


def _scope_compatibility(default_uri: str, selection_key: str, property_type: str) -> str:
    normalized_origin = safe_origin(default_uri)
    if normalized_origin is None or not _property_type_matches(selection_key, property_type):
        return "blocked"
    stream = urlsplit(default_uri)
    if property_type == "domain":
        domain = selection_key.split(":", 1)[1].encode("idna").decode("ascii").lower().rstrip(".")
        host = (stream.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        return "exact" if host == domain else "compatible_candidate" if host.endswith("." + domain) else "blocked"
    site = urlsplit(selection_key)
    site_origin = safe_origin(f"{site.scheme}://{site.netloc}")
    if normalized_origin != site_origin:
        return "blocked"
    site_path = site.path or "/"
    stream_path = stream.path or "/"
    return "exact" if stream_path.startswith(site_path) else "blocked"


def _privacy_safe(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r"(?:email|cookie|password|mfa|passkey|screenshot|rawhtml|browserstorage)", str(key), re.I):
                raise AdvisorError("SEARCH_CONSOLE_LINK_PRIVACY_BLOCKED", f"Privacy-sensitive field was blocked at {path}.{key}.", EXIT_INPUT)
            _privacy_safe(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _privacy_safe(child, f"{path}[{index}]")
    elif isinstance(value, str) and EMAIL.search(value):
        raise AdvisorError("SEARCH_CONSOLE_LINK_PRIVACY_BLOCKED", f"Email-shaped content was blocked at {path}.", EXIT_INPUT)


class SearchConsoleLinkService:
    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _load_request(self, path: Path) -> tuple[Path, dict[str, Any]]:
        resolved = path.expanduser().resolve()
        request = _load_json(resolved, "Search Console link request")
        validate_artifact_data("search-console-link-request", request, path_label=str(resolved))
        if request["contentSha256"] != link_request_sha256(request):
            raise AdvisorError("SEARCH_CONSOLE_LINK_REQUEST_TAMPERED", "The request SHA-256 does not match its content.", EXIT_INPUT)
        root = Path(request["projectRoot"]).expanduser().resolve()
        if not root.is_dir():
            raise AdvisorError("SEARCH_CONSOLE_LINK_ARTIFACT_INVALID", "The selected project root does not exist.", EXIT_INPUT)
        _inside(root, resolved, "The request")
        _privacy_safe(request)
        return root, request

    def _request_blockers(self, request: dict[str, Any], now: datetime) -> list[str]:
        blockers: list[str] = []
        prop = request["ga4Property"]
        stream = request["webStream"]
        search = request["searchConsoleProperty"]
        preflight = request["preflight"]
        if not stream["name"].startswith(prop["name"] + "/dataStreams/"):
            blockers.append("The web stream does not belong to the selected GA4 property.")
        if stream["fingerprintSha256"] != web_stream_fingerprint(stream):
            blockers.append("The web-stream fingerprint does not match the exact resource preview.")
        if search["fingerprintSha256"] != search_console_property_fingerprint(search):
            blockers.append("The Search Console property fingerprint does not match the exact resource preview.")
        actual_scope = _scope_compatibility(stream["defaultUri"], search["selectionKey"], search["propertyType"])
        if request["scopeCompatibility"] != actual_scope:
            blockers.append("The stored scope compatibility does not match the exact resource identities.")
        if actual_scope == "blocked":
            blockers.append("The GA4 web stream and Search Console property do not have a conservatively compatible scope.")
        observed = _parse_time(preflight["observedAt"], "preflight.observedAt")
        if observed > now + timedelta(minutes=5) or now - observed > PLAN_TTL:
            blockers.append("The Google UI preflight is stale or has an invalid future timestamp.")
        link_state = preflight["linkState"]
        if link_state == "already_linked_exact":
            return sorted(set(blockers))
        if search["permissionLevel"] != "owner":
            blockers.append("Creating the link requires verified-owner access to the exact Search Console property.")
        if preflight["ga4EditorStatus"] != "available":
            blockers.append("Creating the link requires the GA4 Editor role on the exact property.")
        expected_account = "matched" if request["mode"] == "browser_assisted" else "user_confirmed"
        if preflight["accountMatch"] not in {expected_account, "matched"}:
            blockers.append("The active Google account was not confirmed for the selected workflow mode.")
        if link_state != "ready_for_review":
            blockers.append(f"The UI preflight is not ready for one exact Submit: {link_state}.")
        if not preflight["linkButtonAvailable"] or not preflight["reviewReady"]:
            blockers.append("The exact Google review page and final Submit control were not both verified.")
        for key, acknowledged in request["acknowledgements"].items():
            if acknowledged is not True:
                blockers.append(f"Required acknowledgement is missing: {key}.")
        return sorted(set(blockers))

    def plan(self, request_path: Path) -> dict[str, Any]:
        root, request = self._load_request(request_path)
        generated = self.now().astimezone(timezone.utc)
        blockers = self._request_blockers(request, generated)
        link_state = request["preflight"]["linkState"]
        status = "no_op" if link_state == "already_linked_exact" and not blockers else "blocked" if blockers else "ready"
        resources = {
            "ga4Property": request["ga4Property"],
            "webStream": request["webStream"],
            "searchConsoleProperty": request["searchConsoleProperty"],
            "scopeCompatibility": request["scopeCompatibility"],
        }
        operations = []
        if status == "ready":
            operations.append({
                "operationId": "google-ui.search-console-link.submit",
                "action": "UI_SUBMIT_CREATE_LINK",
                "ga4Property": request["ga4Property"]["name"],
                "webStream": request["webStream"]["name"],
                "searchConsoleProperty": request["searchConsoleProperty"]["selectionKey"],
                "maximumSubmits": 1,
            })
        plan = {
            "schemaVersion": 1,
            "artifactType": "search-console-link-plan",
            "generatedAt": _utc(generated),
            "expiresAt": _utc(generated + PLAN_TTL),
            "planId": f"search-console-link-plan-{generated.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
            "planSha256": "",
            "projectRoot": str(root),
            "requestSha256": request["contentSha256"],
            "status": status,
            "mode": request["mode"],
            "resources": resources,
            "preconditions": {
                "observedAt": request["preflight"]["observedAt"],
                "linkState": link_state,
                "accountMatch": request["preflight"]["accountMatch"],
                "ga4EditorStatus": request["preflight"]["ga4EditorStatus"],
                "reviewReady": request["preflight"]["reviewReady"],
                "samePageSetConfirmed": request["acknowledgements"]["samePageSetConfirmed"],
                "resourceFingerprints": {
                    "webStream": request["webStream"]["fingerprintSha256"],
                    "searchConsoleProperty": request["searchConsoleProperty"]["fingerprintSha256"],
                },
            },
            "operations": operations,
            "excludedOperations": EXCLUSIONS,
            "expectedReadback": [
                "Exact pair is visible in GA4 Admin > Product links > Search Console Links.",
                "Search Console association is checked separately when accessible.",
                "Report-data availability is not used as immediate link proof.",
            ],
            "blockers": blockers,
            "singleUse": True,
            "confirmationRequired": status == "ready",
            "confirmationScope": "One final Google UI Submit for this exact GA4 property, web stream, and Search Console property only.",
            "networkUsed": False,
            "mutationPerformed": False,
            "browserMutationPending": status == "ready",
        }
        plan["planSha256"] = link_plan_sha256(plan)
        validate_artifact_data("search-console-link-plan", plan)
        location = ArtifactStore(root).write_named_artifact("search-console-link-plans", plan["planId"], plan)
        return {"status": status, "plan": plan, "artifact": location, "networkUsed": False, "mutationPerformed": False}

    def _load_plan(self, path: Path) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        plan = _load_json(resolved, "Search Console link plan")
        validate_artifact_data("search-console-link-plan", plan, path_label=str(resolved))
        if plan["planSha256"] != link_plan_sha256(plan):
            raise AdvisorError("SEARCH_CONSOLE_LINK_PLAN_TAMPERED", "The plan SHA-256 does not match its content.", EXIT_INPUT)
        root = Path(plan["projectRoot"]).expanduser().resolve()
        _inside(root, resolved, "The plan")
        _privacy_safe(plan)
        return plan

    def show_plan(self, path: Path, language: str = "auto") -> dict[str, Any]:
        plan = self._load_plan(path)
        expired = self.now().astimezone(timezone.utc) > _parse_time(plan["expiresAt"], "expiresAt")
        selected = "en" if language == "auto" else language
        status = "blocked" if plan["status"] == "ready" and expired else plan["status"]
        display_plan = plan
        if expired and plan["status"] == "ready":
            display_plan = {**plan, "status": "blocked", "blockers": [*plan["blockers"], "The 30-minute plan has expired; create a fresh plan from the current Google review page."]}
        return {
            "status": status,
            "expired": expired,
            "plan": plan,
            "plain": render_link_plan(display_plan, selected),
            "networkUsed": False,
            "mutationPerformed": False,
        }

    def _load_readback(self, path: Path, root: Path) -> tuple[dict[str, Any], bool, str]:
        resolved = _inside(root, path, "The readback")
        value = _load_json(resolved, "Search Console link readback")
        unknown = set(value) - READBACK_KEYS
        missing = READBACK_KEYS - set(value)
        if unknown or missing:
            raise AdvisorError("SEARCH_CONSOLE_LINK_READBACK_INVALID", "The readback fields are incomplete or unsupported.", EXIT_INPUT, details={"missing": sorted(missing), "unknown": sorted(unknown)})
        if value["uiState"] not in UI_STATES or value["integratedDataState"] not in DATA_STATES:
            raise AdvisorError("SEARCH_CONSOLE_LINK_READBACK_INVALID", "The readback state is unsupported.", EXIT_INPUT)
        for key in ("ga4LinkTableVerified", "searchConsoleAssociationVerified", "pairMatched", "browserInteractionRecorded"):
            if not isinstance(value[key], bool):
                raise AdvisorError("SEARCH_CONSOLE_LINK_READBACK_INVALID", f"{key} must be boolean.", EXIT_INPUT)
        if not isinstance(value["message"], str) or not value["message"].strip() or len(value["message"]) > 2048:
            raise AdvisorError("SEARCH_CONSOLE_LINK_READBACK_INVALID", "The readback message is invalid.", EXIT_INPUT)
        _parse_time(value["observedAt"], "readback.observedAt")
        _privacy_safe(value)
        browser_recorded = value.pop("browserInteractionRecorded")
        integrated_data_state = value.pop("integratedDataState")
        return value, browser_recorded, integrated_data_state

    def _plan_was_recorded(self, root: Path, plan_sha256: str) -> bool:
        folder = root / ".google-analytics-advisor" / "search-console-link-results"
        if not folder.exists():
            return False
        for path in folder.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("planSha256") == plan_sha256:
                return True
        return False

    def record(self, plan_path: Path, confirmation_sha256: str, outcome: str, readback_path: Path) -> dict[str, Any]:
        plan = self._load_plan(plan_path)
        if outcome not in OUTCOMES:
            raise AdvisorError("SEARCH_CONSOLE_LINK_OUTCOME_INVALID", "The link outcome is unsupported.", EXIT_INPUT)
        if confirmation_sha256 != plan["planSha256"]:
            raise AdvisorError("SEARCH_CONSOLE_LINK_CONFIRMATION_MISMATCH", "The full confirmation SHA-256 does not match the immutable plan.", EXIT_INPUT)
        root = Path(plan["projectRoot"]).resolve()
        if self._plan_was_recorded(root, plan["planSha256"]):
            raise AdvisorError("SEARCH_CONSOLE_LINK_PLAN_CONSUMED", "This single-use UI plan already has a recorded outcome.", EXIT_INPUT)
        readback, browser_recorded, integrated_data_state = self._load_readback(readback_path, root)
        observed = _parse_time(readback["observedAt"], "readback.observedAt")
        generated = _parse_time(plan["generatedAt"], "generatedAt")
        expires = _parse_time(plan["expiresAt"], "expiresAt")
        if observed < generated or observed > expires or observed > self.now().astimezone(timezone.utc) + timedelta(minutes=5):
            raise AdvisorError("SEARCH_CONSOLE_LINK_PLAN_EXPIRED", "The recorded UI outcome is outside the plan validity window.", EXIT_INPUT)
        expected_by_status = {"ready": {"created", "cancelled", "ambiguous", "failed"}, "no_op": {"already_linked_exact"}, "blocked": {"blocked"}}
        if outcome not in expected_by_status[plan["status"]]:
            raise AdvisorError("SEARCH_CONSOLE_LINK_OUTCOME_INVALID", "The recorded outcome is incompatible with the plan status.", EXIT_INPUT)
        expected_ui = {
            "created": "linked_created", "already_linked_exact": "exact_existing", "cancelled": "cancelled",
            "blocked": "blocked", "ambiguous": "ambiguous", "failed": "failed",
        }[outcome]
        if readback["uiState"] != expected_ui:
            raise AdvisorError("SEARCH_CONSOLE_LINK_READBACK_INVALID", "The UI state does not match the recorded outcome.", EXIT_INPUT)
        if outcome in {"created", "already_linked_exact"} and not (readback["ga4LinkTableVerified"] and readback["pairMatched"]):
            raise AdvisorError("SEARCH_CONSOLE_LINK_READBACK_INVALID", "A successful or exact-existing outcome requires verified exact-pair GA4 UI readback.", EXIT_INPUT)
        if plan["mode"] == "browser_assisted" and outcome not in {"blocked"} and not browser_recorded:
            raise AdvisorError("SEARCH_CONSOLE_LINK_READBACK_INVALID", "Browser-assisted outcomes must explicitly record browser interaction.", EXIT_INPUT)
        if plan["mode"] == "self_service" and browser_recorded:
            raise AdvisorError("SEARCH_CONSOLE_LINK_READBACK_INVALID", "Self-service outcomes cannot claim browser interaction.", EXIT_INPUT)
        limitations: list[str] = []
        if not readback["searchConsoleAssociationVerified"] and outcome in {"created", "already_linked_exact"}:
            limitations.append("The Search Console Associations page was not independently verified.")
        if integrated_data_state == "pending_expected":
            limitations.append("Integrated report data are still within Google's expected processing delay.")
        elif integrated_data_state == "unavailable_after_delay":
            limitations.append("Integrated report data were unavailable after the expected delay and need a separate read-only diagnosis.")
        generated_result = self.now().astimezone(timezone.utc)
        result = {
            "schemaVersion": 1,
            "artifactType": "search-console-link-result",
            "generatedAt": _utc(generated_result),
            "resultId": f"search-console-link-result-{generated_result.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
            "resultSha256": "",
            "projectRoot": str(root),
            "planId": plan["planId"],
            "planSha256": plan["planSha256"],
            "confirmationSha256": confirmation_sha256,
            "outcome": outcome,
            "mode": plan["mode"],
            "resources": plan["resources"],
            "readback": readback,
            "integratedDataState": integrated_data_state,
            "limitations": limitations,
            "cliNetworkUsed": False,
            "browserInteractionRecorded": browser_recorded,
            "mutationPerformed": outcome == "created",
            "manualActionRequired": outcome in {"blocked", "ambiguous", "failed"} or integrated_data_state == "unavailable_after_delay",
        }
        result["resultSha256"] = link_result_sha256(result)
        validate_artifact_data("search-console-link-result", result)
        location = ArtifactStore(root).write_named_artifact("search-console-link-results", result["resultId"], result)
        return {"status": outcome, "result": result, "artifact": location, "networkUsed": False, "mutationPerformed": result["mutationPerformed"]}

    def show(self, path: Path, language: str) -> dict[str, Any]:
        resolved = path.expanduser().resolve()
        result = _load_json(resolved, "Search Console link result")
        validate_artifact_data("search-console-link-result", result, path_label=str(resolved))
        if result["resultSha256"] != link_result_sha256(result):
            raise AdvisorError("SEARCH_CONSOLE_LINK_RESULT_TAMPERED", "The result SHA-256 does not match its content.", EXIT_INPUT)
        root = Path(result["projectRoot"]).expanduser().resolve()
        _inside(root, resolved, "The result")
        _privacy_safe(result)
        selected = "en" if language == "auto" else language
        return {"status": result["outcome"], "result": result, "plain": render_link_result(result, selected), "networkUsed": False, "mutationPerformed": result["mutationPerformed"]}
