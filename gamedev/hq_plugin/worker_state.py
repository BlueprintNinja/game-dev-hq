"""Only the local worker can claim and finish executions."""

import secrets

from django.db import transaction

from hq_sdk.audit import audit_operation, record_operation

from .models import Run


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
        record_operation(
            "gamedev.run.progress",
            f"Run #{run.pk}: {run.status}",
            metadata={"run_id": run.pk, "status": run.status},
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
