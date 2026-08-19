"""Property-timezone report period calculation and safe comparisons."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import AdvisorError, EXIT_INPUT


def resolve_periods(request: dict[str, Any], property_timezone: str, *, now: Callable[[], datetime] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        zone = ZoneInfo(property_timezone)
    except ZoneInfoNotFoundError as exc:
        raise AdvisorError("REPORT_CONTEXT_DRIFT", "The GA4 property timezone is not a valid IANA timezone.", EXIT_INPUT) from exc
    current_now = (now or (lambda: datetime.now(timezone.utc)))().astimezone(zone)
    today = current_now.date()
    spec = request["period"]
    limitations: list[str] = []
    if spec["mode"] == "last-complete-days":
        days = spec.get("days")
        if not isinstance(days, int) or days < 1 or days > 366 or spec.get("from") is not None or spec.get("to") is not None:
            raise AdvisorError("REPORT_PERIOD_INVALID", "last-complete-days requires days and no explicit dates.", EXIT_INPUT)
        end = today if request.get("includeToday") else today - timedelta(days=1)
        start = end - timedelta(days=days - 1)
    else:
        if spec.get("days") is not None or not spec.get("from") or not spec.get("to"):
            raise AdvisorError("REPORT_PERIOD_INVALID", "explicit period requires from/to and no days value.", EXIT_INPUT)
        start, end = date.fromisoformat(spec["from"]), date.fromisoformat(spec["to"])
        if start > end or end > today:
            raise AdvisorError("REPORT_PERIOD_INVALID", "The explicit report period is invalid or extends into the future.", EXIT_INPUT)
        if end == today and not request.get("includeToday"):
            raise AdvisorError("REPORT_PERIOD_INVALID", "Set includeToday=true to include the incomplete current day.", EXIT_INPUT)
    complete = end < today
    if not complete:
        limitations.append("The current period includes today and is incomplete in the property timezone.")
    periods = [{"label": "current", "from": start.isoformat(), "to": end.isoformat(), "complete": complete}]
    length = (end - start).days + 1
    if "previous-period" in request.get("comparisons", []):
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=length - 1)
        periods.append({"label": "previous", "from": previous_start.isoformat(), "to": previous_end.isoformat(), "complete": True})
    if "previous-year" in request.get("comparisons", []):
        try:
            year_start, year_end = start.replace(year=start.year - 1), end.replace(year=end.year - 1)
        except ValueError:
            limitations.append("Previous-year comparison was omitted because the period cannot be shifted exactly across February 29.")
        else:
            periods.append({"label": "previous-year", "from": year_start.isoformat(), "to": year_end.isoformat(), "complete": True})
            limitations.append("The previous-year comparison uses the same calendar dates; weekdays may differ.")
    for left_index, left in enumerate(periods):
        for right in periods[left_index + 1:]:
            if max(left["from"], right["from"]) <= min(left["to"], right["to"]):
                raise AdvisorError("REPORT_PERIOD_INVALID", "Comparison periods must not overlap.", EXIT_INPUT)
    return periods, limitations


def comparison(current: float | int | None, baseline: float | int | None) -> dict[str, Any]:
    if current is None or baseline is None:
        return {"state": "not-comparable", "delta": None, "relative": None}
    delta = current - baseline
    if baseline == 0:
        return {"state": "from-zero" if current != 0 else "unchanged-zero", "delta": delta, "relative": None}
    return {"state": "comparable", "delta": delta, "relative": delta / abs(baseline)}
