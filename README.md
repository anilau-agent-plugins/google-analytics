# Google Analytics Advisor

Google Analytics Advisor is an Anilau plugin for people who want to understand website statistics
without becoming analytics specialists. It explains Google Analytics 4 in plain language and helps
turn business goals into a clear measurement plan in Codex or Claude Code.

## What version 0.5.0 can do

- explain events, key events, ecommerce, funnels, Google tag and Google Tag Manager;
- help prepare a provisional measurement plan without pretending that live settings were inspected;
- check whether the computer is ready and help install a suitable Python version;
- check saved measurement files for common structural errors;
- check whether a newer plugin version is available without sending user data;
- offer either detailed screen-by-screen self-service instructions or explicitly authorized browser
  control to create a Desktop OAuth app in the user's own Google Cloud project;
- request the complete v1 permission set once through PKCE and a local loopback callback;
- keep OAuth clients and refresh tokens in the operating system's protected credential store;
- manage multiple authorization profiles and run minimal read-only connection diagnostics.
- list accessible GA4 and GTM resources without changing them;
- inspect a selected GA4 property, website stream and GTM container through allowlisted reads;
- statically inspect local website source for Google tag, GTM, `dataLayer`, Consent Mode and likely
  duplicate collection without executing project code;
- run one bounded 28-day event diagnostic and write immutable baseline snapshots and a plain-language
  report under `.google-analytics-advisor/`.

This release does not create a measurement plan, produce full performance reports, or change GA4,
GTM, website code or production. Static source evidence does not prove that production collection
works, and partial/truncated audits are labelled explicitly. Firebase/mobile analytics, Google Ads, user
administration, account deletion and automatic production deployment are outside v1.

## How to install

1. Download and unpack the plugin folder.
2. In Codex or Claude Code, point to that folder and say: “Install this plugin.”
3. Start a new conversation and say: “Check my Google Analytics setup.”

The plugin checks the computer and explains the next step in plain language. If a suitable Python
version is missing, it offers the official installation method for Windows, macOS, or Linux and asks
for permission before installing or changing anything. It also guides the user through creating the
required Google application without asking them to paste secrets into chat. At the start, the user
chooses either a detailed field-by-field guide with official links or browser-assisted setup in a
browser where the intended Google account is already signed in. Browser use requires explicit
permission. The user still personally handles sign-in/2FA, necessary account or organization choices,
Google terms, mutation confirmations and OAuth consent.

### What the plugin uses

- Codex or Claude Code;
- Python 3.10–3.13;
- Windows, macOS, or Ubuntu/Debian Linux;
- Google Analytics 4 terminology and configuration information supplied by the user.

Internet access is required for Google authorization, connection diagnostics, resource discovery,
baseline API reads, and an explicitly requested version check. Local runtime, contract and site
source checks remain offline.

<details>
<summary>Manual installation commands</summary>

### Codex

Add this plugin to the personal marketplace at `~/.agents/plugins/marketplace.json`, using a relative
local source path, then run:

```text
codex plugin add google-analytics@personal
```

Start a new Codex task after installation or update. The current local package has been installed and
loaded through this workflow.

### Claude Code

From a parent directory containing `.claude-plugin/marketplace.json` with a relative entry for this
plugin, run:

```text
claude plugin validate ./google-analytics --strict
claude plugin marketplace add <absolute-marketplace-directory>
claude plugin install google-analytics@anilau-agent-plugins-local
```

Restart Claude Code or run `/reload-plugins` when prompted. Invoke the installed skill as
`google-analytics:google-analytics`.

</details>

## Verify the installation

Windows:

```powershell
.\scripts\google-analytics.ps1 version --json
.\scripts\google-analytics.ps1 doctor --json
```

macOS/Linux:

```sh
sh ./scripts/google-analytics.sh version --json
sh ./scripts/google-analytics.sh doctor --json
```

These commands give the agent a detailed diagnostic result. Ordinary users can simply ask the
plugin to check whether the installation works.

## Update and uninstall

Ask Codex or Claude Code: “Check whether Google Analytics Advisor has an update.” If a newer version
is available, the agent explains it and asks before installing it. Restart the application when the
agent requests it.

<details>
<summary>Manual update commands</summary>

For Codex, refresh the configured marketplace with `codex plugin marketplace upgrade`. For Claude
Code, use `claude plugin update google-analytics@<marketplace>` and reload plugins.

</details>

Remove only the installed cache/configuration:

```text
codex plugin remove google-analytics@personal
claude plugin uninstall google-analytics@anilau-agent-plugins-local
```

Uninstalling a plugin must not delete its canonical repository or future user credentials and project
data. Those are deliberately stored outside source and plugin caches.

## Version check

Version checks are disabled until a release endpoint is configured. To inspect a disclosed trusted
HTTPS version manifest without installing anything:

```text
./scripts/google-analytics.sh version --check --endpoint <trusted-https-version-manifest> --json
```

Only public version metadata is requested. A successful result is cached outside the plugin source
for 30 days. The plugin sends no installation identifier, credentials, analytics data or project data,
and never updates itself. On Windows use the `.ps1` launcher shown above. Disable checks with
`version --disable-check --json` and re-enable them with `version --enable-check --json`.

## Google authorization and data

The advisor helps create a Desktop OAuth application in the customer's own Google Cloud project. Only
the customer's accounts, quotas and credentials are used; Anilau OAuth applications, proxies,
servers and quotas are not used. The user supplies only the absolute local path to the downloaded
client JSON. The agent must pass that path directly to the CLI and must never read, print, upload or
ask the user to paste the file. It reuses suitable existing resources, can prepare a project and
enable the three required APIs through an already installed `gcloud` after confirmation, and operates
Cloud Console when browser control is available. Regular Google Auth Platform Desktop clients are
created in Cloud Console; similarly named IAM/Workforce and IAP OAuth commands are not substitutes.
Permission to control the browser is limited to OAuth onboarding and does not authorize later GA4,
GTM, website, publish or deployment changes.

Before login, inspect the exact scopes and their plain-language purposes:

```powershell
.\scripts\google-analytics.ps1 auth consent-preview --json
```

The user grants the complete v1 permission set once. It covers identity, GA4 read/edit, and GTM
read/edit/version/publish. Possessing these scopes never authorizes a change: every later GA4, GTM,
website or publish operation still requires its own plan and confirmation.

Windows uses current-user DPAPI, macOS uses Keychain, and Linux uses Secret Service through
`secret-tool`. There is no plaintext fallback. Access tokens remain in process memory; protected
refresh tokens and imported OAuth clients are stored outside source and plugin caches. Use
`auth forget-local` to remove only the local refresh token or `auth revoke` to revoke the Google grant
and then remove it locally. Both require the exact confirmation shown by `auth status`.

Never paste client secrets, tokens, passwords, private keys, Authorization headers or Analytics
exports into agent chat. See the skill's customer-owned OAuth setup guide for the complete workflow.

Runtime state/cache, future credentials and project data are separate from source. See
[PRIVACY.md](PRIVACY.md) for the current data flow and [SUPPORT.md](SUPPORT.md) for support and release
status.

## License and independence

Use is governed by [LICENSE](LICENSE): one purchasing organization, perpetual internal use and
internal modifications, with twelve months of repository updates unless the purchase agreement says
otherwise. The plugin is an independent Anilau product and is not affiliated with, sponsored by or
endorsed by Google. It provides technical assistance, not legal advice or guaranteed business results.
