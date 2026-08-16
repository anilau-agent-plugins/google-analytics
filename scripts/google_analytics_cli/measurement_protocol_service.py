"""Secret-safe, one-shot Measurement Protocol debug and production delivery plans."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from .artifact_store import ArtifactStore, canonical_json
from .contracts import validate_artifact_data
from .errors import AdvisorError, EXIT_INPUT, EXIT_NETWORK
from .ga4_mutation_service import mutation_plan_sha256
from .http import JsonTransport
from .measurement_policy import pii_issues, plan_content_sha256
from .secret_store import SecretStore, secret_store


MEASUREMENT_ID = re.compile(r"^G-[A-Z0-9]{5,}$")
EVENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
PARAM_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
PHONE = re.compile(r"(?<![A-Za-z0-9])\+?[1-9][0-9 ()-]{8,}[0-9](?![A-Za-z0-9])")
DEBUG = "https://www.google-analytics.com/debug/mp/collect"
PRODUCTION = "https://www.google-analytics.com/mp/collect"


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdvisorError("INVALID_INPUT_FILE", f"The {label} could not be read as JSON.", EXIT_INPUT) from exc
    if not isinstance(value, dict):
        raise AdvisorError("INVALID_INPUT_FILE", f"The {label} must be a JSON object.", EXIT_INPUT)
    return value


def mp_plan_sha256(plan: dict[str, Any]) -> str:
    value = dict(plan)
    value.pop("planSha256", None)
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _approved(path: Path) -> dict[str, Any]:
    value = _load(path, "measurement plan")
    validate_artifact_data("measurement-plan", value, path_label=str(path))
    if (
        value.get("schemaVersion") != 2 or value.get("status") != "approved"
        or value.get("contentSha256") != plan_content_sha256(value)
        or value.get("approvalSha256") != value.get("contentSha256")
        or not value.get("identity", {}).get("measurementProtocolPlanned")
    ):
        raise AdvisorError("MEASUREMENT_PROTOCOL_NOT_PLANNED", "An approved Measurement Protocol design is required.", EXIT_INPUT)
    return value


def _validate_payload(payload: dict[str, Any], measurement: dict[str, Any], now: datetime) -> None:
    if len(canonical_json(payload)) > 130 * 1024:
        raise AdvisorError("MP_PAYLOAD_TOO_LARGE", "Measurement Protocol payload exceeds 130 kB.", EXIT_INPUT)
    if pii_issues(payload) or PHONE.search(json.dumps(payload, ensure_ascii=False)):
        raise AdvisorError("PII_BLOCKED", "PII-shaped Measurement Protocol content is forbidden.", EXIT_INPUT)
    client_id = payload.get("client_id")
    if not isinstance(client_id, str) or not client_id or len(client_id) > 256:
        raise AdvisorError("MP_IDENTITY_INCOMPLETE", "A bounded client_id matching the web session is required.", EXIT_INPUT)
    events = payload.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 25 or not all(isinstance(item, dict) for item in events):
        raise AdvisorError("MP_EVENTS_INVALID", "A Measurement Protocol request needs 1 to 25 events.", EXIT_INPUT)
    approved_events = {item.get("name"): item for item in measurement.get("events", []) if isinstance(item, dict)}
    transaction_ids: set[str] = set()
    for event in events:
        name = event.get("name")
        if not isinstance(name, str) or not EVENT_NAME.fullmatch(name) or name not in approved_events:
            raise AdvisorError("EVENT_NOT_PLANNED", "A Measurement Protocol event is not approved.", EXIT_INPUT, details={"eventName": name})
        owner = approved_events[name].get("collectionOwner")
        if owner not in {"backend-mp", "crm-mp", "payment-webhook-mp"}:
            raise AdvisorError("EVENT_OWNER_CONFLICT", "The approved collection owner is not server-side Measurement Protocol.", EXIT_INPUT, details={"eventName": name})
        params = event.get("params", {})
        if not isinstance(params, dict) or len(params) > 25 or any(not isinstance(key, str) or not PARAM_NAME.fullmatch(key) for key in params):
            raise AdvisorError("MP_PARAMETERS_INVALID", "Each event supports at most 25 valid parameters.", EXIT_INPUT)
        if "session_id" in params and ("engagement_time_msec" not in params or not isinstance(params["engagement_time_msec"], int) or params["engagement_time_msec"] < 0):
            raise AdvisorError("MP_SESSION_INCOMPLETE", "Session-linked events require engagement_time_msec.", EXIT_INPUT)
        if name in {"purchase", "refund"}:
            transaction_id = params.get("transaction_id")
            if not isinstance(transaction_id, str) or not transaction_id.strip() or len(transaction_id) > 100 or transaction_id in transaction_ids:
                raise AdvisorError("MP_TRANSACTION_ID_INVALID", "Ecommerce needs a unique, non-empty transaction_id.", EXIT_INPUT)
            transaction_ids.add(transaction_id)
            if name == "purchase" and (
                not isinstance(params.get("value"), (int, float)) or isinstance(params.get("value"), bool)
                or not isinstance(params.get("currency"), str) or not re.fullmatch(r"[A-Z]{3}", params["currency"])
                or not isinstance(params.get("items"), list) or not params["items"]
            ):
                raise AdvisorError("MP_ECOMMERCE_INVALID", "Purchase requires numeric value, ISO-style currency, and items.", EXIT_INPUT)
    timestamp = payload.get("timestamp_micros")
    if timestamp is not None:
        if not isinstance(timestamp, int):
            raise AdvisorError("MP_TIMESTAMP_INVALID", "timestamp_micros must be an integer.", EXIT_INPUT)
        observed = datetime.fromtimestamp(timestamp / 1_000_000, tz=timezone.utc)
        if observed < now - timedelta(hours=72) or observed > now + timedelta(minutes=5):
            raise AdvisorError("MP_TIMESTAMP_INVALID", "The event timestamp is outside the supported validation window.", EXIT_INPUT)


class MeasurementProtocolService:
    def __init__(self, *, transport: JsonTransport | None = None, secrets: SecretStore | None = None, now: Callable[[], datetime] | None = None) -> None:
        self.transport = transport or JsonTransport()
        self.secrets = secrets or secret_store()
        self.now = now or (lambda: datetime.now(timezone.utc))

    def delivery_plan(self, measurement_path: Path, payload_path: Path, credential_ref: str, measurement_id: str, endpoint_class: str) -> dict[str, Any]:
        measurement_path = measurement_path.resolve()
        measurement = _approved(measurement_path)
        payload = _load(payload_path.resolve(), "Measurement Protocol payload")
        now = self.now().astimezone(timezone.utc)
        _validate_payload(payload, measurement, now)
        if not MEASUREMENT_ID.fullmatch(measurement_id):
            raise AdvisorError("INVALID_MEASUREMENT_ID", "A public GA4 measurement ID is required.", EXIT_INPUT)
        if endpoint_class not in {"debug", "production"}:
            raise AdvisorError("INVALID_MP_ENDPOINT", "Choose exactly debug or production.", EXIT_INPUT)
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{3,200}", credential_ref):
            raise AdvisorError("SECRET_KEY_INVALID", "The protected credential reference is invalid.", EXIT_INPUT)
        root = Path(str(measurement["site"])).resolve()
        if not root.exists() or not root.is_dir():
            raise AdvisorError("INVALID_PROJECT_ROOT", "The measurement plan site must be an existing project root.", EXIT_INPUT)
        generated = now.isoformat().replace("+00:00", "Z")
        plan: dict[str, Any] = {
            "schemaVersion": 1, "artifactType": "mp-delivery-plan", "generatedAt": generated,
            "expiresAt": (now + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
            "planId": f"mp-delivery-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}", "planSha256": "",
            "projectRoot": str(root),
            "measurementPlan": {"path": str(measurement_path), "planId": measurement["planId"], "contentSha256": measurement["contentSha256"]},
            "measurementId": measurement_id, "credentialRef": credential_ref, "endpointClass": endpoint_class,
            "payload": payload, "payloadSha256": hashlib.sha256(canonical_json(payload)).hexdigest(),
            "oneShot": True, "productionEventsSent": False,
        }
        plan["planSha256"] = mp_plan_sha256(plan)
        validate_artifact_data("mp-delivery-plan", plan)
        location = ArtifactStore(root).write_named_artifact("mp-delivery-plans", plan["planId"], plan)
        return {"status": "confirmation_required", "plan": plan, "artifact": location, "productionEventsSent": False}

    @staticmethod
    def _validate_plan(plan: dict[str, Any]) -> None:
        if plan.get("schemaVersion") != 1 or plan.get("artifactType") != "mp-delivery-plan" or plan.get("planSha256") != mp_plan_sha256(plan):
            raise AdvisorError("MP_PLAN_TAMPERED", "The Measurement Protocol plan is invalid or was changed.", EXIT_INPUT)
        validate_artifact_data("mp-delivery-plan", plan)

    def _execute(self, plan_path: Path, confirmation: str, expected_endpoint: str) -> dict[str, Any]:
        plan_path = plan_path.resolve()
        plan = _load(plan_path, "Measurement Protocol plan")
        self._validate_plan(plan)
        if plan["endpointClass"] != expected_endpoint:
            raise AdvisorError("MP_ENDPOINT_MISMATCH", f"This command requires a {expected_endpoint} delivery plan.", EXIT_INPUT)
        if confirmation != plan["planSha256"]:
            raise AdvisorError("CONFIRMATION_MISMATCH", "The exact Measurement Protocol plan SHA-256 was not confirmed.", EXIT_INPUT)
        now = self.now().astimezone(timezone.utc)
        if now >= datetime.fromisoformat(plan["expiresAt"].replace("Z", "+00:00")):
            raise AdvisorError("MUTATION_PLAN_EXPIRED", "The Measurement Protocol plan expired.", EXIT_INPUT)
        measurement = _approved(Path(plan["measurementPlan"]["path"]))
        if measurement["planId"] != plan["measurementPlan"]["planId"] or measurement["contentSha256"] != plan["measurementPlan"]["contentSha256"]:
            raise AdvisorError("MEASUREMENT_PLAN_TAMPERED", "The measurement plan binding changed.", EXIT_INPUT)
        _validate_payload(plan["payload"], measurement, now)
        root = Path(plan["projectRoot"])
        store = ArtifactStore(root)
        if store.plan_was_consumed(plan["planSha256"]):
            raise AdvisorError("MUTATION_PLAN_REPLAYED", "This one-shot delivery plan was already consumed.", EXIT_INPUT)
        secret = self.secrets.get(plan["credentialRef"]).decode("utf-8")
        if not secret or len(secret) > 512:
            raise AdvisorError("SECRET_VALUE_INVALID", "The protected Measurement Protocol secret is invalid.", EXIT_INPUT)
        endpoint = DEBUG if expected_endpoint == "debug" else PRODUCTION
        query = {"measurement_id": plan["measurementId"], "api_secret": secret}
        if expected_endpoint == "debug":
            query["validation_behavior"] = "ENFORCE_RECOMMENDATIONS"
        try:
            response = self.transport.request("POST", endpoint + "?" + urlencode(query), payload=plan["payload"], max_attempts=1)
        except AdvisorError as exc:
            if expected_endpoint == "production" and exc.code in {"AMBIGUOUS_NETWORK_FAILURE", "HTTP_ERROR"}:
                journal = self._journal(plan, plan_path, confirmation, "ambiguous", False, [], exc.code, now)
                location = store.write_journal(journal)
                return {"status": "ambiguous", "journal": journal, "artifact": location, "productionEventsSent": "unknown"}
            raise
        messages = []
        if expected_endpoint == "debug":
            data = response.data if isinstance(response.data, dict) else {}
            messages = data.get("validationMessages", [])
            if not isinstance(messages, list):
                raise AdvisorError("MALFORMED_HTTP_RESPONSE", "The debug endpoint returned invalid validation messages.", EXIT_NETWORK)
            status = "blocked" if messages else "applied"
            verified = not messages
        else:
            status = "applied"
            verified = False
        journal = self._journal(plan, plan_path, confirmation, status, verified, messages, None, now, response.request_id)
        location = store.write_journal(journal)
        return {
            "status": status, "journal": journal, "artifact": location,
            "debugValidated": expected_endpoint == "debug" and verified,
            "productionEventsSent": expected_endpoint == "production", "processingVerified": False,
        }

    def _journal(self, plan: dict[str, Any], plan_path: Path, confirmation: str, status: str, verified: bool, messages: list[Any], error: str | None, started: datetime, request_id: str | None = None) -> dict[str, Any]:
        finished = self.now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        endpoint = plan["endpointClass"]
        return {
            "schemaVersion": 3, "artifactType": "journal-entry", "generatedAt": finished,
            "journalId": f"mp-journal-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
            "planId": plan["planId"], "planSha256": plan["planSha256"], "confirmationSha256": confirmation,
            "startedAt": started.isoformat().replace("+00:00", "Z"), "finishedAt": finished, "status": status,
            "requestIds": [request_id] if request_id else [],
            "readback": {"verified": verified, "observedStateSha256": hashlib.sha256(canonical_json(messages)).hexdigest(), "message": "Debug validation passed." if verified else ("Production request returned, but processing cannot be proven by the response." if endpoint == "production" and not error else "Validation or delivery was not proven.")},
            "operations": [{"endpointClass": endpoint, "validationMessages": messages, "errorCode": error}],
            "projectRoot": plan["projectRoot"], "planPath": str(plan_path),
            "deploymentPerformed": False, "productionEventsSent": endpoint == "production" and status == "applied",
        }

    def validate(self, plan_path: Path, confirmation: str) -> dict[str, Any]:
        return self._execute(plan_path, confirmation, "debug")

    def send(self, plan_path: Path, confirmation: str) -> dict[str, Any]:
        return self._execute(plan_path, confirmation, "production")

    def reconcile(self, journal_path: Path) -> dict[str, Any]:
        journal = _load(journal_path.resolve(), "Measurement Protocol journal")
        validate_artifact_data("journal-entry", journal, path_label=str(journal_path))
        return {"status": journal["status"], "journal": journal, "reconciliationOnly": True, "mutationPerformed": False, "productionEventsSent": journal.get("productionEventsSent", False)}
