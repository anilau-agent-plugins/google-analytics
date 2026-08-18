# Support

Google Analytics Advisor is free, open-source software. Before asking for help, install the latest
[GitHub Release](https://github.com/anilau-agent-plugins/google-analytics/releases/latest) and ask
Codex or Claude Code to run the plugin's installation check.

## Questions and bug reports

- Use [GitHub Issues](https://github.com/anilau-agent-plugins/google-analytics/issues) for bugs,
  installation problems, and feature requests that contain no private information.
- Email [admin@anilau.com](mailto:admin@anilau.com) for a security concern or a question that cannot
  be discussed publicly.

Include the plugin version, operating system, Python version, agent platform, command name, and
redacted error code. Never publish OAuth client files, tokens, passwords, private keys,
Authorization headers, customer Analytics exports, production datasets, or source code containing
customer data.

The plugin is provided under the MIT License. Community support has no guaranteed response time.
Paid implementation or consulting can be discussed separately through
[anilau.com](https://anilau.com/en/agent-plugins/).

## Safe diagnostics

For authorization failures, share only the redacted error code, command name, and probe status. For
GA4 changes, include only the operation kind, plan ID, journal status, and whether readback
completed. For local website installation, include only the plan ID, relative target path,
expected/observed hash, and journal status. Never repeat an ambiguous write or production event.

Google Cloud CLI is optional. Browser-assisted onboarding requires explicit permission and a browser
already signed in to the intended Google account. The plugin never needs a password, recovery code,
or two-factor authentication code.
