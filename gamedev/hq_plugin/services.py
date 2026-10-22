from typing import Annotated, Literal

from django.db import transaction
from pydantic import Field

from hq_sdk.audit import audit_operation, record_operation
from hq_sdk.capabilities import CapabilitySpec, StrictCommand
from hq_sdk.resources import get_resource

from .apply_execution import capabilities as apply_capabilities
from .contracts import TRANSITIONS
from .execution import capabilities as execution_capabilities
from .models import Task


class CreateTask(StrictCommand):
    project_slug: Annotated[str, Field(min_length=1, max_length=180)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    agent: Literal["codex", "opencode"] = "codex"
    parent_id: Annotated[int, Field(gt=0)] | None = None


class MoveTask(StrictCommand):
    task_id: Annotated[int, Field(gt=0)]
    version: Annotated[int, Field(gt=0)]
    status: Literal["queued", "active", "blocked", "review", "done"]
    evidence: Annotated[str, Field(max_length=4000)] = ""


def create_task(command, *, principal):
    principal.require("gamedev.write")
    if not command.title.strip():
        raise ValueError("A task needs a title.")
    with transaction.atomic(), audit_operation(operation="gamedev.task.create", principal=principal):
        get_resource("projects", command.project_slug, principal=principal)
        parent = None
        if command.parent_id:
            parent = Task.objects.select_for_update().filter(pk=command.parent_id).first()
            if parent is None:
                raise ValueError("The parent task does not exist.")
            if parent.agent != "codex" or parent.project_slug != command.project_slug or parent.status == "done":
                raise ValueError("Delegation requires an unfinished Codex parent in the same project.")
        if command.agent == "opencode" and parent is None:
            raise ValueError("OpenCode requires explicit delegation from Codex.")
        task = Task.objects.create(
            project_slug=command.project_slug, title=command.title.strip(), agent=command.agent, parent=parent
        )
        record_operation(
            "gamedev.task.create",
            f"Created task #{task.pk}: {task.title}",
            metadata={"task_id": task.pk, "agent": task.agent, "parent_id": task.parent_id},
            required=True,
        )
        return {"ok": True, "id": task.pk}


def move_task(command, *, principal):
    principal.require("gamedev.write")
    with transaction.atomic(), audit_operation(operation="gamedev.task.move", principal=principal):
        task = Task.objects.select_for_update().filter(pk=command.task_id).first()
        if task is None:
            raise ValueError("The task does not exist.")
        if task.version != command.version:
            raise ValueError("This task changed. Reload the page before trying again.")
        if command.status not in TRANSITIONS[task.status]:
            raise ValueError(f"Cannot move {task.status} to {command.status}.")
        if command.status == "done":
            latest_run = task.runs.first()
            if latest_run is not None and latest_run.status != "accepted":
                raise ValueError("Review and accept the execution evidence before completing this task.")
            if not command.evidence.strip():
                raise ValueError("Completion evidence is required.")
            if task.children.exclude(status="done").exists():
                raise ValueError("Complete delegated tasks before their parent.")
        before = task.status
        task.status = command.status
        task.evidence = command.evidence.strip()
        task.version += 1
        task.save(update_fields=("status", "evidence", "version", "updated_at"))
        record_operation(
            "gamedev.task.move",
            f"Moved task #{task.pk} from {before} to {task.status}",
            metadata={"task_id": task.pk, "evidence": task.evidence},
            required=True,
        )
        return {"ok": True, "id": task.pk, "version": task.version}


def capabilities():
    return (
        *execution_capabilities(),
        *apply_capabilities(),
        CapabilitySpec(
            "gamedev.task.create",
            "Create a task or explicit delegation",
            "remote_write",
            "gamedev.write",
            CreateTask,
            create_task,
            label="Create game development task",
        ),
        CapabilitySpec(
            "gamedev.task.move",
            "Move a task with review and completion evidence",
            "remote_write",
            "gamedev.write",
            MoveTask,
            move_task,
            label="Move game development task",
        ),
    )
