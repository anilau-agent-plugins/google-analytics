#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ENTRYPOINT="$SCRIPT_DIR/google_analytics.py"
PROBE='import sys; ok=sys.implementation.name.encode().hex()==hex(0x63707974686f6e)[2:] and (3,10)<=sys.version_info[:2]<(3,14); raise SystemExit(0 if ok else 1)'

for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -I -B -X utf8 -c "$PROBE" >/dev/null 2>&1; then
        exec "$candidate" -I -B -X utf8 "$ENTRYPOINT" "$@"
    fi
done

printf '%s\n' '{"schemaVersion":1,"cliVersion":"0.6.0","ok":false,"command":"bootstrap","status":"error","data":{},"warnings":[],"errors":[{"code":"PYTHON_RUNTIME_UNAVAILABLE","message":"Supported CPython 3.10-3.13 was not found.","retryable":false,"details":{},"nextAction":"Install a standard 64-bit CPython from https://www.python.org/downloads/ after explicit consent, then run again."}]}'
exit 2
