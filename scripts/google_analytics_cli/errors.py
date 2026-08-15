"""Stable CLI errors and exit codes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


EXIT_RUNTIME = 2
EXIT_CONFIGURATION = 3
EXIT_INPUT = 4
EXIT_NETWORK = 5
EXIT_INTERNAL = 6


@dataclass
class AdvisorError(Exception):
    code: str
    message: str
    exit_code: int
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    next_action: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
            "nextAction": self.next_action,
        }
