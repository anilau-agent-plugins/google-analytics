"""Dependency-free bounded JSON-over-HTTPS transport."""

from __future__ import annotations

import email.utils
import json
import random
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .errors import AdvisorError, EXIT_NETWORK
from .output import redact


RETRYABLE = {429, 500, 502, 503, 504}


@dataclass
class JsonResponse:
    status: int
    data: Any
    request_id: str | None
    headers: dict[str, str]


class JsonTransport:
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_response_bytes: int = 4 * 1024 * 1024,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_value: Callable[[], float] = random.random,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep
        self.random_value = random_value
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.ssl_context.check_hostname = True
        self.ssl_context.verify_mode = ssl.CERT_REQUIRED

    def _read(self, response: Any) -> bytes:
        body = response.read(self.max_response_bytes + 1)
        if len(body) > self.max_response_bytes:
            raise AdvisorError("HTTP_RESPONSE_TOO_LARGE", "HTTP response exceeded the size limit.", EXIT_NETWORK)
        return body

    @staticmethod
    def _request_id(headers: Any) -> str | None:
        for key in ("x-request-id", "x-guploader-uploadid", "request-id"):
            value = headers.get(key) if headers else None
            if value:
                return str(value)[:256]
        return None

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                parsed = email.utils.parsedate_to_datetime(value)
                return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                return None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: Any = None,
        max_attempts: int | None = None,
    ) -> JsonResponse:
        normalized = method.upper()
        safe_read = normalized in {"GET", "HEAD"}
        attempts = max_attempts if max_attempts is not None else (3 if safe_read else 1)
        if attempts > 1 and not safe_read:
            raise AdvisorError("UNSAFE_RETRY_POLICY", "Retries are disabled for mutation requests.", EXIT_NETWORK)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            request_headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(url, data=body, headers=request_headers, method=normalized)
        for attempt in range(1, attempts + 1):
            try:
                with self.opener(request, timeout=self.timeout, context=self.ssl_context) as response:
                    raw = self._read(response)
                    data = None if not raw else json.loads(raw.decode("utf-8"))
                    response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                    return JsonResponse(response.status, data, self._request_id(response.headers), response_headers)
            except urllib.error.HTTPError as exc:
                raw = exc.read(min(self.max_response_bytes, 8192))
                retry = safe_read and exc.code in RETRYABLE and attempt < attempts
                if retry:
                    delay = self._retry_after(exc.headers.get("Retry-After"))
                    self.sleep(delay if delay is not None else min(8.0, (2 ** (attempt - 1)) + self.random_value()))
                    continue
                raise AdvisorError(
                    "HTTP_ERROR",
                    f"HTTP request failed with status {exc.code}.",
                    EXIT_NETWORK,
                    retryable=exc.code in RETRYABLE,
                    details=redact({"status": exc.code, "requestId": self._request_id(exc.headers), "body": raw.decode("utf-8", "replace")[:2048]}),
                ) from exc
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                if safe_read and attempt < attempts:
                    self.sleep(min(8.0, (2 ** (attempt - 1)) + self.random_value()))
                    continue
                raise AdvisorError(
                    "AMBIGUOUS_NETWORK_FAILURE" if not safe_read else "NETWORK_FAILURE",
                    "The request outcome is ambiguous." if not safe_read else "The network request failed.",
                    EXIT_NETWORK,
                    retryable=safe_read,
                    details={"reason": type(exc).__name__, "ambiguous": not safe_read},
                    next_action="Read back the current remote state before retrying." if not safe_read else "Check connectivity and retry.",
                ) from exc
        raise AssertionError("unreachable")
