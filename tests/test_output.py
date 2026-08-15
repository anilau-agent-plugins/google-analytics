from __future__ import annotations

import unittest

from scripts.google_analytics_cli.output import envelope, redact


class OutputTests(unittest.TestCase):
    def test_recursive_redaction(self) -> None:
        token = "ya" + "29.exposed"
        value = {"nested": [{"refreshToken": "hidden"}], "message": "value " + token}
        result = redact(value)
        self.assertEqual(result["nested"][0]["refreshToken"], "[REDACTED]")
        self.assertNotIn("ya29", result["message"])

    def test_envelope_shape(self) -> None:
        result = envelope("version", ok=True, status="ready")
        self.assertEqual(set(result), {"schemaVersion", "cliVersion", "ok", "command", "status", "data", "warnings", "errors"})

    def test_oauth_transient_fields_are_redacted_but_error_codes_remain(self) -> None:
        result = redact({
            "authorizationCode": "code-value",
            "codeVerifier": "verifier-value",
            "oauthState": "state-value",
            "code": "OAUTH_ACCESS_DENIED",
        })
        self.assertEqual(result["authorizationCode"], "[REDACTED]")
        self.assertEqual(result["codeVerifier"], "[REDACTED]")
        self.assertEqual(result["oauthState"], "[REDACTED]")
        self.assertEqual(result["code"], "OAUTH_ACCESS_DENIED")


if __name__ == "__main__":
    unittest.main()
