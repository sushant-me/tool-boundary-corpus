"""A detector-agnostic corpus harness.

The three tools in this toolkit assert that they find things. Assertions about a detector
are worth very little on their own — the interesting numbers are precision and recall
against labelled cases, and those only exist if somebody wrote the cases down.

This harness is deliberately **not** tied to any detector. A detector is a command that
reads an input path and prints JSON:

    {"findings": [{"rule": "reserved-name-collision", "tool": "set_model_response"}]}

so the same corpus can score `mcpaudit`, `agentbound`, someone else's scanner, or a
detector written tomorrow — and a number produced here can be compared with one produced
next year.

**The honest limitation, stated where the numbers are computed:** this corpus was authored
by the same person who wrote the detectors it scores. A self-authored corpus overstates
performance, and every report this tool prints says so. The mitigations are that positive
cases cite where the pattern came from in the real world, negative cases are the *correct*
implementation of the same job (which is where precision is actually decided), and the
harness ships with fake detectors whose expected metrics are asserted in the tests — so a
harness that flatters every input fails its own suite.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SELF_AUTHORED_CAVEAT = (
    "This corpus was written by the author of the detectors it scores. Self-authored "
    "corpora overstate performance; treat these numbers as a regression gate, not as an "
    "independent benchmark."
)


@dataclass
class Case:
    id: str
    kind: str  # "tool-list" | "code"
    label: str  # "positive" | "negative"
    expected_rules: list[str]
    source: str
    fixture: dict[str, Any]
    path: Path | None = None

    @property
    def is_positive(self) -> bool:
        return self.label == "positive"


@dataclass
class Outcome:
    case: Case
    found_rules: set[str] = field(default_factory=set)
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and self.tp + self.tn > 0 and self.fp == 0 and self.fn == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case.id,
            "kind": self.case.kind,
            "label": self.case.label,
            "expected_rules": self.case.expected_rules,
            "found_rules": sorted(self.found_rules),
            "tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn,
            "passed": self.passed,
            "error": self.error,
        }


def load_cases(directory: str | Path) -> list[Case]:
    """Load every case file, refusing a malformed one rather than skipping it.

    A silently skipped case is a hole in a benchmark, which is the failure mode this whole
    repository is about.
    """
    directory = Path(directory)
    if not directory.exists():
        raise FileNotFoundError(f"no corpus directory at {directory}")
    cases: list[Case] = []
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        missing = {"id", "kind", "label", "expected_rules", "source", "fixture"} - set(payload)
        if missing:
            raise ValueError(f"{path.name} is missing {sorted(missing)}")
        if payload["kind"] not in {"tool-list", "code"}:
            raise ValueError(f"{path.name}: unknown kind {payload['kind']!r}")
        if payload["label"] not in {"positive", "negative"}:
            raise ValueError(f"{path.name}: unknown label {payload['label']!r}")
        if not str(payload["source"]).strip():
            raise ValueError(f"{path.name}: a case needs a source for its pattern")
        if payload["label"] == "positive" and not payload["expected_rules"]:
            raise ValueError(f"{path.name}: a positive case needs at least one expected rule")
        if payload["label"] == "negative" and payload["expected_rules"]:
            raise ValueError(f"{path.name}: a negative case expects no findings")
        cases.append(Case(
            id=payload["id"], kind=payload["kind"], label=payload["label"],
            expected_rules=list(payload["expected_rules"]), source=str(payload["source"]),
            fixture=payload["fixture"], path=path,
        ))
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids in the corpus")
    return cases


def materialise(case: Case, workdir: Path) -> Path:
    """Write a case's fixture to disk in the shape the detector expects."""
    if case.kind == "tool-list":
        target = workdir / "tools.json"
        target.write_text(json.dumps(case.fixture, indent=2), encoding="utf-8")
        return target
    target = workdir / "source"
    target.mkdir(parents=True, exist_ok=True)
    for name, content in (case.fixture.get("files") or {}).items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return target


def run_detector(command: str, target: Path, timeout: float = 60.0) -> tuple[set[str], str]:
    """Run a detector command and read the rules it reported.

    The command is a template with `{input}`. A detector that crashes, times out or prints
    something unparseable is recorded as an error on the case — never as "found nothing",
    which would silently turn a broken detector into a perfect one on negative cases.
    """
    argv = command.replace("{input}", str(target))
    try:
        completed = subprocess.run(
            argv, shell=True, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return set(), f"detector timed out after {timeout:.0f}s"
    if completed.returncode not in (0, 1):
        # 1 is the conventional "findings present" exit code; anything else is a crash.
        return set(), f"detector exited {completed.returncode}: {completed.stderr.strip()[:200]}"
    text = completed.stdout.strip()
    if not text:
        return set(), "detector printed nothing"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return set(), f"detector output is not JSON: {exc}"

    # Accept both shapes a detector realistically prints: an object with a "findings" list,
    # or the bare list. My first version of this assumed the object, because that is what my
    # own detector emits — and the first real detector I pointed it at prints a list, which
    # is exactly the coupling a benchmark is supposed to avoid.
    if isinstance(payload, list):
        findings = payload
    elif isinstance(payload, dict):
        findings = payload.get("findings", [])
    else:
        return set(), f"detector output is a {type(payload).__name__}, not a list of findings"
    if not isinstance(findings, list):
        return set(), "detector 'findings' is not a list"

    rules = {str(f.get("rule")) for f in findings if isinstance(f, dict) and f.get("rule")}
    return rules, ""


def score(case: Case, found: set[str]) -> tuple[int, int, int, int]:
    """Confusion counts for one case, at the level of expected rules.

    * positive case: each expected rule found is a TP, each missed is an FN, and any
      *unexpected* rule is an FP — a detector that flags the right rule among five others
      has not been precise.
    * negative case: any finding at all is an FP; none is a TN.
    """
    expected = set(case.expected_rules)
    tp = fp = fn = tn = 0
    if case.is_positive:
        tp = len(expected & found)
        fn = len(expected - found)
        fp = len(found - expected)
    else:
        fp = len(found)
        tn = 0 if found else 1
    return tp, fp, fn, tn


def evaluate(cases: Iterable[Case], command: str) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for case in cases:
        with tempfile.TemporaryDirectory() as tmp:
            target = materialise(case, Path(tmp))
            found, error = run_detector(command, target)
        outcome = Outcome(case=case, found_rules=found, error=error)
        outcome.tp, outcome.fp, outcome.fn, outcome.tn = score(case, found)
        outcomes.append(outcome)
    return outcomes


def metrics(outcomes: list[Outcome]) -> dict[str, Any]:
    tp = sum(o.tp for o in outcomes)
    fp = sum(o.fp for o in outcomes)
    fn = sum(o.fn for o in outcomes)
    tn = sum(o.tn for o in outcomes)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "cases": len(outcomes),
        "passed": sum(1 for o in outcomes if o.passed),
        "errors": sum(1 for o in outcomes if o.error),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def report(outcomes: list[Outcome], detector: str) -> str:
    summary = metrics(outcomes)
    lines = [
        f"detector: {detector}",
        f"cases:    {summary['cases']}   passed: {summary['passed']}   errors: {summary['errors']}",
        f"tp={summary['tp']} fp={summary['fp']} fn={summary['fn']} tn={summary['tn']}",
        f"precision={summary['precision']:.3f}  recall={summary['recall']:.3f}  f1={summary['f1']:.3f}",
        "",
        "  case                                          label     result   rules",
        "  " + "-" * 86,
    ]
    for outcome in outcomes:
        result = "error" if outcome.error else ("pass" if outcome.passed else "FAIL")
        rules = ",".join(sorted(outcome.found_rules)) or "-"
        lines.append(
            f"  {outcome.case.id[:44]:<44} {outcome.case.label:<9} {result:<8} {rules[:30]}"
        )
        if outcome.error:
            lines.append(f"      error: {outcome.error}")
    failing = [o for o in outcomes if not o.passed]
    if failing:
        lines.append("")
        lines.append("  failures")
        for outcome in failing:
            missing = sorted(set(outcome.case.expected_rules) - outcome.found_rules)
            extra = sorted(outcome.found_rules - set(outcome.case.expected_rules))
            detail = []
            if missing:
                detail.append(f"missed {missing}")
            if extra:
                detail.append(f"unexpected {extra}")
            lines.append(f"    {outcome.case.id}: " + "; ".join(detail or [outcome.error]))
    lines += ["", SELF_AUTHORED_CAVEAT]
    return "\n".join(lines)
