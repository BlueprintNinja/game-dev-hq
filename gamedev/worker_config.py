"""Local operator policy. No executable, path, or check comes from a web request."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CheckSpec(BaseModel):
    """One check the operator configured, and the kind of evidence it gives.

    A bare command is a check of the kind "check", so a policy written before
    the kinds existed keeps working.
    """

    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="check", min_length=1, max_length=40)
    command: list[str] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def a_bare_command_is_a_check(cls, value):
        if isinstance(value, (list, tuple)):
            return {"command": list(value)}
        return value

    @field_validator("command")
    @classmethod
    def commands(cls, value):
        if any(not isinstance(argument, str) or not argument for argument in value):
            raise ValueError("Checks must be nonempty argument arrays.")
        if not Path(value[0]).is_absolute():
            raise ValueError("Check executables must use absolute paths.")
        return value


class ProjectPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: Path
    checks: list[CheckSpec] = Field(min_length=1, max_length=8)
    required_kinds: list[str] = Field(default_factory=list, max_length=8)
    timeout_seconds: int = Field(default=600, ge=30, le=1800)
    coding_enabled: bool = True
    uefn_project: Path | None = None
    # Where an approved change may be written, and which paths inside it may
    # be written at all. Both are the operator's to set; neither is ever
    # taken from a web request.
    apply_destination: Path | None = None
    apply_scope: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("repository", "uefn_project", "apply_destination")
    @classmethod
    def absolute_path(cls, value):
        if value is not None and not value.is_absolute():
            raise ValueError("Worker paths must be absolute.")
        return value

    @field_validator("required_kinds", "apply_scope")
    @classmethod
    def nonempty_names(cls, value):
        if any(not name.strip() for name in value):
            raise ValueError("Names must not be empty.")
        return value

    @field_validator("apply_scope")
    @classmethod
    def relative_scope(cls, value):
        for pattern in value:
            if pattern.startswith("/") or pattern.startswith("\\") or ".." in pattern.split("/"):
                raise ValueError("Apply scope patterns must be relative to the destination.")
        return value


class QuotaFallback(BaseModel):
    """The engine that finishes the work when the one asked for is out of allowance."""

    model_config = ConfigDict(extra="forbid")
    agent: Literal["opencode"]
    matches: list[str] = Field(min_length=1, max_length=8)


class WorkerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codex: Path
    windows_sandbox: Literal["mxc", "elevated"] = "mxc"
    opencode: Path | None = None
    opencode_model: str = ""
    opencode_domains: list[str] = Field(default_factory=list, max_length=8)
    quota_fallback: QuotaFallback | None = None
    artifacts: Path
    projects: dict[str, ProjectPolicy]

    @field_validator("codex", "opencode", "artifacts")
    @classmethod
    def absolute_path(cls, value):
        return ProjectPolicy.absolute_path(value)


def load_policy(path):
    return WorkerPolicy.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
