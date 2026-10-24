"""A non-quota agent failure retains a writable fallback reason."""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

from gamedev.worker import run_agent
from gamedev.worker_config import ProjectPolicy, WorkerPolicy


class FallbackReasonTests(TestCase):
    def test_non_quota_failure_keeps_empty_string_and_does_not_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            policy = WorkerPolicy(
                codex=Path(sys.executable),
                artifacts=directory,
                projects={},
                quota_fallback={"agent": "opencode", "matches": ["You've hit your usage limit"]},
            )
            project = ProjectPolicy(repository=directory, checks=[[sys.executable, "-V"]])
            run = SimpleNamespace(
                pk=1,
                task=SimpleNamespace(agent="codex", parent_id=None),
                requested_profile="routine",
                delegated_by_id=None,
                selected_model="gpt-6-luna",
                selected_effort="high",
            )
            output = json.dumps({"type": "error", "message": "ordinary agent failure"})
            with (
                mock.patch("gamedev.worker.observed_process", return_value=(1, output)),
                mock.patch("gamedev.worker.run_fallback") as fallback,
            ):
                code, captured, engine, reason = run_agent(
                    run, policy, project, directory, "work", lambda: False, lambda **values: None
                )
            self.assertEqual((code, captured, engine, reason), (1, output, None, ""))
            fallback.assert_not_called()
