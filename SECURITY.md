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
