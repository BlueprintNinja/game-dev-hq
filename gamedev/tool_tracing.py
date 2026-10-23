"""CLI tool events without command text, arguments, file paths or tool output."""

import hashlib
import json
from datetime import UTC, datetime

from django.utils import timezone
lazy from django.db import transaction

lazy from hq_sdk.audit import audit_operation, record_operation
lazy from hq_sdk.capabilities import cli_principal

from .usage import valid_number
lazy from .hq_plugin.models import TraceSpan


def object_value(value):
    return value if isinstance(value, dict) else {}


TOOL_TYPES = {"command_execution", "file_change", "mcp_tool_call", "web_search"}


def tool_event(event):
    if event.get("type") in ("item.started", "item.completed"):
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") in TOOL_TYPES:
            return str(item.get("id", "")), item["type"], event["type"] == "item.completed", item
    if event.get("type") == "tool_use":
        part = event.get("part")
        if isinstance(part, dict):
            return str(part.get("id", "")), "opencode_tool", True, part
    return None


class ToolObserver:
    def __init__(self, parent):
        self.parent = parent
        self.cursor = 0
        self.started = {}

    def feed(self, text, *, final=False):
        complete = len(text) if final else text.rfind("\n") + 1
        for line in text[self.cursor : complete].splitlines():
            self.event(line)
        self.cursor = complete

    def event(self, line):
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict) or not (tool := tool_event(event)):
            return
        identity, name, completed, item = tool
        if not identity:
            return
        now = timezone.now()
        if not completed:
            self.started.setdefault(identity, now)
            return
        start, end, precise = self.times(item, identity, now)
        values = {
            "hq.tool.type": name,
            "hq.timing.source": "provider" if precise else "event_receipt",
            "hq.timing.resolution_seconds": 0.001 if precise else 3,
            "hq.timing.precise": precise,
        }
        failed = item.get("exit_code", 0) not in (None, 0) or object_value(item.get("state")).get("status") == "error"
        principal = cli_principal()
        with transaction.atomic(), audit_operation(operation="gamedev.trace.tool", principal=principal):
            TraceSpan.objects.get_or_create(
                span_id=hashlib.sha256((self.parent.span_id + identity).encode()).hexdigest()[:16],
                defaults={
                    "run_id": self.parent.run_id,
                    "trace_id": self.parent.trace_id,
                    "parent_id": self.parent.span_id,
                    "name": "tool." + name,
                    "kind": "TOOL",
                    "started_at": start,
                    "finished_at": end,
                    "failed": failed,
                    "attributes": values,
                },
            )
            record_operation("gamedev.trace.tool", f"Recorded tool event for run #{self.parent.run_id}", required=True)

    def times(self, item, identity, now):
        timing = object_value(object_value(item.get("state")).get("time"))
        start, end = timing.get("start"), timing.get("end")
        if valid_number(start) and valid_number(end) and 0 <= start <= end <= now.timestamp() * 1000 + 60000:
            return datetime.fromtimestamp(start / 1000, UTC), datetime.fromtimestamp(end / 1000, UTC), True
        return self.started.pop(identity, now), now, False
