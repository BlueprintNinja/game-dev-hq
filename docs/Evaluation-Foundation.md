# Local evaluation foundation

Project Echo's first evaluation milestone is a local declared-evidence harness.
It does not compare models. The ten bundled cases are **synthetic harness
self-tests**, including deliberately failing, incomplete and invalid evidence.
Their counts must never be presented as AI quality, benchmark results, a
performance leaderboard, or a causal model ranking.

Run with operator-selected local files:

```text
python -m gamedev.evaluation --dataset gamedev/evaluation_cases.json --observations LOCAL_OBSERVATIONS.json [--output LOCAL_REPORT.json]
```

There is no collector, experiment execution, automation/export switch, Phoenix
integration, network client, subprocess execution, editor operation, persistence,
or Django/database initialization in the evaluator. It neither reads prompts,
source, logs, credentials nor worker records. Input is never executable. Existing
worker/evidence/usage contracts are unchanged. A later adapter must deliberately
construct this new contract; existing worker digests are not interchangeable.
Only the CLI test invokes a Python subprocess, against temporary synthetic files.

## Version 1 contract

`gamedev/evaluation.py` defines the strict Pydantic input models; extra fields are
forbidden at every level, types are not coerced, and booleans are not numbers.
The manifest has `schema_version: 1`, `dataset_id`, `version`,
`provenance: "synthetic-harness-self-test"`, and a nonempty `cases` list. Each case
has `case_id`, `version`, a short synthetic `scenario`, `criteria`, and `fixture`.
This first schema accepts synthetic provenance only. Curated datasets and actual
experiment integration require a future explicit contract decision.

Criteria declare `required_kinds` (unique `source`, `tests`, or `compilation`),
`pass_scope` (`declared_source`, `declared_tests`, or `declared_compilation`),
`required_child_ids`, and `recovery_required`. Scope must name a required kind.
The required `establishes` sentence is fixed:
"Declared required checks passed; no hidden correctness, editor or gameplay verification."
A fixture carries synthetic provenance, structured `evidence` and
`expected_verdict`. Fixture expectations are exercised by tests, never used to
decide an observation's grade.

An observations file is an object with `schema_version: 1`, `dataset_id`,
`dataset_version`, nonempty `configurations`, and `observations` (possibly empty).
Every configuration declares `config_id` and optionally `selected_model` (null
when absent). A configuration must be listed even when it has no observations.
Use neutral names such as `synthetic-config-a` for harness demonstrations.

Each observation requires all of:

- `dataset_id`, `dataset_version`, `case_id`, `case_version`, `config_id`;
- `case_fingerprint`, `criteria_fingerprint`, `evidence_fingerprint`;
- `hq_run_id` (opaque bounded identifier; not looked up), `git_revision`;
- `evidence`: `status`, `checks`, `children`, and `recovery` (explicit null allowed).

`actual_model` and `usage` are optional. Actual and selected model are distinct:
one is never filled from the other. Usage allows only `input_tokens` and
`output_tokens`, each independently null or an integer from 0 through 10^12.
Missing usage remains unknown; a reported zero is known. There is no estimated
cost, credit count, usage total, or inference from run/model names.

Every check is exactly `{ "kind": "tests", "passed": true }` (with the declared
kind and an explicit boolean). Status is `pending`, `completed`, `failed`,
`accepted`, or `success`. Every supplied check, including an extra allowed kind,
must pass for a pass. Duplicate kinds, even identical outcomes, are ambiguous
and invalid. Missing required kinds are incomplete. No command, prompt, source,
log, arbitrary metadata or result summary field is accepted.

A child has `child_id`, `status`, `checks`. Required children must be present
and terminal, with every parent-required kind. All supplied children are graded,
including additional children; duplicate child IDs are invalid. This milestone
handles one flat level only and trusts declared child identities. It does not
retrieve runs, verify provenance or reconstruct delegation graphs.

Recovery is null or `{ "completed": true, "initial_checks": [...], "checks": [...] }`.
Both lists need every required kind. At least one initial check must be false;
final checks must pass and recovery must explicitly complete. A completed
recovery contradicted by missing/failed/ambiguous evidence is invalid. No
recovery is inferred from a final success. Uncompleted recovery never passes.

IDs/versions are 1–64 ASCII letters/digits/dot/underscore/hyphen, beginning with
a letter or digit. Revisions are full lowercase 40- or 64-digit hexadecimal;
fingerprints are lowercase 64-digit SHA-256 hexadecimal. Model names may also contain colon/slash and are at most 180 characters.
Labels/scenarios are at most 300 characters. Identifiers and hashes are declarations, not proof of a
real run, Git object, execution, or trustworthy evidence.

## Fingerprints and deterministic reports

Use the pure helpers `validate_dataset`, `case_fingerprint`, `fingerprint`,
`grade`, and `evaluate` without Django. Canonical JSON sorts object keys, uses
compact separators, ASCII escapes, no nonfinite numbers, and UTF-8 bytes. Array
order is significant. Fingerprint validated model values (`model_dump`, including
null defaults), not an arbitrary original JSON spelling.

`criteria_fingerprint = fingerprint(case.criteria)`;
`evidence_fingerprint = fingerprint(validated_evidence)`;
`case_fingerprint(dataset, case)` hashes an object containing `dataset_id`,
`dataset_version`, and the full validated `case`, including its version,
scenario, criteria and synthetic fixture. Any fixture/criteria edit changes that
binding, even when an operator forgets to bump the version. Dataset/case version
changes also change the case fingerprint. This is integrity binding, not a
signature, authenticity check or hidden-correctness oracle.

The report always uses the manifest's same full expected case list for every
listed configuration. Configurations and cases are sorted by ID. Missing cases
remain present with `missing_observation`, null usage/model/identity, and
`incomplete`. Each case reports its verdict, reason codes, pass scope, bindings,
reported usage, and `gameplay_verified: false`. Compilation/source/test evidence
never implies editor behavior, gameplay, acceptance of a patch, or correctness
outside the declared checks.

Counts distinguish expected, observed, evidence completion, and verified passes.
A verified pass means only a pass of this declared-evidence grader. Per-field
`known_usage_coverage` counts observations with a known value, independently of
verdict and completion. Unknown and zero remain distinguishable. There are no
ratios with omitted cases, inferred costs, winners, or model rankings.

Verdicts are `pass`, `fail`, `incomplete`, `invalid`. Priority when multiple
problems coexist is invalid, then fail, then incomplete. Reason codes retain
all problems (for example failure plus missing evidence). Accepted/success
claims with any failed, incomplete or invalid obligation are invalid. Pending
status is incomplete; failed status fails even with passing checks. Completed
status alone cannot satisfy missing evidence.

Schema errors, duplicate case/config observations, duplicate manifest/config
IDs, unknown cases/configurations, incompatible versions, and fingerprint
mismatches refuse the entire comparison (`configurations: []`) with an explicit
reason code. They are never silently filtered into comparable subsets. Individual
structurally valid evidence problems stay visible as per-case verdicts.
Machine codes include `schema_invalid`, `incompatible_dataset_version`,
`incompatible_case_version`, `case_fingerprint_mismatch`,
`criteria_fingerprint_mismatch`, `evidence_fingerprint_mismatch`,
`duplicate_case_config`, `unknown_case`, `unknown_config`, `failed_check`,
`missing_kind:tests`, `ambiguous_check_kind`, `contradicted_success`,
`missing_child:ID`, `child:ID:pending`, `missing_recovery`,
`recovery_incomplete`, and `contradicted_recovery_completion`.

## Bounds and CLI behavior

Each input file is limited to 1,048,576 bytes (read at most limit + 1). Limits are
100 cases, 16 configurations, 1,600 observations, 8 checks per list, and 8 child
records/required IDs. All limits apply together; the byte limit may be reached
before the collection limits. JSON duplicate keys, NaN/Infinity, floating-point
overflow, invalid encodings and malformed/deeply nested JSON are rejected.
Regular local files only; UNC/device-prefix paths are refused. Files are selected
by the operator, not discovered. The caller remains responsible for choosing
local storage rather than network-mounted filesystem locations.

Output is canonical JSON with a newline, bounded to 2,097,152 bytes. Parse and
schema failures produce small machine-readable error reports without echoing
untrusted data or local paths. Exit codes are 0 pass, 1 fail, 2 invalid/I/O/usage
error, 3 incomplete; mixed verdicts follow the priority above. `--help` uses
normal argparse help. Optional output is written to a sibling temporary file,
flushed/fsynced, then atomically replaced. Input paths cannot be output targets.
Parsing/I/O/output-limit errors go to stdout and do not replace an existing
output file; callers must use the exit status, not a stale file. No files are
uploaded or published. The atomic write is local replacement, not a crash-proof
persistence service, and assumes operator-controlled directories.

The companion suite tests all ten synthetic fixtures, failures/denials, strict
input, bindings, comparisons, limits and CLI exits. Passing those tests validates
the harness implementation. It provides no empirical evidence about AI task
quality, real project performance, model superiority, or gameplay behavior.
