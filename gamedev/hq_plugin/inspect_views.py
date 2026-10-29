"""Read-only Inspect pages; no agent control, file exports, or side effects."""

from django.http import Http404
from django.shortcuts import render
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET

from hq_sdk.pages import page_context
from hq_sdk.web import capability_required

from ..session_inspect import codex_root, list_codex_sessions, read_codex_session
from .models import Run


def _private(response):
    response["Cache-Control"] = "private, no-store"
    patch_vary_headers(response, ("Cookie",))
    return response


@capability_required("gamedev.traces.read")
@require_GET
def inspect(request):
    root = codex_root()
    runs = list(
        Run.objects.select_related("task")
        .only("id", "status", "kind", "engine", "task__id", "task__title", "task__project_slug")
        .order_by("-id")[:30]
    )
    return _private(
        render(
            request,
            "gamedev_hq/inspect.html",
            {
                **page_context("Inspect", "Read-only navigation for local agent sessions and HQ runs."),
                "runs": runs,
                "codex_enrolled": root is not None,
                "sessions": list_codex_sessions(root),
            },
        )
    )


@capability_required("gamedev.traces.read")
@require_GET
def codex_session(request, key):
    session = read_codex_session(codex_root(), key)
    if session is None:
        raise Http404("The enrolled session was not found in the bounded history window.")
    return _private(
        render(
            request,
            "gamedev_hq/inspect_session.html",
            {
                **page_context("Codex session", "Structural events only; conversation and tool content are hidden."),
                "session": session,
            },
        )
    )
