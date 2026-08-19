# Safe Google Tag Manager management

Use this workflow only for GTM web containers. Never use it for AMP, mobile, server-side containers,
Custom HTML, arbitrary JavaScript, user permissions, deletion, or automatic conflict resolution.

## Required evidence

Require an active customer-owned OAuth profile, an approved measurement-plan v2, the exact container
path, and a fresh `gtm context`. Supply a Stage 8 website context before configuring entities,
previewing, creating a version, or publishing. The website context must confirm the same GTM public
ID and contain no unresolved blockers.

Prepare the `gtm-change-request` JSON for the user. Do not ask a non-specialist to write provider JSON
or choose internal tag parameter types. Use only the closed templates: data-layer variable, custom
event/page-view/history trigger, Google tag, and GA4 event tag. Unknown templates, Custom HTML,
personal data, unresolved references, or events outside the measurement plan must remain blocked.

## One stage at a time

The lifecycle is deliberately separated:

1. `WORKSPACE_CREATE` creates one isolated workspace.
2. `WORKSPACE_SYNC` updates that workspace to the latest base and stops on any conflict.
3. `ENTITY_BULK_UPDATE` applies one coherent entity graph with deterministic `new_N` references.
4. `QUICK_PREVIEW` creates a temporary compiler preview.
5. `VERSION_CREATE` creates a version and replaces the original workspace with a new workspace.
6. `PUBLISH` replaces the live version with one exact checked version.

For every stage run `gtm plan`, explain the target, effect, risk, expiry and full `planSha256`, then
request that exact hash. Only afterward run `gtm apply`. An earlier approval, OAuth consent, broad
request, or confirmation for another stage is not sufficient.

Never retry a write. Before apply the CLI rereads all fingerprints and state. A conflict, changed
workspace/entity/version, changed live predecessor, expired context/plan, replay, or hash mismatch
blocks the operation. Report `applied`, `ambiguous`, `failed`, or `blocked` exactly. Use `gtm
reconcile` only for read-only inspection of an ambiguous/partial journal.

## Preview and publish

`QUICK_PREVIEW` proves only that Google compiled a temporary container version without a reported
sync/compiler error. It does not prove that a browser fires the right tag or that GA4 receives an
event. Before publish, help the user run GTM Preview/Tag Assistant and record concise runtime evidence
for the exact container: which synthetic action was performed, which tag fired once, and which
expected non-PII parameters appeared.

`VERSION_CREATE` requires a verified compiler-preview journal for the same unchanged workspace.
Explain before confirmation that Google deletes/replaces that workspace and returns a new path.

`PUBLISH` requires the verified version-creation journal plus explicit runtime evidence. Recheck the
exact version fingerprint and current live predecessor immediately before publish. Send publish once,
then independently read the live version. A timeout or incomplete readback is `ambiguous`; never
repeat publish automatically.

GTM operations never modify website files, deploy a site, change GA4 Admin settings, send production
events, or guarantee that data will appear in reports.
