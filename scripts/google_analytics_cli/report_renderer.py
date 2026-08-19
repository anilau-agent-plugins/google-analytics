"""Plain-language rendering of an immutable Stage 10 report artifact."""

from __future__ import annotations

from typing import Any


RU = {
    "title": "Отчёт Google Analytics", "quality": "Надёжность данных", "facts": "Главные факты",
    "limits": "Ограничения", "recommendations": "Рекомендации", "none": "Нет",
}
EN = {
    "title": "Google Analytics report", "quality": "Data reliability", "facts": "Main facts",
    "limits": "Limitations", "recommendations": "Recommendations", "none": "None",
}


def render_report(report: dict[str, Any], language: str = "auto") -> str:
    labels = RU if language == "ru" else EN
    lines = [f"{labels['title']}: {report['property']}", f"{labels['quality']}: {report.get('qualityTier', 'unknown')}"]
    lines.append(f"\n{labels['facts']}:")
    if report.get("facts"):
        lines.extend(f"- {item.get('statement', item.get('technicalName', 'fact'))}" for item in report["facts"][:7])
    else:
        lines.append(f"- {labels['none']}")
    lines.append(f"\n{labels['limits']}:")
    if report.get("limitations"):
        lines.extend(f"- {item.get('message', item.get('type', 'limitation'))}" for item in report["limitations"])
    else:
        lines.append(f"- {labels['none']}")
    lines.append(f"\n{labels['recommendations']}:")
    if report.get("recommendations"):
        lines.extend(f"- {item.get('problem')}: {item.get('verification')}" for item in report["recommendations"][:5])
    else:
        lines.append(f"- {labels['none']}")
    return "\n".join(lines)
