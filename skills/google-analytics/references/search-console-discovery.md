# Search Console authorization and property discovery

Read this reference when the user wants to connect Google Search Console, inspect available Search
Console properties, or when a broad site assessment needs to establish whether organic-search data
can be added later.

## Capability boundary

This workflow can request read-only Search Console access and list the properties visible to the
connected Google account. After exact selection, Search Analytics performance is available through
[search-console-performance.md](search-console-performance.md). Sitemaps and URL Inspection remain
unavailable. The plugin cannot add, verify, or delete a property, manage users, or create the
GA4/Search Console link.

The only Search Console scope is
`https://www.googleapis.com/auth/webmasters.readonly`. Never request the broader
`https://www.googleapis.com/auth/webmasters` scope.

## Existing authorization profile

Run `auth status --profile <profile-id> --json` before any Search Console command.

- If `capabilities.searchConsole.status` is `ready`, do not ask for consent again.
- If GA4/GTM is ready but Search Console says `authorization_required`, explain the single new
  read-only permission. Run `auth consent-preview --profile <profile-id> --json`, then use
  `auth upgrade --profile <profile-id> --json` only after the user agrees to open Google consent.
- Upgrade must keep the same Google account. If Google returns another account, stop and let the user
  choose the original account or create a separate profile with `auth login`.
- A declined or failed upgrade leaves the existing GA4/GTM profile usable. Never tell the user that
  GA4 must be reconnected merely because Search Console is unavailable.

The existing Desktop OAuth client is reused. If the Search Console API or consent-screen scope is
not configured in the user's Cloud project, follow
[google-cloud-oauth-setup.md](google-cloud-oauth-setup.md). Cloud changes and browser control still
need their own explicit confirmation.

## Discover properties

After Search Console capability and API readiness are confirmed, run:

```text
google-analytics search-console sites list --profile <profile-id> --json
```

This makes one logical read-only `sites.list` operation. Preserve every limitation and the provider's
exact `siteUrl` and permission value.

Explain the property forms:

- URL-prefix: `https://www.example.com/` covers only that exact protocol, host, and prefix;
- Domain: `sc-domain:example.com` covers the domain across protocols and subdomains according to
  Search Console rules.

Use `selectionKey` exactly in later Search Console workflows. Do not remove a trailing slash,
rewrite `www`, switch HTTP/HTTPS, reduce a subdomain to its parent, or merge a URL-prefix property
with a Domain property.

If several entries could match the project, show their exact identities and permission levels and
ask the user to choose. Never select by display name or list order. Owner, full-user, and restricted-
user entries are read candidates. Unverified or unknown entries stay visible but are not selected
for data reads.

An empty successful list is not proof that the API is broken. Explain that the response alone cannot
distinguish between no configured properties and no access for the connected account. A mixed list
is partial, not failed.

## Error handling

- `SEARCH_CONSOLE_AUTHORIZATION_REQUIRED`: offer the explicit scope-upgrade flow.
- `SEARCH_CONSOLE_API_DISABLED`: offer the exact project-specific API enable step; do not request a
  Cloud Platform scope.
- `SEARCH_CONSOLE_SCOPE_MISSING`: re-check the granted scope set; do not broaden permissions.
- `SEARCH_CONSOLE_ACCESS_DENIED`: verify the Google account and Workspace policy.
- `SEARCH_CONSOLE_TOKEN_INVALID`: reauthorize the exact profile.
- `SEARCH_CONSOLE_QUOTA_LIMITED`: wait and retry later; do not start a retry loop.
- `SEARCH_CONSOLE_RESPONSE_INVALID`: do not use a partial malformed list as complete evidence.

Search Console failure is a limitation for a broad advisor request. Continue any valid GA4 analysis
and state that organic-search performance remains unavailable until the access issue is resolved.
