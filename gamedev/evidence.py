"""What a run's evidence is, and whether it is enough.

This module takes only the standard library, so the companion's own tests can
read it without installing anything.
"""

import hashlib
import json


def digest(checks):
    """A fingerprint of the captured check results.

    An approval names this fingerprint, so a decision can be tied to the
    exact evidence that was captured and not to whatever the run holds
    later.
    """
    payload = json.dumps(checks, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def passes(project, checks):
    """Whether the evidence is enough for the task's project.

    Every check must pass, and every kind the project requires must be
    present: a project that asks for source checks and tests is not
    satisfied by one of them alone.
    """
    if not checks:
        return False
    if any(check.get("exit_code") != 0 for check in checks):
        return False
    present = {check.get("kind", "check") for check in checks}
    return all(kind in present for kind in project.required_kinds)


def missing_kinds(project, checks):
    """The kinds the project requires and the evidence does not carry."""
    present = {check.get("kind", "check") for check in checks}
    return [kind for kind in project.required_kinds if kind not in present]
