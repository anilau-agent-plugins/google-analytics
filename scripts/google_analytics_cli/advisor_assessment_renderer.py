"""Plain-language rendering for the full-picture advisor report."""

from __future__ import annotations

import re
from typing import Any


LABELS = {
    "ru": {
        "title": "Коротко: что происходит",
        "trust": "Насколько данным можно доверять",
        "changed": "Что изменилось",
        "current": "Текущие ключевые показатели",
        "drivers": "Что связано с изменением",
        "gaps": "Что не проверено и почему",
        "actions": "Что делать сначала",
        "next": "Следующий шаг",
        "none": "Подтверждённых данных пока нет",
        "no_gaps": "Нет: проверены все запланированные области.",
        "limitation": "Ограничение",
    },
    "en": {
        "title": "In short: what is happening",
        "trust": "How trustworthy the evidence is",
        "changed": "What changed",
        "current": "Current key metrics",
        "drivers": "What is associated with the change",
        "gaps": "What was not checked and why",
        "actions": "What to do first",
        "next": "Next step",
        "none": "No confirmed evidence is available yet",
        "no_gaps": "None: all planned areas were checked.",
        "limitation": "Limitation",
    },
}

RU_QUALITY = {
    "reliable_for_description": "достаточно надёжно для описания ситуации",
    "directional_only": "только для понимания направления, не для точных выводов",
    "insufficient": "данных недостаточно",
    "diagnostic_only": "только для технической диагностики",
    "preliminary": "предварительные данные",
}

RU_METRICS = {
    "activeUsers": "Активные пользователи",
    "newUsers": "Новые пользователи",
    "sessions": "Сессии",
    "engagedSessions": "Сессии с взаимодействием",
    "engagementRate": "Доля сессий с взаимодействием",
    "averageSessionDuration": "Средняя длительность сессии",
    "screenPageViews": "Просмотры страниц",
    "keyEvents": "Ключевые события",
    "sessionKeyEventRate": "Доля сессий с ключевым событием",
    "totalRevenue": "Доход",
    "clicks": "Клики из поиска Google",
    "impressions": "Показы в поиске Google",
    "ctr": "CTR в поиске Google",
    "position": "Средняя позиция в поиске Google",
}

RU_CONFIDENCE_REASONS = {
    "Some source steps were blocked or failed; their dependent conclusions were not invented.":
        "Часть источников была недоступна; зависимые от них выводы не додумывались.",
    "GA4 and Search Console keep separate definitions and timezone limitations.":
        "GA4 и Search Console используют разные определения показателей и границы суток.",
}

RU_LIMITATIONS = {
    "TOP_ROWS_ONLY": "Детальные таблицы Search Console содержат ведущие строки, а не полный экспорт.",
    "PRELIMINARY_DATA": "Google пометил часть данных Search Console как предварительные.",
    "TIMEZONE_BOUNDARIES_DIFFER": "Границы суток GA4 и Search Console различаются из-за часовых поясов.",
    "METRIC_DEFINITIONS_DIFFER": "Клики Search Console и сессии GA4 — разные показатели; их разница не равна потерянным визитам.",
    "GA4_SOURCE_LIMITATION": "В данных GA4 есть ограничение, которое снижает точность сравнения.",
    "HTTP_ERROR": "Один из дополнительных источников не удалось прочитать из-за ответа Google API.",
}

RU_RECOMMENDATIONS = {
    "Some clicked pages have no corresponding measured GA4 sessions in the bounded evidence.": (
        "Для части страниц с кликами из поиска в выбранных данных нет соответствующих сессий GA4.",
        "Проверить теги, согласие, редиректы и страницы входа локально, затем повторить те же отчёты; не считать разницу потерянными визитами.",
    ),
    "The report has data-quality limitations that weaken detailed comparisons.": (
        "Ограничения качества данных снижают точность детальных сравнений.",
        "Устранить или явно принять перечисленные ограничения, затем повторить те же периоды и наборы отчётов.",
    ),
    "Some landing-page evidence is unmatched or ambiguous.": (
        "Часть данных по страницам входа не удалось однозначно сопоставить между источниками.",
        "Проверить редиректы, canonical, согласие и покрытие тегом, затем сравнить долю точных сопоставлений в свежих отчётах.",
    ),
    "Review warnings and confirm the intended business outcomes before designing measurement changes.": (
        "Перед изменением измерений нужно разобрать предупреждения и подтвердить нужные бизнес-результаты.",
        "После проверки повторить те же ограниченные диагностические шаги.",
    ),
    "The report has completeness or privacy limitations.": (
        "Детальные данные имеют ограничения полноты или конфиденциальности.",
        "Использовать общие итоги для оценки масштаба, а детальные строки — только как направляющий сигнал.",
    ),
}

RU_DOMAIN_NAMES = {
    "resource-and-period": "точные ресурсы и периоды",
    "business-goal-and-outcomes": "бизнес-цели и результаты",
    "tag-and-gtm-route": "установка Google tag или GTM",
    "consent-and-coverage": "согласие и полнота сбора",
    "overview": "общая динамика GA4",
    "acquisition": "источники трафика",
    "landing-and-content": "страницы входа и контент",
    "device-and-geo": "устройства и география",
    "events-and-key-events": "события и ключевые события",
    "ecommerce": "электронная торговля",
    "search-console-performance": "эффективность в Google Search Console",
    "cross-source-context": "совместный контекст GA4 и Search Console",
    "data-quality-and-quota": "качество данных и квоты",
    "unanswered-business-questions": "неотвеченные бизнес-вопросы",
}


def _technical_name(item: dict[str, Any]) -> str:
    explicit = item.get("technicalName")
    if explicit:
        return str(explicit)
    identity = str(item.get("calculationId", ""))
    return identity.rsplit(":", 1)[-1] if identity else ""


def _format_number(value: Any, metric: str = "") -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return str(value)
    number = float(value)
    if metric in {"engagementRate", "sessionKeyEventRate", "ctr"}:
        return f"{number * 100:.2f}%"
    if metric == "averageSessionDuration":
        minutes, seconds = divmod(round(number), 60)
        return f"{minutes} мин {seconds:02d} сек" if minutes else f"{seconds} сек"
    if metric in {
        "activeUsers", "newUsers", "sessions", "engagedSessions", "screenPageViews",
        "keyEvents", "clicks", "impressions",
    }:
        return f"{number:,.0f}".replace(",", " ")
    return f"{number:,.2f}".replace(",", " ").rstrip("0").rstrip(".")


def _ru_fact(item: dict[str, Any]) -> str:
    metric = _technical_name(item)
    label = RU_METRICS.get(metric, metric or "Показатель")
    source = str(item.get("source", ""))
    organic = source == "ga4" and "google-organic" in str(item.get("evidenceRefs", []))
    source_suffix = " — органический поиск Google" if organic else ""
    return f"{label} ({metric}){source_suffix}: {_format_number(item.get('value'), metric)}"


def _calculation_key(item: dict[str, Any]) -> str:
    return _technical_name(item) or str(item.get("calculationId", ""))


def _calculation(item: dict[str, Any], language: str) -> str:
    metric = _technical_name(item)
    result = item.get("result", {}) if isinstance(item.get("result"), dict) else {}
    inputs = item.get("inputs", {}) if isinstance(item.get("inputs"), dict) else {}
    current = inputs.get("current", result.get("current"))
    previous = inputs.get("previous", result.get("previous"))
    relative = result.get("relative")
    if language == "ru":
        label = RU_METRICS.get(metric, metric or str(item.get("name", "Показатель")))
        if metric == "ctr" and isinstance(current, (int, float)) and isinstance(previous, (int, float)):
            delta = f"{(float(current) - float(previous)) * 100:+.2f} п.п."
        elif isinstance(result.get("absolute"), (int, float)):
            delta = _format_number(result["absolute"], metric)
            if float(result["absolute"]) > 0:
                delta = "+" + delta
        else:
            delta = "нет сопоставимого изменения"
        relative_text = "нет сопоставимой базы" if relative is None else f"{float(relative) * 100:+.1f}%"
        return (
            f"{label} ({metric}): {_format_number(previous, metric)} → "
            f"{_format_number(current, metric)}; изменение {delta} ({relative_text})"
        )
    return str(item.get("name") or item.get("statement") or metric or "Calculation")


def _extract_leading_count(statement: str, fallback: int = 0) -> int:
    match = re.match(r"\s*(\d+)", statement)
    return int(match.group(1)) if match else fallback


def _ru_finding(item: dict[str, Any]) -> str:
    finding_id = str(item.get("findingId", ""))
    code = str(item.get("code", ""))
    if finding_id == "finding:clicks-without-measured-sessions":
        count = _extract_leading_count(str(item.get("statement", "")), len(item.get("evidenceRefs", [])))
        return (
            f"У {count} точно сопоставленных страниц есть клики Search Console, но в выбранных "
            "строках нет измеренных сессий GA4. Это сигнал для проверки, а не число потерянных визитов."
        )
    if finding_id == "finding:url-mapping":
        count = _extract_leading_count(str(item.get("statement", "")))
        return f"{count} строк по страницам не были объединены автоматически: безопасного точного сопоставления URL не найдено."
    if code == "PUBLIC_ID_MULTIPLE_RUNTIME_FILES":
        ids = ", ".join(str(value) for value in item.get("ids", []))
        return f"Один Google ID встречается в нескольких runtime-файлах ({ids}); это требует проверки на возможное дублирование загрузки."
    if code == "KEY_EVENT_NOT_OBSERVED":
        return f"Настроенное ключевое событие `{item.get('eventName', 'unknown')}` не наблюдалось за диагностические 28 дней."
    return str(item.get("statement") or item.get("message") or code or "Наблюдение")


def _ru_interpretation(item: dict[str, Any]) -> str:
    interpretation_id = str(item.get("interpretationId", ""))
    if interpretation_id == "interpretation:overview":
        return "Отчёт описывает видимость и клики из поиска Google, но сам по себе не доказывает причины изменений."
    if interpretation_id == "interpretation:mapping":
        count = _extract_leading_count(str(item.get("statement", "")))
        return f"Точно сопоставлены {count} страницы; клики Search Console и сессии GA4 остаются разными показателями."
    if interpretation_id == "interpretation:device-context":
        count = _extract_leading_count(str(item.get("statement", "")))
        return f"Контекст устройств доступен для {count} нормализованных категорий; различия долей не доказывают причинность."
    if interpretation_id == "interpretation:metric-boundary":
        return "Разница между кликами Search Console и сессиями GA4 используется только для диагностики и не считается потерянными визитами."
    return str(item.get("statement") or "Интерпретация")


def _ru_confidence_reason(reason: str, domain_count: int) -> str:
    checked = re.fullmatch(r"Checked (\d+) of (\d+) domains\.", reason)
    if checked:
        return f"Проверено {checked.group(1)} из {checked.group(2)} областей."
    if reason in RU_CONFIDENCE_REASONS:
        return RU_CONFIDENCE_REASONS[reason]
    return reason or f"Проверено областей: {domain_count}."


def _ru_limitation(item: dict[str, Any]) -> str:
    code = str(item.get("code", ""))
    message = str(item.get("message", ""))
    if item.get("type") == "small-data" or "fewer than 20 key events" in message:
        return "Хотя бы в одном периоде меньше 20 ключевых событий, поэтому коэффициенты и изменения ориентировочные."
    return RU_LIMITATIONS.get(code, message or code or "Есть ограничение качества данных.")


def _ru_recommendation(item: dict[str, Any]) -> tuple[str, str]:
    problem = str(item.get("problem", ""))
    if problem in RU_RECOMMENDATIONS:
        return RU_RECOMMENDATIONS[problem]
    return (
        problem or "Нужно дополнительное исследование.",
        str(item.get("verification", "Проверить результат тем же ограниченным способом.")),
    )


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def render_assessment(report: dict[str, Any], language: str = "auto") -> str:
    selected = "ru" if language == "ru" else "en"
    labels = LABELS[selected]
    lines = [f"{labels['title']}: {report['diagnosis']}"]
    confidence = report.get("confidence", {})
    quality = str(confidence.get("level", report.get("qualityTier", "unknown")))
    rendered_quality = RU_QUALITY.get(quality, quality) if selected == "ru" else quality
    lines.append(f"\n{labels['trust']}: {rendered_quality} ({quality})")
    reasons = [str(item) for item in confidence.get("reasons", [])]
    if selected == "ru":
        reasons = [_ru_confidence_reason(item, len(report.get("domains", []))) for item in reasons]
        limitations = _unique([_ru_limitation(item) for item in report.get("limitations", [])])
    else:
        limitations = _unique([
            str(item.get("message", item.get("code", "")))
            for item in report.get("limitations", [])
        ])
    lines.extend(f"- {item}" for item in reasons[:5])
    lines.extend(f"- {labels['limitation']}: {item}" for item in limitations[:4])

    lines.append(f"\n{labels['changed']}:")
    calculations: list[dict[str, Any]] = []
    calculation_keys: set[str] = set()
    for item in report.get("calculations", []):
        if not isinstance(item, dict):
            continue
        key = _calculation_key(item)
        if key in calculation_keys:
            continue
        calculation_keys.add(key)
        calculations.append(item)
    if calculations:
        lines.extend(f"- {_calculation(item, selected)}" for item in calculations[:8])
    else:
        lines.append(f"- {labels['none']}")

    facts = [item for item in report.get("facts", []) if isinstance(item, dict)]
    lines.append(f"\n{labels['current']}:")
    if facts:
        if selected == "ru":
            lines.extend(f"- {_ru_fact(item)}" for item in facts[:8])
        else:
            lines.extend(
                f"- {item.get('statement', item.get('technicalName', 'Fact'))}"
                for item in facts[:8]
            )
    else:
        lines.append(f"- {labels['none']}")

    lines.append(f"\n{labels['drivers']}:")
    findings = [item for item in report.get("findings", []) if isinstance(item, dict)]
    interpretations = [item for item in report.get("interpretations", []) if isinstance(item, dict)]
    if selected == "ru":
        drivers = _unique(
            [_ru_finding(item) for item in findings]
            + [_ru_interpretation(item) for item in interpretations]
        )
    else:
        drivers = _unique([str(item.get("statement", "")) for item in findings + interpretations])
    lines.extend(f"- {item}" for item in drivers[:8])
    if not drivers:
        lines.append(f"- {labels['none']}")

    lines.append(f"\n{labels['gaps']}:")
    gaps = [item for item in report.get("domains", []) if item.get("state") != "checked"]
    if gaps:
        for item in gaps:
            domain = str(item.get("domainId", "unknown"))
            name = RU_DOMAIN_NAMES.get(domain, domain) if selected == "ru" else domain
            lines.append(f"- {name} ({domain}): {item.get('reason')}")
    else:
        lines.append(f"- {labels['no_gaps']}")

    lines.append(f"\n{labels['actions']}:")
    recommendations = [
        item for item in report.get("recommendations", [])[:5] if isinstance(item, dict)
    ]
    for item in recommendations:
        if selected == "ru":
            problem, verification = _ru_recommendation(item)
            lines.append(
                f"- {item.get('priority')}. {problem} Проверка результата: {verification}"
            )
        else:
            problem = str(item.get("problem", ""))
            verification = str(item.get("verification", ""))
            lines.append(f"- {item.get('priority')}. {problem} — {verification}")
    if not recommendations:
        lines.append(f"- {labels['none']}")

    if selected == "ru" and recommendations:
        first_problem, first_verification = _ru_recommendation(recommendations[0])
        if recommendations[0].get("requiresMutationWorkflow"):
            next_step = f"Подготовить отдельный безопасный план для рекомендации №1: {first_problem}"
        else:
            next_step = f"Проверить рекомендацию №1 без изменений: {first_verification}"
    else:
        next_step = str(report["safeNextStep"])
    lines.append(f"\n{labels['next']}: {next_step}")
    return "\n".join(lines)
