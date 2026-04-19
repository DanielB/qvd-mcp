"""Claude Desktop config file merge / unmerge.

Claude Desktop reads its MCP server list from a per-user JSON file whose
location varies by platform. ``setup`` wants to add a ``qvd`` entry to
``mcpServers`` without clobbering anything else the user has configured;
``uninstall`` wants to remove it just as carefully. Both need to be
idempotent, atomic, and tolerant of a file that's missing, empty, or
hand-edited into garbage.

The design choices are deliberate:

- **One-deep ``.bak`` safety net.** ``merge`` writes the pre-merge
  contents to ``<path>.bak`` verbatim (bytes, not a re-serialize) so a
  malformed original is preserved exactly. Any prior ``.bak`` is
  overwritten — history is not the point, one-step undo is.
- **Malformed JSON is not an error.** If the existing file fails to
  parse, we log a warning and proceed as if it were ``{}``. A user who
  broke their own config shouldn't be blocked from re-running setup.
- **Atomic writes.** We write to ``<path>.tmp`` and ``os.replace()``
  onto the final name so a crash mid-write can't truncate the config.
- **``unmerge`` leaves the ``.bak`` alone.** It's meant for recovering
  from a setup that went sideways, not for undoing an explicit uninstall.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_FILENAME = "claude_desktop_config.json"

# The key under which our entry lives inside ``mcpServers``. Kept here as a
# single source of truth so every caller (setup, doctor, uninstall) agrees.
QVD_SERVER_NAME = "qvd-mcp"

# A previous iteration used ``"qvd"`` as the key. Setup and uninstall remove
# it defensively so the upgrade is transparent.
LEGACY_SERVER_NAME = "qvd"


class ClaudeConfigError(Exception):
    """Raised for genuinely unrecoverable errors (e.g. filesystem write failure).

    Missing or malformed existing JSON is *not* such an error — the caller is
    expected to proceed with an empty config in that case.
    """


def default_config_path() -> Path:
    """Return the platform-appropriate Claude Desktop config path.

    ``sys.platform`` is read at call time so tests can monkeypatch it.
    """
    if sys.platform == "darwin":
        return (
            Path("~/Library/Application Support/Claude").expanduser()
            / CONFIG_FILENAME
        )
    if sys.platform in ("win32", "cygwin"):
        appdata = os.environ.get("APPDATA")
        base = (
            Path(appdata)
            if appdata
            else Path("~/AppData/Roaming").expanduser()
        )
        return base / "Claude" / CONFIG_FILENAME
    # Linux, BSD, everything else.
    return Path("~/.config/Claude").expanduser() / CONFIG_FILENAME


def _load_existing(config_path: Path) -> dict[str, Any]:
    """Read existing config, returning ``{}`` for missing or malformed files."""
    if not config_path.is_file():
        return {}
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ClaudeConfigError(f"could not read {config_path}: {exc}") from exc
    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError:
        log.warning(
            "Claude Desktop config at %s is not valid JSON; treating as empty",
            config_path,
        )
        return {}
    if not isinstance(raw, dict):
        log.warning(
            "Claude Desktop config at %s is not a JSON object; treating as empty",
            config_path,
        )
        return {}
    return raw


def _atomic_write(config_path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` as JSON to ``config_path`` via ``.tmp`` + ``os.replace()``."""
    payload = json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, config_path)
    except OSError as exc:
        # Best-effort cleanup of the tmp file so we don't leave litter behind.
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise ClaudeConfigError(f"could not write {config_path}: {exc}") from exc


def merge(
    server_name: str,
    command: str,
    args: list[str],
    *,
    config_path: Path | None = None,
) -> None:
    """Merge one entry into ``mcpServers`` in the Claude Desktop config.

    Idempotent — running twice updates the entry rather than duplicating.
    Preserves every other top-level key and every other ``mcpServers``
    entry. Writes a one-deep ``.bak`` of the pre-merge contents (bytes
    verbatim) when an existing file is present, then atomically replaces
    the target with the new JSON.
    """
    path = config_path if config_path is not None else default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = _load_existing(path)

    # Write the backup as raw bytes so a malformed original is preserved
    # exactly. Skip when the file didn't exist — nothing to back up.
    if path.is_file():
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            shutil.copyfile(path, backup)
        except OSError as exc:
            raise ClaudeConfigError(
                f"could not write backup {backup}: {exc}"
            ) from exc

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers
    servers[server_name] = {"command": command, "args": list(args)}

    _atomic_write(path, data)


def unmerge(
    server_name: str,
    *,
    config_path: Path | None = None,
) -> bool:
    """Remove ``server_name`` from ``mcpServers``. Returns whether anything changed.

    Missing file or malformed JSON → ``False`` (no-op, no exception). If
    ``mcpServers`` becomes empty after the removal, the whole key is
    dropped so the file doesn't accumulate empty containers. The ``.bak``
    file from a prior ``merge`` is deliberately left untouched.
    """
    path = config_path if config_path is not None else default_config_path()
    if not path.is_file():
        return False

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ClaudeConfigError(f"could not read {path}: {exc}") from exc

    try:
        raw: Any = json.loads(text)
    except json.JSONDecodeError:
        # Nothing we can safely remove from garbage — don't touch the file.
        return False
    if not isinstance(raw, dict):
        return False
    data: dict[str, Any] = raw

    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or server_name not in servers:
        return False

    del servers[server_name]
    if not servers:
        del data["mcpServers"]

    _atomic_write(path, data)
    return True
