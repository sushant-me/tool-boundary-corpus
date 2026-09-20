#!/usr/bin/env python3
"""Compare detectors case by case, rather than with one headline number.

    python3 adapters/compare.py 'agentbound scan {input} --json' \
                               'python3 adapters/mcp_scanner_adapter.py {input}' \
                               'python3 adapters/mcpaudit_adapter.py {input}'

Each detector is run against every case of a kind, and each case is reported as
*caught* (it reported a rule the case expects), *missed* (it reported nothing) or
*false positive* (it reported on a negative case). That is the same information a
precision/recall row carries, laid out so a reader can see **which classes** a detector
covers instead of a single averaged number.

That layout matters when the detectors are not doing the same job. A scanner with twelve
checks about authentication, transport and resources scores badly on a corpus of
declaration patterns - and one number would say "worse" where the truth is "different
scope, and here is exactly where each of them is blind". The per-class view says which.

Exit codes: 0 every detector ran, 2 a detector could not be run at all.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shlex
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from corpus import evaluate, load_cases, metrics, score  # noqa: E402


def parse_summary(command: str, summary: list) -> dict:
    """Turn one detector's per-case outcomes into caught / missed / false-positive sets."""
    caught, missed, false_positives, errors = set(), set(), set(), set()
    for outcome in summary:
        case_id = outcome.case.id
        expected = set(outcome.case.expected_rules)
        reported = set(outcome.found_rules)
        if outcome.error:
            errors.add(case_id)
        if expected:
            (caught if expected & reported else missed).add(case_id)
        elif reported:
            false_positives.add(case_id)
    return {"caught": caught, "missed": missed, "false_positives": false_positives,
            "errors": errors}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("detectors", nargs="+", help="command templates, {input} substituted")
    parser.add_argument("--kind", default="tool-list")
    args = parser.parse_args(argv)

    cases = load_cases(ROOT / "cases")
    chosen = [c for c in cases if c.kind == args.kind]
    negatives = [c for c in chosen if not c.expected_rules]
    positives = [c for c in chosen if c.expected_rules]

    print(f"{len(chosen)} {args.kind} case(s): {len(positives)} positive, "
          f"{len(negatives)} negative\n")

    results = {}
    for command in args.detectors:
        outcomes = evaluate(chosen, command)
        label = pathlib.Path(shlex.split(command)[0]).name
        if label == "python3":
            label = pathlib.Path(shlex.split(command)[1]).name
        results[label] = parse_summary(command, outcomes)
        bucket = results[label]
        print(f"{label}")
        print(f"  positives caught {len(bucket['caught'])}/{len(positives)}   "
              f"missed {len(bucket['missed'])}   "
              f"false positives on negatives {len(bucket['false_positives'])}"
              f"   errors {len(bucket['errors'])}")

    print()
    header = f"{'case':46} {'expects':34} " + " ".join(
        f"{name[:18]:>18}" for name in results)
    print(header)
    print("-" * len(header))
    for case in chosen:
        expected = ", ".join(case.expected_rules) if case.expected_rules else "(clean)"
        cells = []
        for name, bucket in results.items():
            if case.id in bucket["caught"]:
                cells.append("caught".rjust(18))
            elif case.id in bucket["missed"]:
                cells.append("MISSED".rjust(18))
            elif case.id in bucket["false_positives"]:
                cells.append("FALSE POS".rjust(18))
            else:
                cells.append("silent (ok)".rjust(18))
        print(f"{case.id:46} {expected[:34]:34} " + " ".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
