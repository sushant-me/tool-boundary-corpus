# tool-boundary-corpus

**A labelled corpus of agent tool-boundary cases, and a harness that scores any detector
against it.** Precision and recall only exist if somebody wrote the cases down.

```bash
python3 -m corpus.cli validate
python3 -m corpus.cli list --kind tool-list
python3 -m corpus.cli run --kind code \
  --detector 'agentbound scan {input} --json'
python3 -m corpus.cli run --kind tool-list \
  --detector 'python3 -m mcpaudit.cli audit {input} --json --no-colour'
```

A detector is a command that reads an input path and prints findings — an object with a
`findings` list, **or a bare list** (both are accepted, because the first real detector I
pointed this at prints a list and my harness initially assumed my own shape). A crash, a
timeout, a non-JSON reply or a non-`0/1` exit is recorded as an **error on the case**, never
as "found nothing" — otherwise a broken detector would score perfectly on the negatives.

## Measured, on the cases in this repository

| detector | kind | cases | precision | recall | F1 |
|---|---|---:|---:|---:|---:|
| [`mcpaudit`](https://github.com/sushant-me/mcpaudit) v0.1.1 | tool-list (14) | 14 | **1.000** | **1.000** | 1.000 |
| [`agentbound`](https://github.com/sushant-me/agentbound) v0.1.11 | code (5) | 5 | **1.000** | **1.000** | 1.000 |

Reproduce either row with the commands above; `--json` gives the per-case breakdown. Both
rows name the build they were measured from, and the test suite refuses to report a number
for a different one — a stale `agentbound` on `PATH` once produced precision **0.750** here,
which is the number this repository exists to prove was fixed, so a version mismatch was
indistinguishable from a real regression.

> ### Read this before quoting those numbers
>
> **The corpus was written by the author of both detectors.** A self-authored corpus
> overstates performance, and every report the tool prints says so. Treat these as a
> regression gate, not an independent benchmark. What partly mitigates it: positive cases
> cite where the pattern came from in the real world, the negatives are the *correct*
> implementation of the same job (which is where precision is actually decided), and the
> test suite scores fake detectors whose answers are known — a perfect one, a silent one, a
> trigger-happy one, a crashing one — so a harness that flatters every input fails its own
> tests.

## The false positive this corpus found — and the fix it caused

`agentbound` first measured **precision 0.750** here, and the miss was real rather than a
fixture artefact. Case `code-pattern-only-in-comments` is a file where the vulnerable
pattern appears **only inside a triple-quoted string bound to a name**:

```python
# Never do this: self.tools_dict[tool.name] = tool after a warning.
# Also never ship _RESERVED_TOOL_NAMES = frozenset() missing set_model_response.
DOCSTRING = """
    if tool.name in self.tools_dict:
        logger.warning("duplicate")
    self.tools_dict[tool.name] = tool
"""
```

It was reported at line 6, inside that string — and the two comment lines above it are
themselves the fixture for an earlier fix, in which the same rule matched the comment block
documenting the idiom. The detector's masking already treated a bare string statement (a
docstring) as prose, and deliberately kept string *arguments* visible because
`tool-dict-last-wins` reads the logging message as evidence — a string assigned to a name fell
between the two, so documentation was scanned as code.

**This corpus kept the case failing and named it in CI** (`--known-failure
code-pattern-only-in-comments`) rather than deleting the case or ignoring the failure. That
entry did its job: [agentbound v0.1.10](https://github.com/sushant-me/agentbound/releases/tag/v0.1.10)
extends the prose classifier to assignment values, this build went red with *"now passes —
remove it from --known-failure"*, and the entry was removed with the gate tightened from a
recall floor to precision **and** recall.

| check | before | after |
|---|---|---|
| precision on this corpus | 0.750 | **1.000** |
| recall on this corpus | 1.000 | 1.000 |
| `agentbound scan agentbound` (its CI contract) | exit 0 | exit 0 |
| repository-root scan (fixtures on purpose) | 25 findings | 16 |
| agentbound's own tests | 111 | 114 |

A benchmark that cannot report a bad number is not a benchmark, and a regression that can be
deleted is not a regression. This one was reported, named, fixed, re-measured, and is now
guarded at the stricter threshold.

**A second defect came out of writing the guard for this one.** The score is measured from an
installed release, so the test names the release it expects. Adding that check required
reading `agentbound --version` — which printed **0.1.9** on the v0.1.10 release, because
`pyproject.toml` carried the bump and `agentbound/__init__.py` did not and nothing compared
them. A consumer pinning that project by release could not tell which build it had, which is
this repository's entire method. v0.1.10 is immutable, so
[agentbound v0.1.11](https://github.com/sushant-me/agentbound/releases/tag/v0.1.11) carries
the fix and the test that pins the two version strings together; the pin here moved with it.
The corpus is measured against v0.1.11 and the numbers above are unchanged.

## The cases

**19 cases: 14 tool-list declarations, 5 code.** Each is a JSON file with an id, kind,
label, the rules a detector is expected to report, the *source* of the pattern, and the
fixture.

| kind | positives (must be flagged) | negatives (must stay clean) |
|---|---|---|
| `tool-list` | reserved-name collision, instruction-carrying description, invisible tag-block payload, look-alike names, a destructive tool declaring `readOnlyHint`, missing annotations, unconstrained execution sink, duplicate name, empty description | a well-formed read-only server, a description that merely *mentions* a reserved name, the same shell tool with an `enum`-constrained parameter, a destructive tool that says so, and a server of readers whose names carry mutation words as substrings — `get_runbook`, `list_postgres_instances`, `get_updates`, `read_writer_stats`, `get_grant_balance` |
| `code` | a reserved set missing a framework-owned tool, a registry that logs a duplicate and then overwrites, a confirmation gate that fails open by signature filtering | the same registry with a real guard, and the anti-pattern present only in comments and a docstring |

Every positive cites where the pattern comes from: the Google ADK pull requests, MCP
annotation semantics, the Unicode tag block (ASCII smuggling), homoglyph naming, and one
case taken from a bug in `agentbound` itself.

## Using it on a detector I have not seen

Write an adapter — a command that reads a path and prints findings — and run it. There is
nothing in the harness that knows about either detector, which is the point: a number
produced here can be compared with one produced next year, or by somebody else, on a
detector that does not exist yet.

```bash
python3 -m corpus.cli run --kind tool-list \
  --detector 'your-scanner --format json {input}' --min-precision 0.9 --min-recall 0.9
```

`--min-precision` / `--min-recall` turn the numbers into a gate; exit codes are `0` all
cases passed, `1` a case failed or a floor was missed, `2` the corpus or detector could not
be used. `--kind` is required in practice because scoring a declaration scanner on
framework source reports recall 0 for a tool doing its job — a number is only meaningful
for the inputs a detector claims to handle.

## Status

`v0.1.0`, stdlib only, 27 tests (the harness's own behaviour is tested with fake detectors),
CI on 3.11/3.12/3.13 with both detectors installed at pinned revisions — `mcpaudit` at a
commit, `agentbound` at v0.1.11 — and both rows above re-measured. `CORPUS_REQUIRE_DETECTORS=1`
is set there, so a detector that fails to install or does not match its pin fails the run
instead of skipping past it.
