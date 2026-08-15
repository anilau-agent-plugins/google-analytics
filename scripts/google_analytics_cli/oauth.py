"""Google Desktop OAuth with PKCE S256 and a one-shot loopback receiver."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

from .errors import AdvisorError, EXIT_CONFIGURATION, EXIT_INPUT, EXIT_NETWORK
from .form_http import FormTransport
from .http import JsonTransport


AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/analytics.edit",
    "https://www.googleapis.com/auth/tagmanager.readonly",
    "https://www.googleapis.com/auth/tagmanager.edit.containers",
    "https://www.googleapis.com/auth/tagmanager.edit.containerversions",
    "https://www.googleapis.com/auth/tagmanager.publish",
)

_GRANTED_SCOPE_ALIASES = {
    "https://www.googleapis.com/auth/userinfo.email": "email",
}

SCOPE_GROUPS = (
    {"group": "identity", "purpose": "Identify the connected Google account and email."},
    {"group": "analytics_read", "purpose": "Read GA4 settings and reports in later stages."},
    {"group": "analytics_edit", "purpose": "Change only separately approved GA4 settings in later stages."},
    {"group": "gtm", "purpose": "Read and edit GTM, create versions, and publish only after separate confirmation."},
)


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorization_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    return f"{AUTHORIZATION_ENDPOINT}?{query}"


def _single(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if values is None:
        return None
    if len(values) != 1 or not values[0]:
        raise AdvisorError("OAUTH_CALLBACK_INVALID", "The OAuth callback contains duplicate or empty parameters.", EXIT_INPUT)
    return values[0]


def parse_callback(target: str, expected_state: str) -> str:
    parsed = urllib.parse.urlsplit(target)
    if parsed.path != "/oauth2/callback":
        raise AdvisorError("OAUTH_CALLBACK_INVALID", "The OAuth callback path is invalid.", EXIT_INPUT)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    returned_state = _single(query, "state")
    if returned_state is None or not hmac.compare_digest(returned_state, expected_state):
        raise AdvisorError("OAUTH_STATE_MISMATCH", "The OAuth callback state did not match.", EXIT_INPUT)
    error = _single(query, "error")
    if error:
        code = "OAUTH_ACCESS_DENIED" if error == "access_denied" else "OAUTH_CALLBACK_ERROR"
        raise AdvisorError(code, "Google authorization was not completed.", EXIT_INPUT, details={"googleError": error})
    code = _single(query, "code")
    if not code:
        raise AdvisorError("OAUTH_CALLBACK_INVALID", "The OAuth callback did not contain an authorization code.", EXIT_INPUT)
    return code


class _LoopbackHandler(BaseHTTPRequestHandler):
    server_version = "GoogleAnalyticsAdvisorLoopback/1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        server = self.server
        try:
            server.oauth_code = parse_callback(self.path, server.expected_state)  # type: ignore[attr-defined]
            body = "Authorization completed. You can close this page and return to the agent."
            status = 200
        except AdvisorError as exc:
            server.oauth_error = exc  # type: ignore[attr-defined]
            body = "Authorization could not be completed. Return to the agent for a safe explanation."
            status = 400
        encoded = ("<!doctype html><meta charset=utf-8><title>Google Analytics Advisor</title><p>" + html.escape(body) + "</p>").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _validate_initial_tokens(payload: dict[str, Any]) -> dict[str, Any]:
    token_type = payload.get("token_type")
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    granted = set(str(payload.get("scope", "")).split())
    granted.update(_GRANTED_SCOPE_ALIASES.get(scope, scope) for scope in tuple(granted))
    if token_type != "Bearer" or not isinstance(access_token, str) or not access_token:
        raise AdvisorError("OAUTH_TOKEN_INVALID", "Google returned an invalid access token response.", EXIT_NETWORK)
    missing = [scope for scope in SCOPES if scope not in granted]
    if missing:
        raise AdvisorError(
            "OAUTH_SCOPE_INCOMPLETE",
            "Google authorization did not grant the complete V1 permission set.",
            EXIT_CONFIGURATION,
            details={"missingScopes": missing},
            next_action="Review the requested permission groups and authorize the complete set in one new consent flow.",
        )
    if not isinstance(refresh_token, str) or not refresh_token:
        raise AdvisorError(
            "OAUTH_REFRESH_TOKEN_MISSING",
            "Google did not return durable offline access.",
            EXIT_CONFIGURATION,
            next_action="Start a new explicit consent flow; do not paste tokens into chat.",
        )
    return payload


def authorize(
    client: dict[str, str], *, form: FormTransport | None = None, json_transport: JsonTransport | None = None,
    browser_open: Callable[[str], bool] = webbrowser.open, timeout: float = 300.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(32)
    server = HTTPServer(("127.0.0.1", 0), _LoopbackHandler)
    server.timeout = timeout
    server.expected_state = state  # type: ignore[attr-defined]
    server.oauth_code = None  # type: ignore[attr-defined]
    server.oauth_error = None  # type: ignore[attr-defined]
    redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth2/callback"
    url = authorization_url(client["client_id"], redirect_uri, state, challenge)
    try:
        if not browser_open(url):
            raise AdvisorError(
                "OAUTH_BROWSER_OPEN_FAILED",
                "The system browser could not be opened for Google authorization.",
                EXIT_CONFIGURATION,
                next_action="Check the default browser configuration and start authorization again.",
            )
        started = time.monotonic()
        server.handle_request()
        if time.monotonic() - started >= timeout and server.oauth_code is None:  # type: ignore[attr-defined]
            raise AdvisorError("OAUTH_CALLBACK_TIMEOUT", "Google authorization timed out.", EXIT_NETWORK)
        if server.oauth_error is not None:  # type: ignore[attr-defined]
            raise server.oauth_error  # type: ignore[misc]
        code = server.oauth_code  # type: ignore[attr-defined]
        if not code:
            raise AdvisorError("OAUTH_CALLBACK_TIMEOUT", "Google authorization did not return a callback.", EXIT_NETWORK)
    finally:
        server.server_close()
    tokens = (form or FormTransport()).post(
        TOKEN_ENDPOINT,
        {
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    _validate_initial_tokens(tokens)
    identity_response = (json_transport or JsonTransport(timeout=15.0)).request(
        "GET", USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {tokens['access_token']}"}, max_attempts=1
    )
    identity = identity_response.data
    if not isinstance(identity, dict) or not identity.get("sub") or not identity.get("email"):
        raise AdvisorError("OAUTH_IDENTITY_INVALID", "Google did not return the expected account identity.", EXIT_NETWORK)
    return tokens, identity


def refresh(client: dict[str, str], refresh_token: str, *, form: FormTransport | None = None) -> dict[str, Any]:
    try:
        payload = (form or FormTransport()).post(
            TOKEN_ENDPOINT,
            {
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    except AdvisorError as exc:
        if exc.details.get("googleError") == "invalid_grant":
            raise AdvisorError(
                "OAUTH_TOKEN_INVALID_GRANT",
                "The Google authorization is expired, revoked, or otherwise no longer valid.",
                EXIT_CONFIGURATION,
                next_action="Start a new browser authorization for this profile.",
            ) from exc
        raise
    if payload.get("token_type") != "Bearer" or not isinstance(payload.get("access_token"), str):
        raise AdvisorError("OAUTH_TOKEN_INVALID", "Google returned an invalid refresh response.", EXIT_NETWORK)
    return payload


def revoke(refresh_token: str, *, form: FormTransport | None = None) -> None:
    (form or FormTransport()).post(REVOCATION_ENDPOINT, {"token": refresh_token})
