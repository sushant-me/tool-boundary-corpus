#!/usr/bin/env python3
"""Adapter: score `mcpvuln` against the corpus.

    python3 adapters/mcpvuln_adapter.py <source-directory>

`mcpvuln` (PyPI, `DINAKAR-S/Agentic-MCP-Scanner`) is an independent MCP
vulnerability scanner. It does not print findings to stdout the way the harness
expects - it writes a *scan contract* JSON to a path - so this adapter runs it and
normalises that contract into `{"findings": [{"rule": ...}]}`.

Why this is worth having
------------------------
Two independent implementations of MCP scanning currently exist and nobody has
compared them on common inputs. A one-off run says nothing; an adapter plus the
labelled corpus produces precision and recall numbers that CI can gate on, which is
the same shape as the measurement that caught `agentbound`'s own false positive.

Two deliberate choices
----------------------
* **A missing or failing detector exits 2, never 0.** The harness records that as an
  error on every case rather than as "found nothing". A detector that is absent must
  not score like a perfect one on the negatives - that was a real failure mode in
  this repository's own history.
* **`pattern_id` is used as the rule name, not the human description.** The harness
  matches on stable identifiers; prose in a description changes between releases and
  would silently break the mapping.

Caveat that belongs with any number this produces: mcpvuln scans a *codebase* while
`mcpaudit` audits a *declaration payload*. On the same fixture they read different
inputs, so a direct comparison of counts is not like-for-like.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# mcpvuln's taxonomy translated into this corpus's vocabulary.
#
# Without this the harness scores raw rule strings, so a detector that finds the
# right defect under a different name is counted as BOTH a false positive (a finding
# whose id is not expected) and a false negative (the expected id was not found).
# That is how mcpvuln was first measured at precision 0.000 / recall 0.000 while
# actually detecting the case: it reported
# `mcp.line_jumping.instructions_in_tool_description` where the case expects
# `instruction-in-declaration`.
#
# Anything not in this table is passed through unchanged, so an unmapped rule shows
# up as a real difference rather than being silently swallowed.
RULE_MAP = {
    "mcp.line_jumping.instructions_in_tool_description": "instruction-in-declaration",
    "mcp.line_jumping": "instruction-in-declaration",
    "mcp.invisible_characters": "invisible-characters",
    "mcp.lookalike_tool_name": "look-alike-tool-names",
    "mcp.reserved_name_collision": "reserved-name-collision",
    "mcp.destructive_declared_readonly": "destructive-declared-read-only",
    "mcp.unconstrained_sink": "unconstrained-sink-parameter",
}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: mcpvuln_adapter.py <directory-or-payload>", file=sys.stderr)
        return 2
    target = Path(argv[1])
    if not target.exists():
        print(f"not found: {target}", file=sys.stderr)
        return 2

    # mcpvuln scans a codebase, so a bare declaration payload has to be given a
    # directory to live in. The tool-list cases are single JSON files, and this is
    # what lets the same adapter score both kinds of case.
    tmp_holder: tempfile.TemporaryDirectory | None = None
    if target.is_file():
        tmp_holder = tempfile.TemporaryDirectory()
        stage = Path(tmp_holder.name)
        shutil.copy(target, stage / "tools_list.json")
        scan_target = stage
    else:
        scan_target = target

    # The console entry point, not `python -m mcpvuln`: the package ships no
    # __main__, so the module form fails with "cannot be directly executed".
    exe = shutil.which("mcpvuln")
    if exe is None:
        print("mcpvuln is not installed or not on PATH", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        contract = Path(tmp) / "contract.json"
        proc = subprocess.run(
            [exe, str(scan_target), "--json", str(contract), "--quiet"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if not contract.exists():
            print(
                f"mcpvuln produced no scan contract (exit {proc.returncode}); "
                f"stderr: {proc.stderr.strip()[:300]}",
                file=sys.stderr,
            )
            return 2
        data = json.loads(contract.read_text(encoding="utf-8"))

    findings = []
    for item in data.get("findings", []) or []:
        raw = item.get("pattern_id") or item.get("category") or "unlabelled"
        rule = RULE_MAP.get(raw, raw)
        findings.append({
            "rule": rule,
            "severity": item.get("severity"),
            "file": item.get("file"),
            "line": item.get("line"),
        })

    print(json.dumps({"findings": findings}))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
