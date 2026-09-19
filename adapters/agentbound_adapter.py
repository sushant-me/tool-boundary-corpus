#!/usr/bin/env python3
"""Adapter: score `agentbound` against the code cases.

    python3 adapters/agentbound_adapter.py <source-directory>

agentbound is a static detector for framework-side tool-boundary bugs. This adapter runs it
over a case directory and normalises its report into `{"findings": [{"rule": ...}]}`.

If agentbound is not installed the adapter says so on stderr and exits 2, which the harness
records as an error on every case — never as "found nothing", because a missing detector
must not score like a perfect one on negative cases.
"""

from __future__ import annotations

import json
import subprocess
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: agentbound_adapter.py <directory>", file=sys.stderr)
        return 2
    try:
        import agentbound  # noqa: F401
    except ImportError:
        print("agentbound is not installed in this interpreter", file=sys.stderr)
        return 2

    # The CLI is `agentbound scan <path> --json`; try the console script, then the module.
    attempts = (
        ["agentbound", "scan", argv[1], "--json"],
        [sys.executable, "-m", "agentbound.cli", "scan", argv[1], "--json"],
    )
    completed = None
    for argv_attempt in attempts:
        try:
            completed = subprocess.run(argv_attempt, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            continue
        if completed.returncode != 127:
            break
    if completed is None:
        print("could not run agentbound", file=sys.stderr)
        return 2
    if completed.returncode not in (0, 1):
        print(completed.stderr.strip() or "agentbound failed", file=sys.stderr)
        return 2
    # Documented shape: a JSON object with a "findings" list; tolerate a bare list.

    text = completed.stdout.strip()
    if not text:
        print(json.dumps({"findings": []}))
        return 0
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        print(f"agentbound output was not JSON: {text[:200]}", file=sys.stderr)
        return 2
    # agentbound prints a bare list of findings, not an object with a "findings" key.
    if isinstance(payload, list):
        findings = payload
    else:
        findings = payload.get("findings", [])
    print(json.dumps({"findings": [
        {"rule": f.get("rule", f.get("id", "")), "tool": f.get("file", "")}
        for f in findings if isinstance(f, dict)
    ]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
