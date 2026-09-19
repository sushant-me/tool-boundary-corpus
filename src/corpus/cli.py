"""`corpus validate | list | run` — the corpus as a command.

    corpus validate                                    # every case is well-formed
    corpus list --kind tool-list
    corpus run --detector 'python3 -m mcpaudit.cli audit {input} --json' --kind tool-list
    corpus run --detector 'python3 adapters/agentbound_adapter.py {input}' --kind code

`--kind` exists because a detector is designed for a kind of input. Scoring an MCP
declaration scanner on framework source would report recall 0 for a tool doing its job, and
scoring a code scanner on a JSON tool list reports errors. A number is only meaningful for
the inputs the detector claims to handle, so the harness refuses to pool them silently.

Exit codes: `0` all selected cases passed, `1` at least one failed, `2` the corpus or the
detector could not be used. `--min-precision` / `--min-recall` turn the numbers into a gate.
"""

from __future__ import annotations

import argparse
import json
import sys

from .harness import (
    SELF_AUTHORED_CAVEAT, evaluate, load_cases, metrics, report,
)

DEFAULT_CORPUS = "cases"


def _select(cases, kind: str | None, case_id: str | None):
    if kind:
        cases = [c for c in cases if c.kind == kind]
    if case_id:
        cases = [c for c in cases if c.id == case_id]
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus",
        description="Score a detector against a labelled corpus of agent tool-boundary cases.",
    )
    parser.add_argument("--corpus", default=DEFAULT_CORPUS, help="corpus directory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="check that every case is well-formed")

    list_parser = sub.add_parser("list", help="list the cases")
    list_parser.add_argument("--kind", choices=["tool-list", "code"])
    list_parser.add_argument("--json", action="store_true")

    run_parser = sub.add_parser("run", help="score a detector")
    run_parser.add_argument(
        "--detector", required=True,
        help="command template; {input} is replaced with the case's path",
    )
    run_parser.add_argument("--kind", choices=["tool-list", "code"],
                            help="only score the cases of this kind (recommended)")
    run_parser.add_argument("--case", help="score a single case id")
    run_parser.add_argument("--json", action="store_true")
    run_parser.add_argument("--min-precision", type=float, default=None)
    run_parser.add_argument("--min-recall", type=float, default=None)
    run_parser.add_argument("--quiet-caveat", action="store_true",
                            help="suppress the self-authored-corpus warning (for CI logs)")

    args = parser.parse_args(argv)

    try:
        cases = _select(load_cases(args.corpus), getattr(args, "kind", None),
                        getattr(args, "case", None))
    except (FileNotFoundError, ValueError) as exc:
        print(f"corpus: {exc}", file=sys.stderr)
        return 2

    if args.command == "validate":
        kinds = {}
        for case in cases:
            kinds[case.kind] = kinds.get(case.kind, 0) + 1
        print(f"corpus ok: {len(cases)} case(s) — " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
        return 0

    if args.command == "list":
        if args.json:
            print(json.dumps([{"id": c.id, "kind": c.kind, "label": c.label,
                               "expected_rules": c.expected_rules, "source": c.source}
                              for c in cases], indent=2))
        else:
            for case in cases:
                print(f"  {case.label:<9} {case.kind:<10} {case.id}")
        return 0

    if not cases:
        print("corpus: no cases matched the selection", file=sys.stderr)
        return 2

    outcomes = evaluate(cases, args.detector)
    summary = metrics(outcomes)

    if args.json:
        print(json.dumps({
            "detector": args.detector,
            "kind": args.kind,
            "metrics": summary,
            "caveat": SELF_AUTHORED_CAVEAT,
            "cases": [o.to_dict() for o in outcomes],
        }, indent=2))
    else:
        print(report(outcomes, args.detector))
        if args.quiet_caveat:
            print("\n(self-authored corpus — regression gate, not an independent benchmark)")

    failed = [o for o in outcomes if not o.passed]
    if args.min_precision is not None and summary["precision"] < args.min_precision:
        print(f"corpus: precision {summary['precision']:.3f} < {args.min_precision:.3f}", file=sys.stderr)
        return 1
    if args.min_recall is not None and summary["recall"] < args.min_recall:
        print(f"corpus: recall {summary['recall']:.3f} < {args.min_recall:.3f}", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
