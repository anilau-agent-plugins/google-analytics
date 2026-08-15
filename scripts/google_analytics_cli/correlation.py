"""Correlate explicit public IDs from Google resources and static site evidence."""

from __future__ import annotations

from typing import Any


def correlate(admin: dict[str, Any], gtm: dict[str, Any] | None, site: dict[str, Any]) -> dict[str, Any]:
    site_ids = set(site.get("publicIds", []))
    stream_ids = {
        item.get("webStreamData", {}).get("measurementId")
        for item in admin.get("streams", {}).get("items", [])
        if isinstance(item, dict)
    } - {None}
    container_ids: set[str] = set()
    if gtm:
        for detail in gtm.get("workspaceDetails", []):
            workspace = detail.get("workspace", {})
            if workspace.get("tagManagerUrl"):
                pass
        live = gtm.get("liveVersion", {})
        public_id = live.get("container", {}).get("publicId") if isinstance(live, dict) else None
        if public_id:
            container_ids.add(public_id)
    expected = stream_ids | container_ids
    return {
        "siteIds": sorted(site_ids), "remoteIds": sorted(expected),
        "matchedIds": sorted(site_ids & expected), "siteOnlyIds": sorted(site_ids - expected),
        "remoteOnlyIds": sorted(expected - site_ids),
        "complete": bool(expected) and not (site_ids ^ expected),
    }
