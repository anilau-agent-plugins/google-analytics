# Google Analytics Advisor

[![Version 0.11.0](https://img.shields.io/badge/version-0.11.0-2563eb)](https://github.com/anilau-agent-plugins/google-analytics/releases/latest)
[![MIT License](https://img.shields.io/badge/license-MIT-16a34a)](LICENSE)
[![Codex and Claude Code](https://img.shields.io/badge/works_with-Codex%20%7C%20Claude%20Code-7c3aed)](#how-to-install)

Google Analytics Advisor is a free plugin that helps you understand and improve Google Analytics 4
without becoming an analytics specialist. Talk to it in your own language. It explains what it finds
in plain words, prepares a safe plan, and asks before making any important change.

It works with **Codex** and **Claude Code** on Windows, macOS, and Linux. Version 0.11.0 received its
complete release acceptance on Windows. The macOS and Linux implementations are included, but were
not live-tested for this release; compatibility feedback is handled through GitHub Issues.

## What it helps you do

- understand what your website visitors do and which actions matter to the business;
- receive bounded overview, acquisition, content, audience, event, key-event, ecommerce, realtime,
  and approved funnel reports with evidence and data-quality warnings;
- compare complete periods and get up to five prioritized recommendations in everyday language;
- check accessible GA4 properties, website streams, and Google Tag Manager containers;
- connect read-only Google Search Console access and list exact URL-prefix/Domain properties and
  permission levels without changing them;
- in the current `0.20.0` development build, run bounded Search Console overview, query, page,
  device, country, search-appearance, and recent-hourly reports with completeness warnings;
- inspect Search Console sitemap metadata and Google's indexed evidence for a small, explicitly
  selected URL sample without downloading sitemap XML or requesting indexing;
- locally compare immutable GA4 `google / organic` and Search Console reports for the same complete
  periods, with exact-only page mapping and explicit unmatched, query-ambiguous, and timezone limits;
- find missing, duplicated, or conflicting analytics code in a local website project;
- design useful events, key events, ecommerce tracking, funnels, and consent handling;
- safely configure supported GA4 settings after showing you an exact change plan;
- safely add Google tag or Google Tag Manager code to supported website projects;
- safely create an isolated GTM workspace, configure supported tags/triggers/variables, compile a
  preview, create a version, and separately publish that exact version;
- check Measurement Protocol events before a separately confirmed production send;
- create and protect the Google authorization needed for this work.

The plugin currently understands static HTML, Laravel Blade, React/Vite, and Next.js App Router
projects. It does not deploy websites, manage Analytics/GTM users, delete containers, accept
arbitrary Custom HTML, or promise business results.

## What the plugin uses

You do not need to install or configure Python yourself. The plugin checks what is already on the
computer and helps install a suitable version when needed.

- Codex or Claude Code;
- Python 3.10, 3.11, 3.12, or 3.13;
- a Google account that can access the Analytics resources you want to inspect and, when Search
  Console discovery or reporting is requested, the relevant Search Console properties.

## How to install

The simplest managed installation is to ask the agent:

> Install Google Analytics Advisor from
> https://github.com/anilau-agent-plugins/google-analytics using its public marketplace. Verify the
> installation and start a new task when required.

The agent should explain the source, run the matching commands below, verify the installed version,
and leave Google sign-in and consent to you.

<details>
<summary>Manual marketplace commands</summary>

### Codex

```text
codex plugin marketplace add anilau-agent-plugins/google-analytics --ref main
codex plugin add google-analytics@anilau-google-analytics
```

Start a new Codex task after installation.

### Claude Code

```text
claude plugin marketplace add anilau-agent-plugins/google-analytics
claude plugin install google-analytics@anilau-google-analytics
```

Run `/reload-plugins` or restart Claude Code. The skill name is
`google-analytics:google-analytics`.

</details>

### Release ZIP fallback

1. Open the [latest release](https://github.com/anilau-agent-plugins/google-analytics/releases/latest).
2. Download the file named `google-analytics-0.11.0.zip` and unpack it.
3. In Codex or Claude Code, point to the unpacked folder and say: **“Install this plugin.”**
4. Start a new task or conversation and say: **“Check my Google Analytics setup.”**

The agent completes safe local setup itself. It pauses only when you need to sign in, complete 2FA,
approve Google permissions, choose between genuinely different options, or confirm a change. It never
needs you to paste a password, token, or downloaded Google OAuth file into chat.

### Verify the installation

From an unpacked release or plugin directory, run the command for the current operating system:

```powershell
powershell -NoProfile -File .\scripts\google-analytics.ps1 doctor --json
```

```sh
sh ./scripts/google-analytics.sh doctor --json
```

The result should identify version `0.11.0`, a supported Python runtime, writable protected-data
locations, and available TLS support. `doctor` does not access an Analytics property or change
Google, GTM, or website resources.

## How updates work

The public Git repository and its release tags are the update source. No GitHub token is required.

Codex:

```text
codex plugin marketplace upgrade anilau-google-analytics
codex plugin add google-analytics@anilau-google-analytics
```

Claude Code:

```text
claude plugin marketplace update anilau-google-analytics
claude plugin update google-analytics@anilau-google-analytics
```

Start a new Codex task or restart/reload Claude Code after the update. If you installed from a ZIP,
download the newest [GitHub Release](https://github.com/anilau-agent-plugins/google-analytics/releases)
and ask the agent to update from that folder.

An update replaces the installed plugin copy. It does not delete Google credentials or project
reports because those are stored outside the plugin folder. Automatic updates are not enabled in
version 0.11.0; this prevents an unverified file from silently changing installed code. The plugin can
perform a telemetry-free version check when a trusted signed update manifest is configured, but it
still asks before installation.

## Uninstall and rollback

Removing the plugin does not remove Google credentials or project reports.

Codex:

```text
codex plugin remove google-analytics@anilau-google-analytics
codex plugin marketplace remove anilau-google-analytics
```

Claude Code:

```text
claude plugin uninstall google-analytics@anilau-google-analytics
claude plugin marketplace remove anilau-google-analytics
```

Removing the marketplace is optional when you expect to reinstall later. To roll back, download a
previous release ZIP, verify its published SHA-256, and ask the agent to install that exact version.
Never downgrade or delete `.google-analytics-advisor/` artifacts automatically.

Credential deletion is a separate operation. First inspect the exact profile with `auth status`,
then use its displayed confirmation with `auth forget-local` or `auth revoke`. Delete a selected
project's `.google-analytics-advisor/` directory only when you also intend to remove its local plans,
reports, snapshots, and journals.

## Your data and credentials

Google Analytics Advisor runs on your computer. It sends no telemetry, prompts, credentials, project
files, Analytics data, or Search Console data to Anilau. Google authorization, Analytics requests,
and read-only Search Console discovery, performance, sitemap, and URL Inspection requests go directly
from your computer to Google. Cross-source analysis uses only already-created local reports and makes
no network request.

OAuth clients and refresh tokens are stored in the operating system's protected credential storage:
Windows DPAPI, macOS Keychain, or Linux Secret Service. There is no plaintext fallback. Local plans,
reports, and change journals are stored in `.google-analytics-advisor/` inside the project you select.
See [PRIVACY.md](PRIVACY.md) for the complete data flow.

## Safe changes by design

Reading data does not authorize a change. Before supported GA4, website, or GTM changes, the plugin shows
an immutable plan with the exact target, intended result, risks, expiry time, and SHA-256 fingerprint.
Nothing changes until you confirm that exact fingerprint. Afterward, the plugin reads the result back
and reports `applied`, `partial`, `ambiguous`, or `failed` exactly.

GTM workspace creation, sync, entity changes, compiler preview, version creation, and publish are six
separate plans and confirmations. The plugin never treats a timeout as success, never automatically
repeats an uncertain write or production event, never resolves GTM conflicts automatically, never
publishes automatically, and never deploys a website as part of local installation.

The published validation entrypoint runs with non-loopback network access disabled. Security tests
use synthetic data and fake transports, so validation cannot change GA4/GTM resources or send
production Measurement Protocol events.

## Try these requests

```text
Explain what my Google Analytics setup measures today.
Explain what changed in the last 28 complete days and whether the data is reliable.
Show which Search Console properties this Google account can read.
Explain how my site performed in Google Search over the last 28 finalized days.
Show which sitemaps Google knows and prepare a safe check of three important URLs.
Compare Google Search visibility with what visitors did after landing, without treating clicks and sessions as the same metric.
Show acquisition and key-event performance in plain language.
Which conversions should this website track?
Check this local website for duplicate Google tags.
Prepare a safe plan to install analytics on this project.
Show me what would change before configuring GA4.
Prepare an isolated GTM workspace and show me every change before applying it.
```

Ask in any language. The plugin should answer in the language you use, even though published product
documentation and source files are written in English.

## Help, security, and contributions

- Read [SUPPORT.md](SUPPORT.md) before sharing diagnostic information.
- Report security concerns privately as described in [SECURITY.md](SECURITY.md).
- Bug reports and improvements are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).
- Release history is in [CHANGELOG.md](CHANGELOG.md).

Never put OAuth files, tokens, passwords, private keys, Authorization headers, customer Analytics
exports, production datasets, or private website source into a public GitHub issue.

## More Anilau plugins

Google Analytics Advisor is one of the free, open-source demonstration plugins from
[Anilau Agent Plugins](https://github.com/anilau-agent-plugins), alongside
[Yandex Metrica Advisor](https://github.com/anilau-agent-plugins/yandex-metrika). If your next step
requires auditing or managing the advertising account itself, see the commercial
[Google Ads plugin for Codex and Claude Code](https://anilau.com/en/agent-plugins/google-ads/).
Anilau also develops commercial plugins for Yandex Direct and YouGile. See the
[English catalog](https://anilau.com/en/agent-plugins/) or
[Russian catalog](https://anilau.com/ru/agent-plugins/) for current availability.

## License and independence

The source code is available under the [MIT License](LICENSE). You may use, modify, and distribute it
under the license terms.

Google Analytics Advisor is an independent Anilau product. It is not affiliated with, sponsored by,
or endorsed by Google. It provides technical assistance, not legal advice or guaranteed analytics or
business results.
