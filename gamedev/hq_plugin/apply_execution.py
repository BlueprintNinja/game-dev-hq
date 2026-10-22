"""Applying an approved change, and taking it back.

Approval and application are different operations and are never combined.
Accepting a run records that its captured changes and its captured evidence
were good. Applying writes them into the destination the operator named in
the local policy, which only the worker may do. The web process never
touches a file.
"""

from typing import Annotated

from django.db import transaction
from pydantic import Field

from hq_sdk.audit import audit_operation, record_operation
from hq_sdk.capabilities import CapabilitySpec, StrictCommand

from .models import Run, Task


class ApplyApproved(StrictCommand):
    run_id: Annotated[int, Field(gt=0)]
    version: Annotated[int, Field(gt=0)]
    note: Annotated[str, Field(max_length=4000)] = ""


class RollbackApply(StrictCommand):
    run_id: Annotated[int, Field(gt=0)]
    version: Annotated[int, Field(gt=0)]
    note: Annotated[str, Field(max_length=4000)] = ""


def apply_approved(command, *, principal):
    """Ask the worker to write an accepted run's changes into the destination."""
    principal.require("gamedev.write")
    with transaction.atomic(), audit_operation(operation="gamedev.run.apply", principal=principal):
        source = Run.objects.select_for_update().select_related("task").filter(pk=command.run_id).first()
        if source is None:
            raise ValueError("The run does not exist.")
        if source.version != command.version:
            raise ValueError("Run changed. Reload before applying.")
        if source.status != "accepted":
            raise ValueError("Only an accepted run may be applied.")
        if not source.passed:
            raise ValueError("Only a run with passing evidence may be applied.")
        if not source.diff or not source.approved_diff_sha256:
            raise ValueError("That run holds no approved changes.")
        if source.approved_diff_sha256 != source.diff_sha256:
            raise ValueError("The approval does not name this run's changes.")
        task = Task.objects.select_for_update().get(pk=source.task_id)
        if task.runs.filter(status__in=("queued", "running", "review")).exists():
            raise ValueError("This task already has work running or awaiting review.")
        run = Run.objects.create(
            task=task, prompt=command.note.strip(), kind="apply", applies=source, requested_by=principal.actor
        )
        record_operation(
            "gamedev.run.apply",
            f"Queued apply #{run.pk} of run #{source.pk}",
            metadata={"run_id": run.pk, "applied_run_id": source.pk},
            required=True,
        )
        return {"ok": True, "id": run.pk}


def rollback_apply(command, *, principal):
    """Ask the worker to put back what an apply wrote."""
    principal.require("gamedev.write")
    with transaction.atomic(), audit_operation(operation="gamedev.run.rollback", principal=principal):
        applied = Run.objects.select_for_update().select_related("task").filter(pk=command.run_id).first()
        if applied is None:
            raise ValueError("The run does not exist.")
        if applied.version != command.version:
            raise ValueError("Run changed. Reload before rolling back.")
        if applied.kind != "apply" or applied.status != "accepted":
            raise ValueError("Only an apply that succeeded may be rolled back.")
        if applied.rolled_back_at:
            raise ValueError("That apply has already been rolled back.")
        task = Task.objects.select_for_update().get(pk=applied.task_id)
        if task.runs.filter(status__in=("queued", "running", "review")).exists():
            raise ValueError("This task already has work running or awaiting review.")
        run = Run.objects.create(
            task=task, prompt=command.note.strip(), kind="rollback", applies=applied, requested_by=principal.actor
        )
        record_operation(
            "gamedev.run.rollback",
            f"Queued rollback #{run.pk} of apply #{applied.pk}",
            metadata={"run_id": run.pk, "applied_run_id": applied.pk},
            required=True,
        )
        return {"ok": True, "id": run.pk}


def capabilities():
    return (
        CapabilitySpec(
            "gamedev.run.apply",
            "Write an accepted run's changes into the destination",
            "remote_write",
            "gamedev.write",
            ApplyApproved,
            apply_approved,
            label="Apply approved changes",
        ),
        CapabilitySpec(
            "gamedev.run.rollback",
            "Put back what an apply wrote",
            "remote_write",
            "gamedev.write",
            RollbackApply,
            rollback_apply,
            label="Roll back an apply",
        ),
    )
