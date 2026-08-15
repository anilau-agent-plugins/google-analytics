from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.google_analytics_cli.auth_client import import_client, load_client
from scripts.google_analytics_cli.auth_state import AuthStateStore
from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.secret_store import SecretStore


class MemorySecrets(SecretStore):
    def __init__(self):
        self.values = {}

    def put(self, key, value):
        self.values[key] = value

    def get(self, key):
        return self.values[key]

    def delete(self, key):
        return self.values.pop(key, None) is not None


def desktop_client():
    return {
        "installed": {
            "client_id": "123.apps.googleusercontent.com",
            "project_id": "customer-project",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": "GOCSPX-test-secret",
        }
    }


class AuthClientTests(unittest.TestCase):
    def test_import_protects_secret_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "client.json"
            source.write_text(json.dumps(desktop_client()), encoding="utf-8")
            secrets = MemorySecrets()
            state = AuthStateStore(state_dir=root / "state")
            first = import_client(source, secrets=secrets, state=state)
            second = import_client(source, secrets=secrets, state=state)
            self.assertEqual(first["clientRef"], second["clientRef"])
            self.assertNotIn("GOCSPX", json.dumps(state.read()))
            loaded = load_client(first["clientRef"], secrets=secrets)
            self.assertEqual(loaded["client_secret"], "GOCSPX-test-secret")

    def test_web_client_and_relative_path_are_rejected(self) -> None:
        secrets = MemorySecrets()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "web.json"
            source.write_text(json.dumps({"web": desktop_client()["installed"]}), encoding="utf-8")
            with self.assertRaises(AdvisorError) as caught:
                import_client(source, secrets=secrets, state=AuthStateStore(state_dir=root / "state"))
            self.assertEqual(caught.exception.code, "OAUTH_CLIENT_TYPE_UNSUPPORTED")
            with self.assertRaises(AdvisorError):
                import_client(Path("client.json"), secrets=secrets, state=AuthStateStore(state_dir=root / "state"))


if __name__ == "__main__":
    unittest.main()
