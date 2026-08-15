"""Bounded form-encoded HTTPS transport for OAuth endpoints."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .errors import AdvisorError, EXIT_NETWORK


class FormTransport:
    def __init__(
        self, *, timeout: float = 30.0, max_response_bytes: int = 256 * 1024,
        opener: Callable[..., Any] | None = None, ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.opener = opener or urllib.request.urlopen
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.ssl_context.check_hostname = True
        self.ssl_context.verify_mode = ssl.CERT_REQUIRED

    def post(self, url: str, fields: dict[str, str]) -> dict[str, Any]:
        if not url.startswith("https://"):
            raise AdvisorError("OAUTH_ENDPOINT_INVALID", "OAuth endpoints must use HTTPS.", EXIT_NETWORK)
        body = urllib.parse.urlencode(fields).encode("ascii")
        request = urllib.request.Request(
            url, data=body,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with self.opener(request, timeout=self.timeout, context=self.ssl_context) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise AdvisorError("HTTP_RESPONSE_TOO_LARGE", "OAuth response exceeded the size limit.", EXIT_NETWORK)
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
                if not isinstance(parsed, dict):
                    raise ValueError("OAuth response is not an object")
                return parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read(min(self.max_response_bytes, 8192))
            google_error = None
            try:
                parsed = json.loads(raw.decode("utf-8"))
                if isinstance(parsed, dict):
                    google_error = parsed.get("error")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise AdvisorError(
                "OAUTH_HTTP_ERROR",
                "Google rejected the OAuth request.",
                EXIT_NETWORK,
                details={"status": exc.code, "googleError": google_error},
            ) from exc
        except AdvisorError:
            raise
        except (OSError, urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise AdvisorError(
                "AUTH_ACTION_AMBIGUOUS",
                "The OAuth request did not return a definite result.",
                EXIT_NETWORK,
                retryable=False,
                details={"reason": type(exc).__name__},
                next_action="Do not repeat automatically. Check authorization status and start a new authorization if needed.",
            ) from exc
