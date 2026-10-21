from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from hq_sdk.capabilities import execute_capability, web_principal
from hq_sdk.pages import page_context
from hq_sdk.resources import list_resource
from hq_sdk.web import capability_required

from .contracts import TRANSITIONS
from .forms import MoveForm, TaskForm
from .models import Task


def dashboard_cards():
    return ({"id": "gamedev-tasks", "label": "Game development tasks", "value": str(Task.objects.exclude(status="done").count()),
             "url": reverse("gamedev:index")},)


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
    tasks = list(Task.objects.all())
    names = {p["slug"]: p["name"] for p in projects}
    rows = []
    for task in tasks:
        move = MoveForm(initial={"task_id": task.pk, "version": task.version, "evidence": task.evidence},
                        auto_id=f"id_task_{task.pk}_%s")
        move.fields["status"].choices = [(value, value.title()) for value in TRANSITIONS[task.status]]
        rows.append({"task": task, "project": names.get(task.project_slug, task.project_slug), "move": move})
    return render(request, "gamedev_hq/board.html", {
        **page_context("Game Dev", "Plan real work. Codex leads; OpenCode receives explicit delegations."),
        "form": form, "rows": rows, "has_projects": bool(projects),
        "showcase_url": getattr(settings, "GAMEDEV_SHOWCASE_URL", ""),
    })


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
