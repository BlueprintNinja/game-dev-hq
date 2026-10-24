"""Aggregate only agent-reported usage. Unknown values remain unavailable."""

import json
import math


def valid_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def add_number(totals, name, value):
    if valid_number(value):
        totals[name] = totals.get(name, 0) + value


def codex_usage(totals, event):
    usage = event.get("usage") or {}
    if not isinstance(usage, dict):
        return
    mapping = {
        "input_tokens": "llm.token_count.prompt",
        "output_tokens": "llm.token_count.completion",
        "cached_input_tokens": "hq.tokens.cached",
        "reasoning_output_tokens": "hq.tokens.reasoning",
    }
    for source, target in mapping.items():
        add_number(totals, target, usage.get(source))
    if valid_number(usage.get("input_tokens")) and valid_number(usage.get("output_tokens")):
        add_number(totals, "llm.token_count.total", usage["input_tokens"] + usage["output_tokens"])


def opencode_step(totals, event):
    part = event.get("part") or {}
    if not isinstance(part, dict) or not isinstance(part.get("tokens", {}), dict):
        return
    tokens = part.get("tokens", {})
    for source, target in (
        ("input", "llm.token_count.prompt"),
        ("output", "llm.token_count.completion"),
        ("reasoning", "hq.tokens.reasoning"),
    ):
        add_number(totals, target, tokens.get(source))
    add_number(totals, "llm.cost.total", part.get("cost"))
    cache = tokens.get("cache") or {}
    if isinstance(cache, dict):
        add_number(totals, "hq.tokens.cached", cache.get("read"))
        add_number(totals, "hq.tokens.cache_write", cache.get("write"))
        add_number(totals, "llm.token_count.prompt", cache.get("read"))
        add_number(totals, "llm.token_count.prompt", cache.get("write"))
    add_number(totals, "llm.token_count.total", tokens.get("total"))


def tool_count(totals, event):
    item = event.get("item") or {}
    if (
        event.get("type") == "item.completed"
        and isinstance(item, dict)
        and item.get("type") in ("command_execution", "file_change", "mcp_tool_call", "web_search")
    ):
        add_number(totals, "hq.tools.count", 1)
    if event.get("type") == "tool_use" and isinstance(event.get("part"), dict):
        add_number(totals, "hq.tools.count", 1)


def agent_usage(output):
    totals = {}
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.completed":
            codex_usage(totals, event)
        if event.get("type") == "step_finish":
            opencode_step(totals, event)
        tool_count(totals, event)
    if (
        "llm.token_count.total" not in totals
        and "llm.token_count.prompt" in totals
        and "llm.token_count.completion" in totals
    ):
        totals["llm.token_count.total"] = totals["llm.token_count.prompt"] + totals["llm.token_count.completion"]
    return totals


def compact_checks(checks):
    """Retain check outcomes without copying potentially large command logs."""
    return [{"kind": str(check.get("kind", "check"))[:40], "passed": check.get("exit_code") == 0} for check in checks]
