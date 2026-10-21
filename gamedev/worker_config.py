"""Local operator policy. No executable, path, or check comes from a web request."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProjectPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: Path
    checks: list[list[str]] = Field(min_length=1, max_length=8)
    timeout_seconds: int = Field(default=600, ge=30, le=1800)
    coding_enabled: bool = True
    uefn_project: Path | None = None

    @field_validator("repository", "uefn_project")
    @classmethod
    def absolute_path(cls, value):
        if value is not None and not value.is_absolute():
            raise ValueError("Worker paths must be absolute.")
        return value

    @field_validator("checks")
    @classmethod
    def commands(cls, value):
        if any(not command or any(not isinstance(arg, str) or not arg for arg in command) for command in value):
            raise ValueError("Checks must be nonempty argument arrays.")
        if any(not Path(command[0]).is_absolute() for command in value):
            raise ValueError("Check executables must use absolute paths.")
        return value


class WorkerPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    codex: Path
    windows_sandbox: Literal["mxc", "elevated"] = "mxc"
    opencode: Path | None = None
    opencode_model: str = ""
    opencode_domains: list[str] = Field(default_factory=list, max_length=8)
    artifacts: Path
    projects: dict[str, ProjectPolicy]

    @field_validator("codex", "opencode", "artifacts")
    @classmethod
    def absolute_path(cls, value):
        return ProjectPolicy.absolute_path(value)


def load_policy(path):
    return WorkerPolicy.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
