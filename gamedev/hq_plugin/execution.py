"""Audited execution lifecycle. The web process never launches a command."""

from typing import Annotated, Literal

from django.db import transaction
from django.utils import timezone
from pydantic import Field

from hq_sdk.audit import audit_operation, record_operation
from hq_sdk.capabilities import CapabilitySpec, StrictCommand

from .models import Run, Task
from .observations import decision_span

# A run that finished, badly. Work on it may be asked for again.
FINISHED = ("failed", "canceled", "rejected")


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
    # The fingerprint of the changes the reviewer was shown. When it is sent
    # and it does not match the run, the run changed under the reviewer and
    # the decision is refused.
    diff_sha256: Annotated[str, Field(max_length=64)] = ""


class RetryRun(StrictCommand):
    run_id: Annotated[int, Field(gt=0)]
    version: Annotated[int, Field(gt=0)]
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
        run = Run.objects.create(
            task=task, prompt=command.prompt.strip(), kind=command.kind, requested_by=principal.actor
        )
        task.status = "active"
        task.version += 1
        task.save(update_fields=("status", "version", "updated_at"))
        record_operation(
            "gamedev.run.queue", f"Queued run #{run.pk}", metadata={"run_id": run.pk, "task_id": task.pk}, required=True
        )
        return {"ok": True, "id": run.pk}


def cancel_pending(run):
    """A cancel stops work that has not reached review yet."""
    if run.status not in ("queued", "running"):
        raise ValueError("Only pending work can be canceled.")
    run.cancel_requested = True
    if run.status == "queued":
        run.status = "canceled"


def record_review(run, command):
    """A review needs a finished run, a note, and passing evidence to accept."""
    if run.status != "review" or not command.note.strip():
        raise ValueError("Review requires a finished run and a decision note.")
    if command.diff_sha256 and command.diff_sha256 != run.diff_sha256:
        raise ValueError("The captured changes changed. Reload the run before deciding.")
    if command.decision == "accept" and (not run.passed or run.task.children.exclude(status="done").exists()):
        raise ValueError("Acceptance requires passing checks and completed delegations.")
    run.status = "accepted" if command.decision == "accept" else "rejected"
    if command.decision == "accept":
        # The approval names the exact changes and the exact evidence it was
        # given, so an apply can refuse anything that is not the same ones.
        run.approved_diff_sha256 = run.diff_sha256
        run.approved_evidence_sha256 = run.evidence_sha256
        run.approved_at = timezone.now()


def settle_task(run, command):
    """Acceptance closes the task; a rejection sends it back for changes."""
    task = Task.objects.select_for_update().get(pk=run.task_id)
    task.status = "done" if command.decision == "accept" else "blocked"
    task.evidence = f"Run #{run.pk}: {run.decision_note}\nEvidence: {run.diff_sha256}"
    task.version += 1
    task.save(update_fields=("status", "evidence", "version", "updated_at"))


def decide_run(command, *, principal):
    principal.require("gamedev.write")
    with transaction.atomic(), audit_operation(operation="gamedev.run.decide", principal=principal):
        run = Run.objects.select_for_update().select_related("task").filter(pk=command.run_id).first()
        if run is None:
            raise ValueError("The run does not exist.")
        if run.version != command.version:
            raise ValueError("Run changed. Reload before deciding.")
        if command.decision == "cancel":
            cancel_pending(run)
        else:
            record_review(run, command)
        run.decision_note = command.note.strip()
        run.version += 1
        run.save()
        decision_span(run, command.decision)
        if command.decision in ("accept", "reject"):
            settle_task(run, command)
        record_operation(
            "gamedev.run.decide",
            f"Run #{run.pk}: {command.decision}",
            metadata={
                "run_id": run.pk,
                "note": run.decision_note,
                "diff_sha256": run.diff_sha256,
                "approved_diff_sha256": run.approved_diff_sha256,
                "approved_evidence_sha256": run.approved_evidence_sha256,
            },
            required=True,
        )
        return {"ok": True, "id": run.pk}


def retry_run(command, *, principal):
    """Ask for the same work again, after a run that finished badly.

    The retry is a new run, never a replay of the old one: it starts from
    the repository's current revision, in a new worktree, and its result is
    reviewed on its own. A delegated run keeps its provenance, so a
    delegated job that was rejected or that failed can be revised instead of
    leaving its parent blocked forever.
    """
    principal.require("gamedev.write")
    with transaction.atomic(), audit_operation(operation="gamedev.run.retry", principal=principal):
        run = Run.objects.select_for_update().select_related("task").filter(pk=command.run_id).first()
        if run is None:
            raise ValueError("The run does not exist.")
        if run.version != command.version:
            raise ValueError("Run changed. Reload before retrying.")
        if run.status not in FINISHED:
            raise ValueError("Only a run that failed, was canceled or was rejected can be retried.")
        task = Task.objects.select_for_update().get(pk=run.task_id)
        if task.runs.filter(status__in=("queued", "running", "review")).exists():
            raise ValueError("This task already has work running or awaiting review.")
        retried = Run.objects.create(
            task=task,
            prompt=run.prompt,
            kind=run.kind,
            delegated_by=run.delegated_by,
            retried_from=run,
            requested_by=principal.actor,
        )
        task.status = "active"
        task.version += 1
        task.save(update_fields=("status", "version", "updated_at"))
        record_operation(
            "gamedev.run.retry",
            f"Retried run #{run.pk} as run #{retried.pk}",
            metadata={"run_id": run.pk, "retried_run_id": retried.pk, "task_id": task.pk},
            required=True,
        )
        return {"ok": True, "id": retried.pk}


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
        CapabilitySpec(
            "gamedev.run.retry",
            "Ask for the same work again",
            "remote_write",
            "gamedev.write",
            RetryRun,
            retry_run,
            label="Retry work",
        ),
    )
