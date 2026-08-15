"""Atomic, lock-protected non-secret OAuth profile metadata."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .errors import AdvisorError, EXIT_CONFIGURATION
from .paths import runtime_paths


def _empty() -> dict[str, Any]:
    return {"schemaVersion": 1, "activeProfileId": None, "clients": {}, "profiles": {}}


class AuthStateStore:
    def __init__(self, *, env: dict[str, str] | None = None, state_dir: Path | None = None) -> None:
        self.root = state_dir or runtime_paths(env=env)["state"]
        self.path = self.root / "auth-state.json"
        self.lock_path = self.root / "auth-state.lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        with self.lock_path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AdvisorError(
                "AUTH_STATE_CORRUPT",
                "The local authorization index is unreadable.",
                EXIT_CONFIGURATION,
                details={"path": str(self.path), "reason": type(exc).__name__},
                next_action="Restore or remove the damaged authorization index before continuing.",
            ) from exc
        if not isinstance(value, dict) or value.get("schemaVersion") != 1:
            raise AdvisorError("AUTH_STATE_CORRUPT", "The local authorization index has an unsupported format.", EXIT_CONFIGURATION)
        if not isinstance(value.get("clients"), dict) or not isinstance(value.get("profiles"), dict):
            raise AdvisorError("AUTH_STATE_CORRUPT", "The local authorization index is incomplete.", EXIT_CONFIGURATION)
        return value

    def read(self) -> dict[str, Any]:
        with self._lock():
            return self._read_unlocked()

    def update(self, operation: Callable[[dict[str, Any]], Any]) -> Any:
        with self._lock():
            state = self._read_unlocked()
            result = operation(state)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
            return result
