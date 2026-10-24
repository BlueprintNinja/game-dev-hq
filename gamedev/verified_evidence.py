"""Sanitized evidence contract, independent of the synthetic evaluation grader."""

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RecordID = Annotated[int, Field(gt=0, le=2**63 - 1)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
Timestamp = Annotated[
    str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?\+00:00$")
]
ModelName = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,179}$")]
CheckKind = Literal["check", "source", "tests", "compilation", "uefn_inspect", "uefn_build"]
RunStatus = Literal["queued", "running", "review", "accepted", "rejected", "failed", "canceled"]
Reason = Literal[
    "capture_verified",
    "approval_verified",
    "not_approved",
    "run_in_progress",
    "checks_missing",
    "required_checks_missing",
    "execution_timestamps_missing",
    "actual_model_not_reported",
    "usage_partially_reported",
    "usage_not_reported",
]


class StrictEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class CapturedCheck(StrictEvidence):
    kind: CheckKind
    passed: bool


class ReportedUsage(StrictEvidence):
    input_tokens: int | None = Field(ge=0, le=10**12)
    output_tokens: int | None = Field(ge=0, le=10**12)


class RunTimings(StrictEvidence):
    created_at: Timestamp
    started_at: Timestamp | None
    finished_at: Timestamp | None
    approved_at: Timestamp | None
    duration_seconds: float | None = Field(ge=0)
    queue_seconds: float | None = Field(ge=0)
    approval_seconds: float | None = Field(ge=0)

    @field_validator("created_at", "started_at", "finished_at", "approved_at")
    @classmethod
    def valid_timestamp(cls, value):
        if value is not None:
            datetime.fromisoformat(value)
        return value


class VerifiedRunEvidence(StrictEvidence):
    record_type: Literal["hq-verified-run-evidence"]
    schema_version: Literal[1]
    run_id: RecordID
    task_id: RecordID
    parent_task_id: RecordID | None
    delegated_by_run_id: RecordID | None
    retried_from_run_id: RecordID | None
    escalation_root_run_id: RecordID | None
    status: RunStatus
    task_status: Literal["queued", "active", "blocked", "review", "done"]
    kind: Literal["coding", "uefn_inspect", "uefn_build"]
    base_revision: Revision
    checks: Annotated[list[CapturedCheck], Field(max_length=8)]
    selected_model: ModelName | None
    actual_engine: Literal["codex", "opencode", "editor", "operator"] | None
    actual_model: ModelName | None
    usage: ReportedUsage
    timings: RunTimings
    captured_diff_sha256: Hash
    captured_evidence_sha256: Hash
    approved_diff_sha256: Hash | None
    approved_evidence_sha256: Hash | None
    approval: Literal["verified", "not_approved"]
    completeness: Literal["complete", "incomplete"]
    reason_codes: Annotated[list[Reason], Field(min_length=1, max_length=12)]
    gameplay_verified: bool
    fingerprint: Hash

    @field_validator("schema_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError("invalid_schema_version")
        return value

    @model_validator(mode="after")
    def validate_binding(self):
        if self.gameplay_verified or self.fingerprint != record_fingerprint(self):
            raise ValueError("invalid_record_binding")
        return self


def record_fingerprint(value):
    """SHA-256 of canonical JSON, excluding only the fingerprint field itself."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    payload = {key: item for key, item in value.items() if key != "fingerprint"}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class EvidenceRefused(ValueError):
    """Only a fixed code crosses the boundary; never a path or stored content."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)
