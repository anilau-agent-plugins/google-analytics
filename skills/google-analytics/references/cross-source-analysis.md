# GA4 and Search Console cross-source analysis

Use this workflow only after the exact GA4 property, web stream, Search Console property, and
current/previous date labels are known. Source reads and local analysis are separate: the analyzer
never obtains a token, calls Google, changes a resource, or silently refreshes stale evidence.

## Prepare the two source reports

Create a GA4 `report-request` with the exact `webStream` and only the needed closed presets:

- `google-organic-overview` for measured sessions and outcomes;
- `google-organic-landing` for privacy-safe landing paths, capped at 500 rows;
- `google-organic-device` for device context.

These presets always use exact `streamId`, `sessionSource=google`, and `sessionMedium=organic`
filters. The planner fingerprints the selected `WEB_DATA_STREAM`, verifies property membership and a
safe `defaultUri`, and runs `checkCompatibility` before report queries. Never replace these presets
with a custom report for cross-source evidence.

Create a finalized Search Console `web` report for the same date labels with `overview`, `pages`, and
`devices` as needed. Search Console report schema v2 records whether a page URL contained a query or
fragment, but never stores those values. A schema v1 report remains displayable on its own but cannot
support page joining.

Both source reads have their own immutable plans and quota disclosures. Do not execute either source
report merely because the user requested a local cross-source plan.

## Prepare and run the local analysis

The agent prepares `cross-source-analysis-request` v1; the user does not write JSON. Each source ref
contains an absolute in-project path, file SHA-256, and internal report SHA-256. Then run:

```text
google-analytics advisor cross-source plan --request <absolute-path> --json
google-analytics advisor cross-source show-plan --plan <absolute-path> --json
google-analytics advisor cross-source run --plan <absolute-path> --json
google-analytics advisor cross-source show --report <absolute-path> --language auto --json
```

All four commands must return `networkUsed=false` and `mutationPerformed=false`. The planner checks
source hashes and identities, exact stream/property/site relationship, finalized complete periods,
required datasets, closed GA4 filters, Search Console schema v2 page metadata, and source-quality
limitations. `run` repeats source integrity checks before producing a report.

## Mapping and interpretation rules

Only exact safe URL matches are joined. Lowercase scheme/host and removal of a default port are safe;
path case, trailing slashes, repeated slashes, percent encoding, redirects, locales, `www`, and
HTTP/HTTPS are not normalized into equivalence. Preserve `exact`, `search_console_only`, `ga4_only`,
`query_ambiguous`, `origin_mismatch`, `path_variant`, `canonical_ambiguous`, and
`invalid_or_redacted`. Never fuzzy-match or turn an absent detail row into zero.

- Search Console `clicks` and GA4 `sessions` are different metrics. Their difference is diagnostic,
  never a count of lost visits.
- GA4 `google / organic` does not prove the Search Console search surface or a particular click.
- Compare change only inside each source. Show two stages side by side for exact page matches.
- When GA4 timezone differs from `America/Los_Angeles`, use `date_labels_only`; do not produce daily
  joined trends or exact reconciliation.
- Preserve top-row, sampling, thresholding, cardinality, restriction, truncation, redaction, empty,
  and small-data limitations.
- Local review floors are 100 impressions, 100 sessions, and 20 authoritative key events. They are
  not Google standards. Conversion advice also requires an approved measurement plan.
- Return at most five recommendations, all with source-qualified evidence.

Present: short answer, reliability, separate source facts, exact page matches, ambiguous/unmapped
evidence, prioritized actions, and one safe next step. Any proposed external change starts its own
plan and confirmation workflow.
