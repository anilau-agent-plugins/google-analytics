"""Plain-language previews for local website changes."""

from __future__ import annotations

from typing import Any


def render_plan(plan: dict[str, Any]) -> str:
    route = "прямой Google tag" if plan.get("route") == "direct" else "Google Tag Manager"
    lines = [
        f"Локальный план установки: {route}.",
        f"Файлов: {len(plan.get('operations', []))}. Production deploy: нет.",
    ]
    for operation in plan.get("operations", []):
        action = "создать" if operation.get("kind") == "FILE_CREATE" else "изменить"
        lines.append(f"- {action} {operation.get('resource')}: {operation.get('rationale')}")
    commands = plan.get("verificationCommands", [])
    if commands:
        lines.append("Команды проекта не запускаются автоматически; после применения доступны точные подтвержденные команды проверки:")
        for command in commands:
            rendered = " ".join([command["executable"], *command["arguments"]])
            lines.append(f"- {rendered} (cwd: {command['cwd']}, timeout: {command['timeoutSeconds']}s)")
    lines.extend([
        "Изменение выполняется только после подтверждения точного SHA-256 ниже.",
        f"planSha256: {plan.get('planSha256')}",
    ])
    return "\n".join(lines)
