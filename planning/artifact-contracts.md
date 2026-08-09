# Artifact contracts

The formal Draft 2020-12 schemas live in `planning/contracts/`. These are Stage 1 contracts, not an
implemented runtime. Stage 3 must turn them into dependency-free Python validation without weakening
the constraints.

## Common conventions

- All artifacts use `schemaVersion: 1`, an exact `artifactType`, ISO 8601 UTC `generatedAt` and
  `additionalProperties: false` at governed levels.
- Identifiers are strings because Google resource names and numeric IDs must not lose formatting.
- Monetary values are decimal strings, never binary floats.
- Unknown optional business facts use JSON `null`; enum-like unknown state uses the literal `unknown`.
- Secret-bearing names and values are not accepted by any project artifact contract.
- Content hashes use lowercase SHA-256 hex.

JSON Schema recursively rejects case-insensitive secret-bearing keys in arbitrary provider payloads.
Runtime validation must independently repeat that recursive scan,
verify canonical hashes, require `generatedAt < expiresAt`, check resource/profile consistency and
apply domain rules such as ecommerce currency and transaction identity.

## `project-profile`

Stores project identity, selected Analytics property/web stream, optional GTM container, website,
timezone/currency and confirmed business outcomes. It contains only credential references, never
OAuth clients, refresh/access tokens or API secrets.

## `measurement-plan`

Stores approved business outcomes, events, parameters, key-event intent and source of truth. Its
ecommerce section records currency/value/transaction sources, item requirements and deduplication.
Its consent section records all four Consent Mode v2 defaults, regional overrides, update trigger,
persistence and whether the user's policy choice is confirmed. It is local design evidence and
cannot authorize GA4, GTM or website mutations.

## `snapshot`

Stores the exact API channel, resource identity, retrieval time, content hash, request IDs and
normalized non-secret state used as a precondition. A snapshot is immutable.

## `mutation-plan`

Stores one target system, exact operations, snapshot preconditions, plan expiry, risk class and
expected readback. `planSha256` is calculated over canonical plan content with that field omitted.
Any content, API contract, profile or guarded resource change makes the plan stale.
Schema rules bind website plans to `LOCAL_CODE_CHANGE`/`FILE_PATCH`, Analytics Admin plans to
`REMOTE_CONFIG_CHANGE` and remote HTTP methods, and GTM plans to its three allowed risk classes and
remote HTTP methods. Runtime validation additionally requires expiry after generation and every
operation resource to be covered by a snapshot precondition.

## `report`

Stores periods, source queries, facts, calculations, interpretations, limitations, prioritized
recommendations and open questions. A recommendation is not a mutation request. Sampling,
thresholding, quota and incomplete-period evidence must be preserved when present.
Every query records provider, API channel, method, request ID, filters, the quota-request flag and
response metadata for sampling, thresholding, data loss from `(other)` rows and quota consumption.

## `journal-entry`

Stores the result of one confirmed mutation attempt: plan reference, confirmation hash, start/end,
request IDs, status and readback. It must distinguish `applied`, `no_op`, `partial`, `ambiguous`,
`failed` and `blocked`.

## Validation fixtures

Each schema has a valid fixture and an invalid fixture. Invalid fixtures exercise a meaningful
guard rather than malformed JSON: secret leakage, missing required fields, invalid hashes, invalid
risk/status values or unstructured evidence.
