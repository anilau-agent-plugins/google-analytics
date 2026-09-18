"""Plain-language rendering for the full-picture advisor report."""

from __future__ import annotations

from typing import Any


LABELS = {
    "ru": {
        "title": "Полная картина по сайту", "trust": "Насколько можно доверять данным",
        "changed": "Что изменилось", "drivers": "Что связано с изменением",
        "gaps": "Что не проверено и почему", "actions": "Что делать сначала",
        "next": "Следующий шаг", "none": "Подтверждённых данных пока нет",
    },
    "en": {
        "title": "Full website picture", "trust": "How trustworthy the evidence is",
        "changed": "What changed", "drivers": "What is associated with the change",
        "gaps": "What was not checked and why", "actions": "What to do first",
        "next": "Next step", "none": "No confirmed evidence is available yet",
    },
}


def _statement(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("statement") or item.get("name") or item.get("problem") or item.get("message") or "Evidence item")
    return str(item)


def render_assessment(report: dict[str, Any], language: str = "auto") -> str:
    selected = "ru" if language == "ru" else "en"
    labels = LABELS[selected]
    lines = [f"{labels['title']}: {report['diagnosis']}"]
    confidence = report.get("confidence", {})
    lines.append(f"\n{labels['trust']}: {confidence.get('level', report.get('qualityTier', 'unknown'))}")
    for reason in confidence.get("reasons", [])[:5]:
        lines.append(f"- {reason}")
    lines.append(f"\n{labels['changed']}:")
    evidence = list(report.get("facts", [])) + list(report.get("calculations", []))
    lines.extend(f"- {_statement(item)}" for item in evidence[:8])
    if not evidence:
        lines.append(f"- {labels['none']}")
    lines.append(f"\n{labels['drivers']}:")
    drivers = list(report.get("findings", [])) + list(report.get("interpretations", []))
    lines.extend(f"- {_statement(item)}" for item in drivers[:8])
    if not drivers:
        lines.append(f"- {labels['none']}")
    lines.append(f"\n{labels['gaps']}:")
    gaps = [item for item in report.get("domains", []) if item.get("state") != "checked"]
    lines.extend(f"- {item.get('domainId')}: {item.get('reason')}" for item in gaps)
    if not gaps:
        lines.append("- None")
    lines.append(f"\n{labels['actions']}:")
    recommendations = report.get("recommendations", [])[:5]
    lines.extend(f"- {item.get('priority')}. {item.get('problem')} — {item.get('verification')}" for item in recommendations)
    if not recommendations:
        lines.append(f"- {labels['none']}")
    lines.append(f"\n{labels['next']}: {report['safeNextStep']}")
    return "\n".join(lines)
