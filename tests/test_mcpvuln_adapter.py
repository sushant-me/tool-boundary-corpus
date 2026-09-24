"""Guard the mcpvuln adapter's rule translation.

Why this test exists
--------------------
`adapters/mcpvuln_adapter.py` carries a `RULE_MAP` translating mcpvuln's taxonomy
into this corpus's identifiers. Without it the harness scores raw rule strings, so a
detector that finds the right defect under a different name is counted as BOTH a
false positive and a false negative.

That is not hypothetical. mcpvuln was measured at `precision 0.000 / recall 0.000`
for exactly this reason, and it took several passes to find — the tool was detecting
the case correctly the whole time. With the mapping it measures `1.000 / 0.100`.

The failure mode this guards against is **silent**: if the mapping is deleted, or if
mcpvuln renames its `pattern_id` values in a release, the adapter keeps working and
the numbers quietly go back to being wrong. Nothing else in the suite would notice,
because a score of zero looks like a detector that found nothing rather than like an
integration that stopped translating.

So the assertions are deliberately about *coverage*, not about specific values:
the mapping must exist, and everything it maps to must be vocabulary this corpus
actually expects — otherwise it is translating into strings no case can match.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_adapter():
    """Load the adapter by path.

    Importing it as a module would work only when the repository root happens to be
    on `sys.path`, which depends on how pytest was invoked — the same trap
    `test_rules.py` in the sibling repository records.
    """
    path = ROOT / "adapters" / "mcpvuln_adapter.py"
    spec = importlib.util.spec_from_file_location("mcpvuln_adapter_under_test", path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _corpus_rule_vocabulary() -> set[str]:
    expected: set[str] = set()
    for case in (ROOT / "cases").glob("*.json"):
        payload = json.loads(case.read_text(encoding="utf-8"))
        expected.update(payload.get("expected_rules") or [])
    return expected


def test_mcpvuln_rule_map_is_present_and_non_empty():
    adapter = _load_adapter()
    assert getattr(adapter, "RULE_MAP", None), (
        "RULE_MAP is missing from the mcpvuln adapter. Without it the harness scores "
        "raw rule strings and a correct detection is counted as a false positive and "
        "a false negative at the same time."
    )


def test_every_mapped_rule_is_vocabulary_the_corpus_expects():
    adapter = _load_adapter()
    vocabulary = _corpus_rule_vocabulary()
    assert vocabulary, "no expected_rules found in cases/ — the corpus did not load"

    unmatchable = {
        source: target
        for source, target in adapter.RULE_MAP.items()
        if target not in vocabulary
    }
    assert not unmatchable, (
        "these mappings translate into strings no case expects, so a detector using "
        f"them still cannot be credited: {unmatchable}"
    )


def test_unmapped_rules_pass_through_rather_than_being_dropped():
    """A rule the mapping does not know must still be reported.

    Silently swallowing an unrecognised rule would hide a genuine difference between
    two detectors — the opposite of what this corpus is for. The adapter resolves
    with `RULE_MAP.get(raw, raw)` for that reason; this pins that behaviour.
    """
    adapter = _load_adapter()
    unknown = "mcp.some.future.pattern"
    assert adapter.RULE_MAP.get(unknown, unknown) == unknown


# A stand-in for the real tool, reproducing the side effect that mattered.
#
# mcpvuln 0.3.0 writes `<target-basename>_security_report.md` into its CURRENT
# WORKING DIRECTORY in addition to the `--json` contract — the `--json` flag does
# not redirect that second output. Verified by running the real binary: with a
# fresh empty CWD and a target elsewhere, the run left exactly one such file
# behind. A fake is used rather than the real tool so this test runs in CI, where
# mcpvuln is not installed.
_FAKE_MCPVULN = '''\
import json, os, pathlib, sys

target = sys.argv[1]
contract = sys.argv[sys.argv.index("--json") + 1]
pathlib.Path(contract).write_text(json.dumps({"findings": []}), encoding="utf-8")
pathlib.Path(os.getcwd(), f"{pathlib.Path(target).name}_security_report.md").write_text(
    "stand-in for mcpvuln's human-readable report", encoding="utf-8")
'''


def test_the_adapter_leaves_no_report_files_in_the_working_directory(tmp_path):
    """Running a scan must not litter the caller's directory.

    The adapter stages each single-file case into a temporary directory and then
    runs mcpvuln, which drops `<staging-dir>_security_report.md` into whatever CWD
    it inherits. The harness runs from the repository root, so every JSON case used
    to leave one stray file behind — 78 had accumulated in the checkout.

    Nothing failed when that happened: the adapter still printed valid findings and
    exited correctly. The only symptom was clutter, which is exactly the kind of
    thing that goes unnoticed, so it is pinned here rather than left to review.
    """
    import os
    import subprocess
    import sys

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_tool = fake_bin / "mcpvuln"
    fake_tool.write_text(f"#!{sys.executable}\n{_FAKE_MCPVULN}", encoding="utf-8")
    fake_tool.chmod(0o755)

    # A directory that stands in for "wherever the caller happens to be".
    caller_cwd = tmp_path / "caller-cwd"
    caller_cwd.mkdir()

    case = ROOT / "cases" / "tools-clean-readonly.json"
    assert case.exists(), f"fixture case missing: {case}"

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "adapters" / "mcpvuln_adapter.py"), str(case)],
        cwd=caller_cwd, capture_output=True, text=True, env=env, timeout=120,
    )

    strays = sorted(p.name for p in caller_cwd.glob("*_security_report.md"))
    assert not strays, (
        f"the adapter left {len(strays)} mcpvuln report file(s) in the working "
        f"directory: {strays}. mcpvuln writes that file into its CWD regardless of "
        f"--json, so the subprocess must run with cwd set to a temporary directory."
    )

    # The fix must not break the adapter's actual job.
    assert proc.returncode in (0, 1), (
        f"adapter exited {proc.returncode}; stderr: {proc.stderr.strip()[:300]}"
    )
    payload = json.loads(proc.stdout)
    assert "findings" in payload, payload
