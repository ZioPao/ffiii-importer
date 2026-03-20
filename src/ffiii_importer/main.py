from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from .config.loader import load_bank_mappings, load_settings
from .firefly.client import FireflyClient
from .pipeline.runner import run_import


def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logging.getLogger("ffiii_importer").addHandler(handler)
    logging.getLogger("ffiii_importer").setLevel(logging.INFO)

app = typer.Typer(
    name="ffiii-importer",
    help="Import bank CSV transactions into FireflyIII with LLM-assisted categorization.",
    no_args_is_help=True,
)
console = Console()

# Resolve config defaults relative to the project root (two levels up from this file:
# src/ffiii_importer/main.py → src/ffiii_importer → src → project root)
_PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_SETTINGS = _PROJECT_ROOT / "config" / "settings.yaml"
DEFAULT_MAPPINGS = _PROJECT_ROOT / "config" / "bank_mappings.yaml"


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

    _setup_logging(settings.fingerprint.db_path.parent / "import.log")

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

    stats = run_import(settings, bank_mapping, files, bank_mappings.transfers, dry_run=dry_run)
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


@app.command("list-accounts")
def list_accounts(
    config: Annotated[Path, typer.Option("--config", help="Path to settings.yaml")] = DEFAULT_SETTINGS,
) -> None:
    """List all asset accounts in FireflyIII with their IDs (use these in bank_mappings.yaml)."""
    settings = load_settings(config)

    import httpx
    console.print(f"Fetching accounts from [cyan]{settings.firefly.url}[/]...")
    try:
        with FireflyClient(settings.firefly.url, settings.firefly.token, settings.firefly.timeout_seconds) as client:
            accounts = list(client._get_all_pages("/api/v1/accounts"))
    except httpx.HTTPError as e:
        console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(1)

    # Group by type
    by_type: dict[str, list[tuple[str, str]]] = {}
    for acc in accounts:
        attrs = acc.get("attributes", {})
        acc_type = attrs.get("type", "unknown")
        acc_id = str(acc.get("id", "?"))
        acc_name = attrs.get("name", "?")
        by_type.setdefault(acc_type, []).append((acc_id, acc_name))

    for acc_type, entries in sorted(by_type.items()):
        table = Table(title=f"[bold]{acc_type}[/]")
        table.add_column("ID", style="bold cyan", width=6)
        table.add_column("Name")
        for acc_id, acc_name in sorted(entries, key=lambda x: int(x[0]) if x[0].isdigit() else 0):
            table.add_row(acc_id, acc_name)
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


@app.command("gui")
def launch_gui(
    config: Annotated[Path, typer.Option("--config", help="Path to settings.yaml")] = DEFAULT_SETTINGS,
    mappings: Annotated[Path, typer.Option("--mappings", help="Path to bank_mappings.yaml")] = DEFAULT_MAPPINGS,
) -> None:
    """Launch the interactive TUI."""
    from .gui.app import ImporterApp
    ImporterApp(config_path=config, mappings_path=mappings).run()
