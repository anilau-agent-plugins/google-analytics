"""Shared URL validation and an explicit loopback-only safety mode for test acceptance."""

from __future__ import annotations

import ipaddress
import os
import urllib.parse

from .errors import AdvisorError, EXIT_NETWORK


NETWORK_POLICY_ENV = "GOOGLE_ANALYTICS_ADVISOR_NETWORK_POLICY"
LOOPBACK_ONLY = "loopback-only"


def validate_https_url(url: str) -> urllib.parse.SplitResult:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise AdvisorError("HTTP_URL_INVALID", "The request URL is invalid.", EXIT_NETWORK) from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise AdvisorError(
            "HTTP_URL_INVALID",
            "Production requests require a credential-free HTTPS URL on the standard port.",
            EXIT_NETWORK,
        )
    return parsed


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def enforce_network_policy(url: str, *, injected_transport: bool) -> None:
    """Block real network egress in acceptance runs while allowing injected fake transports."""
    if injected_transport or os.environ.get(NETWORK_POLICY_ENV) != LOOPBACK_ONLY:
        return
    parsed = urllib.parse.urlsplit(url)
    if not is_loopback_host(parsed.hostname):
        raise AdvisorError(
            "TEST_NETWORK_BLOCKED",
            "The validation environment blocked a non-loopback network request.",
            EXIT_NETWORK,
            details={"host": parsed.hostname or "unknown"},
            next_action="Inject a fake transport or use an isolated loopback fixture.",
        )
