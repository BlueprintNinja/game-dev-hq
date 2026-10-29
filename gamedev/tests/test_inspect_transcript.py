import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from gamedev.inspect_transcript import _codex_event, _group, codex_transcript, opencode_transcript
from gamedev.opencode_inspect import list_opencode_sessions
from gamedev.session_inspect import session_key


class TranscriptProjectionTests(unittest.TestCase):
    def test_message_preserves_text_and_redacts_credentials(self):
        event = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Help me; api_key=private-value"}],
            },
        }
        result = _codex_event(event)
        self.assertEqual(result["kind"], "user")
        self.assertIn("Help me", result["content"])
        self.assertNotIn("private-value", result["content"])

    def test_encrypted_reasoning_and_system_messages_are_not_displayed(self):
        for payload in (
            {"type": "reasoning", "encrypted_content": "private"},
            {"type": "message", "role": "system", "content": [{"text": "private"}]},
        ):
            self.assertIsNone(_codex_event({"type": "response_item", "payload": payload}))

    def test_tool_arguments_are_projected_as_text(self):
        event = {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": "pwd"}}
        result = _codex_event(event)
        self.assertEqual((result["kind"], result["label"], result["content"]), ("tool", "shell", "pwd"))

    def test_groups_follow_recorded_user_messages(self):
        events = [{"kind": "user"}, {"kind": "assistant"}, {"kind": "tool"}, {"kind": "user"}]
        self.assertEqual([len(group) for group in _group(events)], [3, 1])

    def test_invalid_codex_record_marks_transcript_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / datetime.now(UTC).strftime("%Y/%m/%d")
            folder.mkdir(parents=True)
            path = folder / "rollout-example.jsonl"
            path.write_text("{bad json}\n", encoding="utf-8")
            result = codex_transcript(root, session_key(path))
            self.assertTrue(result["partial"])
            self.assertEqual(result["groups"], [])

    def test_oversized_codex_record_marks_transcript_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / datetime.now(UTC).strftime("%Y/%m/%d")
            folder.mkdir(parents=True)
            path = folder / "rollout-example.jsonl"
            path.write_text("x" * 300000 + "\n", encoding="utf-8")
            result = codex_transcript(root, session_key(path))
            self.assertTrue(result["partial"])
            self.assertEqual(result["groups"], [])

    def test_codex_earlier_page_does_not_repeat_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / datetime.now(UTC).strftime("%Y/%m/%d")
            folder.mkdir(parents=True)
            path = folder / "rollout-example.jsonl"
            lines = [
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "message", "role": "user", "content": [{"text": str(i)}]},
                    }
                )
                for i in range(200)
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            latest = codex_transcript(root, session_key(path))
            earlier = codex_transcript(root, session_key(path), before=latest["older"])
            self.assertEqual(latest["groups"][0][0]["content"], "40")
            self.assertEqual(earlier["groups"][-1][0]["content"], "39")

    def test_opencode_detail_is_scoped_to_enrolled_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "allowed"
            root.mkdir()
            db = Path(tmp) / "opencode.db"
            stamp = int(time.time() * 1000)
            with closing(sqlite3.connect(db)) as cx:
                cx.executescript(
                    "CREATE TABLE session(id TEXT,directory TEXT);"
                    "CREATE TABLE message(id TEXT,session_id TEXT,time_created INTEGER,data TEXT);"
                    "CREATE TABLE part(id TEXT,session_id TEXT,message_id TEXT,time_created INTEGER,data TEXT);"
                )
                cx.executemany("INSERT INTO session VALUES (?,?)", [("one", str(root)), ("other", tmp)])
                for sid in ("one", "other"):
                    cx.execute("INSERT INTO message VALUES (?,?,?,?)", (sid, sid, stamp, json.dumps({"role": "user"})))
                    cx.execute(
                        "INSERT INTO part VALUES (?,?,?,?,?)",
                        (sid, sid, sid, stamp, json.dumps({"type": "text", "text": "hello"})),
                    )
                cx.execute(
                    "INSERT INTO part VALUES (?,?,?,?,?)",
                    ("failure", "one", "one", stamp + 1, json.dumps({"type": "tool", "state": {"status": "error"}})),
                )
                cx.commit()
            source = (db, root)
            sessions = list_opencode_sessions(source)
            self.assertEqual(len(sessions), 1)
            detail = opencode_transcript(source, sessions[0]["key"])
            self.assertEqual(detail["groups"][0][0]["content"], "hello")
            self.assertEqual(detail["groups"][0][-1]["label"], "Failed")
            self.assertIsNone(opencode_transcript(source, "../other"))

    def test_opencode_earlier_page_preserves_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "allowed"
            root.mkdir()
            db = Path(tmp) / "opencode.db"
            stamp = int(time.time() * 1000)
            with closing(sqlite3.connect(db)) as cx:
                cx.executescript(
                    "CREATE TABLE session(id TEXT,directory TEXT);"
                    "CREATE TABLE message(id TEXT,session_id TEXT,time_created INTEGER,data TEXT);"
                    "CREATE TABLE part(id TEXT,session_id TEXT,message_id TEXT,time_created INTEGER,data TEXT);"
                )
                cx.execute("INSERT INTO session VALUES (?,?)", ("one", str(root)))
                cx.execute("INSERT INTO message VALUES (?,?,?,?)", ("msg", "one", stamp, json.dumps({"role": "user"})))
                cx.executemany(
                    "INSERT INTO part VALUES (?,?,?,?,?)",
                    [
                        (f"{i:03}", "one", "msg", stamp + i, json.dumps({"type": "text", "text": str(i)}))
                        for i in range(100)
                    ],
                )
                cx.commit()
            source = (db, root)
            key = list_opencode_sessions(source)[0]["key"]
            latest = opencode_transcript(source, key)
            with closing(sqlite3.connect(db)) as cx:
                cx.execute(
                    "INSERT INTO part VALUES (?,?,?,?,?)",
                    ("new", "one", "msg", stamp + 101, json.dumps({"type": "text", "text": "new"})),
                )
                cx.commit()
            earlier = opencode_transcript(source, key, before=latest["older"])
            self.assertEqual(latest["groups"][0][0]["content"], "20")
            self.assertEqual(earlier["groups"][-1][0]["content"], "19")
            self.assertIsNone(earlier["older"])
