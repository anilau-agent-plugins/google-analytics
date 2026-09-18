# Third-party notices

Google Analytics Advisor includes a compressed snapshot of the first-party Python `tzdata`
package so IANA property timezones work with a clean Python installation on Windows. POSIX systems
continue to prefer their native timezone database.

- Component: `tzdata`
- Version: `2026.2`
- Project: <https://github.com/python/tzdata>
- License: Apache License 2.0
- Bundled data: `scripts/google_analytics_cli/_vendor/tzdata-2026.2.zip`
- License notices: `docs/third-party/tzdata/`

The bundled archive contains only compiled TZif timezone data. It contains no executable code,
credentials, analytics data, or network capability.
