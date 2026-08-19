# Reporting advisor

Use this workflow for a selected GA4 website property. It is read-only with respect to Google, but it
uses the customer's Data API quota and writes credential-free artifacts below the selected project's
`.google-analytics-advisor/` directory.

## Start from the decision

Ask what the user wants to understand or decide. Resolve the exact `properties/<id>` resource from
previous discovery; never choose among multiple properties by display name. Prefer an approved
measurement plan when recommendations depend on which events represent real business outcomes.

The closed presets are overview, acquisition, landing/content, device/geo, events, key-events,
ecommerce, realtime, custom-core, and experimental funnel. Realtime describes only the last 30 or 60
minutes. Funnel reporting uses Data API v1alpha and remains disabled unless the environment flag,
request opt-in, disclosure acceptance, and approved Stage-10-ready funnel design all agree.

## Prepare, inspect, then run

1. Run `reports catalog --profile <id> --property properties/<id> --json`.
2. Prepare a `report-request` artifact under the chosen project. Default to the last 28 complete days
   in the property's timezone and compare with the immediately preceding period. Include today only
   when the user needs a diagnostic and label it incomplete.
3. Run `reports plan --profile <id> --property properties/<id> --request <path> --json`.
4. Show the exact periods, presets, blockers, restrictions and `planSha256`. Resolve blockers rather
   than editing the immutable plan. A plan expires after 30 minutes.
5. Run `reports run --plan <path> --json`. Do not retry or expand a query after a quota safety stop.
6. Use `reports show --report <path> --language auto --json` for the plain-language rendering.

The planner checks current property timezone/currency, Data API metadata and field compatibility. The
runner rechecks that context before execution. It uses bounded sequential requests, keeps only
normalized typed rows and concise request/evidence metadata, and never stores OAuth tokens or raw
provider payloads.

## Interpretation rules

- Put the answer first, then reliability, evidence, limitations, recommendations and questions.
- Keep technical names such as `sessions`, `keyEvents`, and `totalRevenue` visible beside explanations.
- Preserve sampling ratios, thresholding metadata, `(other)` row loss, schema restrictions,
  truncation, incomplete periods, redactions and quota stops.
- A restricted metric is unavailable, not zero. Empty data is not proof that no business activity
  occurred. Realtime and experimental funnels are not durable completeness evidence.
- Fewer than 100 sessions/users or fewer than 20 key events in a compared period makes rate/trend
  advice directional. A previous value of zero has an absolute change but no percentage change.
- Recommend at most five actions. Each must name the problem, evidence, expected benefit, effort,
  risk and verification method. A recommendation may start a separate mutation workflow, but the
  report itself never authorizes or performs a change.
- Do not claim causality, statistical significance, legal compliance, or guaranteed business results.

Do not access the user's live property during plugin-development acceptance without separate explicit
permission. Synthetic fixtures are the default release evidence.
