from __future__ import annotations

import json
import re

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
- The "category" field MUST be exactly one of the category names listed below. If nothing fits well, use "Various".
- The "budget" field MUST be exactly one of the budget names listed below. If nothing fits well, use "Various". If there are no budgets listed, use null.
- The "confidence" field must be a float between 0.0 and 1.0.
- The "reasoning" field is a brief one-sentence explanation of your choice.
- Your final answer MUST be a single JSON object. No extra text after the JSON.

Available categories:
{categories}

Available budgets:
{budgets}
"""

_USER_TEMPLATE = """\
Transaction description: "{description}"
Transaction date: {date}
Amount: {amount}{currency_part}

Respond with a single JSON object matching this schema:
{{
  "category": "<category name, or 'Various' if unsure>",
  "budget": "<budget name, or 'Various' if unsure, or null if no budgets exist>",
  "confidence": <0.0 to 1.0>,
  "reasoning": "<one sentence>"
}}"""


def _extract_json(text: str) -> str:
    """
    Extract the first JSON object from `text`, tolerating:
    - <think>…</think> blocks emitted by thinking models
    - markdown code fences (```json … ```)
    - leading/trailing prose
    """
    # Strip thinking blocks (greedy is fine — there's only one per response)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    # Strip markdown fences
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)

    # Find the first {...} object (handles nested braces)
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return text[start : i + 1]

    return text.strip()


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

        options: dict = {"temperature": 0.1}
        # Thinking models have their own internal reasoning temperature; nudging
        # the output temperature to 0 keeps the final JSON answer deterministic.
        if self._config.is_thinking_model:
            options["temperature"] = 0

        try:
            response = self._client.chat(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                # format="json" is not supported by most thinking models — skip it for them
                **({"format": "json"} if not self._config.is_thinking_model else {}),
                options=options,
            )
            raw_text = response.message.content or ""
        except Exception:
            return _FALLBACK

        json_text = _extract_json(raw_text)

        try:
            result = LLMCategorizationResult.model_validate_json(json_text)
        except (ValidationError, json.JSONDecodeError):
            return _FALLBACK

        # Validate returned names against known values; nullify if hallucinated
        if result.category and result.category not in self._categories:
            result = result.model_copy(update={"category": None, "confidence": 0.0})
        if result.budget and result.budget not in self._budgets:
            result = result.model_copy(update={"budget": None})

        return result
