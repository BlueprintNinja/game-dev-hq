"""Trusted local metadata-only curation. No registration, writes or policy loading."""

from pydantic import ConfigDict, ValidationError

from hq_sdk.capabilities import StrictCommand

from ..curation import (
    ApprovedShortlist,
    CurationFamily,
    CurationRefused,
    FrozenVerifiedRunEvidence,
    LocalCurationPlan,
    plan_fingerprint,
    summarize,
    validate_family,
)
from ..verified_evidence import EvidenceRefused, Hash
from .evaluation_evidence import read_run_evidence


class CurationSelection(StrictCommand):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")
    proposal_fingerprint: Hash


def _approved_input(selection, approved_shortlist):
    try:
        command = CurationSelection.model_validate(selection)
    except ValidationError:
        raise CurationRefused("invalid_selection") from None
    if type(approved_shortlist) is not ApprovedShortlist:
        raise CurationRefused("invalid_shortlist")
    try:
        approved = ApprovedShortlist.model_validate_json(approved_shortlist.model_dump_json())
    except ValidationError:
        raise CurationRefused("invalid_shortlist") from None
    if command.proposal_fingerprint != approved.proposal_fingerprint:
        raise CurationRefused("proposal_fingerprint_mismatch")
    return approved


def _read_family(family, *, principal, projects):
    records = []
    for selected in family.runs:
        try:
            record = read_run_evidence({"run_id": selected.run_id}, principal=principal, projects=projects)
        except EvidenceRefused:
            raise CurationRefused("evidence_refused") from None
        if record.fingerprint != selected.expected_fingerprint:
            raise CurationRefused("record_fingerprint_mismatch")
        try:
            records.append(FrozenVerifiedRunEvidence.model_validate_json(record.model_dump_json()))
        except ValidationError:
            raise CurationRefused("evidence_refused") from None
    validate_family(family.role, records)
    return CurationFamily(
        family_id=family.family_id, role=family.role, records=tuple(records), evidence_summary=summarize(records)
    )


def build_local_curation_plan(selection, *, principal, approved_shortlist, projects):
    """Both permissions precede parsing and all authoritative adapter reads.

    Caller supplies the trusted shortlist object and existing ProjectPolicy mapping.
    This authority grants no inclusion, reproducibility, extraction or export approval.
    """
    principal.require("gamedev.evaluation.read")
    principal.require("gamedev.evaluation.curate")
    approved = _approved_input(selection, approved_shortlist)
    families = []
    tasks = set()
    for family in approved.families:
        grouped = _read_family(family, principal=principal, projects=projects)
        task_id = grouped.records[0].task_id
        if task_id in tasks:
            raise CurationRefused("task_reused")
        tasks.add(task_id)
        families.append(grouped)
    payload = {
        "record_type": "hq-local-curation-plan",
        "schema_version": 1,
        "proposal_fingerprint": approved.proposal_fingerprint,
        "families": tuple(families),
        "coding_family_count": sum(family.role == "coding_case" for family in families),
        "smoke_control_count": sum(family.role == "smoke_control" for family in families),
        "evidence_summary": summarize(tuple(record for family in families for record in family.records)),
        "benchmark_ready": False,
        "reproducibility_approved": False,
        "export_approved": False,
        "source_extraction_approved": False,
        "model_experiment_approved": False,
        "dataset_split": None,
    }
    payload["fingerprint"] = plan_fingerprint(payload)
    return LocalCurationPlan.model_validate(payload)
