"""Pure adapter contracts run in the host suite as well as the composition."""

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from gamedev.delegation import opencode_command, opencode_environment, read_result
from gamedev.supervisor import ProcessFailure, WorkerLock, run_process
from gamedev.uefn import NoRedirect, inspect_or_build
from gamedev.worker import codex_command, sandbox_command
from gamedev.worker_config import WorkerPolicy, load_policy


class WorkerAdapterTests(SimpleTestCase):
    def policy(self, root):
        return WorkerPolicy(
            codex=Path(sys.executable),
            opencode=Path(sys.executable),
            opencode_model="example/model",
            opencode_domains=["example.com"],
            artifacts=Path(root),
            projects={"example": {"repository": root, "checks": [[sys.executable, "--version"]]}},
        )

    def test_editor_build_returns_real_diagnostics_and_refuses_redirects(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "Example.uefnproject"
            project.touch()
            identity = ({"ok": True, "result": {"mount": root, "world": "/Example/Example.Example"}}, None)
            initialized = ({"result": {}}, "session")
            for diagnostics in ([], [{"message": "Verse compile failed"}]):
                built = ({"result": {"content": [{"text": json.dumps({"returnValue": diagnostics})}]}}, "session")
                with mock.patch("gamedev.uefn.post", side_effect=[identity, initialized, built]) as post:
                    result = inspect_or_build(project, "uefn_build")
                    self.assertEqual(result["passed"], diagnostics == [])
                    self.assertEqual(post.call_args.args[1]["params"]["arguments"]["tool_name"], "BuildAll")
            with mock.patch("gamedev.uefn.post", return_value=identity) as post:
                self.assertEqual(inspect_or_build(project, "uefn_inspect")["identity"]["mount"], root)
                self.assertEqual(post.call_count, 1)
            with mock.patch("gamedev.uefn.post", return_value=identity), self.assertRaises(ValueError):
                inspect_or_build(project, "publish")
            with self.assertRaises(ValueError):
                NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_schema_rejects_missing_or_oversized_delegations(self):
        valid = {"summary": "done", "delegations": []}
        event = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(valid)}}
        self.assertEqual(read_result("warning\n" + json.dumps(event)).summary, "done")
        for output in ("", json.dumps({"type": "turn.failed"})):
            with self.assertRaises(ValueError):
                read_result(output)
        event["item"]["text"] = json.dumps(
            {"summary": "bad", "delegations": [{"title": "child", "prompt": "work"}] * 3}
        )
        with self.assertRaises(ValueError):
            read_result(json.dumps(event))

    def test_local_policy_builds_sandboxed_argument_arrays_and_isolated_config(self):
        with tempfile.TemporaryDirectory() as root:
            policy = self.policy(root)
            path = Path(root) / "policy.json"
            path.write_text(policy.model_dump_json())
            self.assertEqual(load_policy(path), policy)
            self.assertIn("--no-daemon", codex_command(policy, root))
            self.assertIn("--sandbox-state-disable-network", sandbox_command(policy, root, [sys.executable]))
            command = opencode_command(policy, root)
            self.assertIn("features.network_proxy=true", command)
            self.assertIn("--pure", command)
            policy.opencode = None
            with self.assertRaises(ValueError):
                opencode_command(policy, root)
            with mock.patch("gamedev.delegation.Path.home", return_value=Path(root) / "missing"):
                env = opencode_environment(policy, Path(root) / "runtime")
                self.assertEqual(env["OPENCODE_DISABLE_PROJECT_CONFIG"], "true")
                self.assertIn("runtime", env["XDG_CONFIG_HOME"])

    def test_worker_lock_and_prelaunch_cancellation(self):
        with tempfile.TemporaryDirectory() as root:
            with (
                WorkerLock(Path(root) / "worker.lock"),
                self.assertRaises(OSError),
                WorkerLock(Path(root) / "worker.lock"),
            ):
                self.fail("A second worker acquired the same lock")
            with self.assertRaises(ProcessFailure):
                run_process(
                    [sys.executable, "-c", "raise AssertionError('must not start')"],
                    cwd=root,
                    timeout=1,
                    canceled=lambda: True,
                )
