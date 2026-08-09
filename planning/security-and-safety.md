# Security and safety specification

## Authorization model

Use a buyer-owned Google Cloud Desktop OAuth client. Request the complete V1 scope set once so the
user does not repeat consent when moving from reports to GA4 or GTM configuration. Before opening
Google consent, explain in plain language:

- `openid`
- `email`
- `https://www.googleapis.com/auth/analytics.readonly`
- `https://www.googleapis.com/auth/analytics.edit`
- `https://www.googleapis.com/auth/tagmanager.readonly`
- `https://www.googleapis.com/auth/tagmanager.edit.containers`
- `https://www.googleapis.com/auth/tagmanager.edit.containerversions`
- `https://www.googleapis.com/auth/tagmanager.publish`

Explain the scope groups as follows:

- identity scopes identify the selected Google account;
- Analytics read access powers discovery and reports;
- Analytics edit access enables separately confirmed GA4 configuration;
- GTM read/edit/version/publish access enables separately confirmed container workflows;
- no scope permits autonomous changes by policy.

The application must request offline access and use loopback redirect plus PKCE. A Testing consent
configuration is temporary; onboarding must explain how the buyer makes their own app durable for
their permitted users. Never bundle an Anilau OAuth client or route traffic through Anilau.

## Secret boundaries

Secrets include refresh/access tokens, OAuth client secret material, Measurement Protocol API
secrets, passwords, cookies and user-provided identifiers.

- Windows: DPAPI bound to the current user.
- macOS: Keychain.
- Linux: Secret Service/keyring when available.
- Restricted plaintext is a last-resort, explicit-consent fallback with owner-only permissions.
- Project artifacts store opaque credential references only.
- Never echo secrets in command lines, JSON output, logs, plans, reports, journals or responses.
- Tests use syntactically obvious fake values and never production endpoints for event delivery.

## Operation classes

| Class | Examples | Required gate |
| --- | --- | --- |
| `READ_ONLY` | discovery, audits, reports, metadata | Bounded request and explicit live-read authorization where needed. |
| `LOCAL_PLAN` | profile, measurement plan, report | Schema validation and source hashes. |
| `LOCAL_CODE_CHANGE` | insert tag, consent or event code | Exact file preconditions, reviewable diff, confirmation and targeted tests. |
| `REMOTE_CONFIG_CHANGE` | property/stream/key-event/GTM entity update | Immutable plan, current reread, exact SHA-256 confirmation, single apply and readback. |
| `GTM_VERSION_CREATE` | create version from workspace | Clean status/preview plus its own confirmed plan. |
| `GTM_PUBLISH` | publish one exact version | Reinforced confirmation referencing verified version and live predecessor. |
| `PROHIBITED_V1` | delete, user access, automatic deploy | Reject with explanation; do not offer a hidden escape flag. |

## Mutation lifecycle

1. Read the exact affected resources and safety anchors.
2. Normalize and hash current non-secret state.
3. Build an immutable, expiring plan with field-level operations and expected readback.
4. Validate locally and use provider preview/status features where available.
5. Show a plain-language summary plus exact risk and affected resources.
6. Require the exact plan SHA-256; elevated GTM publication requires a second confirmation bound to
   the version and current live version.
7. Reread preconditions immediately before apply.
8. Send a non-idempotent mutation once.
9. Read back independently and journal the actual result.

On timeout or connection loss after send, record `ambiguous`, perform readback and do not retry until
the observed state proves the operation did not apply.

## Reporting integrity

- Record API version/channel, timezone, currency, date boundaries, query, filters and request IDs.
- Preserve sampling, thresholding, cardinality, quota and incomplete-period limitations.
- Separate facts, calculations, interpretations, recommendations and questions structurally.
- Label anomalies as checks, not causes.
- Do not recommend a configuration change when the source data is immature or incompatible.

## Website and consent safety

Inspect the existing implementation before inserting tags. Prevent duplicate Google tags,
containers and events. Set Consent Mode defaults before measurement commands and update consent on
the interaction page. Treat regional/default consent values as user-confirmed policy. Do not deploy
to production without a separate request and project-specific verification.
