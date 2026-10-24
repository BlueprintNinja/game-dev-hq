"""A run whose agent is out of allowance finishes with the fallback engine."""

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from gamedev import worker
from gamedev.supervisor import ProcessFailure
from gamedev.worker_config import QuotaFallback, WorkerPolicy

QUOTA = "You've hit your usage limit"
TOOLS = Path("/tools").resolve()


class QuotaFallbackTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.saved = []

    def policy(self, matches=(QUOTA,)):
        fallback = QuotaFallback(agent="opencode", matches=list(matches)) if matches else None
        return WorkerPolicy(
            codex=TOOLS / "codex",
            opencode=TOOLS / "opencode",
            opencode_model="opencode/minimax-m3.1-flash-free",
            opencode_domains=["opencode.ai"],
            quota_fallback=fallback,
            artifacts=self.root / "artifacts",
            projects={
                "game-dev-hq": {
                    "repository": self.root,
                    "checks": [[str(TOOLS / "check")]],
                    "timeout_seconds": 60,
                }
            },
        )

    def run_object(self):
        task = SimpleNamespace(project_slug="game-dev-hq", agent="codex", parent_id=None)
        return SimpleNamespace(
            pk=7,
            kind="coding",
            requested_profile="routine",
            prompt="Write the file.",
            worker_token="token",
            delegated_by_id=None,
            task=task,
        )

    def run_with(self, agent_output, agent_status=1, matches=(QUOTA,)):
        calls = []

        def fake_process(argv, **options):
            calls.append(list(argv))
            if "--pure" in argv:
                return 0, "the fallback wrote the file"
            if "--no-daemon" in argv:
                return agent_status, agent_output
            return 0, "checks passed"

        def fake_git(repository, *args):
            if args[:1] == ("rev-parse",):
                return "abc123"
            if args[:1] == ("diff",):
                return "diff --git a/file b/file"
            return ""

        with (
            mock.patch("gamedev.delegation.Path.home", return_value=self.root / "missing"),
            mock.patch.object(worker, "run_process", side_effect=fake_process),
            mock.patch.object(worker, "git", side_effect=fake_git),
            mock.patch.object(worker, "update", side_effect=lambda *a, **k: self.saved.append(k)),
            mock.patch.object(worker, "dispatch") as dispatch,
        ):
            error = None
            try:
                worker.execute(self.run_object(), self.policy(matches), mock.Mock())
            except ProcessFailure as failure:
                error = failure
        return calls, dispatch, error

    def test_it_finishes_the_work_with_the_fallback_and_says_so(self):
        output = json.dumps({"type": "error", "message": f"{QUOTA}. Try again at 10:13 PM."})
        calls, dispatch, error = self.run_with(output)
        self.assertIsNone(error)
        self.assertTrue(any("--pure" in call for call in calls), "the fallback engine did not run")
        self.assertFalse(dispatch.called, "the fallback output is not a Codex result and must not be read as one")
        saved = self.saved[-1]
        self.assertEqual(saved["status"], "review")
        self.assertTrue(saved["passed"])
        self.assertIn("fallback to opencode", saved["output"])
        self.assertIn(QUOTA, saved["output"])
        self.assertIn("the fallback wrote the file", saved["output"])

    def test_another_failure_is_not_a_quota_and_does_not_fall_back(self):
        output = json.dumps({"type": "error", "message": "The model refused the request."})
        calls, _dispatch, error = self.run_with(output)
        self.assertFalse(any("--pure" in call for call in calls), "the fallback engine ran for another failure")
        self.assertIsNotNone(error)
        self.assertIn("status 1", str(error))

    def test_without_a_policy_the_run_fails_as_it_did(self):
        output = json.dumps({"type": "error", "message": f"{QUOTA}. Try again at 10:13 PM."})
        calls, _dispatch, error = self.run_with(output, matches=None)
        self.assertFalse(any("--pure" in call for call in calls), "the fallback ran without a policy")
        self.assertIsNotNone(error)
