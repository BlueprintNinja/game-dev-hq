"""Bounded child processes; Windows jobs terminate descendants when the worker dies."""

import os
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path
lazy import ctypes
lazy import fcntl
lazy import msvcrt

lazy import win32api
lazy import win32con
lazy import win32job


class ProcessFailure(RuntimeError):
    pass


def _windows_job(process):

    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    handle = win32api.OpenProcess(win32con.PROCESS_ALL_ACCESS, False, process.pid)
    try:
        win32job.AssignProcessToJobObject(job, handle)
    finally:
        win32api.CloseHandle(handle)
    # Start suspended so descendants cannot escape before job assignment.

    status = ctypes.windll.ntdll.NtResumeProcess(int(process._handle))
    if status:
        win32api.CloseHandle(job)
        raise ProcessFailure("Could not resume the supervised process.")
    return job


def _stop(process, job):
    if os.name == "nt":
        win32job.TerminateJobObject(job, 1)
    else:
        os.killpg(process.pid, signal.SIGKILL)


@contextmanager
def managed_process(argv, **options):
    platform = {"creationflags": 0x00000004 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
    process = subprocess.Popen(argv, **options, **platform)
    job = None
    try:
        job = _windows_job(process) if os.name == "nt" else None
        yield process, job
    finally:
        if process.poll() is None:
            if job is not None or os.name != "nt":
                _stop(process, job)
            else:
                process.kill()
        process.wait()
        if job is not None:
            win32api.CloseHandle(job)
        if os.name != "nt":
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)


def monitor(process, job, output_path, timeout, canceled, progress):
    deadline = time.monotonic() + timeout
    next_update = 0.0
    while process.poll() is None:
        if canceled() or time.monotonic() > deadline or output_path.stat().st_size > 1024 * 1024:
            _stop(process, job)
            raise ProcessFailure("Canceled, timed out, or exceeded the output limit.")
        if time.monotonic() >= next_update:
            progress(output_path.read_bytes()[: 1024 * 1024].decode("utf-8", errors="replace"))
            next_update = time.monotonic() + 3
        time.sleep(0.2)
    if output_path.stat().st_size > 1024 * 1024:
        raise ProcessFailure("Output exceeded the review limit.")
    return process.returncode, output_path.read_text(encoding="utf-8", errors="replace")


def run_process(argv, *, cwd, timeout, canceled=lambda: False, input_text="", env=None, progress=lambda text: None):
    """Never invoke a shell; stop at the time, cancellation, or 1 MiB output limit."""
    if canceled():
        raise ProcessFailure("Canceled before launch.")
    with tempfile.TemporaryDirectory(prefix="hq-process-") as root:
        output_path = Path(root) / "output"
        input_path = Path(root) / "input"
        input_path.write_text(input_text, encoding="utf-8")
        with (
            input_path.open("rb") as stdin,
            output_path.open("wb") as stdout,
            managed_process(argv, cwd=cwd, stdin=stdin, stdout=stdout, stderr=subprocess.STDOUT, env=env) as (
                process,
                job,
            ),
        ):
            return monitor(process, job, output_path, timeout, canceled, progress)


class WorkerLock:
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        try:
            self.stream.seek(0, 2)
            if self.stream.tell() == 0:
                self.stream.write(b"0")
                self.stream.flush()
            self.stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise
        return self

    def __exit__(self, *args):
        self.stream.close()
