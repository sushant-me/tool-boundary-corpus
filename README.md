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
| [`mcpaudit`](https://github.com/sushant-me/mcpaudit) | tool-list (13) | 13 | **1.000** | **1.000** | 1.000 |
| [`agentbound`](https://github.com/sushant-me/agentbound) | code (5) | 5 | **0.750** | **1.000** | 0.857 |

Reproduce either row with the commands above; `--json` gives the per-case breakdown.

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

## The false positive this corpus found

`agentbound` scores precision 0.750, and the miss is real rather than a fixture artefact.
Case `code-pattern-only-in-comments` is a file where the vulnerable pattern appears **only
inside a triple-quoted string**:

```python
DOCSTRING = """
    if tool.name in self.tools_dict:
        logger.warning("duplicate")
    self.tools_dict[tool.name] = tool
"""
```

`agentbound` reports `tool-dict-last-wins` at line 6, inside that string. Its fix for this
class — *"a comment is not code, so a rule must not match one"* — strips `#` comments but
not string literals, and untaken string content is no more executable than a comment. The
case is kept as a negative, so the corpus keeps reporting it until the detector handles it.

Two of my initial fixtures also scored as misses, and the investigation is worth recording
because the first conclusion ("recall gap") was wrong: `tool-reserved-name-shadowing`
requires a framework-owned tool to be *defined* (`def set_model_response(`) as well as
missing from the reserved set, and `confirmation-gate-fails-open` requires the whole
relation — a signature assignment, the parameters extracted from it, and a dict
comprehension filtering arguments by it. My fixtures had the weakness but not the shape the
rule documents, so the corpus was wrong, not the detector. Both now encode the documented
precondition.

## The cases

**18 cases: 13 tool-list declarations, 5 code.** Each is a JSON file with an id, kind,
label, the rules a detector is expected to report, the *source* of the pattern, and the
fixture.

| kind | positives (must be flagged) | negatives (must stay clean) |
|---|---|---|
| `tool-list` | reserved-name collision, instruction-carrying description, invisible tag-block payload, look-alike names, a destructive tool declaring `readOnlyHint`, missing annotations, unconstrained execution sink, duplicate name, empty description | a well-formed read-only server, a description that merely *mentions* a reserved name, the same shell tool with an `enum`-constrained parameter, a destructive tool that says so |
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

`v0.1.0`, stdlib only, 17 tests (the harness's own behaviour is tested with fake detectors),
CI on 3.11/3.12/3.13 with both detectors installed at pinned commits and both rows above
re-measured.
