"""Deterministic OpenAPI export for the generated-contract build step.

Prints the canonical FastAPI OpenAPI document as stable JSON (sorted keys,
two-space indent, trailing newline). The frontend contract generation consumes
this byte-for-byte-stable snapshot; CI compares a fresh export against the
committed `frontend/src/contracts/openapi.json` to detect wire-contract drift.
"""

from __future__ import annotations

import json
import sys

from revolab.main import app


def main() -> None:
    spec = app.openapi()
    json.dump(spec, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
