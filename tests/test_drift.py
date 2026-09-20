"""Tests for the drift cases and the adapter that scores them.

The drift cases exist because a lock file is the only defence against a rug pull, and a
single-payload fixture cannot express one: it takes two states of the same server to see a
change at all. That makes the adapter part of the measurement, exactly as the stdio shim is
in `test_adapters.py` — so it gets tested the same way.

These tests were written after the first version of the adapter scored `mcpaudit` at recall
0.0 on cases it was in fact catching. `mcpaudit` prints drift under a separate top-level
`drift` list keyed by `kind`; the adapter read only `findings`, keyed by `rule`, and dropped
the rest. Nothing failed, because nothing asserted that the adapter read the same keys the
detector writes. The fake detector below pins that contract without needing the real tool
installed.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from corpus import harness

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapters" / "mcpaudit_drift_adapter.py"
CASES = ROOT / "cases"

# A stand-in for `python -m mcpaudit.cli`. It answers the two invocations the adapter makes
# -- `--write-lock` from the approved payload, then `--lock` against the current one -- and
# reports whatever payload the test tells it to, so the adapter's parsing is tested against
# the shape the real tool emits rather than against a paraphrase of it.
FAKE_CLI = '''
import json, os, pathlib, sys

argv = sys.argv[1:]
source = argv[argv.index("audit") + 1]
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"source": source, "argv": argv}) + "\\n")

if "--write-lock" in argv:
    target = pathlib.Path(argv[argv.index("--write-lock") + 1])
    target.write_text(json.dumps({"locked": source}), encoding="utf-8")
    sys.exit(0)

print(os.environ.get("FAKE_PAYLOAD", "{}"))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'''


def drift_cases() -> list[harness.Case]:
    return [c for c in harness.load_cases(CASES) if c.kind == "tool-list-drift"]


def run_adapter(directory: pathlib.Path, *, payload="{}", exit_code=0,
                with_log=False) -> tuple[subprocess.CompletedProcess, list[dict]]:
    """Run the real adapter against the fake detector, in an isolated PYTHONPATH."""
    sandbox = directory / "_fake"
    (sandbox / "mcpaudit").mkdir(parents=True, exist_ok=True)
    (sandbox / "mcpaudit" / "__init__.py").write_text("", encoding="utf-8")
    (sandbox / "mcpaudit" / "cli.py").write_text(FAKE_CLI, encoding="utf-8")

    env = dict(os.environ)
    # Prepend, not replace: the adapter's own imports must keep working.
    env["PYTHONPATH"] = os.pathsep.join([str(sandbox), env.get("PYTHONPATH", "")])
    env["FAKE_PAYLOAD"] = payload
    env["FAKE_EXIT"] = str(exit_code)
    log = directory / "_calls.jsonl"
    if with_log:
        log.write_text("", encoding="utf-8")
        env["FAKE_LOG"] = str(log)

    completed = subprocess.run([sys.executable, str(ADAPTER), str(directory)],
                               capture_output=True, text=True, timeout=120,
                               check=False, env=env)
    calls = []
    if with_log and log.exists():
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return completed, calls


def materialise(case_id: str, tmp: pathlib.Path) -> pathlib.Path:
    cases = {c.id: c for c in harness.load_cases(CASES)}
    directory = tmp / case_id
    directory.mkdir(parents=True, exist_ok=True)
    return harness.materialise(cases[case_id], directory)


def detector(tmp_path: pathlib.Path, payload: dict) -> str:
    """A detector command that prints `payload`, as a file.

    The harness runs detector commands through a shell, so an inline `-c "print(...)"`
    containing JSON quotes is a shell-quoting problem waiting to be mistaken for a detector
    bug — which is how the first version of these tests failed.
    """
    script = tmp_path / "detector.py"
    script.write_text(
        f"import json\nprint(json.dumps({payload!r}))\n", encoding="utf-8")
    return f"{sys.executable} {script}"


# --- the cases themselves ------------------------------------------------------------


def test_the_corpus_carries_the_drift_class() -> None:
    cases = drift_cases()
    assert len(cases) == 4
    positives = [c for c in cases if c.is_positive]
    negatives = [c for c in cases if not c.is_positive]
    assert len(positives) == 3 and len(negatives) == 1


def test_every_drift_case_declares_both_states(tmp_path: pathlib.Path) -> None:
    """A drift case with one state is unscoreable, and would read as a pass."""
    for case in drift_cases():
        assert {"approved", "current"} <= set(case.fixture), case.id
        assert case.fixture["approved"].get("tools"), case.id
        assert case.fixture["current"].get("tools"), case.id


def test_the_negative_control_is_byte_identical_between_states() -> None:
    """The control only controls if nothing changed. Otherwise it is a second positive."""
    case = next(c for c in drift_cases() if c.id == "tools-drift-none")
    assert case.fixture["approved"] == case.fixture["current"]


def test_the_poisoned_case_declares_both_rules_it_trips() -> None:
    """Detecting that a declaration changed is not detecting what it changed into."""
    case = next(c for c in drift_cases() if c.id == "tools-drift-description-poisoned")
    assert set(case.expected_rules) == {"description-changed", "instruction-in-declaration"}


def test_a_detector_that_finds_nothing_fails_the_positive_cases(tmp_path: pathlib.Path) -> None:
    """The mutation that proves the cases are not vacuous.

    If an empty detector passed them, the whole class would be decorative.
    """
    outcomes = harness.evaluate(drift_cases(), detector(tmp_path, {}))
    positives = [o for o in outcomes if o.case.is_positive]
    assert all(not o.passed for o in positives), [o.case.id for o in positives]
    assert all(o.fn for o in positives)


def test_a_detector_that_flags_everything_fails_the_negative_control(
        tmp_path: pathlib.Path) -> None:
    outcomes = harness.evaluate(
        drift_cases(),
        detector(tmp_path, {"findings": [{"rule": "description-changed"}]}))
    control = next(o for o in outcomes if not o.case.is_positive)
    assert not control.passed and control.fp == 1


def test_a_detector_reporting_only_the_drift_rule_passes_the_case(
        tmp_path: pathlib.Path) -> None:
    """The corpus must not require a detector to also re-report the current payload.

    The harness reads `findings[].rule` and nothing else; translating a detector's own
    output shape into that contract is the adapter's job, which the fake-detector tests
    below cover. What this pins is the corpus's own claim: the isolated drift case is
    satisfiable by the drift rule alone, with no ordinary finding alongside it.
    """
    command = detector(tmp_path, {"findings": [{"rule": "description-changed"}]})
    outcomes = harness.evaluate(
        [c for c in drift_cases() if c.id == "tools-drift-description-changed"], command)
    assert outcomes[0].passed, outcomes[0].to_dict()


# --- the adapter ---------------------------------------------------------------------


def test_the_adapter_reads_drift_entries_not_only_findings(tmp_path: pathlib.Path) -> None:
    """The regression test for the bug that produced recall 0.0 on a working detector."""
    directory = materialise("tools-drift-description-changed", tmp_path)
    payload = json.dumps({"findings": [],
                          "drift": [{"kind": "description-changed", "tool": "get_cost_report"}]})
    completed, _ = run_adapter(directory, payload=payload)
    assert completed.returncode == 0, completed.stderr
    rules = {f["rule"] for f in json.loads(completed.stdout)["findings"]}
    assert rules == {"description-changed"}


def test_the_adapter_reports_ordinary_findings_as_well(tmp_path: pathlib.Path) -> None:
    """No filtering: a detector that says two things is scored on both."""
    directory = materialise("tools-drift-description-poisoned", tmp_path)
    payload = json.dumps({
        "findings": [{"rule": "instruction-in-declaration", "tool": "get_cost_report"}],
        "drift": [{"kind": "description-changed", "tool": "get_cost_report"}],
    })
    completed, _ = run_adapter(directory, payload=payload)
    rules = {f["rule"] for f in json.loads(completed.stdout)["findings"]}
    assert rules == {"description-changed", "instruction-in-declaration"}


def test_the_adapter_locks_the_approved_state_and_compares_the_current_one(
        tmp_path: pathlib.Path) -> None:
    """The ordering is the case.

    Recording the lock from `current.json` would make every drift case pass by
    construction, which is the one way to score 1.000 on this class and detect nothing.
    """
    directory = materialise("tools-drift-description-changed", tmp_path)
    completed, calls = run_adapter(directory, payload='{"drift": []}', with_log=True)
    assert completed.returncode == 0, completed.stderr
    assert len(calls) == 2, calls

    recorded, compared = calls
    assert recorded["source"].endswith("approved.json")
    assert "--write-lock" in recorded["argv"]
    assert compared["source"].endswith("current.json")
    assert "--lock" in compared["argv"]


def test_the_adapter_fails_closed_when_a_state_is_missing(tmp_path: pathlib.Path) -> None:
    """Exit 2, so the harness records an error rather than 'found nothing'."""
    directory = tmp_path / "half"
    directory.mkdir()
    (directory / "approved.json").write_text('{"tools": []}', encoding="utf-8")
    completed, _ = run_adapter(directory)
    assert completed.returncode == 2
    assert "current.json" in completed.stderr


def test_the_adapter_fails_closed_when_the_audit_crashes(tmp_path: pathlib.Path) -> None:
    directory = materialise("tools-drift-none", tmp_path)
    completed, _ = run_adapter(directory, payload="", exit_code=3)
    assert completed.returncode == 2, completed.stdout


def test_the_adapter_reports_nothing_for_an_unchanged_server(tmp_path: pathlib.Path) -> None:
    directory = materialise("tools-drift-none", tmp_path)
    completed, _ = run_adapter(directory, payload='{"findings": [], "drift": []}')
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["findings"] == []


def test_an_unmapped_drift_kind_is_reported_rather_than_dropped(
        tmp_path: pathlib.Path) -> None:
    """A detector that adds a class must not have it silently discarded by the shim."""
    directory = materialise("tools-drift-none", tmp_path)
    completed, _ = run_adapter(
        directory, payload='{"drift": [{"kind": "transport-changed"}]}')
    rules = {f["rule"] for f in json.loads(completed.stdout)["findings"]}
    assert rules == {"transport-changed"}


def test_a_drift_case_missing_a_state_is_refused(tmp_path: pathlib.Path) -> None:
    """The guard that stops an unscoreable case existing.

    A drift fixture with one state has nothing to compare, so a detector would be scored
    against an empty diff and every such case would read as a pass.
    """
    payload = {
        "id": "half", "kind": "tool-list-drift", "label": "negative", "expected_rules": [],
        "source": "a real source for this case", "fixture": {"approved": {"tools": []}},
    }
    (tmp_path / "half.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="current"):
        harness.load_cases(tmp_path)


def test_a_drift_case_with_neither_state_is_refused(tmp_path: pathlib.Path) -> None:
    payload = {
        "id": "none", "kind": "tool-list-drift", "label": "negative", "expected_rules": [],
        "source": "a real source for this case", "fixture": {},
    }
    (tmp_path / "none.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="approved"):
        harness.load_cases(tmp_path)


def test_materialise_gives_a_drift_detector_a_directory_of_two_states(
        tmp_path: pathlib.Path) -> None:
    """How a detector records an approval is its business; the corpus ships both states."""
    directory = materialise("tools-drift-description-changed", tmp_path)
    assert directory.is_dir()
    assert sorted(p.name for p in directory.iterdir()) == ["approved.json", "current.json"]


@pytest.mark.parametrize("case_id", [c.id for c in drift_cases()])
def test_every_drift_case_survives_a_round_trip_through_the_adapter(
        case_id: str, tmp_path: pathlib.Path) -> None:
    """Materialising a case must produce readable JSON for both states."""
    directory = materialise(case_id, tmp_path)
    for name in ("approved.json", "current.json"):
        payload = json.loads((directory / name).read_text(encoding="utf-8"))
        assert isinstance(payload.get("tools"), list), name


# --- the README's own code fences -----------------------------------------------------


def test_the_readme_fences_are_balanced_and_enclose_only_code() -> None:
    """A guard found by reading the rendered page rather than the source.

    Editing this README left an empty fence where a reproduce command belonged, and a
    later `pip install` line glued onto the end of a sentence (`...the way I did.bash`).
    Both are invisible in a diff and one of them rendered a `###` section heading as
    literal code. Documentation that describes a benchmark should not be the part nobody
    checks.
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    inside = False
    inside_fence_headings = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("```"):
            inside = not inside
            continue
        # Two or more hashes, so that a `#` comment inside a fenced Python sample is not
        # mistaken for a heading — which is how the first version of this check failed.
        if inside and re.match(r"^#{2,6} ", line):
            inside_fence_headings.append((number, line))

    assert not inside, "a code fence is never closed"
    assert not inside_fence_headings, f"headings rendered as code: {inside_fence_headings}"
    # A fence marker glued to prose is the other half of the same corruption.
    assert ".bash" not in text.replace("```bash", ""), "a fence marker is glued to text"
