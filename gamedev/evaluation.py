"""Local declared-evidence grading, without worker/database/provider integration."""

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Literal, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_INPUT_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 2_097_152
MAX_CASES = 100
MAX_CONFIGS = 16
MAX_OBSERVATIONS = 1600
MAX_CHECKS = 8
MAX_CHILDREN = 8
SCHEMA_VERSION = 1
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")]
ModelName = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,179}$")]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
Text = Annotated[str, Field(min_length=1, max_length=300)]
Kind = Literal["source", "tests", "compilation"]
Status = Literal["pending", "completed", "failed", "accepted", "success"]
Verdict = Literal["pass", "fail", "incomplete", "invalid"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Check(Strict):
    kind: Kind
    passed: bool


Checks = Annotated[list[Check], Field(max_length=MAX_CHECKS)]


class Child(Strict):
    child_id: Identifier
    status: Status
    checks: Checks


class Recovery(Strict):
    completed: bool
    initial_checks: Checks
    checks: Checks


class Evidence(Strict):
    status: Status
    checks: Checks
    children: list[Child] = Field(max_length=MAX_CHILDREN)
    recovery: Recovery | None


class Criteria(Strict):
    required_kinds: list[Kind] = Field(min_length=1, max_length=3)
    pass_scope: Literal["declared_source", "declared_tests", "declared_compilation"]
    establishes: Literal["Declared required checks passed; no hidden correctness, editor or gameplay verification."]
    required_child_ids: list[Identifier] = Field(max_length=MAX_CHILDREN)
    recovery_required: bool


class Fixture(Strict):
    provenance: Literal["synthetic-harness-self-test"]
    evidence: Evidence
    expected_verdict: Verdict


class Case(Strict):
    case_id: Identifier
    version: Identifier
    scenario: Text
    criteria: Criteria
    fixture: Fixture


class Dataset(Strict):
    schema_version: int
    dataset_id: Identifier
    version: Identifier
    provenance: Literal["synthetic-harness-self-test"]
    cases: list[Case] = Field(min_length=1, max_length=MAX_CASES)


class Configuration(Strict):
    config_id: Identifier
    selected_model: ModelName | None = None


class Usage(Strict):
    input_tokens: int | None = Field(default=None, ge=0, le=10**12)
    output_tokens: int | None = Field(default=None, ge=0, le=10**12)


class Observation(Strict):
    dataset_id: Identifier
    dataset_version: Identifier
    case_id: Identifier
    case_version: Identifier
    case_fingerprint: Hash
    criteria_fingerprint: Hash
    config_id: Identifier
    hq_run_id: Identifier
    git_revision: Revision
    evidence_fingerprint: Hash
    evidence: Evidence
    actual_model: ModelName | None = None
    usage: Usage | None = None


class Submission(Strict):
    schema_version: int
    dataset_id: Identifier
    dataset_version: Identifier
    configurations: list[Configuration] = Field(min_length=1, max_length=MAX_CONFIGS)
    observations: list[Observation] = Field(max_length=MAX_OBSERVATIONS)


class EvaluationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def canonical_json(value):
    """Sorted object keys and compact ASCII JSON; array order is significant."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def case_fingerprint(dataset, case):
    return fingerprint(
        {"dataset_id": dataset.dataset_id, "dataset_version": dataset.version, "case": case.model_dump()}
    )


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError("duplicate_json_key")
        result[key] = value
    return result


def _nonfinite(_value):
    raise EvaluationError("nonfinite_number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise EvaluationError("nonfinite_number")
    return number


def parse_json(raw):
    if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_INPUT_BYTES:
        raise EvaluationError("input_too_large")
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite, parse_float=_finite_float)
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, EvaluationError):
            raise
        raise EvaluationError("malformed_json") from None


def _local_path(path):
    if str(path).startswith(("\\\\", "//")):
        raise EvaluationError("nonlocal_path")
    return path


def read_json(path):
    """Read only an operator-selected regular file, with a bounded read."""
    _local_path(path)
    _local_path(path.resolve())
    if not path.is_file():
        raise EvaluationError("input_not_regular_file")
    with path.open("rb") as stream:
        return parse_json(stream.read(MAX_INPUT_BYTES + 1))


def _unique(values, code):
    if len(values) != len(set(values)):
        raise EvaluationError(code)


def validate_dataset(value):
    dataset = Dataset.model_validate(value)
    if dataset.schema_version != SCHEMA_VERSION:
        raise EvaluationError("unsupported_schema_version")
    _unique([case.case_id for case in dataset.cases], "duplicate_case")
    scope_kind = {"declared_source": "source", "declared_tests": "tests", "declared_compilation": "compilation"}
    for case in dataset.cases:
        criteria = case.criteria
        _unique(criteria.required_kinds, "duplicate_required_kind")
        _unique(criteria.required_child_ids, "duplicate_required_child")
        if scope_kind[criteria.pass_scope] not in criteria.required_kinds:
            raise EvaluationError("scope_kind_mismatch")
    return dataset


def _check_issues(checks, required, prefix):
    kinds = [check.kind for check in checks]
    issues = []
    if len(kinds) != len(set(kinds)):
        issues.append(("invalid", prefix + "ambiguous_check_kind"))
    issues.extend(("incomplete", prefix + "missing_kind:" + kind) for kind in required if kind not in kinds)
    if any(not check.passed for check in checks):
        issues.append(("fail", prefix + "failed_check"))
    return issues


def _status_issues(status, issues, prefix):
    if status in ("accepted", "success") and issues:
        issues.append(("invalid", prefix + "contradicted_success"))
    if status == "pending":
        issues.append(("incomplete", prefix + "pending"))
    if status == "failed":
        issues.append(("fail", prefix + "failed_status"))
    return issues


def _children_issues(criteria, evidence):
    ids = [child.child_id for child in evidence.children]
    issues = []
    if len(ids) != len(set(ids)):
        issues.append(("invalid", "ambiguous_child"))
    issues.extend(
        ("incomplete", "missing_child:" + child_id) for child_id in criteria.required_child_ids if child_id not in ids
    )
    for child in evidence.children:
        prefix = "child:" + child.child_id + ":"
        child_issues = _check_issues(child.checks, criteria.required_kinds, prefix)
        issues.extend(_status_issues(child.status, child_issues, prefix))
    return issues


def _recovery_issues(criteria, recovery):
    if recovery is None:
        return [("incomplete", "missing_recovery")] if criteria.recovery_required else []
    issues = _check_issues(recovery.checks, criteria.required_kinds, "recovery:")
    initial = _check_issues(recovery.initial_checks, criteria.required_kinds, "recovery_initial:")
    issues.extend(issue for issue in initial if issue[0] != "fail")
    if not any(not check.passed for check in recovery.initial_checks):
        issues.append(("invalid", "recovery_without_initial_failure"))
    if not recovery.completed:
        issues.append(("incomplete", "recovery_incomplete"))
    elif issues:
        issues.append(("invalid", "contradicted_recovery_completion"))
    return issues


def _verdict(issues):
    for verdict in ("invalid", "fail", "incomplete"):
        if any(issue[0] == verdict for issue in issues):
            return verdict
    return "pass"


def grade(case, evidence):
    """Grade declarations only. A pass never asserts gameplay verification."""
    issues = _check_issues(evidence.checks, case.criteria.required_kinds, "")
    issues.extend(_children_issues(case.criteria, evidence))
    issues.extend(_recovery_issues(case.criteria, evidence.recovery))
    _status_issues(evidence.status, issues, "")
    verdict = _verdict(issues)
    return {
        "verdict": verdict,
        "completed": not any(level in ("invalid", "incomplete") for level, _ in issues),
        "reason_codes": sorted({code for _, code in issues}),
        "pass_scope": case.criteria.pass_scope if verdict == "pass" else None,
        "establishes": case.criteria.establishes if verdict == "pass" else None,
        "gameplay_verified": False,
    }


def _validate_submission(dataset, submission):
    if submission.schema_version != SCHEMA_VERSION:
        raise EvaluationError("unsupported_schema_version")
    if (submission.dataset_id, submission.dataset_version) != (dataset.dataset_id, dataset.version):
        raise EvaluationError("incompatible_dataset_version")
    config_ids = [config.config_id for config in submission.configurations]
    _unique(config_ids, "duplicate_config")
    _unique([(item.config_id, item.case_id) for item in submission.observations], "duplicate_case_config")
    cases = {case.case_id: case for case in dataset.cases}
    for item in submission.observations:
        if item.config_id not in config_ids:
            raise EvaluationError("unknown_config")
        if item.case_id not in cases:
            raise EvaluationError("unknown_case")
        _validate_binding(dataset, cases[item.case_id], item)


def _validate_binding(dataset, case, item):
    if (item.dataset_id, item.dataset_version) != (dataset.dataset_id, dataset.version):
        raise EvaluationError("incompatible_dataset_version")
    if item.case_version != case.version:
        raise EvaluationError("incompatible_case_version")
    if item.case_fingerprint != case_fingerprint(dataset, case):
        raise EvaluationError("case_fingerprint_mismatch")
    if item.criteria_fingerprint != fingerprint(case.criteria):
        raise EvaluationError("criteria_fingerprint_mismatch")
    if item.evidence_fingerprint != fingerprint(item.evidence):
        raise EvaluationError("evidence_fingerprint_mismatch")


def _case_report(dataset, case, observation):
    result = (
        grade(case, observation.evidence)
        if observation
        else {
            "verdict": "incomplete",
            "reason_codes": ["missing_observation"],
            "completed": False,
            "pass_scope": None,
            "establishes": None,
            "gameplay_verified": False,
        }
    )
    return {
        "case_id": case.case_id,
        "case_version": case.version,
        "case_fingerprint": case_fingerprint(dataset, case),
        "criteria_fingerprint": fingerprint(case.criteria),
        **result,
        "hq_run_id": observation.hq_run_id if observation else None,
        "git_revision": observation.git_revision if observation else None,
        "evidence_fingerprint": observation.evidence_fingerprint if observation else None,
        "actual_model": observation.actual_model if observation else None,
        "usage": observation.usage.model_dump() if observation and observation.usage else None,
    }


def _configuration_report(dataset, config, observations):
    rows = [
        _case_report(dataset, case, observations.get((config.config_id, case.case_id)))
        for case in sorted(dataset.cases, key=lambda item: item.case_id)
    ]
    known_usage = {
        name: sum(row["usage"] is not None and row["usage"][name] is not None for row in rows)
        for name in Usage.model_fields
    }
    return {
        "config_id": config.config_id,
        "selected_model": config.selected_model,
        "expected_count": len(rows),
        "observed_count": sum(row["hq_run_id"] is not None for row in rows),
        "completion_count": sum(row["completed"] for row in rows),
        "verified_pass_count": sum(row["verdict"] == "pass" for row in rows),
        "known_usage_coverage": known_usage,
        "cases": rows,
    }


def evaluate(dataset_value, submission_value):
    """Refuse invalid/incompatible submissions as a whole; never compare subsets."""
    try:
        dataset = validate_dataset(dataset_value)
        submission = Submission.model_validate(submission_value)
        _validate_submission(dataset, submission)
    except EvaluationError as exc:
        return {"verdict": "invalid", "reason_codes": [exc.code], "configurations": []}
    except ValidationError, ValueError, RecursionError:
        return {"verdict": "invalid", "reason_codes": ["schema_invalid"], "configurations": []}
    observations = {(item.config_id, item.case_id): item for item in submission.observations}
    reports = [
        _configuration_report(dataset, config, observations)
        for config in sorted(submission.configurations, key=lambda item: item.config_id)
    ]
    issues = [(row["verdict"], code) for report in reports for row in report["cases"] for code in row["reason_codes"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "provenance": dataset.provenance,
        "verdict": _verdict(issues),
        "reason_codes": sorted({code for _, code in issues}),
        "configurations": reports,
    }


class _Parser(argparse.ArgumentParser):
    @override
    def error(self, message):
        raise EvaluationError("invalid_arguments")


def _write_output(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    try:
        args = parser.parse_args(argv)
        for path in (args.dataset, args.observations, args.output):
            if path is not None:
                _local_path(path)
                _local_path(path.resolve())
        if args.output and args.output.resolve() in (args.dataset.resolve(), args.observations.resolve()):
            raise EvaluationError("output_overwrites_input")
        report = evaluate(read_json(args.dataset), read_json(args.observations))
        payload = canonical_json(report) + "\n"
        if len(payload.encode()) > MAX_OUTPUT_BYTES:
            raise EvaluationError("output_too_large")
        if args.output:
            _write_output(args.output, payload)
    except (EvaluationError, OSError) as exc:
        code = exc.code if isinstance(exc, EvaluationError) else "local_io_error"
        report = {"verdict": "invalid", "reason_codes": [code], "configurations": []}
        payload = canonical_json(report) + "\n"
    sys.stdout.write(payload)
    return {"pass": 0, "fail": 1, "invalid": 2, "incomplete": 3}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
