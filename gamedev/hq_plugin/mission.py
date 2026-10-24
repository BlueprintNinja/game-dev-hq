"""Bounded project read projections. Never execute work or inspect raw run content."""

from datetime import timedelta

from django.db.models import Exists, F, OuterRef, Q, Subquery
from django.urls import reverse
from django.utils import timezone

from hq_sdk.ui import Insight, Kpi, ListRow

from .models import InfrastructureSample, ProjectProfile, Run, Task
from .worker_state import WORK_KINDS

LIMIT = 100
RECENT_LIMIT = 20
RUN_FIELDS = (
    "id",
    "task_id",
    "task__title",
    "task__agent",
    "kind",
    "status",
    "engine",
    "requested_profile",
    "selected_model",
    "escalation_root_id",
    "passed",
    "delegated_by_id",
    "applies_id",
    "created_at",
    "updated_at",
)
KINDS = {
    "coding": "Coding",
    "uefn_inspect": "Editor check",
    "uefn_build": "Compilation",
    "apply": "Apply changes",
    "rollback": "Restore files",
}


def run_url(run):
    return reverse("gamedev:run", kwargs={"run_id": run.pk})


def run_row(run):
    detail = f"{KINDS[run.kind]} · {run.status} · Engine: {run.engine or 'not reported'}"
    if run.delegated_by_id:
        detail += f" · Delegated by run #{run.delegated_by_id}"
    detail += f" · Profile: {run.requested_profile or 'unknown'} · Selected: {run.selected_model or 'unknown'}"
    if run.escalation_root_id:
        detail += f" · Escalated from run #{run.escalation_root_id}"
    detail += evidence_detail(run.check_outcomes)
    return ListRow(
        title=f"Run #{run.pk}: {run.task.title}",
        detail=detail,
        meta=run.updated_at.isoformat(timespec="seconds"),
        url=run_url(run),
    )


def evidence_detail(checks):
    if not isinstance(checks, list) or not checks:
        return "; Check evidence: not reported"
    labels = {
        "source": "Source",
        "compilation": "Compilation",
        "harness": "Work process",
        "gameplay": "Gameplay",
        "uefn_build": "Compilation",
        "uefn_inspect": "Editor",
    }
    parts = []
    for check in checks[:12]:
        if isinstance(check, dict):
            label = labels.get(check.get("kind"), "Other check")
            result = "passed" if check.get("passed") is True else "did not pass"
            parts.append(f"{label}: {result}")
    suffix = "; more evidence on run page" if len(checks) > 12 else ""
    return "; " + "; ".join(parts) + suffix


def run_query(project):
    return (
        Run.objects.filter(task__project_slug=project)
        .select_related("task")
        .only(*RUN_FIELDS)
        .annotate(check_outcomes=F("metrics__checks"))
    )


def latest_work():
    return Run.objects.filter(task_id=OuterRef("task_id"), kind__in=WORK_KINDS).order_by("-id")


def current_query(project):
    """Latest work per task, plus latest file operation per source and kind.

    File operations stay relevant only while their approved source is current.
    Recovery is tested across the full history, before any display limit.
    """
    operations = Run.objects.filter(
        task_id=OuterRef("task_id"), kind=OuterRef("kind"), applies_id=OuterRef("applies_id")
    ).order_by("-id")
    applied = Run.objects.filter(
        applies_id=OuterRef("pk"), kind="apply", status="accepted", rolled_back_at__isnull=True
    )
    return (
        run_query(project)
        .annotate(
            has_diff=~Q(diff__regex=r"^\s*$"),
            already_applied=Exists(applied),
        )
        .filter(
            Q(kind__in=WORK_KINDS, pk=Subquery(latest_work().values("id")[:1]))
            | (
                Q(kind__in=("apply", "rollback"), pk=Subquery(operations.values("id")[:1]))
                & (
                    Q(applies_id=Subquery(latest_work().values("id")[:1]))
                    | Q(applies__applies_id=Subquery(latest_work().values("id")[:1]))
                )
            )
        )
        .order_by("-id")
    )


def attention_kind(run):
    if run.status in ("failed", "canceled") or (run.status == "review" and not run.passed):
        return "failures"
    if (
        run.kind == "coding"
        and run.has_diff
        and run.passed
        and (run.status == "review" or (run.status == "accepted" and not run.already_applied))
    ):
        return "changes"
    if run.status == "review":
        return "results"
    return ""


def suggestion(run, group):
    if group == "failures":
        body = "The latest attempt needs attention. Review its evidence and the available retry form."
        label = "Review the latest problem"
    elif group == "changes":
        body = "Checks passed and saved changes are present. Review or apply them using the existing run form."
        label = "Review coding changes"
    else:
        body = "Review the reported result. Passing checks alone do not establish completed game output."
        label = "Acknowledge the result"
    return Insight("attention", "Next step", run.task.title, f"Run #{run.pk}", body, action=label, url=run_url(run))


def health(project, now):
    """Only timestamps establish freshness; readings never imply overall health."""
    profile = ProjectProfile.objects.filter(pk=project).only("worker_seen_at").first()
    sample = InfrastructureSample.objects.only("id", "observed_at").first()
    readings = []
    for label, stamp in (
        ("Worker heartbeat", profile.worker_seen_at if profile else None),
        ("Workstation metrics (all projects)", sample.observed_at if sample else None),
    ):
        state = "Missing" if stamp is None else "Out of date" if stamp < now - timedelta(seconds=90) else "Current"
        readings.append(Kpi(label, state, stamp.isoformat(timespec="seconds") if stamp else "No reading available"))
    readings.extend(
        (
            Kpi("Cost", "Unavailable", "No cost total is established here"),
            Kpi("Project resource use", "Unavailable", "Workstation readings are global"),
        )
    )
    return readings


def project_mission(project, *, principal, now=None):
    principal.require("gamedev.read")
    now = now or timezone.now()
    query = current_query(project)
    # Filter attention in SQL before bounding, so completed history cannot hide a problem.
    current = list(
        query.filter(
            Q(status__in=("review", "failed", "canceled"))
            | Q(kind="coding", status="accepted", already_applied=False, has_diff=True)
        )[: LIMIT + 1]
    )
    active = list(run_query(project).filter(status__in=("queued", "running")).order_by("id")[: LIMIT + 1])
    tasks = Task.objects.filter(project_slug=project, status__in=("queued", "active", "blocked", "review"))
    pending = list(
        tasks.filter(~Exists(Run.objects.filter(task_id=OuterRef("pk"))))
        .only("id", "title", "status")
        .order_by("id")[: LIMIT + 1]
    )
    recent = list(
        run_query(project)
        .filter(updated_at__gte=now - timedelta(days=7))
        .order_by("-updated_at", "-id")[: RECENT_LIMIT + 1]
    )
    groups = {key: [] for key in ("changes", "results", "failures")}
    suggestions = []
    for run in current[:LIMIT]:
        group = attention_kind(run)
        if group:
            groups[group].append(run_row(run))
            suggestions.append((group, run))
    priority = {"failures": 0, "changes": 1, "results": 2}
    suggestions.sort(key=lambda item: (priority[item[0]], -item[1].pk))
    traces_allowed = principal.permits("gamedev.traces.read")
    return {
        **groups,
        "active": [run_row(run) for run in active[:LIMIT]],
        "pending": [
            ListRow(task.title, detail=f"{task.status} · No run yet", url=reverse("gamedev:index"))
            for task in pending[:LIMIT]
        ],
        "recent": [run_row(run) for run in recent[:RECENT_LIMIT]],
        "suggestions": [suggestion(run, group) for group, run in suggestions[:3]],
        "traces": [
            ListRow(f"Trace for run #{run.pk}", url=reverse("gamedev:trace", args=[run.pk]))
            for run in recent[:RECENT_LIMIT]
        ]
        if traces_allowed
        else [],
        "health": health(project, now) if principal.permits("gamedev.metrics.read") else [],
        "truncated": len(current) > LIMIT or len(active) > LIMIT or len(pending) > LIMIT,
        "recent_truncated": len(recent) > RECENT_LIMIT,
        "kpis": [
            Kpi("Working or queued", len(active[:LIMIT]), "Shown; all ages"),
            Kpi("Coding changes to review or apply", len(groups["changes"]), "Shown; latest task attempt"),
            Kpi("Results to acknowledge", len(groups["results"]), "Shown; latest task attempt"),
            Kpi("Problems to review", len(groups["failures"]), "Shown; unresolved latest attempts"),
        ],
    }
