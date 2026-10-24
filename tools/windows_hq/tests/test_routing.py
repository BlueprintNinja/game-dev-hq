"""Routing is selected locally, durable before launch, bounded and explicitly reviewed."""

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase

from gamedev.delegation import CodexResult, DelegatedWork, dispatch
from gamedev.hq_plugin.execution import QueueRun, RetryRun
from gamedev.hq_plugin.models import ProjectProfile, Run, Task, TraceSpan
from gamedev.hq_plugin.worker_state import claim, update
from gamedev.otlp import encode_spans
from gamedev.routing import RoutePolicy
from gamedev.worker import codex_command, execute_coding, process_next
from gamedev.worker_config import WorkerPolicy
from hq_sdk.capabilities import Principal, execute_capability

from .test_workspace import WorkspaceTests, workspace_only


class RoutingPolicyTests(SimpleTestCase):
    def test_defaults_and_strict_policy(self):
        routes = RoutePolicy()
        self.assertEqual(routes.resolve("routine").model, "gpt-6-luna")
        for profile in ("complex", "orchestration"):
            self.assertEqual(routes.resolve(profile).model, "gpt-6.1-sol")
            self.assertEqual(routes.resolve(profile).effort, "high")
        for value in (
            {"routine": {"model": "web/model"}},
            {"routine": {"model": "gpt-6.1-sol"}},
            {"complex": {"model": "gpt-6-luna"}},
            {"routine": {"model": "gpt-6-luna", "effort": "unbounded"}},
            {"routine": {"model": "gpt-6-luna", "arguments": ["unsafe"]}},
            {"other": {}},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RoutePolicy.model_validate(value)
        with self.assertRaises(ValueError):
            routes.resolve("")
        self.assertEqual(DelegatedWork(title="old", prompt="work").agent, "opencode")
        for extra in ({"profile": "complex"}, {"agent": "shell"}, {"executable": "bad"}):
            with self.assertRaises(ValueError):
                DelegatedWork(title="child", prompt="work", **extra)

    def test_exact_cli_model_flags_preserve_sandbox(self):
        policy = WorkerPolicy(codex=Path(sys.executable), artifacts=Path(tempfile.gettempdir()), projects={})
        for profile, model in (("routine", "gpt-6-luna"), ("complex", "gpt-6.1-sol")):
            argv = codex_command(policy, Path(tempfile.gettempdir()), policy.routes.resolve(profile))
            self.assertEqual(argv[argv.index("--model") + 1], model)
            self.assertIn('model_reasoning_effort="high"', argv)
            for value in (
                "--ignore-user-config",
                "workspace-write",
                'windows.sandbox="mxc"',
                'approval_policy="never"',
                "sandbox_workspace_write.network_access=false",
            ):
                self.assertIn(value, argv)
        self.assertEqual(QueueRun(task_id=1, version=1, prompt="work").profile, "routine")
        with self.assertRaises(ValueError):
            QueueRun(task_id=1, version=1, prompt="work", model="arbitrary")


@workspace_only
class RoutingTests(WorkspaceTests):
    def queued(self, profile="routine", kind="coding"):
        task = self.create()["id"]
        result = execute_capability(
            "gamedev.run.queue",
            {
                "task_id": task,
                "version": 1,
                "prompt": "private work instructions",
                "profile": profile,
                "kind": kind,
            },
            principal=self.principal,
        )
        self.assertTrue(result["ok"], result)
        return Run.objects.get(pk=result["id"])

    def retry(self, run, **extra):
        return execute_capability(
            "gamedev.run.retry",
            {
                "run_id": run.pk,
                "version": run.version,
                "note": "Reviewed failure; needs complex reasoning",
                **extra,
            },
            principal=self.principal,
        )

    def failed(self, profile="routine", **values):
        run = self.queued(profile)
        owned = claim(self.principal)
        update(run.pk, owned.worker_token, self.principal, status="failed", **values)
        run.refresh_from_db()
        return run

    def test_queue_profiles_default_web_selection_and_unknown_fields(self):
        for profile in ("routine", "complex", "orchestration"):
            self.assertEqual(self.queued(profile).requested_profile, profile)
        task = Task.objects.get(pk=self.create()["id"])
        url = "/game-dev/queue/"
        data = {"task_id": task.pk, "version": task.version, "prompt": "work"}
        self.client.post(url, {**data, "model": "bad"})
        self.assertFalse(task.runs.exists())
        self.client.post(url, {**data, "profile": "unknown"})
        self.assertFalse(task.runs.exists())
        self.client.post(url, data)
        self.assertEqual(task.runs.get().requested_profile, "routine")
        other = Task.objects.get(pk=self.create()["id"])
        self.client.post(url, {**data, "task_id": other.pk, "profile": "complex"})
        self.assertEqual(other.runs.get().requested_profile, "complex")
        ProjectProfile.objects.create(project_slug="test-island", coding_enabled=True)
        response = self.client.get("/game-dev/")
        # The selector uses the same label/select primitive as the existing queue action.
        self.assertContains(response, 'name="profile"')
        self.assertContains(response, 'value="routine"')
        self.assertContains(response, 'value="orchestration"')

    def test_retry_preserves_profile_and_escalation_is_one_explicit_linked_attempt(self):
        run = self.failed("complex")
        ordinary = self.retry(run)
        self.assertTrue(ordinary["ok"], ordinary)
        self.assertEqual(Run.objects.get(pk=ordinary["id"]).requested_profile, "complex")
        ordinary_owned = claim(self.principal)
        update(ordinary_owned.pk, ordinary_owned.worker_token, self.principal, status="failed")
        routine = self.failed()
        result = self.retry(routine, escalate=True)
        self.assertTrue(result["ok"], result)
        child = Run.objects.get(pk=result["id"])
        self.assertEqual(
            (child.requested_profile, child.escalation_root_id, child.retried_from_id),
            ("complex", routine.pk, routine.pk),
        )
        self.assertEqual(child.status, "queued")
        self.assertEqual(child.selected_model, "")
        self.assertFalse(self.retry(routine, escalate=True)["ok"])
        owned = claim(self.principal)
        update(owned.pk, owned.worker_token, self.principal, status="failed")
        # The earlier ordinary retry may be first in queue; settle the escalation explicitly.
        if owned.pk != child.pk:
            owned = claim(self.principal)
            update(owned.pk, owned.worker_token, self.principal, status="failed")
        child.refresh_from_db()
        self.assertFalse(self.retry(child)["ok"])
        self.assertFalse(self.retry(routine, escalate=True)["ok"])

    def test_escalation_denies_ineligible_states_permissions_notes_and_stale_versions(self):
        run = self.failed()
        for changes in ({"version": run.version - 1}, {"note": ""}):
            self.assertFalse(self.retry(run, escalate=True, **changes)["ok"])
        denied = execute_capability(
            "gamedev.run.retry",
            {
                "run_id": run.pk,
                "version": run.version,
                "note": "reviewed",
                "escalate": True,
            },
            principal=Principal("reader", "web", frozenset({"gamedev.read"})),
        )
        self.assertFalse(denied["ok"])
        for field, value in (
            ("kind", "uefn_build"),
            ("kind", "apply"),
            ("kind", "rollback"),
            ("status", "canceled"),
            ("requested_profile", "complex"),
            ("output", "quota exhausted"),
            ("output", "authentication failed"),
            ("output", "sandbox refused"),
            ("cancel_requested", True),
        ):
            original = getattr(run, field)
            setattr(run, field, value)
            run.save()
            self.assertFalse(self.retry(run, escalate=True)["ok"], (field, value))
            setattr(run, field, original)
            run.save()
        self.assertEqual(run.task.runs.count(), 1)

    def test_orchestration_dispatches_two_routine_children_and_denies_recursion_and_apply(self):
        parent = self.queued("orchestration")
        owned = claim(self.principal)
        update(owned.pk, owned.worker_token, self.principal, selected_model="gpt-6.1-sol")
        result = CodexResult(
            summary="delegated",
            delegations=[
                DelegatedWork(title="Luna child", prompt="bounded task", agent="codex"),
                DelegatedWork(title="Legacy child", prompt="bounded task"),
            ],
        )
        dispatch(owned, result, self.principal)
        children = list(parent.delegated_runs.order_by("id"))
        self.assertEqual([c.task.agent for c in children], ["codex", "opencode"])
        self.assertTrue(all(c.requested_profile == "routine" for c in children))
        with self.assertRaises(ValueError):
            dispatch(owned, result, self.principal)
        update(owned.pk, owned.worker_token, self.principal, status="review", passed=True)
        parent.refresh_from_db()
        denied = execute_capability(
            "gamedev.run.decide",
            {
                "run_id": parent.pk,
                "version": parent.version,
                "decision": "accept",
                "note": "reviewed",
            },
            principal=self.principal,
        )
        self.assertFalse(denied["ok"])
        child = claim(self.principal)
        with self.assertRaises(ValueError):
            dispatch(child, result, self.principal)
        update(child.pk, child.worker_token, self.principal, status="failed")
        child.refresh_from_db()
        self.assertFalse(self.retry(child, escalate=True)["ok"])
        child.status = "accepted"
        child.passed = True
        child.save()
        applied = execute_capability(
            "gamedev.run.apply",
            {
                "run_id": child.pk,
                "version": child.version,
            },
            principal=self.principal,
        )
        self.assertFalse(applied["ok"])

    def test_routine_and_complex_cannot_delegate_and_schema_is_bounded(self):
        result = CodexResult(summary="bad", delegations=[DelegatedWork(title="child", prompt="work")])
        for profile in ("routine", "complex"):
            run = self.queued(profile)
            owned = claim(self.principal)
            with self.assertRaises(ValueError):
                dispatch(owned, result, self.principal)
            self.assertFalse(run.delegated_runs.exists())
            update(owned.pk, owned.worker_token, self.principal, status="failed")
        with self.assertRaises(ValueError):
            CodexResult(summary="bad", delegations=result.delegations * 3)

    def test_worker_records_route_before_invocation_and_trace_export_excludes_content(self):
        run = self.queued("complex")
        output = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": '{"summary":"done","delegations":[]}',
                },
            }
        )
        with tempfile.TemporaryDirectory() as root:
            policy = WorkerPolicy(
                codex=Path(sys.executable),
                artifacts=Path(root),
                projects={
                    "test-island": {"repository": root, "checks": [[sys.executable, "--version"]]},
                },
            )

            def process(argv, **kwargs):
                current = Run.objects.get(pk=run.pk)
                self.assertEqual((current.selected_model, current.selected_effort), ("gpt-6.1-sol", "high"))
                self.assertEqual(current.routing_reason, "local-policy:complex")
                self.assertEqual(argv[argv.index("--model") + 1], "gpt-6.1-sol")
                self.assertIn("delegations empty", kwargs["input_text"])
                return 0, output

            with (
                mock.patch("gamedev.worker.git", return_value="revision"),
                mock.patch("gamedev.worker.run_process", side_effect=process),
                mock.patch("gamedev.worker.collect_evidence", return_value=([], "")),
            ):
                self.assertTrue(process_next(policy, self.principal))
        run.refresh_from_db()
        self.assertEqual(run.status, "review")
        self.assertNotIn("llm.cost.total", run.metrics)
        spans = list(TraceSpan.objects.filter(run=run))
        spans[0].attributes["prompt"] = "PRIVATE SENTINEL"
        exported = encode_spans(spans)
        self.assertNotIn(b"PRIVATE SENTINEL", exported)
        self.assertNotIn(b"private work instructions", exported)
        self.assertIn(b"gpt-6.1-sol", exported)

    def test_historical_unknowns_and_non_coding_never_resolve_or_launch_models(self):
        run = self.queued()
        run.requested_profile = ""
        run.save()
        response = self.client.get(f"/game-dev/runs/{run.pk}/")
        self.assertContains(response, "Unknown / not selected")
        self.assertContains(response, "Unknown (historical or non-coding)")
        owned = claim(self.principal)
        policy = WorkerPolicy(codex=Path(sys.executable), artifacts=Path(tempfile.gettempdir()), projects={})
        with mock.patch("gamedev.worker.run_agent") as agent, self.assertRaises(ValueError):
            execute_coding(owned, policy, mock.Mock(coding_enabled=True), self.principal)
        agent.assert_not_called()
        update(owned.pk, owned.worker_token, self.principal, status="failed")
        editor = self.queued(kind="uefn_inspect")
        self.assertEqual(editor.requested_profile, "")
        with mock.patch("gamedev.worker.run_agent") as agent:
            with mock.patch("gamedev.worker.execute_editor") as execute_editor:
                from gamedev.worker import execute

                execute(editor, mock.Mock(projects={"test-island": mock.Mock()}), self.principal)
                execute_editor.assert_called_once()
            agent.assert_not_called()

    def test_route_migration_keeps_historical_values_unknown(self):
        executor = MigrationExecutor(connection)
        old = executor.loader.project_state([("gamedev_hq", "0009_projectprofile_source_apply_scope")])
        new = executor.loader.project_state([("gamedev_hq", "0010_run_routing")])
        self.assertNotIn("requested_profile", old.models[("gamedev_hq", "run")].fields)
        model = new.apps.get_model("gamedev_hq", "Run")
        self.assertEqual(model().requested_profile, "")
        self.assertEqual(model().selected_model, "")
        self.assertIsNone(model().escalation_root_id)

    def test_rejected_routine_escalates_but_open_states_and_duplicate_task_work_do_not(self):
        run = self.failed()
        for status in ("queued", "running", "review", "accepted"):
            run.status = status
            run.save()
            self.assertFalse(self.retry(run, escalate=True)["ok"])
        run.status = "rejected"
        run.save()
        with (
            mock.patch("gamedev.hq_plugin.execution.record_operation", side_effect=RuntimeError("audit down")),
            self.assertRaises(RuntimeError),
        ):
            self.retry(run, escalate=True)
        self.assertEqual(run.task.runs.count(), 1)
        result = self.retry(run, escalate=True)
        self.assertTrue(result["ok"], result)
        self.assertFalse(self.retry(run)["ok"])

    def test_run_read_and_fragment_permissions_and_queue_write_denial(self):
        run = self.queued()
        for grants in (frozenset(), frozenset({"read"})):
            with mock.patch("hq_sdk.web.web_principal", return_value=Principal("denied", "web", grants)):
                for suffix in ("", "?fragment=1"):
                    response = self.client.get(f"/game-dev/runs/{run.pk}/{suffix}")
                    self.assertEqual(response.status_code, 403)
                    self.assertNotContains(response, "private work instructions", status_code=403)
        reader = Principal("reader", "web", frozenset({"read", "gamedev.read"}))
        with mock.patch("hq_sdk.web.web_principal", return_value=reader):
            self.assertEqual(self.client.post("/game-dev/queue/", {}).status_code, 403)
            self.assertEqual(self.client.post(f"/game-dev/runs/{run.pk}/retry/", {}).status_code, 403)
        self.assertEqual(Run.objects.count(), 1)

    def test_get_cannot_queue_retry_or_escalate(self):
        run = self.failed()
        count = Run.objects.count()
        for url in ("/game-dev/queue/", f"/game-dev/runs/{run.pk}/retry/?escalate=true"):
            self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(Run.objects.count(), count)
        self.assertFalse(RetryRun(run_id=run.pk, version=run.version).escalate)
