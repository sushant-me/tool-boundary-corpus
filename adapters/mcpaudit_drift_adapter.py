#!/usr/bin/env python3
"""Adapter: score `mcpaudit` against the drift cases.

    python3 adapters/mcpaudit_drift_adapter.py <dir>

The harness hands a drift case over as a directory holding two declarations of the same
server, `approved.json` and `current.json`. How a detector records an approval is its own
business, so this adapter makes the lock the way a user would — write it from the approved
payload, then audit the current one against it — and reports everything the auditor said.

Two things are deliberately **not** filtered:

* ordinary findings on the current declaration, and
* the drift entries themselves.

The first version of this adapter read only the `findings` key and dropped everything else,
which scored the detector at recall 0.0 while it was in fact catching both positive cases:
`mcpaudit` prints drift under a separate top-level `drift` list keyed by `kind`, not under
`findings` keyed by `rule`. A shim that quietly discards part of a detector's output is a
way to make a benchmark agree with itself, and this corpus exists to avoid that. So the
union is reported, and a case that trips two rules is expected to declare two rules.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

# The detector's own names, mapped onto this corpus's rule vocabulary. Identity today, but
# written down so that a rename on either side is a visible edit here rather than a silent
# scoring change.
DRIFT_KINDS = {
    "description-changed": "description-changed",
    "schema-changed": "schema-changed",
    "annotations-changed": "annotations-changed",
    "declaration-changed": "declaration-changed",
    "tool-added": "tool-added",
    "tool-removed": "tool-removed",
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: mcpaudit_drift_adapter.py <dir>", file=sys.stderr)
        return 2

    directory = pathlib.Path(argv[1])
    approved, current = directory / "approved.json", directory / "current.json"
    if not approved.exists() or not current.exists():
        print(f"{directory}: needs approved.json and current.json", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        lock = pathlib.Path(tmp) / "tools.lock.json"

        # Record the approval from the *approved* payload. Building the lock from
        # `current.json` would make every drift case pass by construction.
        record = subprocess.run(
            [sys.executable, "-m", "mcpaudit.cli", "audit", str(approved),
             "--write-lock", str(lock), "--no-colour"],
            capture_output=True, text=True, check=False, timeout=120,
        )
        if not lock.exists():
            print(f"could not record the approved declarations: {record.stderr.strip()[:200]}",
                  file=sys.stderr)
            return 2

        audit = subprocess.run(
            [sys.executable, "-m", "mcpaudit.cli", "audit", str(current),
             "--lock", str(lock), "--json", "--no-colour"],
            capture_output=True, text=True, check=False, timeout=120,
        )
        # 1 is this tool's "findings present" exit code, which the harness accepts.
        if audit.returncode not in (0, 1):
            print(f"audit failed: {audit.stderr.strip()[:200]}", file=sys.stderr)
            return 2

        payload = json.loads(audit.stdout)

    findings = [
        {"rule": f["rule"], "tool": f.get("tool", "")}
        for f in payload.get("findings", []) if f.get("rule")
    ]
    findings += [
        {"rule": DRIFT_KINDS.get(d.get("kind"), d.get("kind")), "tool": d.get("tool", "")}
        for d in payload.get("drift", []) if d.get("kind")
    ]
    print(json.dumps({"findings": findings}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
