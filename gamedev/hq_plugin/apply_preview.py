"""Read-only destination preview from the worker's published policy scopes."""

from ..apply import Refusal, changed_paths, destination_kind
from .models import ProjectProfile


def preview(run):
    if run.delegated_by_id or run.task.parent_id:
        return {"error": "Delegated changes require independent review; apply is unavailable.", "known": True}
    completed = (
        run.applied_runs.filter(kind="apply", status="accepted", rolled_back_at__isnull=True).order_by("-id").first()
    )
    if completed:
        return {"completed_run": completed.pk, "known": True}
    profile = ProjectProfile.objects.filter(project_slug=run.task.project_slug).first()
    if profile is None:
        return {"error": "The worker has not published this project's apply destinations yet.", "known": False}
    try:
        entries = changed_paths(run.diff)
        if any(removed for _path, removed in entries):
            raise Refusal("An apply does not delete files.")
        target = destination_kind([path for path, _removed in entries], profile.apply_scope, profile.source_apply_scope)
    except Refusal as exc:
        return {"error": str(exc), "known": True}
    return {"target": "Source repository" if target == "source" else "UEFN island", "known": True}
