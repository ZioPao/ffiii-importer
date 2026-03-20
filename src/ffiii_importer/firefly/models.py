from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class FireflyCategory(BaseModel):
    id: str
    name: str


class FireflyBudget(BaseModel):
    id: str
    name: str


class FireflyTransactionSplit(BaseModel):
    type: str  # "withdrawal", "deposit", "transfer"
    date: date
    amount: str  # Firefly expects a string representation
    description: str
    source_id: str | None = None
    destination_id: str | None = None
    destination_name: str | None = None
    category_name: str | None = None
    budget_name: str | None = None
    notes: str | None = None
    currency_code: str | None = None
    external_id: str | None = None  # store fingerprint here for Firefly-side dedup
    tags: list[str] | None = None


class FireflyTransactionPayload(BaseModel):
    error_if_duplicate_hash: bool = True
    apply_rules: bool = True
    transactions: list[FireflyTransactionSplit]


class FireflyTransactionResponse(BaseModel):
    data: dict[str, Any]
