# Privacy

## Current version

Google Analytics Advisor 0.4.3 runs locally in the user's environment. It does not collect telemetry
and does not send prompts, credentials, project files or analytics data to Anilau. Google
authorization and connection diagnostics communicate directly from the user's computer to Google;
they do not pass through Anilau infrastructure.

During assisted onboarding, an existing Google Cloud CLI or the user's Cloud Console session can be
used to inspect the selected account/project, create a confirmed customer project, and enable the
three required APIs. These interactions go directly to Google. The advisor must not read Cloud CLI
credential files or print access tokens, and it must obtain confirmation before changing Cloud state.

Browser-assisted setup is optional and begins only after explicit permission for the current OAuth
onboarding session. It uses the user's already signed-in browser session directly with Google. The
advisor must not request, read or enter passwords, recovery codes or 2FA codes. The user can instead
choose detailed self-service instructions or withdraw browser permission at any time.

Runtime diagnostics inspect the local operating system, Python candidates, filesystem writability and
TLS availability. Their JSON output remains local unless the user chooses to share it. Project
artifact validation reads only the file explicitly supplied by the user.

The optional version check requests a user-disclosed trusted HTTPS manifest containing public release
metadata. It sends no installation identifier, credentials, analytics data or project data. The local
state stores the last check time and public result for 30 days outside the plugin source. The check is
not configured by default and can be disabled from the CLI.

## Google authorization data

The customer supplies a Desktop OAuth client from the customer's own Google Cloud project. The local
CLI sends the browser authorization request, authorization-code exchange, token refresh, optional
revocation and minimal read-only API probes directly to Google over HTTPS. The requested scopes cover
identity, GA4 read/edit and GTM read/edit/version/publish. No analytics or GTM mutation is implemented
in 0.4.3, and authorization does not approve future mutations.

The imported OAuth client and durable refresh token are retained until the user removes them. Windows
protects them for the current user with DPAPI; macOS uses Keychain; Linux uses Secret Service through
`secret-tool`. There is no plaintext fallback. Access tokens are held only in process memory. A
non-secret local index stores opaque profile/client references, masked client identifiers, project ID,
timestamps, state and confirmation hashes outside plugin source and caches. It does not store email
addresses or tokens.

`auth forget-local` deletes the selected local refresh token without revoking Google's grant.
`auth revoke` asks Google to revoke the grant and deletes the local token only after a definite
successful response. `auth client remove` deletes an unused imported client. Each destructive command
requires the confirmation displayed by a preceding status/list command. The downloaded source JSON is
not deleted automatically.

Google is the external recipient for authorization and diagnostic requests and applies its own terms
and privacy policy. Customer OAuth applications, Cloud projects, quotas and credentials belong to the
customer and are not routed through Anilau infrastructure.

Do not include secrets or customer analytics exports in support messages. If a future support case
requires diagnostic data, the customer must review and explicitly choose what to share.
