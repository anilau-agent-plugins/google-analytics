from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts.google_analytics_cli.runtime import _listed_py_minors, _windows_alias, classify, doctor


def candidate(**changes):
    value = {
        "implementation": "CPython",
        "versionInfo": [3, 12, 1],
        "architecture": "AMD64",
        "pointerSize": 64,
        "freeThreaded": False,
        "modules": {name: True for name in ("ssl", "json", "urllib", "sqlite3", "venv")},
    }
    value.update(changes)
    return value


class RuntimeTests(unittest.TestCase):
    def test_ready(self) -> None:
        self.assertEqual(classify(candidate()), "ready")

    def test_version_bounds(self) -> None:
        self.assertEqual(classify(candidate(versionInfo=[3, 9, 9])), "too_old")
        self.assertEqual(classify(candidate(versionInfo=[3, 14, 0])), "too_new")

    def test_variants_fail_closed(self) -> None:
        self.assertEqual(classify(candidate(implementation="PyPy")), "unsupported_variant")
        self.assertEqual(classify(candidate(freeThreaded=True)), "unsupported_variant")
        self.assertEqual(classify(candidate(pointerSize=32)), "unsupported_variant")

    def test_windows_alias(self) -> None:
        self.assertTrue(_windows_alias(r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python3.exe"))
        self.assertFalse(_windows_alias(r"C:\Python313\python.exe"))

    def test_py_launcher_lists_supported_runtime(self) -> None:
        completed = type("Completed", (), {"returncode": 0, "stdout": " -V:3.12 * Python\n -V:3.14 Python\n -V:3.10 Python", "stderr": ""})()
        with patch("scripts.google_analytics_cli.runtime.subprocess.run", return_value=completed):
            self.assertEqual(_listed_py_minors("py"), [12, 10])

    def test_doctor_leaves_missing_runtime_paths_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            override = Path(temporary) / "not-created"
            with patch.dict("os.environ", {"GOOGLE_ANALYTICS_ADVISOR_HOME": str(override)}):
                result = doctor()
            self.assertTrue(all(result["writable"].values()))
            self.assertFalse(override.exists())


if __name__ == "__main__":
    unittest.main()
