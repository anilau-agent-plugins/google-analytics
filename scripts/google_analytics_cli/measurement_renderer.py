"""Plain-language rendering derived only from a measurement-plan artifact."""

from __future__ import annotations

from typing import Any


def render_plan(plan: dict[str, Any]) -> str:
    if plan.get("schemaVersion") == 1:
        return f"Legacy measurement plan {plan.get('planId')} ({plan.get('status')}). Migrate it to version 2 before implementation."
    lines = [
        f"Measurement plan: {plan['planId']}",
        f"Status: {plan['status']}",
        f"Business model: {plan['businessContext']['businessModel']}",
        "",
        "Business outcomes:",
    ]
    for outcome in plan["outcomes"]:
        lines.append(
            f"- {outcome['name']} ({outcome['class']}): {outcome['businessMeaning']} "
            f"Source of truth: {outcome['sourceOfTruth']} — {outcome['authoritativeState']}."
        )
    lines.extend(["", "Planned GA4 events:"])
    for event in plan["events"]:
        key = "recommended as a key event" if event["keyEventRecommendation"] else "diagnostic/supporting event"
        lines.append(
            f"- {event['name']} ({event['catalogClass']}, {key}): {event['businessMeaning']} "
            f"Trigger: {event['trigger']} Verification: {event['verificationChecks'][0]['successCriterion']}"
        )
    ecommerce = plan["ecommerce"]
    lines.extend(["", f"Ecommerce: {'enabled' if ecommerce['enabled'] else 'not applicable'} — {ecommerce['reason']}"])
    consent = plan["consent"]
    lines.append(f"Consent: {consent['mode']}; policy confirmed: {'yes' if consent['policyConfirmed'] else 'no'}.")
    if plan["openQuestions"]:
        lines.extend(["", "Open questions:"] + [f"- {item}" for item in plan["openQuestions"]])
    if plan["limitations"]:
        lines.extend(["", "Limitations:"] + [f"- {item}" for item in plan["limitations"]])
    lines.extend([
        "",
        "Safety boundary: this plan changed no GA4, GTM, or website configuration and sent no production events.",
        f"Content SHA-256: {plan['contentSha256']}",
    ])
    return "\n".join(lines)
