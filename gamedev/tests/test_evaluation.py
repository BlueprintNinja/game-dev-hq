"""Synthetic harness tests, never a model comparison or quality benchmark."""

import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pydantic import ValidationError

from gamedev import evaluation as ev

DATASET_PATH = Path(__file__).resolve().parents[1] / "evaluation_cases.json"


def observation(dataset, case):
    evidence = case.fixture.evidence
    return {
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "case_id": case.case_id,
        "case_version": case.version,
        "case_fingerprint": ev.case_fingerprint(dataset, case),
        "criteria_fingerprint": ev.fingerprint(case.criteria),
        "config_id": "synthetic-config-a",
        "hq_run_id": "synthetic-run-1",
        "git_revision": "a" * 40,
        "evidence_fingerprint": ev.fingerprint(evidence),
        "evidence": evidence.model_dump(),
    }


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.raw = ev.read_json(DATASET_PATH)
        self.dataset = ev.validate_dataset(self.raw)
        self.case = self.dataset.cases[0]
        self.item = observation(self.dataset, self.case)
        self.submission = {
            "schema_version": 1,
            "dataset_id": self.dataset.dataset_id,
            "dataset_version": self.dataset.version,
            "configurations": [{"config_id": "synthetic-config-a"}],
            "observations": [self.item],
        }

    def evaluate(self):
        return ev.evaluate(self.raw, self.submission)

    def single(self):
        self.raw["cases"] = [self.raw["cases"][0]]

    def refresh(self):
        self.item["evidence_fingerprint"] = ev.fingerprint(ev.Evidence.model_validate(self.item["evidence"]))

    def test_all_ten_synthetic_cases_and_all_verdicts(self):
        self.assertEqual(len(self.dataset.cases), 10)
        verdicts = set()
        for case in self.dataset.cases:
            with self.subTest(case=case.case_id):
                result = ev.grade(case, case.fixture.evidence)
                self.assertEqual(result["verdict"], case.fixture.expected_verdict)
                self.assertFalse(result["gameplay_verified"])
                verdicts.add(result["verdict"])
        self.assertEqual(verdicts, {"pass", "fail", "incomplete", "invalid"})
        self.submission["observations"] = [observation(self.dataset, case) for case in self.dataset.cases]
        report = self.evaluate()["configurations"][0]
        self.assertEqual(report["observed_count"], 10)
        self.assertEqual(report["verified_pass_count"], 4)

    def test_full_coverage_is_required_for_each_configuration(self):
        self.submission["configurations"].append({"config_id": "synthetic-config-b"})
        result = self.evaluate()
        self.assertEqual(result["verdict"], "incomplete")
        first, second = result["configurations"]
        self.assertEqual((first["expected_count"], first["observed_count"], first["completion_count"]), (10, 1, 1))
        self.assertEqual((second["expected_count"], second["observed_count"]), (10, 0))
        self.assertEqual(len(second["cases"]), 10)

    def test_missing_check_cannot_pass(self):
        self.single()
        self.item["evidence"]["checks"].pop()
        self.refresh()
        result = self.evaluate()
        self.assertEqual(result["verdict"], "incomplete")
        self.assertIn("missing_kind:tests", result["reason_codes"])

    def test_false_and_duplicate_checks_and_contradicted_claims(self):
        self.single()
        self.item["evidence"]["checks"][0]["passed"] = False
        self.refresh()
        self.assertEqual(self.evaluate()["verdict"], "fail")
        self.item["evidence"]["status"] = "accepted"
        self.refresh()
        self.assertIn("contradicted_success", self.evaluate()["reason_codes"])
        self.item["evidence"]["status"] = "completed"
        self.item["evidence"]["checks"].append(self.item["evidence"]["checks"][0].copy())
        self.refresh()
        self.assertEqual(self.evaluate()["verdict"], "invalid")

    def test_duplicate_observations_and_unknown_cases_refuse_comparison(self):
        self.submission["observations"].append(copy.deepcopy(self.item))
        self.assertEqual(self.evaluate()["reason_codes"], ["duplicate_case_config"])
        self.submission["observations"].pop()
        self.item["case_id"] = "unknown"
        result = self.evaluate()
        self.assertEqual(result["configurations"], [])
        self.assertEqual(result["reason_codes"], ["unknown_case"])

    def test_all_bindings_must_match(self):
        for field, value, code in (
            ("dataset_version", "2", "incompatible_dataset_version"),
            ("case_version", "2", "incompatible_case_version"),
            ("case_fingerprint", "b" * 64, "case_fingerprint_mismatch"),
            ("criteria_fingerprint", "b" * 64, "criteria_fingerprint_mismatch"),
            ("evidence_fingerprint", "b" * 64, "evidence_fingerprint_mismatch"),
        ):
            with self.subTest(field=field):
                original = self.item[field]
                self.item[field] = value
                self.assertEqual(self.evaluate()["reason_codes"], [code])
                self.item[field] = original
        self.submission["dataset_version"] = "2"
        self.assertEqual(self.evaluate()["configurations"], [])

    def test_changed_criteria_without_version_bump_is_rejected(self):
        self.raw["cases"][0]["criteria"]["recovery_required"] = True
        self.assertEqual(self.evaluate()["reason_codes"], ["case_fingerprint_mismatch"])

    def test_models_are_distinct_and_missing_usage_is_unknown(self):
        self.single()
        self.submission["configurations"][0]["selected_model"] = "synthetic-selected"
        report = self.evaluate()["configurations"][0]
        self.assertIsNone(report["cases"][0]["actual_model"])
        self.assertIsNone(report["cases"][0]["usage"])
        self.assertEqual(report["known_usage_coverage"]["input_tokens"], 0)
        self.item["actual_model"] = "synthetic-actual"
        self.item["usage"] = {"input_tokens": 0}
        report = self.evaluate()["configurations"][0]
        self.assertEqual(report["cases"][0]["usage"], {"input_tokens": 0, "output_tokens": None})
        self.assertEqual(report["known_usage_coverage"], {"input_tokens": 1, "output_tokens": 0})
        self.assertNotEqual(report["selected_model"], report["cases"][0]["actual_model"])

    def test_required_child_must_finish_and_pass(self):
        case = self.dataset.cases[6]
        raw = case.fixture.evidence.model_dump()
        raw["children"][0]["status"] = "pending"
        self.assertEqual(ev.grade(case, ev.Evidence.model_validate(raw))["verdict"], "incomplete")
        raw["status"] = "success"
        self.assertEqual(ev.grade(case, ev.Evidence.model_validate(raw))["verdict"], "invalid")
        raw["status"] = "completed"
        raw["children"][0]["status"] = "failed"
        self.assertEqual(ev.grade(case, ev.Evidence.model_validate(raw))["verdict"], "fail")
        raw["children"].append(raw["children"][0].copy())
        self.assertEqual(ev.grade(case, ev.Evidence.model_validate(raw))["verdict"], "invalid")

    def test_recovery_requires_initial_failure_and_completed_final_evidence(self):
        case = self.dataset.cases[9]
        for modification, expected in (
            ("missing", "invalid"),
            ("pending", "invalid"),
            ("no-failure", "invalid"),
            ("failed-final", "invalid"),
        ):
            with self.subTest(modification=modification):
                raw = case.fixture.evidence.model_dump()
                if modification == "missing":
                    raw["recovery"] = None
                elif modification == "pending":
                    raw["recovery"]["completed"] = False
                elif modification == "no-failure":
                    raw["recovery"]["initial_checks"][0]["passed"] = True
                else:
                    raw["recovery"]["checks"][0]["passed"] = False
                self.assertEqual(ev.grade(case, ev.Evidence.model_validate(raw))["verdict"], expected)

    def test_compilation_and_source_never_establish_gameplay(self):
        for case in self.dataset.cases[:2]:
            result = ev.grade(case, case.fixture.evidence)
            self.assertFalse(result["gameplay_verified"])
            self.assertIn("no hidden correctness, editor or gameplay", result["establishes"])
        raw = self.dataset.cases[1].model_dump()
        raw["criteria"]["pass_scope"] = "gameplay_verified"
        with self.assertRaises(ValidationError):
            ev.Case.model_validate(raw)

    def test_strict_unknown_fields_and_types(self):
        for field, value in (
            ("prompt", "malicious text"),
            ("git_revision", "HEAD"),
            ("evidence_fingerprint", "xyz"),
            ("hq_run_id", 1),
        ):
            with self.subTest(field=field):
                submission = copy.deepcopy(self.submission)
                submission["observations"][0][field] = value
                self.assertEqual(ev.evaluate(self.raw, submission)["verdict"], "invalid")
        for value in (0, 1, "true", None):
            with self.subTest(passed=value):
                self.item["evidence"]["checks"][0]["passed"] = value
                self.assertEqual(self.evaluate()["verdict"], "invalid")
        self.item["evidence"]["checks"][0]["passed"] = True
        for value in (True, 1.0, "1", -1, float("inf")):
            self.item["usage"] = {"input_tokens": value}
            self.assertEqual(self.evaluate()["verdict"], "invalid")
        self.item.pop("usage")
        self.submission["schema_version"] = True
        self.assertEqual(self.evaluate()["verdict"], "invalid")

    def test_nested_unknown_fields_are_rejected(self):
        for location in (
            self.raw,
            self.raw["cases"][0],
            self.raw["cases"][0]["criteria"],
            self.item["evidence"],
            self.item["evidence"]["checks"][0],
        ):
            location["execute"] = "never-run"
            self.assertEqual(self.evaluate()["verdict"], "invalid")
            location.pop("execute")

    def test_strict_json(self):
        for raw, code in (
            (b'{"x":1,"x":2}', "duplicate_json_key"),
            (b'{"x":NaN}', "nonfinite_number"),
            (b"[Infinity]", "nonfinite_number"),
            (b"{", "malformed_json"),
            (b'"\xff"', "malformed_json"),
        ):
            with self.subTest(raw=raw), self.assertRaises(ev.EvaluationError) as error:
                ev.parse_json(raw)
            self.assertEqual(error.exception.code, code)

    def test_input_and_collection_limits(self):
        with self.assertRaises(ev.EvaluationError):
            ev.parse_json(b" " * (ev.MAX_INPUT_BYTES + 1))
        for field, limit in (("cases", ev.MAX_CASES),):
            raw = copy.deepcopy(self.raw)
            raw[field] = [raw[field][0]] * (limit + 1)
            self.assertEqual(ev.evaluate(raw, self.submission)["verdict"], "invalid")
        for field, limit in (("configurations", ev.MAX_CONFIGS), ("observations", ev.MAX_OBSERVATIONS)):
            submission = copy.deepcopy(self.submission)
            submission[field] = [submission[field][0]] * (limit + 1)
            self.assertEqual(ev.evaluate(self.raw, submission)["verdict"], "invalid")
        for field, values in (
            ("checks", [{"kind": "tests", "passed": True}] * (ev.MAX_CHECKS + 1)),
            ("children", [{"child_id": "x", "status": "completed", "checks": []}] * (ev.MAX_CHILDREN + 1)),
        ):
            raw = self.case.fixture.evidence.model_dump()
            raw[field] = values
            with self.assertRaises(ValidationError):
                ev.Evidence.model_validate(raw)

    def test_stable_canonical_fingerprints(self):
        self.assertEqual(ev.canonical_json({"z": 1, "a": True}), '{"a":true,"z":1}')
        self.assertEqual(ev.fingerprint({"z": 1, "a": True}), hashlib.sha256(b'{"a":true,"z":1}').hexdigest())
        reverse = dict(reversed(list(self.case.model_dump().items())))
        self.assertEqual(ev.fingerprint(self.case), ev.fingerprint(reverse))
        before = ev.case_fingerprint(self.dataset, self.case)
        self.case.version = "2"
        self.assertNotEqual(before, ev.case_fingerprint(self.dataset, self.case))

    def test_duplicate_dataset_config_and_unknown_config(self):
        self.raw["cases"].append(copy.deepcopy(self.raw["cases"][0]))
        self.assertEqual(self.evaluate()["reason_codes"], ["duplicate_case"])
        self.raw["cases"].pop()
        self.submission["configurations"] *= 2
        self.assertEqual(self.evaluate()["reason_codes"], ["duplicate_config"])
        self.submission["configurations"].pop()
        self.item["config_id"] = "unknown"
        self.assertEqual(self.evaluate()["reason_codes"], ["unknown_config"])

    def cli(self, dataset_path, observations_path, output=None):
        args = ["--dataset", str(dataset_path), "--observations", str(observations_path)]
        if output:
            args.extend(["--output", str(output)])
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = ev.main(args)
        return code, json.loads(stream.getvalue()), stream.getvalue()

    def test_cli_all_exit_codes_atomic_output_and_input_protection(self):
        self.single()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset, observations, output = [
                root / name for name in ("dataset.json", "observations.json", "report.json")
            ]
            dataset.write_text(ev.canonical_json(self.raw))
            for status, passed, expected in (("completed", True, 0), ("failed", False, 1), ("pending", True, 3)):
                self.item["evidence"]["status"] = status
                self.item["evidence"]["checks"][0]["passed"] = passed
                self.refresh()
                observations.write_text(ev.canonical_json(self.submission))
                code, _, payload = self.cli(dataset, observations, output)
                self.assertEqual(code, expected)
                self.assertEqual(output.read_text(), payload)
                self.assertEqual(len(list(root.iterdir())), 3)
            observations.write_text('{"bad":NaN}')
            self.assertEqual(self.cli(dataset, observations)[0], 2)
            self.assertEqual(self.cli(dataset, observations, dataset)[1]["reason_codes"], ["output_overwrites_input"])
            self.assertEqual(self.cli(dataset, root / "absent")[0], 2)
            observations.write_bytes(b" " * (ev.MAX_INPUT_BYTES + 1))
            self.assertEqual(self.cli(dataset, observations)[1]["reason_codes"], ["input_too_large"])

    def test_cli_module_process_is_database_independent(self):
        self.single()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset, observations = root / "dataset.json", root / "observations.json"
            dataset.write_text(ev.canonical_json(self.raw))
            observations.write_text(ev.canonical_json(self.submission))
            environment = {key: value for key, value in os.environ.items() if key != "DJANGO_SETTINGS_MODULE"}
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "gamedev.evaluation",
                    "--dataset",
                    str(dataset),
                    "--observations",
                    str(observations),
                ],
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["verdict"], "pass")
            self.assertEqual(result.stderr, "")

    def test_output_bound_and_bad_arguments(self):
        with mock.patch.object(ev, "MAX_OUTPUT_BYTES", 1), tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.json"
            path.write_text(ev.canonical_json(self.submission))
            self.assertEqual(self.cli(DATASET_PATH, path)[1]["reason_codes"], ["output_too_large"])
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(ev.main(["--execute", "anything"]), 2)
        self.assertEqual(json.loads(stream.getvalue())["reason_codes"], ["invalid_arguments"])

    def test_nonfinite_overflow_and_nonlocal_paths_are_rejected(self):
        for raw in (b"[1e999]", b"[-1e999]", b"[NaN]", b"[-Infinity]"):
            with self.subTest(raw=raw), self.assertRaises(ev.EvaluationError) as error:
                ev.parse_json(raw)
            self.assertEqual(error.exception.code, "nonfinite_number")
        for path in (Path("//synthetic.invalid/input.json"), Path(r"\\synthetic.invalid\input.json")):
            with self.subTest(path=path), self.assertRaises(ev.EvaluationError) as error:
                ev.read_json(path)
            self.assertEqual(error.exception.code, "nonlocal_path")

    def test_incomplete_failure_is_not_counted_as_completed(self):
        self.single()
        self.item["evidence"]["checks"] = [{"kind": "source", "passed": False}]
        self.refresh()
        report = self.evaluate()["configurations"][0]
        self.assertEqual(report["cases"][0]["verdict"], "fail")
        self.assertFalse(report["cases"][0]["completed"])
        self.assertEqual(report["completion_count"], 0)

    def test_exact_byte_and_usage_boundaries(self):
        padding = b" " * (ev.MAX_INPUT_BYTES - 2)
        self.assertEqual(ev.parse_json(b"{}" + padding), {})
        usage = ev.Usage.model_validate({"input_tokens": 10**12, "output_tokens": 0})
        self.assertEqual(usage.input_tokens, 10**12)
        with self.assertRaises(ValidationError):
            ev.Usage.model_validate({"input_tokens": 10**12 + 1})
        self.raw["schema_version"] = 2
        self.assertEqual(self.evaluate()["reason_codes"], ["unsupported_schema_version"])

    def test_unknown_json_field_cannot_execute_or_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observations = root / "observations.json"
            marker = root / "must-not-exist"
            submission = copy.deepcopy(self.submission)
            submission["execute"] = "create " + str(marker)
            observations.write_text(ev.canonical_json(submission))
            code, report, payload = self.cli(DATASET_PATH, observations)
            self.assertEqual(code, 2)
            self.assertEqual(report["reason_codes"], ["schema_invalid"])
            self.assertNotIn(str(marker), payload)
            self.assertFalse(marker.exists())

    def test_report_determinism_ignores_submission_order(self):
        self.submission["observations"] = [observation(self.dataset, case) for case in self.dataset.cases]
        self.submission["configurations"].append({"config_id": "synthetic-config-b"})
        before = ev.canonical_json(self.evaluate())
        self.submission["observations"].reverse()
        self.submission["configurations"].reverse()
        self.assertEqual(before, ev.canonical_json(self.evaluate()))


class ProviderModelNameTests(unittest.TestCase):
    def test_provider_qualified_model_names_remain_separate_from_config_ids(self):
        model = "synthetic-provider/synthetic-model"
        config = ev.Configuration(config_id="delegated-config", selected_model=model)
        self.assertEqual(config.selected_model, model)
        for invalid in ("bad model", "model\ncommand", "x" * 181):
            with self.assertRaises(ValidationError):
                ev.Configuration(config_id="config-a", selected_model=invalid)
        with self.assertRaises(ValidationError):
            ev.Configuration(config_id="provider/config", selected_model=model)
