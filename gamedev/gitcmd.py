"""One way to run git: supervised, without a shell, and byte for byte.

The worker runs git itself, outside any agent's sandbox, and it needs two
things from it: the text of a command, and the exact bytes of a file at a
revision. The second is what a conflict check compares, so it must not be
decoded on the way through.

What a command said on standard error is kept apart from what it said on
standard output. Git warns about the line endings it will store, and a
warning mixed into a diff would change the diff's fingerprint.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from .supervisor import ProcessFailure, managed_process

SECONDS = 30


def _executable():
    executable = shutil.which("git")
    if not executable:
        raise ProcessFailure("Git is not installed.")
    return executable


def _run(repository, args):
    """What a git command said, and whether it failed."""
    with tempfile.TemporaryDirectory(prefix="hq-git-") as root:
        output_path = Path(root) / "output"
        errors_path = Path(root) / "errors"
        with (
            output_path.open("wb") as stdout,
            errors_path.open("wb") as stderr,
            managed_process(
                [_executable(), "-C", str(repository), *args],
                cwd=repository,
                stdout=stdout,
                stderr=stderr,
            ) as (process, _job),
        ):
            try:
                status = process.wait(timeout=SECONDS)
            except subprocess.TimeoutExpired:
                status = 124
        return status, output_path.read_bytes(), errors_path.read_bytes()


def git(repository, *args):
    """The text a git command answered."""
    status, output, errors = _run(repository, args)
    if status:
        message = errors.decode("utf-8", errors="replace") or output.decode("utf-8", errors="replace")
        raise ProcessFailure(f"Git exited with status {status}: {message[:2000]}")
    return output.decode("utf-8", errors="replace")


def git_bytes(repository, *args):
    """What a git command answered, byte for byte, and whether it failed."""
    status, output, _errors = _run(repository, args)
    return status, output
