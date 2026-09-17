"""Plain-language rendering for bounded Search Console URL Inspection reports."""

from __future__ import annotations

from typing import Any


def render_search_console_indexing_report(report: dict[str, Any], language: str) -> str:
    ru = language == "ru"
    lines = [
        "Что известно об индексировании выбранных страниц" if ru else "What is known about the selected pages",
        "",
        (f"Проверено страниц: {len(report['results'])} из {len(report['selectedUrls'])}." if ru else f"Inspected URLs: {len(report['results'])} of {len(report['selectedUrls'])}."),
        ("Важно: это сохранённая Google версия, а не live-проверка. Небольшую выборку нельзя считать отчётом по всему сайту." if ru else "Important: this is Google's indexed version, not a live test. The small sample is not a site-wide coverage report."),
    ]
    for item in report["results"]:
        lines.extend(["", item["url"], (f"Причина выбора: {item['reason']}" if ru else f"Selection reason: {item['reason']}"), f"verdict: {item['providerVerdict']}", f"coverageState: {item.get('coverageState') or 'unavailable'}", f"pageFetchState: {item.get('pageFetchState') or 'unavailable'}", f"lastCrawlTime: {item.get('lastCrawlTime') or 'unavailable'}", f"canonical: {item.get('canonicalComparison') or 'unavailable'}"])
    if report["limitations"]:
        lines.extend(["", "Ограничения" if ru else "Limitations"])
        lines.extend(f"- {item['message']}" for item in report["limitations"])
    if report["recommendations"]:
        lines.extend(["", "Что проверить дальше" if ru else "What to verify next"])
        lines.extend(f"- {item['problem']} {item['verification']}" for item in report["recommendations"])
    return "\n".join(lines)
