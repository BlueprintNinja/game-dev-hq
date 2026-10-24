"""Globally shared, sanitized decisions from local rows only; no principal."""

from datetime import timedelta
from types import SimpleNamespace

from django.db.models import Count, Max, Min, Q
from django.urls import reverse
from django.utils import timezone

from hq_sdk.ui import Insight

from .mission import attention_kind, attention_query, run_url
from .models import InfrastructureSample, ProjectProfile, Task, TraceSpan

DECISION_LIMIT = 100
EXPORT_LIMIT = 20
FRESHNESS = timedelta(seconds=90)
EXPORT_FAILURE_AGE = timedelta(minutes=5)
EXPORT_BACKLOG_AGE = timedelta(minutes=10)
EXPORT_ATTEMPTS = 3


def summary(
    key, title, count, body, *, url=None, since=None, observed_at=None, status="attention", family="Game Dev operations"
):
    return Insight(
        status,
        "Game Dev",
        title,
        str(count),
        body,
        action="Review evidence",
        url=url or reverse("gamedev:metrics"),
        key=key,
        family=family,
        magnitude=count,
        since=since,
        observed_at=observed_at,
    )


def decision(run):
    group = attention_kind(run)
    if group == "failures":
        title = "Run needs problem review"
        body = "The current attempt failed, was canceled, or did not pass verification. Review the run page."
    elif group == "changes" and run.status == "accepted":
        title = "Accepted coding changes await apply"
        body = "Approval and apply are separate. Review the existing apply form and its destination checks."
    elif group == "changes":
        title = "Coding changes await review"
        body = "Saved changes and passing checks are available for review. This does not establish gameplay acceptance."
    else:
        title = "Run result needs acknowledgement"
        body = "Review the result in its existing form. Passing checks or an empty diff do not establish completed game output."
    # updated_at is not a durable status-transition timestamp: do not guess since.
    return summary(
        f"run:{run.pk}",
        f"{title} (run #{run.pk})",
        1,
        body,
        url=run_url(run),
        observed_at=run.updated_at,
        family=title,
        status="serious" if group == "failures" else "attention",
    )


def decisions():
    rows = list(
        attention_query().values(
            "id",
            "kind",
            "status",
            "passed",
            "has_diff",
            "already_applied",
            "updated_at",
        )[: DECISION_LIMIT + 1]
    )
    items = [decision(SimpleNamespace(pk=row["id"], **row)) for row in rows[:DECISION_LIMIT]]
    if len(rows) > DECISION_LIMIT:
        items.append(
            summary(
                "decisions:limit",
                "Review remaining Game Dev decisions",
                1,
                "Only 100 current decisions are shown, across all projects and all ages. Open the board for the remaining work.",
                url=reverse("gamedev:index"),
                status="attention",
                family="Game Dev coverage",
            )
        )
    return items


def worker_items(now):
    profiles = ProjectProfile.objects.aggregate(
        total=Count("pk"),
        stale=Count("pk", filter=Q(worker_seen_at__lt=now - FRESHNESS)),
        oldest=Min("worker_seen_at", filter=Q(worker_seen_at__lt=now - FRESHNESS)),
    )
    # A newly created task gets 90 seconds for the first worker publication.
    missing = (
        Task.objects.filter(created_at__lt=now - FRESHNESS)
        .exclude(
            project_slug__in=ProjectProfile.objects.values("project_slug"),
        )
        .values("project_slug")
        .distinct()
        .count()
    )
    items = []
    if missing:
        items.append(
            summary(
                "worker:missing",
                "Worker heartbeat missing",
                missing,
                "Projects with work older than 90 seconds have no local worker heartbeat.",
            )
        )
    if profiles["stale"]:
        stamp = profiles["oldest"]
        items.append(
            summary(
                "worker:stale",
                "Worker heartbeat out of date",
                profiles["stale"],
                "Local worker heartbeats were last observed more than 90 seconds ago.",
                since=stamp + FRESHNESS,
                observed_at=stamp,
            )
        )
    if not profiles["total"] and not missing:
        items.append(
            summary(
                "worker:unknown",
                "Worker heartbeat not established",
                0,
                "No heartbeat is available. New work has a 90 second startup grace period; health is unknown.",
                status="neutral",
            )
        )
    return items


def sample_items(now):
    sample = InfrastructureSample.objects.order_by("-observed_at", "-id").values_list("observed_at", flat=True).first()
    if sample is None:
        return [
            summary(
                "sample:missing",
                "Collector sample missing",
                1,
                "No local collector sample is available; workstation health is unknown.",
            )
        ]
    if sample < now - FRESHNESS:
        return [
            summary(
                "sample:stale",
                "Collector sample out of date",
                1,
                "The latest local sample was observed more than 90 seconds ago; current health is unknown.",
                since=sample + FRESHNESS,
                observed_at=sample,
            )
        ]
    return []


def export_items(now):
    rows = list(
        TraceSpan.objects.filter(exported_at__isnull=True)
        .filter(
            Q(finished_at__lt=now - EXPORT_FAILURE_AGE, export_attempts__gte=EXPORT_ATTEMPTS)
            | Q(finished_at__lt=now - EXPORT_BACKLOG_AGE)
        )
        .values("run_id")
        .annotate(total=Count("pk"), observed=Max("finished_at"))
        .order_by("run_id")[: EXPORT_LIMIT + 1]
    )
    items = [
        summary(
            f"export:run:{row['run_id']}",
            f"Trace export needs review (run #{row['run_id']})",
            row["total"],
            "Finished local spans remain unexported: older than 5 minutes with at least 3 attempts, or a backlog older than 10 minutes.",
            url=reverse("gamedev:trace", args=[row["run_id"]]),
            observed_at=row["observed"],
            family="Game Dev trace exports",
        )
        for row in rows[:EXPORT_LIMIT]
    ]
    if len(rows) > EXPORT_LIMIT:
        items.append(
            summary(
                "exports:limit",
                "Review remaining trace export problems",
                1,
                "Only 20 affected runs are shown. Open metrics for local export evidence.",
                status="attention",
            )
        )
    return items


def attention_items():
    """Frozen pickle-safe values; the host owns dependency invalidation and TTL."""
    now = timezone.now()
    return tuple(decisions() + worker_items(now) + sample_items(now) + export_items(now))
