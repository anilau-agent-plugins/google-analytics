# Google Analytics Advisor development plan

Status date: 2026-08-09

## Product

- Display name: Google Analytics Advisor.
- Technical ID and canonical directory: `google-analytics`, `C:\dev\tools\google-analytics`.
- Author: Eduard Volkov. Developer: Anilau. Website: `https://anilau.com`.
- Distribution: private GitHub repository in the future `Anilau Agent Plugins` organization.
- Platforms: ChatGPT/Codex and Claude Code with one shared Skill and Python CLI.
- V1: GA4 website analytics. Universal Analytics, Firebase and mobile analytics are excluded.

## Delivery rule

Every stage follows `research -> detailed plan -> user approval -> implementation -> verification`.
Do not begin the next stage until the current stage meets its acceptance criteria.
Plugin packaging and every later implementation stage must also comply with the shared
[`PLUGIN_STANDARD.md`](../PLUGIN_STANDARD.md). Any justified exception requires a dated decision
record as defined by that standard.

## Stage status

1. Product and technical specification — completed.
2. Plugin packaging — detailed plan prepared; awaiting user approval before implementation.
3. Cross-platform Python runtime — pending.
4. OAuth and protected secret storage — pending.
5. Read-only discovery and baseline audit — pending.
6. Measurement design — pending.
7. GA4 configuration — pending.
8. Website measurement installation — pending.
9. Google Tag Manager management — pending.
10. Reports and plain-language advisor — pending.
11. Security and full validation — pending.
12. Commercial packaging and customer handoff — pending.

## Stage 1 evidence

- [planning/product-spec.md](planning/product-spec.md) defines users, workflows, V1 boundaries and language.
- [planning/api-capability-matrix.md](planning/api-capability-matrix.md) records current API channels, scopes, quotas and support decisions.
- [planning/artifact-contracts.md](planning/artifact-contracts.md) defines the six versioned planning contracts.
- [planning/security-and-safety.md](planning/security-and-safety.md) defines authorization, secrets and mutation safety.
- [planning/commercial-license-draft.md](planning/commercial-license-draft.md) defines the intended commercial terms for legal review.
- `planning/contracts/` contains Draft 2020-12 JSON Schemas and positive/negative fixtures.
- `planning/validate_stage1.ps1` is the repeatable Windows acceptance command; it runs the
  dependency-free semantic validator under a 512 MiB process-tree limit and tests every fixture
  against its Draft 2020-12 schema.

Stage 1 creates specifications only. It intentionally contains no plugin manifest, marketplace entry,
Skill, Analytics CLI, OAuth client, token, customer data or production mutation.

## Stage 2 preparation

- [planning/stage-2-plugin-packaging-plan.md](planning/stage-2-plugin-packaging-plan.md) records the
  researched packaging architecture, exact implementation boundary, marketplace design, acceptance
  criteria and rollback procedure.
- No Stage 2 implementation artifact has been created yet. Implementation requires explicit user
  approval of that detailed plan.

## Global V1 invariants

- Request the complete approved OAuth scope set in one authorization.
- A scope grants technical access but never authorizes a specific change.
- Keep facts, calculations, interpretations, recommendations and questions distinct.
- Preserve unknown facts as `null` or explicit `unknown`; do not invent mappings or values.
- Keep secrets outside project artifacts, plans, snapshots, reports, journals and responses.
- Use immutable, hash-confirmed plans and current-state readback for every remote mutation.
- Never retry an ambiguous non-idempotent write automatically.
- Never publish GTM or deploy a website without a separate explicit confirmation.
- Do not claim legal compliance, causality or guaranteed business results.
