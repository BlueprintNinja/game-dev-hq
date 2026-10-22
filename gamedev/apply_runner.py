"""The worker's side of an apply and of a rollback.

An apply writes an approved change into a destination the operator named in
the local policy. It is a separate operation from the approval: the
approval says the changes are good, and the apply is what writes them.

Before anything is written the runner checks that

- the run being applied was accepted, and its approval names the changes
  this run holds now;
- the change touches no path outside the operator's scope;
- no path leaves the destination and no path is a link;
- the change removes nothing;
- every destination file still holds what it held at the base revision.

Only then is what an apply would replace copied aside, so it can be put
back.
"""

import hashlib
import json
from pathlib import Path

from . import apply as apply_module
from .gitcmd import git, git_bytes
from .supervisor import ProcessFailure


def _base_content(worktree, relative):
    """The bytes a file held at the base revision, or None when it was new."""
    status, data = git_bytes(worktree, "show", f"HEAD:{relative}")
    if status:
        return None
    return data


def _file_content(path):
    """The bytes of a file on disk, or None when it does not exist."""
    if not Path(path).is_file():
        return None
    return Path(path).read_bytes()


def _current_diff(worktree):
    """The changes the retained worktree holds now, exactly as they were captured."""
    git(worktree, "add", "--intent-to-add", "--all")
    return git(worktree, "diff", "--no-ext-diff", "--binary", "HEAD")


def _approved_paths(diff):
    """The paths an approved change writes, and whether it removes anything."""
    entries = apply_module.changed_paths(diff)
    removed = [path for path, was_removed in entries if was_removed]
    if removed:
        raise ProcessFailure(f"The change removes {removed[0]}; an apply does not delete.")
    return [path for path, _was_removed in entries]


def _destination(project):
    if project.apply_destination is None:
        raise ProcessFailure("This project has no apply destination in the local policy.")
    destination = Path(project.apply_destination)
    if not destination.is_dir():
        raise ProcessFailure(f"The apply destination {destination} does not exist.")
    return destination


def execute_apply(run, policy, project):
    """Write an approved change into the destination, and say what it wrote."""
    source = run.applies
    if source is None:
        raise ProcessFailure("This run applies nothing.")
    if source.status != "accepted" or not source.passed:
        raise ProcessFailure("Only an accepted run with passing evidence may be applied.")
    if not source.approved_diff_sha256 or source.approved_diff_sha256 != source.diff_sha256:
        raise ProcessFailure("The approval does not name this run's changes.")
    if not source.worktree:
        raise ProcessFailure("The approved worktree is no longer there.")
    worktree = Path(source.worktree)
    if not worktree.is_dir():
        raise ProcessFailure("The approved worktree is no longer there.")
    destination = _destination(project)
    if not project.apply_scope:
        raise ProcessFailure("This project has no apply scope in the local policy.")

    diff = _current_diff(worktree)
    if hashlib.sha256(diff.encode()).hexdigest() != source.approved_diff_sha256:
        raise ProcessFailure("The approved changes changed since they were approved. Apply refused.")
    paths = _approved_paths(diff)
    if not paths:
        raise ProcessFailure("The approved change touches no file.")

    try:
        changes = apply_module.plan(
            destination,
            worktree,
            paths,
            project.apply_scope,
            lambda relative: _base_content(worktree, relative),
            _file_content,
        )
    except apply_module.Refusal as refusal:
        raise ProcessFailure(str(refusal)) from refusal
    directory = Path(policy.artifacts) / f"apply-{run.pk}"
    manifest = apply_module.snapshot(destination, changes, directory / "snapshot")
    apply_module.save_manifest(directory, manifest)
    written = apply_module.write(destination, changes)
    report = {
        "applied_from_run": source.pk,
        "destination": str(destination),
        "base_revision": source.base_revision,
        "files": manifest,
        "written": written,
        "snapshot": str(directory / "snapshot"),
    }
    return json.dumps(report, indent=2, sort_keys=True)


def execute_rollback(run, policy, project):
    """Put back what an apply replaced."""
    applied = run.applies
    if applied is None:
        raise ProcessFailure("This run rolls nothing back.")
    if applied.kind != "apply" or applied.status != "accepted":
        raise ProcessFailure("Only an apply that succeeded may be rolled back.")
    if applied.rolled_back_at:
        raise ProcessFailure("That apply has already been rolled back.")
    if not applied.snapshot:
        raise ProcessFailure("That apply kept no snapshot.")
    directory = Path(applied.snapshot).parent
    manifest = apply_module.load_manifest(directory)
    if not manifest:
        raise ProcessFailure("That apply kept no manifest, so nothing can be put back.")
    destination = _destination(project)
    restored = apply_module.rollback(destination, manifest, applied.snapshot)
    report = {
        "rolled_back_run": applied.pk,
        "destination": str(destination),
        "restored": restored,
    }
    return json.dumps(report, indent=2, sort_keys=True)
