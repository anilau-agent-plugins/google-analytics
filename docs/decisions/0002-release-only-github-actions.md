# Release-only GitHub Actions for version 0.20.0

Date: 2026-09-18
Owner: Eduard Volkov
Applies to: Google Analytics Advisor 0.20.0 and later until superseded

## Decision

GitHub Actions is a release-only acceptance gate. The repository contains one workflow whose only
trigger is `workflow_dispatch`. Pushes, pull requests, tags, releases, schedules, and other repository
events must not start it.

The workflow may be dispatched only after the owner has explicitly authorized the release operation.
Approval of implementation, tests, a local commit, a push, a tag, or a release plan is not by itself
authorization to consume a GitHub Actions run.

The manual run accepts the exact release version and full commit SHA, checks out that SHA, validates
the metadata, and runs twelve mandatory jobs: CPython 3.10–3.13 on Windows, macOS, and Linux. The
workflow has `contents: read`, receives no Google credentials or repository secrets, performs no
publication, and runs the test suite with production-network access blocked.

## Release order

1. Complete local Windows validation in the canonical repository and installed Codex and Claude
   package copies.
2. Create and inspect the local release commit without running GitHub Actions.
3. Perform read-only remote preflight and obtain explicit release authorization.
4. Push the exact release commit and verify that the push triggered no workflow.
5. Manually dispatch release acceptance for that full SHA and version.
6. Require all twelve jobs to pass before creating the tag or publishing the GitHub Release.

Any failed, cancelled, skipped, or missing matrix job blocks the tag and release. A rerun is a release
action and is not performed silently. The workflow never creates a tag, release, asset, deployment,
or repository write.

## Platform evidence boundary

Windows receives local desktop acceptance, including a temporary DPAPI round trip. GitHub-hosted
Windows, macOS, and Linux runners provide native launcher, runtime, path, encoding, dependency-free
test, and adapter-loading evidence. CI uses only synthetic secrets and fake transports. It does not
claim a live round trip through a real user's macOS login Keychain or Linux desktop Secret Service.

This distinction must remain visible in README and support documentation. Compatibility reports from
real macOS and Linux desktop environments continue to be handled through GitHub Issues without
requesting credentials or customer data.

## Security properties

- Official third-party action tags are resolved and pinned to full commit SHAs.
- The checkout uses the exact supplied commit and does not persist Git credentials.
- Python versions and hosted runner images are explicit, not floating `latest` labels.
- Tests block non-loopback production network access and never receive live Google credentials.
- The workflow has no write permission and contains no publish, deploy, tag, or release command.
- Release metadata, archive hygiene, protected-storage behavior, immutable plan integrity, quotas,
  pagination, and fail-closed provider errors remain required local and matrix tests.

## Superseded decision

This decision replaces the temporary release exception in
`0001-release-validation-without-github-ci.md` for version 0.20.0. Decision 0001 remains in the
repository as the historical record for the already-published 0.11.0 release.
