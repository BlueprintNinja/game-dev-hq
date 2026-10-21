import http.client
import tempfile
import threading
import unittest
from pathlib import Path

from gamedev import preview, store


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "test.sqlite3"
        store.initialize(self.database)
        self.project = store.add_project(self.database, "Island <test>", "local workspace", "UEFN", "operator")

    def task(self, **kwargs):
        return store.add_task(self.database, self.project, "<script>alert(1)</script>", "operator", **kwargs)

    def finish(self, task):
        for status in ("active", "review", "done"):
            store.transition(self.database, task, status, "operator", "tests passed")

    def test_codex_default_and_explicit_delegation(self):
        parent = self.task()
        child = self.task(agent="opencode", parent_id=parent)
        tasks = store.snapshot(self.database)["tasks"]
        self.assertEqual(tasks[0]["agent"], "codex")
        self.assertEqual(tasks[1]["parent_id"], parent)
        self.finish(child)
        self.finish(parent)
        self.assertEqual(store.snapshot(self.database)["tasks"][0]["status"], "done")

    def test_invalid_delegation_rolls_back(self):
        parent = self.task()
        child = self.task(agent="opencode", parent_id=parent)
        other = store.add_project(self.database, "Other", "local", "Godot", "operator")
        before = store.snapshot(self.database)
        for kwargs in ({"agent": "opencode"}, {"agent": "other"}, {"parent_id": child}, {"parent_id": 999}):
            with self.assertRaises(ValueError):
                self.task(**kwargs)
        with self.assertRaises(ValueError):
            store.add_task(self.database, other, "cross project", "operator", "opencode", parent)
        self.assertEqual(before, store.snapshot(self.database))

    def test_completion_requires_review_evidence_and_children(self):
        parent = self.task()
        child = self.task(agent="opencode", parent_id=parent)
        with self.assertRaises(ValueError):
            store.transition(self.database, parent, "done", "operator", "test")
        for status in ("active", "review"):
            store.transition(self.database, parent, status, "operator")
        with self.assertRaises(ValueError):
            store.transition(self.database, parent, "done", "operator", "test")
        self.finish(child)
        with self.assertRaises(ValueError):
            store.transition(self.database, parent, "done", "operator")
        store.transition(self.database, parent, "done", "operator", "review and tests passed")
        with self.assertRaises(ValueError):
            self.task(agent="opencode", parent_id=parent)

    def test_audit_is_atomic_and_persistent(self):
        with self.assertRaises(ValueError):
            store.add_project(self.database, "Rollback", "local", "UEFN", " ")
        task = self.task()
        store.transition(self.database, task, "blocked", "operator", "Needs asset")
        store.transition(self.database, task, "queued", "operator")
        store.initialize(self.database)
        data = store.snapshot(self.database)
        self.assertEqual(len(data["projects"]), 1)
        self.assertEqual(len(data["events"]), 4)
        self.assertEqual(data["events"][-1]["action"], "task.transitioned")

    def test_render_escapes_user_data(self):
        self.task()
        html = preview.render(store.snapshot(self.database))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Island &lt;test&gt;", html)

    def test_ingress_denial_hosts_read_only_and_routes(self):
        self.task()
        server = preview.server(self.database, ["operator@example.test"], "workstation.example.ts.net", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.assertEqual(server.server_address[0], "127.0.0.1")
        with self.assertRaises(OSError):
            preview.server(self.database, ["operator@example.test"], "workstation.example.ts.net", server.server_port)

        def request(path="/", identity=None, host=None, method="GET"):
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            headers = {}
            if identity:
                headers["Tailscale-User-Login"] = identity
            if host:
                headers["Host"] = host
            client.request(method, path, headers=headers)
            response = client.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            client.close()
            return result

        self.assertEqual(request()[0], 403)
        self.assertEqual(request(identity="other@example.test")[0], 403)
        self.assertEqual(request("/healthz")[0], 200)
        self.assertEqual(request(host="evil.test")[0], 400)
        self.assertEqual(request(method="POST", identity="operator@example.test")[0], 501)
        for path in ("/", "/api/snapshot", "/style.css"):
            status, headers, body = request(path, "operator@example.test", "workstation.example.ts.net:9443")
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertTrue(body)
        self.assertEqual(request("/../../LICENSE", "operator@example.test")[0], 404)

    def test_reject_invalid_configuration(self):
        for name in ("0.0.0.0", "*.example.ts.net", "example.com", "name.ts.net/evil"):
            with self.assertRaises(ValueError):
                preview.handler(self.database, ["operator@example.test"], name)
        with self.assertRaises(ValueError):
            preview.handler(self.database, [], "workstation.example.ts.net")


if __name__ == "__main__":
    unittest.main()
