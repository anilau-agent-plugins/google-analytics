# Search Console performance reports

Read this reference for organic Google Search performance after an exact Search Console property
has been selected. This workflow is read-only. It never changes Search Console, GA4, GTM, the site,
OAuth, or the user's Cloud project.

## Start safely

1. Run `auth status --profile <profile-id> --json` and require
   `capabilities.searchConsole.status=ready`.
2. Run `search-console sites list --profile <profile-id> --json` and let the user choose the exact
   readable `selectionKey`. Do not merge or rewrite property identities.
3. Run:

```text
google-analytics search-console reports catalog --profile <profile-id> --site <selection-key> --json
```

For a broad question, default to `overview`, finalized 28 complete Pacific days, the immediately
preceding period, and `searchType=web`. Ask a question only when the site, business meaning, or
requested search surface is genuinely ambiguous. Do not make a non-specialist select raw dimensions.

## Immutable request and plan

Prepare a `search-console-report-request` JSON artifact with the exact project root, profile, site,
question, language, period, comparisons, presets, search type, data state, filters and canonical
`contentSha256`. Validate it, then run:

```text
google-analytics search-console reports plan --profile <profile-id> --site <selection-key> --request <absolute-request-path> --json
google-analytics search-console reports show-plan --plan <absolute-plan-path> --json
```

Explain that planning performs only property discovery, writes a local immutable plan, and does not
read performance data. Show the exact Pacific Time periods, `final`/`all`/`hourly_all` state,
presets, search type, planned request/row budget, limitations, expiry, and `planSha256`.

Run performance only when the user has agreed to the exact read-only plan:

```text
google-analytics search-console reports run --plan <absolute-plan-path> --json
google-analytics search-console reports show --report <absolute-report-path> --language <ru|en> --json
```

## Presets

- `overview`: totals, date trend, device, country, top queries/pages and search appearance;
- `queries`: top query rows, unavailable for Discover and Google News;
- `pages`: top canonical-attributed page rows;
- `devices`, `countries`: compact breakdowns;
- `search-appearance`: provider values followed by bounded provider-derived detail requests;
- `recent-hourly`: at most three Pacific days with `hourly_all`; diagnostic only.

Different search types are separate datasets and must never be summed. `final` is the analysis
default. `all` is preliminary when Google supplies `first_incomplete_date`. `hourly_all` is isolated
from finalized comparisons and must preserve `first_incomplete_hour`.

## Reliability rules

- Search Console uses `America/Los_Angeles`, not the GA4 property timezone or the computer timezone.
- Query, page and search-appearance details are top rows, not exhaustive exports.
- Anonymous queries can be included in totals while absent from query rows.
- Detail sums need not equal totals. Do not label the difference as hidden queries.
- Position is an average of the top result and is absent for Discover; absence is not zero.
- URL rows can use Google's canonical URL rather than the URL the visitor saw.
- GenAI, branded-query and platform-property UI reports are not documented by the current query API.
  Do not reproduce them with homemade classification and call the output Google data.
- The product stops at 20 performance requests and 12,000 rows per run, uses at most 1,000 rows per
  page and two pages per dataset, and never retries quota/network failures automatically.

## Explain the result

Use this order: short answer, reliability, confirmed facts, calculations, limitations,
interpretations, recommendations, unresolved questions, and one safe next step. Keep Google's metric
names beside everyday explanations. Separate correlation from causation, identify CTR thresholds as
local review heuristics, and never promise traffic or commercial results.

Search Console reporting is not yet cross-source GA4 analysis. Present the two sources separately;
URL normalization, joined evidence and causal funnel interpretation belong to a later stage.
