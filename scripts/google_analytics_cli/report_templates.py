"""Closed, beginner-focused GA4 report template registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import AdvisorError, EXIT_INPUT


DISCOVERY_REVISION = "20260817"
MAX_DIMENSIONS = 3
MAX_METRICS = 8
MAX_ROWS = 1000


@dataclass(frozen=True)
class ReportTemplate:
    dimensions: tuple[str, ...]
    metrics: tuple[str, ...]
    required_dimensions: tuple[str, ...] = ()
    required_metrics: tuple[str, ...] = ()
    max_rows: int = 250


CORE_TEMPLATES: dict[str, ReportTemplate] = {
    "overview": ReportTemplate((), ("activeUsers", "newUsers", "sessions", "engagedSessions", "engagementRate", "averageSessionDuration", "screenPageViews", "keyEvents"), required_metrics=("activeUsers", "sessions"), max_rows=10),
    "acquisition": ReportTemplate(("sessionDefaultChannelGroup",), ("sessions", "engagedSessions", "engagementRate", "keyEvents", "sessionKeyEventRate", "totalRevenue"), required_dimensions=("sessionDefaultChannelGroup",), required_metrics=("sessions",)),
    "user-acquisition": ReportTemplate(("firstUserDefaultChannelGroup",), ("newUsers", "activeUsers", "userKeyEventRate"), required_dimensions=("firstUserDefaultChannelGroup",), required_metrics=("newUsers",)),
    "landing": ReportTemplate(("landingPage",), ("sessions", "engagedSessions", "engagementRate", "keyEvents", "sessionKeyEventRate"), required_dimensions=("landingPage",), required_metrics=("sessions",)),
    "content": ReportTemplate(("pagePath", "pageTitle"), ("screenPageViews", "activeUsers", "userEngagementDuration"), required_dimensions=("pagePath",), required_metrics=("screenPageViews",)),
    "device": ReportTemplate(("deviceCategory",), ("sessions", "activeUsers", "engagementRate", "keyEvents", "sessionKeyEventRate"), required_dimensions=("deviceCategory",), required_metrics=("sessions",)),
    "geo": ReportTemplate(("country",), ("sessions", "activeUsers", "keyEvents", "sessionKeyEventRate"), required_dimensions=("country",), required_metrics=("sessions",)),
    "events": ReportTemplate(("eventName",), ("eventCount", "activeUsers"), required_dimensions=("eventName",), required_metrics=("eventCount",)),
    "key-events": ReportTemplate(("eventName",), ("keyEvents", "activeUsers"), required_dimensions=("eventName",), required_metrics=("keyEvents",)),
    "ecommerce": ReportTemplate((), ("ecommercePurchases", "purchaseRevenue", "grossPurchaseRevenue", "refundAmount", "itemsPurchased"), required_metrics=("ecommercePurchases",), max_rows=10),
    "google-organic-overview": ReportTemplate((), ("sessions", "engagedSessions", "engagementRate", "keyEvents", "sessionKeyEventRate", "totalRevenue"), required_metrics=("sessions",), max_rows=10),
    "google-organic-landing": ReportTemplate(("landingPage",), ("sessions", "engagedSessions", "engagementRate", "keyEvents", "sessionKeyEventRate"), required_dimensions=("landingPage",), required_metrics=("sessions",), max_rows=500),
    "google-organic-device": ReportTemplate(("deviceCategory",), ("sessions", "engagedSessions", "engagementRate", "keyEvents", "sessionKeyEventRate"), required_dimensions=("deviceCategory",), required_metrics=("sessions",)),
}

REALTIME_DIMENSIONS = {"eventName", "deviceCategory", "country", "unifiedScreenName"}
REALTIME_METRICS = {"activeUsers", "eventCount", "keyEvents", "screenPageViews"}
CUSTOM_DIMENSIONS = {
    "date", "sessionDefaultChannelGroup", "sessionSourceMedium", "firstUserDefaultChannelGroup",
    "firstUserSourceMedium", "landingPage", "pagePath", "pageTitle", "deviceCategory", "country",
    "eventName", "itemName", "itemCategory", "itemId",
}
CUSTOM_METRICS = {
    "activeUsers", "newUsers", "sessions", "engagedSessions", "engagementRate",
    "averageSessionDuration", "screenPageViews", "screenPageViewsPerSession", "eventCount",
    "keyEvents", "sessionKeyEventRate", "userKeyEventRate", "totalRevenue", "purchaseRevenue",
    "grossPurchaseRevenue", "refundAmount", "ecommercePurchases", "itemsPurchased",
    "userEngagementDuration",
}
FORBIDDEN_FIELDS = {
    "pageLocation", "landingPagePlusQueryString", "pagePathPlusQueryString", "transactionId",
    "userId", "userPseudoId", "userAgeBracket", "userGender", "brandingInterest", "audienceId",
    "audienceName", "city", "latitude", "longitude",
}


def _filter_expression(filters: list[dict[str, Any]]) -> dict[str, Any] | None:
    expressions: list[dict[str, Any]] = []
    for item in filters:
        field = str(item["fieldName"])
        values = [str(value) for value in item["values"]]
        if field not in CUSTOM_DIMENSIONS or field in FORBIDDEN_FIELDS:
            raise AdvisorError("REPORT_FIELD_UNAVAILABLE", f"Custom report filter field is not allowed: {field}.", EXIT_INPUT)
        if len(values) == 1:
            field_filter = {"fieldName": field, "stringFilter": {"matchType": "EXACT", "value": values[0], "caseSensitive": True}}
        else:
            field_filter = {"fieldName": field, "inListFilter": {"values": values, "caseSensitive": True}}
        expressions.append({"filter": field_filter})
    if not expressions:
        return None
    if len(expressions) == 1:
        return expressions[0]
    return {"andGroup": {"expressions": expressions}}


def custom_template(value: dict[str, Any]) -> tuple[ReportTemplate, dict[str, Any] | None]:
    dimensions = tuple(str(item) for item in value.get("dimensions", []))
    metrics = tuple(str(item) for item in value.get("metrics", []))
    if len(dimensions) > MAX_DIMENSIONS or not metrics or len(metrics) > MAX_METRICS:
        raise AdvisorError("REPORT_FIELD_UNAVAILABLE", "Custom reports allow at most 3 dimensions and 8 metrics.", EXIT_INPUT)
    rejected = [item for item in (*dimensions, *metrics) if item in FORBIDDEN_FIELDS]
    unknown_dimensions = [item for item in dimensions if item not in CUSTOM_DIMENSIONS]
    unknown_metrics = [item for item in metrics if item not in CUSTOM_METRICS]
    if rejected or unknown_dimensions or unknown_metrics:
        raise AdvisorError(
            "REPORT_FIELD_UNAVAILABLE", "The custom report contains a field outside the aggregate allowlist.", EXIT_INPUT,
            details={"forbidden": rejected, "dimensions": unknown_dimensions, "metrics": unknown_metrics},
        )
    row_limit = int(value.get("rowLimit", 250))
    return ReportTemplate(dimensions, metrics, dimensions, metrics, min(row_limit, MAX_ROWS)), _filter_expression(value.get("filters", []))


def supported_catalog() -> dict[str, Any]:
    return {
        "corePresets": sorted(CORE_TEMPLATES),
        "otherPresets": ["custom-core", "realtime", "funnel-experimental"],
        "customDimensions": sorted(CUSTOM_DIMENSIONS),
        "customMetrics": sorted(CUSTOM_METRICS),
        "forbiddenFields": sorted(FORBIDDEN_FIELDS),
        "limits": {"dimensions": MAX_DIMENSIONS, "metrics": MAX_METRICS, "rows": MAX_ROWS},
        "discoveryRevision": DISCOVERY_REVISION,
    }
