# Read-only baseline audit

Use this workflow to inspect current evidence without changing GA4, GTM, the site, or production.

## 1. Discover and select

Run the launcher relative to the plugin root:

```text
resources list --profile <profile-id> --json
```

Explain each returned account/property/container in plain language. If there is more than one
candidate, ask the user to choose the exact `properties/N`, `properties/N/dataStreams/N`, or
`accounts/N/containers/N` name. Do not infer the production resource from its display name.

## 2. Inspect local source

Run:

```text
site inspect --project-root <absolute-path> --json
```

This does not use Google or execute project code. It excludes dependencies, generated output,
secret-bearing files, binary files, oversized files and directory links. Evidence in docs/tests is
reported separately and does not prove runtime installation. Dynamic IDs require manual review.

## 3. Create the baseline

After resource selection and permission to perform live read-only access, run:

```text
audit baseline --profile <profile-id> --project-root <absolute-path> --property properties/N --stream properties/N/dataStreams/N --gtm-container accounts/N/containers/N --json
```

Omit `--stream` or `--gtm-container` when unavailable. Do not add
`--experimental-admin-alpha` unless the user explicitly requests experimental reads. The command
stores immutable snapshots and the report below the selected project's
`.google-analytics-advisor/` directory. It never applies a fix or publishes a GTM version.

## 4. Explain the evidence

Translate the JSON into:

1. a short verdict;
2. confirmed facts with evidence sources;
3. limitations such as missing access, empty data, thresholding or truncation;
4. prioritized recommendations that do not imply approval to mutate;
5. business questions that APIs and source code cannot answer.

The Data API diagnostic covers `28daysAgo` through `yesterday`, is limited to 1,000 event rows, and
is not a full analytics performance report. A static code match does not prove a production request
was sent. A probable duplicate remains probable until runtime evidence confirms it.
