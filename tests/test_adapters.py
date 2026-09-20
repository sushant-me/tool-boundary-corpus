"""Tests for `adapters/serve_tools.py`, the shim that lets a live-only scanner read a
captured payload.

The shim exists because some scanners will not read a file: they launch a server over
stdio and ask it questions. That makes the shim part of the measurement, and a shim that
answers the wrong thing turns a comparison into an artefact of the shim. The empty
`resources/list` and `prompts/list` replies are the specific case: a scanner with
resource-exposure checks reports failures when those calls error, so a tool-only fixture
would be scored against findings that exist only because the server was incomplete.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SHIM = ROOT / "adapters" / "serve_tools.py"

TOOLS = {
    "tools": [
        {"name": "run_task", "description": "Runs a maintenance task.",
         "inputSchema": {"type": "object",
                         "properties": {"command": {"type": "string"}}}},
        {"name": "search", "description": "Read-only search.",
         "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
    ]
}


def speak(payload: dict, *requests: dict) -> list[dict]:
    tmp = ROOT / "tests" / "_shim_payload.json"
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    try:
        stdin = "".join(json.dumps(r) + "\n" for r in requests)
        completed = subprocess.run(
            [sys.executable, str(SHIM), str(tmp)],
            input=stdin, capture_output=True, text=True, timeout=60, check=False)
        assert completed.returncode == 0, completed.stderr
        return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    finally:
        tmp.unlink(missing_ok=True)


def test_it_serves_the_tools_it_was_given() -> None:
    replies = speak(TOOLS, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    tools = replies[0]["result"]["tools"]
    assert [t["name"] for t in tools] == ["run_task", "search"]
    # the declaration must survive intact: a scanner scores what it receives
    assert tools[0]["inputSchema"]["properties"]["command"]["type"] == "string"


def test_it_answers_the_protocol_a_scanner_needs() -> None:
    replies = speak(
        TOOLS,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
        {"jsonrpc": "2.0", "id": 4, "method": "prompts/list", "params": {}},
    )
    assert len(replies) == 4
    assert replies[0]["result"]["serverInfo"]["name"] == "corpus-fixture"
    assert replies[2]["result"] == {"resources": []}
    assert replies[3]["result"] == {"prompts": []}


def test_resources_are_empty_rather_than_an_error() -> None:
    """The distinction that keeps a comparison fair.

    Every fixture in this corpus declares tools and nothing else. An error here is read
    by a scanner as 'resource exposure could not be established' and reported as a
    failure - a finding about the shim, not about the fixture.
    """
    (reply,) = speak(TOOLS, {"jsonrpc": "2.0", "id": 1,
                             "method": "resources/templates/list", "params": {}})
    assert "result" in reply, reply
    assert reply["result"] == {"resourceTemplates": []}


def test_an_unknown_method_is_an_error_not_a_silent_result() -> None:
    (reply,) = speak(TOOLS, {"jsonrpc": "2.0", "id": 1,
                             "method": "tools/call", "params": {}})
    assert reply["error"]["code"] == -32601


def test_a_notification_gets_no_reply() -> None:
    """`notifications/initialized` has no id, and answering it desynchronises the stream."""
    replies = speak(TOOLS, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}})
    assert len(replies) == 1
    assert replies[0]["id"] == 7
