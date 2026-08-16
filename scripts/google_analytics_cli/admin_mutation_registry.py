"""Closed Analytics Admin API mutation registry for Stage 7."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import AdvisorError, EXIT_INPUT


ADMIN = "https://analyticsadmin.googleapis.com"


@dataclass(frozen=True)
class MutationOperation:
    kind: str
    api_version: str
    method: str
    path_template: str
    create: bool
    allowed_fields: frozenset[str]
    required_fields: frozenset[str]
    immutable_fields: frozenset[str] = frozenset()
    experimental: bool = False
    secret_response: bool = False


def _operation(
    kind: str, version: str, method: str, path: str, *, create: bool,
    allowed: tuple[str, ...], required: tuple[str, ...] = (), immutable: tuple[str, ...] = (),
    experimental: bool = False, secret_response: bool = False,
) -> MutationOperation:
    return MutationOperation(
        kind, version, method, path, create, frozenset(allowed), frozenset(required),
        frozenset(immutable), experimental, secret_response,
    )


OPERATIONS = {
    item.kind: item for item in (
        _operation("PROPERTY_CREATE", "v1beta", "POST", "/v1beta/properties", create=True,
                   allowed=("parent", "displayName", "industryCategory", "timeZone", "currencyCode"),
                   required=("parent", "displayName", "timeZone", "currencyCode")),
        _operation("PROPERTY_PATCH", "v1beta", "PATCH", "/v1beta/{resource}", create=False,
                   allowed=("displayName", "industryCategory", "timeZone", "currencyCode")),
        _operation("WEB_STREAM_CREATE", "v1beta", "POST", "/v1beta/{resource}/dataStreams", create=True,
                   allowed=("type", "displayName", "webStreamData"),
                   required=("type", "displayName", "webStreamData"), immutable=("type",)),
        _operation("WEB_STREAM_PATCH", "v1beta", "PATCH", "/v1beta/{resource}", create=False,
                   allowed=("displayName", "webStreamData")),
        _operation("KEY_EVENT_CREATE", "v1beta", "POST", "/v1beta/{resource}/keyEvents", create=True,
                   allowed=("eventName", "countingMethod", "defaultValue"),
                   required=("eventName", "countingMethod"), immutable=("eventName",)),
        _operation("KEY_EVENT_PATCH", "v1beta", "PATCH", "/v1beta/{resource}", create=False,
                   allowed=("countingMethod", "defaultValue")),
        _operation("CUSTOM_DIMENSION_CREATE", "v1beta", "POST", "/v1beta/{resource}/customDimensions", create=True,
                   allowed=("parameterName", "displayName", "description", "scope", "disallowAdsPersonalization"),
                   required=("parameterName", "displayName", "scope"), immutable=("parameterName", "scope")),
        _operation("CUSTOM_DIMENSION_PATCH", "v1beta", "PATCH", "/v1beta/{resource}", create=False,
                   allowed=("displayName", "description", "disallowAdsPersonalization")),
        _operation("CUSTOM_METRIC_CREATE", "v1beta", "POST", "/v1beta/{resource}/customMetrics", create=True,
                   allowed=("parameterName", "displayName", "description", "measurementUnit", "scope", "restrictedMetricType"),
                   required=("parameterName", "displayName", "measurementUnit", "scope"), immutable=("parameterName", "scope")),
        _operation("CUSTOM_METRIC_PATCH", "v1beta", "PATCH", "/v1beta/{resource}", create=False,
                   allowed=("displayName", "description", "measurementUnit", "restrictedMetricType")),
        _operation("RETENTION_UPDATE", "v1beta", "PATCH", "/v1beta/{resource}/dataRetentionSettings", create=False,
                   allowed=("eventDataRetention", "userDataRetention", "resetUserDataOnNewActivity")),
        _operation("MP_SECRET_CREATE", "v1beta", "POST", "/v1beta/{resource}/measurementProtocolSecrets", create=True,
                   allowed=("displayName",), required=("displayName",), secret_response=True),
        _operation("MP_SECRET_PATCH", "v1beta", "PATCH", "/v1beta/{resource}", create=False,
                   allowed=("displayName",), secret_response=True),
        _operation("ENHANCED_MEASUREMENT_UPDATE", "v1alpha", "PATCH",
                   "/v1alpha/{resource}/enhancedMeasurementSettings", create=False,
                   allowed=("streamEnabled", "scrollsEnabled", "outboundClicksEnabled", "siteSearchEnabled",
                            "videoEngagementEnabled", "fileDownloadsEnabled", "pageChangesEnabled",
                            "formInteractionsEnabled", "searchQueryParameter", "uriQueryParameter"), experimental=True),
        _operation("DATA_REDACTION_UPDATE", "v1alpha", "PATCH",
                   "/v1alpha/{resource}/dataRedactionSettings", create=False,
                   allowed=("emailRedactionEnabled", "queryParameterRedactionEnabled", "queryParameterKeys"), experimental=True),
    )
}


def get_operation(kind: str) -> MutationOperation:
    operation = OPERATIONS.get(kind)
    if operation is None:
        raise AdvisorError("GA4_OPERATION_NOT_ALLOWED", "The GA4 change type is not allowlisted.", EXIT_INPUT,
                           details={"kind": kind})
    return operation


def validate_body(operation: MutationOperation, body: Any, field_mask: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(body, dict):
        raise AdvisorError("INVALID_CHANGE_REQUEST", "Each GA4 operation body must be an object.", EXIT_INPUT)
    fields = set(body)
    unknown = fields - operation.allowed_fields
    missing = operation.required_fields - fields
    if unknown or missing:
        raise AdvisorError(
            "GA4_FIELD_NOT_ALLOWED", "The GA4 operation contains unsupported or missing fields.", EXIT_INPUT,
            details={"unsupported": sorted(unknown), "missing": sorted(missing)},
        )
    if operation.create:
        if field_mask not in (None, [], ""):
            raise AdvisorError("INVALID_FIELD_MASK", "Create operations do not accept an update mask.", EXIT_INPUT)
        mask: list[str] = []
    else:
        if not isinstance(field_mask, list) or not field_mask or not all(isinstance(item, str) for item in field_mask):
            raise AdvisorError("INVALID_FIELD_MASK", "Patch operations require an explicit field mask.", EXIT_INPUT)
        if len(field_mask) != len(set(field_mask)) or "*" in field_mask:
            raise AdvisorError("INVALID_FIELD_MASK", "Wildcard or duplicate update-mask fields are forbidden.", EXIT_INPUT)
        mask = list(field_mask)
        if set(mask) != fields or not set(mask).issubset(operation.allowed_fields):
            raise AdvisorError("INVALID_FIELD_MASK", "The update mask must exactly match the requested body fields.", EXIT_INPUT)
    return dict(body), mask
