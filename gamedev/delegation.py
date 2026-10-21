"""Structured Codex requests are the sole source of automated OpenCode jobs."""

import json
import os
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
lazy from django.db import transaction

lazy from hq_sdk.audit import audit_operation, record_operation

lazy from .hq_plugin.models import Run, Task


class DelegatedWork(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=12000)


class CodexResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    delegations: list[DelegatedWork] = Field(max_length=2)


def read_result(output):
    messages = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "agent_message":
            messages.append(event["item"].get("text", ""))
    if not messages:
        raise ValueError("Codex did not return a structured result.")
    return CodexResult.model_validate_json(messages[-1])


def dispatch(run, result, principal):

    principal.require("gamedev.write")
    with transaction.atomic(), audit_operation(operation="gamedev.run.delegate", principal=principal):
        parent = Run.objects.select_for_update().get(pk=run.pk)
        if (
            parent.status != "running"
            or parent.worker_token != run.worker_token
            or parent.task.agent != "codex"
            or parent.delegated_by_id
        ):
            raise ValueError("Only the owning Codex run can delegate.")
        if parent.delegated_runs.exists():
            raise ValueError("Delegations have already been dispatched.")
        for item in result.delegations:
            child = Task.objects.create(
                project_slug=parent.task.project_slug,
                title=item.title,
                agent="opencode",
                parent=parent.task,
                status="active",
            )
            delegated = Run.objects.create(task=child, delegated_by=parent, prompt=item.prompt)
            record_operation(
                "gamedev.run.delegate",
                f"Codex run #{parent.pk} delegated run #{delegated.pk}",
                metadata={"parent_run_id": parent.pk, "run_id": delegated.pk},
                required=True,
            )


def opencode_environment(policy, runtime=None):
    permissions = {
        "*": "deny",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "list": "allow",
        "edit": "allow",
        "external_directory": "deny",
        "shell": "deny",
        "bash": "deny",
        "task": "deny",
        "webfetch": "deny",
        "skill": "deny",
        "lsp": "deny",
    }
    config = {
        "plugin": [],
        "permission": permissions,
        "agent": {
            "hq-delegate": {
                "mode": "primary",
                "permission": permissions,
                "prompt": "Complete only the explicit delegated coding task. Do not commit, publish or delegate further. External commands and network tools are disabled.",
            }
        },
        "lsp": False,
        "formatter": False,
        "share": "disabled",
        "autoupdate": False,
    }
    environment = {
        **os.environ,
        "OPENCODE_CONFIG_CONTENT": json.dumps(config),
        "OPENCODE_DISABLE_AUTOUPDATE": "true",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true",
        "OPENCODE_DISABLE_CLAUDE_CODE": "true",
        "OPENCODE_DISABLE_LSP_DOWNLOAD": "true",
    }
    if runtime is not None:
        root = Path(runtime)
        data = root / "data" / "opencode"
        data.mkdir(parents=True)
        auth = Path.home() / ".local" / "share" / "opencode" / "auth.json"
        if auth.is_file():
            shutil.copyfile(auth, data / "auth.json")
        environment.update(
            {
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_DATA_HOME": str(root / "data"),
                "XDG_CACHE_HOME": str(root / "cache"),
                "XDG_STATE_HOME": str(root / "state"),
                "OPENCODE_CONFIG_DIR": str(root / "config" / "opencode"),
            }
        )
    return environment


def opencode_command(policy, worktree):
    if not policy.opencode or not policy.opencode_model or not policy.opencode_domains:
        raise ValueError("OpenCode requires a local executable, model and provider domain policy.")
    command = [
        str(policy.codex),
        "sandbox",
        "-c",
        "windows.sandbox=" + json.dumps(policy.windows_sandbox),
        "-P",
        "hq-delegate",
        "-c",
        'permissions.hq-delegate.extends=":workspace"',
        "-c",
        "permissions.hq-delegate.network.enabled=true",
        "-c",
        "permissions.hq-delegate.network.allow_local_binding=true",
        "-c",
        "features.network_proxy=true",
    ]
    domains = ", ".join(json.dumps(domain) + ' = "allow"' for domain in policy.opencode_domains)
    command += ["-c", "permissions.hq-delegate.network.domains={" + domains + "}"]
    return [
        *command,
        "-C",
        str(worktree),
        "--",
        str(policy.opencode),
        "run",
        "--pure",
        "--agent",
        "hq-delegate",
        "--format",
        "json",
        "--model",
        policy.opencode_model,
        "--dir",
        str(worktree),
    ]
