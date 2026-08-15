"""OAuth client, profile, and protected credential orchestration."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .auth_client import import_client, load_client
from .auth_state import AuthStateStore
from .errors import AdvisorError, EXIT_CONFIGURATION, EXIT_INPUT
from .form_http import FormTransport
from .google_api_probe import run_probes
from .http import JsonTransport
from .oauth import SCOPES, SCOPE_GROUPS, authorize, refresh, revoke
from .secret_store import SecretStore, secret_store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _profile_confirmation(profile_id: str, client_ref: str) -> str:
    return hashlib.sha256(f"{profile_id}:{client_ref}".encode("utf-8")).hexdigest()[:12]


class AuthService:
    def __init__(
        self, *, state: AuthStateStore | None = None, secrets: SecretStore | None = None,
        form: FormTransport | None = None, json_transport: JsonTransport | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.state = state or AuthStateStore(env=env)
        self._secrets = secrets
        self._env = env
        self.form = form
        self.json_transport = json_transport

    @property
    def secrets(self) -> SecretStore:
        if self._secrets is None:
            self._secrets = secret_store(env=self._env)
        return self._secrets

    def client_import(self, path: Path) -> dict[str, Any]:
        result = import_client(path, secrets=self.secrets, state=self.state)
        result["removalConfirmation"] = hashlib.sha256(
            f"{result['clientRef']}:{result['fingerprint']}".encode("utf-8")
        ).hexdigest()[:12]
        result["permissionGroups"] = list(SCOPE_GROUPS)
        result["nextAction"] = "Run auth login with this client reference."
        return result

    def clients(self) -> dict[str, Any]:
        index = self.state.read()
        clients = []
        for client_ref, metadata in sorted(index["clients"].items()):
            clients.append({
                "clientRef": client_ref,
                "projectId": metadata.get("projectId"),
                "maskedClientId": metadata.get("maskedClientId"),
                "removalConfirmation": hashlib.sha256(
                    f"{client_ref}:{metadata['fingerprint']}".encode("utf-8")
                ).hexdigest()[:12],
            })
        return {"clients": clients}

    def _resolve_profile(self, profile_id: str | None) -> tuple[str, dict[str, Any]]:
        index = self.state.read()
        selected = profile_id or index.get("activeProfileId")
        if not selected or selected not in index["profiles"]:
            raise AdvisorError("AUTH_PROFILE_NOT_FOUND", "No connected Google authorization profile was selected.", EXIT_CONFIGURATION)
        return selected, index["profiles"][selected]

    def _load_profile_payload(self, profile_id: str) -> dict[str, Any]:
        try:
            value = json.loads(self.secrets.get(f"oauth-profile:{profile_id}").decode("utf-8"))
        except AdvisorError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise AdvisorError("SECRET_STORE_CORRUPT", "The protected authorization profile is damaged.", EXIT_CONFIGURATION) from exc
        if not isinstance(value, dict) or not value.get("refreshToken") or not value.get("clientRef"):
            raise AdvisorError("SECRET_STORE_CORRUPT", "The protected authorization profile is incomplete.", EXIT_CONFIGURATION)
        return value

    def login(self, client_ref: str, **authorize_kwargs: Any) -> dict[str, Any]:
        index = self.state.read()
        if client_ref not in index["clients"]:
            raise AdvisorError("OAUTH_CLIENT_NOT_FOUND", "The selected OAuth client is not imported.", EXIT_INPUT)
        client = load_client(client_ref, secrets=self.secrets)
        tokens, identity = authorize(
            client, form=self.form, json_transport=self.json_transport, **authorize_kwargs
        )
        subject = str(identity["sub"])
        profile_id = "profile-" + hashlib.sha256(f"{client_ref}:{subject}".encode("utf-8")).hexdigest()[:16]
        timestamp = _now()
        payload = {
            "schemaVersion": 1,
            "clientRef": client_ref,
            "refreshToken": tokens["refresh_token"],
            "grantedScopes": list(SCOPES),
            "identity": {
                "sub": subject,
                "email": str(identity["email"]),
                "emailVerified": bool(identity.get("email_verified", False)),
            },
        }
        self.secrets.put(f"oauth-profile:{profile_id}", json.dumps(payload, separators=(",", ":")).encode("utf-8"))

        def save(state: dict[str, Any]) -> None:
            previous = state["profiles"].get(profile_id, {})
            state["profiles"][profile_id] = {
                "profileId": profile_id,
                "clientRef": client_ref,
                "status": "connected",
                "confirmation": _profile_confirmation(profile_id, client_ref),
                "createdAt": previous.get("createdAt", timestamp),
                "updatedAt": timestamp,
            }
            state["activeProfileId"] = profile_id

        self.state.update(save)
        return {
            "profileId": profile_id,
            "email": payload["identity"]["email"],
            "emailVerified": payload["identity"]["emailVerified"],
            "clientRef": client_ref,
            "grantedScopes": list(SCOPES),
            "status": "connected",
        }

    def profiles(self) -> dict[str, Any]:
        index = self.state.read()
        profiles = []
        for profile_id, metadata in sorted(index["profiles"].items()):
            profiles.append({
                "profileId": profile_id,
                "clientRef": metadata["clientRef"],
                "status": metadata["status"],
                "active": profile_id == index.get("activeProfileId"),
                "confirmation": metadata["confirmation"],
            })
        return {"activeProfileId": index.get("activeProfileId"), "profiles": profiles}

    def status(self, profile_id: str | None = None) -> dict[str, Any]:
        selected, metadata = self._resolve_profile(profile_id)
        if metadata["status"] == "revoked":
            return {
                "profileId": selected,
                "clientRef": metadata["clientRef"],
                "status": "revoked",
                "active": selected == self.state.read().get("activeProfileId"),
                "email": None,
                "emailVerified": None,
                "grantedScopes": [],
                "confirmation": metadata["confirmation"],
            }
        payload = self._load_profile_payload(selected)
        return {
            "profileId": selected,
            "clientRef": metadata["clientRef"],
            "status": metadata["status"],
            "active": selected == self.state.read().get("activeProfileId"),
            "email": payload["identity"]["email"],
            "emailVerified": payload["identity"].get("emailVerified", False),
            "grantedScopes": payload["grantedScopes"],
            "confirmation": metadata["confirmation"],
        }

    def use(self, profile_id: str) -> dict[str, Any]:
        def select(index: dict[str, Any]) -> None:
            if profile_id not in index["profiles"]:
                raise AdvisorError("AUTH_PROFILE_NOT_FOUND", "The selected authorization profile does not exist.", EXIT_INPUT)
            index["activeProfileId"] = profile_id

        self.state.update(select)
        return {"activeProfileId": profile_id}

    def access_token(self, profile_id: str | None = None) -> tuple[str, str, dict[str, Any]]:
        selected, metadata = self._resolve_profile(profile_id)
        payload = self._load_profile_payload(selected)
        client = load_client(payload["clientRef"], secrets=self.secrets)
        try:
            tokens = refresh(client, payload["refreshToken"], form=self.form)
        except AdvisorError as exc:
            if exc.code == "OAUTH_TOKEN_INVALID_GRANT":
                self.state.update(lambda index: index["profiles"][selected].update({"status": "reauthorization_required", "updatedAt": _now()}))
            raise
        return selected, str(tokens["access_token"]), payload

    def doctor(self, profile_id: str | None = None) -> dict[str, Any]:
        selected, token, payload = self.access_token(profile_id)
        probes = run_probes(token, transport=self.json_transport)
        index = self.state.read()
        client_meta = index["clients"].get(payload["clientRef"], {})
        project_id = client_meta.get("projectId")
        service_names = {
            "analyticsAdmin": "analyticsadmin.googleapis.com",
            "analyticsData": "analyticsdata.googleapis.com",
            "tagManager": "tagmanager.googleapis.com",
        }
        for key, service_name in service_names.items():
            if probes.get(key, {}).get("status") == "api_disabled":
                probes[key]["serviceName"] = service_name
                if project_id:
                    probes[key]["enableUrl"] = (
                        "https://console.cloud.google.com/apis/library/"
                        f"{service_name}?project={project_id}"
                    )
        return {
            "profileId": selected,
            "email": payload["identity"]["email"],
            "projectId": project_id,
            "status": probes["status"],
            "probes": probes,
            "requestedCloudScopes": False,
            "mutationPerformed": False,
        }

    def forget_local(self, profile_id: str, confirmation: str) -> dict[str, Any]:
        selected, metadata = self._resolve_profile(profile_id)
        if confirmation != metadata["confirmation"]:
            raise AdvisorError("AUTH_CONFIRMATION_MISMATCH", "The profile confirmation does not match.", EXIT_INPUT)
        removed = self.secrets.delete(f"oauth-profile:{selected}")

        def forget(index: dict[str, Any]) -> None:
            index["profiles"].pop(selected, None)
            if index.get("activeProfileId") == selected:
                index["activeProfileId"] = next(iter(index["profiles"]), None)

        self.state.update(forget)
        return {"profileId": selected, "localCredentialRemoved": removed, "googleGrantRevoked": False}

    def revoke(self, profile_id: str, confirmation: str) -> dict[str, Any]:
        selected, metadata = self._resolve_profile(profile_id)
        if confirmation != metadata["confirmation"]:
            raise AdvisorError("AUTH_CONFIRMATION_MISMATCH", "The profile confirmation does not match.", EXIT_INPUT)
        payload = self._load_profile_payload(selected)
        try:
            revoke(payload["refreshToken"], form=self.form)
        except AdvisorError as exc:
            if exc.code == "AUTH_ACTION_AMBIGUOUS":
                raise AdvisorError(
                    "AUTH_ACTION_AMBIGUOUS",
                    "Google token revocation has an ambiguous outcome; the local credential was retained.",
                    exc.exit_code,
                    next_action="Run auth doctor before deciding whether to retry.",
                ) from exc
            raise
        self.secrets.delete(f"oauth-profile:{selected}")
        def mark_revoked(index: dict[str, Any]) -> None:
            index["profiles"][selected].update({"status": "revoked", "updatedAt": _now()})
            if index.get("activeProfileId") == selected:
                index["activeProfileId"] = next(
                    (profile_id for profile_id, item in index["profiles"].items() if item.get("status") == "connected"),
                    None,
                )

        self.state.update(mark_revoked)
        return {"profileId": selected, "googleGrantRevoked": True, "localCredentialRemoved": True}

    def client_remove(self, client_ref: str, confirmation: str) -> dict[str, Any]:
        index = self.state.read()
        metadata = index["clients"].get(client_ref)
        if not metadata:
            raise AdvisorError("OAUTH_CLIENT_NOT_FOUND", "The OAuth client does not exist.", EXIT_INPUT)
        expected = hashlib.sha256(f"{client_ref}:{metadata['fingerprint']}".encode("utf-8")).hexdigest()[:12]
        if confirmation != expected:
            raise AdvisorError("AUTH_CONFIRMATION_MISMATCH", "The client confirmation does not match.", EXIT_INPUT,
                               details={"expectedConfirmation": expected})
        if any(item.get("clientRef") == client_ref and item.get("status") != "revoked" for item in index["profiles"].values()):
            raise AdvisorError("OAUTH_CLIENT_IN_USE", "Revoke or forget profiles that use this client first.", EXIT_CONFIGURATION)
        removed = self.secrets.delete(f"oauth-client:{client_ref}")
        self.state.update(lambda state: state["clients"].pop(client_ref, None))
        return {"clientRef": client_ref, "removed": removed}
