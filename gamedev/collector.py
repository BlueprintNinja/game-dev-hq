"""Independent telemetry exporter and sampler; failures never run in a web request."""

import argparse
import json
import os
import time
from datetime import timedelta
from pathlib import Path

lazy import django
lazy from django.db import transaction
lazy from django.utils import timezone

lazy from hq_sdk.audit import audit_operation, record_operation
lazy from hq_sdk.capabilities import cli_principal

from .agent_inventory import opencode_usage
from .infrastructure import sample_infrastructure
from .otlp import export_spans, local_endpoint
from .readiness import wait_until_ready
from .supervisor import WorkerLock
from .usage import agent_usage, compact_checks
lazy from .hq_plugin.models import InfrastructureSample, Run, TraceSpan


def collect_once(endpoint, key, principal, session_source=None):
    readings = sample_infrastructure(Path(os.environ["GAMEDEV_WORKSPACE_DATA"]))
    source = session_source or {}
    readings["opencode_sessions"] = opencode_usage(source.get("opencode_database"), source.get("session_roots", []))
    previous = InfrastructureSample.objects.first()
    if previous:
        elapsed = (timezone.now() - previous.observed_at).total_seconds()
        for name in ("network_sent_bytes", "network_received_bytes"):
            old, new = previous.readings.get(name), readings.get(name)
            if isinstance(old, (int, float)) and isinstance(new, (int, float)) and new >= old and elapsed > 0:
                readings[name + "_per_second"] = (new - old) / elapsed
    pending = list(TraceSpan.objects.filter(exported_at__isnull=True, finished_at__isnull=False).order_by("id")[:100])
    exported = False
    error = ""
    if pending:
        try:
            export_spans(pending, endpoint, key)
            exported = True
        except (OSError, ValueError) as exc:
            error = type(exc).__name__  # response bodies and credentials never enter metrics
    readings["phoenix_export"] = {
        "batch_size": len(pending),
        "accepted": exported,
        "error": error,
        "pending": TraceSpan.objects.filter(exported_at__isnull=True, finished_at__isnull=False).count(),
    }
    with transaction.atomic(), audit_operation(operation="gamedev.metrics.collect", principal=principal):
        backfill_usage()
        mark_exports(pending, exported)
        readings["phoenix_export"]["pending"] = TraceSpan.objects.filter(
            exported_at__isnull=True, finished_at__isnull=False
        ).count()
        InfrastructureSample.objects.create(readings=readings)
        InfrastructureSample.objects.filter(observed_at__lt=timezone.now() - timedelta(days=7)).delete()
        record_operation(
            "gamedev.metrics.collect", "Collected local infrastructure and trace export status", required=True
        )
    return readings


def backfill_usage():
    for run in (
        Run.objects.filter(metrics={})
        .exclude(status__in=("queued", "running"))
        .only("id", "output", "verification", "metrics")[:30]
    ):
        run.metrics = {"usage_observed": True, "checks": compact_checks(run.verification), **agent_usage(run.output)}
        run.save(update_fields=("metrics",))


def mark_exports(pending, exported):
    if not pending:
        return
    for span in pending:
        span.export_attempts += 1
        if exported:
            span.exported_at = timezone.now()
    TraceSpan.objects.bulk_update(pending, ("export_attempts", "exported_at"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--workspace-url", default="")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    endpoint = local_endpoint(config["endpoint"])
    key = Path(config["api_key_file"]).read_text(encoding="utf-8").strip()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tools.windows_hq.settings")
    django.setup()
    principal = cli_principal()
    if args.workspace_url:
        wait_until_ready(local_endpoint(args.workspace_url))
    with WorkerLock(Path(os.environ["GAMEDEV_WORKSPACE_DATA"]) / "telemetry.lock"):
        while True:
            try:
                collect_once(endpoint, key, principal, config)
            except (OSError, ValueError) as exc:
                print(f"Telemetry collection failed: {type(exc).__name__}", flush=True)
            if args.once:
                break
            time.sleep(30)


if __name__ == "__main__":
    main()
