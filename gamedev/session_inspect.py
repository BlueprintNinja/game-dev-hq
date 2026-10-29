"""Read-only, bounded Codex session metadata for Game Dev HQ Inspect.

Never retain or render prompts, arguments, tool outputs, reasoning, working
paths, or raw transcript text. Sessions do not prove game verification.
"""

import json
import os
import re
from collections import Counter, deque
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

MAX_SESSION_FILES = 80
MAX_TAIL_BYTES = 3 * 1024 * 1024
MAX_LINE_BYTES = 256 * 1024
MAX_EVENTS = 160
MAX_META_BYTES = 96 * 1024
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,79}\Z")
SESSION_ID = re.compile(r"[a-fA-F0-9-]{32,40}\Z")


def codex_root():
    """An operator must explicitly enroll a local session directory."""
    override = os.environ.get("GAMEDEV_INSPECT_CODEX_ROOT", "").strip()
    return Path(override).expanduser() if override else None


def session_key(path):
    return sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:24]


def _day_files(root, date):
    year = root / f"{date:%Y}"
    month = year / f"{date:%m}"
    day = month / f"{date:%d}"
    try:
        if year.is_symlink() or month.is_symlink() or day.is_symlink() or not day.is_dir():
            return []
        files = []
        for path in day.iterdir():
            if len(files) >= MAX_SESSION_FILES * 3:
                break
            if path.name.startswith("rollout-") and path.suffix == ".jsonl" and not path.is_symlink() and path.is_file():
                actual = path.resolve(strict=True)
                if actual.is_relative_to(root):
                    files.append((path.stat().st_mtime, actual))
        return files
    except OSError:
        return []


def recent_session_files(root, *, today=None):
    """Bound filesystem discovery to 14 day directories without following links."""
    if root is None:
        return []
    root = Path(root)
    try:
        if root.is_symlink() or not root.is_dir():
            return []
        safe_root = root.resolve(strict=True)
    except OSError:
        return []
    today = today or datetime.now(UTC).date()
    records = []
    for days_ago in range(14):
        records.extend(_day_files(safe_root, today - timedelta(days=days_ago)))
    records.sort(key=lambda x: x[0], reverse=True)
    return [path for _, path in records[:MAX_SESSION_FILES]]

def _safe(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        return "Other tool"
    if value.startswith(("functions.", "mcp__", "container.", "python.", "browser.", "web.", "tools.")):
        return value
    if value in ("exec_command", "shell_command", "apply_patch", "update_plan"):
        return value
    return "Other tool"


def _stamp(value):
    """Only accept ISO timestamps, never arbitrary log strings."""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)", value):
        return value[:32]
    return "Unknown"


def _parse_metadata_line(line):
    try:
        event = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(event, dict) or event.get("type") != "session_meta":
        return None
    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    identifier = payload.get("id", "")
    return {
        "id": identifier if isinstance(identifier, str) and SESSION_ID.fullmatch(identifier) else "Unreported",
        "started": _stamp(payload.get("timestamp") or event.get("timestamp")),
    }


def _metadata(path):
    try:
        with path.open("rb") as source:
            consumed = 0
            while consumed < MAX_META_BYTES:
                line = source.readline(min(MAX_LINE_BYTES + 1, MAX_META_BYTES - consumed))
                if not line:
                    break
                consumed += len(line)
                if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                    break
                metadata = _parse_metadata_line(line)
                if metadata is not None:
                    return metadata
    except OSError:
        pass
    return {"id": "Unreported", "started": "Unknown"}

def list_codex_sessions(root, *, today=None):
    records = []
    for path in recent_session_files(root, today=today):
        try:
            stat = path.stat()
        except OSError:
            continue
        meta = _metadata(path)
        records.append({
            "key": session_key(path),
            "id": meta["id"],
            "started": meta["started"],
            "updated": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(timespec="seconds"),
            "bytes": stat.st_size,
        })
    return records


def _label(entry):
    """Project only known structural categories, never free-form log text."""
    if not isinstance(entry, dict):
        return None
    event_type = entry.get("type")
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    if event_type == "response_item":
        if kind in ("function_call", "custom_tool_call", "mcp_tool_call"):
            return "Tool call", _safe(payload.get("name", ""))
        if kind in ("function_call_output", "custom_tool_call_output", "mcp_tool_call_output"):
            return "Tool result", "Returned (content hidden)"
        if kind == "message":
            return "Message", "Content hidden"
        if kind == "reasoning":
            return "Reasoning", "Content hidden"
    if event_type == "event_msg":
        if kind == "task_started":
            return "Turn started", ""
        if kind == "task_complete":
            return "Turn complete", ""
        if kind == "turn_aborted":
            return "Turn aborted", ""
        if kind == "token_count":
            return "Usage update", "Numbers omitted in this view"
    return None


def _parse_event(line):
    if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
        return None
    try:
        return json.loads(line)
    except (ValueError, UnicodeError):
        return None


def _read_events(path):
    try:
        with path.open("rb") as source:
            size = path.stat().st_size
            offset = max(0, size - MAX_TAIL_BYTES)
            source.seek(offset)
            if offset:
                source.readline(MAX_LINE_BYTES + 1)
            events = deque(maxlen=MAX_EVENTS)
            counts = Counter()
            scanned = 0
            while scanned < 15000:
                line = source.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                scanned += 1
                event = _parse_event(line)
                label = _label(event)
                if label is None:
                    continue
                name, detail = label
                counts[name] += 1
                events.append({"stamp": _stamp(event.get("timestamp")), "name": name, "detail": detail})
    except (OSError, ValueError):
        return None
    return {
        "events": list(events),
        "counts": sorted(counts.items()),
        "partial": offset > 0 or scanned >= 15000 or sum(counts.values()) > MAX_EVENTS,
    }


def read_codex_session(root, key, *, today=None):
    """Resolve opaque keys against the enrolled bounded inventory, never a URL path."""
    if not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{24}", key):
        return None
    path = next((p for p in recent_session_files(root, today=today) if session_key(p) == key), None)
    if path is None:
        return None
    result = _read_events(path)
    if result is None:
        return None
    meta = _metadata(path)
    return {
        "key": key, "id": meta["id"], "started": meta["started"],
        "updated": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds"),
        "events": result["events"], "counts": result["counts"], "partial": result["partial"],
        "source": "Local Codex JSONL; read-only metadata, no external correlation implied",
    }
