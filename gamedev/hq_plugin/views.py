from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from hq_sdk.capabilities import execute_capability, web_principal
from hq_sdk.pages import page_context
from hq_sdk.resources import list_resource
from hq_sdk.web import capability_required

from .. import apply as apply_module
from .contracts import TRANSITIONS
from .forms import MoveForm, TaskForm
from .models import Run, Task


def dashboard_cards():
    return (
        {
            "id": "gamedev-tasks",
            "label": "Game development tasks",
            "value": str(Task.objects.exclude(status="done").count()),
            "url": reverse("gamedev:index"),
        },
    )


@capability_required("gamedev.read")
@require_http_methods(["GET", "POST"])
def index(request):
    principal = web_principal(request.user)
    projects = list_resource("projects", {"limit": 500}, principal=principal)["items"]
    form = TaskForm(request.POST if request.method == "POST" else None)
    form.fields["project_slug"].choices = [(p["slug"], p["name"]) for p in projects]
    if request.method == "POST" and form.is_valid():
        result = execute_capability("gamedev.task.create", form.cleaned_data, principal=principal)
        if result.get("ok"):
            messages.success(request, "Task saved.")
            return redirect("gamedev:index")
        form.add_error(None, result.get("error", {}).get("message", "Task could not be saved."))
    tasks = list(Task.objects.prefetch_related("runs").all())
    names = {p["slug"]: p["name"] for p in projects}
    rows = []
    for task in tasks:
        move = MoveForm(
            initial={"task_id": task.pk, "version": task.version, "evidence": task.evidence},
            auto_id=f"id_task_{task.pk}_%s",
        )
        move.fields["status"].choices = [(value, value.title()) for value in TRANSITIONS[task.status]]
        rows.append({"task": task, "project": names.get(task.project_slug, task.project_slug), "move": move})
    return render(
        request,
        "gamedev_hq/board.html",
        {
            **page_context("Game Dev", "Plan real work. Codex leads; OpenCode receives explicit delegations."),
            "form": form,
            "rows": rows,
            "has_projects": bool(projects),
            "showcase_url": getattr(settings, "GAMEDEV_SHOWCASE_URL", ""),
        },
    )


@capability_required("gamedev.write")
@require_POST
def move(request):
    form = MoveForm(request.POST)
    if form.is_valid():
        result = execute_capability("gamedev.task.move", form.cleaned_data, principal=web_principal(request.user))
        if result.get("ok"):
            messages.success(request, "Task updated.")
        else:
            messages.error(request, result.get("error", {}).get("message", "Task could not be updated."))
    else:
        messages.error(request, "Check the task, status and evidence and try again.")
    return redirect("gamedev:index")


@capability_required("gamedev.write")
@require_POST
def queue(request):
    result = execute_capability(
        "gamedev.run.queue",
        {
            "task_id": request.POST.get("task_id"),
            "version": request.POST.get("version"),
            "prompt": request.POST.get("prompt", ""),
            "kind": request.POST.get("kind", "coding"),
        },
        principal=web_principal(request.user),
    )
    if result.get("ok"):
        return redirect("gamedev:run", run_id=result["id"])
    messages.error(request, result.get("error", {}).get("message", "Work could not be queued."))
    return redirect("gamedev:index")


@capability_required("gamedev.read")
@require_http_methods(["GET"])
def run_detail(request, run_id):

    run = get_object_or_404(Run.objects.select_related("task"), pk=run_id)
    template = "gamedev_hq/run.html#progress" if request.GET.get("fragment") == "1" else "gamedev_hq/run.html"
    return render(
        request,
        template,
        {
            **page_context(f"Run #{run.pk}", run.task.title),
            "run": run,
            "changed_paths": changed_paths(run),
            "applied_run": run.applies,
        },
    )


def changed_paths(run):
    """The files a run's captured changes touch, as a preview of an apply."""
    if not run.diff:
        return []
    try:
        return [path for path, removed in apply_module.changed_paths(run.diff) if not removed]
    except apply_module.Refusal:
        return []


@capability_required("gamedev.write")
@require_POST
def decide(request, run_id):
    result = execute_capability(
        "gamedev.run.decide",
        {
            "run_id": run_id,
            "version": request.POST.get("version"),
            "decision": request.POST.get("decision"),
            "note": request.POST.get("note", ""),
            "diff_sha256": request.POST.get("diff_sha256", ""),
        },
        principal=web_principal(request.user),
    )
    if not result.get("ok"):
        messages.error(request, result.get("error", {}).get("message", "Decision could not be saved."))
    return redirect("gamedev:run", run_id=run_id)


@capability_required("gamedev.write")
@require_POST
def retry(request, run_id):
    result = execute_capability(
        "gamedev.run.retry",
        {
            "run_id": run_id,
            "version": request.POST.get("version"),
            "note": request.POST.get("note", ""),
        },
        principal=web_principal(request.user),
    )
    if result.get("ok"):
        return redirect("gamedev:run", run_id=result["id"])
    messages.error(request, result.get("error", {}).get("message", "The work could not be retried."))
    return redirect("gamedev:run", run_id=run_id)


@capability_required("gamedev.write")
@require_POST
def apply(request, run_id):
    result = execute_capability(
        "gamedev.run.apply",
        {
            "run_id": run_id,
            "version": request.POST.get("version"),
            "note": request.POST.get("note", ""),
        },
        principal=web_principal(request.user),
    )
    if result.get("ok"):
        return redirect("gamedev:run", run_id=result["id"])
    messages.error(request, result.get("error", {}).get("message", "The changes could not be queued for apply."))
    return redirect("gamedev:run", run_id=run_id)


@capability_required("gamedev.write")
@require_POST
def rollback(request, run_id):
    result = execute_capability(
        "gamedev.run.rollback",
        {
            "run_id": run_id,
            "version": request.POST.get("version"),
            "note": request.POST.get("note", ""),
        },
        principal=web_principal(request.user),
    )
    if result.get("ok"):
        return redirect("gamedev:run", run_id=result["id"])
    messages.error(request, result.get("error", {}).get("message", "The rollback could not be queued."))
    return redirect("gamedev:run", run_id=run_id)
