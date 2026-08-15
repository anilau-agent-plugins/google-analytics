#!/bin/sh
set -eu
export PYTHONDONTWRITEBYTECODE=1

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(0 if (3,10)<=sys.version_info[:2]<(3,14) else 1)' >/dev/null 2>&1; then
        PYTHON=$candidate
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo 'CPython 3.10-3.13 is required for validation.' >&2
    exit 2
fi

"$PYTHON" -m unittest discover -s tests -v
sh ./scripts/google-analytics.sh version --json
sh ./scripts/google-analytics.sh runtime detect --json
sh ./scripts/google-analytics.sh runtime install-guide --json
