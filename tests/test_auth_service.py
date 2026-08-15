from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.google_analytics_cli.auth import AuthService
from scripts.google_analytics_cli.auth_state import AuthStateStore
from scripts.google_analytics_cli.errors import AdvisorError, EXIT_NETWORK
from scripts.google_analytics_cli.oauth import SCOPES
from scripts.google_analytics_cli.secret_store import SecretStore


class MemorySecrets(SecretStore):
    def __init__(self):
        self.values = {}

    def put(self, key, value): self.values[key] = value
    def get(self, key):
        if key not in self.values:
            raise AdvisorError("SECRET_NOT_FOUND", "missing", 3)
        return self.values[key]
    def delete(self, key): return self.values.pop(key, None) is not None


class AuthServiceTests(unittest.TestCase):
    def configured(self, root):
        secrets = MemorySecrets()
        state = AuthStateStore(state_dir=Path(root))
        client_ref = "client-1"
        secrets.put(
            f"oauth-client:{client_ref}",
            json.dumps({"schemaVersion": 1, "client_id": "123.apps.googleusercontent.com", "client_secret": "x", "project_id": "p"}).encode(),
        )
        state.update(lambda value: value["clients"].update({client_ref: {"clientRef": client_ref, "fingerprint": "f" * 64}}))
        return AuthService(state=state, secrets=secrets), state, secrets, client_ref

    def test_login_profiles_status_use_and_forget(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, state, secrets, client_ref = self.configured(temp)
            token = {"refresh_token": "1//refresh", "scope": " ".join(SCOPES)}
            identity = {"sub": "sub-1", "email": "user@example.com", "email_verified": True}
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(token, identity)):
                result = service.login(client_ref)
            profile_id = result["profileId"]
            self.assertNotIn("user@example.com", json.dumps(state.read()))
            self.assertEqual(service.status(profile_id)["email"], "user@example.com")
            self.assertEqual(service.profiles()["profiles"][0]["profileId"], profile_id)
            service.use(profile_id)
            confirmation = service.status(profile_id)["confirmation"]
            forgotten = service.forget_local(profile_id, confirmation)
            self.assertTrue(forgotten["localCredentialRemoved"])
            self.assertFalse(forgotten["googleGrantRevoked"])

    def test_wrong_confirmation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, _, _, client_ref = self.configured(temp)
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//refresh"}, {"sub": "s", "email": "u@example.com"}
            )):
                profile = service.login(client_ref)["profileId"]
            with self.assertRaises(AdvisorError) as caught:
                service.forget_local(profile, "wrong")
            self.assertEqual(caught.exception.code, "AUTH_CONFIRMATION_MISMATCH")

    def test_invalid_grant_marks_profile_for_reauthorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, state, _, client_ref = self.configured(temp)
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//refresh"}, {"sub": "s", "email": "u@example.com"}
            )):
                profile = service.login(client_ref)["profileId"]
            with patch("scripts.google_analytics_cli.auth.refresh", side_effect=AdvisorError(
                "OAUTH_TOKEN_INVALID_GRANT", "invalid", 3
            )):
                with self.assertRaises(AdvisorError):
                    service.access_token(profile)
            self.assertEqual(state.read()["profiles"][profile]["status"], "reauthorization_required")

    def test_ambiguous_revoke_retains_local_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, state, secrets, client_ref = self.configured(temp)
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//refresh"}, {"sub": "s", "email": "u@example.com"}
            )):
                profile = service.login(client_ref)["profileId"]
            confirmation = service.status(profile)["confirmation"]
            with patch("scripts.google_analytics_cli.auth.revoke", side_effect=AdvisorError(
                "AUTH_ACTION_AMBIGUOUS", "ambiguous", EXIT_NETWORK
            )):
                with self.assertRaises(AdvisorError) as caught:
                    service.revoke(profile, confirmation)
            self.assertEqual(caught.exception.code, "AUTH_ACTION_AMBIGUOUS")
            self.assertIn(f"oauth-profile:{profile}", secrets.values)
            self.assertEqual(state.read()["profiles"][profile]["status"], "connected")

    def test_successful_revoke_deletes_secret_and_marks_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, state, secrets, client_ref = self.configured(temp)
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//refresh"}, {"sub": "s", "email": "u@example.com"}
            )):
                profile = service.login(client_ref)["profileId"]
            confirmation = service.status(profile)["confirmation"]
            with patch("scripts.google_analytics_cli.auth.revoke") as revoke_call:
                result = service.revoke(profile, confirmation)
            revoke_call.assert_called_once()
            self.assertTrue(result["googleGrantRevoked"])
            self.assertNotIn(f"oauth-profile:{profile}", secrets.values)
            self.assertEqual(state.read()["profiles"][profile]["status"], "revoked")
            self.assertIsNone(state.read()["activeProfileId"])
            self.assertEqual(service.status(profile)["status"], "revoked")


if __name__ == "__main__":
    unittest.main()
