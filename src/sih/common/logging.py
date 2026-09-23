"""Pretty, colorized console logging setup for the SIH edge pipeline."""

from __future__ import annotations

import logging
import os
import sys


class PrettyLogFormatter(logging.Formatter):
    """Clean, formatted ANSI color log formatter for terminal output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    COLORS = {
        logging.DEBUG: "\033[36m",     # Cyan
        logging.INFO: "\033[32m",      # Green
        logging.WARNING: "\033[33m",   # Yellow
        logging.ERROR: "\033[31m",     # Red
        logging.CRITICAL: "\033[35m",  # Magenta
    }

    LEVEL_LABELS = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO ",
        logging.WARNING: "WARN ",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "FATAL",
    }

    def __init__(self, use_colors: bool | None = None) -> None:
        super().__init__()
        if use_colors is None:
            no_color = bool(os.environ.get("NO_COLOR"))
            is_tty = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
            self.use_colors = is_tty and not no_color
        else:
            self.use_colors = use_colors

    def format(self, record: logging.LogRecord) -> str:
        record_time = self.formatTime(record, "%H:%M:%S")
        level_label = self.LEVEL_LABELS.get(record.levelno, record.levelname[:5].upper())
        msg = record.getMessage()

        # Simplify logger name, e.g. "sih.egress.mqtt_client" -> "mqtt"
        name = record.name
        if name.startswith("sih."):
            name = name[4:]
        if "." in name:
            name = name.split(".")[-1]

        if self.use_colors:
            color = self.COLORS.get(record.levelno, self.RESET)
            formatted = (
                f"{self.DIM}{record_time}{self.RESET} "
                f"{color}{self.BOLD}[{level_label}]{self.RESET} "
                f"{self.BOLD}[{name}]{self.RESET} {msg}"
            )
        else:
            formatted = f"{record_time} [{level_label}] [{name}] {msg}"

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            if record.exc_text:
                formatted = f"{formatted}\n{record.exc_text}"

        return formatted


def setup_logging(verbose: bool = False, level: int | None = None) -> None:
    """Configure pretty root logging."""
    if level is None:
        log_level = logging.DEBUG if verbose else logging.INFO
    else:
        log_level = level

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(PrettyLogFormatter())

    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    root.addHandler(handler)
