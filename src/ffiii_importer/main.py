from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from .config.loader import load_bank_mappings, load_settings
from .firefly.client import FireflyClient
from .pipeline.runner import run_import

app = typer.Typer(
    name="ffiii-importer",
    help="Import bank CSV transactions into FireflyIII with LLM-assisted categorization.",
    no_args_is_help=True,
)
console = Console()

DEFAULT_SETTINGS = Path("config/settings.yaml")
DEFAULT_MAPPINGS = Path("config/bank_mappings.yaml")


@app.command()
def import_csv(
    bank: Annotated[str, typer.Option("--bank", help="Bank key from bank_mappings.yaml")],
    files: Annotated[list[Path], typer.Option("--file", "-f", help="CSV file(s) to import")],
    config: Annotated[Path, typer.Option("--config", help="Path to settings.yaml")] = DEFAULT_SETTINGS,
    mappings: Annotated[Path, typer.Option("--mappings", help="Path to bank_mappings.yaml")] = DEFAULT_MAPPINGS,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without pushing to Firefly")] = False,
) -> None:
    """Import one or more bank CSV files into FireflyIII."""
    settings = load_settings(config)
    bank_mappings = load_bank_mappings(mappings)

    if bank not in bank_mappings.banks:
        available = ", ".join(bank_mappings.banks.keys())
        raise typer.BadParameter(
            f"Bank '{bank}' not found. Available banks: {available}",
            param_hint="--bank",
        )

    bank_mapping = bank_mappings.banks[bank]

    missing = [f for f in files if not f.exists()]
    if missing:
        for f in missing:
            console.print(f"[red]File not found:[/] {f}")
        raise typer.Exit(1)

    stats = run_import(settings, bank_mapping, files, dry_run=dry_run)
    stats.print_summary()

    if stats.failed > 0:
        raise typer.Exit(1)


@app.command("list-banks")
def list_banks(
    mappings: Annotated[Path, typer.Option("--mappings", help="Path to bank_mappings.yaml")] = DEFAULT_MAPPINGS,
) -> None:
    """List all configured bank keys."""
    bank_mappings = load_bank_mappings(mappings)

    table = Table(title="Configured Banks")
    table.add_column("Key", style="bold cyan")
    table.add_column("Account ID")
    table.add_column("Format")
    table.add_column("Date format")

    for key, bm in bank_mappings.banks.items():
        table.add_row(key, bm.account_id, bm.amount_column_type, bm.date_format)

    console.print(table)


@app.command("check-config")
def check_config(
    config: Annotated[Path, typer.Option("--config", help="Path to settings.yaml")] = DEFAULT_SETTINGS,
    mappings: Annotated[Path, typer.Option("--mappings", help="Path to bank_mappings.yaml")] = DEFAULT_MAPPINGS,
) -> None:
    """Validate configuration and test connectivity to FireflyIII and Ollama."""
    settings = load_settings(config)
    bank_mappings = load_bank_mappings(mappings)

    console.print("[green]✓[/] Configuration files are valid.")
    console.print(f"  Banks configured: {list(bank_mappings.banks.keys())}")

    # Test Firefly connectivity
    console.print(f"\nTesting FireflyIII at [cyan]{settings.firefly.url}[/]...")
    with FireflyClient(settings.firefly.url, settings.firefly.token, settings.firefly.timeout_seconds) as client:
        if client.ping():
            console.print("[green]✓[/] FireflyIII connection successful.")
        else:
            console.print("[red]✗[/] FireflyIII connection failed. Check URL and token.")

    # Test Ollama connectivity
    console.print(f"\nTesting Ollama at [cyan]{settings.ollama.url}[/] (model: {settings.ollama.model})...")
    import httpx
    try:
        resp = httpx.get(f"{settings.ollama.url}/api/tags", timeout=10)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        if settings.ollama.model in models or any(settings.ollama.model in m for m in models):
            console.print(f"[green]✓[/] Ollama connection successful. Model '{settings.ollama.model}' available.")
        else:
            console.print(
                f"[yellow]![/] Ollama reachable but model '{settings.ollama.model}' not found in: {models}"
            )
    except httpx.HTTPError as e:
        console.print(f"[red]✗[/] Ollama connection failed: {e}")
