"""Safe CPython discovery and runtime diagnostics."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .errors import AdvisorError, EXIT_RUNTIME
from .paths import runtime_paths, source_root


MIN_VERSION = (3, 10, 0)
MAX_VERSION = (3, 14, 0)
SUPPORTED_ARCHES = {"amd64", "x86_64", "arm64", "aarch64"}
PROBE = r'''import importlib.util,json,platform,struct,sys
mods={m:importlib.util.find_spec(m) is not None for m in ("ssl","json","urllib","sqlite3","venv")}
print(json.dumps({"implementation":platform.python_implementation(),"version":platform.python_version(),"versionInfo":list(sys.version_info[:3]),"executable":sys.executable,"resolvedExecutable":str(__import__("pathlib").Path(sys.executable).resolve()),"architecture":platform.machine(),"pointerSize":struct.calcsize("P")*8,"platform":sys.platform,"freeThreaded":bool(getattr(sys,"_is_gil_enabled",lambda:True)() is False),"utf8Mode":bool(sys.flags.utf8_mode),"modules":mods}))'''


def _windows_alias(path: str | None) -> bool:
    return bool(path and "windowsapps" in path.replace("/", "\\").lower().split("\\"))


def _listed_py_minors(executable: str, timeout: float = 5.0) -> list[int]:
    try:
        result = subprocess.run(
            [executable, "--list"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    found = {int(value) for value in re.findall(r"-V:3\.(\d+)", result.stdout + result.stderr)}
    return sorted((minor for minor in found if 10 <= minor <= 13), reverse=True)


def _probe(command: list[str], provider: str, timeout: float = 5.0) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command + ["-I", "-X", "utf8", "-c", PROBE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"provider": provider, "status": "broken", "reason": type(exc).__name__}
    if result.returncode != 0:
        return {"provider": provider, "status": "broken", "reason": "probe_failed"}
    try:
        data = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"provider": provider, "status": "broken", "reason": "invalid_probe_json"}
    data["provider"] = provider
    data["status"] = classify(data)
    return data


def classify(data: dict[str, Any]) -> str:
    version = tuple(data.get("versionInfo", []))
    architecture = str(data.get("architecture", "")).lower()
    if data.get("implementation") != "CPython":
        return "unsupported_variant"
    if data.get("freeThreaded"):
        return "unsupported_variant"
    if data.get("pointerSize") != 64 or architecture not in SUPPORTED_ARCHES:
        return "unsupported_variant"
    if version < MIN_VERSION:
        return "too_old"
    if version >= MAX_VERSION:
        return "too_new"
    if not all(data.get("modules", {}).values()):
        return "broken"
    return "ready"


def discover() -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    commands: list[tuple[str, list[str]]] = []
    if os.name == "nt":
        py = shutil.which("py")
        if py and not _windows_alias(py):
            for minor in _listed_py_minors(py):
                commands.append((f"py -3.{minor}", [py, f"-3.{minor}"]))
        for name in ("python3", "python"):
            resolved = shutil.which(name)
            if resolved and _windows_alias(resolved):
                candidates.append({"provider": name, "executable": resolved, "status": "app_execution_alias"})
            elif resolved:
                commands.append((name, [resolved]))
    else:
        for name in ("python3", "python", "py"):
            resolved = shutil.which(name)
            if resolved:
                commands.append((name, [resolved]))
    seen: set[str] = set()
    for provider, command in commands:
        candidate = _probe(command, provider)
        identity = str(candidate.get("resolvedExecutable", command[0])).lower()
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(candidate)
    selected = next((item for item in candidates if item.get("status") == "ready"), None)
    return {"selected": selected, "candidates": candidates}


def installation_guide(discovery: dict[str, Any] | None = None) -> dict[str, Any]:
    found = discovery or discover()
    selected = found.get("selected")
    statuses = {str(item.get("status")) for item in found.get("candidates", [])}
    reason = "ready" if selected else (
        "too_old" if "too_old" in statuses else "too_new" if "too_new" in statuses else
        "unsupported_variant" if "unsupported_variant" in statuses else "broken" if "broken" in statuses else "missing"
    )
    system = platform.system()
    if system == "Windows":
        url = "https://www.python.org/downloads/windows/"
        package_manager = "winget" if shutil.which("winget") else None
    elif system == "Darwin":
        url = "https://www.python.org/downloads/macos/"
        package_manager = "brew" if shutil.which("brew") else None
    else:
        url = "https://www.python.org/downloads/source/"
        package_manager = next((name for name in ("apt", "dnf", "yum", "zypper", "pacman", "apk") if shutil.which(name)), None)
    if selected:
        recommended = f"CPython {selected.get('version')} (detected standard 64-bit build)"
        steps = ["No Python installation is required.", "Run doctor."]
    else:
        recommended = "CPython 3.13 (standard 64-bit build)"
        steps = ["Install a supported CPython side by side.", "Run runtime detect again.", "Run doctor."]
    return {
        "reason": reason,
        "system": system,
        "architecture": platform.machine(),
        "recommendedVersion": recommended,
        "officialUrl": url,
        "detectedPackageManager": package_manager,
        "command": None,
        "requiresExplicitConsent": True,
        "steps": steps,
    }


def doctor() -> dict[str, Any]:
    discovery = discover()
    paths = runtime_paths()
    writable: dict[str, bool] = {}
    for name, path in paths.items():
        temporary_root: Path | None = None
        try:
            probe_directory = path
            if not probe_directory.is_dir():
                ancestor = probe_directory
                while not ancestor.exists() and ancestor != ancestor.parent:
                    ancestor = ancestor.parent
                temporary_root = Path(tempfile.mkdtemp(prefix="google-analytics-doctor-", dir=ancestor))
                probe_directory = temporary_root / "nested" / name
                probe_directory.mkdir(parents=True)
            handle, temp_name = tempfile.mkstemp(prefix="doctor-", dir=probe_directory)
            os.close(handle)
            Path(temp_name).unlink()
            writable[name] = True
        except OSError:
            writable[name] = False
        finally:
            if temporary_root is not None:
                shutil.rmtree(temporary_root, ignore_errors=True)
    try:
        import ssl
        tls = {"available": True, "defaultVerifyPaths": ssl.get_default_verify_paths()._asdict()}
    except ImportError:
        tls = {"available": False}
    return {
        "runtime": discovery,
        "paths": {name: str(path) for name, path in paths.items()},
        "writable": writable,
        "sourceRoot": str(source_root()),
        "tls": tls,
    }


def require_runtime(discovery: dict[str, Any]) -> None:
    if not discovery.get("selected"):
        raise AdvisorError(
            "PYTHON_RUNTIME_UNAVAILABLE",
            "No supported CPython 3.10-3.13 runtime was found.",
            EXIT_RUNTIME,
            details={"candidates": discovery.get("candidates", [])},
            next_action="Review runtime install-guide and obtain consent before installing Python.",
        )
