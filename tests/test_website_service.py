from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.measurement_policy import plan_content_sha256
from scripts.google_analytics_cli.website_service import WebsiteService


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def approved_measurement(path: Path, project: Path) -> dict:
    plan = json.loads((ROOT / "contracts" / "fixtures" / "valid" / "measurement-plan-v2.json").read_text(encoding="utf-8"))
    plan["site"] = str(project)
    plan["status"] = "approved"
    plan["approvedAt"] = "2026-08-16T11:00:00Z"
    plan["contentSha256"] = plan_content_sha256(plan)
    plan["approvalSha256"] = plan["contentSha256"]
    path.write_text(json.dumps(plan), encoding="utf-8")
    return plan


def request(path: Path, project: Path, context: dict, measurement: dict) -> None:
    denied = {key: "denied" for key in ("analytics_storage", "ad_storage", "ad_user_data", "ad_personalization")}
    value = {
        "schemaVersion": 1, "artifactType": "website-change-request", "projectRoot": str(project),
        "contextId": context["contextId"], "contextSha256": context["contextSha256"],
        "measurementPlanId": measurement["planId"], "measurementPlanSha256": measurement["contentSha256"],
        "route": "direct", "intents": [
            {"kind": "CONSENT_DEFAULT", "signals": denied}, {"kind": "CONSENT_UPDATE", "signals": denied},
            {"kind": "DIRECT_TAG", "publicId": "G-TEST12345"},
            {"kind": "GTAG_EVENT", "eventName": "generate_lead", "authoritativeSource": "accepted backend response", "idempotency": "once per accepted lead"},
        ],
        "verificationCommands": [{
            "executable": "python", "arguments": ["-m", "unittest"], "cwd": str(project),
            "timeoutSeconds": 60, "expectedExitCodes": [0], "networkAllowed": False,
        }],
        "networkAllowed": False, "deploymentApproved": False,
    }
    path.write_text(json.dumps(value), encoding="utf-8")


class WebsiteServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = WebsiteService(now=lambda: NOW)

    def test_context_detects_four_acceptance_stacks(self) -> None:
        expected = {
            "static-html": "static-html", "laravel-blade": "laravel",
            "react-vite": "vite", "next-app": "nextjs-app-router",
        }
        import shutil
        for fixture, framework in expected.items():
            with self.subTest(fixture=fixture), tempfile.TemporaryDirectory() as temp:
                project = Path(temp).resolve()
                shutil.copytree(ROOT / "tests" / "fixtures" / "sites" / fixture, project, dirs_exist_ok=True)
                measurement_path = project / "measurement.json"
                approved_measurement(measurement_path, project)
                result = self.service.context(project, measurement_path)
                self.assertIn(framework, result["context"]["stack"]["frameworks"])
                self.assertFalse(result["context"]["codeExecuted"])
                self.assertFalse(result["context"]["networkUsed"])

    def test_plan_apply_verify_replay_and_no_deploy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            source = project / "index.html"
            source.write_text("<html>\n<head></head>\n<body>Fixture</body>\n</html>\n", encoding="utf-8")
            measurement_path = project / "measurement.json"
            measurement = approved_measurement(measurement_path, project)
            context_result = self.service.context(project, measurement_path)
            context = context_result["context"]
            change_path = project / "changes.json"
            request(change_path, project, context, measurement)
            patch_path = project / "install.patch"
            patch_path.write_text(
                "--- a/index.html\n+++ b/index.html\n@@ -1,4 +1,11 @@\n <html>\n-<head></head>\n+<head>\n+<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}\n+gtag('consent','default',{'analytics_storage':'denied','ad_storage':'denied','ad_user_data':'denied','ad_personalization':'denied'});</script>\n+<script async src=\"https://www.googletagmanager.com/gtag/js?id=G-TEST12345\"></script>\n+<script>gtag('js',new Date());gtag('config','G-TEST12345');\n+function analyticsConsentUpdate(state){gtag('consent','update',state);}\n+function trackAcceptedLead(){gtag('event','generate_lead');}</script>\n+</head>\n <body>Fixture</body>\n </html>\n",
                encoding="utf-8",
            )
            planned = self.service.plan(Path(context_result["artifact"]["path"]), change_path, patch_path)
            plan_path = Path(planned["artifact"]["path"])
            original = source.read_bytes()
            with self.assertRaises(AdvisorError):
                self.service.apply(plan_path, "0" * 64)
            self.assertEqual(source.read_bytes(), original)
            applied = self.service.apply(plan_path, planned["plan"]["planSha256"])
            self.assertEqual(applied["status"], "applied")
            self.assertFalse(applied["deploymentPerformed"])
            self.assertTrue(applied["journal"]["readback"]["verified"])
            self.assertTrue(all(not item["executed"] for item in applied["journal"]["verificationCommands"]))
            verified = self.service.verify(Path(applied["artifact"]["path"]))
            self.assertTrue(verified["verified"])
            with self.assertRaises(AdvisorError) as replay:
                self.service.apply(plan_path, planned["plan"]["planSha256"])
            self.assertEqual(replay.exception.code, "MUTATION_PLAN_REPLAYED")

    def test_stale_file_and_consent_order_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            source = project / "index.html"
            source.write_text("<html>\n<head></head>\n<body>Fixture</body>\n</html>\n", encoding="utf-8")
            measurement_path = project / "measurement.json"
            measurement = approved_measurement(measurement_path, project)
            context_result = self.service.context(project, measurement_path)
            change_path = project / "changes.json"
            request(change_path, project, context_result["context"], measurement)
            bad_patch = project / "bad.patch"
            bad_patch.write_text(
                "--- a/index.html\n+++ b/index.html\n@@ -1,4 +1,5 @@\n <html>\n-<head></head>\n+<head><script src=\"https://www.googletagmanager.com/gtag/js?id=G-TEST12345\"></script>\n+<script>gtag('consent','default',{'analytics_storage':'denied','ad_storage':'denied','ad_user_data':'denied','ad_personalization':'denied'});</script></head>\n <body>Fixture</body>\n </html>\n",
                encoding="utf-8",
            )
            with self.assertRaises(AdvisorError) as order:
                self.service.plan(Path(context_result["artifact"]["path"]), change_path, bad_patch)
            self.assertEqual(order.exception.code, "CONSENT_ORDER_INVALID")


if __name__ == "__main__":
    unittest.main()
