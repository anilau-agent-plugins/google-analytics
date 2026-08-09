"""Dependency-free structural checks for the Stage 1 specification artifacts."""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLANNING = ROOT / "planning"
CONTRACTS = PLANNING / "contracts"

CONTRACT_NAMES = {
    "project-profile",
    "measurement-plan",
    "snapshot",
    "mutation-plan",
    "report",
    "journal-entry",
}

EXPECTED_SCOPES = {
    "openid",
    "email",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/analytics.edit",
    "https://www.googleapis.com/auth/tagmanager.readonly",
    "https://www.googleapis.com/auth/tagmanager.edit.containers",
    "https://www.googleapis.com/auth/tagmanager.edit.containerversions",
    "https://www.googleapis.com/auth/tagmanager.publish",
}

REQUIRED_DOCS = {
    ROOT / "DEVELOPMENT_PLAN.md",
    PLANNING / "product-spec.md",
    PLANNING / "api-capability-matrix.md",
    PLANNING / "artifact-contracts.md",
    PLANNING / "security-and-safety.md",
    PLANNING / "commercial-license-draft.md",
}

FORBIDDEN_STAGE_1_PATHS = {
    ROOT / ".codex-plugin" / "plugin.json",
    ROOT / ".claude-plugin" / "plugin.json",
    ROOT / "skills",
    ROOT / "scripts",
}

SECRET_PATTERNS = {
    "google_access_token": re.compile(r"\bya29\.[A-Za-z0-9._-]+"),
    "google_refresh_token": re.compile(r"\b1//[A-Za-z0-9._-]+"),
    "google_client_secret": re.compile(r"\bGOCSPX-[A-Za-z0-9_-]+"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |)PRIVATE KEY-----"),
}

SECRET_KEY_PATTERN = re.compile(
    r"(?:secret|token|password|credentials?|private[_-]?key)(?:value)?$", re.IGNORECASE
)


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def load_json(path: Path, errors: list[str]) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(errors, f"invalid JSON {path.relative_to(ROOT)}: {exc}")
        return None


def scope_set(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = set(re.findall(r"`(openid|email|https://www\.googleapis\.com/auth/[a-z.]+)`", text))
    return found


def scan_secret_keys(value: object, location: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEY_PATTERN.search(key):
                fail(errors, f"secret-bearing key in valid fixture at {location}.{key}")
            scan_secret_keys(child, f"{location}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_secret_keys(child, f"{location}[{index}]", errors)


def resource_is_covered(resource: str, precondition: str) -> bool:
    return resource == precondition or resource.startswith(precondition.rstrip("/") + "/")


def validate_semantics(name: str, data: dict[str, object], errors: list[str]) -> None:
    if name == "mutation-plan":
        generated = datetime.fromisoformat(str(data["generatedAt"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(data["expiresAt"]).replace("Z", "+00:00"))
        if generated >= expires:
            fail(errors, "mutation-plan expiresAt must be later than generatedAt")
        preconditions = [str(item["resource"]) for item in data["preconditions"]]  # type: ignore[index]
        for operation in data["operations"]:  # type: ignore[union-attr]
            resource = str(operation["resource"])
            if not any(resource_is_covered(resource, guarded) for guarded in preconditions):
                fail(errors, f"mutation-plan operation lacks covering precondition: {resource}")
    elif name == "report":
        for period in data["periods"]:  # type: ignore[union-attr]
            if date.fromisoformat(str(period["from"])) > date.fromisoformat(str(period["to"])):
                fail(errors, f"report period starts after it ends: {period['label']}")
    elif name == "measurement-plan":
        ecommerce = data["ecommerce"]  # type: ignore[assignment]
        if ecommerce["enabled"]:
            for field in ("currencySource", "valueRule", "transactionIdSource"):
                if not ecommerce[field]:
                    fail(errors, f"enabled ecommerce requires {field}")
            event_names = {item["name"] for item in data["events"]}  # type: ignore[union-attr]
            if "purchase" not in event_names:
                fail(errors, "enabled ecommerce requires a purchase event")


def main() -> int:
    errors: list[str] = []

    for path in sorted(REQUIRED_DOCS):
        if not path.is_file():
            fail(errors, f"missing required document: {path.relative_to(ROOT)}")

    for path in sorted(FORBIDDEN_STAGE_1_PATHS):
        if path.exists():
            fail(errors, f"Stage 2 artifact exists too early: {path.relative_to(ROOT)}")

    schema_names = {path.name.removesuffix(".schema.json") for path in CONTRACTS.glob("*.schema.json")}
    if schema_names != CONTRACT_NAMES:
        fail(errors, f"schema set mismatch: {sorted(schema_names)}")

    for fixture_kind in ("valid", "invalid"):
        fixture_root = CONTRACTS / "fixtures" / fixture_kind
        fixture_names = {path.stem for path in fixture_root.glob("*.json")}
        if fixture_names != CONTRACT_NAMES:
            fail(errors, f"{fixture_kind} fixture set mismatch: {sorted(fixture_names)}")

    for path in sorted(CONTRACTS.rglob("*.json")):
        data = load_json(path, errors)
        if path.name.endswith(".schema.json") and isinstance(data, dict):
            schema_id = data.get("$id")
            if not isinstance(schema_id, str) or not schema_id.startswith("urn:anilau:google-analytics:"):
                fail(errors, f"non-local schema id in {path.relative_to(ROOT)}")

    for name in sorted(CONTRACT_NAMES):
        path = CONTRACTS / "fixtures" / "valid" / f"{name}.json"
        data = load_json(path, errors)
        if isinstance(data, dict):
            scan_secret_keys(data, name, errors)
            try:
                validate_semantics(name, data, errors)
            except (KeyError, TypeError, ValueError) as exc:
                fail(errors, f"semantic validation failed for {name}: {exc}")

    for path in (PLANNING / "api-capability-matrix.md", PLANNING / "security-and-safety.md"):
        if path.is_file():
            found = scope_set(path)
            if found != EXPECTED_SCOPES:
                fail(errors, f"OAuth scope mismatch in {path.name}: {sorted(found)}")

    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".json", ".py"}:
            continue
        text = path.read_text(encoding="utf-8")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                fail(errors, f"possible {label} in {path.relative_to(ROOT)}")

    result = {
        "ok": not errors,
        "stage": 1,
        "documents": len(REQUIRED_DOCS),
        "schemas": len(CONTRACT_NAMES),
        "positiveFixtures": len(CONTRACT_NAMES),
        "negativeFixtures": len(CONTRACT_NAMES),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
