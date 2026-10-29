"""Bounded projections of enrolled session content for an authorized operator."""

import json
import re
import sqlite3
from collections import deque
from contextlib import closing

from .inspect_redaction import redact
from .opencode_inspect import MAX_EVENTS, _read, _session_key, _when
from .session_inspect import MAX_LINE_BYTES, MAX_TAIL_BYTES, _parse_event, _stamp, recent_session_files, session_key

MAX_TEXT = 16000
PAGE_PARTS = MAX_EVENTS // 2


def _text(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    value = redact(value)
    return value[:MAX_TEXT] + ("\n[Content truncated]" if len(value) > MAX_TEXT else "")


def _entry(kind, stamp, content, label=""):
    return {"kind": kind, "stamp": stamp, "content": _text(content), "label": _text(label)}


def _group(events):
    groups = []
    for event in events:
        if not groups or event["kind"] == "user":
            groups.append([])
        groups[-1].append(event)
    return groups


def _codex_event(event):
    if not isinstance(event, dict) or event.get("type") != "response_item":
        return None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    stamp = _stamp(event.get("timestamp"))
    if kind == "message" and payload.get("role") in ("user", "assistant"):
        parts = payload.get("content")
        if isinstance(parts, list):
            text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str))
            return _entry(payload["role"], stamp, text) if text else None
    if kind in ("function_call", "custom_tool_call"):
        return _entry(
            "tool", stamp, payload.get("arguments", payload.get("input", "")), str(payload.get("name", "Tool"))
        )
    if kind in ("function_call_output", "custom_tool_call_output"):
        return _entry("result", stamp, payload.get("output", ""), "Tool result")
    if kind == "reasoning" and isinstance(payload.get("summary"), list):
        text = "\n".join(
            p.get("text", "") for p in payload["summary"] if isinstance(p, dict) and isinstance(p.get("text"), str)
        )
        return _entry("summary", stamp, text, "Reasoning summary") if text else None
    return None


def codex_transcript(root, key, *, before=None):
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{24}", key):
        return None
    path = next((p for p in recent_session_files(root) if session_key(p) == key), None)
    if path is None:
        return None
    events = deque(maxlen=MAX_EVENTS)
    try:
        with path.open("rb") as source:
            source.seek(0, 2)
            size = source.tell()
            if before is not None and (not isinstance(before, int) or before < 1 or before > size):
                return None
            end = before if before is not None else size
            offset = max(0, end - MAX_TAIL_BYTES)
            source.seek(offset)
            if offset:
                source.readline(MAX_LINE_BYTES + 1)
            seen = 0
            skipped = False
            while seen < 15000 and source.tell() < end:
                position = source.tell()
                line = source.readline(MAX_LINE_BYTES + 1)
                if not line or source.tell() > end:
                    break
                seen += 1
                parsed = _parse_event(line)
                if parsed is None:
                    skipped = True
                    while line and not line.endswith(b"\n") and seen < 15000:
                        line = source.readline(MAX_LINE_BYTES + 1)
                        seen += 1
                    continue
                event = _codex_event(parsed)
                if event:
                    events.append((position, event))
        return {
            "groups": _group([event for _, event in events]),
            "partial": skipped or bool(offset) or len(events) == MAX_EVENTS or seen >= 15000,
            "older": events[0][0] if events and events[0][0] > 0 else (offset or None),
        }
    except OSError:
        return None


def opencode_transcript(source, key, *, before=None):
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{24}", key):
        return None
    cursor = None
    if before is not None:
        if not isinstance(before, str) or not re.fullmatch(r"[0-9]{1,16}\.[A-Za-z0-9_-]{1,96}", before):
            return None
        stamp, identifier = before.split(".", 1)
        cursor = (int(stamp), identifier)
    records = _read(source)
    if records is None:
        return None
    db, _ = source
    identifier = next((item[0] for item in records if _session_key(db, item[0]) == key), None)
    if identifier is None:
        return None
    query = """SELECT p.time_created, json_extract(m.data,'$.role'),
                      CASE WHEN length(p.data) <= ? THEN p.data ELSE NULL END
                      , p.id
               FROM part p JOIN message m ON m.id=p.message_id
               WHERE p.session_id=? AND m.session_id=?
                 AND (? IS NULL OR p.time_created < ? OR (p.time_created = ? AND p.id < ?))
               ORDER BY p.time_created DESC, p.id DESC LIMIT ?"""
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as cx:
            cx.execute("PRAGMA query_only=ON")
            cx.set_progress_handler(lambda: 1, 2000000)
            tick, part_id = cursor or (None, None)
            rows = list(
                cx.execute(query, (MAX_LINE_BYTES, identifier, identifier, tick, tick, tick, part_id, PAGE_PARTS + 1))
            )
        events = []
        skipped = False
        for stamp, role, raw, _part_id in reversed(rows[:PAGE_PARTS]):
            if not isinstance(stamp, int) or not isinstance(raw, str) or len(raw) > MAX_LINE_BYTES:
                skipped = True
                continue
            try:
                part = json.loads(raw)
            except ValueError:
                skipped = True
                continue
            if not isinstance(part, dict):
                skipped = True
                continue
            if part.get("type") == "text" and role in ("user", "assistant"):
                events.append(_entry(role, _when(stamp), part.get("text", "")))
            elif part.get("type") == "tool" and isinstance(part.get("state"), dict):
                state = part["state"]
                events.append(_entry("tool", _when(stamp), state.get("input", ""), str(part.get("tool", "Tool"))))
                if "output" in state or "error" in state or state.get("status") == "error":
                    label = "Failed" if state.get("status") == "error" else "Tool result"
                    events.append(_entry("result", _when(stamp), state.get("output", state.get("error", "")), label))
        return {
            "groups": _group(events[-MAX_EVENTS:]),
            "partial": skipped or len(rows) > PAGE_PARTS,
            "older": f"{rows[PAGE_PARTS - 1][0]}.{rows[PAGE_PARTS - 1][3]}" if len(rows) > PAGE_PARTS else None,
        }
    except sqlite3.Error, OSError, ValueError, TypeError:
        return None
