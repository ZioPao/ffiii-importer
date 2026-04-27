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


def fetch_asset_accounts(client: FireflyClient) -> dict[str, str]:
    """Return {iban: account_id} for all asset accounts that have an IBAN."""
    result: dict[str, str] = {}
    for item in client.get_accounts("asset"):
        acct_id = str(item["id"])
        attrs = item.get("attributes", {})
        iban = attrs.get("iban") or ""
        if iban:
            result[iban.upper()] = acct_id
    return result


def fetch_expense_accounts(client: FireflyClient) -> dict[str, str]:
    """Return {name: account_id} for all expense accounts."""
    result: dict[str, str] = {}
    for item in client.get_accounts("expense"):
        acct_id = str(item["id"])
        name = item.get("attributes", {}).get("name", "")
        if name:
            result[name] = acct_id
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


def fetch_tags(client: FireflyClient) -> dict[str, str]:
    """Return {tag: id} for all Firefly tags."""
    result: dict[str, str] = {}
    for item in client.get_tags():
        tag_id = str(item["id"])
        tag = item.get("attributes", {}).get("tag", "")
        if tag:
            result[tag] = tag_id
    return result


def fetch_firefly_transactions(
    client: FireflyClient,
    account_id: str,
    start: str,
    end: str,
) -> list[dict]:
    """Return transactions for an account as list of {date, amount (signed), description, external_id}."""
    result = []
    for item in client.get_account_transactions(account_id, start, end):
        for split in item.get("attributes", {}).get("transactions", []):
            source_id = str(split.get("source_id") or "")
            dest_id = str(split.get("destination_id") or "")
            try:
                raw_amount = Decimal(split.get("amount", "0"))
            except Exception:
                raw_amount = Decimal("0")
            if source_id == account_id:
                amount = -raw_amount
            elif dest_id == account_id:
                amount = raw_amount
            else:
                continue
            result.append({
                "date": split.get("date", "")[:10],
                "amount": amount,
                "description": split.get("description", ""),
                "external_id": split.get("external_id") or None,
            })
    return result


def transaction_exists(client: FireflyClient, txn: RawTransaction) -> bool:
    """Return True if Firefly already has a transaction on the same date with the same amount."""
    date_str = txn.date.isoformat()
    amount = abs(txn.amount)
    for item in client.get_transactions(date_str, date_str):
        for split in item.get("attributes", {}).get("transactions", []):
            try:
                if Decimal(split.get("amount", "0")) == amount:
                    return True
            except Exception:
                continue
    return False


def build_payload(
    txn: RawTransaction,
    category_name: str | None,
    budget_name: str | None,
    destination_account_id: str | None = None,
    tags: list[str] | None = None,
    expense_destination_name: str | None = None,
) -> dict:
    amount = abs(txn.amount)

    if destination_account_id is not None:
        txn_type = "transfer"
        source_id = txn.source_account_id
        dest_id = destination_account_id
        dest_name = None
    elif txn.amount < Decimal("0"):
        txn_type = "withdrawal"
        source_id = txn.source_account_id
        dest_id = None
        dest_name = expense_destination_name  # may be None (Firefly auto-creates)
    else:
        txn_type = "deposit"
        source_id = None
        dest_id = txn.source_account_id
        dest_name = None

    split = FireflyTransactionSplit(
        type=txn_type,
        date=txn.date,
        amount=str(amount),
        description=txn.description,
        source_id=source_id,
        destination_id=dest_id,
        destination_name=dest_name,
        # Transfers and deposits don't use budgets in Firefly
        category_name=category_name if txn_type != "transfer" else None,
        budget_name=budget_name if txn_type == "withdrawal" else None,
        notes=txn.notes,
        currency_code=txn.currency,
        external_id=txn.fingerprint,
        tags=tags if tags else None,
    )

    payload = FireflyTransactionPayload(transactions=[split])
    return payload.model_dump(mode="json", exclude_none=True)


def push_transaction(
    client: FireflyClient,
    txn: RawTransaction,
    category_name: str | None,
    budget_name: str | None,
    destination_account_id: str | None = None,
    tags: list[str] | None = None,
    expense_destination_name: str | None = None,
) -> dict:
    payload = build_payload(txn, category_name, budget_name, destination_account_id, tags, expense_destination_name)
    return client.create_transaction(payload)
