"""Logging setup for CLI and server modes.

Server mode is the sensitive one: MCP stdio reserves **stdout** for JSON-RPC
frames. Nothing in the server process may write to stdout or the client
disconnects with a cryptic parse error. We can't safely redirect stdout
(doing so would break the MCP transport itself), so the contract here is:
all our logging goes to stderr, and we rely on FastMCP's own discipline
about keeping its banner/update output on stderr.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s %(message)s"


def _file_handler(log_dir: Path) -> RotatingFileHandler:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "qvd-mcp.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    return handler


def configure_cli(level: str, log_dir: Path) -> None:
    """Rich stderr output plus a rotating file log. Safe for interactive use."""
    from rich.logging import RichHandler

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    rich = RichHandler(
        show_path=False,
        show_time=False,
        markup=False,
        rich_tracebacks=True,
    )
    rich.setLevel(level)
    root.addHandler(rich)
    root.addHandler(_file_handler(log_dir))


def configure_server(level: str, log_dir: Path) -> None:
    """Stderr-only logging for the MCP server.

    No stdout redirect — the MCP stdio transport needs the real stdout for
    JSON-RPC. Our contract: we only call ``logging`` (stderr), and we rely
    on FastMCP to keep its own banner/update output on stderr.
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    stderr_handler.setLevel(level)
    root.addHandler(stderr_handler)
    root.addHandler(_file_handler(log_dir))
