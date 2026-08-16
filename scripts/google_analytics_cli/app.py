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
    site_context = site_sub.add_parser("context")
    site_context.add_argument("--project-root", required=True, type=Path)
    site_context.add_argument("--measurement-plan", required=True, type=Path)
    site_context.add_argument("--baseline", type=Path)
    site_context.add_argument("--json", action="store_true")
    site_plan = site_sub.add_parser("plan")
    site_plan.add_argument("--context", required=True, type=Path)
    site_plan.add_argument("--changes", required=True, type=Path)
    site_plan.add_argument("--patch", required=True, type=Path)
    site_plan.add_argument("--json", action="store_true")
    site_show = site_sub.add_parser("show")
    site_show.add_argument("--plan", required=True, type=Path)
    site_show.add_argument("--json", action="store_true")
    site_apply = site_sub.add_parser("apply")
    site_apply.add_argument("--plan", required=True, type=Path)
    site_apply.add_argument("--confirm-sha256", required=True)
    site_apply.add_argument("--json", action="store_true")
    site_verify = site_sub.add_parser("verify")
    site_verify.add_argument("--journal", required=True, type=Path)
    site_verify.add_argument("--json", action="store_true")
    site_reconcile = site_sub.add_parser("reconcile")
    site_reconcile.add_argument("--journal", required=True, type=Path)
    site_reconcile.add_argument("--json", action="store_true")
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
    measurement = sub.add_parser("measurement")
    measurement_sub = measurement.add_subparsers(dest="measurement_command", required=True, parser_class=Parser)
    measurement_context = measurement_sub.add_parser("context")
    measurement_context.add_argument("--project-root", required=True, type=Path)
    measurement_context.add_argument("--profile", required=True)
    baseline_choice = measurement_context.add_mutually_exclusive_group(required=True)
    baseline_choice.add_argument("--baseline", type=Path)
    baseline_choice.add_argument("--without-baseline", action="store_true")
    measurement_context.add_argument("--answers", type=Path)
    measurement_context.add_argument("--json", action="store_true")
    measurement_draft = measurement_sub.add_parser("draft")
    measurement_draft.add_argument("--context", required=True, type=Path)
    measurement_draft.add_argument("--output-dir", required=True, type=Path)
    measurement_draft.add_argument("--json", action="store_true")
    measurement_show = measurement_sub.add_parser("show")
    measurement_show.add_argument("--input", required=True, type=Path)
    measurement_show.add_argument("--format", choices=["plain", "json"], default="plain")
    measurement_show.add_argument("--json", action="store_true")
    measurement_approve = measurement_sub.add_parser("approve")
    measurement_approve.add_argument("--input", required=True, type=Path)
    measurement_approve.add_argument("--confirm-sha256", required=True)
    measurement_approve.add_argument("--json", action="store_true")
    measurement_migrate = measurement_sub.add_parser("migrate")
    measurement_migrate.add_argument("--input", required=True, type=Path)
    measurement_migrate.add_argument("--json", action="store_true")
    ga4 = sub.add_parser("ga4")
    ga4_sub = ga4.add_subparsers(dest="ga4_command", required=True, parser_class=Parser)
    ga4_capabilities = ga4_sub.add_parser("capabilities")
    ga4_capabilities.add_argument("--profile", required=True)
    ga4_capabilities.add_argument("--property", dest="property_name", required=True)
    ga4_capabilities.add_argument("--json", action="store_true")
    ga4_plan = ga4_sub.add_parser("plan")
    ga4_plan.add_argument("--profile", required=True)
    ga4_plan.add_argument("--measurement-plan", required=True, type=Path)
    ga4_plan.add_argument("--changes", required=True, type=Path)
    ga4_plan.add_argument("--json", action="store_true")
    ga4_show = ga4_sub.add_parser("show")
    ga4_show.add_argument("--plan", required=True, type=Path)
    ga4_show.add_argument("--json", action="store_true")
    ga4_apply = ga4_sub.add_parser("apply")
    ga4_apply.add_argument("--plan", required=True, type=Path)
    ga4_apply.add_argument("--confirm-sha256", required=True)
    ga4_apply.add_argument("--json", action="store_true")
    ga4_reconcile = ga4_sub.add_parser("reconcile")
    ga4_reconcile.add_argument("--journal", required=True, type=Path)
    ga4_reconcile.add_argument("--json", action="store_true")
    mp = sub.add_parser("mp")
    mp_sub = mp.add_subparsers(dest="mp_command", required=True, parser_class=Parser)
    mp_plan = mp_sub.add_parser("delivery-plan")
    mp_plan.add_argument("--measurement-plan", required=True, type=Path)
    mp_plan.add_argument("--payload", required=True, type=Path)
    mp_plan.add_argument("--credential-ref", required=True)
    mp_plan.add_argument("--measurement-id", required=True)
    mp_plan.add_argument("--endpoint-class", required=True, choices=["debug", "production"])
    mp_plan.add_argument("--json", action="store_true")
    mp_validate = mp_sub.add_parser("validate")
    mp_validate.add_argument("--plan", required=True, type=Path)
    mp_validate.add_argument("--confirm-sha256", required=True)
    mp_validate.add_argument("--json", action="store_true")
    mp_send = mp_sub.add_parser("send")
    mp_send.add_argument("--plan", required=True, type=Path)
    mp_send.add_argument("--confirm-sha256", required=True)
    mp_send.add_argument("--json", action="store_true")
    mp_reconcile = mp_sub.add_parser("reconcile")
    mp_reconcile.add_argument("--journal", required=True, type=Path)
    mp_reconcile.add_argument("--json", action="store_true")
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
    if args.group == "site":
        if args.site_command == "inspect":
            from .site_scanner import inspect_site

            result = inspect_site(args.project_root)
            return "site inspect", "partial" if result["truncated"] else "ready", result
        from .website_service import WebsiteService

        service = WebsiteService()
        if args.site_command == "context":
            result = service.context(args.project_root, args.measurement_plan, args.baseline)
            return "site context", result["status"], result
        if args.site_command == "plan":
            result = service.plan(args.context, args.changes, args.patch)
            return "site plan", result["status"], result
        if args.site_command == "show":
            result = service.show(args.plan)
            return "site show", result["status"], result
        if args.site_command == "apply":
            result = service.apply(args.plan, args.confirm_sha256)
            return "site apply", result["status"], result
        if args.site_command == "verify":
            result = service.verify(args.journal)
            return "site verify", result["status"], result
        if args.site_command == "reconcile":
            result = service.reconcile(args.journal)
            return "site reconcile", result["status"], result
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
    if args.group == "measurement":
        from .measurement_service import MeasurementService

        service = MeasurementService()
        if args.measurement_command == "context":
            result = service.context(
                args.project_root, args.profile, baseline_path=args.baseline,
                without_baseline=args.without_baseline, answers_path=args.answers,
            )
            return "measurement context", result["status"], result
        if args.measurement_command == "draft":
            result = service.draft(args.context, args.output_dir)
            return "measurement draft", result["plan"]["status"], result
        if args.measurement_command == "show":
            result = service.show(args.input, args.format)
            return "measurement show", result["status"], result
        if args.measurement_command == "approve":
            result = service.approve(args.input, args.confirm_sha256)
            return "measurement approve", "approved", result
        if args.measurement_command == "migrate":
            result = service.migrate(args.input)
            return "measurement migrate", "blocked", result
    if args.group == "ga4":
        from .ga4_mutation_service import Ga4MutationService

        service = Ga4MutationService()
        if args.ga4_command == "capabilities":
            return "ga4 capabilities", "ready", service.capabilities(args.profile, args.property_name)
        if args.ga4_command == "plan":
            result = service.plan(args.profile, args.measurement_plan, args.changes)
            return "ga4 plan", result["status"], result
        if args.ga4_command == "show":
            result = service.show(args.plan)
            return "ga4 show", result["status"], result
        if args.ga4_command == "apply":
            result = service.apply(args.plan, args.confirm_sha256)
            return "ga4 apply", result["status"], result
        if args.ga4_command == "reconcile":
            result = service.reconcile(args.journal)
            return "ga4 reconcile", result["status"], result
    if args.group == "mp":
        from .measurement_protocol_service import MeasurementProtocolService

        service = MeasurementProtocolService()
        if args.mp_command == "delivery-plan":
            result = service.delivery_plan(args.measurement_plan, args.payload, args.credential_ref, args.measurement_id, args.endpoint_class)
            return "mp delivery-plan", result["status"], result
        if args.mp_command == "validate":
            result = service.validate(args.plan, args.confirm_sha256)
            return "mp validate", result["status"], result
        if args.mp_command == "send":
            result = service.send(args.plan, args.confirm_sha256)
            return "mp send", result["status"], result
        if args.mp_command == "reconcile":
            result = service.reconcile(args.journal)
            return "mp reconcile", result["status"], result
    raise AdvisorError("INVALID_ARGUMENTS", "Unsupported command.", EXIT_INPUT)
