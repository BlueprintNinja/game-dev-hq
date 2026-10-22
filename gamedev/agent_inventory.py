"""Read-only usage for enrolled OpenCode sessions; never select message text or credentials."""

import json
import sqlite3
import time
from pathlib import Path

from .usage import valid_number


def enrolled(directory, roots):
    path = Path(directory).resolve()
    return any(path.is_relative_to(Path(root).resolve()) for root in roots)


def opencode_usage(database, roots):
    if not database or not roots or not Path(database).is_file():
        return {"available": False, "reason": "No enrolled session source configured"}
    models = {}
    sessions = set()
    rows = 0
    try:
        uri = Path(database).resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1) as connection:
            connection.execute("PRAGMA query_only=ON")
            records = connection.execute(
                "SELECT s.id,s.directory,json_extract(m.data,'$.modelID'),json_extract(m.data,'$.providerID'),json_extract(m.data,'$.tokens'),json_extract(m.data,'$.cost') FROM message m JOIN session s ON s.id=m.session_id WHERE json_extract(m.data,'$.role')='assistant' AND m.time_created>=? ORDER BY m.time_created DESC LIMIT 10000",
                (int((time.time() - 86400) * 1000),),
            )
            for session, directory, model, provider, tokens, cost in records:
                if not enrolled(directory, roots):
                    continue
                rows += 1
                sessions.add(session)
                key = (str(provider or "unreported")[:100], str(model or "unreported")[:180])
                result = models.setdefault(
                    key,
                    {
                        "provider": key[0],
                        "model": key[1],
                        "messages": 0,
                        "reported_cost_usd": None,
                        "reported_tokens": {},
                    },
                )
                result["messages"] += 1
                add_usage(result, tokens, cost)
    except sqlite3.Error, OSError, ValueError:
        return {"available": False, "reason": "Session source could not be read"}
    return {
        "available": True,
        "window_hours": 24,
        "message_limit": 10000,
        "messages": rows,
        "sessions": len(sessions),
        "models": list(models.values()),
        "source": "OpenCode local usage records; separate from HQ run totals",
    }


def add_usage(result, tokens, cost):
    values = json.loads(tokens) if isinstance(tokens, str) else {}
    if isinstance(values, dict):
        for name in ("input", "output", "reasoning", "total"):
            value = values.get(name)
            if valid_number(value):
                result["reported_tokens"][name] = result["reported_tokens"].get(name, 0) + value
    if valid_number(cost):
        result["reported_cost_usd"] = (result["reported_cost_usd"] or 0) + cost
