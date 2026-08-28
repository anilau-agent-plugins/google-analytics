# Release validation without GitHub CI for version 0.11.0

Date: 2026-08-28
Owner: Eduard Volkov
Applies to: Google Analytics Advisor 0.11.0

## Decision

Version 0.11.0 is released without GitHub Actions. The repository contains no active workflow, so
pushes, tags, pull requests, and publication of the release cannot consume GitHub Actions minutes.

The plugin continues to implement Windows, macOS, and Linux adapters and supports CPython 3.10–3.13.
Release acceptance for 0.11.0 is performed locally on Windows with the CPython version available on
the release workstation. Live macOS and Linux validation is not claimed.

## Reason and risk

The owner has deferred paid GitHub CI usage until the next product version. The resulting risk is
that an operating-system- or Python-version-specific compatibility problem may be discovered only
after release by a user of an environment not exercised on the release workstation.

This decision does not relax OAuth, credential storage, mutation confirmation, one-shot delivery,
readback, secret scanning, or production-network isolation requirements.

## Compensating measures

- Run the complete loopback-only test suite and release-hygiene checks locally on Windows.
- Test macOS Keychain, Linux Secret Service, shell launchers, and platform selection through
  dependency-free synthetic unit tests on Windows.
- Validate both plugin manifests and both marketplace manifests locally.
- Build the public ZIP from the exact release commit, scan it, test it from a clean path, and publish
  its SHA-256 checksum.
- Document the validation boundary in README and SUPPORT and handle macOS/Linux feedback as it is
  received.

## Review date

Revisit and replace this exception before releasing version 0.20.0. That release is expected to
restore a full Windows/macOS/Linux and CPython 3.10–3.13 CI matrix before publication.
