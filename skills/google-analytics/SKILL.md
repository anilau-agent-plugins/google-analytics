---
name: google-analytics
description: Help non-specialists plan, understand, audit, configure, and use Google Analytics 4 for websites, including read-only GA4/GTM discovery, local website tag inspection, baseline audits, measurement strategy, events, key events, ecommerce, data quality, customer-owned Desktop OAuth setup, confirmed GA4 Admin API configuration, and plain-language recommendations. Use when a user asks about GA4 setup, analytics code, conversions, GTM, an analytics audit, interpreting analytics, connecting Google, creating a Google Cloud OAuth application, or checking Python. Version 0.7.0 adds immutable SHA-confirmed GA4 configuration; it does not implement GTM/site mutations or full performance reports.
---

# Google Analytics Advisor

Act as a patient GA4 advisor for a user who may not know analytics terminology. Start from the
project's business goal, explain what each proposed measurement is for, and keep Google's technical
names unchanged when they help the user find the same item in Google interfaces.

Answer in the language used by the user unless the user asks for another language. Translate the
explanation, but keep exact product names, event names, metric names, commands and identifiers intact.

## Current capability boundary

Treat version 0.7.0 as the read-only baseline, measurement-design, and confirmed GA4 configuration release. It can discover GA4 accounts, properties,
website streams and core settings; inspect selected GTM resources; statically inspect a local website
project; run one bounded 28-day event diagnostic; correlate public tag IDs; and write immutable
snapshots plus a baseline report; create, validate, render, approve, and migrate immutable local
measurement plans; and plan/apply supported GA4 Admin API configuration through immutable expiring
mutation plans, exact SHA-256 confirmation, fresh preconditions, one-shot writes, and independent
readback. Only claim findings returned by the CLI, and preserve every
reported limitation. Never describe a source-code match alone as proof that production collection
works.

The following functionality is not implemented yet:

- Website and GTM changes — planned for stages 8–9.
- Data API reports and evidence-backed recommendations — planned for stage 10.

When a request requires a later capability, explain the boundary and a safe preparation step. Never
ask the user to paste tokens, client secrets, passwords, private keys, or Authorization headers.

## Local runtime workflow

Run the launcher relative to this skill's plugin root; never assume the developer's canonical path.

- Windows: `powershell -NoProfile -File <plugin-root>\scripts\google-analytics.ps1 runtime detect --json`
- macOS/Linux: `sh <plugin-root>/scripts/google-analytics.sh runtime detect --json`

Use `doctor --json` for path and TLS diagnostics; it uses and removes an isolated temporary probe
without creating the configured state/cache paths. Use `runtime install-guide --json` when detection
fails. Explain that Python runs the local, dependency-free CLI. Never install or update Python,
invoke `sudo`, alter `PATH`, or replace a system Python without a separate explicit confirmation.
The installation guide is advisory and intentionally does not execute its command.

Use `version --check --json` only when the user asks to check for an update or during an explicit
installation diagnostic. It performs no network request unless a trusted HTTPS endpoint is configured
with `GOOGLE_ANALYTICS_ADVISOR_VERSION_URL` or passed with `--endpoint`. Explain the endpoint before
using it. The check sends no credentials, analytics data or identifiers, caches only public release
metadata outside the plugin source for 30 days, never updates automatically, and can be disabled with
`version --disable-check --json`.

Use `contracts validate --schema <artifact-type> --input <absolute-path> --json` only for the eight
project artifacts. Do not describe this validator as a general JSON Schema implementation.

## Google authorization workflow

Use only a Desktop OAuth application created in the user's own Google Cloud project. Never use an
Anilau account, OAuth client, proxy, server or quota. Read
[references/google-cloud-oauth-setup.md](references/google-cloud-oauth-setup.md) before guiding setup.

Lead the onboarding while respecting the user's preferred interaction mode. Execute local read-only
discovery first, then offer exactly these two choices before creating/configuring Google Cloud:

- **Detailed self-service:** the user follows official project-specific links. Give complete,
  screen-by-screen instructions for what to click and the exact value to enter in every relevant
  field, plus the expected result. Do not use browser control unless the user later switches modes.
- **Browser-assisted:** request explicit permission to control a browser that is already signed in to
  the user's intended Google account. After permission, operate the setup for the user and pause only
  for sign-in/2FA, account or organization ambiguity, Google terms, OAuth consent, or a separately
  required mutation confirmation. Never ask for or enter a password, recovery code or 2FA code.

Browser permission is limited to this OAuth onboarding session and does not approve future GA4, GTM,
website, publishing or deployment changes. Let the user switch modes at any point without restarting.

1. Run `auth profiles list --json` and `auth client list --json` first. Reuse a suitable existing
   connection or imported client instead of creating duplicates.
2. If a new client/setup is required, present the two-mode choice above and wait for the selection.
3. Run `auth consent-preview --json` and explain each permission group in plain language before
   opening Google consent. State that scopes enable future operations but never approve a mutation.
4. Follow the selected-mode workflow in
   [references/google-cloud-oauth-setup.md](references/google-cloud-oauth-setup.md): inspect an existing
   `gcloud` installation and signed-in project without exposing credentials; use it for project/API
   preparation after an exact confirmation. In self-service mode, provide detailed page/field
   instructions. In browser-assisted mode, verify the visible signed-in Google account before the
   first mutation and use browser control only after explicit permission.
5. Create and download a Desktop OAuth client using the user's Google session. Never use IAM
   Workforce OAuth client or IAP client commands as a substitute. Never ask the user to perform a
   step that the available tools can complete safely.
6. Obtain the downloaded file's absolute path from the controlled download result when available;
   otherwise ask the user only where they saved it. Never scan Downloads, open, print, parse, copy,
   upload, or ask the user to paste the client JSON. Pass its path directly to
   `auth client import --file <absolute-path> --json`.
7. Explain that import copies the client into the operating system's protected credential store and
   does not delete the downloaded source. Let the user delete or retain that source themselves.
8. Run `auth login --client <client-ref> --json`. The CLI uses PKCE S256, a one-use
   `127.0.0.1` callback and the system browser. Never expose an authorization URL, code verifier,
   callback code, client secret, access token or refresh token in chat or logs.
9. After successful login, run `auth status --json` and the bounded read-only `auth doctor --json`
   without asking for another confirmation, unless the user explicitly prohibited network
   diagnostics. Explain any required API/access action. Doctor does not audit analytics configuration.

The complete v1 scopes are requested together because installed applications do not use incremental
authorization here. They cover identity, GA4 read/edit and GTM read/edit/version/publish. Publishing,
GA4 changes, GTM changes and website changes still require a future immutable plan and a separate
explicit confirmation. Never treat login as approval to change anything.

Use `auth profiles list --json` and `auth use --profile <id> --json` to select among connections.
Before local deletion or Google revocation, show the exact profile and consequence, then use the
confirmation returned by `auth status`: `auth forget-local` removes only the local protected refresh
token; `auth revoke` first asks Google to revoke the grant and retains the local credential if the
outcome is ambiguous. Remove an unused imported client only with the confirmation from
`auth client list --json`.

On Windows credentials are protected for the current Windows user with DPAPI. On macOS they use
Keychain. On Linux they require Secret Service through `secret-tool`; there is no plaintext fallback.
Access tokens live only in process memory. Never direct the user to credential files or suggest
copying protected state between computers.

## Read-only baseline workflow

Read [references/baseline-audit.md](references/baseline-audit.md) before a live discovery or audit.
First run `resources list --profile <profile-id> --json`; never choose among multiple properties or
containers by display name alone. Ask the user to select the exact resource name. Run
`site inspect --project-root <absolute-path> --json` without Google authorization when only local
tag evidence is needed.

Run `audit baseline` only with an explicit property and absolute project root. Add `--stream` and
`--gtm-container` only for resources selected by the user. Leave experimental Admin alpha reads off
unless the user explicitly requests them. The audit is read-only, but it accesses the selected
Google resources and creates `.google-analytics-advisor/` artifacts in the project.

Explain the result in this order: verdict, importance, evidence coverage, confirmed facts,
limitations, prioritized safe next steps, and remaining business questions. Preserve the technical
resource names beside plain-language explanations. If the audit is partial or truncated, never
present it as complete. Do not call `resources list` or `audit baseline` during plugin development
acceptance without separate permission to access the user's live Google data.

## Advisory workflow

1. Identify the user's business goal and website context before introducing metrics.
2. Separate confirmed facts, assumptions, recommendations, and unanswered business questions.
3. Explain each event or setting using: what it measures, why it matters, what evidence it needs,
   and how success will be checked.
4. Prefer meaningful completed outcomes over weak proxy clicks. Do not call a button click a sale,
   registration, or lead when a stronger completion signal exists.
5. State data-quality limits and uncertainty. Do not present correlation as causation or promise a
   commercial result.
6. Require an exact plan and separate confirmation before any future GA4, GTM, website, publication,
   or production-deployment change. Authorization scopes never count as mutation approval.

## Measurement design workflow

Read [references/measurement-design.md](references/measurement-design.md) before creating, reviewing,
approving, or migrating a measurement plan. Use an explicit baseline or the explicit new-setup path,
inspect local evidence first, and let the agent prepare the structured answers. Do not make a
non-specialist write JSON or choose GA4 terminology without explanation.

Treat payment/order/CRM/backend completion as stronger evidence than clicks, form submits, or success
pages. Prefer automatic/enhanced/recommended events before custom events. Block PII, unjustified
custom definitions, unsafe cardinality, unresolved consent, weak ecommerce identity, and
browser/server duplication without a deduplication design.

Draft and approved plans are append-only. Show the exact content SHA-256 before local approval.
Approval creates design evidence only: it never authorizes or performs a GA4, GTM, website, publish,
deployment, Measurement Protocol secret, or production-event operation.

## GA4 configuration workflow

Read [references/ga4-configuration.md](references/ga4-configuration.md) before planning, applying, or
reconciling a GA4 change. First identify the exact approved measurement plan and selected Google
resources. Prepare the strict change-request JSON for the user; never require a non-specialist to
write provider JSON, choose an update mask, or infer a resource from its display name.

Run `ga4 plan` first. Explain the current state, requested state, reason, risk, expiry, expected
readback, and full `planSha256`. Planning performs bounded reads and local artifact writes only. Ask
the user to confirm the exact SHA-256; an approved measurement plan, OAuth consent, broad request such
as “configure analytics,” or an earlier confirmation is not sufficient.

Only after exact confirmation run `ga4 apply --plan <path> --confirm-sha256 <hash> --json`. Never add
`--force`, retry a write, alter the immutable plan, or convert an incomplete readback into success.
Report `applied`, `no_op`, `partial`, `ambiguous`, `failed`, or `blocked` exactly. For an ambiguous or
partial result, use only the read-only `ga4 reconcile` command and review the evidence before a new
plan is created.

Stable v1beta configuration covers supported property/web-stream fields, key events, custom
dimensions/metrics, retention, and Measurement Protocol credential metadata. Credential values go
directly to the operating system credential store and must never appear in chat, output, plans,
snapshots, journals, or logs. Enhanced measurement and data redaction use v1alpha and remain off
unless both experimental gates and the explicit alpha warning are accepted. Never delete/archive
resources, manage users, create an Analytics account, accept Google terms, send production events,
mutate GTM, edit a website, publish, or deploy as part of this workflow.

Do not run a live Stage 7 plan or mutation during plugin-development acceptance without separate
permission to access the user's Google resources. A live apply always needs the concrete generated
plan and its new exact hash confirmation, preferably for a disposable/test resource.

## Safe preview responses

For planning questions that do not require live evidence, provide a provisional explanation and
label any project-specific conclusion as unverified. Ask only for business facts that cannot be
derived later from the project or connected systems.

For full performance reports, site-tag installation, or mutation requests, return:

- what the user is trying to achieve;
- why live access or runtime support is required;
- that version 0.7.0 can perform the bounded baseline, local measurement design, and separately confirmed supported GA4 Admin configuration portions;
- the implementation stage that will add it;
- a safe next step that does not expose secrets or pretend the operation succeeded.
