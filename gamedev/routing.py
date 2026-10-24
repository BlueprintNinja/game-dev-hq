"""Closed local coding routes. Web input chooses a profile, never a command."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CodingProfile = Literal["routine", "complex", "orchestration"]
Model = Literal["gpt-6-luna", "gpt-6.1-sol"]
Effort = Literal["low", "medium", "high"]


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: Model
    effort: Effort = "high"


class RoutePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    routine: Route = Field(default_factory=lambda: Route(model="gpt-6-luna"))
    complex: Route = Field(default_factory=lambda: Route(model="gpt-6.1-sol"))
    orchestration: Route = Field(default_factory=lambda: Route(model="gpt-6.1-sol"))


def resolve(policy, profile):
    # Historical queued work has no requested profile. Resolve it at execution,
    # recording the choice without rewriting its historical request.
    if profile not in ("", "routine", "complex", "orchestration"):
        raise ValueError("Unknown coding profile.")
    name = profile or "routine"
    route = getattr(policy.routes, name)
    reason = "local_policy:" + name if profile else "local_policy:legacy_queue_default"
    return route, reason


def delegation_allowed(run):
    return (
        run.kind == "coding"
        and run.task.agent == "codex"
        and not run.delegated_by_id
        and not run.task.parent_id
        and run.requested_profile == "orchestration"
        and run.selected_model == "gpt-6.1-sol"
    )


def route_metadata(run):
    # Values are closed routing vocabulary and numeric IDs, never raw output.
    values = {
        "hq.route.profile": run.requested_profile,
        "hq.route.model": run.selected_model,
        "hq.route.effort": run.selected_effort,
        "hq.route.reason": run.routing_reason,
    }
    for key, value in (
        ("hq.delegated_by.id", run.delegated_by_id),
        ("hq.retried_from.id", run.retried_from_id),
        ("hq.escalated_from.id", run.escalated_from_id),
    ):
        if value:
            values[key] = value
    return values
