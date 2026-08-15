from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.google_analytics_cli.measurement_policy import evaluate_plan, plan_content_sha256


ROOT = Path(__file__).resolve().parents[1]


class MeasurementPolicyTests(unittest.TestCase):
    def plan(self) -> dict:
        return json.loads((ROOT / "contracts" / "fixtures" / "valid" / "measurement-plan-v2.json").read_text(encoding="utf-8"))

    def issues(self, plan: dict, budgets: dict | None = None) -> list[str]:
        plan["contentSha256"] = plan_content_sha256(plan)
        return evaluate_plan(plan, budgets)["blockers"]

    def test_reserved_custom_event_and_parameter_limits_are_blocked(self) -> None:
        plan = self.plan()
        event = plan["events"][0]
        event["name"] = "_private_event"
        event["catalogClass"] = "custom"
        event["customJustification"] = "Needed"
        event["verificationChecks"][0]["event"] = event["name"]
        event["parameters"] = [{
            "name": f"field_{index}", "meaning": "Synthetic field", "source": "synthetic", "type": "string",
            "required": False, "scope": "event", "privacy": "non-pii", "cardinality": "low",
            "reportingUse": None, "registration": "none",
        } for index in range(26)]
        issues = self.issues(plan)
        self.assertTrue(any("reserved" in item.lower() for item in issues))
        self.assertTrue(any("more than 25" in item for item in issues))

    def test_proxy_key_event_is_blocked_when_outcome_has_stronger_source(self) -> None:
        plan = self.plan()
        plan["events"][0]["authoritativeSource"] = "browser"
        issues = self.issues(plan)
        self.assertTrue(any("proxy" in item for item in issues))

    def test_custom_definition_cardinality_and_budget_are_blocked(self) -> None:
        plan = self.plan()
        plan["events"][0]["parameters"] = [{
            "name": "record_id", "meaning": "Synthetic record ID", "source": "record.id", "type": "string",
            "required": False, "scope": "event", "privacy": "non-pii", "cardinality": "unique",
            "reportingUse": "Find a record", "registration": "event-dimension",
        }]
        plan["customDefinitions"] = [{
            "parameter": "record_id", "registration": "event-dimension", "reportingUse": "Find a record",
            "cardinality": "high", "budgetAvailable": False,
        }]
        issues = self.issues(plan, {"keyEvents": 30, "event-dimension": 50, "user-dimension": 0, "event-metric": 0})
        self.assertTrue(any("cardinality" in item for item in issues))
        self.assertTrue(any("budget" in item for item in issues))

    def test_server_owner_requires_measurement_protocol_linkage(self) -> None:
        plan = self.plan()
        plan["events"][0]["collectionOwner"] = "backend-mp"
        issues = self.issues(plan)
        self.assertTrue(any("Measurement Protocol" in item for item in issues))

    def test_funnel_references_and_consent_defaults_are_blocked(self) -> None:
        plan = self.plan()
        plan["funnels"] = [{
            "id": "lead-funnel", "businessQuestion": "Does a visit become a lead?",
            "steps": ["page_view", "generate_lead"], "open": True, "directlyFollowed": False,
            "timeWindow": "30 days", "crossSession": True, "authoritativeCompletion": "purchase",
            "breakdownIntent": None, "limitations": [], "stage10Ready": False,
        }]
        plan["consent"]["defaults"]["analytics_storage"] = "policy-dependent"
        issues = self.issues(plan)
        self.assertTrue(any("unknown events" in item for item in issues))
        self.assertTrue(any("authoritative completion" in item for item in issues))
        self.assertTrue(any("explicit granted or denied" in item for item in issues))

    def test_purchase_requires_prescribed_identity_and_currency(self) -> None:
        plan = self.plan()
        event = copy.deepcopy(plan["events"][0])
        event.update({"name": "purchase", "catalogClass": "recommended", "parameters": [{
            "name": "value", "meaning": "Paid value", "source": "order.total", "type": "number",
            "required": True, "scope": "event", "privacy": "non-pii", "cardinality": "medium",
            "reportingUse": None, "registration": "none",
        }]})
        event["verificationChecks"][0]["event"] = "purchase"
        plan["events"] = [event]
        issues = self.issues(plan)
        self.assertTrue(any("transaction_id" in item and "items" in item for item in issues))
        self.assertTrue(any("currency is required" in item for item in issues))


if __name__ == "__main__":
    unittest.main()
