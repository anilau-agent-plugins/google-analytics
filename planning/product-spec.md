# Product specification

Verified: 2026-08-09

## Purpose and audience

Google Analytics Advisor helps a non-specialist understand, configure and use GA4 for a website.
It should inspect available evidence first, explain why each choice matters, perform only approved
changes, verify the result and translate reporting data into practical business language.

The Skill answers in the user's language. Google event names, parameter names, API fields and
resource names remain unchanged so that users can find them in Google interfaces and documentation.

## Core user journeys

### Set up a new website

1. Inspect the website repository and ask only for business facts that cannot be derived.
2. Discover accessible Analytics and GTM resources.
3. Recommend Google tag or GTM based on the actual project.
4. Produce a measurement plan covering goals, events, consent, ecommerce and verification.
5. Create separate approved plans for GA4, GTM and website changes.
6. Apply, test and read back only the approved work. Production deployment remains separate.

### Audit an existing implementation

1. Read GA4/GTM state and inspect site code before asking setup questions.
2. Detect missing, duplicate or contradictory tags and events.
3. Separate verified facts, data-quality limitations and business unknowns.
4. Return a baseline verdict and a small prioritized set of next actions.

### Design events and key events

1. Map business outcomes to authoritative application states.
2. Prefer Google's recommended event names and parameters where applicable.
3. Mark a key event only when it represents a meaningful result.
4. Never substitute a button click for a stronger backend or completed-flow signal.
5. Define evidence that will prove each event works.

### Measure ecommerce

Define the funnel and recommended ecommerce events. Require a stable `transaction_id`, ISO 4217
currency when monetary value is supplied, numeric value semantics, item identity and duplicate-event
protection. A payment-success state is authoritative for `purchase`; opening checkout is not.

### Configure consent

Integrate with the project's existing consent solution. Cover Consent Mode v2 values
`analytics_storage`, `ad_storage`, `ad_user_data` and `ad_personalization`. Set defaults before any
measurement command and update on the page where the user changes consent. Treat regional defaults
as a business/legal decision. Never state that a technical configuration guarantees compliance.

### Use Measurement Protocol

Use Measurement Protocol only to supplement Google tag or GTM collection. Validate payloads at the
debug endpoint with `ENFORCE_RECOMMENDATIONS`; validation must not create report data. Production
delivery is a separate, explicit action and an HTTP success alone does not prove ingestion.

### Explain performance

Offer overview, acquisition, landing/content, device/geo, event, key-event, ecommerce, realtime and
funnel views. Compare complete periods where possible. Always disclose sampling, thresholding,
cardinality, quota, incomplete periods, late data and incompatible dimensions or metrics.

## V1 capabilities

- List and inspect accessible GA4 accounts, properties and web streams.
- Read and plan supported property, web-stream, retention, enhanced-measurement and redaction settings.
- Read and manage key events, custom dimensions, custom metrics and Measurement Protocol secrets.
- Run Data API core, realtime, metadata and compatibility requests.
- Offer funnel reports as an experimental read-only feature with an explicit alpha warning.
- Inspect and manage GTM containers, workspaces, tags, triggers, variables, preview, versions and publish.
- Inspect and modify website code after an approved file plan.
- Produce evidence-backed, plain-language reports and recommendations.

## Explicit exclusions

- Universal Analytics, Firebase, app streams and mobile SDKs.
- Analytics account provisioning and acceptance of Google terms on the user's behalf.
- User and permission management in Analytics or GTM.
- Delete, trash, archive or undelete operations.
- Google Ads, Data Manager, BigQuery, Search Console and other product links.
- Automatic production deployment, scheduled autonomous changes or automatic GTM publish.
- Upload of real customer identifiers from fixtures or tests.
- Legal advice, causal claims and guaranteed commercial outcomes.

## Evidence and terminology

- `fact`: directly observed in source code or returned by an API.
- `calculation`: deterministic result with visible inputs and formula.
- `interpretation`: bounded explanation consistent with evidence.
- `recommendation`: proposed next action with benefit, effort, risk and verification.
- `question`: a business fact that cannot be derived safely.
- `unknown`: required information that is unavailable; never coerce it into a familiar value.

## Plain-language response contract

Every audit or report starts with:

1. What is happening.
2. Why it matters to the business.
3. How reliable the evidence is.
4. What to do first.
5. How to verify improvement.

Technical details follow only when they help the user act or verify.

