"""Fail-closed restricted unified-diff parser and exact local patch simulator."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import AdvisorError, EXIT_INPUT
from .site_scanner import BLOCKED_NAME_PATTERN, BLOCKED_NAMES, EXCLUDED_DIRS, _is_link_or_reparse


MAX_PATCH_BYTES = 2 * 1024 * 1024
MAX_PATCH_FILES = 20
MAX_FILE_BYTES = 2 * 1024 * 1024
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")
HEX64 = re.compile(r"^[a-f0-9]{64}$")
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
SECRET_VALUE = re.compile(
    r"(?:GOCSPX-[A-Za-z0-9_-]+|\bya29\.[A-Za-z0-9._-]+|\b1//[A-Za-z0-9._-]+|"
    r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----|(?:api[_-]?secret|client[_-]?secret|password)\s*[:=]\s*['\"]?[^\s'\"$<{]{6,})",
    re.I,
)
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE = re.compile(r"(?<![A-Za-z0-9])\+?[1-9][0-9 ()-]{8,}[0-9](?![A-Za-z0-9])")


@dataclass(frozen=True)
class PatchFile:
    path: str
    create: bool
    hunks: tuple[tuple[int, int, int, int, tuple[str, ...]], ...]
    added_lines: tuple[str, ...]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_relative(value: str) -> str:
    if "\x00" in value or "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value) or ":" in value:
        raise AdvisorError("UNSAFE_PATCH_PATH", "Patch paths must be normalized relative POSIX paths.", EXIT_INPUT, details={"path": value[:256]})
    path = PurePosixPath(value)
    if not value or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise AdvisorError("UNSAFE_PATCH_PATH", "Patch path traversal or normalization was blocked.", EXIT_INPUT, details={"path": value[:256]})
    lower_parts = [part.lower() for part in path.parts]
    name = lower_parts[-1]
    if any(part in EXCLUDED_DIRS for part in lower_parts) or name in BLOCKED_NAMES or name.startswith(".env") or BLOCKED_NAME_PATTERN.search(name):
        raise AdvisorError("UNSAFE_PATCH_PATH", "Generated, dependency, runtime, or credential paths cannot be patched.", EXIT_INPUT, details={"path": value})
    if Path(name).suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".crt", ".cer"}:
        raise AdvisorError("UNSAFE_PATCH_PATH", "Credential and certificate files cannot be patched.", EXIT_INPUT, details={"path": value})
    for part in lower_parts:
        stem = part.rstrip(" .").split(".", 1)[0]
        if stem in WINDOWS_RESERVED or part != part.rstrip(" ."):
            raise AdvisorError("UNSAFE_PATCH_PATH", "A Windows reserved path was blocked.", EXIT_INPUT, details={"path": value})
    return path.as_posix()


def parse_patch(raw: bytes) -> list[PatchFile]:
    if len(raw) > MAX_PATCH_BYTES or b"\x00" in raw:
        raise AdvisorError("INVALID_PATCH", "The patch is binary or exceeds the 2 MiB limit.", EXIT_INPUT)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AdvisorError("INVALID_PATCH", "The patch must be UTF-8.", EXIT_INPUT) from exc
    if SECRET_VALUE.search(text) or EMAIL.search(text) or PHONE.search(text):
        raise AdvisorError("PATCH_SENSITIVE_CONTENT_BLOCKED", "A patch containing secret-shaped or PII-shaped content cannot be stored as a project artifact.", EXIT_INPUT)
    lines = text.splitlines()
    files: list[PatchFile] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith(("diff --git ", "index ")):
            index += 1
            continue
        if line.startswith(("old mode ", "new mode ", "deleted file mode ", "new file mode ", "rename from ", "rename to ", "copy from ", "copy to ", "Binary files ", "GIT binary patch")):
            raise AdvisorError("INVALID_PATCH", "Mode, rename, copy, delete, and binary patch metadata is forbidden.", EXIT_INPUT)
        if not line.startswith("--- "):
            if line.strip():
                raise AdvisorError("INVALID_PATCH", "Unexpected unified-diff content.", EXIT_INPUT, details={"line": index + 1})
            index += 1
            continue
        old_header = line[4:].split("\t", 1)[0]
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise AdvisorError("INVALID_PATCH", "A patch file header is incomplete.", EXIT_INPUT)
        new_header = lines[index][4:].split("\t", 1)[0]
        index += 1
        create = old_header == "/dev/null"
        if new_header == "/dev/null":
            raise AdvisorError("INVALID_PATCH", "File deletion is forbidden.", EXIT_INPUT)
        if not new_header.startswith("b/") or (not create and (not old_header.startswith("a/") or old_header[2:] != new_header[2:])):
            raise AdvisorError("INVALID_PATCH", "Only a/path to b/same-path patches and file creation are allowed.", EXIT_INPUT)
        path = _safe_relative(new_header[2:])
        hunks: list[tuple[int, int, int, int, tuple[str, ...]]] = []
        added: list[str] = []
        while index < len(lines) and not lines[index].startswith("--- ") and not lines[index].startswith("diff --git "):
            if lines[index].startswith(("index ", "new file mode ")):
                if lines[index].startswith("new file mode "):
                    raise AdvisorError("INVALID_PATCH", "Mode changes are forbidden.", EXIT_INPUT)
                index += 1
                continue
            match = HUNK.match(lines[index])
            if not match:
                raise AdvisorError("INVALID_PATCH", "Only exact unified-diff hunks are supported.", EXIT_INPUT, details={"line": index + 1})
            old_start, old_count, new_start, new_count = (int(match.group(1)), int(match.group(2) or 1), int(match.group(3)), int(match.group(4) or 1))
            index += 1
            body: list[str] = []
            seen_old = seen_new = 0
            while index < len(lines) and not lines[index].startswith(("@@ ", "--- ", "diff --git ")):
                item = lines[index]
                if item == "\\ No newline at end of file":
                    index += 1
                    continue
                if not item or item[0] not in {" ", "+", "-"}:
                    raise AdvisorError("INVALID_PATCH", "A hunk line has an invalid prefix.", EXIT_INPUT, details={"line": index + 1})
                body.append(item)
                if item[0] in {" ", "-"}: seen_old += 1
                if item[0] in {" ", "+"}: seen_new += 1
                if item[0] == "+": added.append(item[1:])
                index += 1
            if seen_old != old_count or seen_new != new_count:
                raise AdvisorError("INVALID_PATCH", "A hunk count does not match its body.", EXIT_INPUT)
            hunks.append((old_start, old_count, new_start, new_count, tuple(body)))
        if not hunks:
            raise AdvisorError("INVALID_PATCH", "Each patched file requires at least one hunk.", EXIT_INPUT)
        if SECRET_VALUE.search("\n".join(added)) or EMAIL.search("\n".join(added)) or PHONE.search("\n".join(added)):
            raise AdvisorError("PATCH_SENSITIVE_CONTENT_BLOCKED", "Secret-shaped or PII-shaped added content was blocked.", EXIT_INPUT, details={"path": path})
        files.append(PatchFile(path, create, tuple(hunks), tuple(added)))
    if not files or len(files) > MAX_PATCH_FILES:
        raise AdvisorError("INVALID_PATCH", "A patch must contain 1 to 20 files.", EXIT_INPUT)
    lowered = [item.path.casefold() for item in files]
    if len(lowered) != len(set(lowered)):
        raise AdvisorError("UNSAFE_PATCH_PATH", "Duplicate or case-colliding patch paths were blocked.", EXIT_INPUT)
    return files


def _resolve_target(root: Path, relative: str, *, allow_missing: bool) -> Path:
    target = root.joinpath(*PurePosixPath(relative).parts)
    parent = target.parent
    if not parent.exists() or _is_link_or_reparse(parent):
        raise AdvisorError("UNSAFE_PATCH_PATH", "Patch parent must be an existing non-linked directory.", EXIT_INPUT, details={"path": relative})
    resolved_parent = parent.resolve(strict=True)
    if resolved_parent != root and root not in resolved_parent.parents:
        raise AdvisorError("UNSAFE_PATCH_PATH", "Patch target escapes the project root.", EXIT_INPUT, details={"path": relative})
    if target.exists() and _is_link_or_reparse(target):
        raise AdvisorError("UNSAFE_PATCH_PATH", "Linked or reparse-point targets cannot be patched.", EXIT_INPUT, details={"path": relative})
    if not allow_missing and not target.exists():
        raise AdvisorError("PATCH_PRECONDITION_FAILED", "A target file is missing.", EXIT_INPUT, details={"path": relative})
    return target


def _decode_source(raw: bytes, relative: str) -> tuple[str, str, str, bool]:
    if len(raw) > MAX_FILE_BYTES or b"\x00" in raw[:8192]:
        raise AdvisorError("INVALID_PATCH_TARGET", "Only bounded text source files are supported.", EXIT_INPUT, details={"path": relative})
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AdvisorError("INVALID_PATCH_TARGET", "Only UTF-8 and UTF-8-BOM source files are supported.", EXIT_INPUT, details={"path": relative}) from exc
    crlf = text.count("\r\n")
    lone_lf = text.count("\n") - crlf
    if crlf and lone_lf:
        raise AdvisorError("INVALID_PATCH_TARGET", "Mixed newline styles are not supported.", EXIT_INPUT, details={"path": relative})
    newline = "\r\n" if crlf else "\n"
    return text, "utf-8-bom" if bom else "utf-8", newline, text.endswith(("\n", "\r\n"))


def simulate(root: Path, patch: PatchFile) -> dict[str, Any]:
    target = _resolve_target(root, patch.path, allow_missing=patch.create)
    if patch.create and target.exists():
        raise AdvisorError("PATCH_PRECONDITION_FAILED", "A create target already exists.", EXIT_INPUT, details={"path": patch.path})
    raw = b"" if patch.create else target.read_bytes()
    text, encoding, newline, trailing = _decode_source(raw, patch.path)
    source = text.splitlines()
    result: list[str] = []
    cursor = 0
    for old_start, _old_count, _new_start, _new_count, body in patch.hunks:
        expected = 0 if patch.create and old_start == 0 else old_start - 1
        if expected != cursor:
            if expected < cursor or expected > len(source):
                raise AdvisorError("PATCH_PRECONDITION_FAILED", "A hunk location is outside the source file.", EXIT_INPUT, details={"path": patch.path})
            result.extend(source[cursor:expected])
            cursor = expected
        for line in body:
            prefix, content = line[0], line[1:]
            if prefix in {" ", "-"}:
                if cursor >= len(source) or source[cursor] != content:
                    raise AdvisorError("PATCH_PRECONDITION_FAILED", "Patch context does not exactly match the source file.", EXIT_INPUT, details={"path": patch.path})
                if prefix == " ": result.append(content)
                cursor += 1
            else:
                result.append(content)
    result.extend(source[cursor:])
    output = newline.join(result)
    if trailing or patch.create:
        output += newline
    encoded = output.encode("utf-8")
    if encoding == "utf-8-bom":
        encoded = b"\xef\xbb\xbf" + encoded
    if len(encoded) > MAX_FILE_BYTES:
        raise AdvisorError("INVALID_PATCH_TARGET", "The patched file exceeds the 2 MiB limit.", EXIT_INPUT, details={"path": patch.path})
    return {
        "path": patch.path, "target": target, "create": patch.create, "before": raw, "after": encoded,
        "beforeSha256": sha256_bytes(raw), "afterSha256": sha256_bytes(encoded), "beforeSize": len(raw),
        "afterSize": len(encoded), "encoding": encoding, "newline": "CRLF" if newline == "\r\n" else "LF",
    }


def simulate_patch(project_root: Path, raw: bytes) -> list[dict[str, Any]]:
    root = project_root.resolve(strict=True)
    if _is_link_or_reparse(root):
        raise AdvisorError("SITE_SCAN_ROOT_INVALID", "The project root cannot be linked or a reparse point.", EXIT_INPUT)
    return [simulate(root, item) for item in parse_patch(raw)]
