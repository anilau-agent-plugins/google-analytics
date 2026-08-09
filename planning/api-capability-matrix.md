# API capability matrix

Verified against official Google documentation on 2026-08-09. Every later implementation stage must
refresh the relevant row before changing code or claiming compatibility.

## OAuth scopes requested together

- `openid`
- `email`
- `https://www.googleapis.com/auth/analytics.readonly`
- `https://www.googleapis.com/auth/analytics.edit`
- `https://www.googleapis.com/auth/tagmanager.readonly`
- `https://www.googleapis.com/auth/tagmanager.edit.containers`
- `https://www.googleapis.com/auth/tagmanager.edit.containerversions`
- `https://www.googleapis.com/auth/tagmanager.publish`

The set intentionally excludes Analytics provisioning/user-deletion/user-management, GTM delete,
GTM user/account management, Firebase, Google Ads, Data Manager and Cloud Platform scopes.

## Analytics Data API

| Capability | Channel | Scope | V1 | Safety and evidence |
| --- | --- | --- | --- | --- |
| `runReport`, pagination | v1beta REST surface documented as Data API v1 | `analytics.readonly` | Yes | Read-only; record request, response metadata, quota and page bounds. |
| `batchRunReports`, pivot reports | v1beta | `analytics.readonly` | Yes, when materially cheaper | Read-only; do not batch unrelated user requests. |
| `runRealtimeReport` | v1beta | `analytics.readonly` | Yes | Diagnostic evidence only; realtime is not durable verification. |
| `getMetadata` | v1beta/v1alpha reference surface | `analytics.readonly` | Yes | Use property metadata before accepting dimensions/metrics. |
| `checkCompatibility` | v1beta | `analytics.readonly` | Yes | Required before non-trivial custom reports. |
| `runFunnelReport` | v1alpha | `analytics.readonly` | Experimental read-only | Feature flag, alpha warning and compatibility guard. |
| Audience exports | v1beta | `analytics.readonly` | No | Outside beginner-focused V1. |

Quota classes are Core, Realtime and Funnel. Standard properties currently expose 200,000 tokens per
property/day, 40,000 per property/hour, 14,000 per project/property/hour and 10 concurrent requests
per property in each class. Always request `returnPropertyQuota` where supported. Potentially
thresholded dimensions have an additional 120 requests/property/hour allowance. Treat these values
as a dated snapshot, not permanent constants.

Reports may be sampled or thresholded and may differ from the UI. Store response metadata and show
limitations; never silently label API output as complete.

Sources:

- https://developers.google.com/analytics/devguides/reporting/data/v1/basics
- https://developers.google.com/analytics/devguides/reporting/data/v1/quotas
- https://developers.google.com/analytics/devguides/reporting/data/v1/reporting-data-expectations
- https://developers.google.com/analytics/devguides/reporting/data/v1/funnels

## Analytics Admin API

| Capability | Channel | Scope | V1 | Safety and evidence |
| --- | --- | --- | --- | --- |
| Account summaries and accounts | v1beta: `accountSummaries.list`, `accounts.get/list` | `analytics.readonly` or `analytics.edit` | Read only | Account creation and Google terms remain manual. |
| Properties | v1beta: `properties.get/list/create/patch` | readonly for reads; edit for writes | Yes | Delete/trash and user-data acknowledgement are excluded. |
| Data retention settings | v1beta: `properties.getDataRetentionSettings/updateDataRetentionSettings` | readonly/edit by method | Yes | Mutation plan and readback required for update. |
| Web data streams | v1beta: `properties.dataStreams.get/list/create/patch` | readonly/edit by method | Yes | Web streams only; delete and app stream creation are excluded. |
| Enhanced measurement | v1alpha: `properties.dataStreams.getEnhancedMeasurementSettings/updateEnhancedMeasurementSettings` | readonly/edit by method | Experimental, off by default | Explicit feature flag, alpha warning, pinned request/response contract and fail-closed compatibility check. |
| Data redaction | v1alpha: `properties.dataStreams.getDataRedactionSettings/updateDataRedactionSettings` | readonly/edit by method | Experimental, off by default | Same alpha gate as enhanced measurement. |
| Key events | v1beta: `properties.keyEvents.get/list/create/patch` | readonly/edit by method | Yes | Use KeyEvent resources, not deprecated ConversionEvent; delete excluded. |
| Custom dimensions | v1beta: `properties.customDimensions.get/list/create/patch` | readonly/edit by method | Yes | Archive excluded. |
| Custom metrics | v1beta: `properties.customMetrics.get/list/create/patch` | readonly/edit by method | Yes | Archive excluded. |
| Measurement Protocol secrets | v1beta: `properties.dataStreams.measurementProtocolSecrets.get/list/create/patch` | readonly/edit by method | Yes | Secret value goes directly to OS secret storage and never into snapshots; delete excluded. |
| Event create/edit rules | v1alpha | edit | No by default | Reassess after core event implementation is stable. |
| Google Ads/Firebase/BigQuery links | mixed alpha/beta | edit | No | Separate future products and scopes. |
| User permissions/access reports | mixed | manage-users scopes | No | Scopes intentionally absent. |
| Delete/trash/archive | mixed | edit | No | Destructive operations excluded from V1. |

Alpha endpoints can break. Enhanced measurement and data redaction therefore remain disabled until
the user approves the experimental feature after a live contract check. The runtime must pin the
exact channel per capability and fail closed when a supported method moves or changes.

Sources:

- https://developers.google.com/analytics/devguides/config/admin/v1
- https://developers.google.com/analytics/devguides/config/admin/v1/rest
- https://developers.google.com/identity/protocols/oauth2/scopes

## Tag Manager API v2

| Capability | Scope | V1 | Safety and evidence |
| --- | --- | --- | --- |
| List accounts/containers/workspaces/entities | `tagmanager.readonly` or edit scope accepted by method | Yes | Bounded pagination and serialized requests. |
| Create/update container and workspace entities | `tagmanager.edit.containers` | Yes | Isolated workspace, current fingerprint and exact diff. |
| Synchronize workspace (`workspaces.sync`) | `tagmanager.edit.containers` | Yes | Conflicts block later mutation/version creation. |
| Read workspace status (`workspaces.getStatus`) | `tagmanager.readonly` or `tagmanager.edit.containers` | Yes | Read-only conflict/status evidence. |
| Quick preview (`workspaces.quick_preview`) | `tagmanager.edit.containerversions` | Yes | Preview evidence only; never implies publish approval. |
| Create container version | `tagmanager.edit.containerversions` | Yes | Separate immutable plan and readback. |
| Publish container version | `tagmanager.publish` | Yes | Separate reinforced confirmation after preview/version. |
| Delete container/version/workspace/entity | delete/edit scopes | No | Destructive operations excluded even if an edit scope permits an entity delete method. |
| Manage users/accounts | manage scopes | No | Scopes intentionally absent. |

Quota snapshot: 10,000 requests/project/day and 0.25 QPS/project, enforced over 25 requests per 100
seconds. Future transport must serialize GTM calls, apply bounded backoff to safe reads and never
automatically retry an ambiguous write.

Sources:

- https://developers.google.com/tag-platform/tag-manager/api/reference/rest
- https://developers.google.com/tag-platform/tag-manager/api/v2/authorization
- https://developers.google.com/tag-platform/tag-manager/api/v2/limits-quotas

## Measurement Protocol

| Capability | Authorization | V1 | Safety and evidence |
| --- | --- | --- | --- |
| Validate web events at `/debug/mp/collect` | measurement ID + API secret | Yes | `ENFORCE_RECOMMENDATIONS`; no report data is created. |
| Send web events to `/mp/collect` | measurement ID + API secret | Yes, explicit production path only | Validate first; never retry an ambiguous send automatically. |
| App events | Firebase app ID + API secret | No | Mobile/Firebase excluded. |

Current limits include 25 events per request, 25 parameters per event and 40-character event and
parameter names. The validation server does not validate the API secret itself. Measurement Protocol
supplements rather than replaces browser tagging.

Sources:

- https://developers.google.com/analytics/devguides/collection/protocol/ga4
- https://developers.google.com/analytics/devguides/collection/protocol/ga4/reference
- https://developers.google.com/analytics/devguides/collection/protocol/ga4/validating-events
