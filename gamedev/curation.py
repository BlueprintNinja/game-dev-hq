"""Pure, bounded metadata contracts for deliberate local curation, not datasets."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .verified_evidence import CapturedCheck, Hash, Reason, RecordID, VerifiedRunEvidence

MAX_FAMILIES = 8
MAX_RUNS = 16
EMPTY_DIFF_SHA256 = hashlib.sha256(b"").hexdigest()
FamilyID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")]
Role = Literal["coding_case", "smoke_control"]


class CurationRefused(ValueError):
    """Fixed codes only; no private values or paths."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fingerprint(value, own_field):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    payload = {key: item for key, item in value.items() if key != own_field}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        default=lambda item: item.model_dump(mode="json"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def proposal_fingerprint(value):
    """Canonical shortlist SHA-256; excludes only proposal_fingerprint."""
    return _fingerprint(value, "proposal_fingerprint")


def plan_fingerprint(value):
    """Canonical plan SHA-256; excludes only fingerprint."""
    return _fingerprint(value, "fingerprint")


class StrictCuration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class VersionOne(StrictCuration):
    schema_version: Literal[1]

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("unsupported_version")
        return value


class ApprovedRun(StrictCuration):
    run_id: RecordID
    expected_fingerprint: Hash


class ApprovedFamily(StrictCuration):
    family_id: FamilyID
    role: Role
    runs: Annotated[tuple[ApprovedRun, ...], Field(min_length=1, max_length=MAX_RUNS)]


def validate_roster(families):
    family_ids = [family.family_id for family in families]
    run_ids = [run.run_id for family in families for run in family.runs]
    if len(family_ids) != len(set(family_ids)):
        raise CurationRefused("duplicate_family_id")
    if len(run_ids) > MAX_RUNS:
        raise CurationRefused("run_limit")
    if len(run_ids) != len(set(run_ids)):
        raise CurationRefused("duplicate_run_id")


class ApprovedShortlist(VersionOne):
    """Trusted caller input, an integrity binding rather than an approval signature."""

    record_type: Literal["hq-local-curation-shortlist"]
    families: Annotated[tuple[ApprovedFamily, ...], Field(min_length=1, max_length=MAX_FAMILIES)]
    proposal_fingerprint: Hash

    @model_validator(mode="after")
    def validate_binding(self):
        validate_roster(self.families)
        if self.proposal_fingerprint != proposal_fingerprint(self):
            raise ValueError("proposal_fingerprint_mismatch")
        return self


class FrozenVerifiedRunEvidence(VerifiedRunEvidence):
    """Reuse the evidence allowlist, freezing its two sequence fields as well."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")
    checks: Annotated[tuple[CapturedCheck, ...], Field(max_length=8)]
    reason_codes: Annotated[tuple[Reason, ...], Field(min_length=1, max_length=12)]


class EvidenceSummary(StrictCuration):
    complete_records: int = Field(ge=0, le=MAX_RUNS)
    incomplete_records: int = Field(ge=0, le=MAX_RUNS)
    reason_codes: Annotated[tuple[Reason, ...], Field(min_length=1, max_length=12)]


def summarize(records):
    return EvidenceSummary(
        complete_records=sum(record.completeness == "complete" for record in records),
        incomplete_records=sum(record.completeness == "incomplete" for record in records),
        reason_codes=tuple(sorted({reason for record in records for reason in record.reason_codes})),
    )


def validate_family(role, records):
    if len({record.task_id for record in records}) != 1:
        raise CurationRefused("mixed_task_family")
    if role == "coding_case":
        if any(record.kind != "coding" for record in records):
            raise CurationRefused("noncoding_family")
        if all(record.captured_diff_sha256 == EMPTY_DIFF_SHA256 for record in records):
            raise CurationRefused("empty_coding_family")
    elif any(record.captured_diff_sha256 != EMPTY_DIFF_SHA256 for record in records):
        raise CurationRefused("nonempty_smoke_control")


class CurationFamily(StrictCuration):
    family_id: FamilyID
    role: Role
    records: Annotated[tuple[FrozenVerifiedRunEvidence, ...], Field(min_length=1, max_length=MAX_RUNS)]
    evidence_summary: EvidenceSummary

    @model_validator(mode="after")
    def validate_group(self):
        validate_family(self.role, self.records)
        if self.evidence_summary != summarize(self.records):
            raise ValueError("summary_mismatch")
        return self


def shortlist_payload(families):
    return {
        "record_type": "hq-local-curation-shortlist",
        "schema_version": 1,
        "families": [
            {
                "family_id": family.family_id,
                "role": family.role,
                "runs": [
                    {"run_id": record.run_id, "expected_fingerprint": record.fingerprint} for record in family.records
                ],
            }
            for family in families
        ],
    }


class LocalCurationPlan(VersionOne):
    record_type: Literal["hq-local-curation-plan"]
    proposal_fingerprint: Hash
    families: Annotated[tuple[CurationFamily, ...], Field(min_length=1, max_length=MAX_FAMILIES)]
    coding_family_count: int = Field(ge=0, le=MAX_FAMILIES)
    smoke_control_count: int = Field(ge=0, le=MAX_FAMILIES)
    evidence_summary: EvidenceSummary
    benchmark_ready: bool = False
    reproducibility_approved: bool = False
    export_approved: bool = False
    source_extraction_approved: bool = False
    model_experiment_approved: bool = False
    dataset_split: None = None
    fingerprint: Hash

    @model_validator(mode="after")
    def validate_plan(self):
        if any(
            (
                self.benchmark_ready,
                self.reproducibility_approved,
                self.export_approved,
                self.source_extraction_approved,
                self.model_experiment_approved,
            )
        ):
            raise ValueError("forbidden_approval")
        proposal = shortlist_payload(self.families)
        proposal["proposal_fingerprint"] = self.proposal_fingerprint
        ApprovedShortlist.model_validate_json(json.dumps(proposal))
        tasks = [family.records[0].task_id for family in self.families]
        if len(tasks) != len(set(tasks)):
            raise CurationRefused("task_reused")
        if self.coding_family_count != sum(family.role == "coding_case" for family in self.families):
            raise ValueError("count_mismatch")
        if self.smoke_control_count != sum(family.role == "smoke_control" for family in self.families):
            raise ValueError("count_mismatch")
        if self.evidence_summary != summarize(tuple(record for family in self.families for record in family.records)):
            raise ValueError("summary_mismatch")
        if self.fingerprint != plan_fingerprint(self):
            raise ValueError("plan_fingerprint_mismatch")
        return self
