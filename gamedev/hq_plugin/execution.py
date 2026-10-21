"""Audited execution lifecycle. The web process never launches a command."""

from typing import Annotated, Literal

from django.db import transaction
from pydantic import Field

from hq_sdk.audit import audit_operation, record_operation
from hq_sdk.capabilities import CapabilitySpec, StrictCommand

from .models import Run, Task


class QueueRun(StrictCommand):
    task_id: Annotated[int, Field(gt=0)]
    version: Annotated[int, Field(gt=0)]
    prompt: Annotated[str, Field(min_length=1, max_length=12000)]
    kind: Literal["coding", "uefn_inspect", "uefn_build"] = "coding"


class DecideRun(StrictCommand):
    run_id: Annotated[int, Field(gt=0)]
    version: Annotated[int, Field(gt=0)]
    decision: Literal["cancel", "accept", "reject"]
    note: Annotated[str, Field(max_length=4000)] = ""


def queue_run(command, *, principal):
    principal.require("gamedev.write")
    with transaction.atomic(), audit_operation(operation="gamedev.run.queue", principal=principal):
        task = Task.objects.select_for_update().filter(pk=command.task_id).first()
        if task is None:
            raise ValueError("The task does not exist.")
        if task.version != command.version or task.status == "done":
            raise ValueError("Task changed or is complete. Reload before starting work.")
        if not command.prompt.strip():
            raise ValueError("Describe the work first.")
        if task.runs.filter(status__in=("queued", "running", "review")).exists():
            raise ValueError("This task already has work running or awaiting review.")
        if task.agent == "opencode":
            raise ValueError("Only the Codex worker can dispatch an OpenCode delegation.")
        run = Run.objects.create(task=task, prompt=command.prompt.strip(), kind=command.kind)
        task.status = "active"
        task.version += 1
        task.save(update_fields=("status", "version", "updated_at"))
        record_operation(
            "gamedev.run.queue", f"Queued run #{run.pk}", metadata={"run_id": run.pk, "task_id": task.pk}, required=True
        )
        return {"ok": True, "id": run.pk}


def decide_run(command, *, principal):
    principal.require("gamedev.write")
    with transaction.atomic(), audit_operation(operation="gamedev.run.decide", principal=principal):
        run = Run.objects.select_for_update().select_related("task").filter(pk=command.run_id).first()
        if run is None:
            raise ValueError("The run does not exist.")
        if run.version != command.version:
            raise ValueError("Run changed. Reload before deciding.")
        if command.decision == "cancel":
            if run.status not in ("queued", "running"):
                raise ValueError("Only pending work can be canceled.")
            run.cancel_requested = True
            if run.status == "queued":
                run.status = "canceled"
        else:
            if run.status != "review" or not command.note.strip():
                raise ValueError("Review requires a finished run and a decision note.")
            if command.decision == "accept" and (not run.passed or run.task.children.exclude(status="done").exists()):
                raise ValueError("Acceptance requires passing checks and completed delegations.")
            run.status = "accepted" if command.decision == "accept" else "rejected"
        run.decision_note = command.note.strip()
        run.version += 1
        run.save()
        if command.decision in ("accept", "reject"):
            task = Task.objects.select_for_update().get(pk=run.task_id)
            task.status = "done" if command.decision == "accept" else "blocked"
            task.evidence = f"Run #{run.pk}: {run.decision_note}\nEvidence: {run.diff_sha256}"
            task.version += 1
            task.save(update_fields=("status", "evidence", "version", "updated_at"))
        record_operation(
            "gamedev.run.decide",
            f"Run #{run.pk}: {command.decision}",
            metadata={"run_id": run.pk, "note": run.decision_note, "diff_sha256": run.diff_sha256},
            required=True,
        )
        return {"ok": True, "id": run.pk}


def capabilities():
    return (
        CapabilitySpec(
            "gamedev.run.queue",
            "Queue supervised work",
            "remote_write",
            "gamedev.write",
            QueueRun,
            queue_run,
            label="Start work",
        ),
        CapabilitySpec(
            "gamedev.run.decide",
            "Review or cancel work",
            "remote_write",
            "gamedev.write",
            DecideRun,
            decide_run,
            label="Review work",
        ),
    )
