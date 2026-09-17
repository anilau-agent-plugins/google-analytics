# Search Console and GA4 linking

Use this reference only for helping a user connect one exact Search Console property to one exact
GA4 web data stream. This is a Google UI configuration operation, not a Search Console API or
Analytics Admin API operation.

## What the link is for

Explain in plain language before opening Google:

- the link adds Google's `Google Organic Search Queries` and `Google Organic Search Traffic`
  reports to the selected GA4 property;
- the Advisor's direct Search Console reports and local cross-source analysis already work without
  the link;
- Search Console data can become visible to people who have access to the linked GA4 property;
- Search Console keeps at most 16 months of data for these reports;
- data are normally available about 48 hours after Search Console collects them;
- the Search Console report collection is unpublished in GA4 navigation by default;
- publishing that collection is a different UI change and is not included in link confirmation;
- Google currently does not provide a public API for managing this link.

Official references:

- [Connect Search Console to Google Analytics](https://support.google.com/analytics/answer/10737381)
- [Search Console associations](https://support.google.com/webmasters/answer/9419894)
- [Analytics Admin API resources](https://developers.google.com/analytics/devguides/config/admin/v1/rest)
- [Search Console API resources](https://developers.google.com/webmaster-tools/v1/api_reference_index)

## Required roles and limits

Creating the link requires both:

- GA4 `Editor` on the exact property;
- verified owner of the exact Search Console property.

Keep Google's one-to-one constraints visible:

- one web stream can link to only one Search Console property;
- one Search Console property can link to only one web stream;
- one GA4 property can have only one web stream linked to Search Console.

Google does not let the user edit a Search Console link. A different pair requires deletion and a
new link. Never delete, replace, or recreate an existing link in this workflow.

## Start with exact read-only identities

Use existing read-only discovery. Do not select by display name alone. Show:

- GA4 `properties/<id>` and display name;
- exact `properties/<id>/dataStreams/<id>`, display name, `measurementId`, and safe `defaultUri`;
- exact Search Console `selectionKey`, Domain or URL-prefix type, and normalized/raw permission;
- whether the stream URI is conservatively compatible with the Search Console scope.

The API permission must be `owner` for creation. A full or restricted user can read data but cannot
be treated as a verified owner. Similar Domain and URL-prefix resources are not interchangeable.
Ask the user to confirm that both resources collect the same set of pages when that cannot be proven
from exact identities.

## Offer exactly two modes

Before opening Google UI, offer:

1. **Detailed self-service.** Give the exact resource checklist and the steps below. The user performs
   every click.
2. **Browser-assisted.** Ask for fresh explicit permission to control a browser already signed in to
   the intended Google account. Earlier OAuth/browser permission does not carry over.

The user can switch modes, but create a fresh request and plan after the switch. Never ask for or
enter a password, recovery code, passkey, CAPTCHA answer, or MFA code. Hand control to the user for
those steps.

## Self-service steps

Give these steps with the exact selected names beside them:

1. Open [Google Analytics](https://analytics.google.com/) and select the exact GA4 property.
2. Open `Admin`.
3. Under `Product links`, open `Search Console Links`.
4. Inspect the existing link table first.
5. If the exact pair already exists, stop and record `already_linked_exact`.
6. If another pair occupies either side, stop. Do not remove it.
7. Click `Link` only when the target pair is free.
8. In `Link to Search Console properties I manage`, choose the exact Search Console property and
   click `Confirm`.
9. Click `Next`, choose the exact web data stream, then click `Next`.
10. Stop on the review screen before `Submit` and compare every displayed resource with the exact
    preview.

The user must not click `Submit` until the agent has generated the immutable plan, shown the full
`planSha256`, and the user has replied with that exact full SHA-256. Generic approval is not enough.

## Browser-assisted steps

After explicit permission:

1. Verify the visible Google account matches the selected authorization profile without persisting
   the email address in project artifacts.
2. Navigate to the exact GA4 property and `Admin > Product links > Search Console Links`.
3. Read the existing-link table before selecting anything.
4. Stop on account mismatch, reauthentication, missing Editor/owner access, any existing conflict,
   changed UI, or ambiguous resource identity.
5. Select only the exact Search Console property and web stream.
6. Stop at Google's final review page before `Submit`.
7. Prepare the request and run:

```text
google-analytics search-console link plan --request <absolute-path> --json
google-analytics search-console link show-plan --plan <absolute-path> --language <ru|en> --json
```

8. Show the exact pair, visibility consequence, exclusions, expiry, and full `planSha256`.
9. Wait for the user to reply with the exact full SHA-256.
10. Recheck the active account and review page. If either changed or the plan expired, do not submit;
    make a new plan.
11. Click `Submit` once.

Do not retry a click after a timeout, navigation error, or ambiguous response.

## Request preparation

The agent prepares `search-console-link-request`; the user never writes JSON. It must stay inside the
selected project and contain no email, cookie, screenshot, raw HTML, browser storage, password,
passkey, MFA, token, or secret.

The request records:

- exact resource identities and SHA-256 fingerprints;
- selected mode;
- conservative scope compatibility;
- UI preflight state and time;
- account-match state without the account address;
- Editor and verified-owner evidence;
- acknowledgements for visibility, limits, delay/retention, independent API analysis, and separate
  collection publication.

Only `ready_for_review` can create a ready plan. `already_linked_exact` creates a no-op plan. Every
conflict, missing permission, account mismatch, reauthentication requirement, or UI ambiguity creates
a blocked plan with no operation.

## Confirmation boundary

A ready plan:

- expires after 30 minutes;
- is single-use;
- contains exactly one `UI_SUBMIT_CREATE_LINK` operation;
- authorizes only one final `Submit` for the exact pair;
- excludes deletion, recreation, ownership verification, user management, and Library publication;
- does not authorize any GA4, GTM, website, sitemap, or indexing change.

The CLI does not open or control a browser and cannot create the provider link. It returns
`networkUsed=false` and `mutationPerformed=false` during planning. Browser control is an agent
interaction protected by the separate permission and confirmation boundaries above.

## Readback and recording

After the single submit, verify at minimum:

- an exact matching row in `GA4 Admin > Product links > Search Console Links`;
- the exact GA4 property, web stream, and Search Console property in that row.

When accessible, independently inspect `Search Console > Settings > Associations`. Do not call the
operation successful only because a button disappeared, the URL changed, or no error was visible.

Prepare a small privacy-safe readback file with semantic states only. Do not save screenshots, raw
HTML, account email, or authentication data. Record it with:

```text
google-analytics search-console link record --plan <absolute-path> --confirm-sha256 <full-sha256> --outcome <created|already_linked_exact|cancelled|blocked|ambiguous|failed> --readback <absolute-path> --json
google-analytics search-console link show --result <absolute-path> --language <ru|en> --json
```

`created` requires exact-pair GA4 table readback. `already_linked_exact` is a no-op. `ambiguous`
consumes the plan and forbids automatic retry; inspect the UI read-only before considering a new
plan.

## Data availability after linking

Link readback and data availability are separate:

- `pending_expected` is normal immediately after creation;
- do not use missing immediate report data as proof of failure;
- offer a later read-only check after Google's expected delay, but do not schedule one unless the
  user asks;
- `unavailable_after_delay` needs a new read-only diagnosis, not another Submit;
- publishing the Search Console collection remains a separately planned UI change.

End with one safe next step. Never promise more traffic, rankings, conversions, or revenue from
creating the link.
