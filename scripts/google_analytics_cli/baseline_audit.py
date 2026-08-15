"""Stage 5 resource discovery and plain-language baseline orchestration."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analytics_admin import AnalyticsAdminClient
from .analytics_data import AnalyticsDataClient
from .artifact_store import ArtifactStore, utc_now
from .auth import AuthService
from .correlation import correlate
from .errors import AdvisorError, EXIT_INPUT
from .read_operation import ReadExecutor
from .site_scanner import inspect_site
from .tag_manager import TagManagerClient


def _safe_gtm(source: dict[str, Any]) -> dict[str, Any]:
    def named(items: list[Any]) -> list[dict[str, Any]]:
        safe = []
        for item in items:
            if isinstance(item, dict):
                safe.append({key: item[key] for key in ("path", "name", "type", "tagId", "triggerId", "variableId", "workspaceId", "fingerprint") if key in item})
        return safe
    result = {
        "workspaces": named(source.get("workspaces", {}).get("items", [])),
        "workspaceLimitApplied": bool(source.get("workspaceLimitApplied")),
        "workspaceDetails": [],
        "versionHeaders": named(source.get("versionHeaders", {}).get("items", [])),
        "versionHeadersTruncated": bool(source.get("versionHeaders", {}).get("truncated")),
    }
    live = source.get("liveVersion", {})
    result["liveVersion"] = {key: live[key] for key in ("path", "name", "containerVersionId", "fingerprint") if key in live}
    for detail in source.get("workspaceDetails", []):
        result["workspaceDetails"].append({
            "workspace": named([detail.get("workspace", {})])[0] if detail.get("workspace") else {},
            "counts": {key: len(detail.get(key, {}).get("items", [])) for key in ("tags", "triggers", "variables", "builtInVariables", "googleTagConfigs")},
            "truncated": {key: bool(detail.get(key, {}).get("truncated")) for key in ("tags", "triggers", "variables", "builtInVariables", "googleTagConfigs")},
            "statusCounts": {
                "workspaceChanges": len(detail.get("status", {}).get("workspaceChange", [])),
                "mergeConflicts": len(detail.get("status", {}).get("mergeConflict", [])),
            },
        })
    return result


FINDING_MESSAGES = {
    "MULTIPLE_PUBLIC_IDS": "Several Google measurement identifiers were found in runtime source.",
    "PUBLIC_ID_MULTIPLE_RUNTIME_FILES": "The same Google identifier appears in more than one runtime file.",
    "DUPLICATE_TAG_LOADER": "More than one Google tag or GTM loader was found in runtime source.",
    "POSSIBLE_DOUBLE_COLLECTION": "Direct Google tag and GTM loaders coexist, so duplicate collection is possible.",
    "CONSENT_DEFAULT_NOT_FOUND": "A Consent Mode update was found without a matching default command.",
    "CONSENT_DEFAULT_AFTER_LOADER": "Consent defaults appear after a Google loader in the same file.",
    "DYNAMIC_MEASUREMENT_ID": "The measurement identifier appears to be generated dynamically and needs manual review.",
    "NO_KEY_EVENTS_FOUND": "No GA4 key events were returned for the selected property.",
    "KEY_EVENT_NOT_OBSERVED": "A configured key event was not observed in the bounded 28-day diagnostic.",
    "TAG_RESOURCE_MISMATCH": "Public identifiers in source and selected Google resources do not fully match.",
    "GTM_DRAFT_CHANGES_PRESENT": "At least one GTM workspace has unpublished changes or merge conflicts.",
}


def _explain_finding(finding: dict[str, Any]) -> dict[str, Any]:
    code = str(finding.get("code", "BASELINE_FINDING"))
    severity = finding.get("severity", "info")
    if severity not in {"info", "warning", "high"}:
        severity = "warning"
    return {
        **finding,
        "severity": severity,
        "confidence": finding.get("confidence", "probable"),
        "statement": finding.get("message", FINDING_MESSAGES.get(code, code.replace("_", " ").title())),
        "businessImpact": finding.get("businessImpact", "This can reduce confidence in analytics used for decisions."),
        "evidence": finding.get("evidence", [{"source": "baseline", "code": code}]),
        "limitation": finding.get("limitation", "Baseline evidence does not prove production behavior by itself."),
        "safeNextStep": finding.get("safeNextStep", "Review the cited evidence before planning any change."),
    }


class BaselineService:
    def __init__(self, *, auth: AuthService | None = None) -> None:
        self.auth = auth or AuthService()

    def _clients(self, profile: str | None) -> tuple[str, ReadExecutor, AnalyticsAdminClient, AnalyticsDataClient, TagManagerClient]:
        selected, token, _ = self.auth.access_token(profile)
        executor = ReadExecutor(token, transport=self.auth.json_transport)
        return selected, executor, AnalyticsAdminClient(executor), AnalyticsDataClient(executor), TagManagerClient(executor)

    def resources(self, profile: str | None) -> dict[str, Any]:
        selected, executor, admin, _, gtm = self._clients(profile)
        summaries = admin.account_summaries()
        limitations: list[dict[str, Any]] = []
        try:
            accounts = gtm.accounts()
        except AdvisorError as exc:
            accounts = {"items": [], "pages": 0, "truncated": False}
            limitations.append({"code": exc.code, "message": exc.message, "provider": "tag-manager"})
        containers: list[dict[str, Any]] = []
        limited = False
        for account in accounts["items"]:
            path = account.get("path")
            if isinstance(path, str):
                try:
                    page = gtm.containers(path)
                except AdvisorError as exc:
                    limitations.append({"code": exc.code, "message": exc.message, "provider": "tag-manager", "resource": path})
                    continue
                for container in page["items"]:
                    if isinstance(container, dict):
                        features = container.get("features", {})
                        public_id = str(container.get("publicId", ""))
                        container = {
                            **container,
                            "containerKind": "gtm" if public_id.startswith("GTM-") or features.get("supportWorkspaces") else "google-tag",
                        }
                    containers.append(container)
                limited = limited or page["truncated"]
        return {
            "profileId": selected, "analytics": summaries, "tagManager": {"accounts": accounts, "containers": containers},
            "limitations": limitations + ([{"code": "PAGINATION_LIMIT_REACHED", "message": "Resource discovery reached its safety bound."}] if summaries["truncated"] or limited else []),
            "requestLedger": executor.ledger, "mutationPerformed": False,
        }

    def audit(
        self, profile: str | None, project_root: Path, *, property_name: str | None,
        stream_name: str | None, gtm_container: str | None, experimental_alpha: bool = False,
    ) -> dict[str, Any]:
        if not property_name:
            raise AdvisorError(
                "SELECTION_REQUIRED", "Select an Analytics property before creating a baseline audit.", EXIT_INPUT,
                next_action="Run resources list and repeat audit baseline with --property properties/N.",
            )
        if stream_name and not stream_name.startswith(property_name + "/dataStreams/"):
            raise AdvisorError("RESOURCE_NOT_FOUND", "The selected web stream does not belong to the selected property.", EXIT_INPUT)
        if experimental_alpha and not stream_name:
            raise AdvisorError(
                "EXPERIMENTAL_CAPABILITY_UNAVAILABLE",
                "Experimental Admin alpha reads require an explicitly selected web stream.", EXIT_INPUT,
            )
        site = inspect_site(project_root)
        selected, executor, admin_client, data_client, gtm_client = self._clients(profile)
        admin = admin_client.property_baseline(
            property_name, experimental_alpha=experimental_alpha, stream_name=stream_name
        )
        streams = admin["streams"]["items"]
        if stream_name and stream_name not in {item.get("name") for item in streams if isinstance(item, dict)}:
            raise AdvisorError("RESOURCE_NOT_FOUND", "The selected web stream was not returned by Analytics Admin API.", EXIT_INPUT)
        if stream_name:
            selected_stream = next(item for item in streams if item.get("name") == stream_name)
            if selected_stream.get("type") not in {None, "WEB_DATA_STREAM"}:
                raise AdvisorError("UNSUPPORTED_STREAM_TYPE", "Stage 5 supports website data streams only.", EXIT_INPUT)
        provider_limitations: list[dict[str, Any]] = []
        try:
            data = data_client.event_diagnostic(property_name)
        except AdvisorError as exc:
            data = None
            provider_limitations.append({"code": exc.code, "message": exc.message, "provider": "analytics-data"})
        try:
            gtm_raw = gtm_client.container_baseline(gtm_container) if gtm_container else None
        except AdvisorError as exc:
            gtm_raw = None
            provider_limitations.append({"code": exc.code, "message": exc.message, "provider": "tag-manager"})
        gtm = _safe_gtm(gtm_raw) if gtm_raw else None
        correlation = correlate(admin, gtm_raw, site)
        limitations: list[dict[str, Any]] = list(site["limitations"]) + provider_limitations
        for name, value in admin.items():
            if isinstance(value, dict) and value.get("truncated"):
                limitations.append({"code": "PAGINATION_LIMIT_REACHED", "message": f"{name} was truncated at the safety limit."})
        if data and data["report"].get("truncated"):
            limitations.append({"code": "PAGINATION_LIMIT_REACHED", "message": "The event diagnostic was limited to 1000 rows."})
        if gtm and (gtm["workspaceLimitApplied"] or gtm["versionHeadersTruncated"]):
            limitations.append({"code": "PAGINATION_LIMIT_REACHED", "message": "The GTM deep audit reached its safety bound."})
        findings = list(site["findings"])
        if not admin["keyEvents"]["items"]:
            findings.append({"code": "NO_KEY_EVENTS_FOUND", "severity": "attention", "message": "No key events were returned; meaningful outcomes may not be configured yet."})
        if data:
            observed = {
                row.get("dimensionValues", [{}])[0].get("value")
                for row in data["report"].get("rows", []) if isinstance(row, dict) and row.get("dimensionValues")
            } - {None}
            configured = {
                item.get("eventName") for item in admin["keyEvents"]["items"] if isinstance(item, dict)
            } - {None}
            for event_name in sorted(configured - observed):
                findings.append({
                    "code": "KEY_EVENT_NOT_OBSERVED", "severity": "warning", "eventName": event_name,
                    "confidence": "confirmed", "limitation": "The diagnostic covers only 28daysAgo through yesterday.",
                })
        if gtm and any(
            detail["statusCounts"]["workspaceChanges"] or detail["statusCounts"]["mergeConflicts"]
            for detail in gtm["workspaceDetails"]
        ):
            findings.append({"code": "GTM_DRAFT_CHANGES_PRESENT", "severity": "warning", "confidence": "confirmed"})
        if correlation["siteOnlyIds"] or correlation["remoteOnlyIds"]:
            findings.append({"code": "TAG_RESOURCE_MISMATCH", "severity": "warning", "message": "Public IDs in the site and selected Google resources do not fully match."})
        findings = [_explain_finding(item) for item in findings]

        store = ArtifactStore(project_root)
        request_ids = [str(item["requestId"]) for item in executor.ledger if item.get("requestId")]
        snapshots = [
            store.write_snapshot("analytics-admin", "v1beta", property_name, admin, request_ids),
            store.write_snapshot("website", "static-scan", str(project_root.resolve()), site, request_ids),
        ]
        if data:
            snapshots.append(store.write_snapshot("analytics-data", "v1beta", property_name, data, request_ids))
        if gtm and gtm_container:
            snapshots.append(store.write_snapshot("tag-manager", "v2", gtm_container, gtm, request_ids))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        fingerprint = hashlib.sha256(f"{selected}:{property_name}:{stamp}".encode()).hexdigest()[:12]
        audit = {
            "schemaVersion": 1, "artifactType": "baseline-report", "generatedAt": utc_now(),
            "auditId": f"baseline-{stamp}-{fingerprint}", "profileRef": selected,
            "projectRoot": str(project_root.resolve()),
            "targets": {"property": property_name, "webStream": stream_name, "gtmContainer": gtm_container},
            "scope": {"readOnly": True, "siteScan": "local-static", "dataPeriod": "28daysAgo..yesterday", "experimentalAdminAlpha": experimental_alpha},
            "completeness": "partial" if limitations else "complete", "snapshots": snapshots,
            "facts": {"streamCount": len(streams), "keyEventCount": len(admin["keyEvents"]["items"]), "sitePublicIds": site["publicIds"], "correlation": correlation},
            "findings": findings, "limitations": limitations,
            "recommendations": [{"priority": 1, "message": "Review warnings and confirm the intended business outcomes before designing measurement changes.", "mutationRequired": False}],
            "questions": ([] if stream_name else ["Which returned web stream is the authoritative production website stream?"]),
            "requestEvidence": {"operations": executor.ledger, "authorizationHeaderStored": False},
            "partial": bool(limitations), "truncated": any(item["code"] in {"PAGINATION_LIMIT_REACHED", "SITE_SCAN_LIMIT_REACHED"} for item in limitations),
            "experimental": experimental_alpha,
        }
        location = store.write_audit(audit)
        return {"audit": audit, "artifact": location, "mutationPerformed": False}
