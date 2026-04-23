"""Tests for config.py — optional source_dir and silent degrade."""
from __future__ import annotations

from pathlib import Path

import pytest

from qvd_mcp.config import Config, ConfigError, load


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_config_dataclass_accepts_none_source(tmp_path: Path) -> None:
    cfg = Config(cache_dir=tmp_path, source_dir=None)
    assert cfg.source_dir is None


def test_load_with_existing_source(tmp_path: Path) -> None:
    source = tmp_path / "qvds"
    source.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    cfg_file = _write(
        tmp_path / "config.toml",
        f"source_dir = '{source}'\ncache_dir = '{cache}'\n",
    )
    cfg = load(cfg_file)
    assert cfg.source_dir == source.resolve()


def test_load_without_source_is_ok(tmp_path: Path) -> None:
    """No source_dir configured — cache-only mode, no error."""
    cache = tmp_path / "cache"
    cache.mkdir()
    cfg_file = _write(tmp_path / "config.toml", f"cache_dir = '{cache}'\n")
    cfg = load(cfg_file)
    assert cfg.source_dir is None


def test_load_with_missing_source_degrades_silently(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """source_dir set but path doesn't exist — treat as unset, log warning."""
    cache = tmp_path / "cache"
    cache.mkdir()
    cfg_file = _write(
        tmp_path / "config.toml",
        f"source_dir = '{tmp_path / 'nope'}'\ncache_dir = '{cache}'\n",
    )
    with caplog.at_level("WARNING"):
        cfg = load(cfg_file)
    assert cfg.source_dir is None
    assert any("does not exist" in rec.message.lower() for rec in caplog.records)


def test_load_with_cli_source_override_validates(tmp_path: Path) -> None:
    """Explicit --source that doesn't exist is still a hard error (user asked for it)."""
    cache = tmp_path / "cache"
    cache.mkdir()
    cfg_file = _write(tmp_path / "config.toml", f"cache_dir = '{cache}'\n")
    with pytest.raises(ConfigError, match="does not exist"):
        load(cfg_file, source_override=tmp_path / "not-here")
