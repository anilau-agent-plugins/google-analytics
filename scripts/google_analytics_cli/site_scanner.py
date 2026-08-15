"""Bounded static website scan that never executes project code or follows links."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any

from .errors import AdvisorError, EXIT_INPUT


EXCLUDED_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "vendor", ".venv", "venv", "dist", "build",
    "coverage", ".coverage", ".cache", "cache", "logs", "log", "generated", "__pycache__",
    ".next", ".nuxt",
    ".google-analytics-advisor",
}
BLOCKED_NAMES = {
    ".env", ".env.local", ".env.production", ".npmrc", ".pypirc", "credentials.json",
    "service-account.json", "client_secret.json", "id_rsa", "id_ed25519",
}
BLOCKED_NAME_PATTERN = re.compile(r"(?:secret|credential|service[-_]?account|private[-_]?key|oauth|token)", re.I)
TEXT_EXTENSIONS = {
    ".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".vue", ".php", ".twig", ".blade.php",
    ".py", ".rb", ".go", ".java", ".cs", ".json", ".yaml", ".yml", ".md",
}
MAX_FILES = 10_000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024

PATTERNS = {
    "gtag_loader": re.compile(r"googletagmanager\.com/gtag/js", re.I),
    "gtm_loader": re.compile(r"googletagmanager\.com/gtm\.js", re.I),
    "gtm_noscript": re.compile(r"googletagmanager\.com/ns\.html", re.I),
    "gtag_config": re.compile(r"\bgtag\s*\(\s*['\"]config['\"]", re.I),
    "data_layer": re.compile(r"\bdataLayer\b"),
    "consent_default": re.compile(r"['\"]consent['\"]\s*,\s*['\"]default['\"]", re.I),
    "consent_update": re.compile(r"['\"]consent['\"]\s*,\s*['\"]update['\"]", re.I),
    "measurement_protocol": re.compile(r"google-analytics\.com/(?:mp/collect|g/collect)", re.I),
}
ID_PATTERN = re.compile(r"\b(?:GTM-[A-Z0-9]+|G-[A-Z0-9]+|GT-[A-Z0-9]+)\b", re.I)
CONSENT_SIGNALS = ("analytics_storage", "ad_storage", "ad_user_data", "ad_personalization")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse)


def _classification(relative: Path) -> str:
    parts = {part.lower() for part in relative.parts}
    if parts & {"test", "tests", "spec", "specs", "docs", "documentation", "examples", "fixtures"}:
        return "non-runtime"
    return "runtime"


def _supported(path: Path) -> bool:
    lower = path.name.lower()
    return lower.endswith(".blade.php") or path.suffix.lower() in TEXT_EXTENSIONS


def inspect_site(project_root: Path) -> dict[str, Any]:
    if not project_root.is_absolute() or not project_root.exists() or not project_root.is_dir() or _is_link_or_reparse(project_root):
        raise AdvisorError("SITE_SCAN_ROOT_INVALID", "Site project root must be an existing absolute directory.", EXIT_INPUT)
    root = project_root.resolve()
    evidence: list[dict[str, Any]] = []
    scanned_files = 0
    scanned_bytes = 0
    skipped = {"linked": 0, "blocked": 0, "binaryOrUnsupported": 0, "oversized": 0}
    truncated = False

    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_dirs = []
        for name in sorted(dirs):
            candidate = current_path / name
            if name.lower() in EXCLUDED_DIRS:
                continue
            if _is_link_or_reparse(candidate):
                skipped["linked"] += 1
                continue
            safe_dirs.append(name)
        dirs[:] = safe_dirs
        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root)
            blocked_name = name.lower() in BLOCKED_NAMES or name.lower().startswith(".env") or bool(BLOCKED_NAME_PATTERN.search(name)) or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
            if blocked_name or _is_link_or_reparse(path):
                skipped["blocked" if blocked_name else "linked"] += 1
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                skipped["linked"] += 1
                continue
            if root not in resolved.parents:
                skipped["linked"] += 1
                continue
            if not _supported(path):
                skipped["binaryOrUnsupported"] += 1
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                skipped["oversized"] += 1
                continue
            if scanned_files >= MAX_FILES or scanned_bytes + size > MAX_TOTAL_BYTES:
                truncated = True
                break
            try:
                raw = path.read_bytes()
                if b"\x00" in raw[:8192]:
                    skipped["binaryOrUnsupported"] += 1
                    continue
                text = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                skipped["binaryOrUnsupported"] += 1
                continue
            scanned_files += 1
            scanned_bytes += len(raw)
            kind = _classification(relative)
            digest = hashlib.sha256(raw).hexdigest()
            for line_number, line in enumerate(text.splitlines(), 1):
                matches: list[tuple[str, str | None]] = []
                for evidence_kind, pattern in PATTERNS.items():
                    if pattern.search(line):
                        matches.append((evidence_kind, None))
                for public_id in sorted(set(ID_PATTERN.findall(line.upper()))):
                    matches.append(("public_google_id", public_id))
                for signal in CONSENT_SIGNALS:
                    if signal in line:
                        matches.append(("consent_signal", signal))
                for evidence_kind, public_id in matches:
                    evidence.append({
                        "path": relative.as_posix(), "line": line_number, "kind": evidence_kind,
                        "publicId": public_id, "fileSha256": digest, "classification": kind,
                    })
        if truncated:
            break

    runtime = [item for item in evidence if item["classification"] == "runtime"]
    ids = sorted({item["publicId"] for item in runtime if item["kind"] == "public_google_id" and item["publicId"]})
    kinds = [item["kind"] for item in runtime]
    ga_ids = [item for item in ids if item.startswith(("G-", "GT-"))]
    gtm_ids = [item for item in ids if item.startswith("GTM-")]
    findings: list[dict[str, Any]] = []
    if len(ga_ids) > 1 or len(gtm_ids) > 1:
        findings.append({"code": "MULTIPLE_PUBLIC_IDS", "severity": "warning", "ids": ids})
    id_paths: dict[str, set[str]] = {}
    for item in runtime:
        if item["kind"] == "public_google_id" and item["publicId"]:
            id_paths.setdefault(item["publicId"], set()).add(item["path"])
    repeated_ids = sorted(public_id for public_id, paths in id_paths.items() if len(paths) > 1)
    if repeated_ids:
        findings.append({"code": "PUBLIC_ID_MULTIPLE_RUNTIME_FILES", "severity": "warning", "ids": repeated_ids})
    if kinds.count("gtag_loader") > 1 or kinds.count("gtm_loader") > 1:
        findings.append({"code": "DUPLICATE_TAG_LOADER", "severity": "warning", "ids": ids})
    if ga_ids and gtm_ids and "gtag_loader" in kinds and "gtm_loader" in kinds:
        findings.append({"code": "POSSIBLE_DOUBLE_COLLECTION", "severity": "warning", "ids": ids})
    if "consent_update" in kinds and "consent_default" not in kinds:
        findings.append({"code": "CONSENT_DEFAULT_NOT_FOUND", "severity": "warning", "ids": []})
    by_path: dict[str, list[dict[str, Any]]] = {}
    for item in runtime:
        by_path.setdefault(item["path"], []).append(item)
    if any(
        min(item["line"] for item in items if item["kind"] == "consent_default")
        > min(item["line"] for item in items if item["kind"] in {"gtag_loader", "gtm_loader"})
        for items in by_path.values()
        if any(item["kind"] == "consent_default" for item in items)
        and any(item["kind"] in {"gtag_loader", "gtm_loader"} for item in items)
    ):
        findings.append({"code": "CONSENT_DEFAULT_AFTER_LOADER", "severity": "warning", "ids": []})
    if "gtag_loader" in kinds and not ga_ids:
        findings.append({"code": "DYNAMIC_MEASUREMENT_ID", "severity": "manual_review", "ids": []})
    limitations = []
    if truncated:
        limitations.append({"code": "SITE_SCAN_LIMIT_REACHED", "message": "The bounded site scan stopped at its safety limit."})
    return {
        "projectRoot": str(root), "scannedFiles": scanned_files, "scannedBytes": scanned_bytes,
        "truncated": truncated, "evidence": evidence, "publicIds": ids, "findings": findings,
        "limitations": limitations, "skipped": skipped, "networkUsed": False, "codeExecuted": False,
    }
