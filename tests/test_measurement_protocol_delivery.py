from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.http import JsonResponse
from scripts.google_analytics_cli.measurement_policy import plan_content_sha256
from scripts.google_analytics_cli.measurement_protocol_service import MeasurementProtocolService
from scripts.google_analytics_cli.secret_store import SecretStore


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


class MemorySecrets(SecretStore):
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def put(self, key: str, value: bytes) -> None:
        self.values[key] = value

    def get(self, key: str) -> bytes:
        return self.values[key]

    def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


class FakeTransport:
    def __init__(self, *, messages: list[dict[str, Any]] | None = None, fail: bool = False) -> None:
        self.messages = messages or []
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.fail:
            raise AdvisorError("AMBIGUOUS_NETWORK_FAILURE", "unknown", 5, details={"ambiguous": True})
        data = {"validationMessages": self.messages} if "/debug/" in url else None
        return JsonResponse(200 if data is not None else 204, data, "mp-request", {})


def measurement(path: Path, project: Path) -> dict:
    plan = json.loads((ROOT / "contracts" / "fixtures" / "valid" / "measurement-plan-v2.json").read_text(encoding="utf-8"))
    plan["site"] = str(project)
    plan["status"] = "approved"
    plan["approvedAt"] = "2026-08-16T11:00:00Z"
    plan["identity"]["measurementProtocolPlanned"] = True
    plan["identity"]["clientSessionLinkage"] = "client_id and session_id from the web session"
    plan["identity"]["lateArrivalPolicy"] = "within 72 hours"
    plan["events"][0]["collectionOwner"] = "backend-mp"
    plan["contentSha256"] = plan_content_sha256(plan)
    plan["approvalSha256"] = plan["contentSha256"]
    path.write_text(json.dumps(plan), encoding="utf-8")
    return plan


class MeasurementProtocolDeliveryTests(unittest.TestCase):
    def service(self, transport: FakeTransport, secret: str = "canary-protected-secret") -> MeasurementProtocolService:
        return MeasurementProtocolService(transport=transport, secrets=MemorySecrets({"mp/test": secret.encode()}), now=lambda: NOW)

    def test_debug_plan_validation_never_serializes_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path = project / "measurement.json"
            measurement(measurement_path, project)
            payload_path = project / "payload.json"
            payload_path.write_text(json.dumps({"client_id": "12345.67890", "events": [{"name": "generate_lead", "params": {"session_id": 123, "engagement_time_msec": 10}}]}), encoding="utf-8")
            transport = FakeTransport()
            service = self.service(transport)
            planned = service.delivery_plan(measurement_path, payload_path, "mp/test", "G-TEST12345", "debug")
            rendered = json.dumps(planned)
            self.assertNotIn("canary-protected-secret", rendered)
            applied = service.validate(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"])
            self.assertTrue(applied["debugValidated"])
            self.assertFalse(applied["productionEventsSent"])
            self.assertIn("validation_behavior=ENFORCE_RECOMMENDATIONS", transport.calls[0]["url"])
            self.assertNotIn("canary-protected-secret", json.dumps(applied))

    def test_debug_messages_block_and_production_timeout_is_ambiguous_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path = project / "measurement.json"
            measurement(measurement_path, project)
            payload_path = project / "payload.json"
            payload_path.write_text(json.dumps({"client_id": "12345.67890", "events": [{"name": "generate_lead", "params": {}}]}), encoding="utf-8")
            debug_transport = FakeTransport(messages=[{"validationCode": "VALUE_INVALID", "description": "fixture"}])
            debug = self.service(debug_transport)
            debug_plan = debug.delivery_plan(measurement_path, payload_path, "mp/test", "G-TEST12345", "debug")
            blocked = debug.validate(Path(debug_plan["artifact"]["path"]), debug_plan["plan"]["planSha256"])
            self.assertEqual(blocked["status"], "blocked")
            production_transport = FakeTransport(fail=True)
            production = self.service(production_transport)
            production_plan = production.delivery_plan(measurement_path, payload_path, "mp/test", "G-TEST12345", "production")
            result = production.send(Path(production_plan["artifact"]["path"]), production_plan["plan"]["planSha256"])
            self.assertEqual(result["status"], "ambiguous")
            self.assertEqual(len(production_transport.calls), 1)
            self.assertEqual(result["productionEventsSent"], "unknown")

    def test_payload_blocks_pii_unapproved_owner_and_bad_ecommerce(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            measurement_path = project / "measurement.json"
            measurement(measurement_path, project)
            payload_path = project / "payload.json"
            payload_path.write_text(json.dumps({"client_id": "person@example.com", "events": [{"name": "generate_lead", "params": {}}]}), encoding="utf-8")
            with self.assertRaises(AdvisorError) as pii:
                self.service(FakeTransport()).delivery_plan(measurement_path, payload_path, "mp/test", "G-TEST12345", "debug")
            self.assertEqual(pii.exception.code, "PII_BLOCKED")


if __name__ == "__main__":
    unittest.main()
