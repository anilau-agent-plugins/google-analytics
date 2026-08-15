from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.site_scanner import inspect_site


class SiteScannerTests(unittest.TestCase):
    def test_detects_runtime_tags_consent_and_probable_duplicate_without_raw_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "index.html").write_text(
                "<script src='https://www.googletagmanager.com/gtag/js?id=G-ABC123'></script>\n"
                "<script>gtag('consent','default',{'analytics_storage':'denied'});</script>\n"
                "<script src='https://www.googletagmanager.com/gtm.js?id=GTM-XYZ99'></script>\n",
                encoding="utf-8",
            )
            (root / ".env").write_text("MEASUREMENT_PROTOCOL_SECRET=never-read", encoding="utf-8")
            (root / "oauth-token.json").write_text("G-SECRET999", encoding="utf-8")
            (root / "docs").mkdir()
            (root / "docs" / "example.md").write_text("G-EXAMPLE1", encoding="utf-8")
            (root / "generated.min.js").write_text("G-MINIFIED1", encoding="utf-8")
            (root / "fragment.php").write_text("$value = 'G-I';", encoding="utf-8")
            (root / "storage" / "framework" / "views").mkdir(parents=True)
            (root / "storage" / "framework" / "views" / "compiled.php").write_text("G-COMPILED1", encoding="utf-8")
            result = inspect_site(root.resolve())
        self.assertEqual(result["publicIds"], ["G-ABC123", "GTM-XYZ99"])
        self.assertIn("POSSIBLE_DOUBLE_COLLECTION", {item["code"] for item in result["findings"]})
        rendered = repr(result)
        self.assertNotIn("never-read", rendered)
        self.assertNotIn("G-SECRET999", rendered)
        self.assertNotIn("G-I", result["publicIds"])
        self.assertNotIn("<script", rendered)
        docs = [item for item in result["evidence"] if item["path"].startswith("docs/")]
        self.assertTrue(docs and all(item["classification"] == "non-runtime" for item in docs))
        generated = [item for item in result["evidence"] if item["publicId"] in {"G-MINIFIED1", "G-COMPILED1"}]
        self.assertTrue(generated and all(item["classification"] == "non-runtime" for item in generated))

    def test_requires_absolute_existing_root(self) -> None:
        with self.assertRaises(AdvisorError) as caught:
            inspect_site(Path("relative"))
        self.assertEqual(caught.exception.code, "SITE_SCAN_ROOT_INVALID")


if __name__ == "__main__":
    unittest.main()
