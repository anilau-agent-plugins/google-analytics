"""Dated GA4 website event naming and catalog rules used by measurement planning."""

from __future__ import annotations

import re


VERIFIED_AT = "2026-08-15"
SOURCE_URLS = (
    "https://developers.google.com/analytics/devguides/collection/ga4/reference/recommended-events",
    "https://support.google.com/analytics/answer/13316687",
    "https://support.google.com/analytics/answer/9267744",
)

AUTOMATIC_EVENTS = {"first_visit", "page_view", "session_start", "user_engagement"}
ENHANCED_EVENTS = {
    "click", "file_download", "form_start", "form_submit", "scroll", "video_complete",
    "video_progress", "video_start", "view_search_results",
}
RECOMMENDED_EVENTS = {
    "add_payment_info", "add_shipping_info", "add_to_cart", "add_to_wishlist", "begin_checkout",
    "close_convert_lead", "close_unconvert_lead", "disqualify_lead", "generate_lead", "join_group",
    "login", "purchase", "qualify_lead", "refund", "remove_from_cart", "search", "select_content",
    "select_item", "select_promotion", "share", "sign_up", "tutorial_begin", "tutorial_complete",
    "view_cart", "view_item", "view_item_list", "view_promotion", "working_lead",
}
RESERVED_CUSTOM_EVENTS = AUTOMATIC_EVENTS | ENHANCED_EVENTS | {
    "app_remove", "app_store_refund", "app_store_subscription_cancel", "app_store_subscription_renew",
    "error", "first_open", "in_app_purchase", "screen_view", "view_complete",
}
RESERVED_PREFIXES = ("_", "firebase_", "ga_", "google_", "gtag.")
RESERVED_PARAMETER_NAMES = {
    "cid", "customer_id", "customerid", "dclid", "gclid", "session_id", "sessionid", "sfmc_id",
    "sid", "srsltid", "uid", "user_id", "userid",
}
EVENT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
PARAMETER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")


def catalog_class(name: str) -> str:
    if name in AUTOMATIC_EVENTS:
        return "automatic"
    if name in ENHANCED_EVENTS:
        return "enhanced"
    if name in RECOMMENDED_EVENTS:
        return "recommended"
    return "custom"


def event_name_issues(name: str, event_class: str) -> list[str]:
    issues: list[str] = []
    if not EVENT_NAME.fullmatch(name):
        issues.append("Event names must start with a letter, use only letters, numbers, and underscores, and be at most 40 characters.")
    if event_class == "custom" and (name in RESERVED_CUSTOM_EVENTS or name.startswith(RESERVED_PREFIXES)):
        issues.append("A custom event cannot use a Google-reserved event name or prefix.")
    return issues


def parameter_name_issues(name: str, registration: str) -> list[str]:
    issues: list[str] = []
    if not PARAMETER_NAME.fullmatch(name):
        issues.append("Parameter names must start with a letter, use only letters, numbers, and underscores, and be at most 40 characters.")
    if name.startswith(RESERVED_PREFIXES):
        issues.append("The parameter uses a Google-reserved prefix.")
    if registration != "none" and name in RESERVED_PARAMETER_NAMES:
        issues.append("The parameter cannot be registered as a custom definition because its name is reserved.")
    return issues
