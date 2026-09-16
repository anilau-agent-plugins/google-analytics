from __future__ import annotations

import json
import unittest

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.http import JsonResponse
from scripts.google_analytics_cli.oauth import SEARCH_CONSOLE_SCOPES
from scripts.google_analytics_cli.search_console import SearchConsoleService, normalize_sites


class FakeAuth:
    def __init__(self, *, ready=True, response=None, error=None):
        self.ready = ready
        self.access_calls = 0
        self.transport = Transport(response=response, error=error)
        self.json_transport = self.transport

    def status(self, profile_id):
        return {
            "profileId": profile_id,
            "capabilities": {
                "searchConsole": {
                    "status": "ready" if self.ready else "authorization_required",
                    "missingScopes": [] if self.ready else list(SEARCH_CONSOLE_SCOPES),
                }
            },
        }

    def access_token(self, profile_id):
        self.access_calls += 1
        return profile_id, "private-access-token", {"grantedScopes": list(SEARCH_CONSOLE_SCOPES)}


class Transport:
    def __init__(self, *, response=None, error=None):
        self.response = {"siteEntry": []} if response is None else response
        self.error = error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        return JsonResponse(200, self.response, "request-1", {})


class SearchConsoleTests(unittest.TestCase):
    def test_normalizes_domain_url_prefix_and_both_permission_enum_styles(self) -> None:
        result = normalize_sites({"siteEntry": [
            {"siteUrl": "https://www.example.com/", "permissionLevel": "siteFullUser"},
            {"siteUrl": "sc-domain:example.com", "permissionLevel": "SITE_OWNER"},
            {"siteUrl": "http://blog.example.com/path/", "permissionLevel": "SITE_RESTRICTED_USER"},
        ]})
        self.assertEqual(result["status"], "ready")
        by_url = {item["siteUrl"]: item for item in result["sites"]}
        self.assertEqual(by_url["sc-domain:example.com"]["propertyType"], "domain")
        self.assertEqual(by_url["sc-domain:example.com"]["permissionLevel"], "owner")
        self.assertEqual(by_url["https://www.example.com/"]["propertyType"], "url_prefix")
        self.assertEqual(by_url["https://www.example.com/"]["permissionLevel"], "full")
        self.assertTrue(all(item["dataReadable"] for item in result["sites"]))

    def test_unknown_and_unverified_entries_are_preserved_as_partial(self) -> None:
        result = normalize_sites({"siteEntry": [
            {"siteUrl": "sc-domain:example.com", "permissionLevel": "SITE_OWNER"},
            {"siteUrl": "https://unverified.example/", "permissionLevel": "siteUnverifiedUser"},
            {"siteUrl": "future-property:example", "permissionLevel": "FUTURE_ROLE"},
        ]})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["counts"], {"total": 3, "readable": 1, "unverified": 1, "unknown": 1})
        future = next(item for item in result["sites"] if item["siteUrl"] == "future-property:example")
        self.assertEqual(future["providerPermissionLevel"], "FUTURE_ROLE")
        self.assertIsNone(future["selectionKey"])
        self.assertFalse(future["dataReadable"])

    def test_empty_list_is_action_required_not_api_failure(self) -> None:
        result = normalize_sites({})
        self.assertEqual(result["status"], "action_required")
        self.assertEqual(result["counts"]["total"], 0)
        self.assertEqual(result["limitations"][0]["code"], "NO_SEARCH_CONSOLE_SITES_VISIBLE")

    def test_all_unverified_sites_are_visible_but_require_action(self) -> None:
        result = normalize_sites({"siteEntry": [
            {"siteUrl": "sc-domain:example.com", "permissionLevel": "siteUnverifiedUser"},
            {"siteUrl": "https://www.example.com/", "permissionLevel": "SITE_UNVERIFIED_USER"},
        ]})
        self.assertEqual(result["status"], "action_required")
        self.assertEqual(result["counts"], {"total": 2, "readable": 0, "unverified": 2, "unknown": 0})
        self.assertTrue(all(not item["dataReadable"] for item in result["sites"]))

    def test_missing_scope_stops_before_token_refresh_or_search_console_network(self) -> None:
        auth = FakeAuth(ready=False)
        with self.assertRaises(AdvisorError) as caught:
            SearchConsoleService(auth=auth).sites("profile-1")
        self.assertEqual(caught.exception.code, "SEARCH_CONSOLE_AUTHORIZATION_REQUIRED")
        self.assertFalse(caught.exception.details["networkRequestPerformed"])
        self.assertEqual(auth.access_calls, 0)
        self.assertEqual(auth.transport.calls, [])

    def test_discovery_uses_one_exact_read_only_operation_and_preserves_ledger(self) -> None:
        auth = FakeAuth(response={"siteEntry": [
            {"siteUrl": "sc-domain:example.com", "permissionLevel": "SITE_OWNER"}
        ]})
        result = SearchConsoleService(auth=auth).sites("profile-1")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(auth.transport.calls), 1)
        method, url, kwargs = auth.transport.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(url, "https://www.googleapis.com/webmasters/v3/sites")
        self.assertIsNone(kwargs["payload"])
        self.assertEqual(result["requestLedger"][0]["operationId"], "searchconsole.sites.list")
        self.assertTrue(result["networkUsed"])
        self.assertFalse(result["mutationPerformed"])
        self.assertNotIn("private-access-token", repr(result))

    def test_disabled_api_and_quota_have_search_console_specific_errors(self) -> None:
        cases = [
            (
                403,
                {"error": {"errors": [{"reason": "accessNotConfigured"}]}},
                "SEARCH_CONSOLE_API_DISABLED",
            ),
            (
                403,
                {"error": {"status": "PERMISSION_DENIED", "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}},
                "SEARCH_CONSOLE_SCOPE_MISSING",
            ),
            (
                429,
                {"error": {"errors": [{"reason": "rateLimitExceeded"}]}},
                "SEARCH_CONSOLE_QUOTA_LIMITED",
            ),
            (
                401,
                {"error": {"status": "UNAUTHENTICATED"}},
                "SEARCH_CONSOLE_TOKEN_INVALID",
            ),
            (
                403,
                {"error": {"status": "PERMISSION_DENIED"}},
                "SEARCH_CONSOLE_ACCESS_DENIED",
            ),
        ]
        for status, body, expected in cases:
            with self.subTest(expected=expected):
                error = AdvisorError(
                    "HTTP_ERROR", "failed", 5,
                    details={"status": status, "body": json.dumps(body)},
                )
                auth = FakeAuth(error=error)
                with self.assertRaises(AdvisorError) as caught:
                    SearchConsoleService(auth=auth).sites("profile-1")
                self.assertEqual(caught.exception.code, expected)

    def test_malformed_provider_response_fails_closed(self) -> None:
        for payload in ([], {"siteEntry": {}}, {"siteEntry": "bad"}):
            with self.subTest(payload=payload):
                with self.assertRaises(AdvisorError) as caught:
                    normalize_sites(payload)
                self.assertEqual(caught.exception.code, "SEARCH_CONSOLE_RESPONSE_INVALID")


if __name__ == "__main__":
    unittest.main()
