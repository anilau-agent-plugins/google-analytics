from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.secret_store import LinuxSecretServiceStore, WindowsDpapiStore


class SecretStoreTests(unittest.TestCase):
    @unittest.skipUnless(__import__("os").name == "nt", "DPAPI is Windows-only")
    def test_dpapi_round_trip_tamper_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = WindowsDpapiStore(Path(temp))
            store.put("profile:test", b"secret-value")
            self.assertEqual(store.get("profile:test"), b"secret-value")
            path = next((Path(temp) / "secrets").glob("*.dpapi"))
            damaged = bytearray(path.read_bytes())
            damaged[-1] ^= 1
            path.write_bytes(damaged)
            with self.assertRaises(AdvisorError) as caught:
                store.get("profile:test")
            self.assertEqual(caught.exception.code, "SECRET_STORE_CORRUPT")
            self.assertTrue(store.delete("profile:test"))
            self.assertFalse(store.delete("profile:test"))

    def test_linux_secret_passes_only_through_stdin(self) -> None:
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs.get("input")))
            if "lookup" in args:
                return subprocess.CompletedProcess(args, 0, stdout=b"c2VjcmV0\n", stderr=b"")
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        with patch("scripts.google_analytics_cli.secret_store.shutil.which", return_value="/usr/bin/secret-tool"):
            store = LinuxSecretServiceStore(runner=runner)
            store.put("key-1", b"secret")
            self.assertEqual(store.get("key-1"), b"secret")
        self.assertNotIn("c2VjcmV0", calls[0][0])
        self.assertEqual(calls[0][1], b"c2VjcmV0")


if __name__ == "__main__":
    unittest.main()
