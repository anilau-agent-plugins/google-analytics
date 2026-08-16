"""Stage 8 policy checks for route, consent, events, ecommerce, and commands."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import AdvisorError, EXIT_INPUT


INTENTS = {
    "DIRECT_TAG", "GTM_CONTAINER", "DATA_LAYER_EVENT", "GTAG_EVENT", "SPA_PAGE_VIEW",
    "CONSENT_DEFAULT", "CONSENT_UPDATE", "MP_SERVER_ADAPTER",
}
PUBLIC_ID = re.compile(r"^(?:G-[A-Z0-9]{5,}|GT-[A-Z0-9]{5,}|GTM-[A-Z0-9]{4,})$")
FORBIDDEN_COMMAND = re.compile(r"(?:^|[-_:])(install|update|upgrade|migrate|deploy|publish|ssh|scp|curl|wget)(?:$|[-_:])", re.I)
CONSENT_SIGNALS = {"analytics_storage", "ad_storage", "ad_user_data", "ad_personalization"}


def validate_request(request: dict[str, Any], context: dict[str, Any], measurement: dict[str, Any]) -> list[dict[str, Any]]:
    if request.get("projectRoot") != context.get("projectRoot"):
        raise AdvisorError("PROJECT_ROOT_MISMATCH", "The website change request belongs to another project.", EXIT_INPUT)
    for key, expected in {
        "contextId": context.get("contextId"), "contextSha256": context.get("contextSha256"),
        "measurementPlanId": measurement.get("planId"), "measurementPlanSha256": measurement.get("contentSha256"),
    }.items():
        if request.get(key) != expected:
            raise AdvisorError("WEBSITE_BINDING_MISMATCH", f"The website change request has a stale {key} binding.", EXIT_INPUT)
    if context.get("blockers"):
        raise AdvisorError("WEBSITE_CONTEXT_BLOCKED", "Website context blockers must be resolved before planning a file change.", EXIT_INPUT, details={"blockers": context["blockers"]})
    route = request.get("route")
    existing_direct = context.get("analytics", {}).get("directIds", [])
    existing_gtm = context.get("analytics", {}).get("gtmIds", [])
    if existing_direct and route != "direct" or existing_gtm and route != "gtm":
        raise AdvisorError("ANALYTICS_ROUTE_CONFLICT", "The request would replace the existing analytics route without an explicit migration design.", EXIT_INPUT)
    intents = request.get("intents")
    if not isinstance(intents, list) or not intents or len(intents) > 50 or not all(isinstance(item, dict) for item in intents):
        raise AdvisorError("INVALID_WEBSITE_CHANGE", "A website change requires 1 to 50 typed intents.", EXIT_INPUT)
    approved_events = {item.get("name"): item for item in measurement.get("events", []) if isinstance(item, dict)}
    pageview_count = 0
    consent_kinds: set[str] = set()
    for item in intents:
        kind = item.get("kind")
        if kind not in INTENTS:
            raise AdvisorError("INVALID_WEBSITE_CHANGE", "An unsupported website intent was requested.", EXIT_INPUT, details={"kind": kind})
        public_id = item.get("publicId")
        if public_id is not None and (not isinstance(public_id, str) or not PUBLIC_ID.fullmatch(public_id)):
            raise AdvisorError("INVALID_PUBLIC_ID", "A Google tag or GTM container ID is invalid.", EXIT_INPUT)
        if kind == "DIRECT_TAG" and (route != "direct" or not str(public_id).startswith(("G-", "GT-"))):
            raise AdvisorError("ANALYTICS_ROUTE_CONFLICT", "DIRECT_TAG requires the direct route and a GA4/Google tag ID.", EXIT_INPUT)
        if kind == "GTM_CONTAINER" and (route != "gtm" or not str(public_id).startswith("GTM-")):
            raise AdvisorError("ANALYTICS_ROUTE_CONFLICT", "GTM_CONTAINER requires the GTM route and a GTM container ID.", EXIT_INPUT)
        if kind in {"DATA_LAYER_EVENT", "GTAG_EVENT", "MP_SERVER_ADAPTER"}:
            event_name = item.get("eventName")
            if event_name not in approved_events:
                raise AdvisorError("EVENT_NOT_PLANNED", "A website event is not approved in the measurement plan.", EXIT_INPUT, details={"eventName": event_name})
            owner = approved_events[event_name].get("collectionOwner")
            expected = {"DATA_LAYER_EVENT": "browser-gtm", "GTAG_EVENT": "browser-gtag"}.get(kind)
            if expected and owner not in {expected, "existing"}:
                raise AdvisorError("EVENT_OWNER_CONFLICT", "The requested browser event conflicts with its approved collection owner.", EXIT_INPUT, details={"eventName": event_name})
            if kind == "MP_SERVER_ADAPTER" and owner not in {"backend-mp", "crm-mp", "payment-webhook-mp"}:
                raise AdvisorError("EVENT_OWNER_CONFLICT", "Measurement Protocol requires an approved server-side event owner.", EXIT_INPUT)
            if not isinstance(item.get("authoritativeSource"), str) or not item["authoritativeSource"].strip() or not isinstance(item.get("idempotency"), str) or not item["idempotency"].strip():
                raise AdvisorError("EVENT_IMPLEMENTATION_INCOMPLETE", "Each event needs an authoritative completion source and an idempotency rule.", EXIT_INPUT)
            if event_name in {"purchase", "refund"}:
                parameters = item.get("parameters")
                required = {"transaction_id", "value", "currency", "items"} if event_name == "purchase" else {"transaction_id"}
                if not isinstance(parameters, list) or not required.issubset(set(parameters)):
                    raise AdvisorError("ECOMMERCE_IMPLEMENTATION_INCOMPLETE", "Ecommerce intent lacks required approved parameters.", EXIT_INPUT)
        if kind == "SPA_PAGE_VIEW":
            pageview_count += 1
            if item.get("strategy") not in {"automatic-history", "manual-router"}:
                raise AdvisorError("SPA_PAGEVIEW_UNRESOLVED", "Choose one explicit SPA page-view strategy.", EXIT_INPUT)
        if kind in {"CONSENT_DEFAULT", "CONSENT_UPDATE"}:
            consent_kinds.add(kind)
            signals = item.get("signals")
            if not isinstance(signals, dict) or set(signals) != CONSENT_SIGNALS:
                raise AdvisorError("CONSENT_SIGNALS_INCOMPLETE", "All four Consent Mode v2 signals are required.", EXIT_INPUT)
    if pageview_count > 1:
        raise AdvisorError("SPA_PAGEVIEW_CONFLICT", "Only one SPA page-view strategy is allowed.", EXIT_INPUT)
    if consent_kinds and (not measurement.get("consent", {}).get("policyConfirmed") or consent_kinds != {"CONSENT_DEFAULT", "CONSENT_UPDATE"}):
        raise AdvisorError("CONSENT_POLICY_UNRESOLVED", "Consent integration requires confirmed policy plus both default and update mappings.", EXIT_INPUT)
    commands = request.get("verificationCommands", [])
    normalized: list[dict[str, Any]] = []
    if not isinstance(commands, list) or len(commands) > 10:
        raise AdvisorError("INVALID_VERIFICATION_COMMAND", "At most ten exact verification commands are allowed.", EXIT_INPUT)
    root = Path(context["projectRoot"])
    for command in commands:
        if not isinstance(command, dict) or set(command) != {"executable", "arguments", "cwd", "timeoutSeconds", "expectedExitCodes", "networkAllowed"}:
            raise AdvisorError("INVALID_VERIFICATION_COMMAND", "Verification commands require the exact safe command contract.", EXIT_INPUT)
        executable = command["executable"]
        arguments = command["arguments"]
        if not isinstance(executable, str) or not executable or FORBIDDEN_COMMAND.search(executable) or not isinstance(arguments, list) or any(not isinstance(arg, str) or FORBIDDEN_COMMAND.search(arg) for arg in arguments):
            raise AdvisorError("UNSAFE_VERIFICATION_COMMAND", "Install, mutation, deploy, network helper, or non-argument commands are forbidden.", EXIT_INPUT)
        cwd = Path(command["cwd"])
        try:
            cwd.resolve().relative_to(root.resolve())
        except (OSError, ValueError) as exc:
            raise AdvisorError("UNSAFE_VERIFICATION_COMMAND", "Verification command cwd must stay inside the project root.", EXIT_INPUT) from exc
        if command["networkAllowed"] is not False or not isinstance(command["timeoutSeconds"], int) or not 1 <= command["timeoutSeconds"] <= 600:
            raise AdvisorError("UNSAFE_VERIFICATION_COMMAND", "Verification commands are offline and bounded to 1-600 seconds.", EXIT_INPUT)
        exits = command["expectedExitCodes"]
        if not isinstance(exits, list) or not exits or any(not isinstance(code, int) or code < 0 or code > 255 for code in exits):
            raise AdvisorError("INVALID_VERIFICATION_COMMAND", "Expected exit codes are invalid.", EXIT_INPUT)
        normalized.append(command)
    return normalized


def validate_simulated_outputs(route: str, simulations: list[dict[str, Any]], context: dict[str, Any]) -> None:
    texts = []
    for item in simulations:
        try:
            texts.append((item["path"], item["after"].decode("utf-8-sig")))
        except UnicodeDecodeError as exc:
            raise AdvisorError("INVALID_PATCH_TARGET", "Patched website output must remain UTF-8.", EXIT_INPUT) from exc
    joined = "\n".join(text for _, text in texts)
    changed_paths = {path for path, _ in texts}
    direct_count = len(re.findall(r"googletagmanager\.com/gtag/js", joined, re.I))
    gtm_count = len(re.findall(r"googletagmanager\.com/gtm\.js", joined, re.I))
    analytics = context.get("analytics", {})
    direct_count += sum(1 for path in analytics.get("directLoaderPaths", []) if path not in changed_paths)
    gtm_count += sum(1 for path in analytics.get("gtmLoaderPaths", []) if path not in changed_paths)
    if route == "direct" and (direct_count != 1 or gtm_count):
        raise AdvisorError("TAG_LOADER_COUNT_INVALID", "The planned files must contain exactly one direct loader and no GTM loader.", EXIT_INPUT)
    if route == "gtm" and (gtm_count != 1 or direct_count):
        raise AdvisorError("TAG_LOADER_COUNT_INVALID", "The planned files must contain exactly one GTM loader and no direct loader.", EXIT_INPUT)
    for path, text in texts:
        lower = text.lower()
        loader_positions = [pos for marker in ("googletagmanager.com/gtag/js", "googletagmanager.com/gtm.js") if (pos := lower.find(marker)) >= 0]
        if loader_positions:
            default = re.search(r"['\"]consent['\"]\s*,\s*['\"]default['\"]", text, re.I)
            if default is None or default.start() > min(loader_positions):
                raise AdvisorError("CONSENT_ORDER_INVALID", "Consent defaults must appear before the tag/container loader.", EXIT_INPUT, details={"path": path})
            if not CONSENT_SIGNALS.issubset(set(signal for signal in CONSENT_SIGNALS if signal in text)):
                raise AdvisorError("CONSENT_SIGNALS_INCOMPLETE", "All four consent signals must be present before measurement starts.", EXIT_INPUT, details={"path": path})
    if context.get("stack", {}).get("renderingModel") == "spa":
        automatic = bool(re.search(r"send_page_view\s*[:=]\s*(?:true|['\"]true['\"])", joined, re.I) or "automatic-history" in joined)
        manual = bool(re.search(r"page_(?:view|location|referrer)", joined, re.I) or "manual-router" in joined)
        if automatic and manual:
            raise AdvisorError("SPA_PAGEVIEW_CONFLICT", "Automatic and manual SPA page-view strategies cannot coexist.", EXIT_INPUT)
