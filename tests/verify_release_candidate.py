"""Fail-closed exact revision and version checks for a release-only CI run."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _read_json(relative: str) -> dict[str, object]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def metadata_versions() -> dict[str, str]:
    codex = _read_json(".codex-plugin/plugin.json")
    claude = _read_json(".claude-plugin/plugin.json")
    marketplace = _read_json(".claude-plugin/marketplace.json")
    package_text = (ROOT / "scripts" / "google_analytics_cli" / "__init__.py").read_text(encoding="utf-8")
    package_match = re.search(r'^__version__ = "([^"]+)"$', package_text, re.MULTILINE)
    if package_match is None:
        raise ValueError("Python package version is missing")
    return {
        "codex": str(codex.get("version", "")),
        "claude": str(claude.get("version", "")),
        "claudeMarketplace": str(marketplace["plugins"][0].get("version", "")),  # type: ignore[index]
        "python": package_match.group(1),
    }


def verify_version(expected: str) -> None:
    if SEMVER.fullmatch(expected) is None:
        raise ValueError("RELEASE_VERSION must be an exact stable SemVer value")
    versions = metadata_versions()
    mismatches = {name: value for name, value in versions.items() if value != expected}
    if mismatches:
        raise ValueError(f"Release metadata version mismatch: {mismatches}")

    required_text = {
        "scripts/google-analytics.ps1": f'\"cliVersion\":\"{expected}\"',
        "scripts/google-analytics.sh": f'\"cliVersion\":\"{expected}\"',
        "README.md": f"google-analytics-{expected}.zip",
        "PRIVACY.md": f"Google Analytics Advisor {expected}",
        "CHANGELOG.md": f"## {expected} -",
    }
    for relative, marker in required_text.items():
        if marker not in (ROOT / relative).read_text(encoding="utf-8"):
            raise ValueError(f"{relative} does not contain release marker {marker!r}")

    codex = _read_json(".codex-plugin/plugin.json")
    expected_tag = f"/blob/v{expected}/"
    for field in ("privacyPolicyURL", "termsOfServiceURL"):
        value = str(codex.get("interface", {}).get(field, ""))  # type: ignore[union-attr]
        if expected_tag not in value:
            raise ValueError(f"Codex manifest {field} is not pinned to v{expected}")


def verify_commit(expected: str) -> None:
    expected = expected.lower()
    if FULL_SHA.fullmatch(expected) is None:
        raise ValueError("RELEASE_COMMIT must be a full 40-character lowercase commit SHA")
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", check=True,
    )
    actual = result.stdout.strip().lower()
    if actual != expected:
        raise ValueError(f"Checked-out commit {actual} does not match RELEASE_COMMIT {expected}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=os.environ.get("RELEASE_VERSION"))
    parser.add_argument("--commit", default=os.environ.get("RELEASE_COMMIT"))
    args = parser.parse_args(argv)
    if not args.version or not args.commit:
        parser.error("release version and full commit SHA are required")
    try:
        verify_version(args.version)
        verify_commit(args.commit)
    except (KeyError, OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"release candidate verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "version": args.version, "commit": args.commit}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
