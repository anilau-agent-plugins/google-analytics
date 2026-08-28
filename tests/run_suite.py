"""Canonical test entrypoint with production-network egress disabled by default."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
existing_pythonpath = os.environ.get("PYTHONPATH")
os.environ["PYTHONPATH"] = str(TESTS) + (os.pathsep + existing_pythonpath if existing_pythonpath else "")
os.environ["GOOGLE_ANALYTICS_ADVISOR_NETWORK_POLICY"] = "loopback-only"
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from network_guard import install  # noqa: E402 - environment is prepared first

install()


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
