from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import ImporterApp


class TextualLogHandler(logging.Handler):
    """Routes log records from ffiii_importer into the Textual GUI log panel."""

    def __init__(self, app: ImporterApp) -> None:
        super().__init__()
        self._app = app

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        level = record.levelname
        if level == "ERROR":
            markup = f"[red]{msg}[/red]"
        elif level == "WARNING":
            markup = f"[yellow]{msg}[/yellow]"
        else:
            markup = msg
        self._app.call_from_thread(self._app.append_log, markup)
