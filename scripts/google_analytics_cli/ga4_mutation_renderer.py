"""Plain-language rendering for Stage 7 mutation previews."""

from __future__ import annotations

from typing import Any


LABELS = {
    "PROPERTY_CREATE": "Создать GA4 property",
    "PROPERTY_PATCH": "Изменить основные настройки GA4 property",
    "WEB_STREAM_CREATE": "Создать web data stream",
    "WEB_STREAM_PATCH": "Изменить web data stream",
    "KEY_EVENT_CREATE": "Добавить key event",
    "KEY_EVENT_PATCH": "Изменить key event",
    "CUSTOM_DIMENSION_CREATE": "Зарегистрировать custom dimension",
    "CUSTOM_DIMENSION_PATCH": "Изменить custom dimension",
    "CUSTOM_METRIC_CREATE": "Зарегистрировать custom metric",
    "CUSTOM_METRIC_PATCH": "Изменить custom metric",
    "RETENTION_UPDATE": "Изменить срок хранения детальных данных",
    "MP_SECRET_CREATE": "Создать Measurement Protocol secret",
    "MP_SECRET_PATCH": "Переименовать Measurement Protocol secret",
    "ENHANCED_MEASUREMENT_UPDATE": "Изменить enhanced measurement (experimental)",
    "DATA_REDACTION_UPDATE": "Изменить data redaction (experimental)",
}


def render_plan(plan: dict[str, Any]) -> dict[str, Any]:
    operations = []
    for item in plan.get("operations", []):
        operations.append({
            "operationId": item["operationId"],
            "change": LABELS.get(item["kind"], item["kind"]),
            "resource": item["resource"],
            "why": item["rationale"],
            "currentState": item.get("before"),
            "requestedState": item["body"],
            "risk": "experimental API" if item.get("experimental") else "remote GA4 configuration change",
            "verification": item["expectedReadback"],
        })
    return {
        "verdict": "GA4 will not be changed until the exact SHA-256 below is confirmed.",
        "planId": plan["planId"],
        "expiresAt": plan["expiresAt"],
        "planSha256": plan["planSha256"],
        "operations": operations,
        "confirmationCommand": f"ga4 apply --plan <path> --confirm-sha256 {plan['planSha256']} --json",
        "authorizationIsNotApproval": True,
        "automaticRetry": False,
    }
