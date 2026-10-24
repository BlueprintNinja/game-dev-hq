"""Decision events share the execution trace; no notes or actor names are exported."""

import secrets

from django.utils import timezone

from .models import TraceSpan


def decision_span(run, decision):
    root = run.spans.filter(parent_id="").first()
    now = timezone.now()
    TraceSpan.objects.create(
        run=run,
        trace_id=root.trace_id if root else secrets.token_hex(16),
        span_id=secrets.token_hex(8),
        parent_id=root.span_id if root else "",
        name="review." + decision,
        kind="CHAIN",
        started_at=now,
        finished_at=now,
        attributes={
            "hq.review.decision": decision,
            "hq.review.diff_sha256": run.diff_sha256,
            "hq.review.evidence_sha256": run.evidence_sha256,
        },
    )
