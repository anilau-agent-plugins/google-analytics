from __future__ import annotations

import json
import unittest

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.google_api_probe import run_probes


class Response:
    def __init__(self, data): self.data = data


class Transport:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if "userinfo" in url:
            return Response({"sub": "s", "email": "u@example.com", "email_verified": True})
        if "analyticsadmin" in url:
            return Response({"accountSummaries": [{"propertySummaries": [{"property": "properties/123"}]}]})
        if "tagmanager" in url:
            return Response({"account": [{"accountId": "1"}]})
        if "analyticsdata" in url:
            return Response({"dimensions": []})
        if "/webmasters/v3/sites" in url:
            return Response({"siteEntry": [{"siteUrl": "sc-domain:example.com"}]})
        raise AssertionError(url)


class DisabledTransport(Transport):
    def request(self, method, url, **kwargs):
        if "tagmanager" in url:
            body = json.dumps({"error": {"details": [{"reason": "SERVICE_DISABLED"}]}})
            raise AdvisorError("HTTP_ERROR", "failed", 5, details={"status": 403, "body": body})
        return super().request(method, url, **kwargs)


class ScopeTransport(Transport):
    def request(self, method, url, **kwargs):
        if "analyticsadmin" in url:
            body = json.dumps({"error": {"details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}})
            raise AdvisorError("HTTP_ERROR", "failed", 5, details={"status": 403, "body": body})
        return super().request(method, url, **kwargs)


class ProbeTests(unittest.TestCase):
    def test_ready_probes_are_read_only(self) -> None:
        result = run_probes("access", transport=Transport())
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["readOnly"])
        self.assertTrue(result["analyticsData"]["resourceAvailable"])

    def test_disabled_api_is_distinct(self) -> None:
        result = run_probes("access", transport=DisabledTransport())
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["tagManager"]["status"], "api_disabled")

    def test_missing_scope_is_distinct_from_resource_access(self) -> None:
        result = run_probes("access", transport=ScopeTransport())
        self.assertEqual(result["analyticsAdmin"]["status"], "scope_missing")

    def test_search_console_probe_is_conditional_and_read_only(self) -> None:
        transport = Transport()
        missing = run_probes("access", transport=transport, search_console_enabled=False)
        self.assertEqual(missing["searchConsole"]["status"], "authorization_required")
        self.assertFalse(missing["searchConsole"]["networkRequestPerformed"])

        ready = run_probes("access", transport=transport, search_console_enabled=True)
        self.assertEqual(ready["searchConsole"]["status"], "ready")
        self.assertTrue(ready["searchConsole"]["resourceAvailable"])
        self.assertTrue(ready["searchConsole"]["networkRequestPerformed"])
        search_console_calls = [call for call in transport.calls if "/webmasters/" in call[1]]
        self.assertEqual(len(search_console_calls), 1)
        self.assertEqual(search_console_calls[0][0], "GET")
        self.assertEqual(search_console_calls[0][1], "https://www.googleapis.com/webmasters/v3/sites")


if __name__ == "__main__":
    unittest.main()
