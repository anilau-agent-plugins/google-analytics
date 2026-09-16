from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.google_analytics_cli.auth import AuthService
from scripts.google_analytics_cli.auth_state import AuthStateStore
from scripts.google_analytics_cli.errors import AdvisorError, EXIT_NETWORK
from scripts.google_analytics_cli.oauth import BASE_SCOPES, SCOPES, TARGET_SCOPES
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


class SelectiveFailureSecrets(MemorySecrets):
    def __init__(self):
        super().__init__()
        self.fail_put_refs = set()

    def put(self, key, value):
        if key in self.fail_put_refs:
            raise AdvisorError("SECRET_STORE_UNAVAILABLE", "simulated write failure", 3)
        super().put(key, value)


class AuthServiceTests(unittest.TestCase):
    def configured(self, root):
        secrets = MemorySecrets()
        return self.configured_with_secrets(root, secrets)

    def configured_with_secrets(self, root, secrets):
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

    def test_legacy_profile_keeps_analytics_ready_and_requires_only_search_console_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, _, secrets, client_ref = self.configured(temp)
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//target", "scope": " ".join(TARGET_SCOPES)},
                {"sub": "s", "email": "u@example.com"},
            )):
                profile = service.login(client_ref)["profileId"]
            payload = json.loads(secrets.get(f"oauth-profile:{profile}").decode())
            payload["refreshToken"] = "1//legacy"
            payload["grantedScopes"] = list(BASE_SCOPES)
            secrets.put(f"oauth-profile:{profile}", json.dumps(payload).encode())

            status = service.status(profile)
            self.assertEqual(status["status"], "connected")
            self.assertEqual(status["capabilities"]["analyticsGtm"]["status"], "ready")
            self.assertEqual(status["capabilities"]["searchConsole"]["status"], "authorization_required")
            self.assertTrue(status["authorizationUpgradeRequired"])
            self.assertEqual(status["missingTargetScopes"], ["https://www.googleapis.com/auth/webmasters.readonly"])

    def test_target_upgrade_is_required_when_any_analytics_scope_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, _, secrets, client_ref = self.configured(temp)
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//target", "scope": " ".join(TARGET_SCOPES)},
                {"sub": "s", "email": "u@example.com"},
            )):
                profile = service.login(client_ref)["profileId"]
            payload = json.loads(secrets.get(f"oauth-profile:{profile}").decode())
            payload["grantedScopes"] = [scope for scope in TARGET_SCOPES if scope != BASE_SCOPES[2]]
            secrets.put(f"oauth-profile:{profile}", json.dumps(payload).encode())

            status = service.status(profile)
            self.assertEqual(status["capabilities"]["searchConsole"]["status"], "ready")
            self.assertEqual(status["capabilities"]["analyticsGtm"]["status"], "authorization_required")
            self.assertEqual(status["missingTargetScopes"], [BASE_SCOPES[2]])
            self.assertTrue(status["authorizationUpgradeRequired"])

    def test_scope_upgrade_preserves_profile_identity_and_rotates_through_protected_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, state, secrets, client_ref = self.configured(temp)
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//legacy", "scope": " ".join(TARGET_SCOPES)},
                {"sub": "s", "email": "old@example.com"},
            )):
                profile = service.login(client_ref)["profileId"]
            payload = json.loads(secrets.get(f"oauth-profile:{profile}").decode())
            payload["refreshToken"] = "1//legacy"
            payload["grantedScopes"] = list(BASE_SCOPES)
            secrets.put(f"oauth-profile:{profile}", json.dumps(payload).encode())

            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//upgraded", "scope": " ".join(TARGET_SCOPES)},
                {"sub": "s", "email": "new@example.com", "email_verified": True},
            )) as authorize_call:
                result = service.upgrade(profile)

            self.assertEqual(result["profileId"], profile)
            self.assertEqual(result["status"], "upgraded")
            self.assertEqual(result["capabilities"]["searchConsole"]["status"], "ready")
            self.assertEqual(result["email"], "new@example.com")
            self.assertEqual(authorize_call.call_args.kwargs["scopes"], TARGET_SCOPES)
            current = json.loads(secrets.get(f"oauth-profile:{profile}").decode())
            self.assertEqual(current["refreshToken"], "1//upgraded")
            self.assertNotIn(f"oauth-profile:{profile}:scope-set-search-console-read-v1", secrets.values)
            self.assertEqual(state.read()["profiles"][profile]["credentialState"], "current")

    def test_identity_mismatch_and_authorization_failure_preserve_legacy_credential(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, _, secrets, client_ref = self.configured(temp)
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//target", "scope": " ".join(TARGET_SCOPES)},
                {"sub": "s", "email": "u@example.com"},
            )):
                profile = service.login(client_ref)["profileId"]
            payload = json.loads(secrets.get(f"oauth-profile:{profile}").decode())
            payload["refreshToken"] = "1//legacy"
            payload["grantedScopes"] = list(BASE_SCOPES)
            legacy = json.dumps(payload).encode()
            secrets.put(f"oauth-profile:{profile}", legacy)

            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//wrong", "scope": " ".join(TARGET_SCOPES)},
                {"sub": "different", "email": "other@example.com"},
            )):
                with self.assertRaises(AdvisorError) as mismatch:
                    service.upgrade(profile)
            self.assertEqual(mismatch.exception.code, "OAUTH_PROFILE_IDENTITY_MISMATCH")
            self.assertEqual(secrets.get(f"oauth-profile:{profile}"), legacy)

            with patch("scripts.google_analytics_cli.auth.authorize", side_effect=AdvisorError(
                "OAUTH_ACCESS_DENIED", "denied", 4
            )):
                with self.assertRaises(AdvisorError) as denied:
                    service.upgrade(profile)
            self.assertTrue(denied.exception.details["previousProfilePreserved"])
            self.assertEqual(secrets.get(f"oauth-profile:{profile}"), legacy)

    def test_failed_canonical_finalization_keeps_upgraded_staging_credential_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            secrets = SelectiveFailureSecrets()
            service, state, _, client_ref = self.configured_with_secrets(temp, secrets)
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//target", "scope": " ".join(TARGET_SCOPES)},
                {"sub": "s", "email": "u@example.com"},
            )):
                profile = service.login(client_ref)["profileId"]
            canonical_ref = f"oauth-profile:{profile}"
            staging_ref = f"{canonical_ref}:scope-set-search-console-read-v1"
            legacy = json.loads(secrets.get(canonical_ref).decode())
            legacy["refreshToken"] = "1//legacy"
            legacy["grantedScopes"] = list(BASE_SCOPES)
            secrets.put(canonical_ref, json.dumps(legacy).encode())

            secrets.fail_put_refs.add(canonical_ref)
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//upgraded", "scope": " ".join(TARGET_SCOPES)},
                {"sub": "s", "email": "u@example.com"},
            )):
                result = service.upgrade(profile)

            self.assertEqual(result["status"], "upgraded")
            self.assertEqual(result["credentialWarnings"][0]["code"], "AUTH_CREDENTIAL_FINALIZATION_PENDING")
            self.assertEqual(state.read()["profiles"][profile]["credentialRef"], staging_ref)
            self.assertEqual(service.status(profile)["capabilities"]["searchConsole"]["status"], "ready")
            self.assertEqual(json.loads(secrets.get(staging_ref).decode())["refreshToken"], "1//upgraded")
            self.assertEqual(json.loads(secrets.get(canonical_ref).decode())["refreshToken"], "1//legacy")

            secrets.fail_put_refs.clear()
            finalized = service.upgrade(profile)
            self.assertEqual(finalized["status"], "already_current")
            self.assertEqual(finalized["credentialWarnings"], [])
            self.assertEqual(state.read()["profiles"][profile]["credentialRef"], canonical_ref)
            self.assertNotIn(staging_ref, secrets.values)
            self.assertEqual(json.loads(secrets.get(canonical_ref).decode())["refreshToken"], "1//upgraded")

    def test_failed_staging_write_preserves_legacy_credential_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            secrets = SelectiveFailureSecrets()
            service, state, _, client_ref = self.configured_with_secrets(temp, secrets)
            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//target", "scope": " ".join(TARGET_SCOPES)},
                {"sub": "s", "email": "u@example.com"},
            )):
                profile = service.login(client_ref)["profileId"]
            canonical_ref = f"oauth-profile:{profile}"
            staging_ref = f"{canonical_ref}:scope-set-search-console-read-v1"
            legacy = json.loads(secrets.get(canonical_ref).decode())
            legacy["refreshToken"] = "1//legacy"
            legacy["grantedScopes"] = list(BASE_SCOPES)
            legacy_bytes = json.dumps(legacy).encode()
            secrets.put(canonical_ref, legacy_bytes)
            before_metadata = dict(state.read()["profiles"][profile])
            secrets.fail_put_refs.add(staging_ref)

            with patch("scripts.google_analytics_cli.auth.authorize", return_value=(
                {"refresh_token": "1//upgraded", "scope": " ".join(TARGET_SCOPES)},
                {"sub": "s", "email": "u@example.com"},
            )):
                with self.assertRaises(AdvisorError) as caught:
                    service.upgrade(profile)

            self.assertEqual(caught.exception.code, "SECRET_STORE_UNAVAILABLE")
            self.assertTrue(caught.exception.details["previousProfilePreserved"])
            self.assertEqual(secrets.get(canonical_ref), legacy_bytes)
            self.assertNotIn(staging_ref, secrets.values)
            self.assertEqual(state.read()["profiles"][profile], before_metadata)


if __name__ == "__main__":
    unittest.main()
