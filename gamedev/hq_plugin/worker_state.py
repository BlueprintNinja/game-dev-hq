"""Only the local worker can claim and finish executions."""

import secrets

from django.db import transaction
from django.utils import timezone

from hq_sdk.audit import audit_operation, record_operation

from .models import Run, Task

# A run that does work. An apply and a rollback are operations on a run
# that already did its work, so they never settle the task's state.
WORK_KINDS = ("coding", "uefn_inspect", "uefn_build")


def claim(principal):
    principal.require("gamedev.write")
    with transaction.atomic(), audit_operation(operation="gamedev.run.claim", principal=principal):
        run = Run.objects.select_for_update().filter(status="queued", cancel_requested=False).order_by("id").first()
        if run is None:
            return None
        run.status = "running"
        run.worker_token = secrets.token_hex(32)
        run.version += 1
        run.save()
        record_operation("gamedev.run.claim", f"Claimed run #{run.pk}", required=True)
        return run


def update(run_id, token, principal, **values):
    principal.require("gamedev.write")
    with transaction.atomic(), audit_operation(operation="gamedev.run.progress", principal=principal):
        run = Run.objects.select_for_update().get(pk=run_id)
        if run.status != "running" or not secrets.compare_digest(run.worker_token, token):
            raise ValueError("Worker no longer owns this run.")
        for key, value in values.items():
            setattr(run, key, value)
        if run.cancel_requested and run.status == "review":
            run.status = "canceled"
            run.passed = False
        run.version += 1
        run.save()
        if run.status in ("review", "failed", "canceled") and run.kind in WORK_KINDS:
            task = Task.objects.select_for_update().get(pk=run.task_id)
            task.status = "review" if run.status == "review" else "blocked"
            task.version += 1
            task.save(update_fields=("status", "version", "updated_at"))
        record_operation(
            "gamedev.run.progress",
            f"Run #{run.pk}: {run.status}",
            metadata={"run_id": run.pk, "status": run.status},
            required=True,
        )


def note_rollback(applied_run_id, principal):
    """Record that an apply was taken back."""
    principal.require("gamedev.write")
    with transaction.atomic(), audit_operation(operation="gamedev.run.rollback", principal=principal):
        run = Run.objects.select_for_update().get(pk=applied_run_id)
        run.rolled_back_at = timezone.now()
        run.version += 1
        run.save(update_fields=("rolled_back_at", "version", "updated_at"))
        record_operation(
            "gamedev.run.rollback",
            f"Apply run #{run.pk} was taken back",
            metadata={"run_id": run.pk},
            required=True,
        )


def recover(principal):
    """Called under the exclusive worker lock; never replay an interrupted job."""
    for run in Run.objects.filter(status="running"):
        update(
            run.pk,
            run.worker_token,
            principal,
            status="failed",
            output=run.output + "\nWorker stopped. Review the retained work before retrying.",
        )
