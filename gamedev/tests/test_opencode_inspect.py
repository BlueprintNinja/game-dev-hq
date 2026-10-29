"""Opt-in OpenCode session metadata and workspace isolation checks."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from gamedev.opencode_inspect import list_opencode_sessions, opencode_source, read_opencode_session


class OpenCodeInspectTests(unittest.TestCase):
    def test_opt_in_directory_scoping_and_private_data(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "workspace"
            root.mkdir()
            foreign = base / "workspace-other"
            foreign.mkdir()
            db = base / "sessions.db"
            with closing(sqlite3.connect(db)) as cx:
                cx.executescript(
                    "CREATE TABLE session(id TEXT,directory TEXT); "
                    "CREATE TABLE message(session_id TEXT,time_created INTEGER,data TEXT); "
                    "CREATE TABLE part(session_id TEXT,time_created INTEGER,data TEXT);"
                )
                cx.executemany(
                    "INSERT INTO session VALUES (?,?)",
                    [("ses_111", str(root)), ("ses_222", str(foreign))],
                )
                cx.executemany(
                    "INSERT INTO part VALUES (?,?,?)",
                    [
                        ("ses_111", 10500, json.dumps({"type": "step-start", "text": "SUPERSECRET"})),
                        ("ses_111", 10550, json.dumps({"type": ["invalid"], "text": "SUPERSECRET"})),
                        (
                            "ses_111",
                            10600,
                            json.dumps({"type": "tool", "state": {"status": "error", "output": "SUPERSECRET"}}),
                        ),
                        ("ses_222", 10700, json.dumps({"type": "tool", "state": {"status": "error"}})),
                    ],
                )
                cx.executemany(
                    "INSERT INTO message VALUES (?,?,?)",
                    [
                        ("ses_111", 10000, json.dumps({"role": "assistant", "text": "SUPERSECRET"})),
                        ("ses_111", 11000, json.dumps({"role": "user", "content": "SUPERSECRET"})),
                        ("ses_222", 12000, json.dumps({"role": "assistant", "text": "SUPERSECRET"})),
                    ],
                )
                cx.commit()
            self.assertIsNone(list_opencode_sessions(None, now=12))
            sessions = list_opencode_sessions((db, root), now=12)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["messages"], 2)
            timeline = read_opencode_session((db, root), sessions[0]["key"], now=12)
            self.assertEqual(timeline["id"], "ses_111")
            self.assertEqual(
                [event["name"] for event in timeline["events"]],
                ["Assistant response", "Execution step started", "Tool failure", "Message"],
            )
            self.assertTrue(timeline["parts_available"])
            self.assertNotIn("SUPERSECRET", repr(sessions) + repr(timeline))
            self.assertNotIn(str(root), repr(sessions) + repr(timeline))
            self.assertIsNone(read_opencode_session((db, root), "../../etc/passwd", now=12))
            with patch.dict(
                "os.environ",
                {"GAMEDEV_INSPECT_OPENCODE_DB": str(db), "GAMEDEV_INSPECT_OPENCODE_ROOT": str(root)},
            ):
                self.assertEqual(opencode_source(), (db, root))
            with patch.dict("os.environ", {}, clear=True):
                self.assertIsNone(opencode_source())

    def test_missing_or_invalid_database_is_not_fabricated_as_empty_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            db = Path(temp) / "not-sqlite.db"
            db.write_text("not a database", encoding="utf-8")
            self.assertIsNone(list_opencode_sessions((db, root), now=12))
            self.assertIsNone(read_opencode_session((db, root), "0" * 24, now=12))


if __name__ == "__main__":
    unittest.main()
