"""The worker waits for the workspace before it opens the database."""

import os
import sys
from unittest import mock

from django.test import SimpleTestCase

from gamedev import worker


class WorkerStartupTests(SimpleTestCase):
    """The workspace migrates that database on every start."""

    def run_worker(self, argv, side_effect=None):
        environment = {"GAMEDEV_WORKSPACE_DATA": os.environ.get("GAMEDEV_WORKSPACE_DATA", "var/workspace")}
        with (
            mock.patch.dict(os.environ, environment),
            mock.patch.object(sys, "argv", ["gamedev.worker", *argv]),
            mock.patch.object(worker, "load_policy", return_value=mock.Mock()),
            mock.patch.object(worker, "cli_principal", return_value=mock.Mock()),
            mock.patch.object(worker, "WorkerLock"),
            mock.patch.object(worker, "recover"),
            mock.patch.object(worker, "process_next", return_value=False),
            mock.patch.object(worker, "wait_until_ready", side_effect=side_effect) as wait,
        ):
            worker.main()
        return wait

    def test_it_waits_for_the_workspace_before_it_claims_anything(self):
        wait = self.run_worker(
            ["--policy", "policy.json", "--once", "--workspace-url", "http://127.0.0.1:18767/health/live/"]
        )
        wait.assert_called_once_with("http://127.0.0.1:18767/health/live/", timeout=600)

    def test_it_gives_up_with_what_the_workspace_answered(self):
        refusal = TimeoutError("The workspace did not answer at http://127.0.0.1:18767/health/live/: refused")
        with mock.patch("sys.stderr") as errors, self.assertRaises(SystemExit) as raised:
            self.run_worker(
                ["--policy", "policy.json", "--once", "--workspace-url", "http://127.0.0.1:18767/health/live/"],
                side_effect=refusal,
            )
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("did not answer", "".join(call.args[0] for call in errors.write.call_args_list))
