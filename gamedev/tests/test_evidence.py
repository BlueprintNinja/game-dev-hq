"""The local policy, and what a run's evidence has to show."""

import sys
import tempfile
import unittest
from pathlib import Path

from gamedev import evidence
from gamedev.worker_config import ProjectPolicy, WorkerPolicy, load_policy

CHECK = [sys.executable, "-m", "unittest", "discover"]
OTHER = [sys.executable, "tools/verse_source_check.py"]


def policy(**values):
    base = {"repository": str(Path(tempfile.gettempdir()) / "Island"), "checks": [CHECK]}
    base.update(values)
    return ProjectPolicy.model_validate(base)


class PolicyTests(unittest.TestCase):
    def test_a_bare_command_is_a_check(self):
        project = policy()
        self.assertEqual(project.checks[0].kind, "check")
        self.assertEqual(project.checks[0].command, CHECK)

    def test_a_check_may_name_the_kind_of_evidence_it_gives(self):
        project = policy(checks=[{"kind": "source", "command": OTHER}])
        self.assertEqual(project.checks[0].kind, "source")

    def test_a_check_executable_must_be_an_absolute_path(self):
        with self.assertRaises(ValueError):
            policy(checks=[["python", "-V"]])

    def test_a_check_may_not_be_empty(self):
        with self.assertRaises(ValueError):
            policy(checks=[[]])

    def test_a_project_needs_at_least_one_check(self):
        with self.assertRaises(ValueError):
            policy(checks=[])

    def test_an_apply_scope_must_be_relative(self):
        with self.assertRaises(ValueError):
            policy(apply_scope=["/etc/passwd"])
        with self.assertRaises(ValueError):
            policy(apply_scope=["../outside"])

    def test_a_destination_must_be_an_absolute_path(self):
        with self.assertRaises(ValueError):
            policy(apply_destination="island")

    def test_a_policy_file_holds_no_command_from_a_web_request(self):
        source = WorkerPolicy.model_validate(
            {
                "codex": sys.executable,
                "artifacts": str(Path(tempfile.gettempdir()) / "Runs"),
                "projects": {"island": {"repository": str(Path(tempfile.gettempdir()) / "Island"), "checks": [CHECK]}},
            }
        )
        self.assertEqual(source.projects["island"].checks[0].command, CHECK)

    def test_the_policy_is_read_from_the_file_the_worker_names(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker-policy.json"
            path.write_text(
                json.dumps(
                    {
                        "codex": sys.executable,
                        "artifacts": str(Path(tempfile.gettempdir()) / "Runs"),
                        "projects": {
                            "island": {"repository": str(Path(tempfile.gettempdir()) / "Island"), "checks": [CHECK]}
                        },
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_policy(path)
        self.assertIn("island", loaded.projects)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.project = policy(required_kinds=["source", "tests"])

    def test_every_check_must_pass(self):
        checks = [
            {"kind": "source", "command": OTHER, "exit_code": 0, "output": ""},
            {"kind": "tests", "command": CHECK, "exit_code": 1, "output": "failed"},
        ]
        self.assertFalse(evidence.passes(self.project, checks))

    def test_every_required_kind_must_be_present(self):
        checks = [{"kind": "source", "command": OTHER, "exit_code": 0, "output": ""}]
        self.assertFalse(evidence.passes(self.project, checks))
        self.assertEqual(evidence.missing_kinds(self.project, checks), ["tests"])

    def test_the_required_kinds_must_all_pass(self):
        checks = [
            {"kind": "source", "command": OTHER, "exit_code": 0, "output": ""},
            {"kind": "tests", "command": CHECK, "exit_code": 0, "output": ""},
        ]
        self.assertTrue(evidence.passes(self.project, checks))

    def test_no_evidence_is_not_enough(self):
        self.assertFalse(evidence.passes(self.project, []))

    def test_a_digest_names_the_exact_evidence(self):
        checks = [{"kind": "source", "command": OTHER, "exit_code": 0, "output": "ok"}]
        self.assertEqual(evidence.digest(checks), evidence.digest(list(checks)))
        changed = [{"kind": "source", "command": OTHER, "exit_code": 0, "output": "different"}]
        self.assertNotEqual(evidence.digest(checks), evidence.digest(changed))

    def test_a_digest_names_the_kind_of_evidence_too(self):
        first = [{"kind": "source", "command": OTHER, "exit_code": 0, "output": ""}]
        second = [{"kind": "tests", "command": OTHER, "exit_code": 0, "output": ""}]
        self.assertNotEqual(evidence.digest(first), evidence.digest(second))


if __name__ == "__main__":
    unittest.main()
