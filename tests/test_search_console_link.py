from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.search_console_link_service import (
    SearchConsoleLinkService,
    link_request_sha256,
    search_console_property_fingerprint,
    web_stream_fingerprint,
)


NOW = datetime(2026, 9, 17, 12, 10, tzinfo=timezone.utc)


class SearchConsoleLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.service = SearchConsoleLinkService(now=lambda: NOW)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, name: str, value: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _request(self, **changes) -> Path:
        stream = {
            "name": "properties/200/dataStreams/300", "displayName": "Example web",
            "type": "WEB_DATA_STREAM", "defaultUri": "https://example.test",
            "measurementId": "G-EXAMPLE123", "fingerprintSha256": "",
        }
        stream["fingerprintSha256"] = web_stream_fingerprint(stream)
        search = {
            "selectionKey": "sc-domain:example.test", "propertyType": "domain",
            "permissionLevel": "owner", "providerPermissionLevel": "SITE_OWNER",
            "fingerprintSha256": "",
        }
        search["fingerprintSha256"] = search_console_property_fingerprint(search)
        request = {
            "schemaVersion": 1, "artifactType": "search-console-link-request",
            "createdAt": "2026-09-17T12:04:00Z",
            "requestId": "search-console-link-request-test0001",
            "projectRoot": str(self.root), "profileId": "profile-0123456789abcdef",
            "mode": "browser_assisted", "language": "en", "desiredOutcome": "create_exact_link",
            "ga4Property": {"name": "properties/200", "displayName": "Example GA4"},
            "webStream": stream, "searchConsoleProperty": search, "scopeCompatibility": "exact",
            "preflight": {
                "observedAt": "2026-09-17T12:05:00Z", "linkState": "ready_for_review",
                "accountMatch": "matched", "ga4EditorStatus": "available",
                "linkButtonAvailable": True, "reviewReady": True,
            },
            "acknowledgements": {
                "samePageSetConfirmed": True, "dataVisibilityUnderstood": True,
                "oneToOneLimitsUnderstood": True, "delayAndRetentionUnderstood": True,
                "independentApiAnalysisUnderstood": True, "collectionPublicationSeparate": True,
            },
            "contentSha256": "",
        }
        for key, value in changes.items():
            if key.startswith("preflight_"):
                request["preflight"][key.removeprefix("preflight_")] = value
            elif key.startswith("ack_"):
                request["acknowledgements"][key.removeprefix("ack_")] = value
            elif key.startswith("search_"):
                request["searchConsoleProperty"][key.removeprefix("search_")] = value
            else:
                request[key] = value
        request["webStream"]["fingerprintSha256"] = web_stream_fingerprint(request["webStream"])
        request["searchConsoleProperty"]["fingerprintSha256"] = search_console_property_fingerprint(request["searchConsoleProperty"])
        request["contentSha256"] = link_request_sha256(request)
        return self._write("request.json", request)

    def _readback(self, *, state: str = "linked_created", browser: bool = True, **changes) -> Path:
        value = {
            "observedAt": "2026-09-17T12:12:00Z", "uiState": state,
            "ga4LinkTableVerified": state in {"linked_created", "exact_existing"},
            "searchConsoleAssociationVerified": False,
            "pairMatched": state in {"linked_created", "exact_existing"},
            "message": "The exact pair is visible in the GA4 link table.",
            "integratedDataState": "pending_expected" if state == "linked_created" else "not_checked",
            "browserInteractionRecorded": browser,
        }
        value.update(changes)
        return self._write("readback.json", value)

    def test_ready_plan_is_local_exact_and_plain_language(self) -> None:
        result = self.service.plan(self._request())
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["networkUsed"])
        self.assertFalse(result["mutationPerformed"])
        self.assertEqual(len(result["plan"]["operations"]), 1)
        self.assertEqual(result["plan"]["operations"][0]["action"], "UI_SUBMIT_CREATE_LINK")
        shown = self.service.show_plan(Path(result["artifact"]["path"]), "ru")
        self.assertIn("Полный SHA-256", shown["plain"])
        self.assertIn("не нужна для прямых отчётов", shown["plain"])

    def test_created_result_requires_exact_confirmation_and_is_single_use(self) -> None:
        planned = self.service.plan(self._request())
        plan = planned["plan"]
        path = Path(planned["artifact"]["path"])
        with self.assertRaises(AdvisorError) as caught:
            self.service.record(path, "0" * 64, "created", self._readback())
        self.assertEqual(caught.exception.code, "SEARCH_CONSOLE_LINK_CONFIRMATION_MISMATCH")
        recorded = self.service.record(path, plan["planSha256"], "created", self._readback())
        self.assertTrue(recorded["mutationPerformed"])
        self.assertEqual(recorded["result"]["outcome"], "created")
        self.assertIn("expected processing delay", recorded["result"]["limitations"][-1])
        shown = self.service.show(Path(recorded["artifact"]["path"]), "en")
        self.assertIn("Data availability is checked separately", shown["plain"])
        with self.assertRaises(AdvisorError) as consumed:
            self.service.record(path, plan["planSha256"], "created", self._readback())
        self.assertEqual(consumed.exception.code, "SEARCH_CONSOLE_LINK_PLAN_CONSUMED")

    def test_exact_existing_link_is_no_op(self) -> None:
        planned = self.service.plan(self._request(preflight_linkState="already_linked_exact", preflight_reviewReady=False, preflight_linkButtonAvailable=False))
        self.assertEqual(planned["status"], "no_op")
        self.assertEqual(planned["plan"]["operations"], [])
        readback = self._readback(state="exact_existing")
        recorded = self.service.record(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"], "already_linked_exact", readback)
        self.assertFalse(recorded["mutationPerformed"])

    def test_conflict_permission_and_acknowledgement_fail_closed(self) -> None:
        planned = self.service.plan(self._request(
            preflight_linkState="conflicting_ga4_link", search_permissionLevel="full",
            search_providerPermissionLevel="SITE_FULL_USER", ack_dataVisibilityUnderstood=False,
        ))
        self.assertEqual(planned["status"], "blocked")
        joined = " ".join(planned["plan"]["blockers"])
        self.assertIn("verified-owner", joined)
        self.assertIn("dataVisibilityUnderstood", joined)
        self.assertEqual(planned["plan"]["operations"], [])

    def test_scope_identity_and_fingerprint_tampering_are_blocked(self) -> None:
        path = self._request(scopeCompatibility="compatible_candidate")
        planned = self.service.plan(path)
        self.assertEqual(planned["status"], "blocked")
        value = json.loads(path.read_text(encoding="utf-8"))
        value["webStream"]["displayName"] = "Changed"
        value["contentSha256"] = link_request_sha256(value)
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(AdvisorError) as caught:
            self.service.plan(path)
        self.assertEqual(caught.exception.code, "ARTIFACT_VALIDATION_FAILED")

    def test_tampered_plan_and_expired_plan_are_rejected(self) -> None:
        planned = self.service.plan(self._request())
        path = Path(planned["artifact"]["path"])
        value = json.loads(path.read_text(encoding="utf-8"))
        value["resources"]["scopeCompatibility"] = "compatible_candidate"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(AdvisorError) as caught:
            self.service.show_plan(path)
        self.assertEqual(caught.exception.code, "ARTIFACT_VALIDATION_FAILED")
        fresh = self.service.plan(self._request(requestId="search-console-link-request-test0002"))
        later = SearchConsoleLinkService(now=lambda: NOW + timedelta(hours=1))
        expired = later.show_plan(Path(fresh["artifact"]["path"]))
        self.assertTrue(expired["expired"])
        self.assertEqual(expired["status"], "blocked")
        self.assertIn("has expired", expired["plain"])

    def test_ambiguous_outcome_does_not_claim_mutation_and_consumes_plan(self) -> None:
        planned = self.service.plan(self._request())
        readback = self._readback(
            state="ambiguous", ga4LinkTableVerified=False, pairMatched=False,
            message="The provider response was ambiguous.", integratedDataState="not_checked",
        )
        recorded = self.service.record(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"], "ambiguous", readback)
        self.assertFalse(recorded["mutationPerformed"])
        self.assertTrue(recorded["result"]["manualActionRequired"])

    def test_self_service_cannot_claim_browser_interaction(self) -> None:
        planned = self.service.plan(self._request(mode="self_service", preflight_accountMatch="user_confirmed"))
        self.assertEqual(planned["status"], "ready")
        with self.assertRaises(AdvisorError) as caught:
            self.service.record(Path(planned["artifact"]["path"]), planned["plan"]["planSha256"], "created", self._readback(browser=True))
        self.assertEqual(caught.exception.code, "SEARCH_CONSOLE_LINK_READBACK_INVALID")

    def test_readback_privacy_and_exact_pair_are_enforced(self) -> None:
        planned = self.service.plan(self._request())
        plan_path = Path(planned["artifact"]["path"])
        readback = self._readback(message="Signed in as person@example.test")
        with self.assertRaises(AdvisorError) as private:
            self.service.record(plan_path, planned["plan"]["planSha256"], "created", readback)
        self.assertEqual(private.exception.code, "SEARCH_CONSOLE_LINK_PRIVACY_BLOCKED")
        readback = self._readback(pairMatched=False)
        with self.assertRaises(AdvisorError) as mismatch:
            self.service.record(plan_path, planned["plan"]["planSha256"], "created", readback)
        self.assertEqual(mismatch.exception.code, "SEARCH_CONSOLE_LINK_READBACK_INVALID")


if __name__ == "__main__":
    unittest.main()
