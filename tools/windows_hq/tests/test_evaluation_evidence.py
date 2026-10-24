"""Authoritative selection, approval binding, redaction and read-only boundaries."""

import copy
import hashlib
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.utils import timezone
from pydantic import ValidationError

from gamedev.evidence import digest
from gamedev.hq_plugin.evaluation_evidence import EvidenceSelection, read_run_evidence
from gamedev.hq_plugin.models import Run, Task, TraceSpan
from gamedev.tests.test_revision_objects import repository
from gamedev.verified_evidence import EvidenceRefused, VerifiedRunEvidence, record_fingerprint
from gamedev.worker_config import ProjectPolicy
from hq_sdk.capabilities import AuthorizationError, Principal, capability_registry

from .test_workspace import workspace_only

PRIVATE = "PRIVATE_SENTINEL prompt output diff path command secret project title trace"


@workspace_only
class VerifiedEvidenceTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.revision, _ = repository(self.root)
        self.project = ProjectPolicy(
            repository=self.root,
            checks=[{"kind": "tests", "command": [sys.executable, "--version"]}],
            required_kinds=["tests"],
        )
        self.projects = {"private-project-sentinel": self.project}
        self.principal = Principal("reader", "cli", frozenset({"gamedev.evaluation.read"}))
        self.task = Task.objects.create(project_slug="private-project-sentinel", title=PRIVATE, evidence=PRIVATE)
        self.now = timezone.now()
        self.checks = [{"kind": "tests", "exit_code": 0, "command": [PRIVATE], "output": PRIVATE}]
        self.run = Run.objects.create(
            task=self.task,
            prompt=PRIVATE,
            output=PRIVATE,
            diff=PRIVATE,
            kind="coding",
            status="accepted",
            base_revision=self.revision,
            selected_model="gpt-6-luna",
            engine="opencode",
            passed=True,
            verification=self.checks,
            evidence_sha256=digest(self.checks),
            diff_sha256=hashlib.sha256(PRIVATE.encode()).hexdigest(),
            approved_evidence_sha256=digest(self.checks),
            approved_diff_sha256=hashlib.sha256(PRIVATE.encode()).hexdigest(),
            started_at=self.now - timedelta(seconds=20),
            finished_at=self.now - timedelta(seconds=10),
            approved_at=self.now - timedelta(seconds=5),
            metrics={
                "usage_observed": True,
                "llm.token_count.prompt": 0,
                "llm.token_count.completion": 8,
                "llm.model_name": PRIVATE,
                "llm.cost.total": 20,
                "arbitrary": PRIVATE,
            },
            worktree=PRIVATE,
            worker_token="sensitive-worker-token",
            requested_by=PRIVATE,
            decision_note=PRIVATE,
        )
        self.change(created_at=self.now - timedelta(seconds=30))

    def change(self, **values):
        Run.objects.filter(pk=self.run.pk).update(**values)
        self.run.refresh_from_db()

    def read(self, **selection):
        return read_run_evidence({"run_id": self.run.pk, **selection}, principal=self.principal, projects=self.projects)

    def assert_refused(self, code):
        with self.assertRaises(EvidenceRefused) as caught:
            self.read()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)
        self.assertNotIn(PRIVATE, str(caught.exception))

    def unapprove(self, **values):
        self.change(
            status="failed",
            passed=False,
            approved_at=None,
            approved_diff_sha256="",
            approved_evidence_sha256="",
            **values,
        )

    def test_valid_accepted_deterministic_bound_record(self):
        with self.assertNumQueries(1):
            record = self.read()
        self.assertEqual(record.record_type, "hq-verified-run-evidence")
        self.assertEqual(record.schema_version, 1)
        self.assertEqual(record.run_id, self.run.pk)
        self.assertEqual(record.task_id, self.task.pk)
        self.assertEqual(record.approval, "verified")
        self.assertEqual(record.completeness, "complete")
        self.assertEqual(record.checks[0].model_dump(), {"kind": "tests", "passed": True})
        self.assertEqual(record.captured_evidence_sha256, digest(self.checks))
        self.assertEqual(record.fingerprint, record_fingerprint(record))
        self.assertEqual(record, self.read())
        self.assertEqual(VerifiedRunEvidence.model_validate_json(record.model_dump_json()), record)
        self.assertFalse(record.gameplay_verified)
        self.assertEqual(record.timings.duration_seconds, 10)
        self.assertEqual(record.timings.queue_seconds, 10)
        self.assertEqual(record.timings.approval_seconds, 5)

    def test_selected_model_is_not_actual_model_and_usage_is_independent(self):
        record = self.read()
        self.assertEqual(record.selected_model, "gpt-6-luna")
        self.assertEqual(record.actual_engine, "opencode")
        self.assertIsNone(record.actual_model)
        self.assertEqual(record.usage.model_dump(), {"input_tokens": 0, "output_tokens": 8})
        self.change(engine="", selected_model="", metrics={})
        record = self.read()
        self.assertIsNone(record.selected_model)
        self.assertIsNone(record.actual_engine)
        self.assertIsNone(record.actual_model)
        self.assertEqual(record.usage.model_dump(), {"input_tokens": None, "output_tokens": None})
        self.assertIn("usage_not_reported", record.reason_codes)

    def test_invalid_usage_is_null_and_never_coerced_or_derived(self):
        for value in (True, -1, 1.0, "12", 10**12 + 1, [], {}):
            with self.subTest(value=value):
                self.change(
                    metrics={
                        "usage_observed": True,
                        "llm.token_count.prompt": value,
                        "llm.token_count.completion": 0,
                        "llm.token_count.total": 100,
                    }
                )
                record = self.read()
                self.assertEqual(record.usage.model_dump(), {"input_tokens": None, "output_tokens": 0})
                self.assertIn("usage_partially_reported", record.reason_codes)
        self.change(metrics={"llm.token_count.prompt": 10, "usage_observed": "true"})
        self.assertIsNone(self.read().usage.input_tokens)
        self.change(metrics={"usage_observed": True, "llm.token_count.prompt": 10**12})
        self.assertEqual(self.read().usage.input_tokens, 10**12)

    def test_failure_is_representable_and_task_done_is_not_approval(self):
        checks = [{"kind": "tests", "exit_code": 1, "passed": True, "output": PRIVATE}]
        self.unapprove(verification=checks, evidence_sha256=digest(checks))
        Task.objects.filter(pk=self.task.pk).update(status="done")
        record = self.read()
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.task_status, "done")
        self.assertFalse(record.checks[0].passed)
        self.assertEqual(record.approval, "not_approved")
        self.assertIsNone(record.approved_evidence_sha256)
        self.assertIn("not_approved", record.reason_codes)
        self.assertEqual(record.completeness, "complete")

    def test_incomplete_capture_is_explicit_and_missing_capture_is_refused(self):
        self.unapprove(verification=[], evidence_sha256=digest([]), started_at=None, finished_at=None)
        record = self.read()
        self.assertEqual(record.completeness, "incomplete")
        self.assertEqual(record.checks, [])
        self.assertTrue(
            {"checks_missing", "required_checks_missing", "execution_timestamps_missing"} <= set(record.reason_codes)
        )
        self.change(status="running")
        self.assertIn("run_in_progress", self.read().reason_codes)
        self.change(evidence_sha256="")
        self.assert_refused("missing_evidence_hash")

    def test_capture_tampering_and_stale_approval_are_refused(self):
        original = {
            name: getattr(self.run, name)
            for name in (
                "verification",
                "diff",
                "diff_sha256",
                "evidence_sha256",
                "approved_diff_sha256",
                "approved_evidence_sha256",
            )
        }
        changed = [{"kind": "tests", "exit_code": 0, "command": [PRIVATE], "output": "changed"}]
        for values, code in (
            ({"verification": changed}, "evidence_digest_mismatch"),
            ({"diff": "changed"}, "diff_digest_mismatch"),
            ({"approved_diff_sha256": "a" * 64}, "approved_diff_mismatch"),
            ({"approved_evidence_sha256": "a" * 64}, "approved_evidence_mismatch"),
            ({"verification": changed, "evidence_sha256": digest(changed)}, "approved_evidence_mismatch"),
            ({"diff_sha256": ""}, "missing_diff_hash"),
            ({"evidence_sha256": "bad"}, "missing_evidence_hash"),
        ):
            with self.subTest(code=code):
                self.change(**{**original, **values})
                self.assert_refused(code)
        self.change(**original)
        self.assertEqual(self.read().approval, "verified")

    def test_accepted_requires_passed_status_timestamp_and_all_required_checks(self):
        self.change(passed=False)
        self.assert_refused("accepted_checks_invalid")
        self.change(passed=True, approved_at=None)
        self.assert_refused("accepted_timestamps_missing")
        self.change(approved_at=self.now - timedelta(seconds=5), status="rejected")
        self.assert_refused("approval_status_mismatch")
        self.change(status="accepted")
        self.project.required_kinds = ["tests", "source"]
        self.assert_refused("accepted_checks_invalid")
        self.project.required_kinds = ["tests"]
        checks = [{"kind": "tests", "exit_code": 1}]
        self.change(verification=checks, evidence_sha256=digest(checks), approved_evidence_sha256=digest(checks))
        self.assert_refused("accepted_checks_invalid")

    def test_ambiguous_unknown_and_malformed_checks_fail_closed(self):
        for checks, code in (
            (self.checks * 2, "ambiguous_check_kind"),
            ([{"kind": PRIVATE, "exit_code": 0}], "unknown_check_kind"),
            ([{"kind": "tests", "exit_code": False}], "invalid_check_exit_code"),
            ([{"kind": "tests", "exit_code": "0"}], "invalid_check_exit_code"),
            ([{"kind": "tests"}], "invalid_check_exit_code"),
            ([PRIVATE], "invalid_checks"),
            ({"tests": True}, "invalid_checks"),
            (self.checks * 9, "invalid_checks"),
        ):
            with self.subTest(code=code):
                self.change(verification=checks, evidence_sha256=digest(checks))
                self.assert_refused(code)

    def test_unresolvable_revision_and_wrong_repository_refuse_without_shell(self):
        for revision, code in (
            ("HEAD", "invalid_revision"),
            ("a" * 40, "revision_unresolvable"),
            ("", "invalid_revision"),
        ):
            with self.subTest(revision=revision):
                self.change(base_revision=revision)
                self.assert_refused(code)
        self.change(base_revision=self.revision)
        self.projects.clear()
        self.assert_refused("repository_not_configured")

    def test_permissions_precede_selection_database_and_filesystem(self):
        for grants in (set(), {"gamedev.read"}, {"gamedev.write"}, {"gamedev.metrics.read"}, {"gamedev.traces.read"}):
            denied = Principal("denied", "cli", frozenset(grants))
            with self.subTest(grants=grants), self.assertNumQueries(0), self.assertRaises(AuthorizationError):
                read_run_evidence({"run_id": self.run.pk}, principal=denied, projects=self.projects)
        # Permission is intentionally ungranted by the plugin manifest; no new
        # remotely callable capability is introduced in this milestone.
        self.assertFalse(any("evaluation" in name for name in capability_registry() if name.startswith("gamedev.")))

    def test_explicit_strict_selection_and_unknown_fields(self):
        for selection in (
            {},
            {"run_id": 0},
            {"run_id": True},
            {"run_id": "1"},
            {"run_id": 1.0},
            {"run_id": self.run.pk, "project": "other"},
            {"run_id": self.run.pk, "latest": True},
        ):
            with (
                self.subTest(selection=selection),
                self.assertNumQueries(0),
                self.assertRaisesRegex(EvidenceRefused, "invalid_selection"),
            ):
                read_run_evidence(selection, principal=self.principal, projects=self.projects)
        with self.assertRaisesRegex(EvidenceRefused, "run_not_found"):
            read_run_evidence({"run_id": 2**63 - 1}, principal=self.principal, projects=self.projects)
        self.assertEqual(EvidenceSelection(run_id=self.run.pk).run_id, self.run.pk)

    def test_strict_nested_output_schema_and_fingerprint_tampering(self):
        value = self.read().model_dump()
        for field in (None, "usage", "timings"):
            changed = copy.deepcopy(value)
            (changed if field is None else changed[field])["prompt"] = PRIVATE
            with self.subTest(field=field), self.assertRaises(ValidationError):
                VerifiedRunEvidence.model_validate(changed)
        changed = copy.deepcopy(value)
        changed["checks"][0]["output"] = PRIVATE
        with self.assertRaises(ValidationError):
            VerifiedRunEvidence.model_validate(changed)
        for key, replacement in (
            ("schema_version", True),
            ("schema_version", 2),
            ("run_id", True),
            ("gameplay_verified", True),
            ("gameplay_verified", 0),
            ("status", "success"),
            ("fingerprint", "a" * 64),
        ):
            changed = {**value, key: replacement}
            with self.subTest(key=key, replacement=replacement), self.assertRaises(ValidationError):
                VerifiedRunEvidence.model_validate(changed)
        changed = copy.deepcopy(value)
        changed["usage"]["input_tokens"] = True
        with self.assertRaises(ValidationError):
            VerifiedRunEvidence.model_validate(changed)

    def test_privacy_allowlist_and_no_effects(self):
        before = list(Run.objects.values())
        queries = []

        def read_only(execute, sql, params, many, context):
            self.assertTrue(sql.lstrip().upper().startswith("SELECT"), sql)
            queries.append(sql)
            return execute(sql, params, many, context)

        with (
            connection.execute_wrapper(read_only),
            mock.patch("subprocess.Popen", side_effect=AssertionError("process")),
            mock.patch("socket.socket", side_effect=AssertionError("network")),
            mock.patch("gamedev.otlp.export_spans", side_effect=AssertionError("export")),
            mock.patch.object(TraceSpan.objects, "create", side_effect=AssertionError("trace")),
        ):
            record = self.read()
        self.assertEqual(len(queries), 1)
        self.assertEqual(before, list(Run.objects.values()))
        raw = record.model_dump_json()
        for secret in (PRIVATE, "private-project-sentinel", "sensitive-worker-token", str(self.root)):
            self.assertNotIn(secret, raw)
        forbidden = {
            "prompt",
            "output",
            "diff",
            "changed_paths",
            "command",
            "stdout",
            "stderr",
            "attributes",
            "metrics",
            "task_title",
            "project",
            "dataset_id",
            "case_id",
            "verdict",
            "grade",
        }

        def check_keys(value):
            if isinstance(value, dict):
                self.assertFalse(forbidden & set(value))
                for item in value.values():
                    check_keys(item)
            elif isinstance(value, list):
                for item in value:
                    check_keys(item)

        check_keys(json.loads(raw))
        for column in ("prompt", "output", "worktree", "worker_token", "title", "attributes"):
            self.assertNotIn(f'."{column}"', queries[0])

    def test_timestamp_order_future_times_and_missing_terminal_time(self):
        for values in (
            {"approved_at": self.now + timedelta(days=1)},
            {"approved_at": self.now - timedelta(seconds=25)},
            {"finished_at": self.now - timedelta(seconds=40)},
        ):
            original = {key: getattr(self.run, key) for key in values}
            self.change(**values)
            self.assert_refused("invalid_timestamps")
            self.change(**original)
        self.change(finished_at=None)
        self.assert_refused("accepted_timestamps_missing")

    def test_structured_lineage_ids_only_and_capture_bound_changes_fingerprint(self):
        root_task = Task.objects.create(project_slug=self.task.project_slug, title=PRIVATE)
        previous = Run.objects.create(task=root_task, prompt=PRIVATE, status="failed")
        Task.objects.filter(pk=self.task.pk).update(parent=root_task)
        self.change(delegated_by=previous, retried_from=previous, escalation_root=previous)
        record = self.read()
        self.assertEqual(record.parent_task_id, root_task.pk)
        self.assertEqual(record.delegated_by_run_id, previous.pk)
        self.assertEqual(record.retried_from_run_id, previous.pk)
        self.assertEqual(record.escalation_root_run_id, previous.pk)
        changed_checks = copy.deepcopy(self.checks)
        changed_checks[0]["output"] += "different captured log"
        self.change(
            verification=changed_checks,
            evidence_sha256=digest(changed_checks),
            approved_evidence_sha256=digest(changed_checks),
        )
        changed = self.read()
        self.assertEqual(record.checks, changed.checks)
        self.assertNotEqual(record.fingerprint, changed.fingerprint)
