"""Typer CLI for qvd-mcp."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from qvd_mcp import __version__
from qvd_mcp.config import Config, ConfigError
from qvd_mcp.config import load as load_config
from qvd_mcp.convert import run_once
from qvd_mcp.logging_setup import configure_cli, configure_server

app = typer.Typer(
    name="qvd-mcp",
    help="Query QVD files directly from disk with SQL and AI.",
    no_args_is_help=True,
    add_completion=False,
)
_out = Console()
_err = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if not value:
        return
    try:
        pyqvd_v = pkg_version("pyqvd")
    except PackageNotFoundError:
        pyqvd_v = "?"
    _out.print(f"qvd-mcp {__version__} (pyqvd {pyqvd_v})")
    raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Print version and exit."),
    ] = None,
) -> None:
    """qvd-mcp: query QVD files with SQL via MCP."""


def _load_or_exit(
    source: Path | None, cache: Path | None, log_level: str | None
) -> Config:
    try:
        return load_config(
            source_override=source,
            cache_override=cache,
            log_level_override=log_level,
        )
    except ConfigError as exc:
        _err.print(f"[bold red]config error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc


@app.command()
def convert(
    source: Annotated[
        Path | None,
        typer.Option("--source", help="Directory of QVDs. Overrides config.source_dir."),
    ] = None,
    cache: Annotated[
        Path | None,
        typer.Option("--cache", help="Parquet cache directory. Overrides config.cache_dir."),
    ] = None,
    log_level: Annotated[str, typer.Option("--log-level")] = "INFO",
) -> None:
    """Run one QVD → Parquet conversion pass, print a summary, exit."""
    config = _load_or_exit(source, cache, log_level)
    configure_cli(config.log_level, config.log_dir)
    report = run_once(config)

    table = Table(title="Conversion report", title_style="bold")
    table.add_column("Outcome", style="cyan")
    table.add_column("Count", justify="right")
    table.add_row("converted", str(len(report.converted)))
    table.add_row("skipped", str(len(report.skipped)))
    table.add_row("failed", str(len(report.failed)))
    table.add_row("pruned", str(len(report.pruned)))
    _out.print(table)

    if report.failed:
        _err.print("[yellow]failures:[/yellow]")
        for src, reason in report.failed:
            _err.print(f"  {src}: {reason}")
        raise typer.Exit(code=1)


@app.command()
def serve(
    source: Annotated[Path | None, typer.Option("--source")] = None,
    cache: Annotated[Path | None, typer.Option("--cache")] = None,
    log_level: Annotated[str, typer.Option("--log-level")] = "INFO",
) -> None:
    """Run the MCP server over stdio until the client disconnects.

    Stdout is reserved for the JSON-RPC stream; human output goes to stderr.
    We also opt out of FastMCP's banner and PyPI update check so startup
    stays quiet and offline — the tool should behave the same with or
    without a network connection.
    """
    import os

    os.environ.setdefault("FASTMCP_SHOW_CLI_BANNER", "false")
    os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")

    config = _load_or_exit(source, cache, log_level)
    configure_server(config.log_level, config.log_dir)
    from qvd_mcp.server import serve as _serve

    _serve(config)
