"""Plain-language rendering for the guarded GA4/Search Console UI-link workflow."""

from __future__ import annotations

from typing import Any


def _resource_lines(resources: dict[str, Any], *, ru: bool) -> list[str]:
    ga4 = resources.get("ga4Property", {})
    stream = resources.get("webStream", {})
    search = resources.get("searchConsoleProperty", {})
    return [
        ("GA4 property: " if ru else "GA4 property: ") + f"{ga4.get('displayName')} ({ga4.get('name')})",
        ("Web stream: " if ru else "Web stream: ") + f"{stream.get('displayName')} ({stream.get('name')}, {stream.get('measurementId')})",
        ("Search Console property: " if ru else "Search Console property: ") + str(search.get("selectionKey")),
    ]


def render_link_plan(plan: dict[str, Any], language: str) -> str:
    ru = language == "ru"
    lines = ["Связь Search Console и GA4" if ru else "Search Console and GA4 link", ""]
    lines.extend(_resource_lines(plan.get("resources", {}), ru=ru))
    lines.extend([
        "",
        ("Статус плана: " if ru else "Plan status: ") + str(plan.get("status")),
        ("Режим: " if ru else "Mode: ") + str(plan.get("mode")),
        ("Действует до: " if ru else "Expires at: ") + str(plan.get("expiresAt")),
        ("Полный SHA-256: " if ru else "Full SHA-256: ") + str(plan.get("planSha256")),
    ])
    if plan.get("status") == "ready":
        lines.extend([
            "",
            "Этот SHA разрешает только один финальный Submit для показанной пары ресурсов."
            if ru else "This SHA authorizes only one final Submit for the shown resource pair.",
            "Удаление, пересоздание, подтверждение владения и публикация коллекции отчётов не разрешены."
            if ru else "Deletion, recreation, ownership verification, and report-collection publication are not authorized.",
        ])
    elif plan.get("status") == "no_op":
        lines.extend(["", "Точная связь уже существует; Submit не нужен." if ru else "The exact link already exists; no Submit is needed."])
    else:
        lines.extend(["", "Блокирующие причины:" if ru else "Blockers:"])
        lines.extend(f"- {item}" for item in plan.get("blockers", []))
    lines.extend([
        "",
        "Связь не нужна для прямых отчётов Advisor. Данные могут появляться с задержкой, а коллекция Search Console в GA4 по умолчанию не опубликована."
        if ru else "The link is not required for direct Advisor reports. Data can be delayed, and the Search Console collection in GA4 is unpublished by default.",
    ])
    return "\n".join(lines)


def render_link_result(result: dict[str, Any], language: str) -> str:
    ru = language == "ru"
    lines = ["Результат связи Search Console и GA4" if ru else "Search Console and GA4 link result", ""]
    lines.extend(_resource_lines(result.get("resources", {}), ru=ru))
    lines.extend([
        "",
        ("Результат: " if ru else "Outcome: ") + str(result.get("outcome")),
        ("Пара подтверждена в интерфейсе: " if ru else "Exact pair verified in the UI: ") + str(bool(result.get("readback", {}).get("pairMatched"))).lower(),
        ("Состояние данных: " if ru else "Integrated data state: ") + str(result.get("integratedDataState")),
        str(result.get("readback", {}).get("message", "")),
    ])
    if result.get("limitations"):
        lines.extend(["", "Ограничения:" if ru else "Limitations:"])
        lines.extend(f"- {item}" for item in result["limitations"])
    if result.get("outcome") == "created":
        lines.extend([
            "",
            "Связь подтверждена по UI. Появление данных проверяется отдельно; отсутствие данных сразу после создания не является ошибкой."
            if ru else "The link is verified in the UI. Data availability is checked separately; missing immediate data is not a link failure.",
        ])
    return "\n".join(lines)
