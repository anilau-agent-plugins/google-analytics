# Changelog

## Unreleased - 0.20.0 development

- Added a GA4-only proactive advisor playbook that routes broad, targeted, and ambiguous requests,
  tracks assessment completeness, degrades safely when evidence is missing, and keeps analysis
  separate from every mutation workflow.
- Added Search Console read-only authorization and exact property discovery with one guarded
  `sites.list` operation, normalized URL-prefix/Domain identities, permission levels, and explicit
  partial/empty-access handling.
- Added capability-aware OAuth status and rollback-safe scope upgrade for existing profiles. Failed,
  declined, partial, or wrong-account upgrades preserve the previous GA4/GTM credential.
- Added conditional Search Console diagnostics and customer-owned Cloud onboarding without requesting
  Search Console write access, Cloud Platform scopes, or treating consent as mutation approval.
- Added immutable bounded Search Console performance reports for overview, queries, pages, devices,
  countries, search appearance, and short hourly diagnostics with Pacific Time periods, explicit
  preliminary/top-row/privacy limitations, and a no-retry 20-request/12,000-row safety budget.
- Added separate Search Console request/plan/report contracts, deterministic Russian/English
  rendering, exact property and credential guards, and fail-closed handling for unsupported GenAI,
  branded-query, and platform-property UI features.
- Fixed live Search Analytics summary rows that legitimately omit `keys`, prevented summary and
  availability probes from being mislabeled as truncated detail, and made the plain-language output
  show readable period comparisons without repeating identical limitations.
- Added bounded Search Console sitemap metadata snapshots and single-use URL Inspection plans for a
  reasoned sample of up to ten exact-property URLs, with zero automatic retries and partial evidence.
- Added Domain/URL-prefix containment checks, privacy-safe provider normalization, deprecated sitemap
  field handling, and explicit indexed-version-only and non-site-wide limitations in Russian and English.
- Fixed live Search Console sitemap counters represented by Google as decimal JSON strings and kept
  their normalized artifact form as bounded non-negative integers.
- Added three exact-web-stream GA4 `google / organic` source presets and Search Console report schema
  v2 page ambiguity metadata that never stores query or fragment values.
- Added immutable local-only GA4/Search Console cross-source request, plan, and report contracts,
  exact-only URL mapping, overview/landing/device analysis, source-quality propagation, and Russian/
  English plain-language rendering without equating clicks and sessions.
- Fixed live cross-source acceptance for long numeric GA4 stream IDs while retaining phone-number
  protection, and prevented duplicate overview/dedicated Search Console page datasets from creating
  false canonical ambiguity.
- Kept public version metadata unchanged while `0.20.0` remains under staged development.

## 0.11.0 - 2026-08-28

- Added public Git-backed marketplace metadata and verified installation, update, rollback, and
  uninstall instructions for Codex and Claude Code.
- Added a bounded handoff to the commercial Google Ads plugin when a concrete next step requires
  advertising-account or campaign work, without recommending it merely because paid traffic or an
  advertising tag is present.
- Added a direct Google Ads product-page link to the README.
- Documented the owner-approved Windows-only release acceptance for 0.11.0 and removed GitHub
  Actions so this release does not consume CI minutes; full cross-platform CI is deferred to 0.20.0.
- Added a default-deny validation runner that blocks all non-loopback production network requests,
  including inherited subprocess checks, while allowing only synthetic fake transports and the local
  one-use OAuth callback.
- Hardened dependency-free HTTP handling with strict credential-free HTTPS URL validation, bounded
  retry counts, JSON content-type checks, and removal of Authorization headers on cross-host
  redirects; OAuth form posts are restricted to Google's exact token and revocation endpoints.
- Made every protected credential backend reject empty and oversized values before storage while
  preserving DPAPI, macOS Keychain, and Linux Secret Service with no plaintext fallback.
- Expanded contract, transport, secret, archive, credential-leak, CI, and cross-process safety tests;
  the Windows acceptance suite uses only synthetic data and performs no Google, website, publish,
  deploy, or production-event mutation.
- Updated CI to run the Windows CPython 3.10-3.13 security suite before release on pushes, pull
  requests, and manual dispatch, and hardened the local limited-memory runner for Windows PowerShell
  5.1 and PowerShell 7.

## 0.10.2 - 2026-08-28

- Fixed approved measurement plans being rejected by GA4, website, Measurement Protocol, and GTM
  workflows because they incorrectly required the confirmed draft hash to equal the approved
  revision's content hash.
- Centralized approved-plan integrity validation while preserving separate SHA-256 roles: exact user
  confirmation evidence for the draft and tamper detection for the final immutable revision.
- Added end-to-end regression coverage using the distinct hashes produced by the real approval flow.

## 0.10.1 - 2026-08-28

- Fixed a false report-planning blocker caused by treating unrelated incompatible fields returned by
  `checkCompatibility` as if they belonged to the requested report.
- Limited compatibility evaluation to the exact requested dimensions and metrics and requested only
  `COMPATIBLE` results from Google, while preserving fail-closed handling for missing or explicitly
  incompatible requested fields.
- Added a regression fixture matching the real Google response shape that previously blocked channel,
  landing-page, and device reports.
- Prevented ISO report dates from being mistaken for phone numbers by the artifact privacy scanner.

## 0.10.0 - 2026-08-19

- Added immutable read-only report requests and plans for bounded overview, acquisition, landing and
  content, device and geography, events, key events, ecommerce, realtime, approved custom-core, and
  separately gated experimental funnel reporting.
- Added property-timezone periods, previous-period and previous-year comparisons, metadata and
  compatibility checks, sequential pagination, quota safety floors, context-drift protection, and
  no-write operation enforcement.
- Added typed normalized evidence, SHA-256-bound report artifacts, privacy redaction, and explicit
  sampling, thresholding, cardinality, restriction, truncation, incomplete-period, small-data, and
  realtime/funnel reliability labels.
- Added Russian and English plain-language rendering plus at most five evidence-backed prioritized
  recommendations that never claim causality or authorize a mutation.
- Preserved the public MIT license, public GitHub repository, customer-owned OAuth/Cloud architecture,
  and Windows live-acceptance policy while retaining cross-platform runtime support.

## 0.9.0 - 2026-08-19

- Added a web-only Google Tag Manager lifecycle with six separately confirmed stages: isolated
  workspace creation, sync, coherent entity bulk update, compiler preview, version creation, and
  publish.
- Added versioned GTM context and change-request contracts plus a closed template registry for data
  layer variables, supported web triggers, Google tags, and GA4 event tags; arbitrary Custom HTML,
  JavaScript, PII, deletes, mobile/AMP/server containers, and automatic conflict resolution remain
  blocked.
- Added immutable 30-minute mutation plans, exact SHA-256 confirmation, fresh workspace/entity/version
  fingerprints, replay protection, one-shot writes, independent readback, and read-only reconciliation.
- Bound version creation to an unchanged compiler-previewed workspace and publish to the exact created
  version, unchanged live predecessor, and explicit runtime preview evidence. GTM is never published
  automatically or by automated tests.
- Preserved the public MIT license, public GitHub repository, community support model, and
  customer-owned OAuth/Cloud architecture introduced in 0.8.0.

## 0.8.0 - 2026-08-16

- Published Google Analytics Advisor as free, open-source software under the MIT License.
- Added plain-language installation and update instructions plus public support, security, and
  contribution guidance.
- Added bounded website context discovery for static HTML, Laravel Blade, React/Vite and Next.js App
  Router without executing project code.
- Added restricted UTF-8 unified-diff parsing, content-addressed patch artifacts, immutable 30-minute
  local mutation plans, exact SHA-256 confirmation, stale-file/replay protection, safe recovery,
  independent hash readback, and no-deploy journals.
- Added direct Google tag and website-side GTM route safeguards, Consent Mode v2 ordering/signals,
  one-owner event policy, one SPA page-view strategy, and ecommerce identity checks.
- Added separate one-shot Measurement Protocol debug and production delivery plans using protected
  credential references; debug uses `ENFORCE_RECOMMENDATIONS`, and uncertain production sends are
  never retried.
- Added synthetic fixtures and Windows acceptance coverage without real websites, GTM mutations,
  deployments, or production events.

## 0.7.0 - 2026-08-16

- Added a closed Analytics Admin API mutation registry for supported property/web-stream fields, key
  events, custom dimensions/metrics, retention, and Measurement Protocol credential metadata.
- Added fresh v2 snapshots, immutable 30-minute mutation plans, exact SHA-256 confirmation,
  precondition refresh, replay protection, one-shot writes, independent readback, and append-only
  journals with explicit applied/partial/ambiguous/failed outcomes.
- Added credential-aware Measurement Protocol handling that moves provider values directly to DPAPI,
  Keychain, or Secret Service and excludes them from output and artifacts.
- Added experimental fail-closed gates for v1alpha enhanced measurement and data redaction.
- Added `ga4 capabilities`, `plan`, `show`, `apply`, and `reconcile` commands, plain-language guidance,
  and synthetic tests that never call live mutation endpoints.

## 0.6.0 - 2026-08-15

- Added evidence-first local measurement contexts and immutable version 2 measurement plans while
  preserving read compatibility for version 1 plans.
- Added outcome/source-of-truth, event/key-event, parameter, custom-definition, ecommerce,
  Measurement Protocol, consent, funnel, privacy, cardinality, and verification policy checks.
- Added local `measurement context`, `draft`, `show`, `approve`, and `migrate` commands with exact
  SHA-256 approval and no GA4, GTM, website, secret, or production-event mutations.
- Added plain-language rendering, protected project artifact indexes, Stage 6 guidance, and Windows
  synthetic workflow tests.

## 0.5.0 - 2026-08-15

- Added an exact read-operation registry, bounded pagination and safe retries for only allowlisted
  Data API read POST requests.
- Added read-only Analytics Admin, Analytics Data and serialized GTM discovery with explicit safety
  limits and stable error classification.
- Added a bounded local site scanner for Google tag, GTM, dataLayer and Consent Mode evidence that
  excludes secret-bearing files, dependencies, generated output and directory links.
- Added immutable project snapshots, an additive `baseline-report` contract, correlation findings
  and the `resources list`, `site inspect`, and `audit baseline` commands.
- Kept Measurement Protocol secret endpoints, remote mutations, production browser checks and full
  analytics reporting outside this release.
- Live Windows smoke hardened large-account discovery, rejected short false-positive measurement
  IDs, classified compiled/minified/tooling evidence as non-runtime, and protected project audit
  artifacts from accidental Git commits.

## 0.4.3 - 2026-08-15

- Accepted Google's canonical `userinfo.email` scope identifier as equivalent to the requested
  `email` alias while continuing to fail closed when any GA4 or GTM scope is missing.
- Added regression coverage for the canonical scope returned by Google's token endpoint.
- Kept all connection checks read-only and all Cloud API enablement and analytics mutations behind
  separate explicit confirmation.

## 0.4.2 - 2026-08-15

- Added a mandatory initial choice between detailed self-service OAuth setup and explicitly
  authorized browser-assisted setup.
- Added project-specific, screen-by-screen Google Cloud instructions with exact field values and API
  links for self-service users.
- Limited browser permission to the current OAuth onboarding session, required an already signed-in
  intended Google account, and prohibited requesting or entering passwords and 2FA/recovery codes.
- Kept browser permission separate from future GA4, GTM, website, publish and deployment approvals.

## 0.4.1 - 2026-08-10

- Changed OAuth onboarding from a manual checklist to an agent-led workflow that reuses existing
  resources, prepares projects/APIs through available `gcloud`, and operates Cloud Console when the
  host provides browser control.
- Limited user handoffs to sign-in/2FA, ambiguous business choices, Google terms, explicit Cloud
  mutation confirmation, OAuth consent, and a download path when it cannot be captured safely.
- Explicitly prohibited substituting IAM/Workforce or IAP OAuth clients for a regular Google Auth
  Platform Desktop client.

## 0.4.0 - 2026-08-10

- Added customer-owned Google Desktop OAuth with PKCE S256, a one-use `127.0.0.1` loopback callback,
  exact state validation and a one-time request for the complete v1 scope set.
- Added safe Desktop client import, multiple authorization profiles, refresh, local forget, Google
  revocation and exact-confirmation safeguards.
- Added current-user DPAPI storage on Windows, macOS Keychain and Linux Secret Service support with no
  plaintext fallback; access tokens remain in memory only.
- Added minimal read-only Google identity, Analytics Admin, Analytics Data and Tag Manager connection
  probes with disabled-API and permission diagnostics.
- Added plain-language customer-owned Cloud setup, privacy, retention and deletion guidance.
- Kept full GA4/GTM/site audits, reports and every mutation outside this stage.

## 0.3.0 - 2026-08-10

- Aligned the repository with the shared Anilau plugin standard.
- Kept product contracts in the published package while moving implementation plans outside it.
- Added repository metadata, English product/privacy/support documentation and release hygiene checks.
- Added an optional trusted, rate-limited, telemetry-free version metadata check.
- Added Windows CI for Python 3.10–3.13. macOS and Linux remain supported implementation targets,
  with compatibility fixes handled from user feedback rather than release-gating live tests.

## 0.2.0 - 2026-08-09

- Added dependency-free Python CLI and Windows/macOS/Linux launchers.
- Added CPython 3.10–3.13 detection, installation guidance, diagnostics and external runtime paths.
- Added project artifact validation, redacted JSON output and bounded HTTPS transport foundation.
- Added Windows tests and Stage 3 acceptance automation; full OS/version matrix remains pending.

## 0.1.0 - 2026-08-09

- Added Codex and Claude Code plugin manifests.
- Added one shared `google-analytics` skill and Codex UI metadata.
- Added local Codex and Claude marketplace packaging.
- Added preview product documentation.
- Deliberately omitted Python runtime, OAuth, Google API access and analytics functionality.
