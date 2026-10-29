"""Opt-in OpenCode session metadata; never retrieve message bodies or tool content."""

import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from .agent_inventory import enrolled

MAX_MESSAGES = 10000
MAX_SESSIONS = 80
MAX_EVENTS = 160
MIN_TIME_MS = 14 * 24 * 3600 * 1000
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")


def opencode_source():
    """Both the read-only database and directory enrollment must be explicit."""
    db = os.environ.get("GAMEDEV_INSPECT_OPENCODE_DB", "").strip()
    root = os.environ.get("GAMEDEV_INSPECT_OPENCODE_ROOT", "").strip()
    if not db or not root:
        return None
    return Path(db).expanduser(), Path(root).expanduser()


def _session_key(db, identifier):
    return sha256((str(db.resolve()) + ":" + identifier).encode("utf-8")).hexdigest()[:24]


def _read(source, *, now=None):
    if source is None:
        return None
    db, root = source
    if not root.is_dir() or root.is_symlink() or not db.is_file() or db.is_symlink():
        return None
    threshold = int((now if now is not None else time.time()) * 1000) - MIN_TIME_MS
    # Project only whitelisted metadata via SQL; do NOT SELECT m.data.
    query = """SELECT s.id, s.directory, m.time_created,
                      json_extract(m.data,'$.role')
               FROM message m JOIN session s ON s.id = m.session_id
               WHERE m.time_created >= ?
               ORDER BY m.time_created DESC LIMIT ?"""
    try:
        uri = db.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as cx:
            cx.execute("PRAGMA query_only=ON")
            ticks = 0

            def bounded():
                nonlocal ticks
                ticks += 1
                return 1 if ticks > 2000 else 0

            cx.set_progress_handler(bounded, 1000)
            data = list(cx.execute(query, (threshold, MAX_MESSAGES)))
    except sqlite3.Error, OSError, ValueError:
        return None
    records = []
    for identifier, directory, timestamp, role in data:
        if not isinstance(identifier, str) or not SESSION_ID.fullmatch(identifier):
            continue
        if not isinstance(directory, str) or not enrolled(directory, [root]):
            continue
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            continue
        records.append((identifier, timestamp, role if role in ("assistant", "user", "system") else "other"))
    return records


def _when(timestamp):
    try:
        return datetime.fromtimestamp(timestamp / 1000, UTC).isoformat(timespec="seconds")
    except ValueError, OSError, OverflowError:
        return "Unknown"


def list_opencode_sessions(source, *, now=None):
    records = _read(source, now=now)
    if records is None:
        return None
    db, _ = source
    sessions = {}
    for identifier, stamp, _role in records:
        if identifier not in sessions:
            if len(sessions) >= MAX_SESSIONS:
                continue
            sessions[identifier] = {
                "key": _session_key(db, identifier),
                "id": identifier,
                "updated": _when(stamp),
                "messages": 0,
            }
        sessions[identifier]["messages"] += 1
    return list(sessions.values())


def _parts(db, identifier):
    """Read only fixed event fields; never retrieve tool inputs or outputs."""
    query = """SELECT time_created, json_extract(data, '$.type'),
                      json_extract(data, '$.state.status')
               FROM part WHERE session_id = ?
               ORDER BY time_created DESC LIMIT ?"""
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as cx:
            cx.execute("PRAGMA query_only=ON")
            cx.set_progress_handler(lambda: 1, 2000000)
            rows = list(cx.execute(query, (identifier, MAX_EVENTS + 1)))
    except sqlite3.Error, OSError, ValueError:
        return [], False, False
    names = {"step-start": "Execution step started", "step-finish": "Execution step finished"}
    events = []
    for stamp, kind, status in rows[:MAX_EVENTS]:
        if isinstance(stamp, bool) or not isinstance(stamp, int):
            continue
        if kind == "tool":
            name = "Tool failure" if status == "error" else "Tool activity"
            detail = status if status in ("pending", "running", "completed", "error") else "Unknown status"
        elif isinstance(kind, str) and kind in names:
            name, detail = names[kind], ""
        else:
            continue
        events.append((stamp, {"stamp": _when(stamp), "name": name, "detail": detail}))
    return events, len(rows) > MAX_EVENTS, True


def read_opencode_session(source, key, *, now=None):
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{24}", key):
        return None
    records = _read(source, now=now)
    if records is None:
        return None
    db, _ = source
    matched = [(identifier, stamp, role) for identifier, stamp, role in records if _session_key(db, identifier) == key]
    if not matched:
        return None
    identifier = matched[0][0]
    selected = [(stamp, role) for _, stamp, role in reversed(matched)]
    events = [
        (
            stamp,
            {"stamp": _when(stamp), "name": "Assistant response" if role == "assistant" else "Message", "detail": role},
        )
        for stamp, role in selected[-MAX_EVENTS:]
    ]
    parts, parts_partial, parts_available = _parts(db, identifier)
    events.extend(parts)
    events.sort(key=lambda event: event[0])
    return {
        "events": [event for _, event in events[-MAX_EVENTS:]],
        "key": key,
        "id": identifier,
        "messages": len(selected),
        "partial": len(selected) > MAX_EVENTS
        or len(records) >= MAX_MESSAGES
        or parts_partial
        or len(events) > MAX_EVENTS,
        "parts_available": parts_available,
        "source": "OpenCode local SQLite (content hidden); read-only metadata, no HQ correlation implied",
    }
