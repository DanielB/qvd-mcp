"""Diagnostic checks for ``qvd-mcp doctor``.

Each check is a pure function returning a :class:`CheckResult`. The checks are
intentionally tolerant: a fresh install with no config, no cache, and no
Claude Desktop entry should produce a readable mix of ``warn`` rows rather
than a wall of ``fail`` — the ``setup`` wizard is one command away from
turning most of those yellows green.

Exit-code contract (consumed by Wave 3's CLI):

- ``0`` — no failures
- ``1`` — at least one ``fail`` that isn't the config-parse check
- ``2`` — the config-parse check itself failed (broken TOML, bad types, etc.),
  which means every downstream check had to be skipped and the user needs
  to fix the config before anything else is actionable

Library code is silent: :func:`render` returns a :class:`rich.table.Table`
for the caller to print. Nothing here writes to stdout or stderr.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import platformdirs
from rich.table import Table

from qvd_mcp import claude_config, state
from qvd_mcp.config import APP_NAME, Config, ConfigError
from qvd_mcp.config import load as load_config

log = logging.getLogger(__name__)

Status = Literal["pass", "warn", "fail"]

# Stable check names. Load-bearing: ``exit_code`` uses CONFIG_CHECK_NAME to
# decide between exit 1 and exit 2, so renaming either side independently
# would silently break the contract.
PY_CHECK_NAME = "Python version"
BINARY_CHECK_NAME = "Binary resolvable"
CONFIG_CHECK_NAME = "Config parses"
SOURCE_CHECK_NAME = "Source dir readable"
CACHE_CHECK_NAME = "Cache dir writable"
PARQUETS_CHECK_NAME = "Has parquets"
STATE_CHECK_NAME = "State file"
CLAUDE_CHECK_NAME = "Claude Desktop config"
LOGS_CHECK_NAME = "Recent logs"

# Substring that distinguishes the warn-worthy "no source_dir yet" ConfigError
# from the fail-worthy TOML-parse / type-coercion / missing-directory errors.
# See ``config.load`` for the raising sites.
_SOURCE_UNSET_MARKER = "is not set"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    message: str


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_python_version() -> CheckResult:
    """Verify the interpreter is new enough for qvd-mcp (Python >= 3.11)."""
    major, minor = sys.version_info[:2]
    version_str = f"{major}.{minor}.{sys.version_info[2]}"
    # UP036 would remove this branch because pyproject pins >= 3.11, but the
    # whole point of the diagnostic is to check it at runtime on the user's
    # actual interpreter.
    if sys.version_info >= (3, 11):  # noqa: UP036
        return CheckResult(PY_CHECK_NAME, "pass", f"Python {version_str}")
    return CheckResult(
        PY_CHECK_NAME,
        "fail",
        f"Python {version_str}; qvd-mcp requires >= 3.11",
    )


def check_binary_resolvable() -> CheckResult:
    """Locate the ``qvd-mcp`` entry point on PATH."""
    found = shutil.which("qvd-mcp")
    if found:
        return CheckResult(BINARY_CHECK_NAME, "pass", found)
    return CheckResult(
        BINARY_CHECK_NAME,
        "warn",
        "not on PATH (fine for `uv run` / dev)",
    )


def check_config_parses() -> tuple[CheckResult, Config | None]:
    """Attempt to load the TOML config.

    Returns a ``(CheckResult, Config | None)`` pair so downstream checks can
    reuse the parsed config without re-reading the file. A missing
    ``source_dir`` is a ``warn`` (user hasn't run ``setup`` yet) rather than
    a ``fail``; anything else wrong with the config is a ``fail``.
    """
    try:
        config = load_config()
    except ConfigError as exc:
        message = str(exc)
        if _SOURCE_UNSET_MARKER in message:
            return (
                CheckResult(
                    CONFIG_CHECK_NAME,
                    "warn",
                    "source_dir not set; run `qvd-mcp setup`",
                ),
                None,
            )
        return CheckResult(CONFIG_CHECK_NAME, "fail", message), None
    return (
        CheckResult(
            CONFIG_CHECK_NAME,
            "pass",
            f"source_dir={config.source_dir}",
        ),
        config,
    )


def _skipped(name: str) -> CheckResult:
    return CheckResult(name, "warn", "skipped: config unavailable")


def check_source_dir_readable(config: Config | None) -> CheckResult:
    """Confirm ``config.source_dir`` is an existing, readable directory."""
    if config is None:
        return _skipped(SOURCE_CHECK_NAME)
    path = config.source_dir
    if not path.is_dir():
        return CheckResult(
            SOURCE_CHECK_NAME,
            "fail",
            f"not a directory: {path}",
        )
    if not os.access(path, os.R_OK):
        return CheckResult(
            SOURCE_CHECK_NAME,
            "fail",
            f"not readable: {path}",
        )
    return CheckResult(SOURCE_CHECK_NAME, "pass", str(path))


def check_cache_dir_writable(config: Config | None) -> CheckResult:
    """Confirm ``config.cache_dir`` (or its parent, if missing) is writable.

    If the cache directory doesn't exist yet — the common fresh-install case —
    we create its *parent* so we can probe writability there. We deliberately
    do not create the cache dir itself: that's a ``setup`` / ``convert``
    responsibility, not a diagnostic's.
    """
    if config is None:
        return _skipped(CACHE_CHECK_NAME)
    path = config.cache_dir
    if path.is_dir():
        if os.access(path, os.W_OK):
            return CheckResult(CACHE_CHECK_NAME, "pass", str(path))
        return CheckResult(
            CACHE_CHECK_NAME,
            "fail",
            f"not writable: {path}",
        )
    # Side effect is intentional per spec: make the parent so we can probe it.
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CheckResult(
            CACHE_CHECK_NAME,
            "fail",
            f"cannot create parent {parent}: {exc}",
        )
    if os.access(parent, os.W_OK):
        return CheckResult(
            CACHE_CHECK_NAME,
            "pass",
            f"{path} (parent writable; dir will be created on first convert)",
        )
    return CheckResult(
        CACHE_CHECK_NAME,
        "fail",
        f"parent not writable: {parent}",
    )


def check_has_parquets(config: Config | None) -> CheckResult:
    """Look for at least one ``*.parquet`` file under the cache directory."""
    if config is None:
        return _skipped(PARQUETS_CHECK_NAME)
    cache = config.cache_dir
    if not cache.is_dir():
        return CheckResult(
            PARQUETS_CHECK_NAME,
            "warn",
            "no parquets yet; run `qvd-mcp convert`",
        )
    try:
        first = next(cache.glob("*.parquet"), None)
    except OSError as exc:
        return CheckResult(
            PARQUETS_CHECK_NAME,
            "fail",
            f"could not scan {cache}: {exc}",
        )
    if first is None:
        return CheckResult(
            PARQUETS_CHECK_NAME,
            "warn",
            "no parquets yet; run `qvd-mcp convert`",
        )
    count = sum(1 for _ in cache.glob("*.parquet"))
    return CheckResult(
        PARQUETS_CHECK_NAME,
        "pass",
        f"{count} parquet file(s) in {cache}",
    )


def check_state_file(config: Config | None) -> CheckResult:
    """Validate the cache's state file.

    ``state.load`` normalises missing / corrupt / wrong-version files to an
    empty :class:`~qvd_mcp.state.State`, so an empty ``entries`` dict is the
    pragmatic "something's off" signal. A populated ``entries`` means the
    file was present, parsed, and matched ``SCHEMA_VERSION``.
    """
    if config is None:
        return _skipped(STATE_CHECK_NAME)
    cache = config.cache_dir
    if not cache.is_dir():
        return CheckResult(
            STATE_CHECK_NAME,
            "warn",
            "state file missing; cache not populated yet",
        )
    st = state.load(cache)
    if st.schema_version != state.SCHEMA_VERSION or not isinstance(st.entries, dict):
        # Belt-and-braces: ``state.load`` already normalises bad payloads to
        # an empty ``State()`` with the current schema version, so hitting
        # this branch would indicate a code-level regression.
        return CheckResult(
            STATE_CHECK_NAME,
            "fail",
            f"unexpected schema_version={st.schema_version}",
        )
    if not st.entries:
        return CheckResult(
            STATE_CHECK_NAME,
            "warn",
            "state file missing or empty; run `qvd-mcp convert`",
        )
    return CheckResult(
        STATE_CHECK_NAME,
        "pass",
        f"schema_version={st.schema_version}, {len(st.entries)} entry(ies)",
    )


def check_claude_desktop_config() -> CheckResult:
    """Confirm Claude Desktop's JSON config has the ``qvd`` server wired up."""
    # Resolve via the module (not a local import) so tests can monkeypatch
    # ``qvd_mcp.claude_config.default_config_path`` at runtime.
    path = claude_config.default_config_path()
    if not path.is_file():
        return CheckResult(
            CLAUDE_CHECK_NAME,
            "warn",
            f"file not found: {path}; run `qvd-mcp setup`",
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckResult(
            CLAUDE_CHECK_NAME,
            "fail",
            f"could not read {path}: {exc}",
        )
    # Direct json.loads — not claude_config._load_existing — because we need
    # malformed JSON to fail the check, not silently coerce to {}.
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return CheckResult(
            CLAUDE_CHECK_NAME,
            "fail",
            f"malformed JSON in {path}: {exc}",
        )
    if not isinstance(data, dict):
        return CheckResult(
            CLAUDE_CHECK_NAME,
            "fail",
            f"top-level JSON value in {path} is not an object",
        )
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or "qvd" not in servers:
        return CheckResult(
            CLAUDE_CHECK_NAME,
            "warn",
            "qvd entry not found; run `qvd-mcp setup`",
        )
    return CheckResult(CLAUDE_CHECK_NAME, "pass", f"qvd entry present in {path}")


def check_recent_logs(config: Config | None) -> CheckResult:
    """Tail the last five non-DEBUG lines of the rotating log file.

    Purely informational — always passes. We respect ``config.log_dir`` when
    available (it may be overridden) and otherwise fall back to the
    platform-default location.
    """
    log_dir = config.log_dir if config is not None else Path(platformdirs.user_log_dir(APP_NAME))
    log_path = log_dir / "qvd-mcp.log"
    if not log_path.is_file():
        return CheckResult(LOGS_CHECK_NAME, "pass", "no log file yet")
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            # ``LOG_FORMAT`` renders level as ``%(levelname)-5s`` → a 5-char
            # token; ``" DEBUG "`` (with the trailing space after padding) is
            # the load-bearing substring for filtering.
            tail: deque[str] = deque(
                (line.rstrip("\n") for line in fh if " DEBUG " not in line),
                maxlen=5,
            )
    except OSError as exc:
        return CheckResult(LOGS_CHECK_NAME, "pass", f"could not read {log_path}: {exc}")
    if not tail:
        return CheckResult(LOGS_CHECK_NAME, "pass", "log file present but empty")
    return CheckResult(LOGS_CHECK_NAME, "pass", "\n".join(tail))


# ---------------------------------------------------------------------------
# Orchestration + presentation
# ---------------------------------------------------------------------------


def run_all() -> list[CheckResult]:
    """Run every diagnostic in order and return their results."""
    results: list[CheckResult] = []
    results.append(check_python_version())
    results.append(check_binary_resolvable())
    config_result, config = check_config_parses()
    results.append(config_result)
    results.append(check_source_dir_readable(config))
    results.append(check_cache_dir_writable(config))
    results.append(check_has_parquets(config))
    results.append(check_state_file(config))
    results.append(check_claude_desktop_config())
    results.append(check_recent_logs(config))
    return results


_STATUS_STYLES: dict[Status, str] = {
    "pass": "green",
    "warn": "yellow",
    "fail": "red",
}
_STATUS_EMOJI: dict[Status, str] = {
    "pass": "\u2705",  # white heavy check mark
    "warn": "\u26a0\ufe0f",  # warning sign
    "fail": "\u274c",  # cross mark
}


def render(results: list[CheckResult], *, use_emoji: bool = False) -> Table:
    """Build a Rich table summarising ``results``.

    The table is returned, not printed; the CLI layer is responsible for
    output. Status cells are coloured per :data:`_STATUS_STYLES`; when
    ``use_emoji`` is true a leading glyph is added for quick visual scan.
    """
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    for idx, result in enumerate(results, start=1):
        style = _STATUS_STYLES[result.status]
        status_cell = f"[{style}]{result.status}[/{style}]"
        if use_emoji:
            status_cell = f"{_STATUS_EMOJI[result.status]} {status_cell}"
        table.add_row(str(idx), result.name, status_cell, result.message)
    return table


def exit_code(results: list[CheckResult]) -> int:
    """Map a list of results to the command's exit code.

    Priority: a broken *config* check dominates (exit 2) because every
    downstream check was skipped and the user needs to fix that first.
    Otherwise: any fail → 1, else 0.
    """
    config_failed = any(
        r.name == CONFIG_CHECK_NAME and r.status == "fail" for r in results
    )
    if config_failed:
        return 2
    if any(r.status == "fail" for r in results):
        return 1
    return 0
