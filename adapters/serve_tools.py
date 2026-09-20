#!/usr/bin/env python3
"""Serve a captured `tools/list` payload over stdio JSON-RPC.

Some scanners will only read declarations by launching a server, so a captured payload
needs something to answer the protocol. This is that: stdlib only, no SDK, and it serves
the payload it is given.

    python3 adapters/serve_tools.py <payload.json>

It answers the methods such a scanner may call, including `resources/list`,
`prompts/list` and `resources/templates/list` with **empty lists** rather than errors.
That matters for comparing fairly: a scanner with checks for resource exposure and
prompt templates reports a failure when those calls error out, and a review of a
*tool-only* payload would then be scored against findings that exist only because the
shim was incomplete. Returning empty lists says "this server declares no resources",
which is true of every corpus fixture, and leaves the tool checks measuring tools.
"""

from __future__ import annotations

import json
import pathlib
import sys

PROTOCOL_VERSION = "2025-06-18"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: serve_tools.py <payload.json>", file=sys.stderr)
        return 2

    payload = json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
    tools = payload.get("tools", payload)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        method, request_id = request.get("method"), request.get("id")
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "corpus-fixture", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {"tools": tools}
        elif method in ("resources/list", "resources/templates/list"):
            result = {"resources": []} if method == "resources/list" else {"resourceTemplates": []}
        elif method == "prompts/list":
            result = {"prompts": []}
        elif method in ("notifications/initialized", "initialized"):
            continue  # a notification: no response
        else:
            print(json.dumps({
                "jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }), flush=True)
            continue

        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result},
                         ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
