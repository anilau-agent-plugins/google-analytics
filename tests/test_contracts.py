from __future__ import annotations

import unittest
from pathlib import Path

from scripts.google_analytics_cli.contracts import ARTIFACTS, validate_artifact
from scripts.google_analytics_cli.errors import AdvisorError


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "contracts" / "fixtures"


class ContractTests(unittest.TestCase):
    def test_all_valid_fixtures(self) -> None:
        for name in sorted(ARTIFACTS):
            with self.subTest(name=name):
                result = validate_artifact(name, FIXTURES / "valid" / f"{name}.json")
                self.assertTrue(result["valid"])

    def test_all_invalid_fixtures(self) -> None:
        for name in sorted(ARTIFACTS):
            with self.subTest(name=name):
                with self.assertRaises(AdvisorError) as caught:
                    validate_artifact(name, FIXTURES / "invalid" / f"{name}.json")
                self.assertEqual(caught.exception.exit_code, 4)


if __name__ == "__main__":
    unittest.main()
