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
from .oauth import (
    BASE_SCOPES,
    BASE_SCOPE_SET_REVISION,
    SCOPE_GROUPS,
    SEARCH_CONSOLE_SCOPES,
    TARGET_SCOPES,
    TARGET_SCOPE_SET_REVISION,
    authorize,
    missing_scopes,
    normalize_granted_scopes,
    refresh,
    revoke,
)
from .secret_store import SecretStore, secret_store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _profile_confirmation(profile_id: str, client_ref: str) -> str:
    return hashlib.sha256(f"{profile_id}:{client_ref}".encode("utf-8")).hexdigest()[:12]


def _canonical_profile_ref(profile_id: str) -> str:
    return f"oauth-profile:{profile_id}"


def _staging_profile_ref(profile_id: str) -> str:
    return f"oauth-profile:{profile_id}:scope-set-{TARGET_SCOPE_SET_REVISION}"


def _scope_capabilities(granted_scopes: list[str] | tuple[str, ...]) -> dict[str, Any]:
    normalized = list(normalize_granted_scopes(granted_scopes))
    analytics_missing = missing_scopes(normalized, BASE_SCOPES)
    search_missing = missing_scopes(normalized, SEARCH_CONSOLE_SCOPES)
    target_missing = missing_scopes(normalized, TARGET_SCOPES)
    if not search_missing and not analytics_missing:
        granted_revision = TARGET_SCOPE_SET_REVISION
    elif not analytics_missing:
        granted_revision = BASE_SCOPE_SET_REVISION
    else:
        granted_revision = "unknown"
    return {
        "grantedScopes": normalized,
        "grantedScopeSetRevision": granted_revision,
        "targetScopeSetRevision": TARGET_SCOPE_SET_REVISION,
        "missingTargetScopes": target_missing,
        "capabilities": {
            "analyticsGtm": {
                "status": "ready" if not analytics_missing else "authorization_required",
                "missingScopes": analytics_missing,
            },
            "searchConsole": {
                "status": "ready" if not search_missing else "authorization_required",
                "missingScopes": search_missing,
            },
        },
        "authorizationUpgradeRequired": bool(target_missing),
    }


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

    @staticmethod
    def _credential_ref(profile_id: str, metadata: dict[str, Any] | None = None) -> str:
        value = (metadata or {}).get("credentialRef")
        return str(value) if isinstance(value, str) and value else _canonical_profile_ref(profile_id)

    def _load_profile_payload_ref(self, credential_ref: str) -> dict[str, Any]:
        try:
            value = json.loads(self.secrets.get(credential_ref).decode("utf-8"))
        except AdvisorError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise AdvisorError("SECRET_STORE_CORRUPT", "The protected authorization profile is damaged.", EXIT_CONFIGURATION) from exc
        if not isinstance(value, dict) or not value.get("refreshToken") or not value.get("clientRef"):
            raise AdvisorError("SECRET_STORE_CORRUPT", "The protected authorization profile is incomplete.", EXIT_CONFIGURATION)
        return value

    def _load_profile_payload(
        self, profile_id: str, metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if metadata is None:
            _, metadata = self._resolve_profile(profile_id)
        return self._load_profile_payload_ref(self._credential_ref(profile_id, metadata))

    def _rotate_profile_payload(
        self, profile_id: str, client_ref: str, payload: dict[str, Any], *, timestamp: str,
    ) -> list[dict[str, Any]]:
        """Switch credentials through a protected staging slot before touching the legacy slot."""
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        canonical_ref = _canonical_profile_ref(profile_id)
        staging_ref = _staging_profile_ref(profile_id)
        self.secrets.put(staging_ref, encoded)
        try:
            if self.secrets.get(staging_ref) != encoded:
                raise AdvisorError(
                    "SECRET_STORE_READBACK_FAILED",
                    "The protected authorization upgrade could not be read back safely.",
                    EXIT_CONFIGURATION,
                )

            def activate_staging(state: dict[str, Any]) -> None:
                previous = state["profiles"].get(profile_id, {})
                state["profiles"][profile_id] = {
                    "profileId": profile_id,
                    "clientRef": client_ref,
                    "status": "connected",
                    "confirmation": _profile_confirmation(profile_id, client_ref),
                    "credentialRef": staging_ref,
                    "credentialState": "staging",
                    "createdAt": previous.get("createdAt", timestamp),
                    "updatedAt": timestamp,
                }
                state["activeProfileId"] = profile_id

            self.state.update(activate_staging)
        except Exception as exc:
            try:
                self.secrets.delete(staging_ref)
            except AdvisorError:
                pass
            if isinstance(exc, AdvisorError):
                raise
            raise AdvisorError(
                "AUTH_CREDENTIAL_ROTATION_FAILED",
                "The protected authorization upgrade could not be activated; the previous profile was preserved.",
                EXIT_CONFIGURATION,
                details={"reason": type(exc).__name__},
            ) from exc

        warnings: list[dict[str, Any]] = []
        try:
            self.secrets.put(canonical_ref, encoded)
            if self.secrets.get(canonical_ref) != encoded:
                raise AdvisorError(
                    "SECRET_STORE_READBACK_FAILED",
                    "The canonical authorization credential could not be read back safely.",
                    EXIT_CONFIGURATION,
                )

            def activate_canonical(state: dict[str, Any]) -> None:
                state["profiles"][profile_id].update({
                    "credentialRef": canonical_ref,
                    "credentialState": "current",
                    "updatedAt": timestamp,
                })

            self.state.update(activate_canonical)
        except Exception as exc:
            warnings.append({
                "code": "AUTH_CREDENTIAL_FINALIZATION_PENDING",
                "message": "The upgraded credential is active in protected staging storage; canonical cleanup is pending.",
                "reason": type(exc).__name__,
            })
            return warnings

        try:
            self.secrets.delete(staging_ref)
        except AdvisorError as exc:
            warnings.append({
                "code": "AUTH_CREDENTIAL_CLEANUP_PENDING",
                "message": "The upgraded canonical credential is active, but protected staging cleanup is pending.",
                "reason": exc.code,
            })
        return warnings

    def _finalize_staging_credential(
        self, profile_id: str, metadata: dict[str, Any], payload: dict[str, Any], *, timestamp: str,
    ) -> list[dict[str, Any]]:
        if self._credential_ref(profile_id, metadata) != _staging_profile_ref(profile_id):
            return []
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        canonical_ref = _canonical_profile_ref(profile_id)
        warnings: list[dict[str, Any]] = []
        try:
            self.secrets.put(canonical_ref, encoded)
            if self.secrets.get(canonical_ref) != encoded:
                raise AdvisorError(
                    "SECRET_STORE_READBACK_FAILED",
                    "The canonical authorization credential could not be read back safely.",
                    EXIT_CONFIGURATION,
                )
            self.state.update(lambda state: state["profiles"][profile_id].update({
                "credentialRef": canonical_ref,
                "credentialState": "current",
                "updatedAt": timestamp,
            }))
            try:
                self.secrets.delete(_staging_profile_ref(profile_id))
            except AdvisorError as exc:
                warnings.append({
                    "code": "AUTH_CREDENTIAL_CLEANUP_PENDING",
                    "message": "Protected staging cleanup is still pending.",
                    "reason": exc.code,
                })
        except Exception as exc:
            warnings.append({
                "code": "AUTH_CREDENTIAL_FINALIZATION_PENDING",
                "message": "The upgraded credential remains active in protected staging storage.",
                "reason": type(exc).__name__,
            })
        return warnings

    def _delete_profile_credentials(
        self, profile_id: str, metadata: dict[str, Any],
    ) -> tuple[bool, list[dict[str, Any]]]:
        active_ref = self._credential_ref(profile_id, metadata)
        removed = self.secrets.delete(active_ref)
        warnings: list[dict[str, Any]] = []
        for credential_ref in {_canonical_profile_ref(profile_id), _staging_profile_ref(profile_id)} - {active_ref}:
            try:
                removed = self.secrets.delete(credential_ref) or removed
            except AdvisorError as exc:
                warnings.append({
                    "code": "AUTH_CREDENTIAL_CLEANUP_PENDING",
                    "message": "An inactive protected authorization slot could not be cleaned up.",
                    "reason": exc.code,
                })
        return removed, warnings

    def login(self, client_ref: str, **authorize_kwargs: Any) -> dict[str, Any]:
        index = self.state.read()
        if client_ref not in index["clients"]:
            raise AdvisorError("OAUTH_CLIENT_NOT_FOUND", "The selected OAuth client is not imported.", EXIT_INPUT)
        client = load_client(client_ref, secrets=self.secrets)
        tokens, identity = authorize(
            client, form=self.form, json_transport=self.json_transport, scopes=TARGET_SCOPES,
            **authorize_kwargs,
        )
        subject = str(identity["sub"])
        profile_id = "profile-" + hashlib.sha256(f"{client_ref}:{subject}".encode("utf-8")).hexdigest()[:16]
        timestamp = _now()
        payload = {
            "schemaVersion": 1,
            "clientRef": client_ref,
            "refreshToken": tokens["refresh_token"],
            "grantedScopes": list(normalize_granted_scopes(str(tokens.get("scope", "")))),
            "identity": {
                "sub": subject,
                "email": str(identity["email"]),
                "emailVerified": bool(identity.get("email_verified", False)),
            },
        }
        warnings = self._rotate_profile_payload(
            profile_id, client_ref, payload, timestamp=timestamp,
        )
        scope_state = _scope_capabilities(payload["grantedScopes"])
        return {
            "profileId": profile_id,
            "email": payload["identity"]["email"],
            "emailVerified": payload["identity"]["emailVerified"],
            "clientRef": client_ref,
            **scope_state,
            "status": "connected",
            "credentialWarnings": warnings,
        }

    def upgrade(self, profile_id: str | None = None, **authorize_kwargs: Any) -> dict[str, Any]:
        selected, metadata = self._resolve_profile(profile_id)
        if metadata.get("status") == "revoked":
            raise AdvisorError(
                "AUTH_PROFILE_REVOKED", "The selected authorization profile was revoked.", EXIT_CONFIGURATION,
                next_action="Create a new authorization instead of upgrading this profile.",
            )
        current = self._load_profile_payload(selected, metadata)
        current_scope_state = _scope_capabilities(current.get("grantedScopes", []))
        if not current_scope_state["authorizationUpgradeRequired"]:
            warnings = self._finalize_staging_credential(
                selected, metadata, current, timestamp=_now(),
            )
            return {
                "profileId": selected,
                "email": current.get("identity", {}).get("email"),
                "clientRef": current["clientRef"],
                **current_scope_state,
                "status": "already_current",
                "previousProfilePreserved": True,
                "credentialWarnings": warnings,
            }

        client = load_client(current["clientRef"], secrets=self.secrets)
        try:
            tokens, identity = authorize(
                client, form=self.form, json_transport=self.json_transport, scopes=TARGET_SCOPES,
                **authorize_kwargs,
            )
        except AdvisorError as exc:
            exc.details = {**exc.details, "profileId": selected, "previousProfilePreserved": True}
            raise
        expected_subject = str(current.get("identity", {}).get("sub", ""))
        if not expected_subject or str(identity.get("sub", "")) != expected_subject:
            raise AdvisorError(
                "OAUTH_PROFILE_IDENTITY_MISMATCH",
                "Google returned a different account than the selected authorization profile.",
                EXIT_CONFIGURATION,
                details={"profileId": selected, "previousProfilePreserved": True},
                next_action="Choose the original Google account, or use auth login to create a separate profile.",
            )
        timestamp = _now()
        upgraded = {
            "schemaVersion": 1,
            "clientRef": current["clientRef"],
            "refreshToken": tokens["refresh_token"],
            "grantedScopes": list(normalize_granted_scopes(str(tokens.get("scope", "")))),
            "identity": {
                "sub": expected_subject,
                "email": str(identity["email"]),
                "emailVerified": bool(identity.get("email_verified", False)),
            },
        }
        try:
            warnings = self._rotate_profile_payload(
                selected, current["clientRef"], upgraded, timestamp=timestamp,
            )
        except AdvisorError as exc:
            exc.details = {**exc.details, "profileId": selected, "previousProfilePreserved": True}
            raise
        return {
            "profileId": selected,
            "email": upgraded["identity"]["email"],
            "emailVerified": upgraded["identity"]["emailVerified"],
            "clientRef": current["clientRef"],
            **_scope_capabilities(upgraded["grantedScopes"]),
            "status": "upgraded",
            "previousProfilePreserved": True,
            "credentialWarnings": warnings,
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
                "grantedScopeSetRevision": "none",
                "targetScopeSetRevision": TARGET_SCOPE_SET_REVISION,
                "missingTargetScopes": list(TARGET_SCOPES),
                "capabilities": {
                    "analyticsGtm": {"status": "unavailable", "missingScopes": list(BASE_SCOPES)},
                    "searchConsole": {"status": "unavailable", "missingScopes": list(SEARCH_CONSOLE_SCOPES)},
                },
                "authorizationUpgradeRequired": False,
                "confirmation": metadata["confirmation"],
            }
        payload = self._load_profile_payload(selected, metadata)
        scope_state = _scope_capabilities(payload.get("grantedScopes", []))
        return {
            "profileId": selected,
            "clientRef": metadata["clientRef"],
            "status": metadata["status"],
            "active": selected == self.state.read().get("activeProfileId"),
            "email": payload["identity"]["email"],
            "emailVerified": payload["identity"].get("emailVerified", False),
            **scope_state,
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
        payload = self._load_profile_payload(selected, metadata)
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
        search_console_ready = not missing_scopes(
            payload.get("grantedScopes", []), SEARCH_CONSOLE_SCOPES,
        )
        probes = run_probes(
            token, transport=self.json_transport, search_console_enabled=search_console_ready,
        )
        index = self.state.read()
        client_meta = index["clients"].get(payload["clientRef"], {})
        project_id = client_meta.get("projectId")
        service_names = {
            "analyticsAdmin": "analyticsadmin.googleapis.com",
            "analyticsData": "analyticsdata.googleapis.com",
            "tagManager": "tagmanager.googleapis.com",
            "searchConsole": "searchconsole.googleapis.com",
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
            "analyticsGtmReady": all(
                probes.get(key, {}).get("status") in {"ready", "not_verifiable_no_property"}
                for key in ("analyticsAdmin", "analyticsData", "tagManager")
            ),
            "requestedCloudScopes": False,
            "mutationPerformed": False,
        }

    def forget_local(self, profile_id: str, confirmation: str) -> dict[str, Any]:
        selected, metadata = self._resolve_profile(profile_id)
        if confirmation != metadata["confirmation"]:
            raise AdvisorError("AUTH_CONFIRMATION_MISMATCH", "The profile confirmation does not match.", EXIT_INPUT)
        removed, warnings = self._delete_profile_credentials(selected, metadata)

        def forget(index: dict[str, Any]) -> None:
            index["profiles"].pop(selected, None)
            if index.get("activeProfileId") == selected:
                index["activeProfileId"] = next(iter(index["profiles"]), None)

        self.state.update(forget)
        return {
            "profileId": selected,
            "localCredentialRemoved": removed,
            "googleGrantRevoked": False,
            "credentialWarnings": warnings,
        }

    def revoke(self, profile_id: str, confirmation: str) -> dict[str, Any]:
        selected, metadata = self._resolve_profile(profile_id)
        if confirmation != metadata["confirmation"]:
            raise AdvisorError("AUTH_CONFIRMATION_MISMATCH", "The profile confirmation does not match.", EXIT_INPUT)
        payload = self._load_profile_payload(selected, metadata)
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
        _removed, warnings = self._delete_profile_credentials(selected, metadata)
        def mark_revoked(index: dict[str, Any]) -> None:
            index["profiles"][selected].update({"status": "revoked", "updatedAt": _now()})
            if index.get("activeProfileId") == selected:
                index["activeProfileId"] = next(
                    (profile_id for profile_id, item in index["profiles"].items() if item.get("status") == "connected"),
                    None,
                )

        self.state.update(mark_revoked)
        return {
            "profileId": selected,
            "googleGrantRevoked": True,
            "localCredentialRemoved": True,
            "credentialWarnings": warnings,
        }

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
