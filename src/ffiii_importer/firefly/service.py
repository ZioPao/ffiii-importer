from __future__ import annotations

from decimal import Decimal

from ..csv_reader.models import RawTransaction
from .client import FireflyClient
from .models import FireflyBudget, FireflyCategory, FireflyTransactionPayload, FireflyTransactionSplit


def fetch_categories(client: FireflyClient) -> dict[str, str]:
    """Return {name: id} for all Firefly categories."""
    result: dict[str, str] = {}
    for item in client.get_categories():
        cat_id = str(item["id"])
        name = item.get("attributes", {}).get("name", "")
        if name:
            result[name] = cat_id
    return result


def fetch_budgets(client: FireflyClient) -> dict[str, str]:
    """Return {name: id} for all Firefly budgets."""
    result: dict[str, str] = {}
    for item in client.get_budgets():
        bud_id = str(item["id"])
        name = item.get("attributes", {}).get("name", "")
        if name:
            result[name] = bud_id
    return result


def build_payload(
    txn: RawTransaction,
    category_name: str | None,
    budget_name: str | None,
) -> dict:
    amount = abs(txn.amount)
    txn_type = "withdrawal" if txn.amount < Decimal("0") else "deposit"

    split = FireflyTransactionSplit(
        type=txn_type,
        date=txn.date,
        amount=str(amount),
        description=txn.description,
        source_id=txn.source_account_id if txn_type == "withdrawal" else None,
        destination_id=txn.source_account_id if txn_type == "deposit" else None,
        category_name=category_name,
        budget_name=budget_name,
        notes=txn.notes,
        currency_code=txn.currency,
        external_id=txn.fingerprint,
    )

    payload = FireflyTransactionPayload(transactions=[split])
    return payload.model_dump(exclude_none=True)


def push_transaction(
    client: FireflyClient,
    txn: RawTransaction,
    category_name: str | None,
    budget_name: str | None,
) -> dict:
    payload = build_payload(txn, category_name, budget_name)
    return client.create_transaction(payload)
