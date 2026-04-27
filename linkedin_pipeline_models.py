from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LinkedInTriageDecision(BaseModel):
    job_id: str
    decision: Literal["keep", "borderline", "reject"]


class LinkedInTriageResponse(BaseModel):
    jobs: list[LinkedInTriageDecision] = Field(default_factory=list)


class LinkedInScoreResponse(BaseModel):
    score: int
    experience_required: str = "Not stated"
