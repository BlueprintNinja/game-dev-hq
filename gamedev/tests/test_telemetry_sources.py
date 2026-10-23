"""Privacy and failure boundaries of the real telemetry adapters."""

import json
import sqlite3
import subprocess
import tempfile
import time
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse

from gamedev.agent_inventory import add_usage, enrolled, opencode_usage
from gamedev.infrastructure import gpu_readings, listening, process_readings, sample_infrastructure
from gamedev.otlp import NoRedirect, attribute, encode_spans, export_spans
from gamedev.platform_metrics import backup_reading, scheduled_tasks
from gamedev.tool_tracing import ToolObserver, tool_event
from gamedev.usage import agent_usage, compact_checks


class AgentInventoryTests(unittest.TestCase):
    def test_enrollment_is_directory_scoped_and_reads_usage_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "sessions.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript(
                    "CREATE TABLE session(id TEXT,directory TEXT); CREATE TABLE message(session_id TEXT,time_created INTEGER,data TEXT);"
                )
                connection.executemany(
                    "INSERT INTO session VALUES (?,?)",
                    [("yes", str(root / "enrolled")), ("no", str(root / "enrolled-other"))],
                )
                payload = json.dumps(
                    {
                        "role": "assistant",
                        "modelID": "model",
                        "providerID": "provider",
                        "tokens": {"input": 8, "output": 2},
                        "cost": 0,
                        "text": "PRIVATE MESSAGE",
                    }
                )
                connection.executemany(
                    "INSERT INTO message VALUES (?,?,?)",
                    [
                        ("yes", int(time.time() * 1000), payload),
                        ("no", int(time.time() * 1000), payload),
                        ("yes", 0, payload),
                    ],
                )
                connection.commit()
            result = opencode_usage(database, [root / "enrolled"])
            self.assertEqual(result["messages"], 1)
            self.assertEqual(result["sessions"], 1)
            self.assertEqual(result["models"][0]["reported_cost_usd"], 0)
            self.assertEqual(result["models"][0]["reported_tokens"], {"input": 8, "output": 2})
            self.assertNotIn("PRIVATE", json.dumps(result))
            self.assertFalse(opencode_usage(database, [root / "absent"])["models"])
            database.write_text("not sqlite")
            self.assertFalse(opencode_usage(database, [root])["available"])
        self.assertFalse(opencode_usage(None, [])["available"])
        self.assertFalse(enrolled("/one-other", ["/one"]))

    def test_invalid_and_unreported_usage_stays_unknown(self):
        result = {"reported_tokens": {}, "reported_cost_usd": None}
        add_usage(result, None, -1)
        add_usage(result, '{"input":true,"output":-2,"reasoning":3}', None)
        self.assertEqual(result, {"reported_tokens": {"reasoning": 3}, "reported_cost_usd": None})
        self.assertEqual(
            compact_checks([{"kind": "tests", "exit_code": 0, "output": "SECRET"}]), [{"kind": "tests", "passed": True}]
        )
        output = "\n".join(
            json.dumps(event)
            for event in [
                {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2, "cached_input_tokens": 3}},
                {"type": "item.completed", "item": {"type": "command_execution"}},
                {"type": "tool_use", "part": {}},
                {
                    "type": "step_finish",
                    "part": {"tokens": {"input": 2, "output": 1, "total": 7, "cache": {"write": 4}}, "cost": 0.1},
                },
            ]
        )
        usage = agent_usage(output)
        self.assertEqual(usage["hq.tools.count"], 2)
        self.assertEqual(usage["llm.token_count.total"], 14)
        self.assertEqual(usage["llm.cost.total"], 0.1)
        self.assertEqual(agent_usage('[]\ninvalid\n{"type":"step_finish","part":[]}'), {})


class InfrastructureTests(unittest.TestCase):
    def test_gpu_readings_and_missing_sensor_values(self):
        with (
            mock.patch("gamedev.infrastructure.shutil.which", return_value="nvidia-smi"),
            mock.patch(
                "gamedev.infrastructure.subprocess.run",
                return_value=SimpleNamespace(stdout="0, GPU, 12, 200, 1000, 40, N/A\ninvalid"),
            ),
        ):
            readings = gpu_readings()
        self.assertEqual(readings[0]["utilization_percent"], 12)
        self.assertIsNone(readings[0]["power_watts"])
        with mock.patch("gamedev.infrastructure.shutil.which", return_value=None):
            self.assertEqual(gpu_readings(), [])
        with (
            mock.patch("gamedev.infrastructure.shutil.which", return_value="nvidia-smi"),
            mock.patch("gamedev.infrastructure.subprocess.run", side_effect=subprocess.TimeoutExpired("fixed", 3)),
        ):
            self.assertEqual(gpu_readings(), [])

    def test_infrastructure_snapshot_and_backup_age(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = root / "workspace"
            data.mkdir()
            (data / "hq.sqlite3").write_bytes(b"db")
            self.assertEqual(backup_reading(data)["count"], 0)
            (root / "backups").mkdir()
            (root / "backups/hq-test.sqlite3").write_bytes(b"backup")
            self.assertEqual(backup_reading(data)["latest_bytes"], 6)
            with (
                mock.patch("gamedev.infrastructure.gpu_readings", return_value=[]),
                mock.patch("gamedev.infrastructure.scheduled_tasks", return_value=[]),
                mock.patch("gamedev.infrastructure.process_readings", return_value={}),
                mock.patch("gamedev.infrastructure.listening", return_value=False),
            ):
                snapshot = sample_infrastructure(data)
            self.assertEqual(snapshot["database_bytes"], 2)
            self.assertIsNone(snapshot["gpu_utilization_percent"])
            self.assertEqual(snapshot["services"]["phoenix"], {"port": 6006, "listening": False})
            self.assertNotIn(str(root), json.dumps(snapshot))

    def test_fixed_service_processes_exclude_command_lines(self):
        connection = SimpleNamespace(status="LISTEN", pid=123, laddr=SimpleNamespace(port=6006))
        process = mock.Mock(pid=123)
        process.cpu_times.return_value = SimpleNamespace(user=1, system=2)
        process.memory_info.return_value = SimpleNamespace(rss=100)
        process.num_threads.return_value = 4
        with (
            mock.patch("gamedev.infrastructure.psutil.net_connections", return_value=[connection]),
            mock.patch("gamedev.infrastructure.psutil.Process", return_value=process),
        ):
            self.assertEqual(process_readings()["6006"], {"pid": 123, "rss_bytes": 100, "threads": 4, "cpu_seconds": 3})
        process.cmdline.assert_not_called()
        with mock.patch("gamedev.infrastructure.socket.create_connection", side_effect=OSError):
            self.assertFalse(listening(6006))
        with mock.patch("gamedev.infrastructure.socket.create_connection"):
            self.assertTrue(listening(6006))

    def test_scheduled_task_query_is_read_only_and_handles_failure(self):
        with (
            mock.patch("gamedev.platform_metrics.os", SimpleNamespace(name="nt", environ={})),
            mock.patch(
                "gamedev.platform_metrics.subprocess.run",
                return_value=SimpleNamespace(stdout='[{"name":"GameDevHQ-Worker","state":"Running"}]'),
            ) as run,
        ):
            self.assertEqual(scheduled_tasks()[0]["state"], "Running")
            command = run.call_args.args[0][-1]
            self.assertTrue(command.startswith("$names"))
            self.assertNotIn("Set-ScheduledTask", command)
        with mock.patch("gamedev.platform_metrics.subprocess.run", side_effect=OSError):
            self.assertEqual(scheduled_tasks(), [])


class ToolEventTests(unittest.TestCase):
    def test_event_parsing_timing_and_incremental_boundaries(self):
        self.assertIsNone(tool_event({"type": "item.completed", "item": {"type": "agent_message"}}))
        self.assertIsNone(tool_event({"type": "tool_use", "part": None}))
        self.assertEqual(tool_event({"type": "tool_use", "part": {"id": "a"}})[:3], ("a", "opencode_tool", True))
        observer = ToolObserver(SimpleNamespace(span_id="123", run_id=1))
        with mock.patch.object(observer, "event") as event:
            observer.feed("one\npar")
            observer.feed("one\npartial", final=True)
            self.assertEqual(event.call_args_list, [mock.call("one"), mock.call("partial")])
        now = datetime.now(UTC)
        start, end, precise = observer.times({"state": {"time": {"start": 1000, "end": 2000}}}, "x", now)
        self.assertTrue(precise)
        self.assertEqual((end - start).total_seconds(), 1)
        self.assertFalse(observer.times({"state": None}, "x", now)[2])
        observer.event("not json")
        observer.event("[]")
        observer.event('{"type":"tool_use","part":{}}')


class ExportTests(unittest.TestCase):
    def test_otlp_values_status_and_rejection(self):
        for value, field in [(True, "bool_value"), (1, "int_value"), (1.5, "double_value"), ("x", "string_value")]:
            self.assertEqual(attribute("key", value).value.WhichOneof("value"), field)
        span = SimpleNamespace(
            attributes={},
            kind="TOOL",
            run_id=1,
            trace_id="01" * 16,
            span_id="02" * 8,
            parent_id="03" * 8,
            name="tool",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            failed=True,
        )
        self.assertTrue(encode_spans([span]))
        response = mock.MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = b""
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch("gamedev.otlp.urllib.request.build_opener", return_value=opener):
            self.assertEqual(export_spans([span], "http://127.0.0.1:6006/v1/traces", "secret"), 0)
            response.status = 401
            with self.assertRaises(ValueError):
                export_spans([span], "http://127.0.0.1:6006/v1/traces", "secret")
            response.status = 200
            response.read.return_value = b"\xff"
            with self.assertRaisesRegex(ValueError, "invalid export acknowledgment"):
                export_spans([span], "http://127.0.0.1:6006/v1/traces", "secret")
            response.status = 200
            partial = ExportTraceServiceResponse()
            partial.partial_success.rejected_spans = 1
            response.read.return_value = partial.SerializeToString()
            with self.assertRaises(ValueError):
                export_spans([span], "http://127.0.0.1:6006/v1/traces", "secret")
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://external")
