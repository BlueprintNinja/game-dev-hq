"""Applying an approved change to a destination, and taking it back.

Only the standard library: the companion's own tests read it without
installing anything.

The rules this module holds to:

- only a path the operator's scope names may be written;
- a path that leaves the destination, or that is a link, is refused;
- a file that changed in the destination since the run started is a
  conflict, and the change is not written over it;
- what a write replaces is copied aside first, so it can be put back;
- nothing is deleted by an apply. A change that removes a file is refused.

The comparison is by fingerprint rather than by content, so a file is
judged exactly as the repository judged it.
"""

import hashlib
import json
import re
import shutil
from functools import lru_cache
from pathlib import Path

# A path may not be absolute, may not name a drive, and may not climb out.
ABSOLUTE = re.compile(r"^(/|[A-Za-z]:)")
# Git writes a path in quotes when it holds a character it cannot spell
# plainly. Such a path is refused rather than guessed at.
QUOTED = re.compile(r'^"')
# The characters a scope pattern treats as ordinary text.
PATTERN_SPECIAL = re.compile(r"([.^$+(){}\[\]|\\])")


class Refusal(ValueError):
    """Something about the change or the destination is not safe."""


def sha256(data):
    """The fingerprint of some bytes."""
    return hashlib.sha256(data).hexdigest()


def normalize(path_text):
    """The relative path, or a refusal when it may leave the destination."""
    text = str(path_text).replace("\\", "/").strip()
    if not text:
        raise Refusal("An empty path does not name a file.")
    if ABSOLUTE.match(text):
        raise Refusal(f"'{path_text}' is not a relative path.")
    parts = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise Refusal(f"'{path_text}' leaves the destination.")
        parts.append(part)
    if not parts:
        raise Refusal(f"'{path_text}' does not name a file.")
    return "/".join(parts)


@lru_cache(maxsize=256)
def _scope_regex(pattern):
    out = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if pattern[index : index + 3] == "**/":
            out.append("(?:[^/]+/)*")
            index += 3
            continue
        if pattern[index : index + 2] == "**":
            out.append(".*")
            index += 2
            continue
        if character == "*":
            out.append("[^/]*")
            index += 1
            continue
        if character == "?":
            out.append("[^/]")
            index += 1
            continue
        out.append(PATTERN_SPECIAL.sub(r"\\\1", character))
        index += 1
    return re.compile("^" + "".join(out) + "$")


def matches_scope(relative, patterns):
    """Whether a relative path is one the operator's scope names."""
    if not patterns:
        return False
    return any(_scope_regex(pattern).match(relative) for pattern in patterns)


def _diff_path(text):
    """The path a diff header names."""
    value = text.strip()
    if value == "/dev/null":
        return None
    if QUOTED.match(value):
        raise Refusal("The change touches a path that cannot be read safely.")
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value


def changed_paths(diff_text):
    """The paths a diff touches, with whether each was removed."""
    entries = []
    removed = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            removed = None
        elif line.startswith("--- "):
            removed = _diff_path(line[4:])
        elif line.startswith("+++ "):
            added = _diff_path(line[4:])
            if added is None:
                if removed:
                    entries.append((removed, True))
                removed = None
            else:
                entries.append((added, False))
                removed = None
    return entries


class Change:
    """One file an apply would write, and what it would replace."""

    def __init__(self, relative, content, previous):
        self.relative = relative
        self.content = content
        self.previous = previous

    @property
    def created(self):
        """Whether the file is new in the destination."""
        return self.previous is None

    def as_dict(self):
        return {
            "path": self.relative,
            "created": self.created,
            "previous_sha256": sha256(self.previous) if self.previous is not None else None,
            "new_sha256": sha256(self.content),
            "bytes": len(self.content),
        }


def plan(destination, worktree, paths, scope, base_content, file_content):
    """The files an approved change would write, and what it would replace.

    `base_content(relative)` gives the bytes the file held at the base
    revision, or None when it did not exist. `file_content(path)` gives the
    bytes of a file on disk, or None when it does not exist. A destination
    file that no longer matches its base is a conflict: somebody changed it
    in the island while the work was being reviewed, and it is not written
    over.
    """
    root = Path(destination).resolve()
    changes = []
    for raw in paths:
        relative = normalize(raw)
        if not matches_scope(relative, scope):
            raise Refusal(f"'{relative}' is outside the approved scope.")
        target = root / relative
        source = Path(worktree) / relative
        if target.is_symlink():
            raise Refusal(f"'{relative}' is a link in the destination.")
        if not target.resolve().is_relative_to(root):
            raise Refusal(f"'{relative}' leaves the destination.")
        if source.is_symlink():
            raise Refusal(f"'{relative}' is a link in the approved changes.")
        if not source.is_file():
            raise Refusal(f"'{relative}' is not in the approved changes.")
        content = source.read_bytes()
        previous = file_content(target)
        if previous != base_content(relative):
            raise Refusal(f"'{relative}' changed in the destination since the run started.")
        changes.append(Change(relative, content, previous))
    return changes


def write(root, changes):
    """Write the planned files into the destination."""
    written = []
    for change in changes:
        target = Path(root) / change.relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(change.content)
        written.append(change.relative)
    return written


def snapshot(root, changes, directory):
    """Copy aside what an apply would replace, so it can be put back.

    The manifest records every file, including the ones that did not exist
    and will therefore have to be removed again.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = []
    for change in changes:
        stored = directory / change.relative
        stored.parent.mkdir(parents=True, exist_ok=True)
        if change.previous is not None:
            shutil.copy2(Path(root) / change.relative, stored)
        manifest.append(change.as_dict())
    return manifest


def save_manifest(directory, manifest):
    """Write the manifest beside the snapshot."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_manifest(directory):
    """The manifest of an apply, or None when there is none."""
    path = Path(directory) / "manifest.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def rollback(root, manifest, directory):
    """Put back what an apply replaced."""
    restored = []
    for entry in manifest:
        relative = entry["path"]
        target = Path(root) / relative
        if entry.get("previous_sha256") is None:
            if target.is_file():
                target.unlink()
            restored.append(relative)
            continue
        stored = Path(directory) / relative
        if not stored.is_file():
            raise Refusal(f"The snapshot of '{relative}' is missing; nothing was put back.")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(stored, target)
        restored.append(relative)
    return restored


def fingerprint(path):
    """The fingerprint of a file's contents."""
    return sha256(Path(path).read_bytes())
