from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.website_patch import parse_patch, simulate_patch


class WebsitePatchTests(unittest.TestCase):
    def test_exact_patch_preserves_crlf_and_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            path = root / "index.html"
            path.write_bytes(b"\xef\xbb\xbf<head>\r\n<title>Old</title>\r\n</head>\r\n")
            patch = b"--- a/index.html\n+++ b/index.html\n@@ -1,3 +1,4 @@\n <head>\n+<meta name=\"fixture\">\n <title>Old</title>\n </head>\n"
            result = simulate_patch(root, patch)[0]
            self.assertEqual(result["encoding"], "utf-8-bom")
            self.assertEqual(result["newline"], "CRLF")
            self.assertIn(b"\r\n<meta", result["after"])

    def test_create_and_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            created = simulate_patch(root, b"--- /dev/null\n+++ b/analytics.js\n@@ -0,0 +1 @@\n+export const ready = true;\n")[0]
            self.assertTrue(created["create"])
            with self.assertRaises(AdvisorError):
                parse_patch(b"--- a/../outside.js\n+++ b/../outside.js\n@@ -1 +1 @@\n-a\n+b\n")

    def test_blocks_delete_secret_pii_and_case_collision(self) -> None:
        cases = [
            b"--- a/a.js\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n",
            b"--- /dev/null\n+++ b/a.js\n@@ -0,0 +1 @@\n+const api_secret='very-secret-value';\n",
            b"--- /dev/null\n+++ b/a.js\n@@ -0,0 +1 @@\n+const x='person@example.com';\n",
            b"--- /dev/null\n+++ b/A.js\n@@ -0,0 +1 @@\n+a\n--- /dev/null\n+++ b/a.js\n@@ -0,0 +1 @@\n+b\n",
        ]
        for patch in cases:
            with self.subTest(patch=patch[:20]), self.assertRaises(AdvisorError):
                parse_patch(patch)


if __name__ == "__main__":
    unittest.main()
