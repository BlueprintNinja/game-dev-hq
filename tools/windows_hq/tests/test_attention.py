"""Native attention in the real workspace composition, through the public SDK."""

import pickle
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gamedev.hq_plugin.attention import attention_items
from gamedev.hq_plugin.models import InfrastructureSample, ProjectProfile, Run, Task, TraceSpan
from hq_sdk.capabilities import Principal
from hq_sdk.plugin import plugin_attention_items
from hq_sdk.testing import providers_derived_again

from . import test_workspace

workspace_only = test_workspace.workspace_only


@workspace_only
@override_settings(ALLOWED_HOSTS=["testserver"], SEVERINO_SITE_HOST="testserver", SECURE_SSL_HOST="testserver")
class NativeAttentionTests(TestCase):
    def setUp(self):
        test_workspace.WorkspaceTests.setUp(self)
        ProjectProfile.objects.create(project_slug="test-island")
        InfrastructureSample.objects.create(readings={"PRIVATE SENTINEL": "PRIVATE SENTINEL"})

    def run_record(self, *, project="test-island", **values):
        task = Task.objects.create(project_slug=project, title="PRIVATE SENTINEL")
        return Run.objects.create(task=task, prompt="PRIVATE SENTINEL", output="PRIVATE SENTINEL", **values)

    def items(self):
        return {
            entry["item"].key: entry["item"]
            for entry in plugin_attention_items()
            if entry["source_id"] == "gamedev.tasks"
        }

    def test_categories_all_projects_and_empty_diff_are_distinct(self):
        review = self.run_record(status="review", passed=True, diff="PRIVATE SENTINEL")
        approved = self.run_record(status="accepted", passed=True, diff="PRIVATE SENTINEL")
        build = self.run_record(project="PRIVATE-SENTINEL", kind="uefn_build", status="review", passed=True)
        empty = self.run_record(status="review", passed=True, diff=" \n\t")
        failed = self.run_record(status="review", passed=False, diff="PRIVATE SENTINEL")
        completed = self.run_record(status="accepted", passed=True)  # Completed validation run30.
        rejected = self.run_record(status="rejected", passed=True, diff="PRIVATE SENTINEL")
        items = self.items()
        for run, phrase in (
            (review, "await review"),
            (approved, "await apply"),
            (build, "acknowledgement"),
            (empty, "acknowledgement"),
            (failed, "problem review"),
        ):
            item = items[f"run:{run.pk}"]
            self.assertIn(phrase, item.title)
            self.assertEqual(item.magnitude, 1)
            self.assertEqual(item.url, f"/game-dev/runs/{run.pk}/")
            self.assertIsNone(item.since)
            self.assertEqual(item.observed_at, run.updated_at)
            self.assertEqual(item.actions, ())
        for run in (completed, rejected):
            self.assertNotIn(f"run:{run.pk}", items)
        self.assertNotIn("PRIVATE", repr(items))
        self.assertNotIn("gameplay passed", repr(items).lower())

    def test_superseded_work_apply_and_rollback_recovery_clear(self):
        old = self.run_record(status="failed")
        source = Run.objects.create(task=old.task, status="accepted", passed=True, diff="changes")
        failed_apply = Run.objects.create(task=old.task, kind="apply", applies=source, status="failed")
        self.assertIn(f"run:{failed_apply.pk}", self.items())
        applied = Run.objects.create(task=old.task, kind="apply", applies=source, status="accepted")
        self.assertEqual(self.items(), {})
        failed_restore = Run.objects.create(task=old.task, kind="rollback", applies=applied, status="failed")
        self.assertIn(f"run:{failed_restore.pk}", self.items())
        Run.objects.create(task=old.task, kind="rollback", applies=applied, status="accepted")
        self.assertEqual(self.items(), {})
        newer = Run.objects.create(task=old.task, status="failed")
        self.assertEqual(set(self.items()), {f"run:{newer.pk}"})

    def test_old_current_problem_survives_completed_recent_history(self):
        run = self.run_record(status="failed")
        Run.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(days=100))
        for _ in range(12):
            self.run_record(status="accepted", passed=True)
        self.assertIn(f"run:{run.pk}", self.items())

    def test_bounded_full_history_constant_queries_and_no_heavy_payload(self):
        run = self.run_record(status="failed")
        old = timezone.now() - timedelta(days=100)
        Run.objects.filter(pk=run.pk).update(updated_at=old)
        with CaptureQueriesContext(connection) as small:
            attention_items()
        Run.objects.bulk_create([Run(task=run.task, status="accepted", passed=True) for _ in range(300)])
        for _ in range(105):
            self.run_record(status="failed")
        with CaptureQueriesContext(connection) as large:
            items = attention_items()
        self.assertEqual(len(small), len(large))
        self.assertEqual(len(large), 5)
        self.assertEqual(len([item for item in items if item.key.startswith("run:")]), 100)
        self.assertIn("decisions:limit", {item.key for item in items})
        self.assertNotIn(f"run:{run.pk}", {item.key for item in items})
        for query in large:
            selected = query["sql"].split(" FROM ")[0]
            for field in ('"title"', '"prompt"', '"output"', '"readings"', '"metrics"', '"attributes"'):
                self.assertNotIn(field, selected)
        self.assertLess(len(pickle.dumps(items)), 60000)

    def test_worker_sample_missing_grace_current_stale_and_recovery(self):
        now = timezone.now()
        self.assertEqual(attention_items(), ())
        ProjectProfile.objects.all().delete()
        InfrastructureSample.objects.all().delete()
        run = self.run_record(status="accepted")
        self.assertNotIn("worker:missing", {item.key for item in attention_items()})
        Task.objects.filter(pk=run.task_id).update(created_at=now - timedelta(seconds=91))
        items = {item.key: item for item in attention_items()}
        self.assertEqual(items["worker:missing"].magnitude, 1)
        self.assertIn("sample:missing", items)
        ProjectProfile.objects.create(project_slug="test-island")
        sample = InfrastructureSample.objects.create(readings={"title": "PRIVATE SENTINEL"})
        stamp = now - timedelta(seconds=91)
        ProjectProfile.objects.update(worker_seen_at=stamp)
        InfrastructureSample.objects.filter(pk=sample.pk).update(observed_at=stamp)
        items = {item.key: item for item in attention_items()}
        self.assertEqual(set(items), {"worker:stale", "sample:stale"})
        self.assertEqual(items["worker:stale"].since, stamp + timedelta(seconds=90))
        self.assertEqual(items["sample:stale"].observed_at, stamp)
        ProjectProfile.objects.update(worker_seen_at=timezone.now())
        InfrastructureSample.objects.create()
        self.assertEqual(attention_items(), ())

    def span(self, run, *, age=0, attempts=0, **values):
        now = timezone.now()
        return TraceSpan.objects.create(
            run=run,
            trace_id="a" * 32,
            span_id=f"{TraceSpan.objects.count():016x}",
            name="PRIVATE SENTINEL",
            attributes={"PRIVATE SENTINEL": "PRIVATE SENTINEL"},
            started_at=now - timedelta(minutes=20),
            finished_at=now - timedelta(minutes=age),
            export_attempts=attempts,
            **values,
        )

    def test_export_transient_repeated_failure_stall_recovery_and_stable_key(self):
        run = self.run_record(status="accepted")
        self.span(run, age=1, attempts=3)
        self.span(run, age=6, attempts=1)
        self.assertEqual(attention_items(), ())
        span = self.span(run, age=6, attempts=3)
        item = self.items()[f"export:run:{run.pk}"]
        self.assertEqual(item.magnitude, 1)
        self.assertEqual(item.url, f"/game-dev/runs/{run.pk}/trace/")
        self.span(run, age=11)
        changed = self.items()[item.key]
        self.assertEqual(changed.magnitude, 2)
        self.assertEqual(changed.key, item.key)
        TraceSpan.objects.filter(run=run).update(exported_at=timezone.now())
        self.assertEqual(self.items(), {})
        self.assertIsNotNone(span.finished_at)

    def test_export_payload_bound_and_old_problem_not_hidden_by_recent_spans(self):
        for _ in range(22):
            self.span(self.run_record(status="accepted"), age=11)
        run = self.run_record(status="accepted")
        for _ in range(30):
            self.span(run)
        items = attention_items()
        self.assertEqual(len([item for item in items if item.key.startswith("export:run:")]), 20)
        self.assertIn("exports:limit", {item.key for item in items})

    def test_pickles_composition_reuse_and_writes_invalidate(self):
        run = self.run_record(status="failed")
        value = attention_items()
        self.assertIsInstance(value, tuple)
        self.assertEqual(pickle.loads(pickle.dumps(value)), value)
        self.assertEqual(providers_derived_again("gamedev.tasks"), [])
        entries = plugin_attention_items()
        self.assertTrue(
            any(entry["source_id"] == "gamedev.tasks" and entry["item"].key == f"run:{run.pk}" for entry in entries)
        )
        Run.objects.filter(pk=run.pk).update(status="accepted", passed=True)
        self.assertNotIn(f"run:{run.pk}", self.items())
        ProjectProfile.objects.update(worker_seen_at=timezone.now() - timedelta(minutes=4))
        self.assertIn("worker:stale", self.items())
        self.assertEqual(providers_derived_again("gamedev.tasks"), [])

    def test_live_home_sanitized_no_writes_network_or_process_and_denied_details(self):
        self.client.get("/")  # Establish the host authentication session first.
        run = self.run_record(status="failed", metrics={"PRIVATE SENTINEL": "PRIVATE SENTINEL"})
        self.span(run, age=11)
        # The host persists derived answers in hq_derived, not domain state.
        # Warm its read cache after fixture writes before asserting a write-free GET.
        self.client.get("/")
        self.client.get("/action-items/")
        with (
            mock.patch("socket.socket.connect", side_effect=AssertionError("network")),
            mock.patch("subprocess.Popen", side_effect=AssertionError("process")),
            mock.patch("gamedev.hq_plugin.views.execute_capability", side_effect=AssertionError("mutation")),
            CaptureQueriesContext(connection) as queries,
        ):
            response = self.client.get("/")
            queue = self.client.get("/action-items/")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.context["action_queue_count"], 2)
        self.assertContains(response, "/action-items/")
        self.assertContains(queue, "Run needs problem review")
        self.assertContains(queue, f"/game-dev/runs/{run.pk}/")
        self.assertNotContains(queue, "PRIVATE SENTINEL")
        self.assertNotContains(response, "PRIVATE SENTINEL")
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")), query)
        with mock.patch("hq_sdk.web.web_principal", return_value=Principal("ungranted", "web", frozenset({"read"}))):
            self.assertGreaterEqual(self.client.get("/").context["action_queue_count"], 2)
            self.assertContains(self.client.get("/action-items/"), "Run needs problem review")
            for url in (
                f"/game-dev/runs/{run.pk}/",
                "/game-dev/mission/",
                "/game-dev/harness/",
                f"/game-dev/runs/{run.pk}/trace/",
            ):
                self.assertEqual(self.client.get(url).status_code, 403, url)
