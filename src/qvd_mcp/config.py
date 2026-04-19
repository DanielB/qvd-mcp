"""Configuration loading for qvd-mcp.

Minimal TOML-backed config. A missing file is fine; all fields have defaults
except ``source_dir``, which the user must set either in the file or via CLI.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

APP_NAME = "qvd-mcp"
DEFAULT_MAX_QUERY_ROWS = 1000
MAX_QUERY_ROW_CEILING = 10_000
DEFAULT_QUERY_TIMEOUT_S = 30
DEFAULT_AUTO_REFRESH_DEBOUNCE_S = 10


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
    source_dir: Path
    cache_dir: Path
    reader: str = "pyqvd"
    max_query_rows: int = DEFAULT_MAX_QUERY_ROWS
    query_timeout_s: int = DEFAULT_QUERY_TIMEOUT_S
    log_level: str = "INFO"
    log_dir: Path = field(default_factory=default_log_dir)
    auto_refresh_debounce_s: int = DEFAULT_AUTO_REFRESH_DEBOUNCE_S

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


def load(
    config_path: Path | None = None,
    *,
    source_override: Path | None = None,
    cache_override: Path | None = None,
    log_level_override: str | None = None,
) -> Config:
    """Load config, applying CLI overrides on top of file values and defaults.

    A missing config file is allowed — only a missing ``source_dir`` is fatal.
    """
    path = config_path or default_config_path()
    raw: dict[str, object] = {}
    if path.is_file():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"config file at {path} is not valid TOML: {exc}") from exc

    source_value: str | Path | None = source_override or _coerce_str(raw, "source_dir")
    if source_value is None:
        raise ConfigError(
            "source_dir is not set. Pass --source or add source_dir to "
            f"{path}. See examples/config.example.toml."
        )
    source_dir = Path(str(source_value)).expanduser().resolve()
    if not source_dir.is_dir():
        raise ConfigError(f"source_dir does not exist or is not a directory: {source_dir}")

    cache_value: str | Path | None = cache_override or _coerce_str(raw, "cache_dir")
    cache_dir = (
        Path(str(cache_value)).expanduser().resolve()
        if cache_value is not None
        else default_cache_dir()
    )

    reader = _coerce_str(raw, "reader") or "pyqvd"
    if reader != "pyqvd":
        raise ConfigError(
            f"unknown reader '{reader}'. Only 'pyqvd' is supported in this release."
        )

    max_rows = _coerce_int(raw, "max_query_rows", DEFAULT_MAX_QUERY_ROWS)
    max_rows = max(1, min(max_rows, MAX_QUERY_ROW_CEILING))

    timeout = _coerce_int(raw, "query_timeout_s", DEFAULT_QUERY_TIMEOUT_S)
    timeout = max(1, timeout)

    debounce = _coerce_int(raw, "auto_refresh_debounce_s", DEFAULT_AUTO_REFRESH_DEBOUNCE_S)
    debounce = max(0, debounce)

    log_level = (log_level_override or _coerce_str(raw, "log_level") or "INFO").upper()

    return Config(
        source_dir=source_dir,
        cache_dir=cache_dir,
        reader=reader,
        max_query_rows=max_rows,
        query_timeout_s=timeout,
        log_level=log_level,
        auto_refresh_debounce_s=debounce,
    )
