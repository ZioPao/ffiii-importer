from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from ..config.models import AppSettings, BankMapping
from ..csv_reader.normalizer import parse_csv
from ..fingerprint import store as fp_store
from ..firefly.client import FireflyClient
from ..firefly.service import fetch_budgets, fetch_categories, push_transaction
from ..ollama.categorizer import Categorizer

console = Console()


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


def run_import(
    settings: AppSettings,
    bank_mapping: BankMapping,
    csv_files: list[Path],
    dry_run: bool = False,
) -> ImportStats:
    stats = ImportStats()
    effective_dry_run = dry_run or settings.import_.dry_run

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
        except httpx.HTTPError as e:
            raise SystemExit(f"Failed to fetch data from FireflyIII: {e}")

    console.print(
        f"  Loaded [cyan]{len(categories)}[/] categories, [cyan]{len(budgets)}[/] budgets."
    )

    # 3. Build Ollama categorizer (loaded once with the full category/budget lists)
    console.print(f"[bold]Initializing Ollama[/] (model: {settings.ollama.model})...")
    categorizer = Categorizer(settings.ollama, categories, budgets)

    # 4. Process each CSV file
    for csv_file in csv_files:
        console.rule(f"[bold]{csv_file.name}[/]")
        try:
            transactions = list(parse_csv(csv_file, bank_mapping))
        except ValueError as e:
            console.print(f"[red]Parse error:[/] {e}")
            stats.failed += 1
            continue

        stats.total += len(transactions)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            console=console,
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
                    if fp_store.is_duplicate(db_conn, txn.fingerprint):
                        console.print(
                            f"  [yellow]SKIP[/] duplicate: {txn.date} | {txn.description[:50]}"
                        )
                        stats.skipped_duplicate += 1
                        progress.advance(task)
                        continue

                    # LLM categorization
                    result = categorizer.categorize(txn)

                    # Skip if category is required but unknown
                    if settings.import_.skip_unknown_category and result.category is None:
                        console.print(
                            f"  [yellow]SKIP[/] no category: {txn.date} | {txn.description[:50]}"
                        )
                        stats.skipped_unknown_category += 1
                        progress.advance(task)
                        continue

                    category_label = f"[green]{result.category}[/]" if result.category else "[dim]none[/]"
                    budget_label = f"[blue]{result.budget}[/]" if result.budget else "[dim]none[/]"

                    if effective_dry_run:
                        console.print(
                            f"  [cyan]DRY[/] {txn.date} | {txn.amount:>10} | "
                            f"{txn.description[:40]} → cat={category_label} bud={budget_label}"
                        )
                        stats.dry_run += 1
                        progress.advance(task)
                        continue

                    # Push to Firefly
                    try:
                        push_transaction(firefly, txn, result.category, result.budget)
                        fp_store.record(db_conn, txn.fingerprint, txn.source_account_id, txn.description)
                        console.print(
                            f"  [green]OK[/]  {txn.date} | {txn.amount:>10} | "
                            f"{txn.description[:40]} → cat={category_label} bud={budget_label}"
                        )
                        stats.imported += 1
                    except httpx.HTTPStatusError as e:
                        console.print(
                            f"  [red]FAIL[/] {txn.date} | {txn.description[:40]} — "
                            f"HTTP {e.response.status_code}: {e.response.text[:120]}"
                        )
                        stats.failed += 1

                    progress.advance(task)

    db_conn.close()
    return stats
