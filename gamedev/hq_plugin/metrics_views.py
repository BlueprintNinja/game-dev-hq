import json

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from hq_sdk.capabilities import execute_capability, web_principal
from hq_sdk.pages import page_context
from hq_sdk.web import capability_required

from .models import Run


@capability_required("gamedev.traces.read")
@require_GET
def trace(request, run_id):
    run = get_object_or_404(Run.objects.only("id"), pk=run_id)
    spans = list(run.spans.all()[:500])
    rows = [
        {
            "span": span,
            "duration": round((span.finished_at - span.started_at).total_seconds(), 3) if span.finished_at else None,
        }
        for span in spans
    ]
    return render(
        request,
        "gamedev_hq/trace.html",
        {
            **page_context(f"Trace for run #{run.pk}", "Execution stages, tool events and reported usage."),
            "run": run,
            "rows": rows,
            "phoenix_url": getattr(settings, "GAMEDEV_PHOENIX_URL", ""),
        },
    )


@capability_required("gamedev.metrics.read")
@require_GET
def metrics(request):
    principal = web_principal(request.user)
    hours = request.GET.get("hours", "24")
    if hours not in ("1", "24", "168", "720"):
        return JsonResponse({"error": "Select a supported time window."}, status=400)
    result = execute_capability(
        "gamedev.metrics.snapshot",
        {"project": request.GET.get("project", ""), "hours": int(hours)},
        principal=principal,
    )
    if not result.get("ok"):
        return JsonResponse({"error": "Select a supported time window."}, status=400)
    data = result.get("result", result)
    if request.GET.get("format") == "json":
        response = JsonResponse(data)
        response["Cache-Control"] = "private, no-store"
        return response
    recent = Run.objects.only("id", "status", "kind", "engine").order_by("-id")
    project = request.GET.get("project", "")
    if project:
        recent = recent.filter(task__project_slug=project)
    template = "gamedev_hq/metrics.html#readings" if request.GET.get("fragment") == "1" else "gamedev_hq/metrics.html"
    return render(
        request,
        template,
        {
            **page_context("Harness", "Runs, checks, agent usage and workstation health."),
            "metrics": data,
            "all_readings": json.dumps(data.get("infrastructure", {}), indent=2),
            "recent": recent[:20],
            "phoenix_url": getattr(settings, "GAMEDEV_PHOENIX_URL", "")
            if principal.permits("gamedev.traces.read")
            else "",
            "project": project,
            "hours": request.GET.get("hours", "24"),
        },
    )
