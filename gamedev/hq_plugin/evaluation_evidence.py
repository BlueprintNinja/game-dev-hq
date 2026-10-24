"""Explicit, permissioned Run/Task -> sanitized evidence; no writes or exports.

`projects` is a trusted in-memory mapping of project slugs to ProjectPolicy,
provided by the operator's caller. It is never loaded from worker policy here
and cannot be supplied in an evaluation selection. No capability is registered.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Annotated

from django.utils import timezone
from pydantic import ConfigDict, Field, ValidationError

from hq_sdk.capabilities import StrictCommand

from ..evidence import digest
from ..revision_objects import verify_commit
from ..verified_evidence import EvidenceRefused, VerifiedRunEvidence, record_fingerprint
from .models import Run

MAX_CAPTURE_BYTES = 1_048_576
CHECK_KINDS = frozenset(("check", "source", "tests", "compilation", "uefn_inspect", "uefn_build"))
TERMINAL = frozenset(("review", "accepted", "rejected", "failed", "canceled"))
# One joined SELECT is a coherent row snapshot. Neither trace rows nor task
# titles/evidence, prompts, outputs, worktree paths or actor identities are read.
RUN_FIELDS = (
    "id",
    "task_id",
    "task__id",
    "task__project_slug",
    "task__status",
    "task__parent_id",
    "delegated_by_id",
    "retried_from_id",
    "escalation_root_id",
    "kind",
    "status",
    "base_revision",
    "verification",
    "evidence_sha256",
    "diff",
    "diff_sha256",
    "passed",
    "selected_model",
    "engine",
    "metrics",
    "approved_diff_sha256",
    "approved_evidence_sha256",
    "approved_at",
    "created_at",
    "started_at",
    "finished_at",
)


class EvidenceSelection(StrictCommand):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: Annotated[int, Field(gt=0, le=2**63 - 1)]


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _checks(run):
    checks = run.verification
    if not isinstance(checks, list) or len(checks) > 8:
        raise EvidenceRefused("invalid_checks")
    try:
        payload = json.dumps(checks, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except ValueError, TypeError, RecursionError:
        raise EvidenceRefused("invalid_checks") from None
    if len(payload.encode("utf-8")) > MAX_CAPTURE_BYTES:
        raise EvidenceRefused("capture_too_large")
    if not _hash(run.evidence_sha256):
        raise EvidenceRefused("missing_evidence_hash")
    if digest(checks) != run.evidence_sha256:
        raise EvidenceRefused("evidence_digest_mismatch")
    sanitized = []
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise EvidenceRefused("invalid_checks")
        # Legacy command-only checks are explicitly the generic check kind.
        kind = check.get("kind", "check")
        if not isinstance(kind, str) or kind not in CHECK_KINDS:
            raise EvidenceRefused("unknown_check_kind")
        if kind in seen:
            raise EvidenceRefused("ambiguous_check_kind")
        if type(check.get("exit_code")) is not int:
            raise EvidenceRefused("invalid_check_exit_code")
        seen.add(kind)
        sanitized.append({"kind": kind, "passed": check["exit_code"] == 0})
    return sanitized


def _captured_diff(run):
    if not _hash(run.diff_sha256):
        raise EvidenceRefused("missing_diff_hash")
    if len(run.diff.encode("utf-8")) > MAX_CAPTURE_BYTES:
        raise EvidenceRefused("capture_too_large")
    if hashlib.sha256(run.diff.encode("utf-8")).hexdigest() != run.diff_sha256:
        raise EvidenceRefused("diff_digest_mismatch")


def _timestamp(value, now):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None or value > now:
        raise EvidenceRefused("invalid_timestamps")
    return value.astimezone(UTC).isoformat()


def _timings(run):
    now = timezone.now()
    stamps = {}
    for name in ("created_at", "started_at", "finished_at", "approved_at"):
        value = getattr(run, name)
        stamps[name] = _timestamp(value, now) if value is not None else None
    if run.created_at is None:
        raise EvidenceRefused("invalid_timestamps")
    previous = run.created_at
    for value in (run.started_at, run.finished_at, run.approved_at):
        if value is not None:
            if value < previous:
                raise EvidenceRefused("invalid_timestamps")
            previous = value
    stamps.update(
        duration_seconds=(run.finished_at - run.started_at).total_seconds()
        if run.finished_at and run.started_at
        else None,
        queue_seconds=(run.started_at - run.created_at).total_seconds() if run.started_at else None,
        approval_seconds=(run.approved_at - run.finished_at).total_seconds()
        if run.approved_at and run.finished_at
        else None,
    )
    return stamps


def _approval(run, checks, missing, timings):
    if run.status != "accepted":
        # A nonaccepted row with retained approval metadata is contradictory,
        # rather than permission to imply an approval from task completion.
        if run.approved_at or run.approved_diff_sha256 or run.approved_evidence_sha256:
            raise EvidenceRefused("approval_status_mismatch")
        return "not_approved"
    if run.passed is not True or not checks or missing or any(not item["passed"] for item in checks):
        raise EvidenceRefused("accepted_checks_invalid")
    if not timings["approved_at"] or not timings["started_at"] or not timings["finished_at"]:
        raise EvidenceRefused("accepted_timestamps_missing")
    if run.approved_diff_sha256 != run.diff_sha256:
        raise EvidenceRefused("approved_diff_mismatch")
    if run.approved_evidence_sha256 != run.evidence_sha256:
        raise EvidenceRefused("approved_evidence_mismatch")
    return "verified"


def _usage(metrics):
    metrics = metrics if isinstance(metrics, dict) and metrics.get("usage_observed") is True else {}
    result = {}
    for target, source in (("input_tokens", "llm.token_count.prompt"), ("output_tokens", "llm.token_count.completion")):
        value = metrics.get(source)
        result[target] = value if type(value) is int and 0 <= value <= 10**12 else None
    return result


def _reasons(run, checks, missing, timings, usage, approval):
    reasons = ["capture_verified", "approval_verified" if approval == "verified" else "not_approved"]
    incomplete = False
    for absent, code in (
        (run.status not in TERMINAL, "run_in_progress"),
        (not checks, "checks_missing"),
        (bool(missing), "required_checks_missing"),
        (not timings["started_at"] or not timings["finished_at"], "execution_timestamps_missing"),
    ):
        if absent:
            incomplete = True
            reasons.append(code)
    # There is no independently reported actual-model field on Run today.
    # Routing selection and trace attributes cannot supply one.
    reasons.append("actual_model_not_reported")
    known = sum(value is not None for value in usage.values())
    if known < 2:
        reasons.append("usage_not_reported" if known == 0 else "usage_partially_reported")
    return "incomplete" if incomplete else "complete", sorted(reasons)


def read_run_evidence(selection, *, principal, projects):
    """Read exactly one selected run, with a caller-supplied trusted composition.

    Authorization precedes validation, lookup and local repository reads. This
    service does not register a web/CLI/MCP capability, write an audit/denial,
    load policy/credentials, persist a record, or assign an evaluation case.
    Rejections expose only EvidenceRefused.code, never stored content.
    """
    principal.require("gamedev.evaluation.read")
    try:
        command = EvidenceSelection.model_validate(selection)
    except ValidationError:
        raise EvidenceRefused("invalid_selection") from None
    try:
        run = Run.objects.select_related("task").only(*RUN_FIELDS).get(pk=command.run_id)
    except Run.DoesNotExist:
        raise EvidenceRefused("run_not_found") from None
    project = projects.get(run.task.project_slug)
    if project is None:
        raise EvidenceRefused("repository_not_configured")
    if run.kind not in ("coding", "uefn_inspect", "uefn_build"):
        raise EvidenceRefused("unsupported_run_kind")
    verify_commit(project.repository, run.base_revision)
    checks = _checks(run)
    _captured_diff(run)
    required = project.required_kinds if run.kind == "coding" else [run.kind]
    if any(kind not in CHECK_KINDS for kind in required):
        raise EvidenceRefused("unsupported_required_kind")
    missing = set(required) - {item["kind"] for item in checks}
    timings = _timings(run)
    approval = _approval(run, checks, missing, timings)
    usage = _usage(run.metrics)
    completeness, reasons = _reasons(run, checks, missing, timings, usage, approval)
    payload = {
        "record_type": "hq-verified-run-evidence",
        "schema_version": 1,
        "run_id": run.pk,
        "task_id": run.task_id,
        "parent_task_id": run.task.parent_id,
        "delegated_by_run_id": run.delegated_by_id,
        "retried_from_run_id": run.retried_from_id,
        "escalation_root_run_id": run.escalation_root_id,
        "status": run.status,
        "task_status": run.task.status,
        "kind": run.kind,
        "base_revision": run.base_revision,
        "checks": checks,
        "selected_model": run.selected_model or None,
        "actual_engine": run.engine or None,
        "actual_model": None,
        "usage": usage,
        "timings": timings,
        "captured_diff_sha256": run.diff_sha256,
        "captured_evidence_sha256": run.evidence_sha256,
        "approved_diff_sha256": run.approved_diff_sha256 or None,
        "approved_evidence_sha256": run.approved_evidence_sha256 or None,
        "approval": approval,
        "completeness": completeness,
        "reason_codes": reasons,
        "gameplay_verified": False,
    }
    payload["fingerprint"] = record_fingerprint(payload)
    try:
        return VerifiedRunEvidence.model_validate(payload)
    except ValidationError:
        raise EvidenceRefused("invalid_record_fields") from None
