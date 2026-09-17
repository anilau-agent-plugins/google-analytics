"""Small fail-closed validator for the project's Draft 2020-12 schema subset."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .errors import AdvisorError, EXIT_INPUT
from .paths import source_root


ARTIFACTS = {
    "project-profile", "measurement-plan", "snapshot", "mutation-plan", "report",
    "journal-entry", "baseline-report", "ga4-change-request", "website-context",
    "website-change-request", "mp-delivery-plan", "gtm-context", "gtm-change-request",
    "report-request", "report-plan",
    "search-console-report-request", "search-console-report-plan", "search-console-report",
    "search-console-sitemap-snapshot", "search-console-inspection-request",
    "search-console-inspection-plan", "search-console-inspection-report",
    "cross-source-analysis-request", "cross-source-analysis-plan", "cross-source-analysis-report",
}
ALLOWED = {
    "$schema", "$id", "$defs", "$ref", "title", "type", "const", "enum", "required",
    "properties", "propertyNames", "additionalProperties", "items", "minItems", "maxItems", "uniqueItems",
    "minLength", "minimum", "maximum", "pattern", "format", "oneOf", "allOf", "if", "then",
}
SECRET_KEY = re.compile(r"(?:secret|token|password|credentials?|private[_-]?key)(?:value)?$", re.I)


class ValidationFailure(ValueError):
    pass


def _fail(path: str, message: str) -> None:
    raise ValidationFailure(f"{path}: {message}")


def _check_schema_keywords(schema: Any, path: str = "$") -> None:
    if not isinstance(schema, dict):
        return
    unknown = set(schema) - ALLOWED
    if unknown:
        _fail(path, f"unsupported schema keyword(s): {', '.join(sorted(unknown))}")
    if "$schema" in schema and schema["$schema"] != "https://json-schema.org/draft/2020-12/schema":
        _fail(path, "unexpected JSON Schema dialect")
    if "$id" in schema and not re.fullmatch(r"urn:anilau:google-analytics:[a-z0-9-]+:v[0-9]+", str(schema["$id"])):
        _fail(path, "schema id is not an allowed local URN")
    for key in ("properties", "$defs"):
        for name, child in schema.get(key, {}).items():
            _check_schema_keywords(child, f"{path}.{key}.{name}")
    for key in ("items", "propertyNames", "additionalProperties", "if", "then"):
        child = schema.get(key)
        if isinstance(child, dict):
            _check_schema_keywords(child, f"{path}.{key}")
    for key in ("oneOf", "allOf"):
        for index, child in enumerate(schema.get(key, [])):
            _check_schema_keywords(child, f"{path}.{key}[{index}]")


def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise ValidationFailure(f"unsupported non-local $ref: {ref}")
    current: Any = root
    for segment in ref[2:].split("/"):
        segment = segment.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or segment not in current:
            raise ValidationFailure(f"unresolved $ref: {ref}")
        current = current[segment]
    if not isinstance(current, dict):
        raise ValidationFailure(f"$ref does not resolve to a schema: {ref}")
    return current


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, False)


def _validate(schema: dict[str, Any], value: Any, root: dict[str, Any], path: str = "$") -> None:
    if "$ref" in schema:
        _validate(_resolve_ref(root, schema["$ref"]), value, root, path)
    expected = schema.get("type")
    if expected is not None:
        allowed = [expected] if isinstance(expected, str) else expected
        if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
            _fail(path, "invalid type declaration")
        if not any(_matches_type(value, item) for item in allowed):
            _fail(path, f"expected type {allowed}")
    if "const" in schema and value != schema["const"]:
        _fail(path, f"expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        _fail(path, "value is not in enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            _fail(path, "string is shorter than minLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            _fail(path, "string does not match pattern")
        fmt = schema.get("format")
        try:
            if fmt == "date":
                date.fromisoformat(value)
            elif fmt == "date-time":
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    _fail(path, "date-time must include a timezone")
            elif fmt is not None:
                _fail(path, f"unsupported format {fmt}")
        except ValueError:
            _fail(path, f"invalid {fmt}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
        _fail(path, "number is below minimum")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "maximum" in schema and value > schema["maximum"]:
        _fail(path, "number is above maximum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            _fail(path, "array is shorter than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            _fail(path, "array is longer than maxItems")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                _fail(path, "array items are not unique")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                _validate(schema["items"], item, root, f"{path}[{index}]")
    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                _fail(path, f"missing required property {required}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if isinstance(schema.get("propertyNames"), dict):
                _validate(schema["propertyNames"], key, root, f"{path}.<key>")
            if key in properties:
                _validate(properties[key], item, root, f"{path}.{key}")
            else:
                additional = schema.get("additionalProperties", True)
                if additional is False:
                    _fail(path, f"unexpected property {key}")
                if isinstance(additional, dict):
                    _validate(additional, item, root, f"{path}.{key}")
    for child in schema.get("allOf", []):
        _validate(child, value, root, path)
    if "oneOf" in schema:
        matches = 0
        for child in schema["oneOf"]:
            try:
                _validate(child, value, root, path)
                matches += 1
            except ValidationFailure:
                pass
        if matches != 1:
            _fail(path, f"oneOf matched {matches} branches")
    if "if" in schema:
        try:
            _validate(schema["if"], value, root, path)
        except ValidationFailure:
            pass
        else:
            if "then" in schema:
                _validate(schema["then"], value, root, path)


def _scan_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEY.search(str(key)):
                _fail(path, f"secret-bearing property {key}")
            _scan_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_secrets(child, f"{path}[{index}]")


def _semantics(name: str, data: dict[str, Any]) -> None:
    _scan_secrets(data)
    discriminator = data.get("changeRequestType") if name == "ga4-change-request" else data.get("artifactType")
    if discriminator != name:
        _fail("$.changeRequestType" if name == "ga4-change-request" else "$.artifactType", f"does not match requested schema {name}")
    if name in {"report-request", "search-console-report-request", "search-console-inspection-request", "cross-source-analysis-request"}:
        from .artifact_store import canonical_json

        expected = hashlib.sha256(canonical_json({key: value for key, value in data.items() if key != "contentSha256"})).hexdigest()
        if data["contentSha256"] != expected:
            _fail("$.contentSha256", "does not match canonical request content")
        if name == "cross-source-analysis-request":
            for label, period in data["expectedPeriods"].items():
                if date.fromisoformat(period["from"]) > date.fromisoformat(period["to"]):
                    _fail(f"$.expectedPeriods.{label}", "period starts after it ends")
            if not data["webStream"].startswith(data["property"] + "/dataStreams/"):
                _fail("$.webStream", "must belong to the selected property")
            return
        if name == "search-console-inspection-request":
            if data["requestedBudget"]["maxUrls"] != len(data["urls"]):
                _fail("$.requestedBudget.maxUrls", "must equal the exact selected URL count")
            urls = [item["url"] for item in data["urls"]]
            if len(urls) != len(set(urls)):
                _fail("$.urls", "duplicate exact URLs are not allowed")
            return
        period = data["period"]
        if period["mode"] == "last-complete-days" and (period.get("days") is None or period.get("from") is not None or period.get("to") is not None):
            _fail("$.period", "last-complete-days requires days only")
        if period["mode"] == "explicit" and (period.get("days") is not None or not period.get("from") or not period.get("to")):
            _fail("$.period", "explicit requires from/to only")
        if name == "report-request":
            if "custom-core" in data["presets"] and not data.get("customCore"):
                _fail("$.customCore", "is required by custom-core")
            if "custom-core" not in data["presets"] and data.get("customCore") is not None:
                _fail("$.customCore", "is allowed only with custom-core")
            organic = any(item.startswith("google-organic-") for item in data["presets"])
            if organic and not data.get("webStream"):
                _fail("$.webStream", "is required by google-organic presets")
            if not organic and data.get("webStream") is not None:
                _fail("$.webStream", "is allowed only with google-organic presets")
        else:
            if data["dataState"] == "hourly_all" and data["presets"] != ["recent-hourly"]:
                _fail("$.presets", "hourly_all requires only recent-hourly")
            if data["dataState"] != "hourly_all" and "recent-hourly" in data["presets"]:
                _fail("$.dataState", "recent-hourly requires hourly_all")
            if data["searchType"] in {"discover", "googleNews"} and any(
                item["dimension"] == "query" for item in data.get("filters", [])
            ):
                _fail("$.filters", "query filters are unavailable for this search type")
    elif name in {"report-plan", "search-console-report-plan", "search-console-inspection-plan", "cross-source-analysis-plan"}:
        from .artifact_store import canonical_json

        generated = datetime.fromisoformat(data["generatedAt"].replace("Z", "+00:00"))
        if name != "cross-source-analysis-plan":
            expires = datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00"))
            if generated >= expires:
                _fail("$.expiresAt", "must be later than generatedAt")
        expected = hashlib.sha256(canonical_json({key: value for key, value in data.items() if key != "planSha256"})).hexdigest()
        if data["planSha256"] != expected:
            _fail("$.planSha256", "does not match canonical plan content")
        if data.get("mutationPerformed") is not False:
            _fail("$.mutationPerformed", "report plans are read-only")
        if name != "cross-source-analysis-plan":
            query_field = "operations" if name == "search-console-inspection-plan" else "queries"
            if not data.get(query_field) and not data.get("blockers"):
                _fail("$.queries", "an unblocked report plan requires at least one query")
        elif data.get("networkUsed") is not False:
            _fail("$.networkUsed", "cross-source analysis plans must remain local")
        if name == "search-console-report-plan":
            budget = data.get("budget", {})
            if budget.get("maxRequests", 0) > 20 or budget.get("maxRows", 0) > 12000:
                _fail("$.budget", "exceeds the Search Console product budget")
            for index, query in enumerate(data.get("queries", [])):
                if query.get("operationId") != "searchconsole.searchanalytics.query":
                    _fail(f"$.queries[{index}].operationId", "is not an allowed Search Console read")
                if query.get("rowLimit", 0) > 1000 or query.get("maxPages", 0) > 2:
                    _fail(f"$.queries[{index}]", "exceeds the per-query product budget")
        if name == "search-console-inspection-plan":
            if data.get("singleUse") is not True or data.get("inspectionDataRead") is not False:
                _fail("$", "inspection plans must be single-use and cannot contain inspection data")
            if data.get("budget", {}).get("maxUrlCalls") != 10 or data.get("budget", {}).get("automaticRetries") != 0:
                _fail("$.budget", "inspection plan must preserve the 10-call hard limit and zero retries")
            if data.get("budget", {}).get("plannedUrlCalls") != len(data.get("operations", [])):
                _fail("$.budget.plannedUrlCalls", "must equal the exact operation count")
            for index, operation in enumerate(data.get("operations", [])):
                if operation.get("operationId") != "searchconsole.urlinspection.inspect":
                    _fail(f"$.operations[{index}].operationId", "is not the allowed URL Inspection read")
    elif name == "mutation-plan":
        generated = datetime.fromisoformat(data["generatedAt"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00"))
        if generated >= expires:
            _fail("$.expiresAt", "must be later than generatedAt")
        guarded = [item["resource"].rstrip("/") for item in data["preconditions"]]
        for index, operation in enumerate(data["operations"]):
            resource = operation["resource"]
            if not any(resource == item or resource.startswith(item + "/") for item in guarded):
                _fail(f"$.operations[{index}].resource", "lacks a covering precondition")
        if data.get("schemaVersion") == 3 and data.get("target") != "website":
            _fail("$.target", "mutation-plan v3 is reserved for website changes")
        if data.get("schemaVersion") == 4 and data.get("target") != "tag-manager":
            _fail("$.target", "mutation-plan v4 is reserved for Tag Manager changes")
    elif name == "gtm-context":
        generated = datetime.fromisoformat(data["generatedAt"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00"))
        if generated >= expires:
            _fail("$.expiresAt", "must be later than generatedAt")
    elif name == "gtm-change-request":
        stage = data["stage"]
        required = {
            "WORKSPACE_CREATE": ("workspaceName",),
            "WORKSPACE_SYNC": ("workspace",),
            "ENTITY_BULK_UPDATE": ("workspace", "entities"),
            "QUICK_PREVIEW": ("workspace",),
            "VERSION_CREATE": ("workspace", "evidenceJournal", "versionName"),
            "PUBLISH": ("version", "versionFingerprint", "evidenceJournal", "runtimeEvidence"),
        }[stage]
        missing = [key for key in required if not data.get(key)]
        if missing:
            _fail("$", f"{stage} requires: {', '.join(missing)}")
        if stage != "ENTITY_BULK_UPDATE" and "entities" in data:
            _fail("$.entities", "entities are allowed only for ENTITY_BULK_UPDATE")
    elif name == "website-context":
        if data.get("codeExecuted") is not False or data.get("networkUsed") is not False:
            _fail("$", "website context must be produced by local static inspection")
    elif name == "mp-delivery-plan":
        generated = datetime.fromisoformat(data["generatedAt"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00"))
        if generated >= expires:
            _fail("$.expiresAt", "must be later than generatedAt")
    elif name in {"search-console-report", "search-console-inspection-report", "cross-source-analysis-report"}:
        from .artifact_store import canonical_json

        expected = hashlib.sha256(canonical_json({key: value for key, value in data.items() if key != "reportSha256"})).hexdigest()
        if data["reportSha256"] != expected:
            _fail("$.reportSha256", "does not match canonical Search Console report content")
        if data.get("mutationPerformed") is not False:
            _fail("$.mutationPerformed", "Search Console reports are read-only")
        if name == "cross-source-analysis-report":
            if data.get("networkUsed") is not False:
                _fail("$.networkUsed", "cross-source analysis must remain local")
            for collection in ("facts", "calculations", "interpretations", "findings", "recommendations"):
                for index, item in enumerate(data.get(collection, [])):
                    if not item.get("evidenceRefs"):
                        _fail(f"$.{collection}[{index}].evidenceRefs", "must identify source-qualified evidence")
            if len(data.get("recommendations", [])) > 5:
                _fail("$.recommendations", "must contain at most five items")
        elif name == "search-console-report":
            for index, period in enumerate(data["periods"]):
                if date.fromisoformat(period["from"]) > date.fromisoformat(period["to"]):
                    _fail(f"$.periods[{index}]", "period starts after it ends")
            if data.get("schemaVersion") == 2:
                for dataset_index, dataset in enumerate(data.get("datasets", [])):
                    if "page" not in dataset.get("dimensions", []):
                        continue
                    for row_index, row in enumerate(dataset.get("rows", [])):
                        quality = row.get("dimensionQuality", {}).get("page")
                        if not isinstance(quality, dict):
                            _fail(f"$.datasets[{dataset_index}].rows[{row_index}].dimensionQuality.page", "is required by report v2 page rows")
                        required = {"queryPresent", "queryRemoved", "fragmentRemoved", "redacted", "normalizationRevision"}
                        if not required.issubset(quality):
                            _fail(f"$.datasets[{dataset_index}].rows[{row_index}].dimensionQuality.page", "is incomplete")
                        if quality["queryPresent"] not in {True, False, "unknown"}:
                            _fail(f"$.datasets[{dataset_index}].rows[{row_index}].dimensionQuality.page.queryPresent", "must be true, false, or unknown")
                        if not all(isinstance(quality[field], bool) for field in ("queryRemoved", "fragmentRemoved", "redacted")):
                            _fail(f"$.datasets[{dataset_index}].rows[{row_index}].dimensionQuality.page", "boolean quality fields are invalid")
                        if quality["normalizationRevision"] != "search-console-page-v1":
                            _fail(f"$.datasets[{dataset_index}].rows[{row_index}].dimensionQuality.page.normalizationRevision", "is unsupported")
        elif data.get("budget", {}).get("automaticRetries") != 0:
            _fail("$.budget.automaticRetries", "URL Inspection cannot retry automatically")
    elif name == "search-console-sitemap-snapshot":
        from .artifact_store import canonical_json

        expected = hashlib.sha256(canonical_json({key: value for key, value in data.items() if key != "snapshotSha256"})).hexdigest()
        if data["snapshotSha256"] != expected:
            _fail("$.snapshotSha256", "does not match canonical sitemap snapshot content")
        if data.get("mutationPerformed") is not False:
            _fail("$.mutationPerformed", "sitemap snapshots are read-only")
        if data["mode"] == "root-list" and (data.get("requestedSitemapIndex") is not None or data.get("requestedSitemap") is not None):
            _fail("$", "root-list cannot name a sitemap")
        if data["mode"] == "index-list" and (not data.get("requestedSitemapIndex") or data.get("requestedSitemap") is not None):
            _fail("$", "index-list requires only requestedSitemapIndex")
        if data["mode"] == "get" and (not data.get("requestedSitemap") or data.get("requestedSitemapIndex") is not None):
            _fail("$", "get requires only requestedSitemap")
    elif name == "report":
        for index, period in enumerate(data["periods"]):
            if date.fromisoformat(period["from"]) > date.fromisoformat(period["to"]):
                _fail(f"$.periods[{index}]", "period starts after it ends")
        if data.get("schemaVersion") == 1:
            allowed_limitations = {"sampling", "thresholding", "cardinality", "quota", "incomplete-period", "compatibility", "small-data", "other"}
            for index, item in enumerate(data.get("limitations", [])):
                if item.get("type") not in allowed_limitations:
                    _fail(f"$.limitations[{index}].type", "unknown report v1 limitation type")
        if data.get("schemaVersion") == 2:
            from .artifact_store import canonical_json

            for field in ("reportSha256", "profileId", "status", "qualityTier", "propertyContext", "datasets"):
                if field not in data:
                    _fail("$", f"report v2 requires {field}")
            expected = hashlib.sha256(canonical_json({key: value for key, value in data.items() if key != "reportSha256"})).hexdigest()
            if data["reportSha256"] != expected:
                _fail("$.reportSha256", "does not match canonical report content")
    elif name == "measurement-plan":
        if data["ecommerce"]["enabled"]:
            for field in ("currencySource", "valueRule", "transactionIdSource"):
                if not data["ecommerce"][field]:
                    _fail(f"$.ecommerce.{field}", "is required when ecommerce is enabled")
            if "purchase" not in {item["name"] for item in data["events"]}:
                _fail("$.events", "enabled ecommerce requires purchase")
        if data.get("schemaVersion") == 2:
            from .measurement_policy import evaluate_plan, pii_issues, plan_content_sha256

            if data["contentSha256"] != plan_content_sha256(data):
                _fail("$.contentSha256", "does not match canonical plan content")
            unsafe = pii_issues(data)
            if unsafe:
                _fail("$", f"PII-shaped content is prohibited: {'; '.join(unsafe)}")
            if data["status"] == "approved":
                evaluation = evaluate_plan(data)
                if evaluation["blockers"]:
                    _fail("$", f"approved plan has blockers: {'; '.join(evaluation['blockers'])}")
                if not data["approvedAt"] or not data["approvalSha256"]:
                    _fail("$", "approved plan requires approval evidence")


def validate_artifact_data(name: str, data: dict[str, Any], *, path_label: str = "<memory>") -> dict[str, Any]:
    if name not in ARTIFACTS:
        raise AdvisorError("UNKNOWN_ARTIFACT_TYPE", f"Unknown artifact type: {name}", EXIT_INPUT)
    try:
        schema = json.loads((source_root() / "contracts" / f"{name}.schema.json").read_text(encoding="utf-8"))
        if not isinstance(schema, dict) or not isinstance(data, dict):
            raise ValidationFailure("schema and artifact must be JSON objects")
        _check_schema_keywords(schema)
        _validate(schema, data, schema)
        _semantics(name, data)
    except (OSError, json.JSONDecodeError, ValidationFailure, KeyError, TypeError, ValueError) as exc:
        raise AdvisorError(
            "ARTIFACT_VALIDATION_FAILED",
            "Artifact validation failed.",
            EXIT_INPUT,
            details={"artifactType": name, "path": path_label, "reason": str(exc)},
            next_action="Correct the artifact and validate it again.",
        ) from exc
    return {"artifactType": name, "path": path_label, "valid": True}


def validate_artifact(name: str, input_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdvisorError(
            "ARTIFACT_VALIDATION_FAILED", "Artifact validation failed.", EXIT_INPUT,
            details={"artifactType": name, "path": str(input_path), "reason": str(exc)},
            next_action="Correct the artifact and validate it again.",
        ) from exc
    result = validate_artifact_data(name, data, path_label=str(input_path.resolve()))
    return result
