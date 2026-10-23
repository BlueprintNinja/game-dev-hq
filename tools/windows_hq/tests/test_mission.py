"""Mission Control behavior in the real workspace composition."""

from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gamedev.hq_plugin.mission import project_mission
from gamedev.hq_plugin.models import InfrastructureSample, ProjectProfile, Run, Task, TraceSpan
from gamedev.supervisor import ProcessFailure
from gamedev.worker import process_next
from hq_sdk.capabilities import AuthorizationError, Principal

from . import test_workspace

workspace_only = test_workspace.workspace_only


@workspace_only
@override_settings(ALLOWED_HOSTS=["testserver"], SEVERINO_SITE_HOST="testserver", SECURE_SSL_HOST="testserver")
class MissionTests(TestCase):
    setUp = test_workspace.WorkspaceTests.setUp

    def run_record(self, *, project="test-island", title="Build loop", **values):
        task = Task.objects.create(project_slug=project, title=title)
        return Run.objects.create(task=task, prompt="PRIVATE PROMPT", output="PRIVATE OUTPUT", **values)

    def projection(self, grants=("gamedev.read",)):
        return project_mission("test-island", principal=Principal("reader", "web", frozenset(grants)))

    def test_selector_empty_unknown_and_project_filter(self):
        self.assertContains(self.client.get("/game-dev/mission/"), "Choose a project to see")
        self.assertEqual(self.client.get("/game-dev/mission/?project=missing").status_code, 404)
        self.run_record(project="other", title="Other project secret", status="failed")
        run = self.run_record(title="Our work", status="running", engine="codex")
        response = self.client.get("/game-dev/mission/?project=test-island")
        self.assertContains(response, "Our work")
        self.assertNotContains(response, "Other project secret")
        self.assertContains(response, f"/game-dev/runs/{run.pk}/")
        self.assertNotContains(response, "PRIVATE")

    def test_old_active_and_pending_survive_window_and_engine_delegation_are_real(self):
        run = self.run_record(status="running", engine="opencode")
        source = self.run_record(status="accepted")
        Run.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=20), delegated_by=source)
        Task.objects.create(project_slug="test-island", title="Waiting task")
        data = self.projection()
        self.assertEqual(len(data["active"]), 1)
        self.assertIn("Engine: opencode", data["active"][0].detail)
        self.assertIn(f"Delegated by run #{source.pk}", data["active"][0].detail)
        self.assertEqual(len(data["pending"]), 1)

    def test_coding_editor_and_empty_diff_are_separate(self):
        coding = self.run_record(status="review", passed=True, diff="diff --git a/a b/a")
        editor = self.run_record(kind="uefn_build", status="review", passed=True)
        empty = self.run_record(status="review", passed=True, diff=" \n\t")
        data = self.projection()
        self.assertEqual([row.url for row in data["changes"]], [f"/game-dev/runs/{coding.pk}/"])
        self.assertEqual(
            {row.url for row in data["results"]}, {f"/game-dev/runs/{editor.pk}/", f"/game-dev/runs/{empty.pk}/"}
        )
        self.assertEqual(data["failures"], [])

    def test_latest_attempt_and_recovered_apply_are_authoritative(self):
        source = self.run_record(status="accepted", passed=True, diff="changes")
        Run.objects.create(task=source.task, kind="apply", applies=source, status="failed")
        Run.objects.create(task=source.task, kind="apply", applies=source, status="accepted", passed=False)
        old = self.run_record(status="failed")
        Run.objects.create(task=old.task, status="rejected", prompt="later")
        latest = self.run_record(status="failed")
        Run.objects.filter(pk=latest.pk).update(updated_at=timezone.now() - timedelta(days=20))
        data = self.projection()
        self.assertEqual([row.url for row in data["failures"]], [f"/game-dev/runs/{latest.pk}/"])
        self.assertEqual(data["changes"], [])
        self.assertEqual(data["suggestions"][0].url, f"/game-dev/runs/{latest.pk}/")

    def test_stale_missing_freshness_and_permission_boundaries(self):
        self.run_record(status="accepted")
        self.assertEqual(self.projection()["health"], [])
        self.assertEqual(self.projection()["traces"], [])
        metrics = ("gamedev.read", "gamedev.metrics.read")
        self.assertEqual([k.value for k in self.projection(metrics)["health"][:2]], ["Missing", "Missing"])
        ProjectProfile.objects.create(project_slug="test-island")
        sample = InfrastructureSample.objects.create(readings={"private": "MUST NOT EXPORT"})
        old = timezone.now() - timedelta(minutes=5)
        ProjectProfile.objects.update(worker_seen_at=old)
        InfrastructureSample.objects.filter(pk=sample.pk).update(observed_at=old)
        data = self.projection(metrics)
        self.assertEqual([k.value for k in data["health"][:2]], ["Out of date", "Out of date"])
        self.assertEqual(data["traces"], [])
        self.assertTrue(self.projection(("gamedev.read", "gamedev.traces.read"))["traces"])
        with mock.patch(
            "gamedev.hq_plugin.mission_views.web_principal",
            return_value=Principal("reader", "web", frozenset({"read", "gamedev.read"})),
        ):
            response = self.client.get("/game-dev/mission/?project=test-island")
        self.assertNotContains(response, "Reading freshness")
        self.assertNotContains(response, "/trace/")
        self.assertNotContains(response, "MUST NOT EXPORT")

    def test_fragment_matches_full_escapes_html_and_get_has_no_effect(self):
        run = self.run_record(title='<script>alert("x")</script>', status="review", passed=True, diff="changes")
        url = "/game-dev/mission/?project=test-island"
        self.client.get("/game-dev/mission/")  # Establish the host authentication session first.
        with (
            mock.patch("gamedev.hq_plugin.views.execute_capability", side_effect=AssertionError("mutation")),
            mock.patch("subprocess.Popen", side_effect=AssertionError("process")),
            mock.patch("socket.socket.connect", side_effect=AssertionError("network")),
            CaptureQueriesContext(connection) as queries,
        ):
            full = self.client.get(url)
            fragment = self.client.get(url, HTTP_X_FRAGMENT="mission")
            polled = self.client.get(url + "&fragment=1")
        self.assertEqual(full.status_code, 200)
        self.assertEqual(fragment.status_code, 200)
        self.assertEqual(polled.status_code, 200)
        self.assertEqual(polled.content, fragment.content)
        self.assertIn(fragment.content.strip(), full.content)
        self.assertContains(fragment, "&lt;script&gt;")
        self.assertNotContains(fragment, '<script>alert("x")</script>')
        self.assertContains(fragment, f"/game-dev/runs/{run.pk}/")
        for query in queries:
            self.assertFalse(query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")), query)
        self.assertEqual(self.client.post(url).status_code, 405)

    def test_query_budget_is_constant_and_heavy_fields_not_selected(self):
        self.run_record(status="review", passed=True, diff="changes")
        with CaptureQueriesContext(connection) as small:
            self.projection()
        for index in range(30):
            self.run_record(title=f"Task {index}", status="failed")
        with CaptureQueriesContext(connection) as large:
            self.projection()
        self.assertEqual(len(small), len(large))
        self.assertLessEqual(len(large), 4)
        for query in large:
            selected = query["sql"].split(" FROM ")[0]
            for field in ('"prompt"', '"output"', '"diff"'):
                # Diff is used in a SQL boolean expression, never returned as raw text.
                if field != '"diff"':
                    self.assertNotIn(field, selected)

    def test_denied_read_and_distinct_compact_evidence(self):
        with self.assertRaises(AuthorizationError):
            self.projection(())
        with mock.patch("hq_sdk.web.web_principal", return_value=Principal("denied", "web", frozenset())):
            self.assertEqual(self.client.get("/game-dev/mission/").status_code, 403)
        self.run_record(
            status="review",
            passed=True,
            diff="changes",
            metrics={
                "checks": [
                    {"kind": "source", "passed": True},
                    {"kind": "compilation", "passed": True},
                    {"kind": "harness", "passed": True},
                    {"kind": "gameplay", "passed": False},
                ]
            },
        )
        row = self.projection()["changes"][0]
        for text in ("Source: passed", "Compilation: passed", "Work process: passed", "Gameplay: did not pass"):
            self.assertIn(text, row.detail)

    def test_missing_host_or_gamedev_read_denies_before_resource_access(self):
        for grants in (("gamedev.read",), ("read",)):
            with (
                self.subTest(grants=grants),
                mock.patch("hq_sdk.web.web_principal", return_value=Principal("limited", "web", frozenset(grants))),
                mock.patch(
                    "gamedev.hq_plugin.mission_views.list_resource",
                    side_effect=AssertionError("unauthorized resource access"),
                ),
            ):
                for url in ("/game-dev/mission/", "/game-dev/mission/?project=test-island"):
                    self.assertEqual(self.client.get(url).status_code, 403)
                    self.assertEqual(self.client.get(url, HTTP_X_FRAGMENT="mission").status_code, 403)

    def test_truncation_is_visible_and_old_failure_retained(self):
        for index in range(102):
            self.run_record(title=f"Failure {index}", status="failed")
        data = self.projection()
        self.assertTrue(data["truncated"])
        self.assertTrue(data["recent_truncated"])
        self.assertEqual(len(data["failures"]), 100)
        self.assertEqual(len(data["recent"]), 20)


@workspace_only
@override_settings(ALLOWED_HOSTS=["testserver"], SEVERINO_SITE_HOST="testserver", SECURE_SSL_HOST="testserver")
class WorkerRootTraceTests(TestCase):
    setUp = test_workspace.WorkspaceTests.setUp

    def test_root_outcomes_distinguish_file_operations_from_verification(self):
        for kind, status, passed, failed in (
            ("apply", "accepted", False, False),
            ("rollback", "accepted", False, False),
            ("apply", "failed", False, True),
            ("coding", "review", False, True),
        ):
            with self.subTest(kind=kind, status=status):
                task = Task.objects.create(project_slug="test-island", title="Trace case")
                run = Run.objects.create(task=task, kind=kind, status="running")

                def execute(*args, run=run, status=status, passed=passed):
                    Run.objects.filter(pk=run.pk).update(status=status, passed=passed)

                with (
                    mock.patch("gamedev.worker.claim", return_value=run),
                    mock.patch("gamedev.worker.execute", side_effect=execute),
                ):
                    self.assertTrue(process_next(None, self.principal))
                span = TraceSpan.objects.get(run=run, name="hq.run")
                self.assertEqual(span.failed, failed)
                if kind in ("apply", "rollback"):
                    self.assertNotIn("hq.verification.passed", span.attributes)
                    self.assertEqual(span.attributes["hq.operation.succeeded"], status == "accepted")
                else:
                    self.assertIs(span.attributes["hq.verification.passed"], False)
                    self.assertNotIn("hq.operation.succeeded", span.attributes)

    def test_operation_exception_is_a_failed_root_without_verification(self):
        task = Task.objects.create(project_slug="test-island", title="Failed apply")
        run = Run.objects.create(task=task, kind="apply", status="running")
        with (
            mock.patch("gamedev.worker.claim", return_value=run),
            mock.patch("gamedev.worker.execute", side_effect=ProcessFailure("failure")),
        ):
            self.assertTrue(process_next(None, self.principal))
        run.refresh_from_db()
        span = TraceSpan.objects.get(run=run, name="hq.run")
        self.assertEqual(run.status, "failed")
        self.assertTrue(span.failed)
        self.assertIs(span.attributes["hq.operation.succeeded"], False)
        self.assertNotIn("hq.verification.passed", span.attributes)
