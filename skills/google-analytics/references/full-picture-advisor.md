# Full-picture advisor orchestration

Use this workflow after the proactive advisor playbook classifies a request as broad. It turns the
broad assessment into one immutable, resumable, read-only operation over the existing baseline, GA4,
Search Console, and exact-only cross-source services. It does not add a Google API operation and does
not authorize a GA4, GTM, Search Console, website, publish, or deployment change.

## Prepare the request

Resolve the exact local project, authorization profile, `properties/<id>` property,
`properties/<id>/dataStreams/<id>` web stream, verified origin, and—when already available—the exact
Search Console `selectionKey`. Ask one context question only when an exact identity or intended
business outcome cannot otherwise be established.

Write an `advisor-assessment-request` artifact inside the selected project. Use `mode=broad`, the
user's language, the last 28 complete days with `previous-period` by default, and the known business
model. Reference a matching baseline and approved measurement plan when available. Record Search
Console as `ready` only when the selected authorization and exact property already permit it. Compute
`contentSha256` over canonical content excluding that field, then validate the artifact:

Set `gtmContainer` only for a discovered resource whose `containerKind` is `gtm` and whose features
support workspaces. A resource marked `google-tag` may still have a Tag Manager API container path,
but it has no GTM workspace to audit: keep `gtmContainer` null and use the site scan plus GA4 stream
correlation for that Google tag. Never send a `google-tag` container path into the deep GTM baseline.

```text
google-analytics contracts validate --schema advisor-assessment-request --input <absolute-request-path> --json
```

Do not start OAuth consent automatically merely to make the assessment broader. If Search Console is
not ready, continue with GA4 and disclose the unavailable domains.

## Plan before performance reads

```text
google-analytics advisor full-picture plan --request <absolute-request-path> --json
google-analytics advisor full-picture show-plan --plan <absolute-plan-path> --json
```

Planning may perform the existing bounded resource/metadata preflights, but it must not read GA4 or
Search Console performance rows. Inspect the exact identities, aligned periods, five ordered steps,
fourteen-domain completeness matrix, child-plan references, budgets, blockers, expiry, and full
`planSha256`. The execution order is `baseline`, `ga4`, `search-console`, `cross-source`, then
`synthesis`. Source failures do not cause automatic full-suite retries.

## Run and resume

```text
google-analytics advisor full-picture run --plan <absolute-plan-path> --json
google-analytics advisor full-picture resume-plan --checkpoint <absolute-checkpoint-path> --json
```

Execution is sequential. Each step writes an immutable checkpoint containing exact identities,
periods, step states, domain states, budgets, result hashes, and the previous-checkpoint hash. A
normal source failure produces a partial checkpoint and lets independent evidence continue. Identity,
integrity, or context drift fails closed.

After interruption or a recoverable failure, use only the newest checkpoint. `resume-plan` verifies
the complete chain and every completed result, creates a fresh expiring plan, reuses valid completed
steps, and retries only unfinished or failed work. Never edit a checkpoint, child plan, or source
report to make it resumable.

## Explain the report

```text
google-analytics advisor full-picture show --report <absolute-report-path> --language auto --json
```

Present the diagnosis, confidence reasons, changes, associated drivers, gaps, no more than five
prioritized recommendations, and exactly one current safe next step. Keep facts, calculations,
interpretations, findings, limitations, and questions distinct. Preserve every unavailable,
not-applicable, stale, blocked, or not-checked domain and every source-quality warning.

Every recommendation must retain evidence references, expected benefit without a guarantee, effort,
risk, verification, and whether a separate mutation workflow is required. A recommendation is not an
approval. If the user chooses one that changes GA4, GTM, Search Console, website code, publishing, or
deployment, enter that capability's separate plan and confirmation workflow.
