#!/usr/bin/env python3
"""Adapter: score `mcp-security-scanner` (the `mcp-scan` CLI) against tool-list cases.

    python3 adapters/mcp_scanner_adapter.py <tools.json>

Needs `pip install mcp-security-scanner`, and `adapters/serve_tools.py` to answer the
protocol: this scanner reads declarations by **launching a server**, not by reading a
file, so the captured payload is served over stdio.

**Read this before quoting a number from it.** This scanner does not do the same job as
the detectors in the table. Twelve checks, most of them about authentication, transport,
resource exposure and session handling, against a corpus of declaration patterns. Only
two of its checks have an equivalent here:

    P-02  prompt/description injection heuristics  -> instruction-in-declaration
    X-01  dangerous capability detection in tools  -> unconstrained-sink-parameter

Everything else it reports has no case in this corpus, and everything this corpus
asserts about annotations, name collisions, invisible characters and look-alikes has no
check in it. So a headline precision/recall for this adapter would measure the mismatch
between two scopes rather than the quality of either — run it against the specific cases
those two rules cover, and read the comparison in the README, which says the same thing
with the evidence.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent

#: Its check ids, mapped to the corpus rule that asks the same question. Anything not in
#: here is deliberately ignored rather than guessed at.
RULE_MAP = {
    "P-02": "instruction-in-declaration",
    "X-01": "unconstrained-sink-parameter",
}


def scan(tools_path: str) -> dict:
    binary = shutil.which("mcp-scan")
    if binary is None:
        raise SystemExit("mcp-scan not found; pip install mcp-security-scanner")

    with tempfile.TemporaryDirectory() as tmp:
        report = pathlib.Path(tmp) / "report.json"
        command = f"{sys.executable} {HERE / 'serve_tools.py'} {pathlib.Path(tools_path).resolve()}"
        completed = subprocess.run(
            [binary, "scan", "--transport", "stdio", "--command", command,
             "--format", "json", "--output", str(report)],
            capture_output=True, text=True, check=False, timeout=300,
        )
        if not report.exists():
            raise SystemExit(
                f"mcp-scan produced no report (exit {completed.returncode}): "
                f"{completed.stderr.strip()[:200]}")
        return json.loads(report.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: mcp_scanner_adapter.py <tools.json>", file=sys.stderr)
        return 2

    payload = scan(argv[1])
    findings = []
    for check in payload.get("findings", []):
        rule = RULE_MAP.get(check.get("id"))
        if rule and not check.get("passed"):
            # One finding per check: this scanner's verdict is per check, not per tool.
            findings.append({"rule": rule, "tool": ""})
    print(json.dumps({"findings": findings}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
