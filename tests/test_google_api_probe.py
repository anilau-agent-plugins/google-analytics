from __future__ import annotations

import json
import unittest

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.google_api_probe import run_probes


class Response:
    def __init__(self, data): self.data = data


class Transport:
    def request(self, method, url, **kwargs):
        if "userinfo" in url:
            return Response({"sub": "s", "email": "u@example.com", "email_verified": True})
        if "analyticsadmin" in url:
            return Response({"accountSummaries": [{"propertySummaries": [{"property": "properties/123"}]}]})
        if "tagmanager" in url:
            return Response({"account": [{"accountId": "1"}]})
        if "analyticsdata" in url:
            return Response({"dimensions": []})
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


if __name__ == "__main__":
    unittest.main()
