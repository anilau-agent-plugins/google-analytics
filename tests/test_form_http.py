from __future__ import annotations

import io
import unittest
import urllib.error

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.form_http import FormTransport


class FormHttpTests(unittest.TestCase):
    def test_form_body_is_sent_once_and_not_exposed_by_error(self) -> None:
        calls = []
        secret = "1//unit-test-refresh-secret"

        def opener(request, **kwargs):
            calls.append(request.data)
            raise urllib.error.URLError("offline")

        with self.assertRaises(AdvisorError) as caught:
            FormTransport(opener=opener).post(
                "https://oauth2.googleapis.com/token",
                {"refresh_token": secret, "grant_type": "refresh_token"},
            )
        self.assertEqual(len(calls), 1)
        self.assertIn(b"refresh_token=1%2F%2Funit-test-refresh-secret", calls[0])
        self.assertNotIn(secret, str(caught.exception.as_dict()))
        self.assertEqual(caught.exception.code, "AUTH_ACTION_AMBIGUOUS")

    def test_google_error_is_classified_without_response_body(self) -> None:
        secret_body = b'{"error":"invalid_grant","error_description":"sensitive detail"}'

        def opener(request, **kwargs):
            raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, io.BytesIO(secret_body))

        with self.assertRaises(AdvisorError) as caught:
            FormTransport(opener=opener).post("https://oauth2.googleapis.com/token", {"code": "secret-code"})
        details = caught.exception.details
        self.assertEqual(details, {"status": 400, "googleError": "invalid_grant"})
        self.assertNotIn("sensitive detail", str(caught.exception.as_dict()))


if __name__ == "__main__":
    unittest.main()
