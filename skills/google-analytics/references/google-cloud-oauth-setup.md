# Customer-owned Google OAuth setup

Use this guide only when the user asks to connect Google Analytics Advisor. Google Console labels can
change; prefer the current official screens and explain concepts rather than relying on an exact menu
position.

## Operating principle

After local client/profile discovery, always offer two explicit modes before Google Cloud setup:

1. **Detailed self-service** — give the user official links and precise screen/field instructions.
2. **Browser-assisted** — use browser control only after the user explicitly permits it and confirms
   that the intended Google account is already signed in there.

Do not silently choose browser control. Browser permission is session-scoped, revocable at any time,
and is not permission for later GA4/GTM/site mutations. If the browser is not signed in, pause for the
user to complete sign-in and 2FA personally; never ask for or type authentication secrets.

The project, consent screen, client, quotas and Google data must remain under the user's control. Do
not create or use an Anilau OAuth application, proxy or service-account key.

## Assisted setup ladder

1. Check for an already imported client/profile. If none is suitable, detect `gcloud` without
   installing it. When present, use read-only commands with JSON/quiet output to identify the active
   account, current project and accessible candidate projects. Never print access tokens or credential
   files. When absent, continue through Cloud Console; do not make Google Cloud CLI installation a
   prerequisite.
2. Prefer an existing project controlled by the user. Ask the user to choose only when multiple
   plausible projects exist or organization ownership is unclear. Creating a project changes Google
   Cloud state and its immutable ID/organization parent can matter: propose the exact ID, display name
   and parent, obtain confirmation, then use `gcloud projects create` when available or Cloud Console
   browser control otherwise. Do not attach billing unless a required API explicitly needs it and the
   user separately approves the billing account.
3. Read back the selected project. Prepare an exact plan to enable only
   `analyticsadmin.googleapis.com`, `analyticsdata.googleapis.com`, and
   `tagmanager.googleapis.com`. After one confirmation covering those three services and the exact
   project, run `gcloud services enable ... --project <project-id>` when available. Otherwise operate
   the API Library pages. Verify enabled state afterward; do not request a general Cloud OAuth scope.
4. Open Google Auth Platform for the selected project. In browser-assisted mode, first verify the
   visible Google account with the user, then operate ordinary fields/navigation. Pause for sign-in,
   2FA, account switching, organization policy blocks, terms acceptance, and choices that cannot be
   inferred safely. In self-service mode, use the detailed instructions below.
5. Use `Google Analytics Advisor` as the default app/client name. Prefer the signed-in account for
   support/contact email when Google offers it, but let the user choose if multiple addresses exist.
   Recommend **Internal** only when all intended users belong to the same Google Workspace
   organization and Google offers the option; recommend **External** otherwise. For External Testing,
   add the authorizing account as a test user and warn that testing-mode refresh tokens can expire;
   do not promise that Production publishing or Google verification will be unnecessary.
6. Add exactly the scopes returned by `auth consent-preview --json`. Do not broaden the list. Let the
   user personally review/accept Google API Services User Data Policy or any equivalent terms.
7. Go to Google Auth Platform **Clients**, choose **Create Client**, select **Desktop app**, retain the
   proposed name, create it, and download its JSON. This console flow is the supported regular Google
   Auth Platform path. Do not use `gcloud iam oauth-clients create`: it manages IAM/Workforce OAuth
   clients with a different scope model, not this Desktop client. Do not use IAP OAuth commands.
8. Capture the absolute downloaded path directly from the controlled download result if the tool
   exposes it without reading the file. Otherwise ask the user to choose a known save location and
   report only that path. Never list or scan Downloads, preview the JSON, or transfer it through chat.
9. Pass the path directly to local `auth client import`, continue with `auth login`, then verify
   `auth status` and `auth doctor`. Summarize completed steps and any remaining Google policy issue.

If browser control was selected but is unavailable, explain that limitation and offer to switch to
detailed self-service. Do not pretend browser actions succeeded.

## Detailed self-service instructions

Replace `<PROJECT_ID>` in every link with the selected project ID. Adapt labels if Google changes the
interface, but preserve the stated values and verify each resulting screen.

1. **Project:** open `https://console.cloud.google.com/projectcreate`. Set **Project name** to
   `Google Analytics Advisor`. Use a unique 6–30 character Project ID; explain that it is immutable.
   Select the user's organization/folder only when applicable, otherwise retain **No organization**.
   Click **Create**, wait until creation completes, and record the Project ID.
2. **Required APIs:** open each project-specific page and click **Enable** (or verify **API enabled**):
   - `https://console.cloud.google.com/apis/library/analyticsadmin.googleapis.com?project=<PROJECT_ID>`
   - `https://console.cloud.google.com/apis/library/analyticsdata.googleapis.com?project=<PROJECT_ID>`
   - `https://console.cloud.google.com/apis/library/tagmanager.googleapis.com?project=<PROJECT_ID>`
3. **Google Auth Platform:** open
   `https://console.cloud.google.com/auth/overview?project=<PROJECT_ID>` and choose **Get started** if
   shown. Enter **App name** `Google Analytics Advisor`; choose the user's intended support email;
   enter the intended developer contact email. The user must personally review and accept Google's
   User Data Policy/terms before continuing.
4. **Audience:** choose **Internal** only when all intended authorizing accounts belong to the same
   Google Workspace organization and Google offers it; otherwise choose **External**. For External
   Testing, add the exact authorizing Google account under **Test users** and warn about testing-mode
   refresh-token expiry.
5. **Data Access / Scopes:** add exactly the eight scopes printed by
   `auth consent-preview --json`; do not add `profile`, Cloud Platform, user-management, deletion,
   Firebase, Google Ads or Data Manager scopes. Save the configuration.
6. **Desktop client:** open
   `https://console.cloud.google.com/auth/clients?project=<PROJECT_ID>`, click **Create Client**, set
   **Application type** to **Desktop app**, set **Name** to `Google Analytics Advisor`, and click
   **Create**. Download the client JSON. Do not open it or paste it into chat.
7. Ask for only the absolute save path, pass it directly to `auth client import`, and continue with
   login/status/doctor. If any label or result differs, ask for a non-secret screenshot or the exact
   visible message and adjust the next instruction.

The CLI rejects Web application and service-account JSON. It accepts a loopback redirect chosen at
runtime on `127.0.0.1`; do not configure an out-of-band redirect and do not ask the user to paste a
browser code. Login requests all v1 scopes together, uses PKCE S256, and opens Google's consent page
in the system browser.

## Permission meaning

- `openid` and `email`: identify which Google account was connected.
- `analytics.readonly`: read GA4 reports and configuration in later stages.
- `analytics.edit`: make only separately planned and confirmed GA4 settings changes in later stages.
- `tagmanager.readonly`: inspect GTM in later stages.
- `tagmanager.edit.containers` and `tagmanager.edit.containerversions`: prepare separately approved
  GTM workspace and version changes in later stages.
- `tagmanager.publish`: publish only after an additional explicit confirmation; login never publishes.

No scope for deleting containers, managing users, creating Analytics accounts, Firebase, Google Ads,
Data Manager or general Google Cloud administration is requested.

## Safe diagnostics

After login, `auth doctor --json` asks Google only for identity and minimal read-only GA4/GTM/API
responses. If an API is disabled, use the returned project-specific enable link when available. An
access-denied result can instead mean that the connected Google account lacks access to GA4 or GTM;
do not solve that by requesting broader OAuth scopes.

Official references:

- [OAuth for desktop applications](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Google OAuth scope catalog](https://developers.google.com/identity/protocols/oauth2/scopes)
- [Google Tag Manager authorization](https://developers.google.com/tag-platform/tag-manager/api/v2/authorization)
- [OAuth security practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices)
- [Create Desktop OAuth credentials](https://developers.google.com/workspace/guides/create-credentials#desktop-app)
- [Create a project with gcloud](https://cloud.google.com/sdk/gcloud/reference/projects/create)
- [Enable services with gcloud](https://cloud.google.com/sdk/gcloud/reference/services/enable)
