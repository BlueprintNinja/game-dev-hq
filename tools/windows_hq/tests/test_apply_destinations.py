"""Tooling applies to its repository; island files keep a separate boundary."""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

from gamedev.hq_plugin.models import Run
from gamedev.hq_plugin.worker_state import claim, update
from gamedev.profile import publish_profiles
from gamedev.supervisor import ProcessFailure
from gamedev.worker import execute
from gamedev.worker_config import WorkerPolicy
from hq_sdk.capabilities import execute_capability

from .test_execution import git
from .test_workspace import WorkspaceTests, workspace_only


@workspace_only
class ApplyDestinationTests(WorkspaceTests):
    def approved(self, worktree, revision):
        queued = execute_capability(
            "gamedev.run.queue",
            {"task_id": self.create()["id"], "version": 1, "prompt": "Fix tooling"},
            principal=self.principal,
        )
        self.assertTrue(queued["ok"], queued)
        run = claim(self.principal)
        diff = git(worktree, "diff", "--no-ext-diff", "--binary", "HEAD")
        fingerprint = hashlib.sha256(diff.encode()).hexdigest()
        update(
            run.pk,
            run.worker_token,
            self.principal,
            status="review",
            passed=True,
            diff=diff,
            diff_sha256=fingerprint,
            evidence_sha256="c" * 64,
            base_revision=revision,
            worktree=str(worktree),
        )
        run.refresh_from_db()
        result = execute_capability(
            "gamedev.run.decide",
            {
                "run_id": run.pk,
                "version": run.version,
                "decision": "accept",
                "note": "Reviewed exact tooling change",
                "diff_sha256": fingerprint,
            },
            principal=self.principal,
        )
        self.assertTrue(result["ok"], result)
        run.refresh_from_db()
        return run

    def test_source_apply_and_rollback_preserve_island_and_original_windows_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = root / "source"
            worktree = root / "work"
            island = root / "island"
            (repository / "tools").mkdir(parents=True)
            (island / "Content").mkdir(parents=True)
            (repository / "tools/check.py").write_bytes(b"old\n")
            (island / "Content/keep.verse").write_bytes(b"island unchanged\n")
            git(repository, "init")
            git(repository, "config", "core.autocrlf", "false")
            git(repository, "add", "-A")
            git(repository, "-c", "user.name=tests", "-c", "user.email=tests@example.test", "commit", "-m", "base")
            revision = git(repository, "rev-parse", "HEAD").strip()
            git(repository, "worktree", "add", "--detach", str(worktree), revision)
            (repository / "tools/check.py").write_bytes(b"old\r\n")
            (worktree / "tools/check.py").write_bytes(b"new\n")
            (worktree / "workspace").mkdir()
            (worktree / "workspace/README.md").write_bytes(b"instructions\n")
            git(worktree, "add", "--intent-to-add", "--all")
            policy = WorkerPolicy(
                codex=Path(sys.executable),
                artifacts=root / "artifacts",
                projects={
                    "test-island": {
                        "repository": repository,
                        "checks": [[sys.executable, "--version"]],
                        "apply_destination": island,
                        "apply_scope": ["Content/**/*.verse"],
                        "source_apply_scope": ["tools/*.py", "workspace/*.md"],
                    }
                },
            )
            publish_profiles(policy)
            run = self.approved(worktree, revision)
            response = self.client.get(f"/game-dev/runs/{run.pk}/")
            self.assertContains(response, "Source repository")
            self.assertContains(response, "Apply approved changes")
            requested = execute_capability(
                "gamedev.run.apply", {"run_id": run.pk, "version": run.version}, principal=self.principal
            )
            self.assertTrue(requested["ok"], requested)
            operation = claim(self.principal)
            # Even a content change with CRLF is a conflict; no file may be written.
            (repository / "tools/check.py").write_bytes(b"operator edit\r\n")
            with self.assertRaises(ProcessFailure):
                execute(operation, policy, self.principal)
            self.assertFalse((repository / "workspace/README.md").exists())
            (repository / "tools/check.py").write_bytes(b"old\r\n")
            execute(operation, policy, self.principal)
            operation.refresh_from_db()
            self.assertEqual(operation.status, "accepted")
            repeated = execute_capability(
                "gamedev.run.apply", {"run_id": run.pk, "version": run.version}, principal=self.principal
            )
            self.assertFalse(repeated["ok"])
            self.assertContains(self.client.get(f"/game-dev/runs/{run.pk}/"), "View the completed apply")
            self.assertEqual(Path(json.loads(operation.output)["destination"]), repository)
            self.assertEqual((repository / "tools/check.py").read_bytes(), b"new\n")
            self.assertEqual((island / "Content/keep.verse").read_bytes(), b"island unchanged\n")
            rolled = execute_capability(
                "gamedev.run.rollback", {"run_id": operation.pk, "version": operation.version}, principal=self.principal
            )
            self.assertTrue(rolled["ok"], rolled)
            execute(claim(self.principal), policy, self.principal)
            self.assertEqual((repository / "tools/check.py").read_bytes(), b"old\r\n")
            self.assertFalse((repository / "workspace/README.md").exists())
            self.assertEqual((island / "Content/keep.verse").read_bytes(), b"island unchanged\n")

    def test_island_verse_crlf_apply_conflicts_and_exact_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository, worktree, island = (root / name for name in ("source", "work", "island"))
            relative = "Content/check.verse"
            (repository / "Content").mkdir(parents=True)
            (island / "Content").mkdir(parents=True)
            (repository / relative).write_bytes(b"old\n")
            (island / relative).write_bytes(b"old\r\n")
            git(repository, "init")
            git(repository, "config", "core.autocrlf", "false")
            git(repository, "add", "-A")
            git(repository, "-c", "user.name=tests", "-c", "user.email=tests@example.test", "commit", "-m", "base")
            revision = git(repository, "rev-parse", "HEAD").strip()
            git(repository, "worktree", "add", "--detach", str(worktree), revision)
            (worktree / relative).write_bytes(b"new\n")
            policy = WorkerPolicy(
                codex=Path(sys.executable),
                artifacts=root / "artifacts",
                projects={
                    "test-island": {
                        "repository": repository,
                        "checks": [[sys.executable, "--version"]],
                        "apply_destination": island,
                        "apply_scope": ["Content/**/*.verse"],
                    }
                },
            )
            publish_profiles(policy)
            run = self.approved(worktree, revision)
            requested = execute_capability(
                "gamedev.run.apply", {"run_id": run.pk, "version": run.version}, principal=self.principal
            )
            self.assertTrue(requested["ok"], requested)
            operation = claim(self.principal)
            # Neither real operator edits nor non-text/binary differences are EOL equivalence.
            for conflict in (b"operator edit\r\n", b"old\x00\r\n", b"old\xff\r\n"):
                (island / relative).write_bytes(conflict)
                with self.assertRaises(ProcessFailure):
                    execute(operation, policy, self.principal)
                self.assertEqual((island / relative).read_bytes(), conflict)
                self.assertFalse((root / "artifacts" / f"apply-{operation.pk}" / "snapshot").exists())
            (island / relative).write_bytes(b"old\r\n")
            execute(operation, policy, self.principal)
            operation.refresh_from_db()
            self.assertEqual(operation.status, "accepted")
            self.assertEqual((island / relative).read_bytes(), b"new\n")
            self.assertEqual((Path(operation.snapshot) / relative).read_bytes(), b"old\r\n")
            self.assertEqual((repository / relative).read_bytes(), b"old\n")
            rolled = execute_capability(
                "gamedev.run.rollback", {"run_id": operation.pk, "version": operation.version}, principal=self.principal
            )
            self.assertTrue(rolled["ok"], rolled)
            execute(claim(self.principal), policy, self.principal)
            self.assertEqual((island / relative).read_bytes(), b"old\r\n")

    def test_unconfigured_tooling_is_blocked_before_queueing(self):
        from django.db import transaction

        from hq_sdk.audit import audit_operation, record_operation

        policy = WorkerPolicy(
            codex=Path(sys.executable),
            artifacts=Path(tempfile.gettempdir()),
            projects={
                "test-island": {
                    "repository": tempfile.gettempdir(),
                    "checks": [[sys.executable, "--version"]],
                    "apply_scope": ["Content/**/*.verse"],
                }
            },
        )
        publish_profiles(policy)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            git(root, "init")
            (root / "tool.py").write_bytes(b"old\n")
            git(root, "add", "-A")
            git(root, "-c", "user.name=tests", "-c", "user.email=tests@example.test", "commit", "-m", "base")
            (root / "tool.py").write_bytes(b"new\n")
            run = self.approved(root, git(root, "rev-parse", "HEAD").strip())
            result = execute_capability(
                "gamedev.run.apply", {"run_id": run.pk, "version": run.version}, principal=self.principal
            )
            self.assertFalse(result["ok"])
            self.assertContains(self.client.get(f"/game-dev/runs/{run.pk}/"), "Apply is unavailable")
            self.assertNotContains(self.client.get(f"/game-dev/runs/{run.pk}/"), ">Apply approved changes<")
            with transaction.atomic(), audit_operation(operation="test.apply", principal=self.principal):
                failed = Run.objects.create(
                    task=run.task, kind="apply", status="failed", applies=run, output="Outside approved scope"
                )
                record_operation("test.apply", "Created failed apply fixture", required=True)
            response = self.client.get(f"/game-dev/runs/{failed.pk}/")
            self.assertContains(response, "original approved result is retained")
            self.assertNotContains(response, "Ask again")
            retried = execute_capability(
                "gamedev.run.retry", {"run_id": failed.pk, "version": failed.version}, principal=self.principal
            )
            self.assertFalse(retried["ok"])
