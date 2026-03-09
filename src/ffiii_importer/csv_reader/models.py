from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class RawTransaction(BaseModel):
    """Bank-agnostic normalized transaction, ready for LLM categorization."""

    date: date
    description: str
    amount: Decimal  # negative = expense, positive = income/deposit
    notes: str | None = None
    currency: str | None = None
    source_account_id: str  # Firefly asset account ID (from bank mapping)
    fingerprint: str  # sha256 hex for dedup
