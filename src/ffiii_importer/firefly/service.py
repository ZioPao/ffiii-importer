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
    destination_account_id: str | None = None,
) -> dict:
    amount = abs(txn.amount)

    if destination_account_id is not None:
        txn_type = "transfer"
        source_id = txn.source_account_id
        dest_id = destination_account_id
    elif txn.amount < Decimal("0"):
        txn_type = "withdrawal"
        source_id = txn.source_account_id
        dest_id = None
    else:
        txn_type = "deposit"
        source_id = None
        dest_id = txn.source_account_id

    split = FireflyTransactionSplit(
        type=txn_type,
        date=txn.date,
        amount=str(amount),
        description=txn.description,
        source_id=source_id,
        destination_id=dest_id,
        # Transfers don't use categories or budgets in Firefly
        category_name=category_name if txn_type != "transfer" else None,
        budget_name=budget_name if txn_type != "transfer" else None,
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
    destination_account_id: str | None = None,
) -> dict:
    payload = build_payload(txn, category_name, budget_name, destination_account_id)
    return client.create_transaction(payload)
