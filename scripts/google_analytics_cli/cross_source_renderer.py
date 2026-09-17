"""Plain-language RU/EN renderer for cross-source evidence."""

from __future__ import annotations

from typing import Any


def render_cross_source_report(report: dict[str, Any], language: str) -> str:
    ru = language == "ru"
    title = "Совместный анализ GA4 и Search Console" if ru else "GA4 and Search Console cross-source analysis"
    lines = [title, ""]
    if ru:
        lines.append("Короткий ответ: Search Console показывает видимость и клики до перехода, а GA4 — отдельно измеренные сессии и действия после начала сессии.")
        lines.append(f"Надёжность: {report['qualityTier']}; совпадение границ суток: {report['boundaryAlignment']}.")
        lines.append("\nПодтверждённые факты:")
    else:
        lines.append("Short answer: Search Console shows visibility and clicks before the visit; GA4 separately shows measured sessions and on-site outcomes after a session begins.")
        lines.append(f"Reliability: {report['qualityTier']}; day-boundary alignment: {report['boundaryAlignment']}.")
        lines.append("\nConfirmed facts:")
    for item in report.get("facts", []):
        lines.append(f"- [{item.get('source')}] {item.get('statement')}")
    counts = report.get("mappingSummary", {}).get("counts", {})
    lines.append(("\nСопоставление страниц: " if ru else "\nPage mapping: ") + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    lines.append("\n" + ("Неоднозначные и несопоставленные данные:" if ru else "Ambiguous and unmapped evidence:"))
    for item in report.get("findings", []):
        if item.get("category") in {"data_quality", "measurement"}:
            lines.append(f"- {item.get('statement')}")
    lines.append("\n" + ("Приоритетные действия:" if ru else "Priority actions:"))
    for item in report.get("recommendations", [])[:5]:
        lines.append(f"- {item.get('priority')}. {item.get('problem')} {item.get('verification')}")
    lines.append("\n" + (("Безопасный следующий шаг: " if ru else "Safe next step: ") + report.get("safeNextStep", "")))
    lines.append("\n" + ("Ограничения:" if ru else "Limitations:"))
    for item in report.get("limitations", []):
        lines.append(f"- [{item.get('source', 'cross-source')}] {item.get('message')}")
    return "\n".join(lines)
