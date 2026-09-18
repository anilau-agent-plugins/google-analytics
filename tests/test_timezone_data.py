from __future__ import annotations

import unittest
import zipfile
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from scripts.google_analytics_cli.timezone_data import (
    BUNDLED_TZDATA,
    BUNDLED_TZDATA_VERSION,
    load_timezone,
)


class TimezoneDataTests(unittest.TestCase):
    def test_bundled_snapshot_is_present_and_versioned(self) -> None:
        self.assertEqual(BUNDLED_TZDATA_VERSION, "2026.2")
        self.assertTrue(BUNDLED_TZDATA.is_file())
        with zipfile.ZipFile(BUNDLED_TZDATA) as archive:
            names = archive.namelist()
            self.assertGreaterEqual(len(names), 500)
            self.assertTrue(all(name.startswith("zoneinfo/") for name in names))
            self.assertFalse(any(name.endswith((".py", ".pyc", ".pyo")) for name in names))
            for name in ("zoneinfo/UTC", "zoneinfo/Asia/Bangkok", "zoneinfo/America/Los_Angeles"):
                self.assertEqual(archive.read(name)[:4], b"TZif")

    def test_bundled_fallback_preserves_dst_rules(self) -> None:
        with patch(
            "scripts.google_analytics_cli.timezone_data._system_zoneinfo",
            side_effect=ZoneInfoNotFoundError("America/Los_Angeles"),
        ):
            zone = load_timezone("America/Los_Angeles")
        winter = datetime(2026, 1, 1, tzinfo=timezone.utc).astimezone(zone)
        summer = datetime(2026, 7, 1, tzinfo=timezone.utc).astimezone(zone)
        self.assertEqual(winter.utcoffset().total_seconds(), -8 * 60 * 60)
        self.assertEqual(summer.utcoffset().total_seconds(), -7 * 60 * 60)

    def test_unknown_zone_still_fails_closed(self) -> None:
        with self.assertRaises(ZoneInfoNotFoundError):
            load_timezone("Etc/Definitely-Not-A-Timezone")

    def test_third_party_license_is_shipped(self) -> None:
        license_root = BUNDLED_TZDATA.parents[3] / "docs" / "third-party" / "tzdata"
        self.assertIn("Apache Software License 2.0", (license_root / "LICENSE").read_text(encoding="utf-8"))
        self.assertIn("Apache License", (license_root / "LICENSE_APACHE").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
