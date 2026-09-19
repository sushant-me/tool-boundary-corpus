#!/usr/bin/env python3
"""Adapter: score `mcpaudit` against the tool-list cases.

    python3 adapters/mcpaudit_adapter.py <tool-list.json>

mcpaudit's own `--json` output already has the shape the harness wants, so this adapter
runs it and passes the findings through. Set PYTHONPATH to the mcpaudit checkout (or install
it) and the adapter does not care which.

Exit 1 is mcpaudit's "findings present", which the harness accepts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: mcpaudit_adapter.py <tools.json>", file=sys.stderr)
        return 2
    completed = subprocess.run(
        [sys.executable, "-m", "mcpaudit.cli", "audit", argv[1], "--json", "--no-colour"],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 1):
        print(completed.stderr.strip() or "mcpaudit failed", file=sys.stderr)
        return 2
    payload = json.loads(completed.stdout)
    print(json.dumps({"findings": [
        {"rule": f["rule"], "tool": f.get("tool", "")} for f in payload.get("findings", [])
    ]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
