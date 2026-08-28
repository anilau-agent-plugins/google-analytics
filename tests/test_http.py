from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.http import JsonTransport, _SafeRedirectHandler


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

    def test_url_validation_rejects_plaintext_credentials_fragments_and_ports(self) -> None:
        for url in (
            "http://example.test/data",
            "https://user:password@example.test/data",
            "https://example.test:8443/data",
            "https://example.test/data#fragment",
        ):
            with self.subTest(url=url), self.assertRaises(AdvisorError) as caught:
                JsonTransport(opener=SequenceOpener([Response()])).request("GET", url)
            self.assertEqual(caught.exception.code, "HTTP_URL_INVALID")

    def test_retry_attempts_are_bounded(self) -> None:
        for attempts in (0, 6, True):
            with self.subTest(attempts=attempts), self.assertRaises(AdvisorError) as caught:
                JsonTransport(opener=SequenceOpener([Response()])).request(
                    "GET", "https://example.test", max_attempts=attempts
                )
            self.assertEqual(caught.exception.code, "UNSAFE_RETRY_POLICY")

    def test_non_json_content_type_is_rejected_without_retry(self) -> None:
        opener = SequenceOpener([Response(b"<html>error</html>", headers={"Content-Type": "text/html"})])
        with self.assertRaises(AdvisorError) as caught:
            JsonTransport(opener=opener).request("GET", "https://example.test")
        self.assertEqual(caught.exception.code, "MALFORMED_HTTP_RESPONSE")
        self.assertEqual(opener.calls, 1)

    def test_cross_host_redirect_strips_authorization(self) -> None:
        original = urllib.request.Request(
            "https://analyticsdata.googleapis.com/source",
            headers={"Authorization": "Bearer private-access-token"},
        )
        redirected = _SafeRedirectHandler().redirect_request(
            original, None, 302, "Found", {}, "https://example.test/target"
        )
        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Authorization"))

    def test_loopback_only_policy_blocks_default_transport_before_network(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_ANALYTICS_ADVISOR_NETWORK_POLICY": "loopback-only"}):
            with self.assertRaises(AdvisorError) as caught:
                JsonTransport(timeout=0.01).request(
                    "GET", "https://analyticsdata.googleapis.com/v1beta/properties/1/metadata",
                    max_attempts=1,
                )
        self.assertEqual(caught.exception.code, "TEST_NETWORK_BLOCKED")


if __name__ == "__main__":
    unittest.main()
