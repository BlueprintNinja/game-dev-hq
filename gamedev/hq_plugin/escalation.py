"""Explicit, audited escalation is one retry, never an automatic recovery loop."""

from .models import Run


def validate_escalation(run, command):
    if (
        run.kind != "coding"
        or run.task.agent != "codex"
        or run.delegated_by_id
        or run.task.parent_id
        or run.requested_profile != "routine"
        or run.status not in ("failed", "rejected")
        or run.cancel_requested
        or not command.note.strip()
    ):
        raise ValueError("Escalation needs a failed or rejected routine coding run and an operator note.")
    # A task may have many ordinary retries but only one Sol escalation attempt.
    # Check the whole task, including descendants of ordinary retries.
    if Run.objects.filter(task_id=run.task_id, escalated_from__isnull=False).exists():
        raise ValueError("This task has already used its one escalation attempt.")
    if run.engine == "opencode" or run.fallback_reason:
        raise ValueError("Review quota fallback separately before asking for more work.")
