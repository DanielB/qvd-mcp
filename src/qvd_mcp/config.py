"""Configuration loading for qvd-mcp.

Minimal TOML-backed config. A missing file is fine; ``source_dir`` is
optional — when unset, the server runs in cache-only mode (no auto-refresh,
no conversion). A file-configured ``source_dir`` that doesn't exist on
disk silently degrades to cache-only with a warning, which covers the
case of a colleague copying the producer's ``config.toml``.
"""
from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

log = logging.getLogger(__name__)

APP_NAME = "qvd-mcp"
DEFAULT_MAX_QUERY_ROWS = 1000
MAX_QUERY_ROW_CEILING = 10_000
DEFAULT_QUERY_TIMEOUT_S = 30
DEFAULT_AUTO_REFRESH_DEBOUNCE_S = 10
DEFAULT_INCLUDE: tuple[str, ...] = ("*.qvd",)
DEFAULT_EXCLUDE: tuple[str, ...] = ()


def default_cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir(APP_NAME))


def default_config_path() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME)) / "config.toml"


def default_log_dir() -> Path:
    return Path(platformdirs.user_log_dir(APP_NAME))


class ConfigError(Exception):
    """Raised when configuration is missing required values or malformed."""


@dataclass(frozen=True)
class Config:
    cache_dir: Path
    source_dir: Path | None = None
    max_query_rows: int = DEFAULT_MAX_QUERY_ROWS
    query_timeout_s: int = DEFAULT_QUERY_TIMEOUT_S
    log_level: str = "INFO"
    log_dir: Path = field(default_factory=default_log_dir)
    auto_refresh_debounce_s: int = DEFAULT_AUTO_REFRESH_DEBOUNCE_S
    # Glob patterns (relative to ``source_dir``) that scope which QVDs
    # are discovered. ``include`` defaults to all ``.qvd`` files; ``exclude``
    # is additionally applied after ``include`` and wins on match.
    include: tuple[str, ...] = DEFAULT_INCLUDE
    exclude: tuple[str, ...] = DEFAULT_EXCLUDE

    def parquet_path_for(self, view_name: str) -> Path:
        return self.cache_dir / f"{view_name}.parquet"


def _coerce_str(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"config key '{key}' must be a string")
    return value


def _coerce_int(raw: dict[str, object], key: str, fallback: int) -> int:
    value = raw.get(key, fallback)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"config key '{key}' must be an integer")
    return value


def _coerce_str_list(
    raw: dict[str, object], key: str, fallback: tuple[str, ...]
) -> tuple[str, ...]:
    value = raw.get(key)
    if value is None:
        return fallback
    if not isinstance(value, list):
        raise ConfigError(f"config key '{key}' must be a list of strings")
    for item in value:
        if not isinstance(item, str):
            raise ConfigError(f"config key '{key}' must contain only strings")
    return tuple(value)


def load(
    config_path: Path | None = None,
    *,
    source_override: Path | None = None,
    cache_override: Path | None = None,
    log_level_override: str | None = None,
    include_override: tuple[str, ...] | None = None,
    exclude_override: tuple[str, ...] | None = None,
) -> Config:
    """Load config, applying CLI overrides on top of file values and defaults.

    ``source_dir`` is optional. When it is set in the file but the path does
    not exist on disk, we log a warning and degrade to cache-only mode —
    this handles the realistic case of a colleague copying the producer's
    config.toml, where the embedded source path won't exist on their machine.
    An explicit ``source_override`` (CLI ``--source``) that is missing is
    still a hard error, because the user asked for it by name.

    ``reader`` used to be a config option pointing at a backend (pyqvd vs
    a planned qvd-rs). We only ship one backend and the Rust alternative
    isn't currently viable (stale upstream, no Python 3.13 / arm64 wheels),
    so the dispatch layer was removed. A ``reader`` key in existing
    configs is silently ignored.
    """
    path = config_path or default_config_path()
    raw: dict[str, object] = {}
    if path.is_file():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"config file at {path} is not valid TOML: {exc}") from exc

    source_dir: Path | None = None
    if source_override is not None:
        source_dir = Path(source_override).expanduser().resolve()
        if not source_dir.is_dir():
            raise ConfigError(
                f"source_dir does not exist or is not a directory: {source_dir}"
            )
    else:
        source_value = _coerce_str(raw, "source_dir")
        if source_value is not None:
            candidate = Path(str(source_value)).expanduser().resolve()
            if candidate.is_dir():
                source_dir = candidate
            else:
                log.warning(
                    "source_dir %s does not exist; running cache-only. "
                    "Re-run `qvd-mcp setup` to reconfigure.",
                    candidate,
                )

    cache_value: str | Path | None = cache_override or _coerce_str(raw, "cache_dir")
    cache_dir = (
        Path(str(cache_value)).expanduser().resolve()
        if cache_value is not None
        else default_cache_dir()
    )

    max_rows = _coerce_int(raw, "max_query_rows", DEFAULT_MAX_QUERY_ROWS)
    max_rows = max(1, min(max_rows, MAX_QUERY_ROW_CEILING))

    timeout = _coerce_int(raw, "query_timeout_s", DEFAULT_QUERY_TIMEOUT_S)
    timeout = max(1, timeout)

    debounce = _coerce_int(raw, "auto_refresh_debounce_s", DEFAULT_AUTO_REFRESH_DEBOUNCE_S)
    debounce = max(0, debounce)

    log_level = (log_level_override or _coerce_str(raw, "log_level") or "INFO").upper()

    include = (
        include_override
        if include_override is not None
        else _coerce_str_list(raw, "include", DEFAULT_INCLUDE)
    )
    exclude = (
        exclude_override
        if exclude_override is not None
        else _coerce_str_list(raw, "exclude", DEFAULT_EXCLUDE)
    )

    return Config(
        cache_dir=cache_dir,
        source_dir=source_dir,
        max_query_rows=max_rows,
        query_timeout_s=timeout,
        log_level=log_level,
        auto_refresh_debounce_s=debounce,
        include=include,
        exclude=exclude,
    )
