from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Footer, Header, Label, RichLog, Select, Static, Switch

from ..config.loader import load_bank_mappings, load_settings
from ..pipeline.runner import ImportStats, run_import
from .log_handler import TextualLogHandler

# src/ffiii_importer/gui/app.py → gui → ffiii_importer → src → project root
DEFAULT_CONFIG = Path(__file__).parent.parent.parent.parent / "config" / "settings.yaml"
DEFAULT_MAPPINGS = Path(__file__).parent.parent.parent.parent / "config" / "bank_mappings.yaml"

_LOGGER = logging.getLogger("ffiii_importer")


# ── Messages ──────────────────────────────────────────────────────────────────

@dataclass
class ImportDone(Message):
    stats: ImportStats | None
    error: Exception | None = None


# ── App ───────────────────────────────────────────────────────────────────────

CSS = """
Screen {
    layout: horizontal;
}

#sidebar {
    width: 36;
    min-width: 30;
    border: solid $primary;
    padding: 1 2;
}

#sidebar Label {
    margin-top: 1;
    color: $text-muted;
}

#sidebar Select {
    margin-bottom: 1;
}

#files-list {
    height: auto;
    max-height: 8;
    border: solid $panel;
    padding: 0 1;
    margin-bottom: 1;
}

#file-path-input {
    width: 1fr;
}

#btn-browse {
    width: 10;
}

#file-row {
    height: 3;
    margin-bottom: 0;
}

#dry-run-row {
    height: 3;
    align: left middle;
    margin-bottom: 1;
}

#dry-run-row Label {
    margin: 0 1 0 0;
    color: $text;
}

#btn-run {
    width: 1fr;
    margin-top: 1;
}

#log-panel {
    border: solid $primary;
    padding: 1 2;
    width: 1fr;
}

#stats-bar {
    height: 3;
    border: solid $panel;
    padding: 0 2;
    content-align: left middle;
    color: $text-muted;
}
"""


class ImporterApp(App):
    TITLE = "ffiii-importer"
    CSS = CSS
    BINDINGS = [("ctrl+c", "quit", "Quit"), ("ctrl+q", "quit", "Quit")]

    def __init__(self, config_path: Path = DEFAULT_CONFIG, mappings_path: Path = DEFAULT_MAPPINGS) -> None:
        super().__init__()
        self._config_path = config_path
        self._mappings_path = mappings_path
        self._selected_files: list[Path] = []
        self._log_handler: TextualLogHandler | None = None

    # ── Layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("Bank")
                yield Select(options=[], id="bank-select", prompt="Select bank...")
                yield Label("CSV files")
                with VerticalScroll(id="files-list"):
                    yield Static("(none)", id="files-display")
                with Horizontal(id="file-row"):
                    yield Button("Browse", id="btn-browse", variant="default")
                yield Label("Dry run")
                with Horizontal(id="dry-run-row"):
                    yield Switch(id="dry-run-switch")
                    yield Label("Preview only")
                yield Button("Run Import", id="btn-run", variant="primary")
            with Vertical(id="log-panel"):
                yield RichLog(id="log", highlight=True, markup=True, wrap=True)
        yield Static("Ready.", id="stats-bar")
        yield Footer()

    def on_mount(self) -> None:
        try:
            mappings = load_bank_mappings(self._mappings_path)
            options = [(key, key) for key in mappings.banks]
            self.query_one("#bank-select", Select).set_options(options)
        except SystemExit as e:
            self.append_log(f"[red]Failed to load bank mappings: {e}[/red]")

    # ── File selection ────────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-browse")
    def handle_browse(self) -> None:
        self.run_worker(self._pick_files, thread=True)

    def _pick_files(self) -> None:
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            paths = filedialog.askopenfilenames(
                title="Select CSV files",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            )
            root.destroy()
            if paths:
                self.call_from_thread(self._add_files, [Path(p) for p in paths])
        except Exception:
            # Tk unavailable — user can type paths manually (future enhancement)
            self.call_from_thread(
                self.append_log,
                "[yellow]File dialog unavailable. Place CSV paths in the input manually.[/yellow]",
            )

    def _add_files(self, paths: list[Path]) -> None:
        for p in paths:
            if p not in self._selected_files:
                self._selected_files.append(p)
        self._refresh_files_display()

    def _refresh_files_display(self) -> None:
        display = self.query_one("#files-display", Static)
        if not self._selected_files:
            display.update("(none)")
        else:
            display.update("\n".join(f.name for f in self._selected_files))

    # ── Import ────────────────────────────────────────────────────────────────

    @on(Button.Pressed, "#btn-run")
    def handle_run(self) -> None:
        bank_select = self.query_one("#bank-select", Select)
        if bank_select.value is Select.BLANK:
            self.append_log("[yellow]Select a bank first.[/yellow]")
            return
        if not self._selected_files:
            self.append_log("[yellow]Add at least one CSV file.[/yellow]")
            return

        self.query_one("#btn-run", Button).disabled = True
        self.query_one("#stats-bar", Static).update("Running...")
        self.query_one("#log", RichLog).clear()

        dry_run = self.query_one("#dry-run-switch", Switch).value
        bank_key = str(bank_select.value)

        self._attach_log_handler()
        self.run_worker(
            lambda: self._do_import(bank_key, list(self._selected_files), dry_run),
            thread=True,
            exclusive=True,
        )

    def _do_import(self, bank_key: str, csv_files: list[Path], dry_run: bool) -> None:
        try:
            settings = load_settings(self._config_path)
            mappings = load_bank_mappings(self._mappings_path)
            bank_mapping = mappings.banks[bank_key]
            stats = run_import(
                settings,
                bank_mapping,
                csv_files,
                transfer_rules=mappings.transfers,
                dry_run=dry_run,
                progress_enabled=False,
            )
            self.call_from_thread(self.post_message, ImportDone(stats=stats))
        except Exception as exc:
            self.call_from_thread(self.post_message, ImportDone(stats=None, error=exc))

    def on_import_done(self, msg: ImportDone) -> None:
        self._detach_log_handler()
        self.query_one("#btn-run", Button).disabled = False

        if msg.error:
            self.append_log(f"[red]Import failed: {msg.error}[/red]")
            self.query_one("#stats-bar", Static).update(f"Error: {msg.error}")
            return

        s = msg.stats
        assert s is not None
        summary = (
            f"Done — imported: {s.imported}  dry: {s.dry_run}  "
            f"dup: {s.skipped_duplicate}  no-cat: {s.skipped_unknown_category}  failed: {s.failed}"
        )
        self.query_one("#stats-bar", Static).update(summary)

    # ── Log helpers ───────────────────────────────────────────────────────────

    def append_log(self, markup: str) -> None:
        self.query_one("#log", RichLog).write(markup)

    def _attach_log_handler(self) -> None:
        handler = TextualLogHandler(self)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        _LOGGER.addHandler(handler)
        _LOGGER.setLevel(logging.INFO)
        self._log_handler = handler

    def _detach_log_handler(self) -> None:
        if self._log_handler:
            _LOGGER.removeHandler(self._log_handler)
            self._log_handler = None
