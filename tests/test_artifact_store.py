from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.google_analytics_cli.artifact_store import ArtifactStore
from scripts.google_analytics_cli.errors import AdvisorError


class ArtifactStoreTests(unittest.TestCase):
    def test_snapshot_is_content_addressed_and_audit_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            store = ArtifactStore(root)
            first = store.write_snapshot("website", "static-scan", str(root), {"ok": True}, [])
            second = store.write_snapshot("website", "static-scan", str(root), {"ok": True}, [])
            self.assertEqual(first["snapshotId"], second["snapshotId"])
            audit = {"auditId": "baseline-test", "generatedAt": "2026-08-15T12:00:00Z"}
            location = store.write_audit(audit)
            self.assertEqual(json.loads(Path(location["path"]).read_text(encoding="utf-8")), audit)
            with self.assertRaises(AdvisorError):
                store.write_audit(audit)
            self.assertEqual(list((root / ".google-analytics-advisor").rglob("*.tmp")), [])
            self.assertEqual((root / ".google-analytics-advisor" / ".gitignore").read_text(encoding="utf-8"), "*\n")

    def test_secret_shaped_data_is_blocked_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = ArtifactStore(Path(temp).resolve())
            with self.assertRaises(AdvisorError):
                store.write_snapshot("website", "test", "root", {"refreshToken": "1//private"}, [])
            self.assertEqual(list(Path(temp).rglob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
