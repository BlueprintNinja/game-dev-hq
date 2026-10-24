"""Trusted local routes. Requests select a profile, never command arguments."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CodingProfile = Literal["routine", "complex", "orchestration"]


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: Literal["gpt-6-luna", "gpt-6.1-sol"]
    effort: Literal["low", "medium", "high"] = "high"


class RoutePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routine: Route = Field(default_factory=lambda: Route(model="gpt-6-luna"))
    complex: Route = Field(default_factory=lambda: Route(model="gpt-6.1-sol"))
    orchestration: Route = Field(default_factory=lambda: Route(model="gpt-6.1-sol"))

    @model_validator(mode="after")
    def approved_models(self):
        for profile in ("routine", "complex", "orchestration"):
            self.resolve(profile)
        return self

    def resolve(self, profile):
        if profile not in ("routine", "complex", "orchestration"):
            raise ValueError("Unknown coding profile; choose a profile for a new run.")
        route = getattr(self, profile)
        expected = "gpt-6-luna" if profile == "routine" else "gpt-6.1-sol"
        if route.model != expected:
            raise ValueError("The profile must use its approved model.")
        return route
