# Changelog

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
