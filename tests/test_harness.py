"""Tests for the harness, using fake detectors whose scores are known in advance.

A benchmark is only worth something if it can report a *bad* number. So the tests here do
not only check that the real detectors score well — they check that the harness gives the
right answer for detectors that are perfect, silent, trigger-happy, crashing, or outputting
the wrong JSON shape. A harness that flatters every input would fail this file.

The corpus's own shape is tested too: malformed cases must be refused rather than skipped,
because a silently skipped case is a hole in a benchmark.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from corpus import evaluate, load_cases, materialise, metrics, score  # noqa: E402
from corpus.harness import Case, run_detector  # noqa: E402

CORPUS = ROOT / "cases"


def write_detector(tmp_path: Path, body: str) -> str:
    """Write a fake detector and return its command template."""
    path = tmp_path / "detector.py"
    path.write_text(body, encoding="utf-8")
    return f"{sys.executable} {path} {{input}}"


def _require_or_skip(reason: str) -> None:
    """Skip, unless the environment says a skip is not acceptable.

    CI installs both detectors at pinned commits and sets
    `CORPUS_REQUIRE_DETECTORS=1`, so a missing or mismatched detector is an error
    there. Without it, a broken install would silently reduce the benchmark to
    zero cases - the same failure mode the corpus refuses everywhere else.
    """
    if os.environ.get("CORPUS_REQUIRE_DETECTORS") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


def _installed_commit(distribution: str) -> str | None:
    """The commit a pip VCS install records in PEP 610 `direct_url.json`."""
    import importlib.metadata as metadata

    try:
        raw = metadata.distribution(distribution).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw).get("vcs_info", {}).get("commit_id")
    except json.JSONDecodeError:
        return None


PERFECT = """
import json, sys, pathlib
target = pathlib.Path(sys.argv[1])
text = target.read_text() if target.is_file() else "".join(
    p.read_text() for p in target.rglob("*") if p.is_file())
rules = []
if "set_model_response" in text and "tool-list" not in str(target):
    rules.append("tool-reserved-name-shadowing")
if "tools_dict[tool.name] = tool" in text and "DOCSTRING" not in text:
    rules.append("tool-dict-last-wins")
print(json.dumps({"findings": [{"rule": r} for r in rules]}))
"""


# -- the harness must be able to report bad numbers --------------------------


def test_a_perfect_detector_scores_one(tmp_path) -> None:
    command = write_detector(tmp_path, """
import json, sys, pathlib
payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
rules = ["reserved-name-collision"] if any(
    t.get("name") == "set_model_response" for t in payload.get("tools", [])) else []
print(json.dumps({"findings": [{"rule": r} for r in rules]}))
""")
    cases = [c for c in load_cases(CORPUS) if c.id == "tools-reserved-name-collision"]
    summary = metrics(evaluate(cases, command))
    assert summary["precision"] == 1.0
    assert summary["recall"] == 1.0


def test_a_silent_detector_scores_recall_zero(tmp_path) -> None:
    command = write_detector(tmp_path, 'import json\nprint(json.dumps({"findings": []}))\n')
    cases = [c for c in load_cases(CORPUS) if c.label == "positive"]
    summary = metrics(evaluate(cases, command))
    assert summary["tp"] == 0
    assert summary["recall"] == 0.0
    assert summary["fn"] > 0


def test_a_trigger_happy_detector_scores_precision_below_one(tmp_path) -> None:
    """Flags everything with every rule: high recall, and precision that shows the cost."""
    command = write_detector(tmp_path, """
import json
print(json.dumps({"findings": [{"rule": "reserved-name-collision"},
                               {"rule": "tool-dict-last-wins"},
                               {"rule": "something-else"}]}))
""")
    summary = metrics(evaluate(load_cases(CORPUS), command))
    # It cannot have full recall — it only knows three rule names — but the point of the
    # test is that its precision is visibly imperfect rather than rounded to 1.0.
    assert summary["precision"] < 1.0
    assert summary["fp"] > 0


def test_a_crashing_detector_is_an_error_not_a_clean_result(tmp_path) -> None:
    """The failure mode that would let a broken detector score perfectly on negatives."""
    command = write_detector(tmp_path, "import sys\nsys.exit(3)\n")
    outcomes = evaluate(load_cases(CORPUS), command)
    assert all(o.error for o in outcomes)
    assert all(not o.passed for o in outcomes)
    assert metrics(outcomes)["errors"] == len(outcomes)


def test_a_non_json_detector_is_an_error(tmp_path) -> None:
    command = write_detector(tmp_path, "print('all clear!')\n")
    outcomes = evaluate(load_cases(CORPUS), command)
    assert all("not JSON" in o.error for o in outcomes)


def test_a_bare_list_of_findings_is_accepted(tmp_path) -> None:
    """The shape agentbound actually prints — and the coupling my first version had."""
    case = Case(id="c", kind="tool-list", label="positive",
                expected_rules=["reserved-name-collision"], source="test",
                fixture={"tools": [{"name": "x"}]})
    with tempfile_dir() as tmp:
        target = materialise(case, Path(tmp))
        command = write_detector(Path(tmp), """
import json
print(json.dumps([{"rule": "reserved-name-collision", "path": "x"}]))
""")
        found, error = run_detector(command, target)
    assert error == ""
    assert found == {"reserved-name-collision"}


class tempfile_dir:
    """Tiny context manager so the bare-list test does not import tempfile twice."""

    def __enter__(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        return self._tmp.__enter__()

    def __exit__(self, *exc):
        return self._tmp.__exit__(*exc)


def test_an_object_with_a_findings_key_is_accepted(tmp_path) -> None:
    case = Case(id="c", kind="tool-list", label="positive",
                expected_rules=["undocumented-tool"], source="test",
                fixture={"tools": [{"name": "x"}]})
    with tempfile_dir() as tmp:
        target = materialise(case, Path(tmp))
        command = write_detector(Path(tmp), """
import json
print(json.dumps({"findings": [{"rule": "undocumented-tool"}]}))
""")
        found, error = run_detector(command, target)
    assert error == "" and found == {"undocumented-tool"}


# -- scoring semantics -------------------------------------------------------


def positive(rules, expected):
    return Case(id="c", kind="tool-list", label="positive", expected_rules=expected,
                source="test", fixture={}), rules


def negative(rules):
    return Case(id="c", kind="tool-list", label="negative", expected_rules=[],
                source="test", fixture={}), rules


def test_scoring_a_positive_case() -> None:
    case, found = positive(["a"], ["a"])
    assert score(case, set(found)) == (1, 0, 0, 0)

    case, found = positive(["b"], ["a"])
    # Missed the expected rule (fn) and reported an unexpected one (fp) — my first version
    # of this assertion expected (0, 1, 0, 0), which would have let a wrong-rule detector
    # look like a partial success.
    assert score(case, set(found)) == (0, 1, 1, 0)

    case, found = positive(["a", "b"], ["a"])
    assert score(case, set(found)) == (1, 1, 0, 0)   # right rule among unexpected ones


def test_scoring_a_negative_case() -> None:
    case, found = negative([])
    assert score(case, set(found)) == (0, 0, 0, 1)

    case, found = negative(["anything"])
    assert score(case, set(found)) == (0, 1, 0, 0)


def test_a_case_with_no_expected_rules_on_a_positive_is_refused(tmp_path) -> None:
    (tmp_path / "bad.json").write_text(json.dumps({
        "id": "x", "kind": "tool-list", "label": "positive", "expected_rules": [],
        "source": "s", "fixture": {}}))
    with pytest.raises(ValueError, match="at least one expected rule"):
        load_cases(tmp_path)


# -- the corpus itself -------------------------------------------------------


def test_the_real_corpus_loads_and_is_balanced() -> None:
    cases = load_cases(CORPUS)
    assert len(cases) >= 16
    assert {c.kind for c in cases} == {"tool-list", "code", "tool-list-drift"}
    assert {c.label for c in cases} == {"positive", "negative"}
    positives = [c for c in cases if c.is_positive]
    negatives = [c for c in cases if not c.is_positive]
    assert len(negatives) >= 4, "precision needs negatives"
    assert all(c.source.strip() for c in cases)


def test_every_case_cites_a_source_that_is_not_a_placeholder() -> None:
    for case in load_cases(CORPUS):
        assert len(case.source) > 20, case.id
        assert "todo" not in case.source.lower(), case.id


def test_materialise_writes_a_tool_list_and_a_source_tree(tmp_path) -> None:
    cases = {c.id: c for c in load_cases(CORPUS)}
    tool_target = materialise(cases["tools-clean-readonly"], tmp_path)
    assert tool_target.is_file() and json.loads(tool_target.read_text())["tools"]

    code_target = materialise(cases["code-tools-dict-last-wins"], tmp_path / "two")
    assert code_target.is_dir()
    assert any(p.suffix == ".py" for p in code_target.rglob("*"))


def test_a_malformed_case_is_refused_rather_than_skipped(tmp_path) -> None:
    (tmp_path / "broken.json").write_text(json.dumps({"id": "x"}))
    with pytest.raises(ValueError, match="missing"):
        load_cases(tmp_path)


def test_duplicate_case_ids_are_refused(tmp_path) -> None:
    payload = {"id": "same", "kind": "tool-list", "label": "negative",
               "expected_rules": [], "source": "a real source for this case", "fixture": {}}
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(tmp_path)


def test_the_self_authored_caveat_is_printed_in_every_report() -> None:
    from corpus import SELF_AUTHORED_CAVEAT, report

    cases = load_cases(CORPUS)[:2]
    text = report([], "no-detector")
    assert SELF_AUTHORED_CAVEAT in text


# -- the measured numbers, as a regression gate ------------------------------
#
# Each row names the exact build it was measured from, and refuses to report a
# number for a different one. A stale detector on PATH previously produced
# precision 0.750 here - the number this repository exists to prove was fixed -
# so a mismatch was indistinguishable from a real regression.

PINNED_MCPAUDIT_COMMIT = "d5106b953db26c57f5471a41a2bb9fc98483cee5"


def test_mcpaudit_keeps_its_measured_scores_on_the_tool_list_cases() -> None:
    """A regression gate, with the caveat stated: this corpus is self-authored.

    The build is identified by the commit pip installed it from, not by its version
    string. mcpaudit is pinned to a commit rather than a release and its
    `__version__` is not bumped per commit, so the version cannot distinguish two
    builds - the commit can, and `direct_url.json` records it.
    """
    try:
        import mcpaudit  # noqa: F401
    except ImportError:
        _require_or_skip(
            "mcpaudit is not installed; CI installs it at a pinned commit "
            f"({PINNED_MCPAUDIT_COMMIT[:7]})"
        )
    commit = _installed_commit("mcpaudit")
    if commit != PINNED_MCPAUDIT_COMMIT:
        _require_or_skip(
            f"mcpaudit was installed from {commit or 'an unknown source'}, but this "
            f"repository measures {PINNED_MCPAUDIT_COMMIT}; install that commit to "
            f"reproduce the score"
        )

    cases = [c for c in load_cases(CORPUS) if c.kind == "tool-list"]
    command = f"{sys.executable} -m mcpaudit.cli audit {{input}} --json --no-colour"
    summary = metrics(evaluate(cases, command))
    assert summary["errors"] == 0
    assert summary["recall"] == 1.0, summary
    assert summary["precision"] == 1.0, summary


def test_the_installed_commit_is_read_from_the_distribution(tmp_path, monkeypatch) -> None:
    """The mechanism the check above rests on, tested without a network install.

    `pip install git+...@<sha>` writes PEP 610 `direct_url.json` into the
    distribution, so the commit is available locally. If that ever stops being
    true the check must say so rather than silently compare against nothing.
    """
    assert _installed_commit("a-distribution-that-does-not-exist") is None
    # A version check would compare "" to the pin and "pass" nothing; this cannot.
    assert _installed_commit("a-distribution-that-does-not-exist") != PINNED_MCPAUDIT_COMMIT


# -- --known-failure: named, visible, and unable to rot ----------------------


def run_cli(*args: str):
    import subprocess

    return subprocess.run(
        [sys.executable, "-m", "corpus.cli", *args],
        capture_output=True, text=True, cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin",
             "PYTHONDONTWRITEBYTECODE": "1"},
    )


def silent_detector(tmp_path) -> str:
    return write_detector(tmp_path, 'import json\nprint(json.dumps({"findings": []}))\n')


def test_a_known_failure_is_not_counted_and_the_run_passes(tmp_path) -> None:
    # A silent detector misses every positive, so all three have to be declared known for
    # the run to pass. My first version declared one and expected exit 0 — the unlisted two
    # failed it, which is the behaviour working correctly.
    command = silent_detector(tmp_path)
    result = run_cli("run", "--kind", "code", "--quiet-caveat", "--detector", command,
                     "--known-failure", "code-tools-dict-last-wins",
                     "--known-failure", "code-confirmation-fails-open",
                     "--known-failure", "code-reserved-name-set-incomplete")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("known failure (expected, not counted)") == 3


def test_an_unlisted_failure_still_fails_the_run(tmp_path) -> None:
    command = silent_detector(tmp_path)
    result = run_cli("run", "--kind", "code", "--quiet-caveat", "--detector", command)
    assert result.returncode == 1


def test_a_known_failure_that_starts_passing_is_an_error(tmp_path) -> None:
    """Otherwise the entry lingers, the case is never expected to fail again, and the
    benchmark quietly gets one case weaker."""
    command = write_detector(tmp_path, """
import json, sys, pathlib
target = pathlib.Path(sys.argv[1])
text = "".join(p.read_text() for p in target.rglob("*") if p.is_file())
rules = ["tool-dict-last-wins"] if "tools_dict[tool.name] = tool" in text else []
print(json.dumps({"findings": [{"rule": r} for r in rules]}))
""")
    result = run_cli("run", "--kind", "code", "--quiet-caveat", "--detector", command,
                     "--known-failure", "code-tools-dict-last-wins")
    assert result.returncode == 1
    assert "now passes" in result.stderr


def test_a_known_failure_naming_a_case_not_in_the_run_is_refused(tmp_path) -> None:
    command = silent_detector(tmp_path)
    result = run_cli("run", "--kind", "code", "--detector", command,
                     "--known-failure", "no-such-case")
    assert result.returncode == 2
    assert "not in this run" in result.stderr


def test_the_json_flag_outputs_the_metrics_and_the_caveat(tmp_path) -> None:
    import json as jsonlib

    command = silent_detector(tmp_path)
    result = run_cli("run", "--kind", "code", "--detector", command, "--json")
    payload = jsonlib.loads(result.stdout)
    assert payload["metrics"]["recall"] == 0.0
    assert "self-authored" in payload["caveat"].lower()
    assert len(payload["cases"]) == 5


# --- the detector has to be the build whose score is being asserted ---------
#
# A stale `agentbound` on PATH measured precision 0.750 here and the failure read
# exactly like the false positive coming back. It was 0.1.8: the bug, not the fix,
# and nothing in the test could tell the difference. So the version is checked
# before the score is, and CI - which installs the pinned release - fails rather
# than skips when the check cannot be made.

PINNED_AGENTBOUND = "0.1.12"


def _detector_version(binary: str) -> str:
    """The version a detector reports, or a placeholder if it cannot be read."""
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"<unreadable: {exc.__class__.__name__}>"
    if result.returncode != 0:
        return f"<exit {result.returncode}>"
    return result.stdout.strip() or "<empty>"


def test_agentbound_holds_its_measured_scores_on_the_code_cases() -> None:
    """The regression that this corpus found and agentbound v0.1.10 fixed.

    Before the fix this measured precision 0.750: `code-pattern-only-in-comments` was
    reported at a line inside a string bound to a name. The number is asserted here as
    well as in CI, so the improvement cannot quietly revert.

    The version is checked first. A different build makes this test meaningless in
    both directions: an older one reports the old number as though the fix had
    reverted, and a newer one asserts a score nobody measured.
    """
    import shutil

    binary = shutil.which("agentbound")
    if binary is None:
        _require_or_skip(
            "agentbound is not installed; CI installs it at a pinned release "
            f"(v{PINNED_AGENTBOUND})"
        )
    version = _detector_version(binary)
    if version != PINNED_AGENTBOUND:
        _require_or_skip(
            f"agentbound on PATH ({binary}) reports version {version}, but this "
            f"repository measures v{PINNED_AGENTBOUND}; install that release to "
            f"reproduce the score"
        )

    cases = [c for c in load_cases(CORPUS) if c.kind == "code"]
    summary = metrics(evaluate(cases, f"{binary} scan {{input}} --json"))
    assert summary["errors"] == 0, summary
    assert summary["recall"] == 1.0, summary
    assert summary["precision"] == 1.0, summary


def test_a_detector_at_the_wrong_version_is_refused_not_measured(tmp_path) -> None:
    """Regression: a stale agentbound 0.1.8 on PATH reported precision 0.750.

    That number is the one this repository exists to prove was fixed, so a bare
    mismatch is indistinguishable from a real regression in the output - which is
    how it was read the first time.
    """
    fake = tmp_path / "agentbound"
    fake.write_text("#!/bin/sh\necho 0.1.8\n", encoding="utf-8")
    fake.chmod(0o755)
    assert _detector_version(str(fake)) == "0.1.8"
    assert _detector_version(str(fake)) != PINNED_AGENTBOUND


def test_the_version_check_fails_closed_when_ci_asks_it_to(monkeypatch) -> None:
    """A skip is a hole unless CI can forbid it."""
    monkeypatch.setenv("CORPUS_REQUIRE_DETECTORS", "1")
    with pytest.raises(pytest.fail.Exception):
        _require_or_skip("no detector")


def test_a_detector_that_cannot_report_a_version_is_not_treated_as_matching(
    tmp_path,
) -> None:
    """Unreadable is not the same as correct, and must never read as a match."""
    broken = tmp_path / "agentbound"
    broken.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    broken.chmod(0o755)
    version = _detector_version(str(broken))
    assert version != PINNED_AGENTBOUND
    assert "3" in version, version

    missing = str(tmp_path / "not-a-binary")
    assert _detector_version(missing) != PINNED_AGENTBOUND
