from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.google_analytics_cli.auth_state import AuthStateStore
from scripts.google_analytics_cli.errors import AdvisorError


class AuthStateTests(unittest.TestCase):
    def test_atomic_update_and_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = AuthStateStore(state_dir=Path(temp))
            store.update(lambda value: value["clients"].update({"client-1": {"projectId": "p"}}))
            self.assertEqual(store.read()["clients"]["client-1"]["projectId"], "p")

    def test_corrupt_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "auth-state.json").write_text("[]", encoding="utf-8")
            with self.assertRaises(AdvisorError) as caught:
                AuthStateStore(state_dir=root).read()
            self.assertEqual(caught.exception.code, "AUTH_STATE_CORRUPT")


if __name__ == "__main__":
    unittest.main()
