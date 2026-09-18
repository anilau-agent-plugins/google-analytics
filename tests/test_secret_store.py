from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.secret_store import (
    LinuxSecretServiceStore,
    MAX_SECRET_BYTES,
    MacKeychainStore,
    WindowsDpapiStore,
    secret_store,
)


class SecretStoreTests(unittest.TestCase):
    def test_platform_selector_has_no_plaintext_fallback(self) -> None:
        windows = object()
        mac = object()
        linux = object()
        with (
            patch("scripts.google_analytics_cli.secret_store.WindowsDpapiStore", return_value=windows),
            patch("scripts.google_analytics_cli.secret_store.MacKeychainStore", return_value=mac),
            patch("scripts.google_analytics_cli.secret_store.LinuxSecretServiceStore", return_value=linux),
        ):
            self.assertIs(secret_store(system="Windows", env={"GOOGLE_ANALYTICS_ADVISOR_HOME": "C:\\runtime"}), windows)
            self.assertIs(secret_store(system="Darwin"), mac)
            self.assertIs(secret_store(system="Linux"), linux)
            with self.assertRaises(AdvisorError) as caught:
                secret_store(system="FreeBSD")
            self.assertEqual(caught.exception.code, "SECRET_STORE_UNAVAILABLE")

    @unittest.skipUnless(sys.platform == "darwin", "macOS framework loading is macOS-only")
    def test_macos_keychain_frameworks_load_without_writing(self) -> None:
        self.assertIsInstance(MacKeychainStore(), MacKeychainStore)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux fail-closed check is Linux-only")
    def test_linux_missing_secret_tool_fails_closed(self) -> None:
        with patch("scripts.google_analytics_cli.secret_store.shutil.which", return_value=None):
            with self.assertRaises(AdvisorError) as caught:
                LinuxSecretServiceStore()
        self.assertEqual(caught.exception.code, "SECRET_STORE_UNAVAILABLE")

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

    def test_all_secret_backends_reject_empty_and_oversized_values_before_write(self) -> None:
        calls = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

        with patch("scripts.google_analytics_cli.secret_store.shutil.which", return_value="/usr/bin/secret-tool"):
            store = LinuxSecretServiceStore(runner=runner)
            for value in (b"", b"x" * (MAX_SECRET_BYTES + 1)):
                with self.subTest(size=len(value)), self.assertRaises(AdvisorError) as caught:
                    store.put("key-1", value)
                self.assertEqual(caught.exception.code, "SECRET_VALUE_INVALID")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
