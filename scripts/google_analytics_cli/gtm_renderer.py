"""Plain-language rendering for immutable GTM plans."""

from __future__ import annotations

from typing import Any


STAGE_TEXT = {
    "WORKSPACE_CREATE": "Create one isolated workspace. Nothing is published.",
    "WORKSPACE_SYNC": "Synchronize one workspace with the latest container version. Conflicts stop the workflow.",
    "ENTITY_BULK_UPDATE": "Apply one coherent set of supported tags, triggers, and variables inside the isolated workspace.",
    "QUICK_PREVIEW": "Compile a temporary preview version. This does not prove that browser events work.",
    "VERSION_CREATE": "Create a container version. Google replaces the current workspace with a newly generated workspace.",
    "PUBLISH": "Publish exactly one checked container version and replace the current live version.",
}


def render_plan(plan: dict[str, Any]) -> str:
    stage = str(plan.get("stage", ""))
    operation = plan.get("operations", [{}])[0]
    lines = [
        f"GTM stage: {stage}",
        STAGE_TEXT.get(stage, "Apply one GTM lifecycle operation."),
        f"Container: {plan.get('gtmContext', {}).get('container')}",
        f"Target: {operation.get('resource')}",
        f"Reason: {operation.get('rationale')}",
        "Automatic retry: disabled.",
        "Production website deploy: not included.",
    ]
    if stage == "QUICK_PREVIEW":
        lines.append("A successful compiler preview still requires a separate runtime preview before publish.")
    if stage == "VERSION_CREATE":
        lines.append("Important: version creation deletes/replaces this workspace and returns a new workspace path.")
    if stage == "PUBLISH":
        lines.append("Important: this replaces the live container version. Publish is never automatic.")
    lines.append(f"Confirm this exact SHA-256 only after review: {plan.get('planSha256')}")
    return "\n".join(lines)
