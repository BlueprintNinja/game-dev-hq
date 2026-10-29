"""Read-only Inspect pages; no agent control, file exports, or side effects."""

from django.http import Http404
from django.shortcuts import render
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET

from hq_sdk.pages import page_context
from hq_sdk.web import capability_required

from ..inspect_transcript import codex_transcript, opencode_transcript
from ..opencode_inspect import list_opencode_sessions, opencode_source, read_opencode_session
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
    source = opencode_source()
    opencode_sessions = list_opencode_sessions(source)
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
                "opencode_enrolled": source is not None,
                "opencode_available": opencode_sessions is not None,
                "opencode_sessions": opencode_sessions or [],
            },
        )
    )


@capability_required("gamedev.traces.read")
@require_GET
def session_detail(request, provider, key):
    cursor = request.GET.get("before")
    if cursor is not None and len(cursor) > 113:
        raise Http404("Invalid transcript position.")
    if provider == "codex":
        if cursor is not None and (not cursor.isascii() or not cursor.isdecimal() or len(cursor) > 16):
            raise Http404("Invalid transcript position.")
        before = int(cursor) if cursor is not None else None
        session = read_codex_session(codex_root(), key)
        transcript = codex_transcript(codex_root(), key, before=before) if session else None
        template = "gamedev_hq/inspect_session.html"
        name = "Codex"
        description = "Recorded conversation and tool activity from your local session."
    elif provider == "opencode":
        session = read_opencode_session(opencode_source(), key)
        transcript = opencode_transcript(opencode_source(), key, before=cursor) if session else None
        template = "gamedev_hq/inspect_opencode.html"
        name = "OpenCode"
        description = "Recorded conversation and tool activity from your local session."
    else:
        raise Http404("Choose a supported agent session source.")
    if session is None:
        raise Http404("The enrolled session was not found in the bounded history window.")
    return _private(
        render(
            request,
            template,
            {
                **page_context(f"{name} session", description),
                "session": session,
                "transcript": transcript,
            },
        )
    )
