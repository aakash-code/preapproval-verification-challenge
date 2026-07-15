"""Logging setup shared by the CLI and web app.

`setup_logging` is idempotent: it attaches a rotating file handler
(logs/app.log) plus a plain console handler to the ``preapproval`` logger once,
no matter how many times it is called. Web jobs additionally attach a
``JobLogHandler`` that buffers formatted lines for the progress page.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOGS_DIR = REPO_ROOT / "logs"

_ROOT_LOGGER_NAME = "preapproval"
_FILE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_CONSOLE_FORMAT = "%(message)s"


def setup_logging(logs_dir: Path = DEFAULT_LOGS_DIR) -> logging.Logger:
    """Configure and return the ``preapproval`` logger. Safe to call repeatedly."""
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_file = logs_dir / "app.log"

    have_file = any(
        isinstance(h, RotatingFileHandler)
        and Path(getattr(h, "baseFilename", "")) == log_file.resolve()
        for h in logger.handlers
    )
    if not have_file:
        file_handler = RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)

    have_console = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
        for h in logger.handlers
    )
    if not have_console:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
        console.setLevel(logging.INFO)
        logger.addHandler(console)

    return logger


class JobLogHandler(logging.Handler):
    """A handler that appends formatted records to a list (for web jobs)."""

    def __init__(self, sink: list[str]):
        super().__init__()
        self.sink = sink
        self.setFormatter(logging.Formatter(_CONSOLE_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.append(self.format(record))
        except Exception:  # pragma: no cover - never let logging crash a job
            self.handleError(record)
