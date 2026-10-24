import json
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from gamedev.collector import collect_once
from gamedev.hq_plugin.metrics import MetricsQuery, snapshot
from gamedev.hq_plugin.models import InfrastructureSample, Run, TraceSpan
from gamedev.hq_plugin.worker_state import claim, update
from gamedev.otlp import encode_spans, local_endpoint
from gamedev.tool_tracing import ToolObserver
from gamedev.tracing import observed_process, stage
from gamedev.usage import agent_usage
from hq_sdk.capabilities import Principal, execute_capability

from .test_workspace import WorkspaceTests, workspace_only


@workspace_only
class TelemetryTests(WorkspaceTests):
    def queued(self):
        task = self.create()["id"]
        result = execute_capability(
            "gamedev.run.queue",
            {"task_id": task, "version": 1, "prompt": "Do not export this private prompt"},
            principal=self.principal,
        )
        self.assertTrue(result["ok"], result)
        return Run.objects.get(pk=result["id"])

    def test_nested_spans_preserve_parentage_usage_and_exclude_content(self):
        run = self.queued()
        with stage(run.pk, "hq.run"), stage(run.pk, "agent", "LLM") as metrics:
            metrics.update(
                agent_usage(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}}))
            )
        spans = list(TraceSpan.objects.filter(run=run))
        self.assertEqual(spans[1].parent_id, spans[0].span_id)
        self.assertEqual(spans[1].trace_id, spans[0].trace_id)
        payload = encode_spans(spans)
        self.assertNotIn(b"private prompt", payload)
        decoded = ExportTraceServiceRequest.FromString(payload)
        self.assertEqual(len(decoded.resource_spans[0].scope_spans[0].spans), 2)

    def test_export_outage_keeps_spans_and_still_collects_metrics(self):
        run = self.queued()
        with stage(run.pk, "hq.run"):
            pass
        with (
            mock.patch("gamedev.collector.sample_infrastructure", return_value={}),
            mock.patch("gamedev.collector.export_spans", side_effect=OSError("secret-key-must-not-be-logged")),
        ):
            result = collect_once("http://127.0.0.1:6006/v1/traces", "secret", self.principal)
        span = TraceSpan.objects.get(run=run)
        self.assertIsNone(span.exported_at)
        self.assertEqual(span.export_attempts, 1)
        self.assertEqual(result["phoenix_export"]["error"], "OSError")
        self.assertNotIn("secret", json.dumps(InfrastructureSample.objects.first().readings))
        with (
            mock.patch("gamedev.collector.sample_infrastructure", return_value={}),
            mock.patch("gamedev.collector.export_spans", return_value=0),
        ):
            collect_once("http://127.0.0.1:6006/v1/traces", "secret", self.principal)
        span.refresh_from_db()
        self.assertIsNotNone(span.exported_at)

    def test_metrics_read_scope_filter_and_html_json_pages(self):
        run = self.queued()
        owned = claim(self.principal)
        update(
            run.pk,
            owned.worker_token,
            self.principal,
            status="review",
            passed=True,
            output=json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}}),
        )
        reader = Principal("metrics-reader", "web", frozenset({"gamedev.metrics.read"}))
        result = snapshot(MetricsQuery(project="test-island"), principal=reader)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["summary"]["reported_usage"]["llm.token_count.total"], 14)
        self.assertEqual(snapshot(MetricsQuery(project="other"), principal=reader)["total"], 0)
        denied = execute_capability("gamedev.metrics.snapshot", {}, principal=Principal("denied", "web", frozenset()))
        self.assertFalse(denied["ok"])
        self.assertContains(self.client.get("/game-dev/harness/"), "Unavailable")
        response = self.client.get("/game-dev/harness/?format=json")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("output", response.json())
        self.assertEqual(self.client.get("/game-dev/harness/?hours=999999").status_code, 400)

    def test_tool_events_are_idempotent_and_never_export_arguments(self):
        run = self.queued()
        with stage(run.pk, "agent", "LLM"):
            parent = TraceSpan.objects.get(run=run)
            observer = ToolObserver(parent)
            observer.feed(
                json.dumps(
                    {"type": "item.started", "item": {"id": "one", "type": "command_execution", "command": "SECRET"}}
                )
                + "\n"
            )
            final = json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "one", "type": "command_execution", "exit_code": 1, "aggregated_output": "PRIVATE"},
                }
            )
            observer.event(final)
            observer.event(final)
        tool = TraceSpan.objects.get(run=run, kind="TOOL")
        self.assertEqual(tool.parent_id, parent.span_id)
        self.assertTrue(tool.failed)
        self.assertFalse(tool.attributes["hq.timing.precise"])
        exported = encode_spans(list(run.spans.all()))
        self.assertNotIn(b"SECRET", exported)
        self.assertNotIn(b"PRIVATE", exported)

    def test_failed_stage_records_only_error_type(self):
        run = self.queued()
        with self.assertRaises(ValueError), stage(run.pk, "hq.run"):
            raise ValueError("sensitive detail")
        span = TraceSpan.objects.get(run=run)
        self.assertTrue(span.failed)
        self.assertEqual(span.attributes, {"error.type": "ValueError"})


class TelemetryParsingTests(SimpleTestCase):
    def test_usage_distinguishes_unknown_zero_cache_and_invalid_values(self):
        self.assertEqual(agent_usage("not-json\n[]\n{}"), {})
        output = json.dumps(
            {"type": "step_finish", "part": {"tokens": {"input": 12, "output": 3, "cache": {"read": 4}}, "cost": 0}}
        )
        metrics = agent_usage(output)
        self.assertEqual(metrics["llm.cost.total"], 0)
        self.assertEqual(metrics["llm.token_count.total"], 19)
        self.assertEqual(metrics["hq.tokens.cached"], 4)
        self.assertNotIn(
            "llm.token_count.prompt", agent_usage('{"type":"turn.completed","usage":{"input_tokens":NaN}}')
        )
        self.assertEqual(agent_usage('{"type":"turn.completed","usage":null}'), {})

    def test_export_rejects_external_targets_redirects_and_credentials(self):
        for address in (
            "https://example.com/v1/traces",
            "http://127.0.0.1.evil.test/",
            "http://user:secret@127.0.0.1/",
            "http://127.0.0.1/?secret=1",
        ):
            with self.assertRaises(ValueError):
                local_endpoint(address)
        self.assertEqual(local_endpoint("http://127.0.0.1:6006/v1/traces"), "http://127.0.0.1:6006/v1/traces")

    def test_uninstrumented_process_helpers_remain_usable_without_a_database(self):
        process = mock.Mock(return_value=(0, "no usage"))
        self.assertEqual(observed_process(None, "agent", process, ["fixed-command"]), (0, "no usage"))


@workspace_only
class DispatchBoundaryTests(SimpleTestCase):
    def test_editor_apply_and_rollback_never_enter_coding(self):
        policy = SimpleNamespace(projects={"island": SimpleNamespace(coding_enabled=True)})
        for kind, handler in [
            ("apply", "execute_apply"),
            ("rollback", "execute_rollback"),
            ("uefn_build", "execute_editor"),
        ]:
            with (
                self.subTest(kind=kind),
                mock.patch("gamedev.worker." + handler) as selected,
                mock.patch("gamedev.worker.execute_coding") as coding,
            ):
                from gamedev.worker import execute

                execute(SimpleNamespace(kind=kind, task=SimpleNamespace(project_slug="island")), policy, None)
                selected.assert_called_once()
                coding.assert_not_called()
