"""Small Stage 5 Data API diagnostic; full reporting belongs to Stage 10."""

from __future__ import annotations

from typing import Any

from .pagination import collect_offsets
from .read_operation import ReadExecutor


class AnalyticsDataClient:
    def __init__(self, executor: ReadExecutor) -> None:
        self.executor = executor

    def event_diagnostic(self, property_name: str) -> dict[str, Any]:
        metadata = self.executor.execute("data.metadata.get", resource=property_name).data or {}
        available_metrics = {
            item.get("apiName") for item in metadata.get("metrics", []) if isinstance(item, dict)
        }
        metrics = [{"name": "eventCount"}]
        if "keyEvents" in available_metrics:
            metrics.append({"name": "keyEvents"})
        compatibility = self.executor.execute(
            "data.compatibility.check",
            resource=property_name,
            payload={"dimensions": [{"name": "eventName"}], "metrics": metrics},
        ).data or {}

        def fetch(offset: int, limit: int) -> dict[str, Any]:
            return self.executor.execute(
                "data.report.run",
                resource=property_name,
                payload={
                    "dateRanges": [{"startDate": "28daysAgo", "endDate": "yesterday"}],
                    "dimensions": [{"name": "eventName"}],
                    "metrics": metrics,
                    "limit": str(limit),
                    "offset": str(offset),
                    "returnPropertyQuota": True,
                },
            ).data or {}

        report = collect_offsets(fetch)
        return {
            "metadata": metadata,
            "compatibility": compatibility,
            "report": report,
            "period": {"start": "28daysAgo", "end": "yesterday"},
            "bounded": True,
        }
