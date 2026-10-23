"""Durable local spans; exporting never runs inside the worker or a web request."""

import secrets
from contextlib import contextmanager
from contextvars import ContextVar

lazy from django.db import transaction
from django.utils import timezone

lazy from hq_sdk.audit import audit_operation, record_operation
lazy from hq_sdk.capabilities import cli_principal

from .tool_tracing import ToolObserver
from .usage import agent_usage
lazy from .hq_plugin.models import TraceSpan

_parent = ContextVar("gamedev_span", default=None)


@contextmanager
def stage(run_id, name, kind="CHAIN", attributes=None):
    parent = _parent.get()
    principal = cli_principal()
    with transaction.atomic(), audit_operation(operation="gamedev.trace.start", principal=principal):
        span = TraceSpan.objects.create(
            run_id=run_id,
            name=name,
            kind=kind,
            trace_id=parent.trace_id if parent else secrets.token_hex(16),
            span_id=secrets.token_hex(8),
            parent_id=parent.span_id if parent else "",
            started_at=timezone.now(),
            attributes=attributes or {},
        )
        record_operation("gamedev.trace.start", f"Started {name} for run #{run_id}", required=True)
    token = _parent.set(span)
    values = {}
    try:
        yield values
    except BaseException as exc:
        values["error.type"] = type(exc).__name__
        raise
    finally:
        _parent.reset(token)
        with transaction.atomic(), audit_operation(operation="gamedev.trace.finish", principal=principal):
            span.finished_at = timezone.now()
            span.attributes.update(values)
            span.failed = (
                "error.type" in values
                or values.get("process.exit_code", 0) != 0
                or values.get("hq.verification.passed") is False
            )
            span.save(update_fields=("finished_at", "attributes", "failed"))
            record_operation("gamedev.trace.finish", f"Finished {name} for run #{run_id}", required=True)


def observed_process(run_id, name, process, *args, **kwargs):
    parent = _parent.get()
    if parent is None:
        return process(*args, **kwargs)
    run_id = run_id or parent.run_id
    kind = "LLM" if name in ("agent", "fallback") else "TOOL"
    with stage(run_id, name, kind) as metrics:
        observer = ToolObserver(_parent.get()) if kind == "LLM" else None
        original_progress = kwargs.get("progress", lambda text: None)

        def progress(text):
            if observer:
                observer.feed(text)
            original_progress(text)

        kwargs["progress"] = progress
        code, output = process(*args, **kwargs)
        if observer:
            observer.feed(output, final=True)
        metrics["process.exit_code"] = code
        metrics["hq.output.bytes"] = len(output.encode())
        if kind == "LLM":
            metrics.update(agent_usage(output))
        return code, output
