from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def test_powershell_bootstrap(self) -> None:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-File", str(ROOT / "scripts" / "google-analytics.ps1"), "version", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["cliVersion"], "0.6.0")

    def test_launchers_do_not_install(self) -> None:
        text = ((ROOT / "scripts" / "google-analytics.ps1").read_text(encoding="utf-8") +
                (ROOT / "scripts" / "google-analytics.sh").read_text(encoding="utf-8")).lower()
        for forbidden in ("winget install", "brew install", "apt install", "pip install", "invoke-webrequest"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
