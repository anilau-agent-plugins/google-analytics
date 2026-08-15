"""Fail-closed registry and executor for Stage 5 Google read operations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from .errors import AdvisorError, EXIT_INPUT, EXIT_NETWORK
from .http import JsonResponse, JsonTransport


@dataclass(frozen=True)
class ReadOperation:
    operation_id: str
    method: str
    base_url: str
    path_template: str
    safe_post: bool = False


def _op(operation_id: str, method: str, base: str, path: str, safe_post: bool = False) -> ReadOperation:
    return ReadOperation(operation_id, method, base, path, safe_post)


ADMIN = "https://analyticsadmin.googleapis.com"
DATA = "https://analyticsdata.googleapis.com"
GTM = "https://tagmanager.googleapis.com"

OPERATIONS = {
    item.operation_id: item for item in (
        _op("admin.account_summaries.list", "GET", ADMIN, "/v1beta/accountSummaries"),
        _op("admin.property.get", "GET", ADMIN, "/v1beta/{resource}"),
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
    )
}

RESOURCE_RE = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+)*$")


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
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> JsonResponse:
        operation = OPERATIONS.get(operation_id)
        if operation is None:
            raise AdvisorError("READ_OPERATION_NOT_ALLOWED", "The remote operation is not allowlisted.", EXIT_INPUT)
        if operation.method not in {"GET", "POST"} or (operation.method == "POST" and not operation.safe_post):
            raise AdvisorError("READ_OPERATION_NOT_ALLOWED", "Mutation methods are blocked.", EXIT_INPUT)
        if "{resource}" in operation.path_template:
            if not resource or not RESOURCE_RE.fullmatch(resource):
                raise AdvisorError("INVALID_RESOURCE_NAME", "The Google resource name is invalid.", EXIT_INPUT)
            path = operation.path_template.format(resource=resource)
        else:
            if resource is not None:
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
                max_attempts=3,
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
                code, message = "SCOPE_MISSING", "Google rejected the authorization; reauthorization may be required."
            elif status == 403 and ("accessnotconfigured" in body or "has not been used" in body or "disabled" in body):
                code, message = "API_DISABLED", "A required Google API is disabled in the user's Cloud project."
            elif status in {429, 403} and any(word in body for word in ("quota", "rate limit", "ratelimit")):
                code, message = "QUOTA_LIMITED", "Google API quota limited this read-only request."
            elif status == 403:
                code, message = "RESOURCE_ACCESS_DENIED", "The authorized Google account cannot read this resource."
            else:
                raise
            raise AdvisorError(code, message, EXIT_NETWORK, retryable=False, details={"status": status, "operationId": operation_id}) from exc
