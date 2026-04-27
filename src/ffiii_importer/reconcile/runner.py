from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import ollama
from rich.console import Console
from rich.table import Table

from ..config.models import AppSettings, BankMapping
from ..csv_reader.models import RawTransaction
from ..csv_reader.normalizer import parse_csv
from ..firefly.client import FireflyClient
from ..firefly.service import fetch_firefly_transactions

console = Console()
log = logging.getLogger("ffiii_importer")

_LLM_SYSTEM = (
    "You are reconciling bank transactions. "
    "Descriptions often differ between the bank CSV (verbose, bank-generated) and Firefly III (shorter, manually entered). "
    "Focus on date proximity and exact amount match when judging. "
    "Respond ONLY with a JSON object, no extra text."
)

_LLM_USER = """\
CSV transaction:
- Date: {date}
- Amount: {amount:+.2f}
- Description: "{description}"

Firefly candidates (same or nearby date, same amount):
{candidates}

Does any candidate correspond to the CSV transaction?
{{"match": true, "index": <1-based index>}} or {{"match": false, "index": null}}"""


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


def _exact_match(
    csv_txns: list[RawTransaction],
    firefly_txns: list[dict],
) -> tuple[list[RawTransaction], list[dict], int]:
    """Match by (date, amount). Returns (unmatched_csv, unmatched_firefly, matched_count)."""
    matched_ids: set[int] = set()
    unmatched_csv: list[RawTransaction] = []
    matched_count = 0

    # Group firefly by (date, amount) for fast lookup
    ff_index: dict[tuple, list[tuple[int, dict]]] = {}
    for i, ft in enumerate(firefly_txns):
        try:
            key = (ft["date"], Decimal(str(ft["amount"])))
        except Exception:
            continue
        ff_index.setdefault(key, []).append((i, ft))

    for txn in csv_txns:
        key = (txn.date.isoformat(), txn.amount)
        for i, ft in ff_index.get(key, []):
            if i not in matched_ids:
                matched_ids.add(i)
                matched_count += 1
                break
        else:
            unmatched_csv.append(txn)

    unmatched_firefly = [ft for i, ft in enumerate(firefly_txns) if i not in matched_ids]
    return unmatched_csv, unmatched_firefly, matched_count


def _llm_match(
    txn: RawTransaction,
    candidates: list[dict],
    ollama_config,
) -> dict | None:
    """Ask the LLM if any candidate Firefly transaction matches the CSV transaction."""
    if not candidates:
        return None

    candidates_text = "\n".join(
        f'{i + 1}. Date: {c["date"]}, Amount: {c["amount"]:+.2f}, Description: "{c["description"]}"'
        for i, c in enumerate(candidates)
    )
    user_msg = _LLM_USER.format(
        date=txn.date,
        amount=txn.amount,
        description=txn.description,
        candidates=candidates_text,
    )

    try:
        client = ollama.Client(host=ollama_config.url, timeout=ollama_config.timeout_seconds)
        raw_text = ""
        for chunk in client.chat(
            model=ollama_config.model,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            options={"temperature": 0},
            stream=True,
        ):
            raw_text += chunk.message.content or ""
        result = json.loads(_extract_json(raw_text))
        if result.get("match") and result.get("index"):
            idx = int(result["index"]) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
    except Exception as exc:
        log.warning("LLM match failed for '%s': %s", txn.description[:50], exc)

    return None


class ReconcileResult:
    def __init__(
        self,
        missing_in_firefly: list[RawTransaction],
        only_in_firefly: list[dict],
        matched: int,
        start: date,
        end: date,
    ) -> None:
        self.missing_in_firefly = missing_in_firefly
        self.only_in_firefly = only_in_firefly
        self.matched = matched
        self.start = start
        self.end = end

    def print_summary(self) -> None:
        console.print(
            f"\nReconciliation [cyan]{self.start}[/] → [cyan]{self.end}[/]: "
            f"[green]{self.matched} matched[/], "
            f"[red]{len(self.missing_in_firefly)} missing in Firefly[/], "
            f"[yellow]{len(self.only_in_firefly)} only in Firefly[/]"
        )

        if self.missing_in_firefly:
            table = Table(title="[red]Missing in Firefly[/] (in CSV, not imported)", show_lines=False)
            table.add_column("Date", style="dim", width=12)
            table.add_column("Amount", justify="right", width=12)
            table.add_column("Description")
            for txn in sorted(self.missing_in_firefly, key=lambda t: t.date):
                table.add_row(str(txn.date), f"{txn.amount:+.2f}", txn.description)
            console.print(table)

        if self.only_in_firefly:
            table = Table(title="[yellow]Only in Firefly[/] (not in CSV)", show_lines=False)
            table.add_column("Date", style="dim", width=12)
            table.add_column("Amount", justify="right", width=12)
            table.add_column("Description")
            for txn in sorted(self.only_in_firefly, key=lambda t: t["date"]):
                table.add_row(txn["date"], f"{txn['amount']:+.2f}", txn["description"])
            console.print(table)


def run_reconcile(
    settings: AppSettings,
    bank_mapping: BankMapping,
    csv_files: list[Path],
    start: date | None = None,
    end: date | None = None,
) -> ReconcileResult:
    # 1. Parse CSV files
    csv_transactions: list[RawTransaction] = []
    for csv_file in csv_files:
        try:
            csv_transactions.extend(parse_csv(csv_file, bank_mapping))
        except ValueError as e:
            raise SystemExit(f"Parse error in {csv_file.name}: {e}")

    if not csv_transactions:
        raise SystemExit("No transactions found in the provided CSV file(s).")

    effective_start = start or min(t.date for t in csv_transactions)
    effective_end = end or max(t.date for t in csv_transactions)

    # Filter CSV to the effective date range (matters when --start/--end are explicit)
    csv_transactions = [t for t in csv_transactions if effective_start <= t.date <= effective_end]
    if not csv_transactions:
        raise SystemExit(f"No CSV transactions found in range {effective_start} → {effective_end}.")

    log.info(
        "=== Reconcile | bank=%s start=%s end=%s txns=%d ===",
        bank_mapping.account_id, effective_start, effective_end, len(csv_transactions),
    )

    # 2. Fetch Firefly transactions for the account
    console.print(
        f"[bold]Fetching Firefly transactions[/] for account [cyan]{bank_mapping.account_id}[/] "
        f"({effective_start} → {effective_end})..."
    )
    with FireflyClient(settings.firefly.url, settings.firefly.token, settings.firefly.timeout_seconds) as firefly:
        try:
            firefly_txns = fetch_firefly_transactions(
                firefly,
                bank_mapping.account_id,
                effective_start.isoformat(),
                effective_end.isoformat(),
            )
        except httpx.HTTPError as e:
            raise SystemExit(f"Failed to fetch from FireflyIII: {e}")

    # Hard-filter Firefly results to the effective range (API may return out-of-range entries)
    firefly_txns = [
        ft for ft in firefly_txns
        if effective_start.isoformat() <= ft["date"] <= effective_end.isoformat()
    ]

    console.print(
        f"  Range [cyan]{effective_start}[/] → [cyan]{effective_end}[/] | "
        f"CSV: [cyan]{len(csv_transactions)}[/], Firefly: [cyan]{len(firefly_txns)}[/]"
    )

    # 3. Exact match by (date, amount)
    unmatched_csv, unmatched_firefly, matched_count = _exact_match(csv_transactions, firefly_txns)
    console.print(f"  Exact matches: [green]{matched_count}[/], unmatched CSV: [yellow]{len(unmatched_csv)}[/]")

    # 4. LLM fuzzy match for remaining CSV transactions (same amount, date ±1 day)
    if unmatched_csv:
        console.print(f"  [bold]LLM fuzzy match[/] for {len(unmatched_csv)} unmatched CSV transactions...")
        llm_matched_firefly_ids: set[int] = set()
        still_unmatched_csv: list[RawTransaction] = []

        for txn in unmatched_csv:
            candidates = [
                ft for i, ft in enumerate(unmatched_firefly)
                if i not in llm_matched_firefly_ids
                and ft["amount"] == txn.amount
                and abs((date.fromisoformat(ft["date"]) - txn.date).days) <= 1
            ]
            match = _llm_match(txn, candidates, settings.ollama)
            if match is not None:
                try:
                    llm_matched_firefly_ids.add(unmatched_firefly.index(match))
                except ValueError:
                    pass
                matched_count += 1
            else:
                still_unmatched_csv.append(txn)

        unmatched_firefly = [
            ft for i, ft in enumerate(unmatched_firefly)
            if i not in llm_matched_firefly_ids
        ]
        unmatched_csv = still_unmatched_csv
        console.print(f"  After LLM: [green]{matched_count} total matched[/]")

    log.info(
        "=== Reconcile done | matched=%d missing=%d only_firefly=%d ===",
        matched_count, len(unmatched_csv), len(unmatched_firefly),
    )

    return ReconcileResult(
        missing_in_firefly=unmatched_csv,
        only_in_firefly=unmatched_firefly,
        matched=matched_count,
        start=effective_start,
        end=effective_end,
    )
