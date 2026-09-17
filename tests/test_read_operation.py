from __future__ import annotations

import io
import unittest
import urllib.error

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.http import JsonResponse, JsonTransport
from scripts.google_analytics_cli.pagination import collect_offsets, collect_page_tokens
from scripts.google_analytics_cli.read_operation import OPERATIONS, ReadExecutor


class CapturingTransport:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return JsonResponse(200, {}, None, {})


class ReadOperationTests(unittest.TestCase):
    def test_registry_has_only_get_and_allowlisted_safe_posts(self) -> None:
        posts = [item.operation_id for item in OPERATIONS.values() if item.method == "POST"]
        self.assertEqual(posts, [
            "data.compatibility.check", "data.report.run", "data.report.batch",
            "data.report.realtime", "data.report.funnel", "searchconsole.searchanalytics.query",
        ])
        self.assertTrue(all(item.method in {"GET", "POST"} for item in OPERATIONS.values()))
        self.assertNotIn("secret", " ".join(OPERATIONS).lower())

    def test_executor_builds_only_registered_request_and_ledger_omits_token(self) -> None:
        transport = CapturingTransport()
        executor = ReadExecutor("private-access-token", transport=transport)
        executor.execute("data.report.run", resource="properties/123", payload={"limit": "1"})
        method, url, kwargs = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://analyticsdata.googleapis.com/v1beta/properties/123:runReport")
        self.assertEqual(kwargs["retry_mode"], "allowlisted-read")
        self.assertNotIn("private-access-token", repr(executor.ledger))

    def test_unknown_and_malformed_resources_fail_before_network(self) -> None:
        transport = CapturingTransport()
        executor = ReadExecutor("x", transport=transport)
        for operation, resource in (("admin.delete", None), ("admin.property.get", "../../secret")):
            with self.subTest(operation=operation):
                with self.assertRaises(AdvisorError):
                    executor.execute(operation, resource=resource)
        self.assertEqual(transport.calls, [])

    def test_search_console_registry_exposes_only_read_operations(self) -> None:
        operations = [name for name in OPERATIONS if name.startswith("searchconsole.")]
        self.assertEqual(operations, ["searchconsole.sites.list", "searchconsole.searchanalytics.query"])
        operation = OPERATIONS[operations[0]]
        self.assertEqual(operation.method, "GET")
        self.assertEqual(operation.base_url + operation.path_template, "https://www.googleapis.com/webmasters/v3/sites")
        query = OPERATIONS[operations[1]]
        self.assertEqual(query.method, "POST")
        self.assertTrue(query.safe_post)
        self.assertEqual(query.max_attempts, 1)

    def test_search_analytics_site_is_encoded_and_never_retried(self) -> None:
        transport = CapturingTransport()
        executor = ReadExecutor("private", transport=transport)
        executor.execute(
            "searchconsole.searchanalytics.query", resource="https://www.example.com/path/",
            payload={"startDate": "2026-08-01", "endDate": "2026-08-02", "rowLimit": 1},
        )
        method, url, kwargs = transport.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(
            url,
            "https://searchconsole.googleapis.com/webmasters/v3/sites/https%3A%2F%2Fwww.example.com%2Fpath%2F/searchAnalytics/query",
        )
        self.assertEqual(kwargs["max_attempts"], 1)
        self.assertEqual(kwargs["retry_mode"], "allowlisted-read")

    def test_allowlisted_post_retries_but_arbitrary_post_does_not(self) -> None:
        class Response:
            status = 200
            headers = {}
            def read(self, size=-1): return b"{}"
            def __enter__(self): return self
            def __exit__(self, *args): return False
        class Opener:
            def __init__(self): self.calls = 0
            def __call__(self, request, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise urllib.error.HTTPError(request.full_url, 503, "busy", {"Retry-After": "0"}, io.BytesIO(b"busy"))
                return Response()
        opener = Opener()
        transport = JsonTransport(opener=opener, sleep=lambda _: None)
        result = transport.request("POST", "https://example.test", payload={}, max_attempts=3, retry_mode="allowlisted-read")
        self.assertEqual(result.status, 200)
        with self.assertRaises(AdvisorError) as caught:
            transport.request("POST", "https://example.test", payload={}, max_attempts=2)
        self.assertEqual(caught.exception.code, "UNSAFE_RETRY_POLICY")

    def test_bounded_pagination_detects_cycles_and_offsets(self) -> None:
        pages = {None: {"things": [1], "nextPageToken": "a"}, "a": {"things": [2], "nextPageToken": "a"}}
        result = collect_page_tokens(lambda token: pages[token], "things", max_pages=10, max_items=10)
        self.assertEqual(result["items"], [1, 2])
        self.assertTrue(result["truncated"])
        report = collect_offsets(lambda offset, limit: {"rows": list(range(offset, offset + limit)), "rowCount": 2000})
        self.assertEqual(len(report["rows"]), 1000)
        self.assertTrue(report["truncated"])


if __name__ == "__main__":
    unittest.main()
