from __future__ import annotations

import json

import ollama
from pydantic import ValidationError

from ..config.models import OllamaConfig
from ..csv_reader.models import RawTransaction
from .models import LLMCategorizationResult

_FALLBACK = LLMCategorizationResult(category=None, budget=None, confidence=0.0)

_SYSTEM_TEMPLATE = """\
You are a financial transaction categorizer for a personal finance application.
Your job is to assign a category and optionally a budget to a bank transaction
based on its description, date, and amount.

Rules:
- You MUST respond with valid JSON only. No prose, no markdown fences.
- The "category" field MUST be exactly one of the category names listed below, or null if none fits.
- The "budget" field MUST be exactly one of the budget names listed below, or null if none applies.
- The "confidence" field must be a float between 0.0 and 1.0.
- The "reasoning" field is a brief one-sentence explanation of your choice.

Available categories:
{categories}

Available budgets:
{budgets}
"""

_USER_TEMPLATE = """\
Transaction description: "{description}"
Transaction date: {date}
Amount: {amount}{currency_part}

Respond with JSON matching this schema exactly:
{{
  "category": "<category name or null>",
  "budget": "<budget name or null>",
  "confidence": <0.0 to 1.0>,
  "reasoning": "<one sentence>"
}}"""


class Categorizer:
    def __init__(
        self,
        config: OllamaConfig,
        categories: dict[str, str],
        budgets: dict[str, str],
    ) -> None:
        self._config = config
        self._categories = categories
        self._budgets = budgets
        self._client = ollama.Client(host=config.url)
        self._system_prompt = _SYSTEM_TEMPLATE.format(
            categories="\n".join(f"- {name}" for name in sorted(categories)) or "(none)",
            budgets="\n".join(f"- {name}" for name in sorted(budgets)) or "(none)",
        )

    def categorize(self, txn: RawTransaction) -> LLMCategorizationResult:
        currency_part = f" {txn.currency}" if txn.currency else ""
        user_msg = _USER_TEMPLATE.format(
            description=txn.description,
            date=txn.date.isoformat(),
            amount=txn.amount,
            currency_part=currency_part,
        )

        try:
            response = self._client.chat(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                format="json",
                options={"temperature": 0.1},
            )
            raw_text = response.message.content or ""
        except Exception:
            return _FALLBACK

        try:
            result = LLMCategorizationResult.model_validate_json(raw_text)
        except (ValidationError, json.JSONDecodeError):
            return _FALLBACK

        # Validate returned names against known values; nullify if hallucinated
        if result.category and result.category not in self._categories:
            result = result.model_copy(update={"category": None, "confidence": 0.0})
        if result.budget and result.budget not in self._budgets:
            result = result.model_copy(update={"budget": None})

        return result
