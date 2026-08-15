"""Platform-protected secret storage with no plaintext fallback."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .errors import AdvisorError, EXIT_CONFIGURATION
from .paths import runtime_paths


SERVICE = "com.anilau.google-analytics-advisor"


class SecretStore:
    def put(self, key: str, value: bytes) -> None:
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        raise NotImplementedError

    def delete(self, key: str) -> bool:
        raise NotImplementedError


def _safe_key(key: str) -> str:
    if not key or len(key) > 200 or any(ord(char) < 32 for char in key):
        raise AdvisorError("SECRET_KEY_INVALID", "The secret reference is invalid.", EXIT_CONFIGURATION)
    return key


class WindowsDpapiStore(SecretStore):
    CRYPTPROTECT_UI_FORBIDDEN = 0x1

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    def __init__(self, root: Path) -> None:
        self.root = root / "secrets"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        self.crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(self.DATA_BLOB), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(self.DATA_BLOB),
        ]
        self.crypt32.CryptProtectData.restype = ctypes.c_int
        self.crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(self.DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(self.DATA_BLOB),
        ]
        self.crypt32.CryptUnprotectData.restype = ctypes.c_int
        self.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel32.LocalFree.restype = ctypes.c_void_p

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(_safe_key(key).encode("utf-8")).hexdigest()
        return self.root / f"{digest}.dpapi"

    def _protect(self, value: bytes) -> bytes:
        if not value:
            raise AdvisorError("SECRET_VALUE_INVALID", "An empty secret cannot be stored.", EXIT_CONFIGURATION)
        buffer = ctypes.create_string_buffer(value, len(value))
        source = self.DATA_BLOB(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = self.DATA_BLOB()
        ok = self.crypt32.CryptProtectData(
            ctypes.byref(source), "Google Analytics Advisor credential", None, None, None,
            self.CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(target),
        )
        if not ok:
            raise AdvisorError("SECRET_STORE_UNAVAILABLE", "Windows could not protect the credential.", EXIT_CONFIGURATION,
                               details={"winError": ctypes.get_last_error()})
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            self.kernel32.LocalFree(target.pbData)

    def _unprotect(self, value: bytes) -> bytes:
        buffer = ctypes.create_string_buffer(value, len(value))
        source = self.DATA_BLOB(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = self.DATA_BLOB()
        ok = self.crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, self.CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(target)
        )
        if not ok:
            raise AdvisorError("SECRET_STORE_CORRUPT", "Windows could not unlock the credential.", EXIT_CONFIGURATION,
                               details={"winError": ctypes.get_last_error()})
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            self.kernel32.LocalFree(target.pbData)

    def put(self, key: str, value: bytes) -> None:
        path = self._path(key)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(self._protect(value))
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return self._unprotect(path.read_bytes())
        except FileNotFoundError as exc:
            raise AdvisorError("SECRET_NOT_FOUND", "The requested credential is not available.", EXIT_CONFIGURATION) from exc

    def delete(self, key: str) -> bool:
        path = self._path(key)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False


class MacKeychainStore(SecretStore):
    ERR_NOT_FOUND = -25300
    ERR_DUPLICATE = -25299

    def __init__(self) -> None:
        try:
            self.security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
            self.core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        except OSError as exc:
            raise AdvisorError("SECRET_STORE_UNAVAILABLE", "macOS Keychain is unavailable.", EXIT_CONFIGURATION) from exc
        self.security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
        self.security.SecKeychainItemDelete.restype = ctypes.c_int32
        self.security.SecKeychainItemFreeContent.restype = ctypes.c_int32
        self.core.CFRelease.argtypes = [ctypes.c_void_p]

    @staticmethod
    def _bytes(value: str) -> bytes:
        return value.encode("utf-8")

    def _find(self, key: str) -> tuple[int, bytes | None, ctypes.c_void_p]:
        service = self._bytes(SERVICE)
        account = self._bytes(_safe_key(key))
        length = ctypes.c_uint32()
        data = ctypes.c_void_p()
        item = ctypes.c_void_p()
        status = self.security.SecKeychainFindGenericPassword(
            None, len(service), service, len(account), account,
            ctypes.byref(length), ctypes.byref(data), ctypes.byref(item),
        )
        if status != 0:
            return status, None, item
        try:
            value = ctypes.string_at(data, length.value)
        finally:
            self.security.SecKeychainItemFreeContent(None, data)
        return status, value, item

    def put(self, key: str, value: bytes) -> None:
        status, _, item = self._find(key)
        if status == 0:
            try:
                result = self.security.SecKeychainItemModifyAttributesAndData(item, None, len(value), value)
            finally:
                self.core.CFRelease(item)
        elif status == self.ERR_NOT_FOUND:
            service = self._bytes(SERVICE)
            account = self._bytes(_safe_key(key))
            result = self.security.SecKeychainAddGenericPassword(
                None, len(service), service, len(account), account, len(value), value, None
            )
        else:
            result = status
        if result != 0:
            raise AdvisorError("SECRET_STORE_LOCKED", "macOS Keychain rejected the credential operation.", EXIT_CONFIGURATION,
                               details={"osStatus": result})

    def get(self, key: str) -> bytes:
        status, value, item = self._find(key)
        if status == self.ERR_NOT_FOUND:
            raise AdvisorError("SECRET_NOT_FOUND", "The requested credential is not available.", EXIT_CONFIGURATION)
        if status != 0 or value is None:
            raise AdvisorError("SECRET_STORE_LOCKED", "macOS Keychain could not unlock the credential.", EXIT_CONFIGURATION,
                               details={"osStatus": status})
        if item:
            self.core.CFRelease(item)
        return value

    def delete(self, key: str) -> bool:
        status, _, item = self._find(key)
        if status == self.ERR_NOT_FOUND:
            return False
        if status != 0:
            raise AdvisorError("SECRET_STORE_LOCKED", "macOS Keychain could not access the credential.", EXIT_CONFIGURATION,
                               details={"osStatus": status})
        try:
            result = self.security.SecKeychainItemDelete(item)
        finally:
            self.core.CFRelease(item)
        if result != 0:
            raise AdvisorError("SECRET_STORE_LOCKED", "macOS Keychain could not delete the credential.", EXIT_CONFIGURATION,
                               details={"osStatus": result})
        return True


class LinuxSecretServiceStore(SecretStore):
    def __init__(self, *, runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run) -> None:
        if shutil.which("secret-tool") is None:
            raise AdvisorError(
                "SECRET_STORE_UNAVAILABLE",
                "Linux Secret Service requires secret-tool and an unlocked user keyring.",
                EXIT_CONFIGURATION,
                next_action="Install libsecret-tools with explicit approval, unlock the desktop keyring, and retry.",
            )
        self.runner = runner

    def _run(self, args: list[str], *, value: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        result = self.runner(args, input=value, capture_output=True, check=False)
        if result.returncode != 0:
            raise AdvisorError("SECRET_STORE_LOCKED", "Linux Secret Service rejected the credential operation.",
                               EXIT_CONFIGURATION, details={"exitCode": result.returncode})
        return result

    def put(self, key: str, value: bytes) -> None:
        encoded = base64.b64encode(value)
        self._run(["secret-tool", "store", "--label=Google Analytics Advisor", "application", SERVICE, "key", _safe_key(key)], value=encoded)

    def get(self, key: str) -> bytes:
        result = self._run(["secret-tool", "lookup", "application", SERVICE, "key", _safe_key(key)])
        raw = result.stdout.rstrip(b"\r\n")
        if not raw:
            raise AdvisorError("SECRET_NOT_FOUND", "The requested credential is not available.", EXIT_CONFIGURATION)
        try:
            return base64.b64decode(raw, validate=True)
        except ValueError as exc:
            raise AdvisorError("SECRET_STORE_CORRUPT", "The stored Linux credential is damaged.", EXIT_CONFIGURATION) from exc

    def delete(self, key: str) -> bool:
        self._run(["secret-tool", "clear", "application", SERVICE, "key", _safe_key(key)])
        return True


def secret_store(
    *, env: dict[str, str] | None = None, system: str | None = None,
    linux_runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> SecretStore:
    os_name = system or platform.system()
    if os_name == "Windows":
        return WindowsDpapiStore(runtime_paths(env=env, system="Windows")["state"])
    if os_name == "Darwin":
        return MacKeychainStore()
    if os_name == "Linux":
        return LinuxSecretServiceStore(runner=linux_runner)
    raise AdvisorError("SECRET_STORE_UNAVAILABLE", f"Unsupported credential platform: {os_name}", EXIT_CONFIGURATION)
