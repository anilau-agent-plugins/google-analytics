"""Closed web-only GTM entity template registry for Stage 9."""

from __future__ import annotations

import re
from typing import Any

from .errors import AdvisorError, EXIT_INPUT
from .measurement_policy import pii_issues


MEASUREMENT_ID = re.compile(r"^(?:G|GT)-[A-Z0-9]+$")
EVENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
DATA_LAYER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,127}$")
ENTITY_PATH = re.compile(r"^accounts/[A-Za-z0-9_-]+/containers/[A-Za-z0-9_-]+/workspaces/[A-Za-z0-9_-]+/(?:tags|triggers|variables)/[A-Za-z0-9_-]+$")
SUPPORTED = {
    "data-layer-variable": "variable",
    "custom-event-trigger": "trigger",
    "page-view-trigger": "trigger",
    "history-change-trigger": "trigger",
    "google-tag": "tag",
    "ga4-event-tag": "tag",
}


def _fail(code: str, message: str, **details: Any) -> None:
    raise AdvisorError(code, message, EXIT_INPUT, details=details)


def _template(key: str, value: str) -> dict[str, str]:
    return {"key": key, "type": "template", "value": value}


def _list_parameter(key: str, values: list[str]) -> dict[str, Any]:
    return {"key": key, "type": "list", "list": [{"type": "template", "value": value} for value in values]}


def _condition(event_name: str) -> dict[str, Any]:
    return {
        "type": "equals",
        "parameter": [_template("arg0", "{{_event}}"), _template("arg1", event_name)],
    }


def _require_string(settings: dict[str, Any], key: str, pattern: re.Pattern[str] | None = None) -> str:
    value = settings.get(key)
    if not isinstance(value, str) or not value or (pattern and not pattern.fullmatch(value)):
        _fail("INVALID_GTM_TEMPLATE_SETTING", f"The GTM template setting {key} is invalid.", setting=key)
    return value


def _string_list(settings: dict[str, Any], key: str) -> list[str]:
    value = settings.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value) or len(value) != len(set(value)):
        _fail("INVALID_GTM_TEMPLATE_SETTING", f"The GTM template setting {key} must be a unique string list.", setting=key)
    return value


def _consent(settings: dict[str, Any]) -> dict[str, Any]:
    values = _string_list(settings, "consentTypes")
    allowed = {"analytics_storage", "ad_storage", "ad_user_data", "ad_personalization"}
    if any(value not in allowed for value in values):
        _fail("INVALID_GTM_CONSENT", "Only Consent Mode v2 consent types are supported.")
    if not values:
        return {"consentStatus": "notSet"}
    return {"consentStatus": "needed", "consentType": _list_parameter("consentType", values)}


def _entity_id(kind: str, index: int) -> tuple[str, str]:
    return {"variable": ("variable", "variableId"), "trigger": ("trigger", "triggerId"), "tag": ("tag", "tagId")}[kind][1], f"new_{index}"


def build_entities(items: list[dict[str, Any]], measurement_plan: dict[str, Any], current_state: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        _fail("INVALID_GTM_ENTITY_SET", "A GTM bulk update needs 1 to 100 supported entities.")
    names = [item.get("name") for item in items if isinstance(item, dict)]
    if len(names) != len(items) or len(names) != len(set(names)):
        _fail("DUPLICATE_GTM_ENTITY_NAME", "Every entity in one GTM plan needs a unique name.")
    aliases: dict[str, str] = {}
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            _fail("INVALID_GTM_ENTITY", "Every GTM entity must be an object.")
        template = str(item.get("template", ""))
        kind = str(item.get("entityKind", ""))
        action = str(item.get("action", ""))
        if SUPPORTED.get(template) != kind or action not in {"create", "update", "pause"}:
            _fail("GTM_TEMPLATE_NOT_ALLOWED", "The requested GTM entity template or action is not supported.", template=template, action=action)
        if action == "pause" and kind != "tag":
            _fail("GTM_TEMPLATE_NOT_ALLOWED", "Only a tag can be paused in Stage 9.")
        aliases[str(item["name"])] = f"new_{index}" if action == "create" else str(item.get("entityPath", "")).rsplit("/", 1)[-1]

    planned_events = {str(event.get("name")) for event in measurement_plan.get("events", []) if isinstance(event, dict)}
    changes: list[dict[str, Any]] = []
    summary: list[dict[str, str]] = []
    for index, item in enumerate(items, 1):
        action, kind, template = str(item["action"]), str(item["entityKind"]), str(item["template"])
        name = str(item["name"])
        settings = item.get("settings")
        if not isinstance(settings, dict) or pii_issues(settings):
            _fail("PII_BLOCKED", "Personal or sensitive values are forbidden in GTM configuration artifacts.")
        allowed_settings = {
            "data-layer-variable": {"dataLayerName"},
            "custom-event-trigger": {"eventName"},
            "page-view-trigger": set(),
            "history-change-trigger": set(),
            "google-tag": {"measurementId", "firingTriggers", "blockingTriggers", "consentTypes", "paused"},
            "ga4-event-tag": {"measurementId", "eventName", "eventParameters", "firingTriggers", "blockingTriggers", "consentTypes", "paused"},
        }[template]
        if set(settings) - allowed_settings:
            _fail("INVALID_GTM_TEMPLATE_SETTING", "The GTM template contains unsupported settings.", settings=sorted(set(settings) - allowed_settings))
        collection_name = {"variable": "variables", "trigger": "triggers", "tag": "tags"}[kind]
        current_items = (current_state or {}).get(collection_name, [])
        if action == "create" and any(value.get("name") == name for value in current_items):
            _fail("GTM_ENTITY_ALREADY_EXISTS", "A GTM entity with this name already exists in the workspace.", name=name)
        resource: dict[str, Any] = {"name": name}
        if action == "pause":
            path = str(item.get("entityPath", ""))
            fingerprint = str(item.get("fingerprint", ""))
            existing = next((value for value in (current_state or {}).get("tags", []) if value.get("path") == path), None)
            if not isinstance(existing, dict) or existing.get("fingerprint") != fingerprint or existing.get("name") != name:
                _fail("STALE_GTM_ENTITY", "The tag selected for pause does not match the fresh workspace snapshot.")
            allowed = {"name", "type", "liveOnly", "priority", "notes", "scheduleStartMs", "scheduleEndMs", "parameter", "fingerprint", "firingTriggerId", "blockingTriggerId", "setupTag", "teardownTag", "parentFolderId", "tagFiringOption", "paused", "monitoringMetadata", "monitoringMetadataTagNameKey", "consentSettings", "tagId"}
            resource = {key: value for key, value in existing.items() if key in allowed}
            resource["paused"] = True
            changes.append({"tag": resource, "changeStatus": "updated"})
            summary.append({"action": action, "entityKind": kind, "template": template, "name": name, "alias": aliases[name]})
            continue
        if template == "data-layer-variable":
            data_name = _require_string(settings, "dataLayerName", DATA_LAYER_NAME)
            resource.update({"type": "v", "parameter": [_template("name", data_name), {"key": "dataLayerVersion", "type": "integer", "value": "2"}]})
        elif template == "custom-event-trigger":
            event = _require_string(settings, "eventName", EVENT_NAME)
            if event not in planned_events:
                _fail("GTM_EVENT_NOT_PLANNED", "The GTM trigger event is not in the approved measurement plan.", eventName=event)
            resource.update({"type": "customEvent", "customEventFilter": [_condition(event)]})
        elif template == "page-view-trigger":
            resource["type"] = "pageview"
        elif template == "history-change-trigger":
            resource["type"] = "historyChange"
        else:
            measurement_id = _require_string(settings, "measurementId", MEASUREMENT_ID)
            resource["type"] = "gaawc" if template == "google-tag" else "gaawe"
            parameters = [_template("tagId" if template == "google-tag" else "measurementId", measurement_id)]
            if template == "ga4-event-tag":
                event = _require_string(settings, "eventName", EVENT_NAME)
                if event not in planned_events:
                    _fail("GTM_EVENT_NOT_PLANNED", "The GTM event tag is not in the approved measurement plan.", eventName=event)
                parameters.append(_template("eventName", event))
                event_parameters = settings.get("eventParameters", {})
                if not isinstance(event_parameters, dict) or len(event_parameters) > 25:
                    _fail("INVALID_GTM_TEMPLATE_SETTING", "GA4 event parameters must be an object with at most 25 entries.")
                maps = []
                for parameter_name, variable_name in sorted(event_parameters.items()):
                    if not EVENT_NAME.fullmatch(str(parameter_name)) or not isinstance(variable_name, str) or variable_name not in aliases:
                        _fail("INVALID_GTM_ENTITY_REFERENCE", "A GA4 event parameter must reference an entity in the same plan.", parameter=str(parameter_name))
                    maps.append({"type": "map", "map": [_template("name", str(parameter_name)), _template("value", "{{" + variable_name + "}}")]})
                if maps:
                    parameters.append({"key": "eventParameters", "type": "list", "list": maps})
            firing = _string_list(settings, "firingTriggers")
            blocking = _string_list(settings, "blockingTriggers")
            if not firing or any(ref not in aliases for ref in firing + blocking):
                _fail("INVALID_GTM_ENTITY_REFERENCE", "Every tag needs valid firing-trigger references from the same plan.")
            resource.update({
                "parameter": parameters,
                "firingTriggerId": [aliases[ref] for ref in firing],
                "blockingTriggerId": [aliases[ref] for ref in blocking],
                "tagFiringOption": "oncePerEvent",
                "paused": bool(settings.get("paused", False)),
                "consentSettings": _consent(settings),
            })
        id_key, new_id = _entity_id(kind, index)
        if action == "create":
            resource[id_key] = new_id
        else:
            path = str(item.get("entityPath", ""))
            fingerprint = str(item.get("fingerprint", ""))
            expected_segment = {"variable": "variables", "trigger": "triggers", "tag": "tags"}[kind]
            if not ENTITY_PATH.fullmatch(path) or f"/{expected_segment}/" not in path or not fingerprint:
                _fail("INVALID_GTM_ENTITY_REFERENCE", "Updates require an exact entity path and fingerprint.")
            resource[id_key] = path.rsplit("/", 1)[-1]
            resource["fingerprint"] = fingerprint
            existing = next((value for value in current_items if value.get("path") == path), None)
            if not isinstance(existing, dict) or existing.get("fingerprint") != fingerprint or existing.get("name") != name:
                _fail("STALE_GTM_ENTITY", "The entity selected for update does not match the fresh workspace snapshot.")
            official = {
                "variable": {"name", "type", "notes", "scheduleStartMs", "scheduleEndMs", "parameter", "enablingTriggerId", "disablingTriggerId", "fingerprint", "parentFolderId", "formatValue", "variableId"},
                "trigger": {"name", "type", "customEventFilter", "filter", "autoEventFilter", "waitForTags", "checkValidation", "waitForTagsTimeout", "uniqueTriggerId", "eventName", "interval", "limit", "fingerprint", "parentFolderId", "selector", "intervalSeconds", "maxTimerLengthSeconds", "verticalScrollPercentageList", "horizontalScrollPercentageList", "visibilitySelector", "visiblePercentageMin", "visiblePercentageMax", "continuousTimeMinMilliseconds", "totalTimeMinMilliseconds", "notes", "parameter", "triggerId"},
                "tag": {"name", "type", "liveOnly", "priority", "notes", "scheduleStartMs", "scheduleEndMs", "parameter", "fingerprint", "firingTriggerId", "blockingTriggerId", "setupTag", "teardownTag", "parentFolderId", "tagFiringOption", "paused", "monitoringMetadata", "monitoringMetadataTagNameKey", "consentSettings", "tagId"},
            }[kind]
            resource = {**{key: value for key, value in existing.items() if key in official}, **resource}
        changes.append({kind: resource, "changeStatus": "added" if action == "create" else "updated"})
        summary.append({"action": action, "entityKind": kind, "template": template, "name": name, "alias": aliases[name]})
    return changes, summary
