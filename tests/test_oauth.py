from __future__ import annotations

import threading
import unittest
import urllib.parse
import urllib.request

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.oauth import SCOPES, authorization_url, authorize, generate_pkce, parse_callback


class Form:
    def __init__(self):
        self.fields = None

    def post(self, url, fields):
        self.fields = fields
        return {
            "access_token": "ya29.test-access",
            "refresh_token": "1//test-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": " ".join(SCOPES),
        }


class JsonResponse:
    data = {"sub": "subject-1", "email": "user@example.com", "email_verified": True}


class JsonTransport:
    def request(self, *args, **kwargs):
        return JsonResponse()


class OAuthTests(unittest.TestCase):
    def test_pkce_and_authorization_url(self) -> None:
        verifier, challenge = generate_pkce()
        self.assertGreaterEqual(len(verifier), 43)
        self.assertNotIn("=", challenge)
        url = authorization_url("123.apps.googleusercontent.com", "http://127.0.0.1:1/oauth2/callback", "state", challenge)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["scope"][0].split(), list(SCOPES))

    def test_callback_validation(self) -> None:
        self.assertEqual(parse_callback("/oauth2/callback?state=good&code=abc", "good"), "abc")
        with self.assertRaises(AdvisorError) as wrong:
            parse_callback("/oauth2/callback?state=bad&code=abc", "good")
        self.assertEqual(wrong.exception.code, "OAUTH_STATE_MISMATCH")
        with self.assertRaises(AdvisorError):
            parse_callback("/oauth2/callback?state=good&code=a&code=b", "good")
        with self.assertRaises(AdvisorError) as denied:
            parse_callback("/oauth2/callback?state=good&error=access_denied", "good")
        self.assertEqual(denied.exception.code, "OAUTH_ACCESS_DENIED")

    def test_real_loopback_with_injected_google_transports(self) -> None:
        form = Form()
        threads = []

        def browser_open(url):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            callback = f"{query['redirect_uri'][0]}?state={urllib.parse.quote(query['state'][0])}&code=test-code"
            thread = threading.Thread(target=lambda: urllib.request.urlopen(callback, timeout=5).read(), daemon=True)
            threads.append(thread)
            thread.start()
            return True

        tokens, identity = authorize(
            {"client_id": "123.apps.googleusercontent.com", "client_secret": "GOCSPX-test", "project_id": "p"},
            form=form, json_transport=JsonTransport(), browser_open=browser_open, timeout=5,
        )
        for thread in threads:
            thread.join(5)
        self.assertEqual(tokens["refresh_token"], "1//test-refresh")
        self.assertEqual(identity["email"], "user@example.com")
        self.assertEqual(form.fields["code"], "test-code")
        self.assertNotEqual(form.fields["code_verifier"], "")

    def test_incomplete_scope_fails_closed(self) -> None:
        class IncompleteForm(Form):
            def post(self, url, fields):
                value = super().post(url, fields)
                value["scope"] = "openid email"
                return value

        def browser_open(url):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            callback = f"{query['redirect_uri'][0]}?state={query['state'][0]}&code=test"
            threading.Thread(target=lambda: urllib.request.urlopen(callback, timeout=5).read(), daemon=True).start()
            return True

        with self.assertRaises(AdvisorError) as caught:
            authorize(
                {"client_id": "123.apps.googleusercontent.com", "client_secret": "x", "project_id": "p"},
                form=IncompleteForm(), json_transport=JsonTransport(), browser_open=browser_open, timeout=5,
            )
        self.assertEqual(caught.exception.code, "OAUTH_SCOPE_INCOMPLETE")

    def test_google_canonical_email_scope_satisfies_email_alias(self) -> None:
        class CanonicalEmailForm(Form):
            def post(self, url, fields):
                value = super().post(url, fields)
                value["scope"] = " ".join(
                    "https://www.googleapis.com/auth/userinfo.email" if scope == "email" else scope
                    for scope in SCOPES
                )
                return value

        def browser_open(url):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            callback = f"{query['redirect_uri'][0]}?state={query['state'][0]}&code=test"
            threading.Thread(target=lambda: urllib.request.urlopen(callback, timeout=5).read(), daemon=True).start()
            return True

        tokens, _identity = authorize(
            {"client_id": "123.apps.googleusercontent.com", "client_secret": "x", "project_id": "p"},
            form=CanonicalEmailForm(), json_transport=JsonTransport(), browser_open=browser_open, timeout=5,
        )
        self.assertIn("https://www.googleapis.com/auth/userinfo.email", tokens["scope"])

    def test_missing_refresh_token_fails_closed(self) -> None:
        class MissingRefreshForm(Form):
            def post(self, url, fields):
                value = super().post(url, fields)
                value.pop("refresh_token")
                return value

        def browser_open(url):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            callback = f"{query['redirect_uri'][0]}?state={query['state'][0]}&code=test"
            threading.Thread(target=lambda: urllib.request.urlopen(callback, timeout=5).read(), daemon=True).start()
            return True

        with self.assertRaises(AdvisorError) as caught:
            authorize(
                {"client_id": "123.apps.googleusercontent.com", "client_secret": "x", "project_id": "p"},
                form=MissingRefreshForm(), json_transport=JsonTransport(), browser_open=browser_open, timeout=5,
            )
        self.assertEqual(caught.exception.code, "OAUTH_REFRESH_TOKEN_MISSING")

    def test_browser_failure_does_not_start_token_exchange(self) -> None:
        form = Form()
        with self.assertRaises(AdvisorError) as caught:
            authorize(
                {"client_id": "123.apps.googleusercontent.com", "client_secret": "x", "project_id": "p"},
                form=form, json_transport=JsonTransport(), browser_open=lambda _: False, timeout=0.01,
            )
        self.assertEqual(caught.exception.code, "OAUTH_BROWSER_OPEN_FAILED")
        self.assertIsNone(form.fields)


if __name__ == "__main__":
    unittest.main()
