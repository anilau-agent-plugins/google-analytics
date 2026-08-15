from __future__ import annotations

import io
import json
import unittest
import urllib.error

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.http import JsonTransport


class Response:
    def __init__(self, body=b"{}", status=200, headers=None):
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = headers or {}

    def read(self, size=-1):
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class SequenceOpener:
    def __init__(self, items):
        self.items = list(items)
        self.calls = 0

    def __call__(self, request, **kwargs):
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class HttpTests(unittest.TestCase):
    def test_json_response_and_request_id(self) -> None:
        opener = SequenceOpener([Response(b'{"ok":true}', headers={"x-request-id": "abc"})])
        result = JsonTransport(opener=opener).request("GET", "https://example.test")
        self.assertTrue(result.data["ok"])
        self.assertEqual(result.request_id, "abc")

    def test_safe_retry(self) -> None:
        error = urllib.error.HTTPError("https://example.test", 503, "busy", {"Retry-After": "0"}, io.BytesIO(b"busy"))
        opener = SequenceOpener([error, Response(b"{}")])
        result = JsonTransport(opener=opener, sleep=lambda _: None).request("GET", "https://example.test")
        self.assertEqual(result.status, 200)
        self.assertEqual(opener.calls, 2)

    def test_mutation_is_not_retried(self) -> None:
        opener = SequenceOpener([urllib.error.URLError("lost")])
        with self.assertRaises(AdvisorError) as caught:
            JsonTransport(opener=opener).request("POST", "https://example.test", payload={})
        self.assertEqual(caught.exception.code, "AMBIGUOUS_NETWORK_FAILURE")
        self.assertEqual(opener.calls, 1)

    def test_response_limit(self) -> None:
        with self.assertRaises(AdvisorError):
            JsonTransport(opener=SequenceOpener([Response(b"12345")]), max_response_bytes=4).request("GET", "https://example.test")

    def test_malformed_json_is_not_retried(self) -> None:
        opener = SequenceOpener([Response(b"not-json"), Response(b"{}")])
        with self.assertRaises(AdvisorError) as caught:
            JsonTransport(opener=opener, sleep=lambda _: None).request("GET", "https://example.test")
        self.assertEqual(caught.exception.code, "MALFORMED_HTTP_RESPONSE")
        self.assertEqual(opener.calls, 1)


if __name__ == "__main__":
    unittest.main()
