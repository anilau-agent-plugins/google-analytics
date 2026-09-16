# Proactive advisor playbook

Use this playbook when the user asks for an overall assessment, an unexplained business-result
change, or improvement ideas without limiting the request to one metric or slice. It coordinates the
existing read-only baseline and reporting workflows. It does not add an API, scope, credential,
mutation permission, or durable orchestration artifact.

## Route the request by meaning

Choose one mode from the whole request, not from one keyword:

- **Broad:** the user asks how the site is doing, what is wrong, why outcomes changed, what to
  improve, or for a complete analytics check without limiting the investigation to one metric,
  event, page, channel, device, or period.
- **Targeted:** the user asks for a concrete number, event, period, segment, page, channel, device,
  or hypothesis. Use the smallest existing reporting or diagnostic workflow that answers it. Offer a
  broader assessment afterwards only when useful; do not run it automatically.
- **Ambiguous:** the project, exact property, period, or meaning of the requested outcome cannot be
  resolved safely. Ask the single question that unlocks the most progress. Do not ask the user to
  choose GA4 dimensions, metrics, or preset names.

Requests such as "why did this landing page lose organic sessions?" remain targeted even though
they contain "why". A broad request may become targeted when the user explicitly narrows it.

If the request also asks to fix something, complete the permitted read-only diagnosis first. Move
the fix to the existing separate measurement, GA4, website, or GTM planning workflow. Analysis,
OAuth scopes, and an earlier approval never authorize a mutation.

## Establish exact context

Before Google reads:

1. Resolve the local project, authorization profile, and exact `properties/<id>` resource.
2. When multiple resources are plausible, explain the distinguishing identifiers and ask the user
   to select the exact resource name. Never select by display name alone.
3. Resolve the business goal and authoritative outcomes from an approved measurement plan, a
   relevant baseline, or explicit user context. Preserve unknowns as unanswered questions.
4. Find related baseline, approved measurement-plan, and report artifacts under the selected
   project. Accept them only when their identity, integrity, status, and coverage match.
5. Treat an artifact as stale after a known related site, GA4, or GTM change. When freshness cannot
   be established, say so and offer the appropriate read-only refresh rather than silently trusting
   it.

If authorization is missing, access is denied, network access is forbidden, the property remains
ambiguous, or an immutable artifact is invalid, stop before Google reads and explain the one safe
action that can unblock the assessment.

## Run the broad GA4-only route

### 1. Establish measurement reliability

Read [baseline-audit.md](baseline-audit.md) and use a matching baseline when available. Check the
tag/GTM route, probable duplication, stream identity, consent evidence, key events, ecommerce, and
relevant settings. Relate event evidence to the approved measurement plan when one exists.

Do not treat source-code matches or performance rows as proof that production collection is correct.
Do not call a click, submit, or success-page view a lead, registration, or purchase without an
authoritative completion design.

When no current baseline exists, continue only with the reporting evidence that is safe to use,
lower confidence, mark measurement checks as unavailable or stale, and make a read-only baseline the
leading next step when it materially blocks interpretation.

### 2. Build one bounded performance suite

Read [reporting-advisor.md](reporting-advisor.md). Default to the last 28 complete days in the
property timezone and the immediately preceding comparable period. Let the agent select applicable
presets; the user should not manage the registry.

Use this default suite for a broad request:

- core: `overview`, `acquisition`, `landing`, `content`, `device`, and `events`;
- add `key-events` when key events are available;
- add `ecommerce` only when an approved measurement plan or matching baseline says ecommerce is
  applicable;
- add `geo` or `user-acquisition` only when it helps the business question and quota permits;
- use `realtime` only for a current diagnostic, never as durable trend evidence;
- use `funnel-experimental` only when every existing experimental gate is satisfied and the question
  genuinely requires the approved funnel;
- use `custom-core` only when closed presets cannot answer the question, and explain the chosen
  fields in everyday language.

Prepare one report request where possible, inspect its immutable plan, and run queries sequentially.
Do not expand or retry after a quota safety stop. Do not replace a restricted, incompatible, empty,
or skipped dataset with zero or an inference from another dataset.

### 3. Track completeness

Before calling the result a full assessment, assign one state to every domain:

- `checked`: relevant evidence was examined;
- `not_applicable`: the domain does not apply and the reason is known;
- `unavailable`: the source or required context is absent;
- `blocked`: a safety, access, quota, or integrity condition prevented it;
- `stale`: matching evidence exists but cannot be treated as current;
- `not_checked`: applicable work remains and must be disclosed.

Track these domains:

1. exact resource identity and period;
2. business goal and authoritative outcomes;
3. tag/GTM route and probable duplicate collection;
4. consent and measurement coverage;
5. users, sessions, and engagement trend;
6. acquisition;
7. landing pages and content;
8. device and, when useful, geography;
9. events and key events;
10. ecommerce when applicable;
11. sampling, thresholding, cardinality, restrictions, truncation, incomplete periods, small data,
    and quota coverage;
12. unanswered business questions.

Google Search Console is outside this stage. Its absence never blocks available GA4 analysis. Do
not request a Search Console scope, credential, property, or live access; simply state that organic
search visibility was not assessed when it matters to the user's question.

Do not describe the assessment as complete when an applicable domain remains `not_checked` without
an explanation.

## Stop or degrade safely

Stop confident business conclusions, while retaining verified technical facts, when measurement is
duplicated or materially incomplete, the authoritative outcome is unknown, key events are only proxy
signals, or the result is empty, critically restricted, or insufficient.

Continue with an explicitly partial assessment when a baseline or measurement plan is missing, an
optional domain is not applicable, a quota floor skips later queries, one field is restricted or
incompatible, or one provider request fails. Preserve every source limitation and list the affected
completeness domains. Never fill a gap with an assumption.

Sampling, thresholding, `(other)` loss, truncation, an incomplete period, or small data may allow a
directional description but must lower confidence. Realtime and experimental funnels are never
completeness evidence for durable business performance.

## Present the result

For a broad assessment, use this order:

1. short diagnosis in everyday language;
2. confidence in the data and the main reasons;
3. what changed in traffic and authoritative outcomes;
4. which channels, pages, and devices are associated with the change;
5. what is unreliable, unavailable, stale, not applicable, or not checked;
6. no more than five prioritized actions;
7. one current safe next step to take together.

Each action must include the problem, evidence references, expected benefit without a guarantee,
effort, risk, verification method, and whether it requires a separate mutation workflow. Keep
confirmed facts, calculations, interpretations, recommendations, and unanswered questions distinct.
Keep exact GA4 names beside the explanation. Never turn correlation into causation.

For a targeted request, lead with the direct answer, then reliability and relevant limitations, and
finish with one proportionate next step. Do not force the broad response structure onto a single
number.

## Resume without repeating work

When the assessment cannot finish in one turn, state:

- the exact project/profile/property and period;
- which completeness domains are complete and which remain;
- the immutable artifact paths and hashes already accepted;
- the blocker or next safe read;
- the single prompt the user can use to continue.

On continuation, revalidate identity, integrity, relevance, and known changes. Reuse valid evidence
instead of rerunning the whole suite. Never edit an immutable artifact to make it look current.
