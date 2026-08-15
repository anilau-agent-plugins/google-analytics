from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.google_analytics_cli.version_check import check_version, set_disabled


class Response:
    status = 200
    request_id = None
    headers = {}

    def __init__(self, data):
        self.data = data


class Transport:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error
        self.calls = 0

    def request(self, *args, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return Response(self.data)


class VersionCheckTests(unittest.TestCase):
    def environment(self, root: Path) -> dict[str, str]:
        return {"GOOGLE_ANALYTICS_ADVISOR_HOME": str(root)}

    def test_update_available_and_thirty_day_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = self.environment(Path(temp))
            transport = Transport({"product": "google-analytics", "latestVersion": "0.6.0", "releaseUrl": "https://anilau.com"})
            now = datetime(2026, 8, 10, tzinfo=timezone.utc)
            first = check_version(endpoint="https://updates.test/version.json", env=env, now=now,
                                  transport=transport, trusted_hosts={"updates.test"})
            second = check_version(endpoint="https://updates.test/version.json", env=env, now=now,
                                   transport=transport, trusted_hosts={"updates.test"})
            self.assertEqual(first["status"], "update_available")
            self.assertTrue(second["cached"])
            self.assertEqual(transport.calls, 1)

    def test_current_offline_malformed_and_untrusted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = self.environment(Path(temp))
            current = check_version(endpoint="https://updates.test/v", env=env, force=True,
                                    transport=Transport({"product": "google-analytics", "latestVersion": "0.3.0"}),
                                    trusted_hosts={"updates.test"})
            malformed = check_version(endpoint="https://updates.test/v", env=env, force=True,
                                      transport=Transport({"latestVersion": "next"}), trusted_hosts={"updates.test"})
            offline = check_version(endpoint="https://updates.test/v", env=env, force=True,
                                    transport=Transport(error=OSError("offline")), trusted_hosts={"updates.test"})
            untrusted = check_version(endpoint="http://updates.test/v", env=env)
            self.assertEqual(current["status"], "up_to_date")
            self.assertEqual(malformed["status"], "unavailable")
            self.assertEqual(offline["status"], "unavailable")
            self.assertEqual(untrusted["status"], "untrusted_endpoint")

    def test_can_disable_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = self.environment(Path(temp))
            set_disabled(True, env=env)
            result = check_version(endpoint="https://anilau.com/version.json", env=env)
            self.assertEqual(result["status"], "disabled")


if __name__ == "__main__":
    unittest.main()
