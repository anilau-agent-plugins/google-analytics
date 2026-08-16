# Support

Google Analytics Advisor 0.7.0 adds separately confirmed supported GA4 Admin API configuration to the
read-only baseline and local measurement-design workflow. It is not yet available for commercial
installation. Questions about the product can be submitted through the contact channel on
[anilau.com](https://anilau.com).

When requesting help, include the product version, operating system, Python version, agent platform,
command name and redacted JSON error codes. Do not send OAuth client files, tokens, passwords, private
keys, Authorization headers, customer analytics exports or production datasets.

For authorization failures, share only the redacted error code, command name and probe status. Do not
share the downloaded OAuth client. If `auth doctor` reports `api_disabled`, enable the named API in
the customer's own Cloud project. If it reports `access_denied`, first verify that the connected
Google account has access to the intended GA4 property or GTM account; broader scopes are not a
substitute for resource permissions.

For GA4 configuration issues, include only the redacted error code, operation kind, plan ID, journal
status, and whether readback was complete. Never send Measurement Protocol credential values. If a
result is `ambiguous` or `partial`, run read-only reconciliation and do not repeat the write.

Google Cloud CLI is optional. When it is absent, OAuth onboarding continues through Google Cloud
Console. Do not install `gcloud` merely to create a Desktop client. If console labels change, use the
current Google Auth Platform Clients page and report the mismatch without sharing account data.
Browser-assisted mode requires an available browser-control capability and a browser already signed
in to the intended Google account. If either is unavailable, switch to detailed self-service mode.

The commercial license includes twelve months of repository updates from purchase unless the purchase
agreement states otherwise. Response times, extended support and data-handling arrangements are not
implied unless agreed separately.

Commercial release remains blocked until the private marketplaces, the complete local release gate,
approved website terms/privacy pages and Windows clean-install acceptance are complete. The
Windows/macOS/Linux GitHub Actions matrix runs only after a GitHub Release is published and is reported
as independent post-publication evidence. Native macOS and Linux validation remains feedback-driven.
