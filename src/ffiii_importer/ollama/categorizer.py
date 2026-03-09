from __future__ import annotations

import json
import re

import ollama
from pydantic import ValidationError

from ..config.models import OllamaConfig
from ..csv_reader.models import RawTransaction
from .models import LLMCategorizationResult

_FALLBACK = LLMCategorizationResult(category=None, budget=None, tags=[], destination_account=None, confidence=0.0)

_SYSTEM_TEMPLATE = """\
You are a financial transaction categorizer for a personal finance application.
Your job is to assign a category, optionally a budget, zero or more tags, and a destination account \
to a bank transaction based on its description, date, and amount.

Rules:
- The "category" field MUST be exactly one of the category names listed below. If nothing fits well, use "Various".
- The "budget" field MUST be exactly one of the budget names listed below. If nothing fits well, use "Various". If there are no budgets listed, or if the transaction amount is positive (income/deposit), use null.
- The "tags" field MUST be a JSON array of zero or more tag names from the list below. Only include tags that clearly apply. Use an empty array if none fit.
- The "destination_account" field MUST be exactly one of the expense account names listed below, chosen based on the merchant or purpose of the transaction. If no account fits well, use "Cash".
- The "confidence" field must be a float between 0.0 and 1.0.
- The "reasoning" field is a brief one-sentence explanation of your choice.
- Your final answer MUST be a single JSON object. No extra text after the JSON.

Available categories:
{categories}

Available budgets:
{budgets}

Available tags:
{tags}

Available expense accounts:
{expense_accounts}
"""

_USER_TEMPLATE = """\
Transaction description: "{description}"
Transaction date: {date}
Amount: {amount}{currency_part}

Respond with a single JSON object matching this schema:
{{
  "category": "<category name, or 'Various' if unsure>",
  "budget": "<budget name, or 'Various' if unsure, or null if no budgets exist>",
  "tags": ["<tag name>", ...],
  "destination_account": "<expense account name, or 'Cash' if unsure>",
  "confidence": <0.0 to 1.0>,
  "reasoning": "<one sentence>"
}}"""


def _extract_json(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)
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
        tags: dict[str, str] | None = None,
        expense_accounts: dict[str, str] | None = None,
    ) -> None:
        self._config = config
        self._categories = categories
        self._budgets = budgets
        self._tags = tags or {}
        self._expense_accounts = expense_accounts or {}
        self._client = ollama.Client(host=config.url, timeout=config.timeout_seconds)
        self._system_prompt = _SYSTEM_TEMPLATE.format(
            categories="\n".join(f"- {name}" for name in sorted(categories)) or "(none)",
            budgets="\n".join(f"- {name}" for name in sorted(budgets)) or "(none)",
            tags="\n".join(f"- {name}" for name in sorted(self._tags)) or "(none)",
            expense_accounts="\n".join(f"- {name}" for name in sorted(self._expense_accounts)) or "(none)",
        )
        if config.notes:
            self._system_prompt += f"\nAdditional context:\n{config.notes}\n"

    def categorize(self, txn: RawTransaction) -> LLMCategorizationResult:
        currency_part = f" {txn.currency}" if txn.currency else ""
        user_msg = _USER_TEMPLATE.format(
            description=txn.description,
            date=txn.date.isoformat(),
            amount=txn.amount,
            currency_part=currency_part,
        )

        options: dict = {"temperature": 0.1}
        if self._config.is_thinking_model:
            options["temperature"] = 0

        chat_kwargs: dict = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": user_msg},
            ],
            "options": options,
        }
        if not self._config.is_thinking_model:
            chat_kwargs["format"] = "json"

        if self._config.verbose:
            print(f"\n[LLM] {txn.description[:60]}", flush=True)

        try:
            in_thinking = False
            raw_text = ""
            for chunk in self._client.chat(**chat_kwargs, stream=True):

                # if chunk.message.thinking:
                #     if not in_thinking:
                #         in_thinking = True
                #         print('Thinking:\n', end='', flush=True)
                #     print(chunk.message.thinking, end='', flush=True)
                token = chunk.message.content or ""
                raw_text += token
                if self._config.verbose:
                    print(token, end="", flush=True)
                if len(raw_text) > 8_000:
                    print("\n[LLM] truncated at 8000 chars", flush=True)
                    return _FALLBACK
            if self._config.verbose:
                print(flush=True)
        except Exception as exc:
            print(f"\n[LLM] error: {exc}", flush=True)
            return _FALLBACK

        json_text = _extract_json(raw_text)

        try:
            result = LLMCategorizationResult.model_validate_json(json_text)
        except (ValidationError, json.JSONDecodeError) as exc:
            print(f"\n[LLM] parse failed: {exc}\nraw: {raw_text[:300]}", flush=True)
            return _FALLBACK

        if result.category and result.category not in self._categories:
            result = result.model_copy(update={"category": None, "confidence": 0.0})
        if result.budget and result.budget not in self._budgets:
            result = result.model_copy(update={"budget": None})
        if result.tags:
            valid_tags = [t for t in result.tags if t in self._tags]
            if valid_tags != result.tags:
                result = result.model_copy(update={"tags": valid_tags})
        if result.destination_account and result.destination_account not in self._expense_accounts:
            result = result.model_copy(update={"destination_account": None})

        return result
