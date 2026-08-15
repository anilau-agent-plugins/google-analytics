"""Bounded read-only Analytics Admin API client."""

from __future__ import annotations

from typing import Any

from .pagination import collect_page_tokens
from .read_operation import ReadExecutor


class AnalyticsAdminClient:
    def __init__(self, executor: ReadExecutor) -> None:
        self.executor = executor

    def _list(self, operation: str, key: str, resource: str | None = None) -> dict[str, Any]:
        def fetch(token: str | None) -> dict[str, Any]:
            query = {"pageSize": 200, "pageToken": token}
            return self.executor.execute(operation, resource=resource, query=query).data or {}
        return collect_page_tokens(fetch, key, max_pages=50, max_items=10_000)

    def account_summaries(self) -> dict[str, Any]:
        return self._list("admin.account_summaries.list", "accountSummaries")

    def property_baseline(
        self, property_name: str, *, experimental_alpha: bool = False, stream_name: str | None = None
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "property": self.executor.execute("admin.property.get", resource=property_name).data or {},
            "streams": self._list("admin.streams.list", "dataStreams", property_name),
            "keyEvents": self._list("admin.key_events.list", "keyEvents", property_name),
            "customDimensions": self._list("admin.custom_dimensions.list", "customDimensions", property_name),
            "customMetrics": self._list("admin.custom_metrics.list", "customMetrics", property_name),
            "retention": self.executor.execute("admin.retention.get", resource=property_name).data or {},
            "experimental": False,
        }
        if experimental_alpha:
            if not stream_name:
                raise ValueError("Experimental Admin alpha reads require an explicit web stream.")
            result["experimental"] = True
            result["alpha"] = {
                "enhancedMeasurement": self.executor.execute(
                    "admin.enhanced_measurement.get", resource=stream_name
                ).data or {},
                "dataRedaction": self.executor.execute("admin.data_redaction.get", resource=stream_name).data or {},
            }
        return result
