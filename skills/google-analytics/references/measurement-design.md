# Measurement design workflow

Read this reference before creating, reviewing, approving, or migrating a measurement plan.
Stage 6 is local-only: it does not change GA4, GTM, website code, consent configuration, or production
data. An approved measurement plan is design evidence, not mutation approval.

## Evidence first

1. Select the exact project root and authorization/project profile.
2. Select an explicit Stage 5 baseline artifact. For a genuinely new or unconnected setup, use the
   explicit `--without-baseline` path and preserve that limitation.
3. Inspect the project source before asking questions.
4. Ask only for business facts not recoverable from code, configuration, or the baseline.
5. Never silently choose the newest baseline, a property by display name, or a familiar business
   outcome.

The agent prepares the internal answers JSON; do not ask a non-specialist to write it. Do not put
customer records, example emails/phones, tokens, cookies, Measurement Protocol secrets, or live event
payloads in the file. Use structural field descriptions and synthetic examples only.

## Outcome and source-of-truth rules

Explain the business result first. Classify it as `primary`, `secondary`, `diagnostic`, or
`guardrail`. For every result record the exact state, owner, source, decision use, evidence, and any
weaker proxy.

Use this evidence order:

1. confirmed payment/order/CRM/backend state;
2. confirmed application completion state;
3. frontend success callback after the server response;
4. protected success page;
5. click, technical submit, or page view as a diagnostic proxy.

Never recommend a proxy as a key event while a stronger measurable source is available.

## Event and parameter rules

Prefer automatically collected or enhanced-measurement events, then Google's recommended event and
prescribed parameters, then a justified custom event. Event and parameter names start with a letter,
contain only letters, numbers, and underscores, and are at most 40 characters. An event has at most
25 event parameters. Reserved names and prefixes are rejected locally.

For every parameter define meaning, structural source, type, scope, privacy classification, expected
cardinality, reporting use, and whether it needs a custom definition. Do not register a parameter
without a reporting use. Do not register unique IDs, timestamps, or high/unknown-cardinality values.
Never send PII, sensitive data, or free-form user input to GA4.

## Ecommerce, identity, and server events

For ecommerce require a confirmed purchase state, non-empty stable non-PII `transaction_id`, value
semantics, ISO 4217 currency source, item identity, refunds, and retry/cross-channel deduplication.
Opening checkout or viewing a thank-you page does not prove purchase.

Measurement Protocol supplements browser tagging. A server-owned event requires client/session
linkage, late-arrival policy, and deduplication. `/debug/mp/collect` validation is future implementation
evidence and sends no report data; Stage 6 creates no secret and sends no event. A transport success
must never be described as proof of ingestion.

Use User-ID only with a stable pseudonymous internal ID, never email, phone, name, or a custom
dimension. Define signed-in, signed-out, and logout behavior before recommending it.

## Consent

Record Basic, Advanced, or unresolved mode; all four Consent Mode v2 defaults; regions; CMP; update,
persistence, and revocation behavior; and the owner-confirmed policy source. Defaults precede
measurement commands and updates happen where the user changes consent. Unresolved policy blocks
approval. Do not claim legal compliance.

## Commands

Create context with an explicit baseline:

```text
measurement context --project-root <absolute-project-root> --profile <profile-id-or-project-profile-path> --baseline <absolute-baseline-path> --answers <absolute-internal-answers-json> --json
```

For a new/unconnected setup, replace `--baseline ...` with `--without-baseline`. Create and inspect a
draft:

```text
measurement draft --context <absolute-context-path> --output-dir <project-root>/.google-analytics-advisor --json
measurement show --input <absolute-plan-path> --format plain --json
```

If the draft is not blocked, show the full meaning, limitations, semantic changes, and exact
`contentSha256`. Only after explicit user confirmation run:

```text
measurement approve --input <absolute-draft-path> --confirm-sha256 <exact-content-sha256> --json
```

Approval creates a new immutable artifact. It never overwrites the draft. To convert a legacy plan
inside project data, run `measurement migrate --input <absolute-v1-path> --json`; migration always
creates a blocked v2 draft that must be reconfirmed.

## Explain the result

Present: primary result, reliable source, proposed technical events, key-event recommendations,
ecommerce/consent decisions, blockers, verification, and the Stage 6 safety boundary. Keep GA4 names
unchanged beside plain-language explanations. If a plan is blocked, ask only the unresolved business
questions and do not suggest implementing the placeholder event.
