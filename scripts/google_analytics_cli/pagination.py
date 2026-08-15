"""Bounded pagination helpers with token-cycle detection."""

from __future__ import annotations

from typing import Any, Callable


def collect_page_tokens(
    fetch: Callable[[str | None], dict[str, Any]],
    item_key: str,
    *,
    max_pages: int,
    max_items: int,
) -> dict[str, Any]:
    items: list[Any] = []
    token: str | None = None
    seen: set[str] = set()
    pages = 0
    truncated = False
    while pages < max_pages and len(items) < max_items:
        page = fetch(token)
        pages += 1
        incoming = page.get(item_key, [])
        if isinstance(incoming, list):
            remaining = max_items - len(items)
            items.extend(incoming[:remaining])
            truncated = truncated or len(incoming) > remaining
        next_token = page.get("nextPageToken")
        if not next_token:
            break
        if next_token in seen or next_token == token:
            truncated = True
            break
        seen.add(str(next_token))
        token = str(next_token)
    else:
        truncated = True
    return {"items": items, "pages": pages, "truncated": truncated}


def collect_offsets(
    fetch: Callable[[int, int], dict[str, Any]],
    *,
    page_size: int = 250,
    max_pages: int = 4,
    max_rows: int = 1000,
) -> dict[str, Any]:
    rows: list[Any] = []
    metadata: dict[str, Any] = {}
    pages = 0
    truncated = False
    while pages < max_pages and len(rows) < max_rows:
        page = fetch(len(rows), min(page_size, max_rows - len(rows)))
        pages += 1
        batch = page.get("rows", []) if isinstance(page.get("rows", []), list) else []
        rows.extend(batch)
        for key in (
            "rowCount", "metadata", "propertyQuota", "dimensionHeaders", "metricHeaders",
            "kind", "totals", "maximums", "minimums",
        ):
            if key in page:
                metadata[key] = page[key]
        row_count = int(page.get("rowCount", len(rows)))
        if not batch or len(rows) >= row_count:
            break
    if len(rows) < int(metadata.get("rowCount", len(rows))):
        truncated = True
    return {"rows": rows, "pages": pages, "truncated": truncated, **metadata}
