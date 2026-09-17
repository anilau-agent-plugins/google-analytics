"""Fail-closed registry and executor for Stage 5 Google read operations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from .errors import AdvisorError, EXIT_INPUT, EXIT_NETWORK
from .http import JsonResponse, JsonTransport


@dataclass(frozen=True)
class ReadOperation:
    operation_id: str
    method: str
    base_url: str
    path_template: str
    safe_post: bool = False
    resource_kind: str = "google-resource"
    max_attempts: int = 3


def _op(
    operation_id: str, method: str, base: str, path: str, safe_post: bool = False,
    resource_kind: str = "google-resource", max_attempts: int = 3,
) -> ReadOperation:
    return ReadOperation(operation_id, method, base, path, safe_post, resource_kind, max_attempts)


ADMIN = "https://analyticsadmin.googleapis.com"
DATA = "https://analyticsdata.googleapis.com"
GTM = "https://tagmanager.googleapis.com"
SEARCH_CONSOLE = "https://www.googleapis.com"
SEARCH_CONSOLE_API = "https://searchconsole.googleapis.com"

OPERATIONS = {
    item.operation_id: item for item in (
        _op("admin.account_summaries.list", "GET", ADMIN, "/v1beta/accountSummaries"),
        _op("admin.property.get", "GET", ADMIN, "/v1beta/{resource}"),
        _op("admin.stream.get", "GET", ADMIN, "/v1beta/{resource}"),
        _op("admin.streams.list", "GET", ADMIN, "/v1beta/{resource}/dataStreams"),
        _op("admin.key_events.list", "GET", ADMIN, "/v1beta/{resource}/keyEvents"),
        _op("admin.custom_dimensions.list", "GET", ADMIN, "/v1beta/{resource}/customDimensions"),
        _op("admin.custom_metrics.list", "GET", ADMIN, "/v1beta/{resource}/customMetrics"),
        _op("admin.retention.get", "GET", ADMIN, "/v1beta/{resource}/dataRetentionSettings"),
        _op("admin.enhanced_measurement.get", "GET", ADMIN, "/v1alpha/{resource}/enhancedMeasurementSettings"),
        _op("admin.data_redaction.get", "GET", ADMIN, "/v1alpha/{resource}/dataRedactionSettings"),
        _op("data.metadata.get", "GET", DATA, "/v1beta/{resource}/metadata"),
        _op("data.compatibility.check", "POST", DATA, "/v1beta/{resource}:checkCompatibility", True),
        _op("data.report.run", "POST", DATA, "/v1beta/{resource}:runReport", True),
        _op("data.report.batch", "POST", DATA, "/v1beta/{resource}:batchRunReports", True),
        _op("data.report.realtime", "POST", DATA, "/v1beta/{resource}:runRealtimeReport", True),
        _op("data.report.funnel", "POST", DATA, "/v1alpha/{resource}:runFunnelReport", True),
        _op("gtm.accounts.list", "GET", GTM, "/tagmanager/v2/accounts"),
        _op("gtm.containers.list", "GET", GTM, "/tagmanager/v2/{resource}/containers"),
        _op("gtm.workspaces.list", "GET", GTM, "/tagmanager/v2/{resource}/workspaces"),
        _op("gtm.workspace.status", "GET", GTM, "/tagmanager/v2/{resource}/status"),
        _op("gtm.tags.list", "GET", GTM, "/tagmanager/v2/{resource}/tags"),
        _op("gtm.triggers.list", "GET", GTM, "/tagmanager/v2/{resource}/triggers"),
        _op("gtm.variables.list", "GET", GTM, "/tagmanager/v2/{resource}/variables"),
        _op("gtm.built_in_variables.list", "GET", GTM, "/tagmanager/v2/{resource}/built_in_variables"),
        _op("gtm.gtag_config.list", "GET", GTM, "/tagmanager/v2/{resource}/gtag_config"),
        _op("gtm.live_version.get", "GET", GTM, "/tagmanager/v2/{resource}/versions/live"),
        _op("gtm.version_headers.list", "GET", GTM, "/tagmanager/v2/{resource}/version_headers"),
        _op("searchconsole.sites.list", "GET", SEARCH_CONSOLE, "/webmasters/v3/sites"),
        _op(
            "searchconsole.searchanalytics.query", "POST", SEARCH_CONSOLE_API,
            "/webmasters/v3/sites/{site_url}/searchAnalytics/query", True,
            resource_kind="search-console-site", max_attempts=1,
        ),
        _op(
            "searchconsole.sitemaps.list", "GET", SEARCH_CONSOLE,
            "/webmasters/v3/sites/{site_url}/sitemaps",
            resource_kind="search-console-site", max_attempts=1,
        ),
        _op(
            "searchconsole.sitemaps.get", "GET", SEARCH_CONSOLE,
            "/webmasters/v3/sites/{site_url}/sitemaps/{feedpath}",
            resource_kind="search-console-sitemap", max_attempts=1,
        ),
        _op(
            "searchconsole.urlinspection.inspect", "POST", SEARCH_CONSOLE_API,
            "/v1/urlInspection/index:inspect", True,
            resource_kind="search-console-url", max_attempts=1,
        ),
    )
}

RESOURCE_RE = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+)*$")
DOMAIN_PROPERTY_RE = re.compile(r"^sc-domain:[^\s/:]+(?:\.[^\s/:]+)+$", re.IGNORECASE)


def _search_console_site(value: str) -> str:
    if DOMAIN_PROPERTY_RE.fullmatch(value):
        return quote(value, safe="")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise AdvisorError("INVALID_SEARCH_CONSOLE_SITE", "The Search Console property identity is invalid.", EXIT_INPUT) from exc
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None or parsed.fragment
    ):
        raise AdvisorError("INVALID_SEARCH_CONSOLE_SITE", "The Search Console property identity is invalid.", EXIT_INPUT)
    return quote(value, safe="")


class ReadExecutor:
    def __init__(self, access_token: str, *, transport: JsonTransport | None = None) -> None:
        self._token = access_token
        self.transport = transport or JsonTransport()
        self.ledger: list[dict[str, Any]] = []

    def execute(
        self,
        operation_id: str,
        *,
        resource: str | None = None,
        feedpath: str | None = None,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> JsonResponse:
        operation = OPERATIONS.get(operation_id)
        if operation is None:
            raise AdvisorError("READ_OPERATION_NOT_ALLOWED", "The remote operation is not allowlisted.", EXIT_INPUT)
        if operation.method not in {"GET", "POST"} or (operation.method == "POST" and not operation.safe_post):
            raise AdvisorError("READ_OPERATION_NOT_ALLOWED", "Mutation methods are blocked.", EXIT_INPUT)
        if "{site_url}" in operation.path_template:
            if not resource:
                raise AdvisorError("INVALID_SEARCH_CONSOLE_SITE", "A Search Console property identity is required.", EXIT_INPUT)
            substitutions = {"site_url": _search_console_site(resource)}
            if "{feedpath}" in operation.path_template:
                if not feedpath:
                    raise AdvisorError("INVALID_SEARCH_CONSOLE_SITEMAP", "An exact sitemap URL is required.", EXIT_INPUT)
                substitutions["feedpath"] = quote(feedpath, safe="")
            elif feedpath is not None:
                raise AdvisorError("INVALID_SEARCH_CONSOLE_SITEMAP", "This operation does not accept a sitemap URL.", EXIT_INPUT)
            path = operation.path_template.format(**substitutions)
        elif "{resource}" in operation.path_template:
            if not resource or not RESOURCE_RE.fullmatch(resource):
                raise AdvisorError("INVALID_RESOURCE_NAME", "The Google resource name is invalid.", EXIT_INPUT)
            path = operation.path_template.format(resource=resource)
        else:
            if resource is not None or feedpath is not None:
                raise AdvisorError("INVALID_RESOURCE_NAME", "This operation does not accept a resource name.", EXIT_INPUT)
            path = operation.path_template
        url = operation.base_url + path
        if query:
            url += "?" + urlencode([(key, value) for key, value in query.items() if value is not None])
        ledger_entry = {"operationId": operation_id, "method": operation.method, "resource": resource, "requestId": None}
        self.ledger.append(ledger_entry)
        try:
            response = self.transport.request(
                operation.method,
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                payload=payload,
                max_attempts=operation.max_attempts,
                retry_mode="allowlisted-read" if operation.safe_post else None,
            )
            ledger_entry["requestId"] = response.request_id
            return response
        except AdvisorError as exc:
            if exc.code != "HTTP_ERROR":
                raise
            status = exc.details.get("status")
            body = str(exc.details.get("body", "")).lower()
            if status == 404:
                code, message = "RESOURCE_NOT_FOUND", "The selected Google resource was not found."
            elif status == 401:
                code, message = "TOKEN_INVALID", "Google rejected the authorization token; reauthorization may be required."
            elif status == 403 and ("accessnotconfigured" in body or "has not been used" in body or "disabled" in body):
                code, message = "API_DISABLED", "A required Google API is disabled in the user's Cloud project."
            elif status == 403 and any(
                marker in body for marker in (
                    "access_token_scope_insufficient",
                    "insufficient authentication scopes",
                    "insufficientauthenticationscopes",
                    "insufficientpermissions",
                )
            ):
                code, message = "SCOPE_MISSING", "The access token does not include the required Google API scope."
            elif status in {429, 403} and any(word in body for word in ("quota", "rate limit", "ratelimit")):
                code, message = "QUOTA_LIMITED", "Google API quota limited this read-only request."
            elif status == 403:
                code, message = "RESOURCE_ACCESS_DENIED", "The authorized Google account cannot read this resource."
            else:
                raise
            raise AdvisorError(code, message, EXIT_NETWORK, retryable=False, details={"status": status, "operationId": operation_id}) from exc
