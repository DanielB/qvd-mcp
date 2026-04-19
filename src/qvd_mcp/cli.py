"""Typer CLI for qvd-mcp."""
from __future__ import annotations

import shutil
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from qvd_mcp import __version__, claude_config
from qvd_mcp import doctor as _doctor
from qvd_mcp.config import Config, ConfigError, default_cache_dir
from qvd_mcp.config import load as load_config
from qvd_mcp.convert import run_once
from qvd_mcp.logging_setup import configure_cli, configure_server
from qvd_mcp.setup_wizard import SetupError, run_setup

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
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Print version and exit.",
        ),
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


@app.command()
def setup(
    source: Annotated[
        Path | None,
        typer.Option("--source", help="Directory of QVDs. Required with --yes."),
    ] = None,
    cache: Annotated[
        Path | None,
        typer.Option("--cache", help="Parquet cache dir. Defaults to platformdirs."),
    ] = None,
    no_claude: Annotated[
        bool,
        typer.Option("--no-claude", help="Skip patching the Claude Desktop config."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Non-interactive mode. Uses CLI args and defaults."),
    ] = False,
) -> None:
    """Interactive setup: write config.toml, patch Claude Desktop, run first conversion."""
    try:
        run_setup(source=source, cache=cache, no_claude=no_claude, yes=yes)
    except SetupError as exc:
        _err.print(f"[bold red]setup failed:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc


@app.command()
def doctor(
    emoji: Annotated[
        bool,
        typer.Option("--emoji", help="Prefix status cells with emoji glyphs."),
    ] = False,
) -> None:
    """Run diagnostic checks and print a report.

    Exit code 0 if nothing failed, 1 if any fail, 2 if the config is broken.
    """
    results = _doctor.run_all()
    _out.print(_doctor.render(results, use_emoji=emoji))
    raise typer.Exit(code=_doctor.exit_code(results))


@app.command()
def uninstall(
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip confirmation prompts."),
    ] = False,
    delete_cache: Annotated[
        bool,
        typer.Option(
            "--delete-cache",
            help="Also delete the Parquet cache directory. Source QVDs are never touched.",
        ),
    ] = False,
) -> None:
    """Reverse ``setup``: remove the qvd entry from Claude Desktop, optionally drop the cache.

    Source QVD files are never touched. Without ``--yes``, interactively
    confirms each destructive step.
    """
    # Try to respect a user-set cache_dir from their config. If the config
    # can't load (no source_dir, malformed, etc.), fall back to the platform
    # default — uninstall must work even on a half-broken install.
    cache_dir = default_cache_dir()
    try:
        cfg = load_config()
        cache_dir = cfg.cache_dir
    except ConfigError:
        pass

    claude_path = claude_config.default_config_path()
    _out.print("This will:")
    _out.print(
        f"  • Remove qvd-mcp from Claude Desktop config at [bold]{claude_path}[/bold]"
    )
    if delete_cache:
        _out.print(f"  • Delete the Parquet cache at [bold]{cache_dir}[/bold]")
    else:
        _out.print(f"  • Leave the Parquet cache at [bold]{cache_dir}[/bold]")
    _out.print("  • Leave your QVD source files untouched")
    _out.print()

    # Offer the cache-delete prompt interactively only when the flag wasn't
    # given and we're not in --yes mode.
    if not yes and not delete_cache:
        delete_cache = Confirm.ask("Also delete the Parquet cache?", default=False)

    if not yes and not Confirm.ask("Continue?", default=True):
        _out.print("Aborted.")
        raise typer.Exit(code=0)

    removed = claude_config.unmerge("qvd")
    if removed:
        _out.print(f"[green]Removed qvd entry from {claude_path}[/green]")
    else:
        _out.print(
            f"[yellow]No qvd entry found in {claude_path} (nothing to do)[/yellow]"
        )

    if delete_cache and cache_dir.is_dir():
        shutil.rmtree(cache_dir)
        _out.print(f"[green]Deleted cache dir {cache_dir}[/green]")

    _out.print("\n[bold green]Uninstall complete.[/bold green]")
