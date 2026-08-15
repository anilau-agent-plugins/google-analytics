#!/usr/bin/env python3
"""Google Analytics Advisor dependency-free command-line entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

# Isolated mode intentionally omits script directories from sys.path. Add only this trusted,
# plugin-local directory so the bundled package remains importable without site packages.
SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from google_analytics_cli.app import dispatch
from google_analytics_cli.errors import AdvisorError, EXIT_INTERNAL
from google_analytics_cli.output import configure_streams, emit, envelope


def main(argv: list[str] | None = None) -> int:
    configure_streams()
    args = sys.argv[1:] if argv is None else argv
    command = " ".join(item for item in args if item != "--json") or "unknown"
    try:
        normalized, status, data = dispatch(args)
        emit(envelope(normalized, ok=True, status=status, data=data))
        return 0
    except AdvisorError as exc:
        emit(envelope(command, ok=False, status="error", errors=[exc.as_dict()]))
        return exc.exit_code
    except Exception as exc:  # final invariant boundary; never expose a traceback on stdout
        error = AdvisorError(
            "INTERNAL_ERROR",
            "The runtime encountered an internal error.",
            EXIT_INTERNAL,
            details={"reason": type(exc).__name__},
            next_action="Run doctor and report the error code if it persists.",
        )
        emit(envelope(command, ok=False, status="error", errors=[error.as_dict()]))
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
