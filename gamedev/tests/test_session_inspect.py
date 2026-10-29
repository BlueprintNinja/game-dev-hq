"""Read-only, privacy-preserving session inspection boundaries."""

import json
import tempfile
import unittest
from contextlib import suppress
from datetime import date
from pathlib import Path
from unittest.mock import patch

from gamedev.session_inspect import codex_root, list_codex_sessions, read_codex_session


class InspectSessionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / "sessions"
        self.date = date(2026, 10, 29)
        self.day = self.root / "2026" / "10" / "29"
        self.day.mkdir(parents=True)
        self.source = self.day / "rollout-test.jsonl"

    def write_events(self, events):
        self.source.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    def test_list_and_timeline_expose_no_private_contents(self):
        self.write_events(
            [
                {
                    "type": "session_meta",
                    "payload": {
                        "id": "12345678-1234-1234-1234-123456789012",
                        "timestamp": "2026-10-29T12:00:00Z",
                        "cwd": "SECRET_FOLDER",
                        "other": "SUPERSECRET",
                    },
                },
                {
                    "timestamp": "2026-10-29T12:01:00Z",
                    "type": "response_item",
                    "payload": {"type": "function_call", "name": "functions.exec", "arguments": "SUPERSECRET"},
                },
                {
                    "timestamp": "2026-10-29T12:02:00Z",
                    "type": "response_item",
                    "payload": {"type": "function_call_output", "output": "SUPERSECRET"},
                },
                {
                    "timestamp": "2026-10-29T12:03:00Z",
                    "type": "response_item",
                    "payload": {"type": "reasoning", "text": "SUPERSECRET"},
                },
                {
                    "timestamp": "2026-10-29T12:04:00Z",
                    "type": "response_item",
                    "payload": {"type": "message", "content": "SUPERSECRET"},
                },
                {
                    "timestamp": "2026-10-29T12:05:00Z",
                    "type": "event_msg",
                    "payload": {"type": "turn_aborted", "message": "SUPERSECRET"},
                },
            ]
        )
        entries = list_codex_sessions(self.root, today=self.date)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "12345678-1234-1234-1234-123456789012")
        detail = read_codex_session(self.root, entries[0]["key"], today=self.date)
        self.assertEqual(len(detail["events"]), 5)
        self.assertEqual(detail["events"][0]["detail"], "functions.exec")
        self.assertIn("Turn aborted", [event["name"] for event in detail["events"]])
        self.assertNotIn("SUPERSECRET", repr(entries) + repr(detail))
        self.assertNotIn("SECRET_FOLDER", repr(entries) + repr(detail))

    def test_traversal_and_symlinks_are_rejected(self):
        self.write_events([{"type": "session_meta", "payload": {}}])
        self.assertIsNone(read_codex_session(self.root, "../../other", today=self.date))
        link = self.day / "rollout-link.jsonl"
        with suppress(OSError):
            link.symlink_to(self.source)
        self.assertEqual(len(list_codex_sessions(self.root, today=self.date)), 1)
        with tempfile.TemporaryDirectory() as outside:
            path = Path(outside) / "secret.jsonl"
            path.write_text("SECRET")
            external = self.day / "rollout-external.jsonl"
            with suppress(OSError):
                external.symlink_to(path)
            self.assertEqual(len(list_codex_sessions(self.root, today=self.date)), 1)

    def test_bad_json_and_untrusted_tool_names(self):
        self.source.write_text(
            "{bad}\n"
            + json.dumps({"type": "response_item", "payload": {"type": "function_call", "name": "echo /secret"}})
            + "\n",
            encoding="utf-8",
        )
        session = list_codex_sessions(self.root, today=self.date)[0]
        detail = read_codex_session(self.root, session["key"], today=self.date)
        self.assertEqual(detail["events"][0]["detail"], "Other tool")
        self.assertEqual(list_codex_sessions(self.root, today=date(2027, 1, 1)), [])

    def test_enrollment_is_explicit(self):
        with patch.dict("os.environ", {"CODEX_HOME": "/abc", "GAMEDEV_INSPECT_CODEX_ROOT": "/other"}, clear=True):
            self.assertEqual(codex_root(), Path("/other"))
        with patch.dict("os.environ", {"CODEX_HOME": "/abc"}, clear=True):
            self.assertIsNone(codex_root())
            self.assertEqual(list_codex_sessions(codex_root()), [])


if __name__ == "__main__":
    unittest.main()
