from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import httpx
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ..config.models import AppSettings, BankMapping, TransferRule
from ..csv_reader.models import RawTransaction
from ..csv_reader.normalizer import parse_csv
from ..fingerprint import store as fp_store
from ..firefly.client import FireflyClient
from ..firefly.service import fetch_asset_accounts, fetch_budgets, fetch_categories, fetch_expense_accounts, fetch_tags, push_transaction
from ..ollama.categorizer import Categorizer

console = Console()
log = logging.getLogger("ffiii_importer")


class ImportStats:
    def __init__(self) -> None:
        self.total = 0
        self.imported = 0
        self.skipped_duplicate = 0
        self.skipped_unknown_category = 0
        self.failed = 0
        self.dry_run = 0

    def print_summary(self) -> None:
        table = Table(title="Import Summary", show_header=False)
        table.add_column("", style="bold")
        table.add_column("")
        table.add_row("Total transactions", str(self.total))
        table.add_row("Imported", f"[green]{self.imported}[/]")
        table.add_row("Dry run (not pushed)", f"[cyan]{self.dry_run}[/]")
        table.add_row("Skipped (duplicate)", f"[yellow]{self.skipped_duplicate}[/]")
        table.add_row("Skipped (unknown category)", f"[yellow]{self.skipped_unknown_category}[/]")
        table.add_row("Failed", f"[red]{self.failed}[/]")
        console.print(table)


def _find_transfer_destination(
    txn: RawTransaction,
    iban_to_account: dict[str, str],
    transfer_rules: list[TransferRule],
) -> str | None:
    """Return destination account_id if the transaction is an internal transfer.

    Detection order:
    1. IBAN scan: if any known asset-account IBAN appears in description or notes,
       it's a transfer to that account (skip source account's own IBAN).
    2. Pattern rules: fallback for banks that don't embed IBANs in descriptions.
    """
    haystack = f"{txn.description} {txn.notes or ''}".upper()
    for iban, account_id in iban_to_account.items():
        if account_id == txn.source_account_id:
            continue
        if iban in haystack:
            return account_id

    for rule in transfer_rules:
        if rule.from_account_id and rule.from_account_id != txn.source_account_id:
            continue
        if re.search(rule.pattern, txn.description, re.IGNORECASE):
            return rule.to_account_id

    return None


def run_import(
    settings: AppSettings,
    bank_mapping: BankMapping,
    csv_files: list[Path],
    transfer_rules: list[TransferRule] | None = None,
    dry_run: bool = False,
    progress_enabled: bool = True,
    skip_dedup: bool = False,
) -> ImportStats:
    stats = ImportStats()
    effective_dry_run = dry_run or settings.import_.dry_run
    _transfer_rules = transfer_rules or []

    file_names = ", ".join(f.name for f in csv_files)
    log.info("=== Import session started | bank=%s files=[%s] dry_run=%s skip_dedup=%s ===", bank_mapping.account_id, file_names, effective_dry_run, skip_dedup)

    # 1. Open fingerprint DB
    db_conn = fp_store.open_db(settings.fingerprint.db_path)

    # 2. Connect to Firefly and fetch reference data
    console.print("[bold]Connecting to FireflyIII...[/]")
    with FireflyClient(
        settings.firefly.url,
        settings.firefly.token,
        settings.firefly.timeout_seconds,
    ) as firefly:
        try:
            categories = fetch_categories(firefly)
            budgets = fetch_budgets(firefly)
            tags = fetch_tags(firefly)
            expense_accounts = fetch_expense_accounts(firefly)
            iban_to_account = fetch_asset_accounts(firefly)
        except httpx.HTTPError as e:
            log.error("Failed to fetch data from FireflyIII: %s", e)
            raise SystemExit(f"Failed to fetch data from FireflyIII: {e}")

    console.print(
        f"  Loaded [cyan]{len(categories)}[/] categories, [cyan]{len(budgets)}[/] budgets, "
        f"[cyan]{len(tags)}[/] tags, [cyan]{len(expense_accounts)}[/] expense accounts, "
        f"[cyan]{len(iban_to_account)}[/] accounts with IBAN."
    )
    log.info("Loaded %d categories, %d budgets, %d tags, %d expense accounts, %d IBANs",
             len(categories), len(budgets), len(tags), len(expense_accounts), len(iban_to_account))

    # 3. Build Ollama categorizer (loaded once with the full category/budget/tag/account lists)
    console.print(f"[bold]Initializing Ollama[/] (model: {settings.ollama.model})...")
    categorizer = Categorizer(settings.ollama, categories, budgets, tags, expense_accounts)

    # 4. Process each CSV file
    for csv_file in csv_files:
        console.rule(f"[bold]{csv_file.name}[/]")
        log.info("--- Processing file: %s ---", csv_file.name)
        try:
            transactions = list(parse_csv(csv_file, bank_mapping))
        except ValueError as e:
            console.print(f"[red]Parse error:[/] {e}")
            log.error("Parse error in %s: %s", csv_file.name, e)
            stats.failed += 1
            continue

        stats.total += len(transactions)
        log.info("Parsed %d transactions from %s", len(transactions), csv_file.name)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
            disable=not progress_enabled or settings.ollama.verbose,
        ) as progress:
            task = progress.add_task("Processing...", total=len(transactions))

            with FireflyClient(
                settings.firefly.url,
                settings.firefly.token,
                settings.firefly.timeout_seconds,
            ) as firefly:
                for txn in transactions:
                    progress.update(task, description=txn.description[:50])

                    # Dedup check
                    if not skip_dedup and fp_store.is_duplicate(db_conn, txn.fingerprint):
                        console.print(
                            f"  [yellow]SKIP[/] duplicate: {txn.date} | {txn.description[:50]}"
                        )
                        log.info("SKIP duplicate | %s | %s | %s", txn.date, txn.amount, txn.description)
                        stats.skipped_duplicate += 1
                        progress.advance(task)
                        continue

                    # Transfer detection (bypasses LLM — transfers have no category/budget)
                    destination_account_id = _find_transfer_destination(txn, iban_to_account, _transfer_rules)
                    if destination_account_id is not None:
                        label = f"[magenta]transfer → account {destination_account_id}[/]"
                        if effective_dry_run:
                            console.print(
                                f"  [cyan]DRY[/] {txn.date} | {txn.amount:>10} | "
                                f"{txn.description[:40]} → {label}"
                            )
                            log.info("DRY transfer | %s | %s | %s | -> account %s", txn.date, txn.amount, txn.description, destination_account_id)
                            stats.dry_run += 1
                            progress.advance(task)
                            continue
                        try:
                            push_transaction(firefly, txn, None, None, destination_account_id)
                            fp_store.record(db_conn, txn.fingerprint, txn.source_account_id, txn.description)
                            console.print(
                                f"  [green]OK[/]  {txn.date} | {txn.amount:>10} | "
                                f"{txn.description[:40]} → {label}"
                            )
                            log.info("OK transfer | %s | %s | %s | -> account %s", txn.date, txn.amount, txn.description, destination_account_id)
                            stats.imported += 1
                        except httpx.HTTPStatusError as e:
                            console.print(
                                f"  [red]FAIL[/] {txn.date} | {txn.description[:40]} — "
                                f"HTTP {e.response.status_code}: {e.response.text[:120]}"
                            )
                            log.error("FAIL transfer | %s | %s | %s | HTTP %s: %s", txn.date, txn.amount, txn.description, e.response.status_code, e.response.text[:200])
                            stats.failed += 1
                        progress.advance(task)
                        continue

                    # LLM categorization
                    result = categorizer.categorize(txn)
                    if result.category is None and result.budget is None and result.confidence == 0.0:
                        console.print(
                            f"  [red]LLM[/] fallback (no valid response): "
                            f"{txn.date} | {txn.description[:50]}"
                        )
                        log.warning("LLM fallback | %s | %s | %s", txn.date, txn.amount, txn.description)

                    # Skip if category is required but unknown
                    if settings.import_.skip_unknown_category and result.category is None:
                        console.print(
                            f"  [yellow]SKIP[/] no category: {txn.date} | {txn.description[:50]}"
                        )
                        log.info("SKIP no-category | %s | %s | %s", txn.date, txn.amount, txn.description)
                        stats.skipped_unknown_category += 1
                        progress.advance(task)
                        continue

                    # Resolve destination account name (Cash fallback for withdrawals)
                    expense_dest_name: str | None = None
                    expense_dest_label: str = "[dim]none[/]"
                    if txn.amount < 0:
                        dest_name = result.destination_account or "Cash"
                        expense_dest_name = dest_name if dest_name in expense_accounts else "Cash"
                        expense_dest_label = f"[yellow]{expense_dest_name}[/]"

                    category_label = f"[green]{result.category}[/]" if result.category else "[dim]none[/]"
                    budget_label = f"[blue]{result.budget}[/]" if result.budget else "[dim]none[/]"
                    tags_label = f"[magenta]{', '.join(result.tags)}[/]" if result.tags else "[dim]none[/]"

                    if effective_dry_run:
                        console.print(
                            f"  [cyan]DRY[/] {txn.date} | {txn.amount:>10} | "
                            f"{txn.description[:40]} → cat={category_label} bud={budget_label} "
                            f"tags={tags_label} dest={expense_dest_label}"
                        )
                        log.info("DRY | %s | %s | %s | cat=%s bud=%s tags=%s", txn.date, txn.amount, txn.description, result.category, result.budget, result.tags)
                        stats.dry_run += 1
                        progress.advance(task)
                        continue

                    # Push to Firefly
                    try:
                        push_transaction(firefly, txn, result.category, result.budget, tags=result.tags or None, expense_destination_name=expense_dest_name)
                        fp_store.record(db_conn, txn.fingerprint, txn.source_account_id, txn.description)
                        console.print(
                            f"  [green]OK[/]  {txn.date} | {txn.amount:>10} | "
                            f"{txn.description[:40]} → cat={category_label} bud={budget_label} "
                            f"tags={tags_label} dest={expense_dest_label}"
                        )
                        log.info("OK | %s | %s | %s | cat=%s bud=%s tags=%s", txn.date, txn.amount, txn.description, result.category, result.budget, result.tags)
                        stats.imported += 1
                    except httpx.HTTPStatusError as e:
                        console.print(
                            f"  [red]FAIL[/] {txn.date} | {txn.description[:40]} — "
                            f"HTTP {e.response.status_code}: {e.response.text[:120]}"
                        )
                        log.error("FAIL | %s | %s | %s | HTTP %s: %s", txn.date, txn.amount, txn.description, e.response.status_code, e.response.text[:200])
                        stats.failed += 1

                    progress.advance(task)

    db_conn.close()
    log.info(
        "=== Session complete | total=%d imported=%d dry_run=%d skipped_dup=%d skipped_cat=%d failed=%d ===",
        stats.total, stats.imported, stats.dry_run, stats.skipped_duplicate, stats.skipped_unknown_category, stats.failed,
    )
    return stats
