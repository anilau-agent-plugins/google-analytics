# Google Analytics Advisor

[![Version 0.8.0](https://img.shields.io/badge/version-0.8.0-2563eb)](https://github.com/anilau-agent-plugins/google-analytics/releases/latest)
[![MIT License](https://img.shields.io/badge/license-MIT-16a34a)](LICENSE)
[![Codex and Claude Code](https://img.shields.io/badge/works_with-Codex%20%7C%20Claude%20Code-7c3aed)](#installation-instructions)

Google Analytics Advisor is a free plugin that helps you understand and improve Google Analytics 4
without becoming an analytics specialist. Talk to it in your own language. It explains what it finds
in plain words, prepares a safe plan, and asks before making any important change.

It works with **Codex** and **Claude Code** on Windows, macOS, and Linux.

## What it helps you do

- understand what your website visitors do and which actions matter to the business;
- check accessible GA4 properties, website streams, and Google Tag Manager containers;
- find missing, duplicated, or conflicting analytics code in a local website project;
- design useful events, key events, ecommerce tracking, funnels, and consent handling;
- safely configure supported GA4 settings after showing you an exact change plan;
- safely add Google tag or Google Tag Manager code to supported website projects;
- check Measurement Protocol events before a separately confirmed production send;
- create and protect the Google authorization needed for this work.

The plugin currently understands static HTML, Laravel Blade, React/Vite, and Next.js App Router
projects. It does not publish Google Tag Manager containers, deploy websites, manage Analytics users,
or promise business results.

## Installation instructions

You do not need to install or configure Python yourself. The plugin checks what is already on the
computer and helps install a suitable version when needed. It uses:

- Codex or Claude Code;
- Python 3.10, 3.11, 3.12, or 3.13;
- a Google account that can access the Analytics resources you want to inspect.

To install:

1. Open the [latest release](https://github.com/anilau-agent-plugins/google-analytics/releases/latest).
2. Download the file named `google-analytics-0.8.0.zip` and unpack it.
3. In Codex or Claude Code, point to the unpacked folder and say: **“Install this plugin.”**
4. Start a new task or conversation and say: **“Check my Google Analytics setup.”**

The agent completes safe local setup itself. It pauses only when you need to sign in, complete 2FA,
approve Google permissions, choose between genuinely different options, or confirm a change. It never
needs you to paste a password, token, or downloaded Google OAuth file into chat.

If your application cannot install directly from a folder, ask it to follow the manual instructions
below.

<details>
<summary>Manual installation for Codex and Claude Code</summary>

### Codex

Add the unpacked plugin folder as a local source in your personal marketplace, then run:

```text
codex plugin add google-analytics@personal
```

Start a new Codex task after installation.

### Claude Code

From a local marketplace containing this repository, run:

```text
claude plugin validate ./google-analytics --strict
claude plugin marketplace add <absolute-marketplace-directory>
claude plugin install google-analytics@anilau-agent-plugins-local
```

Run `/reload-plugins` or restart Claude Code when prompted. The skill name is
`google-analytics:google-analytics`.

</details>

## How updates work

The safe update source is the [GitHub Releases page](https://github.com/anilau-agent-plugins/google-analytics/releases).
The release version and files are public, so no GitHub token is required.

To update:

1. Download and unpack the newest release.
2. Point Codex or Claude Code to that folder and say: **“Update my Google Analytics plugin from this
   folder.”**
3. Start a new task or reload plugins when asked.

An update replaces the installed plugin copy. It does not delete Google credentials or project
reports because those are stored outside the plugin folder. Automatic updates are not enabled in
version 0.8.0; this prevents an unverified file from silently changing installed code. The plugin can
perform a telemetry-free version check when a trusted signed update manifest is configured, but it
still asks before installation.

## Your data and credentials

Google Analytics Advisor runs on your computer. It sends no telemetry, prompts, credentials, project
files, or Analytics data to Anilau. Google authorization and Analytics requests go directly from your
computer to Google.

OAuth clients and refresh tokens are stored in the operating system's protected credential storage:
Windows DPAPI, macOS Keychain, or Linux Secret Service. There is no plaintext fallback. Local plans,
reports, and change journals are stored in `.google-analytics-advisor/` inside the project you select.
See [PRIVACY.md](PRIVACY.md) for the complete data flow.

## Safe changes by design

Reading data does not authorize a change. Before supported GA4 or website changes, the plugin shows
an immutable plan with the exact target, intended result, risks, expiry time, and SHA-256 fingerprint.
Nothing changes until you confirm that exact fingerprint. Afterward, the plugin reads the result back
and reports `applied`, `partial`, `ambiguous`, or `failed` exactly.

It never treats a timeout as success, never automatically repeats an uncertain write or production
event, and never deploys a website as part of local installation.

## Try these requests

```text
Explain what my Google Analytics setup measures today.
Which conversions should this website track?
Check this local website for duplicate Google tags.
Prepare a safe plan to install analytics on this project.
Show me what would change before configuring GA4.
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

Google Analytics Advisor is the free, open-source demonstration plugin from
[Anilau Agent Plugins](https://github.com/anilau-agent-plugins). Anilau also develops commercial
plugins for Google Ads, Yandex Direct, and YouGile. See the
[English catalog](https://anilau.com/en/agent-plugins/) or
[Russian catalog](https://anilau.com/ru/agent-plugins/) for current availability.

## License and independence

The source code is available under the [MIT License](LICENSE). You may use, modify, and distribute it
under the license terms.

Google Analytics Advisor is an independent Anilau product. It is not affiliated with, sponsored by,
or endorsed by Google. It provides technical assistance, not legal advice or guaranteed analytics or
business results.
