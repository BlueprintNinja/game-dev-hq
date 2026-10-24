"""Bounded, read-only dashboard projections; no network or process calls."""

import math
from collections import Counter
from datetime import timedelta
from typing import Literal

from django.db.models import Count
from django.utils import timezone

from hq_sdk.capabilities import CapabilitySpec, StrictCommand

from .models import InfrastructureSample, ProjectProfile, Run, TraceSpan


class MetricsQuery(StrictCommand):
    project: str = ""
    hours: Literal[1, 24, 168, 720] = 24


def percentile(values, fraction):
    return round(sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)], 3) if values else None


def run_summary(runs):
    checks = Counter()
    engines = Counter()
    usage = Counter()
    coverage = Counter()
    durations, queues, approvals = [], [], []
    for run in runs:
        engines[run.engine or "not reported"] += 1
        for check in run.metrics.get("checks", []):
            checks["passed" if check.get("passed") else "failed"] += 1
            checks["kind:" + str(check.get("kind", "check"))] += 1
        for name, value in run.metrics.items():
            if name.startswith(("llm.", "hq.tokens.", "hq.tools.")) and isinstance(value, (int, float)):
                usage[name] += value
                coverage[name] += 1
        if run.started_at and run.finished_at:
            durations.append(max(0, (run.finished_at - run.started_at).total_seconds()))
        if run.started_at:
            queues.append(max(0, (run.started_at - run.created_at).total_seconds()))
        if run.approved_at and run.finished_at:
            approvals.append(max(0, (run.approved_at - run.finished_at).total_seconds()))
    return {
        "engines": dict(engines),
        "checks": dict(checks),
        "reported_usage": dict(usage),
        "usage_run_coverage": dict(coverage),
        "duration_p50_seconds": percentile(durations, 0.5),
        "duration_p95_seconds": percentile(durations, 0.95),
        "queue_p95_seconds": percentile(queues, 0.95),
        "approval_p50_seconds": percentile(approvals, 0.5),
        "timed_runs": len(durations),
    }


def snapshot(command, *, principal):
    principal.require("gamedev.metrics.read")
    since = timezone.now() - timedelta(hours=command.hours)
    query = Run.objects.filter(created_at__gte=since)
    if command.project:
        query = query.filter(task__project_slug=command.project)
    counts = {row["status"]: row["total"] for row in query.values("status").annotate(total=Count("id"))}
    kinds = {row["kind"]: row["total"] for row in query.values("kind").annotate(total=Count("id"))}
    runs = list(
        query.only("id", "engine", "metrics", "started_at", "finished_at", "created_at", "approved_at").order_by("-id")[
            :1000
        ]
    )
    latest = InfrastructureSample.objects.first()
    profiles = list(
        ProjectProfile.objects.values("project_slug", "coding_enabled", "uefn_configured", "worker_seen_at")
    )
    return {
        "ok": True,
        "as_of": timezone.now().isoformat(),
        "project": command.project or "all",
        "hours": command.hours,
        "counts": counts,
        "kinds": kinds,
        "total": sum(counts.values()),
        "detail_runs": len(runs),
        "delegations": query.filter(delegated_by__isnull=False).count(),
        "retries": query.filter(retried_from__isnull=False).count(),
        "fallbacks": query.exclude(fallback_reason="").count(),
        "applied": query.filter(kind="apply", status="accepted").count(),
        "rollbacks": query.filter(kind="rollback", status="accepted").count(),
        "summary": run_summary(runs),
        "infrastructure": latest.readings if latest else {},
        "infrastructure_as_of": latest.observed_at.isoformat() if latest else None,
        "infrastructure_stale": latest is None or latest.observed_at < timezone.now() - timedelta(seconds=90),
        "profiles": [
            {
                **profile,
                "worker_seen_at": profile["worker_seen_at"].isoformat(),
                "stale": profile["worker_seen_at"] < timezone.now() - timedelta(seconds=90),
            }
            for profile in profiles
        ],
        "traces": {
            "recorded": TraceSpan.objects.filter(run__in=query).count(),
            "pending_export": TraceSpan.objects.filter(
                run__in=query, exported_at__isnull=True, finished_at__isnull=False
            ).count(),
        },
    }


def capabilities():
    return (
        CapabilitySpec(
            "gamedev.metrics.snapshot",
            "Read aggregate harness metrics",
            "read",
            "gamedev.metrics.read",
            MetricsQuery,
            snapshot,
            label="Read harness metrics",
        ),
    )
