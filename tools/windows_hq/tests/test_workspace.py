import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from gamedev.hq_plugin.models import Task
from hq_sdk.audit import AuditLog
from hq_sdk.capabilities import Principal, execute_capability, web_principal

from ..asgi import TailnetBoundary
from ..manage import collect_static

# The upstream suite discovers this module and imports it with the host's own
# settings, where the Game Dev extension is not installed and the workspace's
# database is not there. These tests belong to the workspace composition; they
# stand down anywhere else instead of failing a suite they cannot serve.
workspace_only = unittest.skipUnless(
    "gamedev.hq_plugin" in settings.INSTALLED_APPS,
    "the Game Dev extension is not installed in this composition",
)


@workspace_only
@override_settings(ALLOWED_HOSTS=["testserver"], SEVERINO_SITE_HOST="testserver", SECURE_SSL_HOST="testserver")
class WorkspaceTests(TestCase):
    def setUp(self):
        self.operator = os.environ["GAMEDEV_OPERATOR_LOGIN"]
        self.user = get_user_model().objects.create_user(username=self.operator, password=None)
        self.principal = web_principal(self.user)
        result = execute_capability("project.create", {"name": "Test island", "slug": "test-island"},
                                    principal=self.principal)
        self.assertTrue(result["ok"], result)
        self.client = Client(HTTP_HOST=settings.SEVERINO_SITE_HOST, HTTP_X_FORWARDED_PROTO="https",
                             HTTP_TAILSCALE_USER_LOGIN=self.operator, REMOTE_ADDR="127.0.0.1")

    def create(self, **kwargs):
        return execute_capability("gamedev.task.create", {"project_slug": "test-island", "title": "Build a loop", **kwargs},
                                  principal=self.principal)

    def move(self, task, status, evidence=""):
        return execute_capability("gamedev.task.move", {"task_id": task.pk, "version": task.version,
                                  "status": status, "evidence": evidence}, principal=self.principal)

    def finish(self, task):
        for status in ("active", "review", "done"):
            result = self.move(task, status, "Tested and reviewed")
            self.assertTrue(result["ok"], result)
            task.refresh_from_db()

    def test_real_task_lifecycle_delegation_and_actor_audit(self):
        parent_id = self.create()["id"]
        child_id = self.create(agent="opencode", parent_id=parent_id)["id"]
        self.finish(Task.objects.get(pk=child_id))
        self.finish(Task.objects.get(pk=parent_id))
        event = AuditLog.objects.filter(object_type="gamedev.task.move").latest("pk")
        self.assertEqual(event.actor_label, self.operator)

    def test_denials_invalid_delegation_completion_and_stale_write(self):
        self.assertFalse(self.create(agent="opencode")["ok"])
        self.assertFalse(self.create(project_slug="missing")["ok"])
        parent = Task.objects.get(pk=self.create()["id"])
        self.create(agent="opencode", parent_id=parent.pk)
        self.assertFalse(self.move(parent, "done", "evidence")["ok"])
        self.assertTrue(self.move(parent, "active")["ok"])
        self.assertFalse(self.move(parent, "review")["ok"])
        parent.refresh_from_db()
        self.assertTrue(self.move(parent, "review")["ok"])
        parent.refresh_from_db()
        self.assertFalse(self.move(parent, "done")["ok"])
        self.assertFalse(self.move(parent, "done", "evidence")["ok"])
        denied = execute_capability("gamedev.task.create", {"project_slug": "test-island", "title": "Denied"},
                                    principal=Principal("no-rights", "web", frozenset()))
        self.assertFalse(denied["ok"])

    def test_required_audit_failure_rolls_back_the_task(self):
        before = Task.objects.count()
        with (mock.patch("gamedev.hq_plugin.services.record_operation", side_effect=RuntimeError("audit unavailable")),
              self.assertRaises(RuntimeError)):
            self.create()
        self.assertEqual(Task.objects.count(), before)

    def test_ui_saves_tasks_and_rejects_an_unallowed_identity(self):
        response = self.client.post("/game-dev/", {"project_slug": "test-island", "title": "Saved from web",
                                                  "agent": "codex", "parent_id": ""})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Task.objects.filter(title="Saved from web").exists())
        self.assertContains(self.client.get("/game-dev/"), "Saved from web")
        self.assertEqual(self.client.get("/", HTTP_TAILSCALE_USER_LOGIN="stranger@example.test").status_code, 403)
        self.user.is_active = False
        self.user.save(update_fields=("is_active",))
        self.assertEqual(self.client.get("/").status_code, 403)

    def test_write_requires_csrf_even_with_operator_identity(self):
        client = Client(enforce_csrf_checks=True, HTTP_HOST=settings.SEVERINO_SITE_HOST,
                        HTTP_X_FORWARDED_PROTO="https", HTTP_TAILSCALE_USER_LOGIN=self.operator,
                        REMOTE_ADDR="127.0.0.1")
        response = client.post("/game-dev/", {"project_slug": "test-island", "title": "Unsafe", "agent": "codex"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Task.objects.filter(title="Unsafe").exists())

    def test_asgi_gate_covers_static_and_rejects_duplicate_identity_or_nonloopback(self):
        async def run(headers, peer="127.0.0.1"):
            events = []

            async def app(scope, receive, send):
                await send({"type": "http.response.start", "status": 200})

            async def send(event):
                events.append(event)

            await TailnetBoundary(app, self.operator)({"type": "http", "path": "/static/app.css",
                                "headers": headers, "client": (peer, 1234)}, None, send)
            return events[0]["status"]

        identity = (b"tailscale-user-login", self.operator.encode())
        self.assertEqual(asyncio.run(run([])), 403)
        self.assertEqual(asyncio.run(run([identity, identity])), 403)
        self.assertEqual(asyncio.run(run([identity], "100.64.0.1")), 403)
        self.assertEqual(asyncio.run(run([identity])), 200)


@workspace_only
class StaticCollectionTests(TestCase):
    """The workspace collects its assets on every start; collecting twice must not empty the tree."""

    def test_collecting_twice_leaves_a_complete_tree(self):
        with tempfile.TemporaryDirectory(prefix="gamedev-static-") as root, override_settings(STATIC_ROOT=root):
            for attempt in (1, 2):
                with self.subTest(attempt=attempt):
                    collect_static()
                    collected = {path.relative_to(root).as_posix() for path in Path(root).rglob("*") if path.is_file()}
                    self.assertTrue(any(name.startswith("css/app.") for name in collected), collected)
                    self.assertTrue(any(name.startswith("fonts/inter/LICENSE") for name in collected), collected)
                    self.assertTrue(any(name.endswith(".txt") for name in collected), collected)
