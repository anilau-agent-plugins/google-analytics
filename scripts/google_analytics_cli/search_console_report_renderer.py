"""Plain-language rendering for immutable Search Console reports."""

from __future__ import annotations

from typing import Any


RU = {
    "title": "Отчёт Google Search Console", "answer": "Что происходит",
    "quality": "Надёжность данных", "facts": "Подтверждённые факты",
    "limits": "Ограничения", "recommendations": "Что можно сделать",
    "questions": "Что ещё нужно уточнить", "none": "Нет",
}
EN = {
    "title": "Google Search Console report", "answer": "What is happening",
    "quality": "Data reliability", "facts": "Confirmed facts",
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
    return f"{label} ({technical}): {item.get('value')}" if technical else str(item.get("statement", ""))


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
    lines.append(f"\n{labels['limits']}:")
    limitations = report.get("limitations", [])
    if limitations:
        lines.extend(
            f"- {RU_LIMITS.get(str(item.get('code')), item.get('message', item.get('code', ''))) if language == 'ru' else item.get('message', item.get('code', ''))}"
            for item in limitations
        )
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
