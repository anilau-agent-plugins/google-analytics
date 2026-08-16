# Safe GA4 configuration

Use this workflow only for supported Analytics Admin API configuration. It does not authorize GTM,
website, deployment, account, user, archive, delete, or production-event operations.

## Before planning

1. Select an exact connected profile and exact Google resource names.
2. Open an approved measurement-plan v2 and verify that its profile/property/site binding still
   matches the request.
3. Explain what the user wants in ordinary language and derive only the necessary supported fields.
4. Create a local `ga4-change-request` file below the selected project. Do not ask the user to edit
   JSON or provide an arbitrary API URL/body.

Example shape for one existing-resource update:

```json
{
  "schemaVersion": 1,
  "changeRequestType": "ga4-change-request",
  "projectRoot": "/absolute/project/path",
  "profileId": "profile-id",
  "operations": [
    {
      "kind": "KEY_EVENT_PATCH",
      "resource": "properties/123/keyEvents/456",
      "fieldMask": ["countingMethod"],
      "body": {"countingMethod": "ONCE_PER_EVENT"},
      "rationale": "Count every confirmed occurrence of the approved business outcome."
    }
  ]
}
```

Create operations must be the only operation in their change request. Existing-resource updates may
contain up to 20 coherent operations within one property. Alpha operations also require both
`"experimentalAdminAlpha": true` and `"alphaWarningAccepted": true`; do not add these flags unless
the user explicitly accepted the current experimental warning.

## Plan

Windows:

```text
powershell -NoProfile -File <plugin-root>\scripts\google-analytics.ps1 ga4 plan --profile <id> --measurement-plan <approved-path> --changes <change-path> --json
```

macOS/Linux uses the `.sh` launcher. Planning refreshes current state and stores immutable snapshots
and a 30-minute mutation plan. Show the result in this order: goal, why, current state, requested
state, risk, readback, expiry, exact hash. Stop and request exact hash confirmation.

## Apply

After exact confirmation only:

```text
ga4 apply --plan <absolute-plan-path> --confirm-sha256 <exact-64-hex> --json
```

The CLI revalidates the approved measurement plan, expiry, replay status, authorization profile and
every precondition. A mismatch blocks all writes. Each write has one attempt. `applied` requires an
independent readback matching every requested field.

If the result is `ambiguous` or `partial`, do not retry. Run:

```text
ga4 reconcile --journal <absolute-journal-path> --json
```

Reconciliation reads current state only. Explain whether a matching resource is observed and ask for
a new plan only after the result is understood.

## Measurement Protocol credentials

Create a Measurement Protocol credential only when the approved measurement plan explicitly includes
a server-side Measurement Protocol design. The provider response is intercepted in process memory
and the value is placed in DPAPI, Keychain, or Secret Service. Only an opaque credential reference
may be reported. Do not call credential list/get for general discovery and never expose the value.

Creating the credential does not authorize sending a debug or production event. Event delivery
belongs to the separately planned website/server implementation stage.
