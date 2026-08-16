# Safe website measurement installation

Use this reference for Stage 8 local source changes and Measurement Protocol delivery. This workflow
does not authorize production deployment or remote GTM changes.

## Local source workflow

1. Require an approved measurement-plan v2 and the exact local project root.
2. Run `site context --project-root <root> --measurement-plan <plan> --json` and explain blockers.
3. Preserve a single existing direct or GTM route. For a new simple GA4-only site prefer direct;
   choose GTM for multi-product or independently managed tagging.
4. Prepare a `website-change-request` and exact restricted unified diff. Keep generated, dependency,
   credential, environment, binary, linked, delete, rename, and outside-root paths out of the diff.
5. Run `site plan --context <context> --changes <request> --patch <diff> --json`.
6. Explain every file, why it changes, risks, exact offline verification commands, expiry,
   `deploymentApproved=false`, and the complete `planSha256`.
7. Ask for the exact hash. General approval, OAuth, measurement-plan approval, or a prior hash is not
   enough. Run `site apply` only after the matching confirmation.
8. Report file readback, static scan limitations, pending project commands, and no-deploy status.
   Use `site verify` or read-only `site reconcile` if later state is uncertain.

Never execute project verification commands automatically. They can run arbitrary project code.
Present the exact executable, argument array, cwd, timeout, and expected exit codes and obtain a new
permission before running them. Never install/update packages, migrate data, publish, or deploy in
this workflow.

## Measurement rules

- Place consent defaults before direct tag/GTM loading and measurement commands.
- Require `analytics_storage`, `ad_storage`, `ad_user_data`, and `ad_personalization`; map updates to
  the existing CMP/consent mechanism. Do not create a CMP or choose legal policy.
- Use one SPA page-view owner: automatic history handling or manual router events, never both.
- Emit events only from the approved authoritative completion state and add an idempotency rule for
  remounts, refreshes, callbacks, and retries.
- For `purchase`, use backend order state, a unique non-PII `transaction_id`, numeric `value`,
  three-letter `currency`, and `items`. Resolve browser/server ownership before implementation.
- In the GTM route, Stage 8 installs only the web snippet and `dataLayer` contract. Remote tags,
  triggers, versions, preview, and publish belong to Stage 9.

## Measurement Protocol

Create a delivery plan with an opaque protected credential reference, public measurement ID, and a
synthetic or explicitly approved payload. The payload must match approved server-owned events and
contain no PII. A web payload needs the matching `client_id`; session-linked events need `session_id`
and `engagement_time_msec`.

Use a debug plan first. Confirm its exact hash and run `mp validate`; the CLI sends once to
`/debug/mp/collect` with `ENFORCE_RECOMMENDATIONS`. Debug events do not enter reports. Any validation
message blocks progression.

Create a new production plan only when the user explicitly asks to send that exact event. Confirm
its new exact hash and run `mp send` once. Never retry after timeout or uncertain network failure;
report `ambiguous`. A successful HTTP response means the request returned, not that GA4 processed it
or that it will appear in reports.
