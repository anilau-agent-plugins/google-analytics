"""Atomic immutable project artifact storage."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import AdvisorError, EXIT_CONFIGURATION
from .paths import project_data_path


SECRET_KEY = re.compile(r"(?:secret|token|password|credentials?|private[_-]?key)(?:value)?$", re.I)
SECRET_TEXT = (
    re.compile(r"\bya29\.[A-Za-z0-9._-]+"), re.compile(r"\b1//[A-Za-z0-9._-]+"),
    re.compile(r"\bGOCSPX-[A-Za-z0-9_-]+"), re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
)


def _assert_secret_free(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEY.search(str(key)):
                raise AdvisorError("ARTIFACT_WRITE_FAILED", "A secret-bearing field was blocked from project artifacts.", EXIT_CONFIGURATION)
            _assert_secret_free(child)
    elif isinstance(value, list):
        for child in value:
            _assert_secret_free(child)
    elif isinstance(value, str) and any(pattern.search(value) for pattern in SECRET_TEXT):
        raise AdvisorError("ARTIFACT_WRITE_FAILED", "A credential-shaped value was blocked from project artifacts.", EXIT_CONFIGURATION)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ArtifactStore:
    def __init__(self, project_root: Path) -> None:
        self.root = project_data_path(project_root)

    @contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".artifact.lock"
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise AdvisorError("ARTIFACT_WRITE_FAILED", "Another artifact write is already in progress.", EXIT_CONFIGURATION) from exc
        try:
            os.close(descriptor)
            yield
        finally:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _atomic_write(self, destination: Path, value: Any) -> None:
        _assert_secret_free(value)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(canonical_json(value))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise AdvisorError("ARTIFACT_WRITE_FAILED", "Could not write the project audit artifact.", EXIT_CONFIGURATION) from exc

    def write_snapshot(self, provider: str, channel: str, resource: str, state: dict[str, Any], request_ids: list[str]) -> dict[str, Any]:
        raw = canonical_json(state)
        digest = hashlib.sha256(raw).hexdigest()
        snapshot_id = f"snapshot-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{digest[:12]}"
        artifact = {
            "schemaVersion": 1, "artifactType": "snapshot", "generatedAt": utc_now(),
            "snapshotId": snapshot_id, "provider": provider, "apiChannel": channel,
            "resource": resource, "stateSha256": digest, "state": state,
            "requestIds": sorted(set(request_ids)),
        }
        relative = Path("snapshots") / f"{snapshot_id}.json"
        destination = self.root / relative
        with self._lock():
            if not destination.exists():
                self._atomic_write(destination, artifact)
        return {"snapshotId": snapshot_id, "path": relative.as_posix(), "sha256": hashlib.sha256(canonical_json(artifact)).hexdigest()}

    def write_audit(self, audit: dict[str, Any]) -> dict[str, Any]:
        audit_id = str(audit["auditId"])
        relative = Path("audits") / f"{audit_id}.json"
        destination = self.root / relative
        index_path = self.root / "index.json"
        with self._lock():
            if destination.exists():
                raise AdvisorError("ARTIFACT_WRITE_FAILED", "Audit identifiers are immutable and cannot be overwritten.", EXIT_CONFIGURATION)
            self._atomic_write(destination, audit)
            index = {"schemaVersion": 1, "audits": []}
            if index_path.exists():
                try:
                    loaded = json.loads(index_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict) and isinstance(loaded.get("audits"), list):
                        index = loaded
                except (OSError, json.JSONDecodeError):
                    pass
            index["audits"].append({"auditId": audit_id, "path": relative.as_posix(), "generatedAt": audit["generatedAt"]})
            self._atomic_write(index_path, index)
        return {"auditId": audit_id, "path": str(destination), "indexPath": str(index_path)}
