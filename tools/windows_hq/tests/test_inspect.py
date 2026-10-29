"""Inspect navigation and privacy in the workstation composition."""

from unittest import mock

from django.test import TestCase, override_settings

from gamedev.hq_plugin.models import Run, Task
from hq_sdk.capabilities import Principal

from . import test_workspace


@test_workspace.workspace_only
@override_settings(ALLOWED_HOSTS=["testserver"], SEVERINO_SITE_HOST="testserver", SECURE_SSL_HOST="testserver")
class InspectViewsTests(TestCase):
    setUp = test_workspace.WorkspaceTests.setUp

    def test_navigation_sources_and_run_trace_are_separate(self):
        task = Task.objects.create(project_slug="test-island", title="Example work")
        run = Run.objects.create(task=task, prompt="PRIVATE PROMPT", output="PRIVATE OUTPUT", status="accepted")
        codex = [{"id": "codex-id", "key": "a" * 24, "started": "time", "updated": "time"}]
        opencode = [{"id": "opencode-id", "key": "b" * 24, "updated": "time", "messages": 1}]
        with (
            mock.patch("gamedev.hq_plugin.inspect_views.codex_root", return_value="enrolled"),
            mock.patch("gamedev.hq_plugin.inspect_views.opencode_source", return_value="enrolled"),
            mock.patch("gamedev.hq_plugin.inspect_views.list_codex_sessions", return_value=codex),
            mock.patch("gamedev.hq_plugin.inspect_views.list_opencode_sessions", return_value=opencode),
        ):
            response = self.client.get("/game-dev/inspect/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertContains(response, f"/game-dev/runs/{run.pk}/trace/")
        self.assertContains(response, f"/game-dev/inspect/codex/{'a' * 24}/")
        self.assertContains(response, f"/game-dev/inspect/opencode/{'b' * 24}/")
        self.assertNotContains(response, "PRIVATE PROMPT")
        self.assertNotContains(response, "PRIVATE OUTPUT")

    def test_capability_and_unknown_session_are_denied(self):
        self.client.get("/game-dev/")
        with mock.patch("hq_sdk.web.web_principal", return_value=Principal("reader", "web", frozenset())):
            self.assertEqual(self.client.get("/game-dev/inspect/").status_code, 403)
            self.assertEqual(self.client.get(f"/game-dev/inspect/codex/{'a' * 24}/").status_code, 403)
        self.assertEqual(self.client.get("/game-dev/inspect/other/invalid/").status_code, 404)
