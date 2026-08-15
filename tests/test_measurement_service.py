from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.google_analytics_cli.contracts import validate_artifact
from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.measurement_service import MeasurementService


ROOT = Path(__file__).resolve().parents[1]


def complete_answers() -> dict:
    return {
        "projectId": "example-shop",
        "websiteUrl": "https://shop.example",
        "businessModel": "ecommerce",
        "timezone": "Asia/Bangkok",
        "currency": "USD",
        "confirmedByUser": True,
        "outcomes": [
            {
                "id": "checkout-started", "name": "Checkout started", "class": "secondary",
                "businessMeaning": "A visitor started checkout.", "owner": "commerce",
                "authoritativeState": "The checkout service created a checkout session.",
                "sourceOfTruth": "application", "proxySignals": ["checkout button click"],
                "decisionUse": "Find checkout abandonment.", "confirmed": True,
            },
            {
                "id": "paid-order", "name": "Paid order", "class": "primary",
                "businessMeaning": "The business received a confirmed payment.", "owner": "finance",
                "authoritativeState": "The order is marked paid after provider confirmation.",
                "sourceOfTruth": "payment", "proxySignals": ["thank-you page view"],
                "decisionUse": "Measure completed revenue.", "confirmed": True,
            },
        ],
        "events": [
            {
                "name": "begin_checkout", "outcomeId": "checkout-started", "businessMeaning": "A checkout session was created.",
                "trigger": "The application returns a successful checkout-session response.",
                "sourceOfTruth": "application", "collectionOwner": "browser-gtag", "keyEvent": False,
                "keyEventJustification": "This is a funnel step, not the final result.",
                "deduplication": "One event for each checkout-session ID.",
                "consentBehavior": "Send only according to the confirmed analytics_storage policy.",
                "implementationTargets": ["checkout frontend"],
                "parameters": [
                    {"name": "items", "meaning": "Checkout items", "source": "checkout.items", "type": "items", "required": True, "scope": "event", "privacy": "non-pii", "cardinality": "medium", "reportingUse": None, "registration": "none"}
                ],
                "verification": [{"method": "local-test", "successCriterion": "One event after one successful checkout-session response."}],
            },
            {
                "name": "purchase", "outcomeId": "paid-order", "businessMeaning": "A paid order was confirmed.",
                "trigger": "The order transitions to paid after payment-provider confirmation.",
                "sourceOfTruth": "payment", "collectionOwner": "browser-gtag", "keyEvent": True,
                "keyEventJustification": "A paid order is the primary business result.",
                "deduplication": "Use the immutable order ID as transaction_id across retries.",
                "consentBehavior": "Send only according to the confirmed analytics_storage policy.",
                "implementationTargets": ["payment confirmation flow"],
                "parameters": [
                    {"name": "transaction_id", "meaning": "Stable non-PII order ID", "source": "order.id", "type": "string", "required": True, "scope": "event", "privacy": "non-pii", "cardinality": "unique", "reportingUse": None, "registration": "none"},
                    {"name": "value", "meaning": "Paid order value", "source": "order.paid_total", "type": "number", "required": True, "scope": "event", "privacy": "non-pii", "cardinality": "medium", "reportingUse": None, "registration": "none"},
                    {"name": "currency", "meaning": "ISO 4217 charged currency", "source": "order.currency", "type": "string", "required": True, "scope": "event", "privacy": "non-pii", "cardinality": "low", "reportingUse": None, "registration": "none"},
                    {"name": "items", "meaning": "Purchased items", "source": "order.items", "type": "items", "required": True, "scope": "event", "privacy": "non-pii", "cardinality": "medium", "reportingUse": None, "registration": "none"},
                    {"name": "payment_method", "meaning": "Low-cardinality payment method family", "source": "order.payment_method_family", "type": "string", "required": False, "scope": "event", "privacy": "non-pii", "cardinality": "low", "reportingUse": "Compare completion by payment method", "registration": "event-dimension"},
                ],
                "verification": [{"method": "local-test", "successCriterion": "One purchase with the expected transaction_id for a synthetic paid order."}],
            },
        ],
        "ecommerce": {
            "enabled": True, "reason": "The website sells products online.", "events": ["begin_checkout", "purchase"],
            "itemIdentity": "Stable catalog item ID and name", "valueRule": "Paid order total after discounts, excluding later refunds",
            "currencySource": "Order ISO 4217 currency", "multiCurrencyPolicy": "Send each order in its charged currency",
            "transactionIdSource": "Immutable non-PII order ID", "transactionIdUniqueness": "Unique per order across all users",
            "purchaseState": "Payment-confirmed paid state", "refundState": "Confirmed provider refund",
            "deduplication": "One purchase per transaction_id across retries", "refundSemantics": "Send full or partial refund against the original transaction_id",
        },
        "identity": {"userIdPlanned": False, "measurementProtocolPlanned": False},
        "consent": {
            "mode": "advanced", "policyConfirmed": True, "policySource": "Owner-confirmed policy",
            "cmp": "Existing consent manager", "defaults": {
                "analytics_storage": "denied", "ad_storage": "denied", "ad_user_data": "denied", "ad_personalization": "denied",
            },
            "regions": ["FR"], "updateTrigger": "CMP callback before navigation",
            "persistence": "Existing consent-manager storage", "waitForUpdateMs": 500,
            "revocationFlow": "Update consent on the same page when the visitor changes the choice",
        },
        "funnels": [{
            "id": "checkout-to-purchase", "businessQuestion": "How many started checkouts become paid orders?",
            "steps": ["begin_checkout", "purchase"], "open": False, "directlyFollowed": False,
            "timeWindow": "30 days", "crossSession": True, "authoritativeCompletion": "purchase",
            "breakdownIntent": "traffic channel", "limitations": [], "stage10Ready": True,
        }],
    }


class MeasurementServiceTests(unittest.TestCase):
    def test_complete_local_workflow_is_immutable_and_performs_no_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "index.html").write_text("<html><body>shop</body></html>", encoding="utf-8")
            answers_path = root / "answers.json"
            answers_path.write_text(json.dumps(complete_answers()), encoding="utf-8")
            service = MeasurementService()
            context_result = service.context(
                root, "profile-test", baseline_path=None, without_baseline=True, answers_path=answers_path.resolve()
            )
            self.assertEqual(context_result["status"], "ready")
            self.assertFalse(context_result["mutationPerformed"])
            context_path = Path(context_result["artifact"]["path"])
            result = service.draft(context_path, root / ".google-analytics-advisor")
            plan = result["plan"]
            self.assertEqual(plan["status"], "draft")
            self.assertTrue(result["validation"]["approvable"])
            self.assertFalse(result["mutationPerformed"])
            validate_artifact("measurement-plan", Path(result["artifact"]["path"]))
            approved = service.approve(Path(result["artifact"]["path"]), plan["contentSha256"])
            self.assertEqual(approved["plan"]["status"], "approved")
            self.assertEqual(approved["plan"]["approvalSha256"], plan["contentSha256"])
            self.assertNotEqual(approved["artifact"]["path"], result["artifact"]["path"])
            self.assertFalse(approved["mutationPerformed"])
            validate_artifact("measurement-plan", Path(approved["artifact"]["path"]))
            shown = service.show(Path(approved["artifact"]["path"]), "plain")
            self.assertIn("Safety boundary", shown["rendered"])
            second = service.draft(context_path, root / ".google-analytics-advisor")
            self.assertNotEqual(second["plan"]["planId"], plan["planId"])

    def test_unknown_business_and_consent_create_blocked_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            service = MeasurementService()
            context = service.context(root, "profile-test", baseline_path=None, without_baseline=True, answers_path=None)
            result = service.draft(Path(context["artifact"]["path"]), root / ".google-analytics-advisor")
            self.assertEqual(result["plan"]["status"], "blocked")
            self.assertFalse(result["validation"]["approvable"])
            self.assertIsNone(result["approvalCommand"])
            with self.assertRaises(AdvisorError):
                service.approve(Path(result["artifact"]["path"]), result["plan"]["contentSha256"])

    def test_wrong_confirmation_and_pii_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            answers = complete_answers()
            answers_path = root / "answers.json"
            answers_path.write_text(json.dumps(answers), encoding="utf-8")
            service = MeasurementService()
            context = service.context(root, "profile-test", baseline_path=None, without_baseline=True, answers_path=answers_path.resolve())
            draft = service.draft(Path(context["artifact"]["path"]), root / ".google-analytics-advisor")
            with self.assertRaises(AdvisorError) as caught:
                service.approve(Path(draft["artifact"]["path"]), "0" * 64)
            self.assertEqual(caught.exception.code, "CONFIRMATION_MISMATCH")
            answers["contactEmail"] = "person@example.com"
            answers_path.write_text(json.dumps(answers), encoding="utf-8")
            with self.assertRaises(AdvisorError) as pii:
                service.context(root, "profile-test", baseline_path=None, without_baseline=True, answers_path=answers_path.resolve())
            self.assertEqual(pii.exception.code, "PII_BLOCKED")

    def test_legacy_v1_migration_creates_blocked_v2_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            legacy_dir = root / ".google-analytics-advisor" / "legacy"
            legacy_dir.mkdir(parents=True)
            legacy = legacy_dir / "plan.json"
            legacy.write_text((ROOT / "contracts" / "fixtures" / "valid" / "measurement-plan.json").read_text(encoding="utf-8"), encoding="utf-8")
            result = MeasurementService().migrate(legacy)
            self.assertEqual(result["plan"]["schemaVersion"], 2)
            self.assertEqual(result["plan"]["status"], "blocked")
            self.assertTrue(result["plan"]["openQuestions"])
            validate_artifact("measurement-plan", Path(result["artifact"]["path"]))

    def test_source_change_makes_draft_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / "index.html"
            source.write_text("<html>initial</html>", encoding="utf-8")
            answers_path = root / "answers.json"
            answers_path.write_text(json.dumps(complete_answers()), encoding="utf-8")
            service = MeasurementService()
            context = service.context(root, "profile-test", baseline_path=None, without_baseline=True, answers_path=answers_path.resolve())
            draft = service.draft(Path(context["artifact"]["path"]), root / ".google-analytics-advisor")
            source.write_text("<html><script>gtag('event','purchase')</script></html>", encoding="utf-8")
            with self.assertRaises(AdvisorError) as caught:
                service.approve(Path(draft["artifact"]["path"]), draft["plan"]["contentSha256"])
            self.assertEqual(caught.exception.code, "STALE_PLAN")

    def test_explicit_baseline_supplies_resource_identity_and_key_event_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            answers_path = root / "answers.json"
            answers_path.write_text(json.dumps(complete_answers()), encoding="utf-8")
            baseline = json.loads((ROOT / "contracts" / "fixtures" / "valid" / "baseline-report.json").read_text(encoding="utf-8"))
            baseline["projectRoot"] = str(root)
            baseline["profileRef"] = "profile-test"
            baseline["facts"]["keyEventCount"] = 30
            baseline_path = root / "baseline.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            service = MeasurementService()
            context = service.context(
                root, "profile-test", baseline_path=baseline_path.resolve(), without_baseline=False,
                answers_path=answers_path.resolve(),
            )
            self.assertEqual(context["context"]["baseline"]["targets"]["property"], "properties/123")
            draft = service.draft(Path(context["artifact"]["path"]), root / ".google-analytics-advisor")
            self.assertEqual(draft["plan"]["status"], "blocked")
            self.assertTrue(any("key events" in item for item in draft["validation"]["blockers"]))


if __name__ == "__main__":
    unittest.main()
