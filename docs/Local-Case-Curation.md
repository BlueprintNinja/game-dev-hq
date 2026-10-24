# Local case curation: metadata only

This milestone adds a strict, frozen version 1 `hq-local-curation-plan`, separate
from synthetic evaluation Dataset/Case and the grader. It does not turn historical
evidence into a reproducible benchmark. No real shortlist, run IDs or content are
stored in this repository. The intended local review shape is three coding task
families plus a separate no-change smoke control; the generic contract does not
hardcode that count, project identities or historical selections.

## Trusted local invocation

A deliberate trusted local caller constructs `gamedev.curation.ApprovedShortlist`
and supplies the existing trusted mapping of project slugs to `ProjectPolicy`:

```python
plan = build_local_curation_plan(
    {"proposal_fingerprint": approved_shortlist.proposal_fingerprint},
    principal=principal,
    approved_shortlist=approved_shortlist,
    projects=trusted_project_policies,
)
sanitized = plan.model_dump(mode="json")
```

The service is `gamedev.hq_plugin.evaluation_curation.build_local_curation_plan`.
Both `gamedev.evaluation.read` and `gamedev.evaluation.curate` are required before
selection parsing, database access or filesystem reads. Permission comes only from
the deliberate trusted local caller. No endpoint, capability registration,
persistent grant, settings or operator grant is added. An in-memory approval object
and its fingerprint are not a signature or independently authenticated human
approval. Application authority is the explicit local curator permission and the
trusted caller; actual human approval receipts remain outside this service.
Existing run approval is not case inclusion, reproducibility or export permission.

The shortlist has exactly `record_type: "hq-local-curation-shortlist"`, integer
`schema_version: 1`, `families`, and `proposal_fingerprint`. Each family has exactly
`family_id`, `role` (`coding_case` or `smoke_control`) and `runs`. Each selected run
has exactly `run_id` and `expected_fingerprint` (the existing verified record's
fingerprint). Family IDs are bounded lowercase identifiers. IDs are positive
integers up to 2^63 - 1, excluding booleans and coercions. At most eight nonempty
families and sixteen total runs are accepted. Family IDs and run IDs are unique
across the entire roster. All fields and nested fields reject unknown inputs.
Python sequence fields are tuples; JSON arrays round-trip through
`model_validate_json`. The trusted object is revalidated on entry.

The selection accepts only `proposal_fingerprint`. It cannot supply paths, policy,
record snapshots, additional run IDs, raw evidence, latest lookups or criteria.
The selected fingerprint must exactly match the approved shortlist. Reordering or
changing family IDs, roles, selected IDs or expected fingerprints changes the
proposal binding. There is no implicit inclusion approval.

## Revalidation and whole-plan refusal

For each explicit ID the service calls the existing
[`read_run_evidence`](Verified-Run-Evidence.md) adapter. Its authoritative joined
Run/Task SELECT and local Git commit, private check capture, revision, diff and run
approval validation remain intact. The service compares the resulting verified
record fingerprint to the approved expected fingerprint. Missing or refused
records, unsupported versions and any drift refuse the whole plan. No snapshot
can substitute, and no partial shortlist silently survives. Captured private
checks are hashed internally by the existing adapter; no log, command, source,
prompt or diff text is returned.

Every record in a family must share one Task ID, and a Task ID cannot occur in
multiple families. A rejected/accepted pair therefore stays grouped. A coding
family contains only coding records and at least one nonempty captured diff.
All-empty families must be `smoke_control`; a smoke control cannot contain a
nonempty diff. Status and run approval are preserved exactly: failed/rejected
examples can remain metadata with `not_approved`. Neither task completion nor a
pair establishes causal recovery, independence, gameplay or complete execution.
Parent, delegation, retry and escalation IDs are preserved without following the
graph or fetching related records.

Service refusals expose only fixed `CurationRefused.code` values:
`invalid_selection`, `invalid_shortlist` (including unsupported shortlist versions
and invalid roster bindings), `proposal_fingerprint_mismatch`,
`record_fingerprint_mismatch`, `evidence_refused` (including missing IDs or invalid
adapter provenance), `mixed_task_family`, `task_reused`, `noncoding_family`,
`empty_coding_family`, and `nonempty_smoke_control`. Pure contract validation uses
Pydantic validation errors, additionally identifying duplicate IDs, run limits,
invalid versions, counts, summaries and integrity bindings. SDK authorization
errors precede these validations. Service errors contain no underlying capture,
path or private refusal details.

## Plan integrity and limits

The plan includes the exact grouped sanitized `VerifiedRunEvidence` records;
only their mutable sequence containers are specialized to frozen tuples. It has
separate coding-family and smoke-control counts, per-family and total complete/
incomplete record counts and explicit evidence reason codes. Incomplete evidence
remains incomplete. The plan has no grading verdict or model quality score.
`benchmark_ready`, `reproducibility_approved`, `export_approved`,
`source_extraction_approved` and `model_experiment_approved` are always false;
`dataset_split` is always null. Parsing rejects attempts to enable these fields,
even after recomputing the plan fingerprint. Counts, summaries, proposal binding
and nested record fingerprints are also rechecked. It supports JSON and pickle
round-trips; pickle is only suitable for trusted local data, not untrusted input.

Both fingerprints use SHA-256 over canonical JSON: sorted keys, compact
separators, ASCII escaping, explicit nulls, preserved array order and no nonfinite
numbers. The shortlist excludes only its own `proposal_fingerprint`; the plan
excludes only its own `fingerprint`. Their record types distinguish the domains
from verified records, HQ check digests and synthetic grader contracts. Hashes
provide integrity, not authenticity; anyone able to rewrite metadata and hashes
can forge a different internally consistent bundle. A saved plan does not
revalidate current database state; invoke the service again to do that. Reads
occur one selected row at a time, not as a cross-run transactional snapshot.

The service performs at most sixteen explicit joined SELECTs, with bounded local
Git verification through the existing adapter. There is no project scan, discovery
or graph traversal. Existing capture-loading and local-storage limits still
apply; hashes bound content identities, not the initial allocation of database
capture fields. No database/filesystem/audit writes, environment reads, policy or
approval file loading, subprocesses, network/provider/editor calls, registry
changes, exports or experiments occur in the service. The local caller owns any
later saving and auditing under separate authorization. No actual data files,
dataset creation/assignment/split, Phoenix upload or model ranking is authorized.

## Verification and next step

Workspace tests use fresh synthetic Run/Task rows and constructed valid local Git
commits, using the existing adapter's fixture and SDK principal. They exercise
permission ordering, strict contracts and limits, grouping, drift, capture
refusal, privacy, completeness, forbidden flags, integrity and the sixteen-query
maximum. No provenance validation is replaced on success paths.

The next step is human reproducibility and permitted-context review. Any case
inclusion, dataset/holdout assignment, extraction, export or model experiment
requires separate future authorization. This milestone provides no reproducibility
approval and no empirical gameplay or model-quality claim.
