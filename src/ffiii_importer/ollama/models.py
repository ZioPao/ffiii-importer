from __future__ import annotations

from pydantic import BaseModel, Field


class LLMCategorizationResult(BaseModel):
    category: str | None = None
    budget: str | None = None
    tags: list[str] = Field(default_factory=list)
    destination_account: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str | None = None
