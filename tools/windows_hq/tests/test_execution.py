"""Execution authorization, durability, evidence and cancellation regressions."""

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from gamedev.delegation import CodexResult, dispatch, opencode_environment
from gamedev.hq_plugin.models import Run, Task
from gamedev.hq_plugin.worker_state import claim, recover, update
from gamedev.supervisor import ProcessFailure, run_process
from gamedev.worker import process_next
from gamedev.worker_config import WorkerPolicy
from hq_sdk.capabilities import Principal, execute_capability

from .test_workspace import WorkspaceTests, workspace_only


@workspace_only
class ExecutionTests(WorkspaceTests):
    def queued(self):
        task = Task.objects.get(pk=self.create()["id"])
        result = execute_capability(
            "gamedev.run.queue",
            {"task_id": task.pk, "version": task.version, "prompt": "Add a useful note"},
            principal=self.principal,
        )
        self.assertTrue(result["ok"], result)
        return Run.objects.get(pk=result["id"])

    def decision(self, run, decision, note="Reviewed the exact changes"):
        return execute_capability(
            "gamedev.run.decide",
            {"run_id": run.pk, "version": run.version, "decision": decision, "note": note},
            principal=self.principal,
        )

    def test_queue_denies_duplicate_untrusted_command_and_unprivileged_actor(self):
        run = self.queued()
        task = run.task
        task.refresh_from_db()
        for extra in ({}, {"executable": "unsafe"}):
            result = execute_capability(
                "gamedev.run.queue",
                {"task_id": task.pk, "version": task.version, "prompt": "work", **extra},
                principal=self.principal,
            )
            self.assertFalse(result["ok"])
        self.assertFalse(
            execute_capability(
                "gamedev.run.decide",
                {"run_id": run.pk, "version": run.version, "decision": "cancel"},
                principal=Principal("denied", "web", frozenset()),
            )["ok"]
        )

    def test_claim_review_failed_checks_stale_decision_and_required_audit(self):
        run = self.queued()
        owned = claim(self.principal)
        self.assertEqual(owned.pk, run.pk)
        self.assertIsNone(claim(self.principal))
        with self.assertRaises(ValueError):
            update(run.pk, "wrong-token", self.principal, status="review")
        update(run.pk, owned.worker_token, self.principal, status="review", passed=False)
        self.assertFalse(self.decision(run, "accept")["ok"])
        run.refresh_from_db()
        self.assertFalse(self.decision(run, "accept")["ok"])
        with (
            mock.patch("gamedev.hq_plugin.execution.record_operation", side_effect=RuntimeError("audit down")),
            self.assertRaises(RuntimeError),
        ):
            self.decision(run, "reject")
        run.refresh_from_db()
        self.assertEqual(run.status, "review")
        self.assertTrue(self.decision(run, "reject")["ok"])

    def test_cancel_and_recovery_never_replay_an_interrupted_job(self):
        run = self.queued()
        self.assertTrue(self.decision(run, "cancel")["ok"])
        self.assertIsNone(claim(self.principal))
        other = self.queued()
        claim(self.principal)
        recover(self.principal)
        other.refresh_from_db()
        self.assertEqual(other.status, "failed")
        self.assertIsNone(claim(self.principal))

    def test_only_owning_codex_run_can_delegate_with_a_bounded_schema(self):
        run = self.queued()
        with self.assertRaises(ValueError):
            dispatch(run, CodexResult(summary="", delegations=[]), self.principal)
        run = claim(self.principal)
        result = CodexResult.model_validate(
            {"summary": "delegate", "delegations": [{"title": "Child", "prompt": "Write a small note"}]}
        )
        dispatch(run, result, self.principal)
        child = run.delegated_runs.get()
        self.assertEqual(child.task.parent_id, run.task_id)
        self.assertEqual(child.task.agent, "opencode")
        with self.assertRaises(ValueError):
            dispatch(run, result, self.principal)

    def test_worker_collects_diff_checks_and_escaped_review_page(self):
        run = self.queued()
        with tempfile.TemporaryDirectory() as root:
            policy = WorkerPolicy(
                codex=Path(sys.executable),
                artifacts=Path(root),
                projects={"test-island": {"repository": root, "checks": [[sys.executable, "--version"]]}},
            )
            output = json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": '{"summary":"done","delegations":[]}'},
                }
            )
            with (
                mock.patch("gamedev.worker.git", side_effect=["a" * 40, "", "", "<script>evil</script>"]),
                mock.patch("gamedev.worker.run_process", side_effect=[(0, output), (0, "PASS")]),
            ):
                self.assertTrue(process_next(policy, self.principal))
        run.refresh_from_db()
        self.assertEqual(run.status, "review")
        self.assertTrue(run.passed)
        self.assertEqual(run.diff_sha256, hashlib.sha256(run.diff.encode()).hexdigest())
        response = self.client.get(f"/game-dev/runs/{run.pk}/")
        self.assertContains(response, "&lt;script&gt;evil&lt;/script&gt;")
        self.assertTrue(self.decision(run, "accept")["ok"])
        run.task.refresh_from_db()
        self.assertEqual(run.task.status, "done")

    def test_cancellation_racing_with_completion_cannot_be_accepted(self):
        self.queued()
        owned = claim(self.principal)
        self.assertTrue(self.decision(owned, "cancel")["ok"])
        update(owned.pk, owned.worker_token, self.principal, status="review", passed=True)
        owned.refresh_from_db()
        self.assertEqual(owned.status, "canceled")
        self.assertFalse(owned.passed)
        self.assertFalse(self.decision(owned, "accept")["ok"])


class SupervisorTests(SimpleTestCase):
    def test_editor_refuses_wrong_project_before_requesting_a_build(self):
        from gamedev.uefn import inspect_or_build

        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "Example.uefnproject"
            project.touch()
            with mock.patch(
                "gamedev.uefn.post",
                return_value=({"ok": True, "result": {"mount": "/wrong", "world": "/Example/Example"}}, None),
            ) as posted:
                with self.assertRaises(ValueError):
                    inspect_or_build(project, "uefn_build")
                self.assertEqual(posted.call_count, 1)

    def test_process_output_and_timeout(self):
        with tempfile.TemporaryDirectory() as root:
            code, output = run_process([sys.executable, "-c", "print('supervised')"], cwd=root, timeout=10)
            self.assertEqual(code, 0)
            self.assertIn("supervised", output)
            with self.assertRaises(ProcessFailure):
                run_process([sys.executable, "-c", "import time;time.sleep(30)"], cwd=root, timeout=0.1)

    def test_opencode_forbids_command_execution_and_recursive_delegation(self):
        config = json.loads(opencode_environment(None)["OPENCODE_CONFIG_CONTENT"])
        for permission in ("shell", "bash", "task", "webfetch", "external_directory"):
            self.assertEqual(config["agent"]["hq-delegate"]["permission"][permission], "deny")

    def test_policy_rejects_relative_commands_and_unknown_input(self):
        with self.assertRaises(ValueError):
            WorkerPolicy(
                codex=Path(sys.executable),
                artifacts=Path(tempfile.gettempdir()),
                projects={"test": {"repository": tempfile.gettempdir(), "checks": [["python", "test.py"]]}},
            )
