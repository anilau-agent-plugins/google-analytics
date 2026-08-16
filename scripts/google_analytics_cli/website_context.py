"""Static, bounded website context discovery for local installation planning."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .artifact_store import ArtifactStore, canonical_json
from .contracts import validate_artifact_data
from .errors import AdvisorError, EXIT_INPUT
from .measurement_policy import plan_content_sha256
from .site_scanner import EXCLUDED_DIRS, _is_link_or_reparse, inspect_site


MANIFESTS = {
    "package.json", "composer.json", "vite.config.js", "vite.config.ts", "next.config.js",
    "next.config.mjs", "nuxt.config.js", "nuxt.config.ts", "artisan", "wp-config.php",
}
LAYOUT_PATTERNS = (
    re.compile(r"(?:^|/)(?:index|app|layout|root)\.(?:html|php|blade\.php|jsx|tsx)$", re.I),
    re.compile(r"(?:^|/)layouts?/.*\.(?:html|php|blade\.php|twig|jsx|tsx)$", re.I),
)
ROUTER_PATTERNS = ("react-router", "next/navigation", "next/router", "vue-router", "history.pushstate")


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdvisorError("INVALID_INPUT_FILE", f"The {label} could not be read as JSON.", EXIT_INPUT) from exc
    if not isinstance(value, dict):
        raise AdvisorError("INVALID_INPUT_FILE", f"The {label} must be a JSON object.", EXIT_INPUT)
    return value


def _approved_measurement(path: Path, root: Path) -> dict[str, Any]:
    value = _load(path, "measurement plan")
    validate_artifact_data("measurement-plan", value, path_label=str(path))
    if value.get("schemaVersion") != 2 or value.get("status") != "approved":
        raise AdvisorError("MEASUREMENT_PLAN_NOT_APPROVED", "Website changes require an approved measurement-plan v2.", EXIT_INPUT)
    if value.get("contentSha256") != plan_content_sha256(value) or value.get("approvalSha256") != value.get("contentSha256"):
        raise AdvisorError("MEASUREMENT_PLAN_TAMPERED", "The approved measurement plan hash is invalid.", EXIT_INPUT)
    site = Path(str(value.get("site", ""))).expanduser()
    if site.is_absolute() and site.resolve() != root:
        raise AdvisorError("PROJECT_ROOT_MISMATCH", "The measurement plan belongs to another project root.", EXIT_INPUT)
    return value


def _project_evidence(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, list[str]]:
    manifests: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    hashes: list[tuple[str, str]] = []
    limitations: list[str] = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = [name for name in sorted(dirs) if name.lower() not in EXCLUDED_DIRS and not _is_link_or_reparse(current_path / name)]
        for name in sorted(files):
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            if _is_link_or_reparse(path):
                continue
            lower = name.lower()
            interesting = lower in MANIFESTS or any(pattern.search(rel) for pattern in LAYOUT_PATTERNS)
            if not interesting:
                continue
            try:
                raw = path.read_bytes()
                if len(raw) > 2 * 1024 * 1024 or b"\x00" in raw[:8192]:
                    limitations.append(f"Unsupported or oversized integration candidate: {rel}")
                    continue
                text = raw.decode("utf-8-sig")
            except (OSError, UnicodeDecodeError):
                limitations.append(f"Non-UTF-8 integration candidate: {rel}")
                continue
            digest = hashlib.sha256(raw).hexdigest()
            hashes.append((rel, digest))
            if lower in MANIFESTS:
                manifests.append({"path": rel, "sha256": digest})
            if any(pattern.search(rel) for pattern in LAYOUT_PATTERNS):
                candidates.append({
                    "path": rel, "sha256": digest, "hasHead": "<head" in text.lower(),
                    "hasBody": "<body" in text.lower(), "routerEvidence": [item for item in ROUTER_PATTERNS if item in text.lower()],
                })
    content_sha = hashlib.sha256(canonical_json(hashes)).hexdigest()
    return manifests, candidates, content_sha, sorted(set(limitations))


def _stack(manifests: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    names = {item["path"].lower() for item in manifests}
    candidate_names = {item["path"].lower() for item in candidates}
    frameworks: list[str] = []
    if "artisan" in names or "composer.json" in names and any(".blade.php" in item for item in candidate_names):
        frameworks.append("laravel")
    if any(item.endswith(("next.config.js", "next.config.mjs")) for item in names) or any("app/layout.tsx" in item for item in candidate_names):
        frameworks.append("nextjs-app-router")
    if any(item.endswith(("vite.config.js", "vite.config.ts")) for item in names):
        frameworks.append("vite")
    if any(item.endswith(("nuxt.config.js", "nuxt.config.ts")) for item in names):
        frameworks.append("nuxt")
    if "wp-config.php" in names:
        frameworks.append("wordpress")
    if not frameworks and any(item.endswith((".html", ".htm")) for item in candidate_names):
        frameworks.append("static-html")
    rendering = "spa" if any(item["routerEvidence"] for item in candidates) or "nextjs-app-router" in frameworks else "multipage"
    return {"frameworks": frameworks or ["unknown"], "renderingModel": rendering, "manifests": manifests}


def build_context(project_root: Path, measurement_plan: Path, baseline: Path | None = None) -> dict[str, Any]:
    if not project_root.is_absolute() or not project_root.exists() or not project_root.is_dir() or _is_link_or_reparse(project_root):
        raise AdvisorError("SITE_SCAN_ROOT_INVALID", "Site project root must be an existing non-linked absolute directory.", EXIT_INPUT)
    root = project_root.resolve()
    measurement_path = measurement_plan.resolve()
    measurement = _approved_measurement(measurement_path, root)
    baseline_value = _load(baseline.resolve(), "baseline") if baseline else None
    scan = inspect_site(root)
    manifests, candidates, content_sha, limitations = _project_evidence(root)
    frameworks = _stack(manifests, candidates)
    ids = scan["publicIds"]
    direct_ids = [item for item in ids if item.startswith(("G-", "GT-"))]
    gtm_ids = [item for item in ids if item.startswith("GTM-")]
    blockers: list[str] = []
    questions: list[str] = []
    if direct_ids and gtm_ids:
        blockers.append("Both direct Google tag and GTM routes are present; choose one authoritative route before applying changes.")
    if scan["truncated"]:
        blockers.append("The static scan was truncated, so integration safety cannot be established.")
    consent = measurement.get("consent", {})
    if not consent.get("policyConfirmed") or consent.get("mode") == "unresolved":
        blockers.append("Consent policy is unresolved; the plugin will not invent defaults or regions.")
    if frameworks["renderingModel"] == "spa":
        questions.append("Confirm that only one SPA page-view strategy will own virtual pageviews.")
    if not candidates:
        blockers.append("No safe shared layout or application integration point was detected.")
    if "wordpress" in frameworks["frameworks"] or "nuxt" in frameworks["frameworks"]:
        limitations.append("This stack is detected but is not an automated Stage 8 acceptance fixture.")
    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    context_id = f"site-context-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
    value: dict[str, Any] = {
        "schemaVersion": 1, "artifactType": "website-context", "generatedAt": generated,
        "contextId": context_id, "contextSha256": "", "projectRoot": str(root),
        "projectContentSha256": content_sha,
        "measurementPlan": {"path": str(measurement_path), "planId": measurement["planId"], "contentSha256": measurement["contentSha256"]},
        "baseline": None if baseline is None else {"path": str(baseline.resolve()), "sha256": hashlib.sha256(baseline.resolve().read_bytes()).hexdigest()},
        "stack": frameworks, "integrationCandidates": candidates,
        "analytics": {
            "publicIds": ids, "directIds": direct_ids, "gtmIds": gtm_ids, "findings": scan["findings"],
            "directLoaderPaths": sorted({item["path"] for item in scan["evidence"] if item["classification"] == "runtime" and item["kind"] == "gtag_loader"}),
            "gtmLoaderPaths": sorted({item["path"] for item in scan["evidence"] if item["classification"] == "runtime" and item["kind"] == "gtm_loader"}),
        },
        "policy": {"consentConfirmed": bool(consent.get("policyConfirmed")), "consentMode": consent.get("mode"), "productionDeployApproved": False},
        "blockers": sorted(set(blockers)), "limitations": sorted(set(limitations + [item["message"] for item in scan["limitations"]])),
        "questions": sorted(set(questions)), "codeExecuted": False, "networkUsed": False,
    }
    hash_input = dict(value)
    hash_input["contextSha256"] = ""
    value["contextSha256"] = hashlib.sha256(canonical_json(hash_input)).hexdigest()
    validate_artifact_data("website-context", value)
    location = ArtifactStore(root).write_named_artifact("website-contexts", context_id, value)
    return {"status": "blocked" if blockers else "ready", "context": value, "artifact": location, "mutationPerformed": False}


def load_context(path: Path) -> dict[str, Any]:
    value = _load(path.resolve(), "website context")
    validate_artifact_data("website-context", value, path_label=str(path))
    check = dict(value)
    actual = check.pop("contextSha256")
    check["contextSha256"] = ""
    if actual != hashlib.sha256(canonical_json(check)).hexdigest():
        raise AdvisorError("WEBSITE_CONTEXT_TAMPERED", "The website context SHA-256 does not match its content.", EXIT_INPUT)
    return value
