"""Plain-language rendering for immutable Search Console reports."""

from __future__ import annotations

from typing import Any


RU = {
    "title": "Отчёт Google Search Console", "answer": "Что происходит",
    "quality": "Надёжность данных", "facts": "Подтверждённые факты",
    "calculations": "Сравнение с предыдущим периодом",
    "limits": "Ограничения", "recommendations": "Что можно сделать",
    "questions": "Что ещё нужно уточнить", "none": "Нет",
}
EN = {
    "title": "Google Search Console report", "answer": "What is happening",
    "quality": "Data reliability", "facts": "Confirmed facts",
    "calculations": "Comparison with the previous period",
    "limits": "Limitations", "recommendations": "What to do next",
    "questions": "Open questions", "none": "None",
}

RU_METRICS = {"clicks": "Клики", "impressions": "Показы", "ctr": "CTR", "position": "Средняя позиция"}
RU_LIMITS = {
    "TOP_ROWS_ONLY": "Показаны только ведущие строки, а не полный список.",
    "PRELIMINARY_DATA": "Google пометил часть данных как предварительную.",
    "SEARCH_CONSOLE_DATA_UNAVAILABLE": "За выбранный период Google не вернул доступных строк.",
    "REQUEST_EVIDENCE_REDACTED": "Потенциально чувствительные значения скрыты в сохранённых доказательствах.",
}


def _ru_fact(item: dict[str, Any]) -> str:
    technical = str(item.get("technicalName", ""))
    label = RU_METRICS.get(technical, technical or "Показатель")
    return f"{label} ({technical}): {_format_metric(technical, item.get('value'))}" if technical else str(item.get("statement", ""))


def _format_metric(name: str, value: Any) -> str:
    if not isinstance(value, (int, float)):
        return str(value)
    if name == "ctr":
        return f"{float(value) * 100:.2f}%"
    if name in {"clicks", "impressions"}:
        return f"{float(value):.0f}"
    if name == "position":
        return f"{float(value):.2f}"
    return f"{float(value):.2f}"


def _calculation(item: dict[str, Any], language: str) -> str:
    technical = str(item.get("calculationId", "")).rsplit(":", 1)[-1]
    inputs = item.get("inputs", {})
    result = item.get("result", {})
    current = inputs.get("current")
    previous = inputs.get("previous")
    relative = result.get("relative")
    relative_text = "n/a" if relative is None else f"{float(relative) * 100:+.1f}%"
    label = RU_METRICS.get(technical, technical) if language == "ru" else technical
    if technical == "ctr" and isinstance(current, (int, float)) and isinstance(previous, (int, float)):
        point_change = (float(current) - float(previous)) * 100
        change = f"{point_change:+.2f} п.п." if language == "ru" else f"{point_change:+.2f} pp"
    else:
        change = _format_metric(technical, result.get("absolute"))
        if isinstance(result.get("absolute"), (int, float)) and float(result["absolute"]) > 0:
            change = "+" + change
    return f"{label} ({technical}): {_format_metric(technical, previous)} → {_format_metric(technical, current)}; {change} ({relative_text})"


def _ru_recommendation(item: dict[str, Any]) -> str:
    problem = str(item.get("problem", ""))
    if "completeness" in problem or "privacy" in problem:
        return "Сначала учитывайте ограничения полноты: используйте общие итоги для масштаба, а детальные строки — как направляющий сигнал."
    if "CTR" in problem:
        return "Проверьте запросы и страницы с большим числом показов и низким CTR; меняйте сниппет или содержание только после проверки соответствия поисковому намерению."
    return problem


def render_search_console_report(report: dict[str, Any], language: str = "en") -> str:
    labels = RU if language == "ru" else EN
    lines = [
        f"{labels['title']}: {report['site']}",
        f"{labels['quality']}: {report.get('qualityTier', 'unknown')}",
    ]
    interpretations = report.get("interpretations", [])
    lines.append(f"\n{labels['answer']}:")
    if interpretations:
        if language == "ru":
            lines.append("- Отчёт описывает видимость и клики из поиска Google, но сам по себе не доказывает причины изменений.")
        else:
            lines.extend(f"- {item.get('statement', '')}" for item in interpretations[:3])
    else:
        lines.append(f"- {labels['none']}")
    lines.append(f"\n{labels['facts']}:")
    facts = report.get("facts", [])
    if facts:
        lines.extend(f"- {_ru_fact(item) if language == 'ru' else item.get('statement', '')}" for item in facts[:8])
    else:
        lines.append(f"- {labels['none']}")
    lines.append(f"\n{labels['calculations']}:")
    calculations = report.get("calculations", [])
    if calculations:
        lines.extend(f"- {_calculation(item, language)}" for item in calculations[:8])
    else:
        lines.append(f"- {labels['none']}")
    lines.append(f"\n{labels['limits']}:")
    limitations = report.get("limitations", [])
    if limitations:
        rendered_limits: list[str] = []
        seen_limits: set[tuple[str, str]] = set()
        for item in limitations:
            key = (str(item.get("code", "")), str(item.get("message", "")))
            if key in seen_limits:
                continue
            seen_limits.add(key)
            rendered_limits.append(
                str(RU_LIMITS.get(key[0], item.get("message", item.get("code", ""))))
                if language == "ru" else str(item.get("message", item.get("code", "")))
            )
        lines.extend(f"- {item}" for item in rendered_limits)
    else:
        lines.append(f"- {labels['none']}")
    lines.append(f"\n{labels['recommendations']}:")
    recommendations = report.get("recommendations", [])
    if recommendations:
        lines.extend(f"- {_ru_recommendation(item) if language == 'ru' else item.get('problem', '') + ': ' + item.get('verification', '')}" for item in recommendations[:5])
    else:
        lines.append(f"- {labels['none']}")
    if report.get("questions"):
        lines.append(f"\n{labels['questions']}:")
        lines.extend(f"- {item}" for item in report["questions"])
    return "\n".join(lines)
