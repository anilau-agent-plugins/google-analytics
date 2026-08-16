"""Immutable plan, one-shot apply, readback, and reconciliation for Stage 7."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from .admin_mutation_registry import ADMIN, OPERATIONS, MutationOperation, get_operation, validate_body
from .artifact_store import ArtifactStore, canonical_json
from .auth import AuthService
from .contracts import validate_artifact_data
from .errors import AdvisorError, EXIT_CONFIGURATION, EXIT_INPUT, EXIT_NETWORK
from .ga4_mutation_renderer import render_plan
from .http import JsonResponse, JsonTransport
from .measurement_policy import pii_issues, plan_content_sha256
from .secret_store import SecretStore


RESOURCE_RE = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+)*$")
HEX64_RE = re.compile(r"^[a-f0-9]{64}$")
EVENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
PARAM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
RETENTION = {"TWO_MONTHS", "FOURTEEN_MONTHS", "TWENTY_SIX_MONTHS", "THIRTY_EIGHT_MONTHS", "FIFTY_MONTHS"}
STANDARD_RETENTION = {"TWO_MONTHS", "FOURTEEN_MONTHS"}
METRIC_UNITS = {"STANDARD", "CURRENCY", "FEET", "METERS", "KILOMETERS", "MILES", "MILLISECONDS", "SECONDS", "MINUTES", "HOURS"}


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


def mutation_plan_sha256(plan: dict[str, Any]) -> str:
    value = dict(plan)
    value.pop("planSha256", None)
    return _sha(value)


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resource(value: Any) -> str:
    text = str(value or "")
    if not RESOURCE_RE.fullmatch(text):
        raise AdvisorError("INVALID_RESOURCE_NAME", "An exact Google resource name is required.", EXIT_INPUT,
                           details={"resource": text[:256]})
    return text


def _property_of(resource: str) -> str | None:
    match = re.search(r"(?:^|/)properties/([A-Za-z0-9_-]+)", resource)
    return f"properties/{match.group(1)}" if match else None


def _provider_field(value: str) -> str:
    return ".".join(re.sub(r"(?<!^)(?=[A-Z])", "_", part).lower() for part in value.split("."))


def _redact_secret_resource(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_secret_resource(child) for key, child in value.items() if key != "secretValue"}
    if isinstance(value, list):
        return [_redact_secret_resource(item) for item in value]
    return value


def _contains(observed: Any, desired: Any) -> bool:
    if isinstance(desired, dict):
        return isinstance(observed, dict) and all(key in observed and _contains(observed[key], value) for key, value in desired.items())
    if isinstance(desired, list):
        return observed == desired
    return observed == desired


class Ga4MutationService:
    def __init__(
        self, *, auth: AuthService | None = None, transport: JsonTransport | None = None,
        secrets: SecretStore | None = None, now: Callable[[], datetime] | None = None,
    ) -> None:
        self.auth = auth or AuthService(json_transport=transport)
        self.transport = transport or JsonTransport()
        self._secrets = secrets
        self.now = now or (lambda: datetime.now(timezone.utc))

    @property
    def secrets(self) -> SecretStore:
        return self._secrets or self.auth.secrets

    @staticmethod
    def _validate_measurement_plan(plan: dict[str, Any]) -> None:
        if plan.get("schemaVersion") != 2 or plan.get("artifactType") != "measurement-plan" or plan.get("status") != "approved":
            raise AdvisorError("MEASUREMENT_PLAN_NOT_APPROVED", "Stage 7 requires an approved measurement-plan v2.", EXIT_INPUT)
        if plan.get("contentSha256") != plan_content_sha256(plan) or plan.get("approvalSha256") != plan.get("contentSha256"):
            raise AdvisorError("MEASUREMENT_PLAN_TAMPERED", "The approved measurement plan hash is invalid.", EXIT_INPUT)
        boundaries = plan.get("stageBoundaries", {})
        if not isinstance(boundaries, dict) or boundaries.get("mutationApprovalGranted") is not False:
            raise AdvisorError("MEASUREMENT_PLAN_TAMPERED", "The measurement plan has an invalid stage boundary.", EXIT_INPUT)

    @staticmethod
    def _validate_change_request(request: dict[str, Any], profile_id: str) -> tuple[Path, list[dict[str, Any]]]:
        if request.get("schemaVersion") != 1 or request.get("changeRequestType") != "ga4-change-request":
            raise AdvisorError("INVALID_CHANGE_REQUEST", "Unsupported GA4 change-request contract.", EXIT_INPUT)
        if request.get("profileId") != profile_id:
            raise AdvisorError("PROFILE_MISMATCH", "The change request belongs to another authorization profile.", EXIT_INPUT)
        project_root = Path(str(request.get("projectRoot", ""))).expanduser()
        if not project_root.is_absolute() or not project_root.exists() or not project_root.is_dir():
            raise AdvisorError("INVALID_PROJECT_ROOT", "The change request requires an existing absolute project root.", EXIT_INPUT)
        operations = request.get("operations")
        if not isinstance(operations, list) or not operations or len(operations) > 20 or not all(isinstance(item, dict) for item in operations):
            raise AdvisorError("INVALID_CHANGE_REQUEST", "A change request needs 1 to 20 typed operations.", EXIT_INPUT)
        return project_root.resolve(), operations

    @staticmethod
    def _validate_semantics(operation: MutationOperation, body: dict[str, Any], measurement: dict[str, Any], before: Any) -> None:
        kind = operation.kind
        if pii_issues(body):
            raise AdvisorError("PII_BLOCKED", "Personal or sensitive sample data is forbidden in GA4 configuration artifacts.", EXIT_INPUT)
        if kind.startswith("PROPERTY_"):
            if "currencyCode" in body and not re.fullmatch(r"[A-Z]{3}", str(body["currencyCode"])):
                raise AdvisorError("INVALID_CURRENCY", "Property currency must be an ISO 4217-style three-letter code.", EXIT_INPUT)
            if "timeZone" in body and not re.fullmatch(r"[A-Za-z_+-]+(?:/[A-Za-z0-9_+.-]+)+", str(body["timeZone"])):
                raise AdvisorError("INVALID_TIMEZONE", "Property timezone must be an IANA timezone name.", EXIT_INPUT)
        if kind == "WEB_STREAM_CREATE":
            if body.get("type") != "WEB_DATA_STREAM" or not isinstance(body.get("webStreamData"), dict):
                raise AdvisorError("MOBILE_STREAM_NOT_SUPPORTED", "Only GA4 web data streams are supported.", EXIT_INPUT)
            if not str(body["webStreamData"].get("defaultUri", "")).startswith(("http://", "https://")):
                raise AdvisorError("INVALID_STREAM_URL", "A web stream needs an HTTP or HTTPS default URI.", EXIT_INPUT)
        if kind.startswith("WEB_STREAM") and "webStreamData" in body:
            stream_data = body["webStreamData"]
            if not isinstance(stream_data, dict) or set(stream_data) != {"defaultUri"}:
                raise AdvisorError("INVALID_STREAM_DATA", "Only webStreamData.defaultUri is configurable.", EXIT_INPUT)
            if not str(stream_data["defaultUri"]).startswith(("http://", "https://")):
                raise AdvisorError("INVALID_STREAM_URL", "A web stream needs an HTTP or HTTPS default URI.", EXIT_INPUT)
        if kind.startswith("KEY_EVENT"):
            event_name = body.get("eventName")
            if kind.endswith("CREATE") and (not isinstance(event_name, str) or not EVENT_RE.fullmatch(event_name)):
                raise AdvisorError("INVALID_EVENT_NAME", "The key-event name is invalid.", EXIT_INPUT)
            planned = {item.get("name") for item in measurement.get("events", []) if item.get("keyEventRecommendation")}
            if event_name and event_name not in planned:
                raise AdvisorError("KEY_EVENT_NOT_PLANNED", "The key event is not approved in the measurement plan.", EXIT_INPUT)
            if body.get("countingMethod") not in {None, "ONCE_PER_EVENT", "ONCE_PER_SESSION"}:
                raise AdvisorError("INVALID_COUNTING_METHOD", "Unsupported key-event counting method.", EXIT_INPUT)
            if "defaultValue" in body:
                default = body["defaultValue"]
                if not isinstance(default, dict) or set(default) != {"numericValue", "currencyCode"}:
                    raise AdvisorError("INVALID_DEFAULT_VALUE", "A key-event default value requires only numericValue and currencyCode.", EXIT_INPUT)
                if not isinstance(default["numericValue"], (int, float)) or isinstance(default["numericValue"], bool) or not re.fullmatch(r"[A-Z]{3}", str(default["currencyCode"])):
                    raise AdvisorError("INVALID_DEFAULT_VALUE", "The default value needs a number and three-letter currency.", EXIT_INPUT)
            if kind == "KEY_EVENT_CREATE" and isinstance(before, dict) and len(before.get("keyEvents", [])) >= 30:
                raise AdvisorError("KEY_EVENT_LIMIT_REACHED", "The GA4 property has no free custom key-event slots.", EXIT_INPUT)
        if kind.startswith("CUSTOM_DIMENSION") or kind.startswith("CUSTOM_METRIC"):
            parameter = body.get("parameterName")
            if parameter and not PARAM_RE.fullmatch(str(parameter)):
                raise AdvisorError("INVALID_PARAMETER_NAME", "The custom-definition parameter name is invalid.", EXIT_INPUT)
            planned = {item.get("parameter") for item in measurement.get("customDefinitions", [])}
            if parameter and parameter not in planned:
                raise AdvisorError("CUSTOM_DEFINITION_NOT_PLANNED", "The custom definition is not approved in the measurement plan.", EXIT_INPUT)
        if kind.startswith("CUSTOM_DIMENSION") and body.get("disallowAdsPersonalization") and body.get("scope", before.get("scope") if isinstance(before, dict) else None) != "USER":
            raise AdvisorError("INVALID_NPA_SCOPE", "Ads-personalization exclusion is supported only for USER dimensions.", EXIT_INPUT)
        if kind.startswith("CUSTOM_METRIC"):
            unit = body.get("measurementUnit", before.get("measurementUnit") if isinstance(before, dict) else None)
            restricted = body.get("restrictedMetricType", before.get("restrictedMetricType", []) if isinstance(before, dict) else [])
            if unit is not None and unit not in METRIC_UNITS:
                raise AdvisorError("INVALID_MEASUREMENT_UNIT", "Unsupported custom-metric measurement unit.", EXIT_INPUT)
            if "scope" in body and body["scope"] != "EVENT":
                raise AdvisorError("INVALID_METRIC_SCOPE", "GA4 custom metrics support EVENT scope here.", EXIT_INPUT)
            if not isinstance(restricted, list) or any(value not in {"COST_DATA", "REVENUE_DATA"} for value in restricted):
                raise AdvisorError("INVALID_METRIC_RESTRICTION", "Unsupported restricted metric type.", EXIT_INPUT)
            if unit == "CURRENCY" and not restricted:
                raise AdvisorError("INVALID_METRIC_RESTRICTION", "Currency metrics require a restricted metric type.", EXIT_INPUT)
            if unit not in {None, "CURRENCY"} and restricted:
                raise AdvisorError("INVALID_METRIC_RESTRICTION", "Restricted metric types are allowed only for currency metrics.", EXIT_INPUT)
        if kind == "CUSTOM_DIMENSION_CREATE" and isinstance(before, dict):
            scope = body.get("scope")
            limit = {"USER": 25, "EVENT": 50, "ITEM": 10}.get(scope)
            used = sum(1 for item in before.get("customDimensions", []) if item.get("scope") == scope)
            if limit is None or used >= limit:
                raise AdvisorError("CUSTOM_DEFINITION_LIMIT_REACHED", "The selected custom-dimension scope has no free slots.", EXIT_INPUT)
        if kind == "CUSTOM_METRIC_CREATE" and isinstance(before, dict) and len(before.get("customMetrics", [])) >= 50:
            raise AdvisorError("CUSTOM_DEFINITION_LIMIT_REACHED", "The GA4 property has no free custom-metric slots.", EXIT_INPUT)
        if kind == "MP_SECRET_CREATE" and not measurement.get("identity", {}).get("measurementProtocolPlanned"):
            raise AdvisorError("MEASUREMENT_PROTOCOL_NOT_PLANNED", "The approved measurement plan does not require Measurement Protocol.", EXIT_INPUT)
        if kind == "RETENTION_UPDATE":
            for key in ("eventDataRetention", "userDataRetention"):
                if key in body and body[key] not in RETENTION:
                    raise AdvisorError("INVALID_RETENTION", "Unsupported data-retention duration.", EXIT_INPUT)
            service_level = before.get("propertyServiceLevel") if isinstance(before, dict) else None
            if service_level != "GOOGLE_ANALYTICS_360" and any(body.get(key) not in {None, *STANDARD_RETENTION} for key in ("eventDataRetention", "userDataRetention")):
                raise AdvisorError("RETENTION_REQUIRES_360", "Long retention values require Google Analytics 360.", EXIT_INPUT)
        if kind == "DATA_REDACTION_UPDATE" and body.get("queryParameterRedactionEnabled"):
            keys = body.get("queryParameterKeys")
            if not isinstance(keys, list) or not keys or any(not isinstance(key, str) or not key or "," in key for key in keys):
                raise AdvisorError("INVALID_REDACTION_KEYS", "Query redaction requires non-empty comma-free keys.", EXIT_INPUT)

    def _request(self, method: str, url: str, token: str, *, payload: Any = None, write: bool = False) -> JsonResponse:
        return self.transport.request(
            method, url, headers={"Authorization": f"Bearer {token}"}, payload=payload,
            max_attempts=1 if write else 3, retry_mode=None,
        )

    def _read_state(self, operation: MutationOperation, resource: str, token: str) -> tuple[Any, list[str]]:
        kind = operation.kind
        if operation.create:
            base, key, extra = {
                "PROPERTY_CREATE": ("/v1beta/properties", "properties", {"filter": f"parent:{resource}"}),
                "WEB_STREAM_CREATE": (f"/v1beta/{resource}/dataStreams", "dataStreams", {}),
                "KEY_EVENT_CREATE": (f"/v1beta/{resource}/keyEvents", "keyEvents", {}),
                "CUSTOM_DIMENSION_CREATE": (f"/v1beta/{resource}/customDimensions", "customDimensions", {}),
                "CUSTOM_METRIC_CREATE": (f"/v1beta/{resource}/customMetrics", "customMetrics", {}),
                "MP_SECRET_CREATE": (f"/v1beta/{resource}/measurementProtocolSecrets", "measurementProtocolSecrets", {}),
            }[kind]
            values: list[Any] = []
            request_ids: list[str] = []
            page_token: str | None = None
            seen: set[str] = set()
            for _ in range(50):
                params = {**extra, "pageSize": 200, "pageToken": page_token}
                response = self._request("GET", ADMIN + base + "?" + urlencode({k: v for k, v in params.items() if v}), token)
                data = _redact_secret_resource(response.data or {})
                if not isinstance(data, dict) or not isinstance(data.get(key, []), list):
                    raise AdvisorError("MALFORMED_HTTP_RESPONSE", "Google returned an invalid resource list.", EXIT_NETWORK)
                values.extend(data.get(key, []))
                if len(values) > 10_000:
                    raise AdvisorError("SNAPSHOT_INCOMPLETE", "The GA4 resource list exceeded the safety bound.", EXIT_NETWORK)
                if response.request_id:
                    request_ids.append(response.request_id)
                page_token = data.get("nextPageToken")
                if not page_token:
                    break
                if not isinstance(page_token, str) or page_token in seen:
                    raise AdvisorError("SNAPSHOT_INCOMPLETE", "GA4 pagination did not complete safely.", EXIT_NETWORK)
                seen.add(page_token)
            else:
                raise AdvisorError("SNAPSHOT_INCOMPLETE", "GA4 pagination exceeded the page bound.", EXIT_NETWORK)
            safe_key = "credentialResources" if kind == "MP_SECRET_CREATE" else key
            return {safe_key: values, "truncated": False}, request_ids
        if kind == "RETENTION_UPDATE":
            path = f"/v1beta/{resource}/dataRetentionSettings"
        elif kind == "ENHANCED_MEASUREMENT_UPDATE":
            path = f"/v1alpha/{resource}/enhancedMeasurementSettings"
        elif kind == "DATA_REDACTION_UPDATE":
            path = f"/v1alpha/{resource}/dataRedactionSettings"
        else:
            path = f"/{operation.api_version}/{resource}"
        response = self._request("GET", ADMIN + path, token)
        state = _redact_secret_resource(response.data or {})
        if operation.experimental and isinstance(state, dict):
            allowed_response = set(operation.allowed_fields) | {"name"}
            unknown = set(state) - allowed_response
            if unknown:
                raise AdvisorError(
                    "EXPERIMENTAL_CONTRACT_DRIFT", "The Admin v1alpha response shape changed; the operation was blocked.",
                    EXIT_NETWORK, details={"unknownFields": sorted(unknown)},
                )
        request_ids = [item for item in [response.request_id] if item]
        if kind == "RETENTION_UPDATE" and isinstance(state, dict):
            property_response = self._request("GET", f"{ADMIN}/v1beta/{resource}", token)
            property_state = property_response.data if isinstance(property_response.data, dict) else {}
            state["propertyServiceLevel"] = property_state.get("serviceLevel")
            if property_response.request_id:
                request_ids.append(property_response.request_id)
        return state, request_ids

    @staticmethod
    def _duplicate(operation: MutationOperation, state: Any, body: dict[str, Any]) -> dict[str, Any] | None:
        if not operation.create or not isinstance(state, dict):
            return None
        key_map = {
            "PROPERTY_CREATE": ("properties", ("parent", "displayName", "timeZone", "currencyCode")),
            "WEB_STREAM_CREATE": ("dataStreams", ("type", "displayName", "webStreamData")),
            "KEY_EVENT_CREATE": ("keyEvents", ("eventName",)),
            "CUSTOM_DIMENSION_CREATE": ("customDimensions", ("parameterName", "scope")),
            "CUSTOM_METRIC_CREATE": ("customMetrics", ("parameterName", "scope")),
            "MP_SECRET_CREATE": ("credentialResources", ("displayName",)),
        }
        collection, keys = key_map[operation.kind]
        for item in state.get(collection, []):
            if isinstance(item, dict) and all(item.get(key) == body.get(key) for key in keys if key in body):
                return item
        return None

    def plan(self, profile_id: str, measurement_path: Path, changes_path: Path) -> dict[str, Any]:
        measurement_path = measurement_path.resolve()
        changes_path = changes_path.resolve()
        measurement = _load(measurement_path, "measurement plan")
        self._validate_measurement_plan(measurement)
        request = _load(changes_path, "GA4 change request")
        validate_artifact_data("ga4-change-request", request)
        project_root, requested = self._validate_change_request(request, profile_id)
        selected, token, _ = self.auth.access_token(profile_id)
        if selected != profile_id:
            raise AdvisorError("PROFILE_MISMATCH", "The selected authorization profile changed.", EXIT_CONFIGURATION)
        create_count = sum(1 for item in requested if get_operation(str(item.get("kind", ""))).create)
        if create_count and len(requested) != 1:
            raise AdvisorError("CREATE_PLAN_MUST_BE_SINGLE", "Each non-idempotent create needs its own mutation plan.", EXIT_INPUT)
        resources = [str(item.get("resource", "")) for item in requested]
        if len(resources) != len(set(resources)):
            raise AdvisorError("DUPLICATE_PLAN_TARGET", "A coherent plan may change each remote resource at most once.", EXIT_INPUT)
        generated = self.now().astimezone(timezone.utc)
        store = ArtifactStore(project_root)
        operations: list[dict[str, Any]] = []
        preconditions: list[dict[str, Any]] = []
        no_ops: list[dict[str, Any]] = []
        for index, item in enumerate(requested, 1):
            operation = get_operation(str(item.get("kind", "")))
            body, field_mask = validate_body(operation, item.get("body"), item.get("fieldMask"))
            resource = _resource(item.get("resource"))
            if operation.kind == "PROPERTY_CREATE" and body.get("parent") != resource:
                raise AdvisorError("RESOURCE_PARENT_MISMATCH", "The property parent must match the selected account resource.", EXIT_INPUT)
            if operation.experimental and not (request.get("experimentalAdminAlpha") is True and request.get("alphaWarningAccepted") is True):
                raise AdvisorError("EXPERIMENTAL_GATE_REQUIRED", "Admin v1alpha changes require both experimental gates.", EXIT_INPUT)
            property_name = _property_of(resource) or (body.get("parent") if operation.kind == "PROPERTY_CREATE" else None)
            planned_property = measurement.get("property")
            if planned_property and property_name and planned_property != property_name:
                raise AdvisorError("PROPERTY_MISMATCH", "The change targets another GA4 property.", EXIT_INPUT)
            state, request_ids = self._read_state(operation, resource, token)
            duplicate = self._duplicate(operation, state, body)
            if duplicate:
                raise AdvisorError("GA4_RESOURCE_ALREADY_EXISTS", "A semantically matching GA4 resource already exists.", EXIT_INPUT,
                                   details={"resource": duplicate.get("name")})
            self._validate_semantics(operation, body, measurement, state)
            if not operation.create and _contains(state, body):
                no_ops.append({"kind": operation.kind, "resource": resource, "reason": "The requested fields already match GA4."})
                continue
            state_sha = _sha(state)
            snapshot_id = f"mutation-snapshot-{_stamp(generated)}-{uuid.uuid4().hex[:12]}"
            snapshot = {
                "schemaVersion": 2, "artifactType": "snapshot", "generatedAt": generated.isoformat().replace("+00:00", "Z"),
                "snapshotId": snapshot_id, "provider": "analytics-admin", "apiChannel": operation.api_version,
                "profileId": profile_id, "resource": resource, "stateSha256": state_sha, "state": state,
                "requestIds": request_ids, "complete": not (isinstance(state, dict) and state.get("truncated")),
            }
            if not snapshot["complete"]:
                raise AdvisorError("SNAPSHOT_INCOMPLETE", "The current GA4 resource list was truncated.", EXIT_NETWORK)
            validate_artifact_data("snapshot", snapshot)
            snapshot_location = store.write_mutation_snapshot(snapshot)
            operation_id = f"op-{index}-{uuid.uuid4().hex[:8]}"
            operations.append({
                "operationId": operation_id, "kind": operation.kind, "apiVersion": operation.api_version,
                "method": operation.method, "resource": resource, "fieldMask": field_mask, "body": body,
                "providerFieldMask": [_provider_field(value) for value in field_mask],
                "rationale": str(item.get("rationale") or "Apply the approved GA4 measurement configuration."),
                "expectedReadback": sorted(body), "create": operation.create, "experimental": operation.experimental,
                "before": state if not operation.create else {"matchingResource": None},
            })
            preconditions.append({
                "operationId": operation_id, "resource": resource, "snapshotId": snapshot_id,
                "snapshotPath": snapshot_location["path"], "stateSha256": state_sha,
            })
        if not operations:
            return {"status": "no_op", "plan": None, "artifact": None, "noOps": no_ops, "mutationPerformed": False}
        plan_id = f"ga4-mutation-{_stamp(generated)}-{uuid.uuid4().hex[:12]}"
        plan = {
            "schemaVersion": 2, "artifactType": "mutation-plan", "generatedAt": generated.isoformat().replace("+00:00", "Z"),
            "expiresAt": (generated + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            "planId": plan_id, "planSha256": "", "target": "analytics-admin", "riskClass": "REMOTE_CONFIG_CHANGE",
            "projectRoot": str(project_root), "profileId": profile_id,
            "measurementPlan": {"path": str(measurement_path), "planId": measurement["planId"], "contentSha256": measurement["contentSha256"]},
            "preconditions": preconditions, "operations": operations,
            "expectedReadback": [f"{item['operationId']}:{','.join(item['expectedReadback'])}" for item in operations],
            "executionPolicy": {"maxAttemptsPerWrite": 1, "stopOnUncertainResult": True, "automaticRollback": False},
        }
        plan["planSha256"] = mutation_plan_sha256(plan)
        validate_artifact_data("mutation-plan", plan)
        location = store.write_mutation_plan(plan)
        return {"status": "confirmation_required", "plan": plan, "artifact": location, "preview": render_plan(plan), "noOps": no_ops, "mutationPerformed": False}

    def show(self, plan_path: Path) -> dict[str, Any]:
        plan = _load(plan_path.resolve(), "mutation plan")
        self._validate_plan(plan)
        return {"status": "confirmation_required", "plan": plan, "preview": render_plan(plan), "mutationPerformed": False}

    @staticmethod
    def _validate_plan(plan: dict[str, Any]) -> None:
        if plan.get("schemaVersion") != 2 or plan.get("artifactType") != "mutation-plan" or plan.get("target") != "analytics-admin":
            raise AdvisorError("INVALID_MUTATION_PLAN", "Unsupported mutation plan.", EXIT_INPUT)
        expected = mutation_plan_sha256(plan)
        if not HEX64_RE.fullmatch(str(plan.get("planSha256", ""))) or plan.get("planSha256") != expected:
            raise AdvisorError("MUTATION_PLAN_TAMPERED", "The mutation plan SHA-256 does not match its content.", EXIT_INPUT)
        validate_artifact_data("mutation-plan", plan)
        operations = plan.get("operations")
        preconditions = plan.get("preconditions")
        measurement = plan.get("measurementPlan")
        policy = plan.get("executionPolicy")
        if (
            not isinstance(operations, list) or not 1 <= len(operations) <= 20
            or not isinstance(preconditions, list) or len(preconditions) != len(operations)
            or not isinstance(measurement, dict)
            or not all(isinstance(measurement.get(key), str) and measurement[key] for key in ("path", "planId", "contentSha256"))
            or policy != {"maxAttemptsPerWrite": 1, "stopOnUncertainResult": True, "automaticRollback": False}
            or not isinstance(plan.get("projectRoot"), str) or not plan["projectRoot"]
            or not isinstance(plan.get("profileId"), str) or not plan["profileId"]
        ):
            raise AdvisorError("INVALID_MUTATION_PLAN", "The Stage 7 mutation-plan contract is incomplete.", EXIT_INPUT)
        operation_ids = [item.get("operationId") for item in operations if isinstance(item, dict)]
        precondition_ids = [item.get("operationId") for item in preconditions if isinstance(item, dict)]
        if (
            len(operation_ids) != len(operations) or len(set(operation_ids)) != len(operations)
            or set(operation_ids) != set(precondition_ids)
            or len(set(item.get("resource") for item in operations)) != len(operations)
        ):
            raise AdvisorError("INVALID_MUTATION_PLAN", "The mutation plan has invalid operation bindings.", EXIT_INPUT)
        precondition_by_id = {item["operationId"]: item for item in preconditions}
        create_count = 0
        for item in operations:
            operation = get_operation(str(item.get("kind", "")))
            body, mask = validate_body(operation, item.get("body"), item.get("fieldMask"))
            resource = _resource(item.get("resource"))
            expected_provider_mask = [_provider_field(value) for value in mask]
            if (
                item.get("apiVersion") != operation.api_version or item.get("method") != operation.method
                or item.get("create") is not operation.create or item.get("experimental") is not operation.experimental
                or item.get("providerFieldMask") != expected_provider_mask
                or item.get("expectedReadback") != sorted(body)
                or precondition_by_id[item["operationId"]].get("resource") != resource
            ):
                raise AdvisorError("INVALID_MUTATION_PLAN", "The mutation plan does not match the closed operation registry.", EXIT_INPUT)
            create_count += int(operation.create)
        if create_count and len(operations) != 1:
            raise AdvisorError("INVALID_MUTATION_PLAN", "Create operations must remain isolated.", EXIT_INPUT)

    def _wire_request(self, item: dict[str, Any], token: str) -> JsonResponse:
        operation = get_operation(item["kind"])
        body, mask = validate_body(operation, item["body"], item["fieldMask"])
        resource = _resource(item["resource"])
        path = operation.path_template.format(resource=resource)
        if not operation.create:
            name = resource
            if operation.kind == "RETENTION_UPDATE":
                name = f"{resource}/dataRetentionSettings"
            elif operation.kind == "ENHANCED_MEASUREMENT_UPDATE":
                name = f"{resource}/enhancedMeasurementSettings"
            elif operation.kind == "DATA_REDACTION_UPDATE":
                name = f"{resource}/dataRedactionSettings"
            body = {"name": name, **body}
            path += "?" + urlencode({"updateMask": ",".join(_provider_field(value) for value in mask)})
        return self._request(operation.method, ADMIN + path, token, payload=body, write=True)

    def _store_mp_secret(self, profile_id: str, response: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        value = response.pop("secretValue", None)
        resource = str(response.get("name", ""))
        if value is None:
            return _redact_secret_resource(response), None
        if not isinstance(value, str) or not value or not resource:
            raise AdvisorError("SECRET_RESPONSE_INVALID", "Google returned an invalid protected-secret response.", EXIT_NETWORK)
        reference = "measurement-protocol:" + hashlib.sha256(f"{profile_id}:{resource}".encode("utf-8")).hexdigest()[:24]
        self.secrets.put(reference, value.encode("utf-8"))
        return _redact_secret_resource(response), reference

    def apply(self, plan_path: Path, confirmation: str) -> dict[str, Any]:
        plan_path = plan_path.resolve()
        plan = _load(plan_path, "mutation plan")
        self._validate_plan(plan)
        if confirmation != plan["planSha256"]:
            raise AdvisorError("MUTATION_CONFIRMATION_MISMATCH", "The exact mutation-plan SHA-256 was not confirmed.", EXIT_INPUT)
        now = self.now().astimezone(timezone.utc)
        expires = datetime.fromisoformat(str(plan["expiresAt"]).replace("Z", "+00:00"))
        if now > expires:
            raise AdvisorError("MUTATION_PLAN_EXPIRED", "The mutation plan expired; create a fresh plan.", EXIT_INPUT)
        project_root = Path(plan["projectRoot"]).resolve()
        store = ArtifactStore(project_root)
        if store.plan_was_consumed(plan["planSha256"]):
            raise AdvisorError("MUTATION_PLAN_REPLAYED", "This mutation plan has already been consumed.", EXIT_INPUT)
        measurement = _load(Path(plan["measurementPlan"]["path"]), "measurement plan")
        self._validate_measurement_plan(measurement)
        if measurement.get("contentSha256") != plan["measurementPlan"]["contentSha256"]:
            raise AdvisorError("STALE_MEASUREMENT_PLAN", "The approved measurement plan changed.", EXIT_INPUT)
        selected, token, _ = self.auth.access_token(plan["profileId"])
        if selected != plan["profileId"]:
            raise AdvisorError("PROFILE_MISMATCH", "The authorization profile changed.", EXIT_CONFIGURATION)
        for item, precondition in zip(plan["operations"], plan["preconditions"]):
            operation = get_operation(item["kind"])
            state, _ = self._read_state(operation, item["resource"], token)
            if _sha(state) != precondition["stateSha256"]:
                raise AdvisorError("STALE_PRECONDITION", "GA4 changed after the preview; create a fresh mutation plan.", EXIT_INPUT,
                                   details={"operationId": item["operationId"]})
        started = now.isoformat().replace("+00:00", "Z")
        results: list[dict[str, Any]] = []
        request_ids: list[str] = []
        overall = "applied"
        write_attempted = False
        for item in plan["operations"]:
            operation = get_operation(item["kind"])
            try:
                precondition = next(value for value in plan["preconditions"] if value["operationId"] == item["operationId"])
                immediate_state, immediate_ids = self._read_state(operation, item["resource"], token)
                request_ids.extend(immediate_ids)
                if _sha(immediate_state) != precondition["stateSha256"]:
                    results.append({"operationId": item["operationId"], "status": "blocked", "errorCode": "STALE_PRECONDITION", "verified": False})
                    overall = "blocked" if len(results) == 1 else "partial"
                    break
                write_attempted = True
                response = self._wire_request(item, token)
                if response.request_id:
                    request_ids.append(response.request_id)
                data = response.data if isinstance(response.data, dict) else {}
                credential_ref = None
                if operation.secret_response:
                    data, credential_ref = self._store_mp_secret(plan["profileId"], dict(data))
                read_resource = str(data.get("name") or item["resource"])
                if operation.create and not data.get("name"):
                    raise AdvisorError("INCOMPLETE_PROVIDER_RESPONSE", "Google did not return the created resource name.", EXIT_NETWORK)
                read_operation = get_operation(item["kind"])
                if operation.create:
                    shadow = dict(item)
                    shadow["resource"] = read_resource
                    shadow["kind"] = {
                        "PROPERTY_CREATE": "PROPERTY_PATCH", "WEB_STREAM_CREATE": "WEB_STREAM_PATCH",
                        "KEY_EVENT_CREATE": "KEY_EVENT_PATCH", "CUSTOM_DIMENSION_CREATE": "CUSTOM_DIMENSION_PATCH",
                        "CUSTOM_METRIC_CREATE": "CUSTOM_METRIC_PATCH", "MP_SECRET_CREATE": "MP_SECRET_PATCH",
                    }[operation.kind]
                    read_operation = get_operation(shadow["kind"])
                observed, read_ids = self._read_state(read_operation, read_resource, token)
                request_ids.extend(read_ids)
                verified = _contains(observed, item["body"])
                result = {
                    "operationId": item["operationId"], "status": "applied" if verified else "ambiguous",
                    "resource": read_resource, "verified": verified, "observedStateSha256": _sha(observed),
                    "credentialRef": credential_ref,
                }
                results.append(result)
                if not verified:
                    overall = "ambiguous" if len(results) == 1 else "partial"
                    break
            except AdvisorError as exc:
                status_code = exc.details.get("status") if isinstance(exc.details, dict) else None
                ambiguous = exc.code in {"AMBIGUOUS_NETWORK_FAILURE", "SECRET_STORE_UNAVAILABLE", "SECRET_STORE_LOCKED", "SECRET_RESPONSE_INVALID", "INCOMPLETE_PROVIDER_RESPONSE"} or status_code in {408, 429, 500, 502, 503, 504}
                results.append({"operationId": item["operationId"], "status": "ambiguous" if ambiguous else "failed", "errorCode": exc.code, "verified": False})
                overall = ("ambiguous" if len(results) == 1 else "partial") if ambiguous else ("failed" if len(results) == 1 else "partial")
                break
        finished = self.now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        journal_id = f"ga4-journal-{_stamp(self.now())}-{uuid.uuid4().hex[:12]}"
        journal = {
            "schemaVersion": 2, "artifactType": "journal-entry", "generatedAt": finished,
            "journalId": journal_id, "planId": plan["planId"], "planSha256": plan["planSha256"],
            "confirmationSha256": confirmation, "startedAt": started, "finishedAt": finished,
            "status": overall, "requestIds": sorted(set(request_ids)), "operations": results,
            "readback": {"verified": overall == "applied", "observedStateSha256": _sha(results),
                         "message": "All requested fields matched independent readback." if overall == "applied" else "The result requires read-only reconciliation."},
            "projectRoot": str(project_root), "profileId": plan["profileId"], "planPath": str(plan_path),
        }
        validate_artifact_data("journal-entry", journal)
        location = store.write_journal(journal)
        return {"status": overall, "journal": journal, "artifact": location, "mutationPerformed": write_attempted}

    def reconcile(self, journal_path: Path) -> dict[str, Any]:
        journal = _load(journal_path.resolve(), "mutation journal")
        if journal.get("schemaVersion") != 2 or journal.get("artifactType") != "journal-entry" or journal.get("status") not in {"ambiguous", "partial"}:
            raise AdvisorError("RECONCILIATION_NOT_ALLOWED", "Only ambiguous or partial Stage 7 journals can be reconciled.", EXIT_INPUT)
        plan = _load(Path(journal["planPath"]), "mutation plan")
        self._validate_plan(plan)
        _, token, _ = self.auth.access_token(journal["profileId"])
        observations = []
        for item in plan["operations"]:
            operation = get_operation(item["kind"])
            state, request_ids = self._read_state(operation, item["resource"], token)
            duplicate = self._duplicate(operation, state, item["body"])
            verified = bool(duplicate) if operation.create else _contains(state, item["body"])
            observations.append({
                "operationId": item["operationId"], "matchingResource": duplicate.get("name") if duplicate else None,
                "verified": verified, "stateSha256": _sha(state), "requestIds": request_ids,
            })
        return {"status": "reconciled_read_only", "observations": observations, "mutationPerformed": False,
                "note": "No write was retried. Review matches before creating any new plan."}

    def capabilities(self, profile_id: str, property_name: str) -> dict[str, Any]:
        property_name = _resource(property_name)
        _, token, _ = self.auth.access_token(profile_id)
        response = self._request("GET", f"{ADMIN}/v1beta/{property_name}", token)
        state = response.data if isinstance(response.data, dict) else {}
        return {
            "profileId": profile_id, "property": property_name, "serviceLevel": state.get("serviceLevel"),
            "stableOperations": [kind for kind, item in OPERATIONS.items() if not item.experimental],
            "experimentalOperationsEnabled": False, "destructiveOperationsSupported": False,
            "mutationApprovalGranted": False, "networkUsed": True,
        }
