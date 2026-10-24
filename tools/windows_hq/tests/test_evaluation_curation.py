"""Fresh synthetic records and constructed local Git storage; never operator runs."""

import copy
import hashlib
import json
import pickle
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.utils import timezone
from pydantic import ValidationError

from gamedev.curation import (
    ApprovedShortlist,
    CurationRefused,
    LocalCurationPlan,
    plan_fingerprint,
    proposal_fingerprint,
)
from gamedev.evidence import digest
from gamedev.hq_plugin.evaluation_curation import build_local_curation_plan
from gamedev.hq_plugin.evaluation_evidence import read_run_evidence
from gamedev.hq_plugin.models import Run, Task
from gamedev.tests.test_revision_objects import repository
from gamedev.worker_config import ProjectPolicy
from hq_sdk.capabilities import AuthorizationError, Principal

from .test_workspace import workspace_only

PRIVATE = "PRIVATE_SENTINEL"
FLAGS = (
    "benchmark_ready",
    "reproducibility_approved",
    "export_approved",
    "source_extraction_approved",
    "model_experiment_approved",
)


@workspace_only
class LocalCurationTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.revision, _ = repository(self.root)
        self.projects = {
            "synthetic-project": ProjectPolicy(
                repository=self.root,
                checks=[{"kind": "tests", "command": [sys.executable, "--version"]}],
                required_kinds=["tests"],
            )
        }
        self.principal = Principal(
            "curator", "cli", frozenset({"gamedev.evaluation.read", "gamedev.evaluation.curate"})
        )
        self.now = timezone.now()

    def make_run(self, *, task=None, status="accepted", diff=PRIVATE, checks=None, **values):
        task = task or Task.objects.create(project_slug="synthetic-project", title=PRIVATE, evidence=PRIVATE)
        checks = (
            [{"kind": "tests", "exit_code": 0, "command": [PRIVATE], "output": PRIVATE}] if checks is None else checks
        )
        accepted = status == "accepted"
        diff_hash = hashlib.sha256(diff.encode()).hexdigest()
        run = Run.objects.create(
            task=task,
            prompt=PRIVATE,
            output=PRIVATE,
            worktree=PRIVATE,
            decision_note=PRIVATE,
            kind="coding",
            status=status,
            diff=diff,
            diff_sha256=diff_hash,
            base_revision=self.revision,
            verification=checks,
            evidence_sha256=digest(checks),
            passed=accepted,
            started_at=self.now - timedelta(seconds=20),
            finished_at=self.now - timedelta(seconds=10),
            approved_at=self.now - timedelta(seconds=5) if accepted else None,
            approved_diff_sha256=diff_hash if accepted else "",
            approved_evidence_sha256=digest(checks) if accepted else "",
            **values,
        )
        Run.objects.filter(pk=run.pk).update(created_at=self.now - timedelta(seconds=30))
        return run

    def shortlist_payload(self, groups):
        return {
            "record_type": "hq-local-curation-shortlist",
            "schema_version": 1,
            "families": [
                {
                    "family_id": f"family-{index}",
                    "role": role,
                    "runs": [
                        {
                            "run_id": run.pk,
                            "expected_fingerprint": read_run_evidence(
                                {"run_id": run.pk}, principal=self.principal, projects=self.projects
                            ).fingerprint,
                        }
                        for run in runs
                    ],
                }
                for index, (role, runs) in enumerate(groups)
            ],
        }

    def approve(self, payload):
        payload = copy.deepcopy(payload)
        payload["proposal_fingerprint"] = proposal_fingerprint(payload)
        return ApprovedShortlist.model_validate_json(json.dumps(payload))

    def build(self, approved, **values):
        return build_local_curation_plan(
            {"proposal_fingerprint": approved.proposal_fingerprint},
            principal=self.principal,
            approved_shortlist=approved,
            projects=self.projects,
            **values,
        )

    def one(self, **values):
        run = self.make_run(**values)
        payload = self.shortlist_payload([("coding_case", [run])])
        return run, payload, self.approve(payload)

    def test_three_coding_families_and_separate_control_preserve_pair_and_lineage(self):
        rejected = self.make_run(status="rejected", checks=[{"kind": "tests", "exit_code": 1}])
        accepted = self.make_run(
            task=rejected.task, retried_from=rejected, delegated_by=rejected, escalation_root=rejected
        )
        parent = Task.objects.create(project_slug="synthetic-project", title=PRIVATE)
        Task.objects.filter(pk=rejected.task_id).update(parent=parent)
        groups = [
            ("coding_case", [rejected, accepted]),
            ("coding_case", [self.make_run()]),
            ("coding_case", [self.make_run()]),
            ("smoke_control", [self.make_run(diff="")]),
        ]
        approved = self.approve(self.shortlist_payload(groups))
        with self.assertNumQueries(5):
            plan = self.build(approved)
        self.assertEqual((plan.coding_family_count, plan.smoke_control_count), (3, 1))
        first, second = plan.families[0].records
        self.assertEqual((first.status, first.approval), ("rejected", "not_approved"))
        self.assertEqual((second.status, second.approval), ("accepted", "verified"))
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(first.parent_task_id, parent.pk)
        self.assertEqual(
            (second.retried_from_run_id, second.delegated_by_run_id, second.escalation_root_run_id), (rejected.pk,) * 3
        )
        self.assertFalse(any(getattr(plan, flag) for flag in FLAGS))
        self.assertIsNone(plan.dataset_split)
        self.assertNotIn(PRIVATE, plan.model_dump_json())
        self.assertEqual(plan, LocalCurationPlan.model_validate_json(plan.model_dump_json()))
        self.assertEqual(plan, pickle.loads(pickle.dumps(plan)))
        with self.assertRaises(ValidationError):
            plan.benchmark_ready = True
        self.assertIsInstance(first.checks, tuple)
        self.assertIsInstance(first.reason_codes, tuple)
        with self.assertRaises(ValidationError):
            first.usage.input_tokens = 9

    def test_both_permissions_precede_parsing_database_filesystem(self):
        for grants in (set(), {"gamedev.evaluation.read"}, {"gamedev.evaluation.curate"}, {"gamedev.write"}):
            with (
                self.subTest(grants=grants),
                self.assertNumQueries(0),
                mock.patch(
                    "gamedev.hq_plugin.evaluation_curation.CurationSelection.model_validate",
                    side_effect=AssertionError("parse"),
                ),
                mock.patch("pathlib.Path.open", side_effect=AssertionError("filesystem")),
                self.assertRaises(AuthorizationError),
            ):
                build_local_curation_plan(
                    object(),
                    principal=Principal("denied", "cli", frozenset(grants)),
                    approved_shortlist=object(),
                    projects=object(),
                )

    def test_strict_selection_and_trusted_object_only_before_io(self):
        _, _, approved = self.one()
        for selection in (
            {},
            {"proposal_fingerprint": True},
            {"proposal_fingerprint": 1},
            {"proposal_fingerprint": "a" * 64, "run_ids": [1]},
            {"proposal_fingerprint": "a" * 64, "latest": True},
            {"proposal_fingerprint": "a" * 64, "projects": {}},
            {"proposal_fingerprint": "a" * 64, "records": []},
        ):
            with (
                self.subTest(selection=selection),
                self.assertNumQueries(0),
                self.assertRaisesRegex(CurationRefused, "^invalid_selection$"),
            ):
                build_local_curation_plan(
                    selection, principal=self.principal, approved_shortlist=approved, projects=self.projects
                )
        with self.assertNumQueries(0), self.assertRaisesRegex(CurationRefused, "^invalid_shortlist$"):
            build_local_curation_plan(
                {"proposal_fingerprint": approved.proposal_fingerprint},
                principal=self.principal,
                approved_shortlist=approved.model_dump(),
                projects=self.projects,
            )

    def test_shortlist_strict_versions_ids_limits_duplicates_and_unknown_fields(self):
        _, payload, _ = self.one()
        changes = []
        for value in (True, "1", 1.0, 0, -1, 2**63):
            changed = copy.deepcopy(payload)
            changed["families"][0]["runs"][0]["run_id"] = value
            changes.append(changed)
        changes.extend({**payload, "schema_version": version} for version in (True, "1", 1.0, 2))
        for target in ("root", "family", "run"):
            changed = copy.deepcopy(payload)
            node = changed if target == "root" else changed["families"][0]
            if target == "run":
                node = node["runs"][0]
            node["private"] = PRIVATE
            changes.append(changed)
        duplicate = copy.deepcopy(payload)
        duplicate["families"] *= 2
        changes.append(duplicate)
        duplicate_run = copy.deepcopy(payload)
        duplicate_run["families"][0]["runs"] *= 2
        changes.append(duplicate_run)
        changes.append({**payload, "families": []})
        for count, runs in ((9, 1), (1, 17), (2, 9)):
            changed = copy.deepcopy(payload)
            changed["families"] = [
                {
                    "family_id": f"family-{i}",
                    "role": "coding_case",
                    "runs": [{"run_id": i * runs + j + 1, "expected_fingerprint": "a" * 64} for j in range(runs)],
                }
                for i in range(count)
            ]
            changes.append(changed)
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaises(ValidationError):
                self.approve(changed)
        bad = {**payload, "proposal_fingerprint": "b" * 64}
        with self.assertRaises(ValidationError):
            ApprovedShortlist.model_validate_json(json.dumps(bad))

    def test_proposal_mismatch_record_drift_refusal_and_missing_run(self):
        run, payload, approved = self.one()
        with self.assertNumQueries(0), self.assertRaisesRegex(CurationRefused, "^proposal_fingerprint_mismatch$"):
            build_local_curation_plan(
                {"proposal_fingerprint": "b" * 64},
                principal=self.principal,
                approved_shortlist=approved,
                projects=self.projects,
            )
        bad = copy.deepcopy(payload)
        bad["families"][0]["runs"][0]["expected_fingerprint"] = "a" * 64
        with self.assertRaisesRegex(CurationRefused, "^record_fingerprint_mismatch$"):
            self.build(self.approve(bad))
        Run.objects.filter(pk=run.pk).update(selected_model="synthetic-model")
        with self.assertRaisesRegex(CurationRefused, "^record_fingerprint_mismatch$"):
            self.build(approved)
        Run.objects.filter(pk=run.pk).update(diff="changed without hash")
        with self.assertRaisesRegex(CurationRefused, "^evidence_refused$"):
            self.build(approved)
        bad["families"][0]["runs"][0]["run_id"] = 2**63 - 1
        with self.assertRaisesRegex(CurationRefused, "^evidence_refused$"):
            self.build(self.approve(bad))
        unsupported = approved.model_copy(update={"schema_version": 2})
        with self.assertNumQueries(0), self.assertRaisesRegex(CurationRefused, "^invalid_shortlist$"):
            self.build(unsupported)

    def test_mixed_tasks_task_reuse_and_empty_coding_refuse_whole_plan(self):
        first = self.make_run()
        second = self.make_run()
        same_task = self.make_run(task=first.task)
        for groups, code in (
            ([("coding_case", [first, second])], "mixed_task_family"),
            ([("coding_case", [first]), ("coding_case", [same_task])], "task_reused"),
            ([("coding_case", [self.make_run(diff="")])], "empty_coding_family"),
            ([("smoke_control", [first])], "nonempty_smoke_control"),
        ):
            approved = self.approve(self.shortlist_payload(groups))
            with self.subTest(code=code), self.assertRaisesRegex(CurationRefused, f"^{code}$"):
                self.build(approved)

    def test_incomplete_failed_capture_is_explicit(self):
        run, _, _ = self.one(status="failed", checks=[])
        Run.objects.filter(pk=run.pk).update(started_at=None, finished_at=None)
        approved = self.approve(self.shortlist_payload([("coding_case", [run])]))
        plan = self.build(approved)
        self.assertEqual(plan.evidence_summary.incomplete_records, 1)
        self.assertIn("checks_missing", plan.families[0].evidence_summary.reason_codes)
        self.assertEqual(plan.families[0].records[0].approval, "not_approved")
        self.assertFalse(plan.benchmark_ready)

    def test_output_flags_splits_unknown_fields_coercion_and_tamper_refused(self):
        _, _, approved = self.one()
        value = self.build(approved).model_dump(mode="json")
        for key, replacement in (
            *((flag, True) for flag in FLAGS),
            *((flag, 0) for flag in FLAGS),
            ("dataset_split", "holdout"),
            ("dataset_split", {}),
            ("grade", 1),
            ("schema_version", True),
            ("schema_version", 2),
            ("coding_family_count", True),
            ("proposal_fingerprint", "b" * 64),
        ):
            changed = {**value, key: replacement}
            changed["fingerprint"] = plan_fingerprint(changed)
            with self.subTest(key=key, replacement=replacement), self.assertRaises(ValidationError):
                LocalCurationPlan.model_validate_json(json.dumps(changed))
        changed = copy.deepcopy(value)
        changed["families"][0]["records"][0]["status"] = "failed"
        with self.assertRaises(ValidationError):
            LocalCurationPlan.model_validate_json(json.dumps(changed))
        for location in ("family", "summary", "record", "usage", "check"):
            changed = copy.deepcopy(value)
            family = changed["families"][0]
            record = family["records"][0]
            target = {
                "family": family,
                "summary": family["evidence_summary"],
                "record": record,
                "usage": record["usage"],
                "check": record["checks"][0],
            }[location]
            target["prompt"] = PRIVATE
            changed["fingerprint"] = plan_fingerprint(changed)
            with self.subTest(location=location), self.assertRaises(ValidationError):
                LocalCurationPlan.model_validate_json(json.dumps(changed))

    def test_maximum_explicit_roster_query_budget_and_no_effects(self):
        groups = [("coding_case", [self.make_run(), self.make_run()]) for _ in range(8)]
        # Each pair must represent one task; no project scan or graph traversal.
        for _, runs in groups:
            Run.objects.filter(pk=runs[1].pk).update(task=runs[0].task)
        approved = self.approve(self.shortlist_payload(groups))
        original_open = Path.open

        def local_read(path, mode="r", *args, **kwargs):
            self.assertEqual(mode, "rb")
            self.assertTrue(path.resolve().is_relative_to(self.root.resolve()))
            return original_open(path, mode, *args, **kwargs)

        before = list(Run.objects.values())
        queries = []

        def read_only(execute, sql, params, many, context):
            self.assertTrue(sql.lstrip().upper().startswith("SELECT"), sql)
            self.assertIn('"id" =', sql)
            queries.append(sql)
            return execute(sql, params, many, context)

        with (
            connection.execute_wrapper(read_only),
            mock.patch("pathlib.Path.open", local_read),
            mock.patch("os.getenv", side_effect=AssertionError("environment")),
            mock.patch("gamedev.hq_plugin.evaluation_curation.read_run_evidence", wraps=read_run_evidence) as reader,
            mock.patch("subprocess.Popen", side_effect=AssertionError("process")),
            mock.patch("socket.socket", side_effect=AssertionError("network")),
            mock.patch("gamedev.worker_config.load_policy", side_effect=AssertionError("policy")),
            mock.patch("pathlib.Path.write_text", side_effect=AssertionError("write")),
            mock.patch("pathlib.Path.write_bytes", side_effect=AssertionError("write")),
            mock.patch("gamedev.otlp.export_spans", side_effect=AssertionError("export")),
        ):
            plan = self.build(approved)
        self.assertEqual(len(queries), 16)
        self.assertEqual(reader.call_count, 16)
        self.assertEqual(
            [call.args[0]["run_id"] for call in reader.call_args_list],
            [run.run_id for family in approved.families for run in family.runs],
        )
        self.assertEqual(before, list(Run.objects.values()))
        raw = plan.model_dump_json()
        for secret in (PRIVATE, "synthetic-project", str(self.root), '"prompt"', '"output"', '"diff"', '"command"'):
            self.assertNotIn(secret, raw)

    def test_editor_evidence_cannot_be_a_coding_family(self):
        run = self.make_run(status="failed", checks=[{"kind": "uefn_inspect", "exit_code": 1}])
        Run.objects.filter(pk=run.pk).update(kind="uefn_inspect")
        approved = self.approve(self.shortlist_payload([("coding_case", [run])]))
        with self.assertRaisesRegex(CurationRefused, "^noncoding_family$"):
            self.build(approved)

    def test_summary_and_counts_refused_even_with_recomputed_fingerprint(self):
        _, _, approved = self.one()
        value = self.build(approved).model_dump(mode="json")
        for key in ("coding_family_count", "smoke_control_count"):
            changed = copy.deepcopy(value)
            changed[key] += 1
            changed["fingerprint"] = plan_fingerprint(changed)
            with self.subTest(key=key), self.assertRaises(ValidationError):
                LocalCurationPlan.model_validate_json(json.dumps(changed))
        changed = copy.deepcopy(value)
        changed["evidence_summary"]["incomplete_records"] += 1
        changed["fingerprint"] = plan_fingerprint(changed)
        with self.assertRaises(ValidationError):
            LocalCurationPlan.model_validate_json(json.dumps(changed))
