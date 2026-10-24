"""The worker refreshes profiles from existing supervised process polls."""

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from gamedev import worker
from gamedev.supervisor import ProcessFailure, run_process
from gamedev.worker_config import QuotaFallback, WorkerPolicy


class WorkerHeartbeatTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = [0.0]
        self.clock = mock.patch("gamedev.worker.time.monotonic", side_effect=lambda: self.now[0])
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.policy = WorkerPolicy(
            codex=Path(sys.executable),
            opencode=Path(sys.executable),
            opencode_model="example/model",
            opencode_domains=["example.com"],
            artifacts=self.root / "artifacts",
            projects={"example": {"repository": self.root, "checks": [[sys.executable, "--version"]]}},
        )
        self.project = self.policy.projects["example"]
        self.heartbeat = worker.profile_heartbeat(self.policy)
        self.publications = mock.patch("gamedev.worker.publish_profiles")
        self.publisher = self.publications.start()
        self.addCleanup(self.publications.stop)

    def progress_poll(self, progress, timestamps):
        for timestamp in timestamps:
            self.now[0] = timestamp
            progress("")  # Existing monitor polls progress with an empty output file too.

    def test_publication_is_throttled_on_silent_process_polls(self):
        with (
            mock.patch("threading.Thread.start") as thread_start,
            mock.patch("socket.socket") as socket_create,
        ):
            self.progress_poll(self.heartbeat, [3, 27, 30, 33, 59, 60, 61])
        self.assertEqual(self.publisher.call_args_list, [mock.call(self.policy), mock.call(self.policy)])
        thread_start.assert_not_called()
        socket_create.assert_not_called()

    def test_coding_fallback_and_configured_checks_reuse_progress_callback(self):
        observed = []

        def fake_process(_argv, **options):
            observed.append(options["progress"])
            start = 30 if len(observed) == 1 else 90
            self.progress_poll(options["progress"], [start, start + 30])
            return 0, "ok"

        fallback_policy = self.policy.model_copy(
            update={"quota_fallback": QuotaFallback(agent="opencode", matches=["quota"])}
        )
        with (
            mock.patch.object(worker, "run_process", side_effect=fake_process) as launch,
            mock.patch.object(worker, "git", return_value=""),
        ):
            worker.run_fallback(
                fallback_policy,
                self.project,
                self.root,
                "prompt",
                lambda: False,
                lambda **_values: None,
                "quota",
                "agent output",
                self.heartbeat,
            )
            worker.collect_evidence(self.policy, self.project, self.root, lambda: False, self.heartbeat)
        self.assertEqual(launch.call_count, 2)
        self.assertEqual(len(observed), 2)
        self.assertIsNotNone(observed[0])
        self.assertIs(observed[1], self.heartbeat)
        self.assertEqual(self.publisher.call_count, 4)

    def test_coding_agent_uses_callback_and_launches_only_the_selected_process(self):
        run = SimpleNamespace(
            pk=1,
            task=SimpleNamespace(agent="opencode", parent_id=None),
            requested_profile="routine",
            selected_model="",
            selected_effort="",
            delegated_by_id=None,
            retried_from_id=None,
            escalation_root_id=None,
        )
        captured = []

        def fake_process(_argv, **options):
            captured.append(options["progress"])
            self.progress_poll(options["progress"], [30, 60])
            return 0, "agent output"

        with (
            mock.patch.object(worker, "run_process", side_effect=fake_process) as launch,
            mock.patch("gamedev.delegation.Path.home", return_value=self.root / "missing"),
        ):
            worker.run_agent(
                run, self.policy, self.project, self.root, "task", lambda: False, lambda **_: None, self.heartbeat
            )
        self.assertEqual(launch.call_count, 1)
        self.assertTrue(callable(captured[0]))
        self.assertEqual(self.publisher.call_count, 2)

    def test_editor_process_poll_uses_optional_callback(self):
        editor_project = SimpleNamespace(uefn_project=self.root / "project.uefnproject")
        run = SimpleNamespace(pk=2, kind="uefn_inspect", worker_token="token")
        (self.root / "project.uefnproject").touch()
        seen = []

        def fake_process(_argv, **options):
            seen.append(options["progress"])
            self.progress_poll(options["progress"], [30, 60])
            return 0, '{"ok": true}'

        with (
            mock.patch.object(worker, "run_process", side_effect=fake_process) as launch,
            mock.patch.object(
                worker.Run.objects, "filter", return_value=mock.Mock(exists=mock.Mock(return_value=False))
            ),
            mock.patch.object(worker, "update"),
        ):
            worker.execute_editor(run, editor_project, mock.Mock(), self.heartbeat)
        self.assertEqual(launch.call_count, 1)
        self.assertIs(seen[0], self.heartbeat)
        self.assertEqual(self.publisher.call_count, 2)

    def test_idle_claim_and_cancellation_behavior_remain_unchanged(self):
        with mock.patch.object(worker, "claim", return_value=None):
            self.assertFalse(worker.process_next(self.policy, mock.Mock(), self.heartbeat))
        self.publisher.assert_not_called()
        with self.assertRaises(ProcessFailure):
            run_process(
                [sys.executable, "-c", "raise AssertionError('must not start')"],
                cwd=self.root,
                timeout=1,
                canceled=lambda: True,
            )
