"""Local, supervised coding worker. Run with python -m gamedev.worker --policy ..."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

lazy import django

lazy from hq_sdk.capabilities import cli_principal

from .delegation import CodexResult, dispatch, opencode_command, opencode_environment, read_result
from .quota import quota_reason
from .readiness import wait_until_ready
from .supervisor import ProcessFailure, WorkerLock, run_process
from .worker_config import load_policy
lazy from .hq_plugin.models import Run
lazy from .hq_plugin.worker_state import claim, recover, update


def git(repository, *args):
    executable = shutil.which("git")
    if not executable:
        raise ProcessFailure("Git is not installed.")
    status, output = run_process([executable, "-C", str(repository), *args], cwd=repository, timeout=30)
    if status:
        raise ProcessFailure(f"Git exited with status {status}: {output[:2000]}")
    return output


def codex_command(policy, worktree):
    return [
        str(policy.codex),
        "--no-daemon",
        "exec",
        "--ignore-user-config",
        "--sandbox",
        "workspace-write",
        "-c",
        'approval_policy="never"',
        "-c",
        "sandbox_workspace_write.network_access=false",
        "-c",
        "windows.sandbox=" + json.dumps(policy.windows_sandbox),
        "--ephemeral",
        "--json",
        "-C",
        str(worktree),
        "-",
    ]


def sandbox_command(policy, worktree, argv):
    return [
        str(policy.codex),
        "-c",
        "windows.sandbox=" + json.dumps(policy.windows_sandbox),
        "sandbox",
        "-P",
        ":workspace",
        "--sandbox-state-disable-network",
        "-C",
        str(worktree),
        "--",
        *argv,
    ]


def run_fallback(policy, project, worktree, prompt, canceled, save, reason, output):
    """Finish the work with the other engine, and say that it did.

    The fallback runs in the same worktree, under the same sandbox and the
    same checks, so the evidence the operator reviews is the same either way.
    """
    agent = policy.quota_fallback.agent
    with tempfile.TemporaryDirectory(prefix="hq-fallback-") as runtime:
        code, result = run_process(
            opencode_command(policy, worktree),
            cwd=worktree,
            timeout=project.timeout_seconds,
            canceled=canceled,
            input_text=prompt,
            env=opencode_environment(policy, runtime),
            progress=lambda text: save(output=text),
        )
    marker = (
        f"\n=== fallback to {agent}: the requested agent reported no allowance ===\n{reason}\n=== {agent} output ===\n"
    )
    return code, f"{output}{marker}{result}", agent


def run_agent(run, policy, project, worktree, base, canceled, save):
    """Run the agent the task names, and the fallback engine when it has no allowance."""
    if run.task.agent == "opencode":
        command = opencode_command(policy, worktree)
        prompt = base
    else:
        schema = policy.artifacts / f"run-{run.pk}-schema.json"
        schema.write_text(json.dumps(CodexResult.model_json_schema()), encoding="utf-8")
        command = [*codex_command(policy, worktree)[:-1], "--output-schema", str(schema), "-"]
        prompt = (
            base
            + "\nReturn the required JSON. You may delegate at most two isolated coding jobs to OpenCode; leave delegations empty when unnecessary. Delegated changes are separate worktrees and need independent review."
        )
    with tempfile.TemporaryDirectory(prefix="hq-agent-") as runtime:
        environment = opencode_environment(policy, runtime) if run.task.agent == "opencode" else None
        code, output = run_process(
            command,
            cwd=worktree,
            timeout=project.timeout_seconds,
            canceled=canceled,
            input_text=prompt,
            env=environment,
            progress=lambda text: save(output=text),
        )
    fallback = None
    if code and run.task.agent == "codex" and policy.quota_fallback is not None:
        reason = quota_reason(policy.quota_fallback.matches, output)
        if reason:
            code, output, fallback = run_fallback(
                policy,
                project,
                worktree,
                base
                + "\nThe agent that was asked for this work reported no allowance. Finish the same task with the engine you are.",
                canceled,
                save,
                reason,
                output,
            )
    return code, output, fallback


def collect_evidence(policy, project, worktree, canceled):
    """The checks the policy names, and the diff of what the run changed."""
    checks = []
    for command in project.checks:
        status, log = run_process(
            sandbox_command(policy, worktree, command), cwd=worktree, timeout=project.timeout_seconds, canceled=canceled
        )
        checks.append({"command": command, "exit_code": status, "output": log[:64000]})
    # Include new files in evidence without creating a commit. Retain worktree for review.
    git(worktree, "add", "--intent-to-add", "--all")
    diff = git(worktree, "diff", "--no-ext-diff", "--binary", "HEAD")
    if len(diff.encode()) > 1024 * 1024:
        raise ProcessFailure("Diff exceeds the review limit. Inspect the retained worktree locally.")
    return checks, diff


def execute(run, policy, principal):

    project = policy.projects.get(run.task.project_slug)
    if project is None:
        raise ProcessFailure("This project has no local worker policy.")
    if run.kind != "coding":
        return execute_editor(run, project, principal)
    if not project.coding_enabled:
        raise ProcessFailure("Coding is disabled by the local policy for this project.")
    if run.task.agent == "opencode" and (not run.delegated_by_id or run.delegated_by.task_id != run.task.parent_id):
        raise ProcessFailure("OpenCode dispatch requires verified Codex delegation provenance.")
    revision = git(project.repository, "rev-parse", "HEAD").strip()
    worktree = policy.artifacts / f"run-{run.pk}"
    policy.artifacts.mkdir(parents=True, exist_ok=True)
    git(project.repository, "worktree", "add", "--detach", str(worktree), revision)

    def save(**values):
        update(run.pk, run.worker_token, principal, **values)

    def canceled():
        return Run.objects.filter(pk=run.pk, cancel_requested=True).exists()

    save(base_revision=revision, worktree=str(worktree))
    base = (
        "Work only on this task. Do not commit, push, merge, publish, access credentials, or change other repositories. Return a summary and verification evidence.\n\n"
        + run.prompt
    )
    code, output, fallback = run_agent(run, policy, project, worktree, base, canceled, save)
    if code:
        save(output=output)
        raise ProcessFailure(f"Agent exited with status {code}.")
    checks, diff = collect_evidence(policy, project, worktree, canceled)
    if run.task.agent == "codex" and fallback is None:
        dispatch(run, read_result(output), principal)
    save(
        status="review",
        output=output,
        diff=diff,
        diff_sha256=hashlib.sha256(diff.encode()).hexdigest(),
        verification=checks,
        passed=all(check["exit_code"] == 0 for check in checks),
    )


def execute_editor(run, project, principal):

    if project.uefn_project is None:
        raise ProcessFailure("Editor access is not configured for this project.")
    code, output = run_process(
        [sys.executable, "-m", "gamedev.uefn", "--project", str(project.uefn_project), "--action", run.kind],
        cwd=Path(__file__).resolve().parents[1],
        timeout=120,
        canceled=lambda: Run.objects.filter(pk=run.pk, cancel_requested=True).exists(),
    )
    evidence = json.loads(output) if code == 0 else {}
    passed = code == 0 and (run.kind == "uefn_inspect" or evidence.get("passed") is True)
    update(
        run.pk,
        run.worker_token,
        principal,
        status="review",
        output=output,
        verification=[{"command": [run.kind], "exit_code": 0 if passed else 1, "output": output}],
        passed=passed,
    )


def process_next(policy, principal):

    run = claim(principal)
    if run is None:
        return False
    try:
        execute(run, policy, principal)
    except (OSError, ValueError, subprocess.SubprocessError, ProcessFailure) as exc:
        current = Run.objects.get(pk=run.pk)
        update(
            run.pk,
            run.worker_token,
            principal,
            status="canceled" if current.cancel_requested else "failed",
            output=current.output + "\n" + str(exc),
        )
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--workspace-url",
        default="",
        help="The workspace health endpoint. The worker waits for it before it opens the database.",
    )
    parser.add_argument("--workspace-timeout", type=float, default=600)
    args = parser.parse_args()
    policy = load_policy(args.policy)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tools.windows_hq.settings")

    django.setup()

    principal = cli_principal()
    try:
        # The workspace migrates the database on every start; a worker that
        # opened it beside that would read a half-migrated file.
        if args.workspace_url:
            wait_until_ready(args.workspace_url, timeout=args.workspace_timeout)
    except TimeoutError as error:
        parser.exit(1, f"Error: {error}\n")
    with WorkerLock(Path(os.environ["GAMEDEV_WORKSPACE_DATA"]) / "worker.lock"):
        recover(principal)
        while True:
            worked = process_next(policy, principal)
            if args.once:
                break
            if not worked:
                time.sleep(3)


if __name__ == "__main__":
    main()
