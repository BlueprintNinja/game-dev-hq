"""Read-only Mission Control delivery adapter."""

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET

from hq_sdk.capabilities import web_principal
from hq_sdk.pages import page_context
from hq_sdk.resources import get_resource, list_resource
from hq_sdk.web import capability_required

from .mission import project_mission


@capability_required("gamedev.read")
@capability_required("read")
@require_GET
def mission(request):
    principal = web_principal(request.user)
    projects = list_resource("projects", {"limit": 500}, principal=principal)["items"]
    slug = request.GET.get("project", "")
    selected = None
    if slug:
        try:
            selected = get_resource("projects", slug, principal=principal)
        except LookupError as exc:
            raise Http404("Choose an existing project.") from exc
    data = project_mission(slug, principal=principal) if selected else {}
    template = "gamedev_hq/mission.html"
    if request.headers.get("X-Fragment") == "mission" or request.GET.get("fragment") == "1":
        template += "#mission"
    response = render(
        request,
        template,
        {
            **page_context("Mission Control", "Choose what to work on next."),
            **data,
            "projects": projects,
            "selected": selected,
            "project": slug,
        },
    )
    response["Cache-Control"] = "private, no-store"
    response["Vary"] = "X-Fragment, Cookie"
    return response
