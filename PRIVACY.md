# Privacy

## Current version

Google Analytics Advisor 0.11.0 runs locally in the user's environment. It does not collect telemetry
and does not send prompts, credentials, project files or analytics data to Anilau. Google
authorization and connection diagnostics communicate directly from the user's computer to Google;
they do not pass through Anilau infrastructure.

During assisted onboarding, an existing Google Cloud CLI or the user's Cloud Console session can be
used to inspect the selected account/project, create a confirmed customer project, and enable the
four required APIs. These interactions go directly to Google. The advisor must not read Cloud CLI
credential files or print access tokens, and it must obtain confirmation before changing Cloud state.

Browser-assisted setup is optional and begins only after explicit permission for the current OAuth
onboarding session. It uses the user's already signed-in browser session directly with Google. The
advisor must not request, read or enter passwords, recovery codes or 2FA codes. The user can instead
choose detailed self-service instructions or withdraw browser permission at any time.

Runtime diagnostics inspect the local operating system, Python candidates, filesystem writability and
TLS availability. Their JSON output remains local unless the user chooses to share it. A local site
inspection reads bounded supported source files below the explicitly selected project root, excludes
secret-bearing files and directory links, and neither executes code nor uses the network.

The optional version check requests a user-disclosed trusted HTTPS manifest containing public release
metadata. It sends no installation identifier, credentials, analytics data or project data. The local
state stores the last check time and public result for 30 days outside the plugin source. The check is
not configured by default and can be disabled from the CLI.

## Google authorization data

The customer supplies a Desktop OAuth client from the customer's own Google Cloud project. The local
CLI sends the browser authorization request, authorization-code exchange, token refresh, optional
revocation, read-only discovery and bounded diagnostics directly to Google over HTTPS. The requested scopes cover
identity, GA4 read/edit, GTM read/edit/version/publish, and Search Console read-only. Version 0.11.0 can perform allowlisted GA4
Admin configuration and local website source changes after separate immutable expiring plans and
exact SHA-256 confirmations. It can also perform supported GTM web-container operations through
separate workspace, sync, entity, compiler-preview, version, and publish plans. Authorization does
not approve any mutation.

The unreleased 0.20.0 development capability can make bounded Search Console property discovery,
Search Analytics, sitemap metadata, and URL Inspection requests. It stores exact property identity,
selected periods or URLs, reasons for URL selection, normalized provider evidence, request IDs,
completeness markers and recommendations under the selected project's
`.google-analytics-advisor/` directory. Search Analytics uses at most 20 requests and 12,000 rows per
run. URL Inspection uses at most 10 sequential requests per single-use plan; the default sample is
five. Neither workflow automatically retries quota or network failures. Sitemap inventory is capped
at 1,000 metadata entries and does not download or parse sitemap XML. Deprecated indexed sitemap
counts are ignored. Provider URL query values are redacted before storage when necessary. These
operations cannot add, delete, verify, or modify a Search Console property, its users, a sitemap, or
the Google index.

The unreleased cross-source analyzer reads only explicitly referenced immutable GA4 and Search
Console report files inside the selected project. It rechecks file and internal SHA-256 values and
does not receive OAuth credentials or call Google. Search Console page query strings and fragments are
removed before storage; schema v2 retains only boolean/unknown ambiguity metadata. URL joining is
exact-only, capped at 500 mappings, and the resulting credential-free plan/report remains under the
project's `.google-analytics-advisor/` directory.

The unreleased full-picture advisor stores a credential-free request, expiring plan, one immutable
checkpoint after each sequential step, and a final report under the selected project's
`.google-analytics-advisor/` directory. Checkpoints contain exact selected resource identities,
periods, domain and step states, bounded budgets, error classifications, artifact paths and hashes;
they contain no OAuth token or client secret. Resume verifies the complete checkpoint chain and
reuses valid completed local results instead of repeating their Google reads. The workflow does not
send advisor artifacts to Anilau and cannot approve or perform a recommended mutation.

The unreleased Search Console/GA4 linking workflow cannot use a public Google API to create the
link. Its CLI writes only credential-free request, 30-minute plan, and semantic result artifacts
under the selected project's `.google-analytics-advisor/` directory. Optional browser assistance
uses the user's existing Google session only after fresh permission. Account email is compared only
transiently and is not stored in project artifacts. Cookies, passwords, MFA/passkeys, screenshots,
raw HTML, and browser storage are prohibited. The user confirms the full plan SHA-256 before one
exact Google UI Submit. Link readback is recorded separately from delayed report-data availability;
no delete/recreate, ownership verification, user management, or report-collection publication is
included.

Read-only baseline audits can request selected GA4 configuration, a bounded event-name/count report,
and selected GTM configuration directly from Google. The CLI does not request Measurement Protocol
secret resources. Normalized snapshots and a baseline report are stored inside the selected project
under `.google-analytics-advisor/`; credentials remain in OS-protected storage and are never written
to those artifacts. The user controls retention by retaining or deleting that project directory.

Read-only performance reporting can request bounded aggregate GA4 Data API results for explicitly
selected properties, dates and closed report presets. The CLI stores only normalized typed rows,
concise request/evidence metadata, Data API request IDs, quality indicators and derived advice in
versioned local report artifacts. It does not store access tokens or unbounded raw provider responses.
Potential email addresses, phone-like values, credential-like strings and URL query/fragment values
are redacted before storage and display. Restricted metrics remain marked unavailable rather than
being interpreted as zero. Reports and recommendations are not sent to Anilau.

Measurement contexts and plans are also stored under `.google-analytics-advisor/`. They contain
structural business outcomes, evidence references, hashes, event definitions, consent decisions, and
verification rules. The planner rejects credential-shaped and PII-shaped input and must use synthetic
examples rather than customer records. Measurement design is local-only and sends no production
event or measurement-plan data to Google or Anilau.

GA4 mutation planning reads the selected current configuration directly from Google and stores
credential-free snapshots, plans, and journals under `.google-analytics-advisor/`. Apply sends only
the fields shown in the confirmed plan, makes one write attempt, and performs a separate readback.
These artifacts can contain resource names and configuration values but never OAuth tokens or
Measurement Protocol credential values.

GTM planning reads the exact selected container, workspace entities, fingerprints, compiler-preview
result, versions, and live predecessor directly from Google. Credential-free GTM contexts, snapshots,
plans, runtime-preview descriptions, and journals are stored under `.google-analytics-advisor/`.
Each remote operation is sent once after exact SHA confirmation and followed by independent readback;
publish is never automatic. These artifacts can contain public container IDs, resource paths, entity
names, non-PII tag parameters, fingerprints, and a concise description of synthetic runtime preview
checks. They must not contain production customer records or secrets.

When a Measurement Protocol credential is created, its provider value is handled in process memory
and immediately placed in DPAPI, Keychain, or Secret Service. Only an opaque credential reference is
written to output and journals. If protected storage cannot be confirmed, the operation is reported
as ambiguous and is not retried automatically. Version 0.11.0 can send only an event bound to a
separate immutable one-shot delivery plan and new exact confirmation. Debug and production endpoints
are never mixed; an uncertain production response is not retried.

Website contexts, content-addressed patches, mutation plans and journals are stored under the selected
project's `.google-analytics-advisor/` directory. They contain paths, hashes, public tag/container IDs,
typed intentions and credential-free validation evidence, but no file bodies in JSON journals and no
secret values. Local apply does not execute project verification commands or perform a deployment.

The imported OAuth client and durable refresh token are retained until the user removes them. Windows
protects them for the current user with DPAPI; macOS uses Keychain; Linux uses Secret Service through
`secret-tool`. There is no plaintext fallback. Access tokens are held only in process memory. A
non-secret local index stores opaque profile/client references, masked client identifiers, project ID,
timestamps, state and confirmation hashes outside plugin source and caches. It does not store email
addresses or tokens.

When an existing profile adds Search Console read-only access, the CLI writes the candidate refresh
token to a protected staging slot, verifies protected readback, and only then switches the profile.
A failed or declined upgrade leaves the previous GA4/GTM credential active. Protected staging slots
are never written to project artifacts or output and are cleaned after canonical rotation.

`auth forget-local` deletes the selected local refresh token without revoking Google's grant.
`auth revoke` asks Google to revoke the grant and deletes the local token only after a definite
successful response. `auth client remove` deletes an unused imported client. Each destructive command
requires the confirmation displayed by a preceding status/list command. The downloaded source JSON is
not deleted automatically.

Google is the external recipient for authorization and diagnostic requests and applies its own terms
and privacy policy. Customer OAuth applications, Cloud projects, quotas and credentials belong to the
customer and are not routed through Anilau infrastructure.

Do not include secrets or customer analytics exports in support messages. If a future support case
requires diagnostic data, the customer must review and explicitly choose what to share.

## Distribution and support websites

Installing from GitHub or opening anilau.com uses those services as ordinary software-distribution
or documentation websites. The plugin does not send Analytics data, project files, credentials, or
telemetry to either service. GitHub and Anilau may process ordinary web-request metadata under their
own policies when the user deliberately visits a page, downloads a release, updates a marketplace,
or submits a support request.
