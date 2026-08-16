from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.google_analytics_cli.admin_mutation_registry import get_operation, validate_body
from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.ga4_mutation_service import Ga4MutationService, mutation_plan_sha256
from scripts.google_analytics_cli.http import JsonResponse
from scripts.google_analytics_cli.measurement_policy import plan_content_sha256
from scripts.google_analytics_cli.secret_store import SecretStore


ROOT = Path(__file__).resolve().parents[1]


class FakeAuth:
    def __init__(self, secrets: SecretStore | None = None) -> None:
        self.secrets = secrets or MemorySecrets()

    def access_token(self, profile_id: str | None = None):
        return profile_id, "access-token-not-serialized", {"identity": {"email": "test@example.invalid"}}


class MemorySecrets(SecretStore):
    def __init__(self, *, fail_put: bool = False) -> None:
        self.values: dict[str, bytes] = {}
        self.fail_put = fail_put

    def put(self, key: str, value: bytes) -> None:
        if self.fail_put:
            raise AdvisorError("SECRET_STORE_LOCKED", "locked", 3)
        self.values[key] = value

    def get(self, key: str) -> bytes:
        return self.values[key]

    def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


class FakeAdminTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.property = {
            "name": "properties/200", "displayName": "Old name", "timeZone": "Asia/Bangkok",
            "currencyCode": "USD", "serviceLevel": "GOOGLE_ANALYTICS_STANDARD",
        }
        self.stream = {"name": "properties/200/dataStreams/300", "type": "WEB_DATA_STREAM", "displayName": "Old stream", "webStreamData": {"defaultUri": "https://example.test", "measurementId": "G-TEST"}}
        self.key_events: list[dict[str, Any]] = []
        self.secrets: list[dict[str, Any]] = []
        self.mutate_before_preflight = False
        self.get_count = 0
        self.patch_count = 0
        self.fail_patch_at: int | None = None
        self.mismatch_readback = False

    def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if method == "GET" and url.endswith("/v1beta/properties/200"):
            self.get_count += 1
            if self.mutate_before_preflight and self.get_count >= 2:
                self.property["displayName"] = "Someone else changed it"
            value = dict(self.property)
            if self.mismatch_readback and self.patch_count:
                value["displayName"] = "Readback mismatch"
            return JsonResponse(200, value, f"read-{self.get_count}", {})
        if method == "PATCH" and "/v1beta/properties/200?" in url:
            self.patch_count += 1
            if self.fail_patch_at == self.patch_count:
                raise AdvisorError("AMBIGUOUS_NETWORK_FAILURE", "unknown", 5, details={"ambiguous": True})
            payload = dict(kwargs["payload"])
            payload.pop("name", None)
            self.property.update(payload)
            return JsonResponse(200, dict(self.property), "write-1", {})
        if method == "GET" and url.endswith("/v1beta/properties/200/dataStreams/300"):
            return JsonResponse(200, dict(self.stream), "read-stream", {})
        if method == "PATCH" and "/v1beta/properties/200/dataStreams/300?" in url:
            self.patch_count += 1
            if self.fail_patch_at == self.patch_count:
                raise AdvisorError("AMBIGUOUS_NETWORK_FAILURE", "unknown", 5, details={"ambiguous": True})
            payload = dict(kwargs["payload"])
            payload.pop("name", None)
            self.stream.update(payload)
            return JsonResponse(200, dict(self.stream), "write-stream", {})
        if method == "GET" and "/keyEvents" in url:
            return JsonResponse(200, {"keyEvents": list(self.key_events)}, "read-events", {})
        if method == "POST" and url.endswith("/v1beta/properties/200/keyEvents"):
            created = {"name": "properties/200/keyEvents/900", **kwargs["payload"]}
            self.key_events.append(created)
            return JsonResponse(200, dict(created), "write-event", {})
        if method == "GET" and url.endswith("/v1beta/properties/200/keyEvents/900"):
            return JsonResponse(200, dict(self.key_events[0]), "read-event", {})
        if method == "GET" and "/measurementProtocolSecrets" in url:
            if url.endswith("/measurementProtocolSecrets?pageSize=200"):
                return JsonResponse(200, {"measurementProtocolSecrets": [dict(item) for item in self.secrets]}, "read-secrets", {})
            return JsonResponse(200, dict(self.secrets[0]), "read-secret", {})
        if method == "GET" and url.endswith("/v1alpha/properties/200/dataStreams/300/enhancedMeasurementSettings"):
            return JsonResponse(200, {"name": "properties/200/dataStreams/300/enhancedMeasurementSettings", "scrollsEnabled": True, "newProviderField": True}, "read-alpha", {})
        if method == "POST" and url.endswith("/measurementProtocolSecrets"):
            created = {
                "name": "properties/200/dataStreams/300/measurementProtocolSecrets/700",
                "displayName": kwargs["payload"]["displayName"], "secretValue": "sensitive-canary-value",
            }
            self.secrets.append(dict(created))
            return JsonResponse(200, dict(created), "write-secret", {})
        raise AssertionError(f"Unexpected request: {method} {url}")


class Ga4MutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)

    def approved_measurement(self, path: Path, *, mp: bool = False) -> dict[str, Any]:
        plan = json.loads((ROOT / "contracts" / "fixtures" / "valid" / "measurement-plan-v2.json").read_text(encoding="utf-8"))
        plan["status"] = "approved"
        plan["approvedAt"] = "2026-08-16T11:00:00Z"
        plan["identity"]["measurementProtocolPlanned"] = mp
        if mp:
            plan["identity"]["clientSessionLinkage"] = "client_id and session_id"
            plan["identity"]["lateArrivalPolicy"] = "within documented limits"
        plan["contentSha256"] = plan_content_sha256(plan)
        plan["approvalSha256"] = plan["contentSha256"]
        path.write_text(json.dumps(plan), encoding="utf-8")
        return plan

    @staticmethod
    def write_changes(path: Path, project: Path, operations: list[dict[str, Any]], **extra: Any) -> None:
        value = {
            "schemaVersion": 1, "changeRequestType": "ga4-change-request", "projectRoot": str(project),
            "profileId": "profile-test", "operations": operations, **extra,
        }
        path.write_text(json.dumps(value), encoding="utf-8")

    def service(self, transport: FakeAdminTransport, secrets: MemorySecrets | None = None) -> Ga4MutationService:
        secret_store = secrets or MemorySecrets()
        return Ga4MutationService(auth=FakeAuth(secret_store), transport=transport, secrets=secret_store, now=lambda: self.now)

    def test_registry_rejects_unknown_fields_and_wildcard_masks(self) -> None:
        operation = get_operation("PROPERTY_PATCH")
        with self.assertRaises(AdvisorError) as unknown:
            validate_body(operation, {"deleteTime": "now"}, ["deleteTime"])
        self.assertEqual(unknown.exception.code, "GA4_FIELD_NOT_ALLOWED")
        with self.assertRaises(AdvisorError) as wildcard:
            validate_body(operation, {"displayName": "New"}, ["*"])
        self.assertEqual(wildcard.exception.code, "INVALID_FIELD_MASK")
        with self.assertRaises(AdvisorError):
            get_operation("PROPERTY_DELETE")

    def test_existing_requested_state_returns_no_op_without_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path, changes_path = project / "measurement.json", project / "changes.json"
            self.approved_measurement(measurement_path)
            self.write_changes(changes_path, project, [{
                "kind": "PROPERTY_PATCH", "resource": "properties/200", "fieldMask": ["displayName"],
                "body": {"displayName": "Old name"},
            }])
            result = self.service(FakeAdminTransport()).plan("profile-test", measurement_path, changes_path)
            self.assertEqual(result["status"], "no_op")
            self.assertIsNone(result["plan"])
            self.assertFalse((project / ".google-analytics-advisor" / "mutation-plans").exists())

    def test_plan_apply_readback_and_replay_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path, changes_path = project / "measurement.json", project / "changes.json"
            self.approved_measurement(measurement_path)
            self.write_changes(changes_path, project, [{
                "kind": "PROPERTY_PATCH", "resource": "properties/200", "fieldMask": ["displayName"],
                "body": {"displayName": "New name"}, "rationale": "Use a clear property name.",
            }])
            transport = FakeAdminTransport()
            service = self.service(transport)
            planned = service.plan("profile-test", measurement_path, changes_path)
            plan_path = Path(planned["artifact"]["path"])
            self.assertEqual(planned["status"], "confirmation_required")
            self.assertFalse(planned["mutationPerformed"])
            self.assertEqual(planned["plan"]["planSha256"], mutation_plan_sha256(planned["plan"]))
            applied = service.apply(plan_path, planned["plan"]["planSha256"])
            self.assertEqual(applied["status"], "applied")
            self.assertTrue(applied["journal"]["readback"]["verified"])
            writes = [call for call in transport.calls if call["method"] == "PATCH"]
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0]["max_attempts"], 1)
            self.assertIn("updateMask=display_name", writes[0]["url"])
            with self.assertRaises(AdvisorError) as replay:
                service.apply(plan_path, planned["plan"]["planSha256"])
            self.assertEqual(replay.exception.code, "MUTATION_PLAN_REPLAYED")

    def test_wrong_hash_and_stale_precondition_never_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path, changes_path = project / "measurement.json", project / "changes.json"
            self.approved_measurement(measurement_path)
            self.write_changes(changes_path, project, [{
                "kind": "PROPERTY_PATCH", "resource": "properties/200", "fieldMask": ["displayName"],
                "body": {"displayName": "New name"},
            }])
            transport = FakeAdminTransport()
            service = self.service(transport)
            planned = service.plan("profile-test", measurement_path, changes_path)
            with self.assertRaises(AdvisorError) as mismatch:
                service.apply(Path(planned["artifact"]["path"]), "0" * 64)
            self.assertEqual(mismatch.exception.code, "MUTATION_CONFIRMATION_MISMATCH")
            transport.mutate_before_preflight = True
            with self.assertRaises(AdvisorError) as stale:
                service.apply(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"])
            self.assertEqual(stale.exception.code, "STALE_PRECONDITION")
            self.assertFalse(any(call["method"] in {"POST", "PATCH"} for call in transport.calls))

    def test_create_is_single_operation_and_key_event_must_be_planned(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path, changes_path = project / "measurement.json", project / "changes.json"
            self.approved_measurement(measurement_path)
            create = {
                "kind": "KEY_EVENT_CREATE", "resource": "properties/200", "body": {
                    "eventName": "generate_lead", "countingMethod": "ONCE_PER_EVENT",
                }, "fieldMask": [],
            }
            self.write_changes(changes_path, project, [create, {
                "kind": "PROPERTY_PATCH", "resource": "properties/200", "body": {"displayName": "New"},
                "fieldMask": ["displayName"],
            }])
            service = self.service(FakeAdminTransport())
            with self.assertRaises(AdvisorError) as single:
                service.plan("profile-test", measurement_path, changes_path)
            self.assertEqual(single.exception.code, "CREATE_PLAN_MUST_BE_SINGLE")
            create["body"]["eventName"] = "unapproved_event"
            self.write_changes(changes_path, project, [create])
            with self.assertRaises(AdvisorError) as unplanned:
                service.plan("profile-test", measurement_path, changes_path)
            self.assertEqual(unplanned.exception.code, "KEY_EVENT_NOT_PLANNED")

    def test_alpha_requires_both_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path, changes_path = project / "measurement.json", project / "changes.json"
            self.approved_measurement(measurement_path)
            operation = [{
                "kind": "ENHANCED_MEASUREMENT_UPDATE", "resource": "properties/200/dataStreams/300",
                "body": {"scrollsEnabled": True}, "fieldMask": ["scrollsEnabled"],
            }]
            self.write_changes(changes_path, project, operation, experimentalAdminAlpha=True)
            with self.assertRaises(AdvisorError) as gated:
                self.service(FakeAdminTransport()).plan("profile-test", measurement_path, changes_path)
            self.assertEqual(gated.exception.code, "EXPERIMENTAL_GATE_REQUIRED")
            self.write_changes(changes_path, project, operation, experimentalAdminAlpha=True, alphaWarningAccepted=True)
            with self.assertRaises(AdvisorError) as drift:
                self.service(FakeAdminTransport()).plan("profile-test", measurement_path, changes_path)
            self.assertEqual(drift.exception.code, "EXPERIMENTAL_CONTRACT_DRIFT")

    def test_measurement_protocol_secret_is_stored_and_never_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path, changes_path = project / "measurement.json", project / "changes.json"
            self.approved_measurement(measurement_path, mp=True)
            self.write_changes(changes_path, project, [{
                "kind": "MP_SECRET_CREATE", "resource": "properties/200/dataStreams/300",
                "body": {"displayName": "Advisor backend"}, "fieldMask": [],
            }])
            transport, secrets = FakeAdminTransport(), MemorySecrets()
            service = self.service(transport, secrets)
            planned = service.plan("profile-test", measurement_path, changes_path)
            applied = service.apply(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"])
            rendered = json.dumps(applied)
            self.assertEqual(applied["status"], "applied")
            self.assertNotIn("sensitive-canary-value", rendered)
            self.assertNotIn("secretValue", rendered)
            self.assertEqual(list(secrets.values.values()), [b"sensitive-canary-value"])
            for artifact in (project / ".google-analytics-advisor").rglob("*.json"):
                self.assertNotIn("sensitive-canary-value", artifact.read_text(encoding="utf-8"))

    def test_secret_store_failure_is_ambiguous_and_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path, changes_path = project / "measurement.json", project / "changes.json"
            self.approved_measurement(measurement_path, mp=True)
            self.write_changes(changes_path, project, [{
                "kind": "MP_SECRET_CREATE", "resource": "properties/200/dataStreams/300",
                "body": {"displayName": "Advisor backend"}, "fieldMask": [],
            }])
            transport, secrets = FakeAdminTransport(), MemorySecrets(fail_put=True)
            service = self.service(transport, secrets)
            planned = service.plan("profile-test", measurement_path, changes_path)
            result = service.apply(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"])
            self.assertEqual(result["status"], "ambiguous")
            self.assertEqual(sum(1 for call in transport.calls if call["method"] == "POST"), 1)

    def test_tampered_plan_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path, changes_path = project / "measurement.json", project / "changes.json"
            self.approved_measurement(measurement_path)
            self.write_changes(changes_path, project, [{
                "kind": "PROPERTY_PATCH", "resource": "properties/200", "body": {"displayName": "New"},
                "fieldMask": ["displayName"],
            }])
            service = self.service(FakeAdminTransport())
            planned = service.plan("profile-test", measurement_path, changes_path)
            path = Path(planned["artifact"]["path"])
            value = json.loads(path.read_text(encoding="utf-8"))
            value["operations"][0]["body"]["displayName"] = "Tampered"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(AdvisorError) as tampered:
                service.apply(path, planned["plan"]["planSha256"])
            self.assertEqual(tampered.exception.code, "MUTATION_PLAN_TAMPERED")

            value = dict(planned["plan"])
            value["operations"] = [dict(planned["plan"]["operations"][0])]
            value["operations"][0]["method"] = "PUT"
            value["planSha256"] = mutation_plan_sha256(value)
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(AdvisorError) as forged:
                service.apply(path, value["planSha256"])
            self.assertEqual(forged.exception.code, "INVALID_MUTATION_PLAN")
            self.assertFalse(any(call["method"] in {"POST", "PATCH"} for call in service.transport.calls))

    def test_expired_plan_is_blocked_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path, changes_path = project / "measurement.json", project / "changes.json"
            self.approved_measurement(measurement_path)
            self.write_changes(changes_path, project, [{"kind": "PROPERTY_PATCH", "resource": "properties/200", "body": {"displayName": "New"}, "fieldMask": ["displayName"]}])
            transport = FakeAdminTransport()
            planned = self.service(transport).plan("profile-test", measurement_path, changes_path)
            later = self.now + timedelta(minutes=31)
            service = Ga4MutationService(auth=FakeAuth(), transport=transport, secrets=MemorySecrets(), now=lambda: later)
            with self.assertRaises(AdvisorError) as expired:
                service.apply(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"])
            self.assertEqual(expired.exception.code, "MUTATION_PLAN_EXPIRED")
            self.assertFalse(any(call["method"] in {"POST", "PATCH"} for call in transport.calls))

    def test_incomplete_readback_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path, changes_path = project / "measurement.json", project / "changes.json"
            self.approved_measurement(measurement_path)
            self.write_changes(changes_path, project, [{"kind": "PROPERTY_PATCH", "resource": "properties/200", "body": {"displayName": "New"}, "fieldMask": ["displayName"]}])
            transport = FakeAdminTransport()
            service = self.service(transport)
            planned = service.plan("profile-test", measurement_path, changes_path)
            transport.mismatch_readback = True
            result = service.apply(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"])
            self.assertEqual(result["status"], "ambiguous")
            self.assertFalse(result["journal"]["readback"]["verified"])

    def test_second_uncertain_write_produces_partial_and_stops(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path, changes_path = project / "measurement.json", project / "changes.json"
            self.approved_measurement(measurement_path)
            self.write_changes(changes_path, project, [
                {"kind": "PROPERTY_PATCH", "resource": "properties/200", "body": {"displayName": "New property"}, "fieldMask": ["displayName"]},
                {"kind": "WEB_STREAM_PATCH", "resource": "properties/200/dataStreams/300", "body": {"displayName": "New stream"}, "fieldMask": ["displayName"]},
            ])
            transport = FakeAdminTransport()
            service = self.service(transport)
            planned = service.plan("profile-test", measurement_path, changes_path)
            transport.fail_patch_at = 2
            result = service.apply(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"])
            self.assertEqual(result["status"], "partial")
            self.assertEqual([item["status"] for item in result["journal"]["operations"]], ["applied", "ambiguous"])
            self.assertEqual(transport.patch_count, 2)


if __name__ == "__main__":
    unittest.main()
