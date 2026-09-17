# Security Policy

## Supported version

Security fixes are provided for the latest published release. Install the newest version before
reporting a problem.

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability. Email
[admin@anilau.com](mailto:admin@anilau.com) with:

- the affected plugin version and operating system;
- a concise description of the risk;
- safe reproduction steps using synthetic data;
- the relevant file or command name.

Do not send real OAuth clients, access or refresh tokens, passwords, private keys, Authorization
headers, customer Analytics exports, production datasets, or private website source. You will receive
an acknowledgement when the report has been reviewed; no fixed response or resolution time is
promised.

## Scope

The plugin is designed to keep credentials in protected operating-system storage, send Google API
requests directly to Google, reject secret-bearing project artifacts, and require exact confirmation
for supported mutations. Reports that show a bypass of these safeguards are especially valuable.

The canonical validation entrypoint runs with a loopback-only network policy. API tests use injected
fake transports and synthetic fixtures; an accidental request to a production host is blocked before
HTTP. Mutation and Measurement Protocol tests therefore cannot change a Google resource or send a
production event. OAuth loopback tests may contact only the local one-use callback server.

Cross-source analysis accepts only absolute source paths inside the selected project, verifies the
file and internal artifact SHA-256 values before planning and again before execution, and has no
authorization or transport dependency. It never performs fuzzy URL matching and never persists
Search Console query or fragment values.

Search Console/GA4 link planning is local-only and accepts only project-contained artifacts with
canonical SHA-256 validation. Ready plans expire after 30 minutes, are single-use, contain one exact
UI Submit, and require the full plan hash. The CLI has no link-management transport and cannot click
Google UI. Browser-assisted execution needs separate permission, stops before Submit, rejects account
or resource drift, and never retries an ambiguous response. Result artifacts reject account email,
cookies, screenshots, raw HTML, passwords, MFA/passkeys, and browser storage. Existing links are
never deleted or recreated by this workflow.

HTTP transport accepts credential-free HTTPS URLs on the standard port, bounds retries and response
sizes, validates JSON responses, and removes Authorization headers on cross-host redirects. OAuth
form requests are limited to Google's token and revocation endpoints. Protected credential backends
reject empty or oversized values and never fall back to plaintext storage.
