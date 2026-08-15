from __future__ import annotations

import unittest
from pathlib import Path

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.paths import runtime_paths


class PathTests(unittest.TestCase):
    def test_absolute_override(self) -> None:
        result = runtime_paths(env={"GOOGLE_ANALYTICS_ADVISOR_HOME": "C:\\runtime"}, system="Windows")
        self.assertEqual(result["state"], Path("C:/runtime/state"))

    def test_relative_override_rejected(self) -> None:
        with self.assertRaises(AdvisorError):
            runtime_paths(env={"GOOGLE_ANALYTICS_ADVISOR_HOME": "relative"}, system="Linux")

    def test_linux_defaults(self) -> None:
        result = runtime_paths(env={}, system="Linux", home=Path("/home/test"))
        self.assertEqual(result["state"], Path("/home/test/.local/state/anilau/google-analytics-advisor"))


if __name__ == "__main__":
    unittest.main()
