# Verified run evidence adapter

The second evaluation milestone is a read-only adapter from an **explicitly
selected HQ Run ID** to a sanitized `hq-verified-run-evidence` version 1 record.
It is separate from [the pure local grader](Evaluation-Foundation.md). It does
not assign datasets or cases, grade work, reconstruct a recovery outcome,
compare models, or establish gameplay correctness. The existing ten-case
synthetic dataset and grader are unchanged; real runs must never be inserted
into their synthetic harness self-tests.

## Invocation and authority

The application service is
`gamedev.hq_plugin.evaluation_evidence.read_run_evidence`:

```python
record = read_run_evidence(
    {"run_id": explicit_run_id},
    principal=principal,
    projects=trusted_project_policies,
)
sanitized = record.model_dump(mode="json")
```

The only selection field is a strict positive integer `run_id` (at most
2^63 - 1). There is no latest-run lookup, project search, default run, path,
repository, model override, or raw evidence input. Unknown selection fields
are refused. Authorization requires the dedicated `gamedev.evaluation.read`
permission **before** selection parsing, database access, or filesystem reads;
ordinary read/write/metrics/traces permissions do not suffice. No capability
is registered or permission granted by default in this milestone. A deliberate
local caller must supply the principal and trusted in-memory mapping of project
slugs to existing `ProjectPolicy` values. There is no new UI, endpoint, command,
worker policy setting, or service configuration.

The service performs one joined SELECT of the authoritative Run and its Task.
It uses the Task's project slug internally only to select the configured
repository and required check kinds; the slug and project policy never enter
the result. No caller-supplied snapshot substitutes for these records. Foreign
key identities for parent task, delegating run, retry source, and escalation
root are preserved as IDs only. Their contents and outcomes are not fetched;
this record makes no assertion about child completion or recovery success.
A later mutation of HQ records does not retroactively update a returned bundle.

## Provenance and refusal

The base revision must be a full lowercase 40-digit SHA-1 or 64-digit SHA-256
OID and resolve to a commit in that project's configured repository. The
bounded standard-library reader in `gamedev/revision_objects.py` reads local
loose objects or v2 pack indexes with v2/v3 packs, including offset/reference
deltas. Linked worktrees and bare repositories are supported. Reconstructed
object content is hashed against the requested OID; a valid-looking nonexistent
revision, blob/tag, corrupt object/index, or unsupported storage is refused.
Git configuration, refs, replace refs, alternate object databases, hooks, and
credentials are not read, and no Git process or lazy fetch is launched. Configure
local storage only, not a network mount. Missing alternate/promisor objects fail
closed; this is intentionally narrower than every repository Git can open.

The reader limits reconstructed/decompressed objects to 2 MiB, compressed
reads to 4 MiB per object, indexes to 64 MiB, pack indexes to 128 per lookup,
and delta depth to 32. Captured checks and captured diff are each limited to
1 MiB after loading from HQ; this does not bound the database's initial row
allocation. Limits refuse the selection rather than truncate evidence.

The captured check digest must equal the **existing `gamedev.evidence.digest`
contract**, hashing the original captured check list, including its private
commands/logs. The captured diff's SHA-256 is independently recomputed from its
UTF-8 text. These values are checked internally; the text is never returned.
Worker digests and the new sanitized-record fingerprint are different contracts.

Checks are at most eight unique kinds from `check`, `source`, `tests`,
`compilation`, `uefn_inspect`, and `uefn_build`. A legacy omitted kind explicitly
means `check`. Each captured exit code must be an integer, excluding booleans;
`passed` is derived only from `exit_code == 0`. Stored `passed` declarations in
individual checks and compact metrics are ignored. Unknown kinds, duplicate
kinds, malformed exit codes, missing digests and mismatched captures are refused.
Configured required kinds must also belong to this allowlist. Current configured
requirements are used; this milestone has no immutable historical policy or
check-command attestation and cannot prove which commands ought to have run.

An accepted record requires all of:

- Run status `accepted` and Run `passed is True`;
- nonempty passing captured checks covering the configured required kinds;
- valid aware created/start/finish/approval timestamps, in order and not future;
- approved diff hash equal to the verified captured diff hash;
- approved evidence hash equal to the actual captured-check digest.

Task status is never an approval. Nonaccepted rows must not retain approval
metadata, and return `approval: "not_approved"`. Failed/rejected/canceled/review
and in-progress evidence can be represented if their captured digests and base
commit verify. An explicitly captured empty check list with `digest([])` is
incomplete evidence; absent capture hashes are refused. Apply/rollback operation
runs are outside this contract and refused. Many historical editor/early-failure
runs lack a base revision or diff digest and therefore cannot be adapted; missing
provenance is never manufactured.

`completeness` describes evidence availability only, independent of whether checks
passed. It is `incomplete` for in-progress runs, missing checks/required kinds,
or missing start/finish timestamps. Unknown model/usage does not make captured
check evidence incomplete. Reasons are explicit allowlisted codes:
`capture_verified`, `approval_verified`, `not_approved`, `run_in_progress`,
`checks_missing`, `required_checks_missing`, `execution_timestamps_missing`,
`actual_model_not_reported`, `usage_not_reported`, `usage_partially_reported`.
There is no grading verdict or dataset assignment.

Refusals raise `EvidenceRefused` with a fixed `.code`, without stored values or
paths. Codes include `invalid_selection`, `run_not_found`,
`repository_not_configured`, `unsupported_run_kind`, `invalid_revision`,
`revision_unresolvable`, `revision_not_commit`, `revision_object_mismatch`,
`revision_repository_unavailable`, `revision_storage_limit`, `invalid_checks`,
`unknown_check_kind`, `ambiguous_check_kind`, `invalid_check_exit_code`,
`capture_too_large`, `missing_evidence_hash`, `evidence_digest_mismatch`,
`missing_diff_hash`, `diff_digest_mismatch`, `unsupported_required_kind`,
`invalid_timestamps`, `accepted_timestamps_missing`, `accepted_checks_invalid`,
`approval_status_mismatch`, `approved_diff_mismatch`,
`approved_evidence_mismatch`, and `invalid_record_fields`. Permission denials use
the SDK's `AuthorizationError`; the service writes no denial or audit record.

## Allowlist and limits of the record

The strict schema in `gamedev/verified_evidence.py` rejects unknown fields at
every level and rejects coercion. The result contains only structured IDs,
run/task status and run kind, base revision, check kind/boolean pass, selected
model, reported actual engine/model, bounded reported input/output tokens,
Run timestamps and derived timings, capture/approval hashes, validation and
completeness codes, version/type, fingerprint, and `gameplay_verified: false`.

The Run's routing `selected_model` is distinct from the actual engine. The
current Run schema has **no independently reported actual model field**, so
`actual_model` always remains null. Neither selected model, raw output,
arbitrary metric names nor trace attributes supply it. Actual engine comes
only from Run.engine (codex/opencode/editor); blank stays null. No default
engine is inferred from task assignment. Input/output token counts come only
from the existing worker/collector run metrics contract with
`usage_observed is True`, and are independently nullable integers from zero
through 10^12. Missing, malformed, negative, boolean, floating, or excessive
values stay null. Zero is known. No totals, costs, credits, estimates, or
arbitrary metrics are included. Metrics/engine/timing metadata are HQ-reported
context; **the check approval digest does not authenticate these fields**.

UTC timestamps use ISO 8601. Duration is finish minus start, queue is start
minus creation, and approval wait is approval minus finish. Missing endpoints
produce null, never estimated or clamped values. Out-of-order or future
available timestamps are refused. There is no invented capture timestamp: the
record exposes the Run's lifecycle timestamps.

Prompt, output, diff, changed paths, command/stdout/stderr, trace attributes,
secrets, actors, task title, project names/slug, routing reason and decision
notes are excluded. Trace rows and agent logs are never queried. Source files,
editor, Phoenix, telemetry exporters, subprocesses and network clients are not
used. The service returns an in-memory record and makes no database/filesystem
writes. It does not create an experiment or change worker policy or live data.

The fingerprint is SHA-256 of the validated record's canonical JSON **excluding
only its own `fingerprint` field**: sorted keys, compact separators, ASCII
escapes, no nonfinite numbers, UTF-8 bytes, explicit nulls and array order
preserved. Fingerprint validation rejects a changed bundle. It is an integrity
binding, not a signature: an actor able to rewrite HQ records and recompute their
hashes can forge provenance. Successful adaptation proves consistency with the
selected local HQ capture, not independent execution, hidden correctness,
gameplay, benchmark quality or model superiority.

## Verification and later human curation

Focused Git reader tests use newly constructed local Git storage; workspace
tests use fresh synthetic Run/Task records and a throwaway test database. No
real runs are read in tests and no adapter output is fed into synthetic grader
cases. Run the companion and standard workspace suites:

```text
python -m unittest gamedev.tests.test_revision_objects gamedev.tests.test_evidence gamedev.tests.test_evaluation -v
python -m unittest discover -s gamedev/tests -v
uv run --locked --no-default-groups python -m tools.windows_hq.check
```

A future human-curation milestone must deliberately select independent task
families, review reproducibility and permitted context, inspect the complete
sanitized candidate, approve inclusion and any dataset/holdout assignment, and
approve any Phoenix upload through a separate audited write capability.
Retries and delegations must not silently become independent benchmark cases.
This adapter supplies no such approval and no empirical model-quality claim.
