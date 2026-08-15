"""Argument parsing and command dispatch."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import __version__
from .auth import AuthService
from .contracts import ARTIFACTS, validate_artifact
from .errors import AdvisorError, EXIT_INPUT
from .runtime import discover, doctor, installation_guide, require_runtime
from .version_check import check_version, set_disabled


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise AdvisorError("INVALID_ARGUMENTS", message, EXIT_INPUT, next_action="Review the command help and try again.")


def build_parser() -> Parser:
    parser = Parser(prog="google-analytics", description="Google Analytics Advisor local runtime")
    sub = parser.add_subparsers(dest="group", required=True, parser_class=Parser)
    version = sub.add_parser("version")
    version.add_argument("--json", action="store_true")
    version.add_argument("--check", action="store_true")
    version.add_argument("--endpoint")
    version.add_argument("--force", action="store_true")
    version.add_argument("--disable-check", action="store_true")
    version.add_argument("--enable-check", action="store_true")
    runtime = sub.add_parser("runtime")
    runtime_sub = runtime.add_subparsers(dest="runtime_command", required=True, parser_class=Parser)
    detect = runtime_sub.add_parser("detect")
    detect.add_argument("--json", action="store_true")
    guide = runtime_sub.add_parser("install-guide")
    guide.add_argument("--json", action="store_true")
    doctor_parser = sub.add_parser("doctor")
    doctor_parser.add_argument("--json", action="store_true")
    contracts = sub.add_parser("contracts")
    contract_sub = contracts.add_subparsers(dest="contract_command", required=True, parser_class=Parser)
    validate = contract_sub.add_parser("validate")
    validate.add_argument("--schema", required=True, choices=sorted(ARTIFACTS))
    validate.add_argument("--input", required=True, type=Path)
    validate.add_argument("--json", action="store_true")
    auth = sub.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True, parser_class=Parser)
    preview = auth_sub.add_parser("consent-preview")
    preview.add_argument("--json", action="store_true")
    client = auth_sub.add_parser("client")
    client_sub = client.add_subparsers(dest="client_command", required=True, parser_class=Parser)
    client_import = client_sub.add_parser("import")
    client_import.add_argument("--file", required=True, type=Path)
    client_import.add_argument("--json", action="store_true")
    client_list = client_sub.add_parser("list")
    client_list.add_argument("--json", action="store_true")
    client_remove = client_sub.add_parser("remove")
    client_remove.add_argument("--client", required=True)
    client_remove.add_argument("--confirm-client", required=True)
    client_remove.add_argument("--json", action="store_true")
    login = auth_sub.add_parser("login")
    login.add_argument("--client", required=True)
    login.add_argument("--json", action="store_true")
    profiles = auth_sub.add_parser("profiles")
    profiles_sub = profiles.add_subparsers(dest="profiles_command", required=True, parser_class=Parser)
    profiles_list = profiles_sub.add_parser("list")
    profiles_list.add_argument("--json", action="store_true")
    auth_status = auth_sub.add_parser("status")
    auth_status.add_argument("--profile")
    auth_status.add_argument("--json", action="store_true")
    auth_use = auth_sub.add_parser("use")
    auth_use.add_argument("--profile", required=True)
    auth_use.add_argument("--json", action="store_true")
    auth_doctor = auth_sub.add_parser("doctor")
    auth_doctor.add_argument("--profile")
    auth_doctor.add_argument("--json", action="store_true")
    auth_revoke = auth_sub.add_parser("revoke")
    auth_revoke.add_argument("--profile", required=True)
    auth_revoke.add_argument("--confirm-profile", required=True)
    auth_revoke.add_argument("--json", action="store_true")
    forget = auth_sub.add_parser("forget-local")
    forget.add_argument("--profile", required=True)
    forget.add_argument("--confirm-profile", required=True)
    forget.add_argument("--json", action="store_true")
    resources = sub.add_parser("resources")
    resources_sub = resources.add_subparsers(dest="resources_command", required=True, parser_class=Parser)
    resources_list = resources_sub.add_parser("list")
    resources_list.add_argument("--profile", required=True)
    resources_list.add_argument("--json", action="store_true")
    site = sub.add_parser("site")
    site_sub = site.add_subparsers(dest="site_command", required=True, parser_class=Parser)
    site_inspect = site_sub.add_parser("inspect")
    site_inspect.add_argument("--project-root", required=True, type=Path)
    site_inspect.add_argument("--json", action="store_true")
    audit = sub.add_parser("audit")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True, parser_class=Parser)
    baseline = audit_sub.add_parser("baseline")
    baseline.add_argument("--profile", required=True)
    baseline.add_argument("--project-root", required=True, type=Path)
    baseline.add_argument("--property", dest="property_name")
    baseline.add_argument("--stream", dest="stream_name")
    baseline.add_argument("--gtm-container")
    baseline.add_argument("--experimental-admin-alpha", action="store_true")
    baseline.add_argument("--json", action="store_true")
    return parser


def dispatch(argv: list[str]) -> tuple[str, str, Any]:
    args = build_parser().parse_args(argv)
    if args.group == "version":
        if args.disable_check and args.enable_check:
            raise AdvisorError("INVALID_ARGUMENTS", "Choose either --disable-check or --enable-check.", EXIT_INPUT)
        if args.disable_check or args.enable_check:
            return "version preferences", "ready", set_disabled(args.disable_check)
        if args.check:
            result = check_version(endpoint=args.endpoint, force=args.force)
            return "version check", result["status"], result
        return "version", "ready", {"version": __version__, "schemaVersion": 1}
    if args.group == "runtime" and args.runtime_command == "detect":
        result = discover()
        require_runtime(result)
        return "runtime detect", "ready", result
    if args.group == "runtime" and args.runtime_command == "install-guide":
        result = discover()
        status = "ready" if result.get("selected") else "action_required"
        return "runtime install-guide", status, installation_guide(result)
    if args.group == "doctor":
        result = doctor()
        require_runtime(result["runtime"])
        if not all(result["writable"].values()) or not result["tls"].get("available"):
            return "doctor", "degraded", result
        return "doctor", "ready", result
    if args.group == "contracts" and args.contract_command == "validate":
        return "contracts validate", "valid", validate_artifact(args.schema, args.input)
    if args.group == "auth":
        if args.auth_command == "consent-preview":
            from .oauth import SCOPES, SCOPE_GROUPS

            return "auth consent-preview", "authorization_required", {
                "profile": "full-v1",
                "scopes": list(SCOPES),
                "permissionGroups": list(SCOPE_GROUPS),
                "mutationApprovalGranted": False,
            }
        service = AuthService()
        if args.auth_command == "client" and args.client_command == "import":
            return "auth client import", "client_ready", service.client_import(args.file)
        if args.auth_command == "client" and args.client_command == "list":
            return "auth client list", "ready", service.clients()
        if args.auth_command == "client" and args.client_command == "remove":
            return "auth client remove", "removed", service.client_remove(args.client, args.confirm_client)
        if args.auth_command == "login":
            return "auth login", "connected", service.login(args.client)
        if args.auth_command == "profiles" and args.profiles_command == "list":
            return "auth profiles list", "ready", service.profiles()
        if args.auth_command == "status":
            result = service.status(args.profile)
            return "auth status", result["status"], result
        if args.auth_command == "use":
            return "auth use", "ready", service.use(args.profile)
        if args.auth_command == "doctor":
            result = service.doctor(args.profile)
            return "auth doctor", result["status"], result
        if args.auth_command == "revoke":
            return "auth revoke", "revoked", service.revoke(args.profile, args.confirm_profile)
        if args.auth_command == "forget-local":
            return "auth forget-local", "removed", service.forget_local(args.profile, args.confirm_profile)
    if args.group == "site" and args.site_command == "inspect":
        from .site_scanner import inspect_site

        result = inspect_site(args.project_root)
        return "site inspect", "partial" if result["truncated"] else "ready", result
    if args.group == "resources" and args.resources_command == "list":
        from .baseline_audit import BaselineService

        result = BaselineService().resources(args.profile)
        return "resources list", "partial" if result["limitations"] else "ready", result
    if args.group == "audit" and args.audit_command == "baseline":
        from .baseline_audit import BaselineService

        result = BaselineService().audit(
            args.profile, args.project_root, property_name=args.property_name,
            stream_name=args.stream_name, gtm_container=args.gtm_container,
            experimental_alpha=args.experimental_admin_alpha,
        )
        return "audit baseline", result["audit"]["completeness"], result
    raise AdvisorError("INVALID_ARGUMENTS", "Unsupported command.", EXIT_INPUT)
