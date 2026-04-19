"""Unit tests for :mod:`qvd_mcp.doctor`.

Every check is exercised in isolation using ``tmp_path`` and
``monkeypatch`` — no subprocesses, no real home-directory access.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from qvd_mcp import claude_config, doctor
from qvd_mcp.config import Config, default_cache_dir


def _config(source: Path, cache: Path, log_dir: Path | None = None) -> Config:
    """Build a Config directly, bypassing ``load``'s source-dir validation.

    Tests that deliberately point at nonexistent paths would otherwise trip
    the real ``load``'s checks before ``doctor`` got a chance to see them.
    """
    return Config(
        source_dir=source,
        cache_dir=cache,
        log_dir=log_dir if log_dir is not None else cache / "logs",
    )


# ---------------------------------------------------------------------------
# Python version
# ---------------------------------------------------------------------------


def test_python_version_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 12, 5, "final", 0))
    result = doctor.check_python_version()
    assert result.status == "pass"
    assert "3.12.5" in result.message


def test_python_version_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "version_info", (3, 10, 0, "final", 0))
    result = doctor.check_python_version()
    assert result.status == "fail"
    assert ">= 3.11" in result.message


# ---------------------------------------------------------------------------
# Binary resolvable
# ---------------------------------------------------------------------------


def test_binary_resolvable_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: "/usr/local/bin/qvd-mcp")
    result = doctor.check_binary_resolvable()
    assert result.status == "pass"
    assert result.message == "/usr/local/bin/qvd-mcp"


def test_binary_resolvable_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)
    result = doctor.check_binary_resolvable()
    assert result.status == "warn"
    assert "PATH" in result.message


# ---------------------------------------------------------------------------
# Config parses
# ---------------------------------------------------------------------------


def _write_config_toml(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _patch_config_path(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    # Patch at the place ``config.load`` calls it (module-local name), not at
    # doctor's import site.
    monkeypatch.setattr("qvd_mcp.config.default_config_path", lambda: path)


def test_config_parses_missing_source_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point at a config file that doesn't exist at all → source_dir unset.
    _patch_config_path(monkeypatch, tmp_path / "config.toml")
    result, config = doctor.check_config_parses()
    assert config is None
    assert result.status == "warn"
    assert "setup" in result.message


def test_config_parses_malformed_toml_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = _write_config_toml(tmp_path / "config.toml", "!!! this is not toml !!!")
    _patch_config_path(monkeypatch, cfg_path)
    result, config = doctor.check_config_parses()
    assert config is None
    assert result.status == "fail"
    assert "TOML" in result.message or "toml" in result.message.lower()


def test_config_parses_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "qvds"
    source.mkdir()
    cfg_path = _write_config_toml(
        tmp_path / "config.toml",
        f'source_dir = "{source.as_posix()}"\n',
    )
    _patch_config_path(monkeypatch, cfg_path)
    result, config = doctor.check_config_parses()
    assert result.status == "pass"
    assert config is not None
    assert config.source_dir == source.resolve()


def test_config_parses_bad_source_dir_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Non-"is not set" ConfigError: source_dir is set but doesn't exist.
    cfg_path = _write_config_toml(
        tmp_path / "config.toml",
        'source_dir = "/nonexistent/path/that/really/does/not/exist"\n',
    )
    _patch_config_path(monkeypatch, cfg_path)
    result, config = doctor.check_config_parses()
    assert config is None
    assert result.status == "fail"


# ---------------------------------------------------------------------------
# Source dir readable
# ---------------------------------------------------------------------------


def test_source_dir_readable_skipped_without_config() -> None:
    result = doctor.check_source_dir_readable(None)
    assert result.status == "warn"
    assert "skipped" in result.message


def test_source_dir_readable_pass(tmp_path: Path) -> None:
    source = tmp_path / "qvds"
    source.mkdir()
    config = _config(source=source, cache=tmp_path / "cache")
    result = doctor.check_source_dir_readable(config)
    assert result.status == "pass"


def test_source_dir_readable_fail(tmp_path: Path) -> None:
    config = _config(source=tmp_path / "nope", cache=tmp_path / "cache")
    result = doctor.check_source_dir_readable(config)
    assert result.status == "fail"


# ---------------------------------------------------------------------------
# Cache dir writable
# ---------------------------------------------------------------------------


def test_cache_dir_writable_skipped_without_config() -> None:
    result = doctor.check_cache_dir_writable(None)
    assert result.status == "warn"


def test_cache_dir_writable_pass_existing(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    config = _config(source=tmp_path, cache=cache)
    result = doctor.check_cache_dir_writable(config)
    assert result.status == "pass"


def test_cache_dir_writable_pass_parent_exists(tmp_path: Path) -> None:
    # Cache dir itself doesn't exist yet (fresh install), but its parent does.
    cache = tmp_path / "not-yet-created"
    assert not cache.exists()
    config = _config(source=tmp_path, cache=cache)
    result = doctor.check_cache_dir_writable(config)
    assert result.status == "pass"
    # The check must not create the cache dir itself — only (at most) its parent.
    assert not cache.exists()


def test_cache_dir_writable_fail_parent_missing(tmp_path: Path) -> None:
    # Parent is a file, so mkdir will fail.
    file_path = tmp_path / "afile"
    file_path.write_text("x")
    cache = file_path / "cache"
    config = _config(source=tmp_path, cache=cache)
    result = doctor.check_cache_dir_writable(config)
    assert result.status == "fail"


# ---------------------------------------------------------------------------
# Has parquets
# ---------------------------------------------------------------------------


def test_has_parquets_skipped_without_config() -> None:
    assert doctor.check_has_parquets(None).status == "warn"


def test_has_parquets_empty_cache_warns(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    config = _config(source=tmp_path, cache=cache)
    result = doctor.check_has_parquets(config)
    assert result.status == "warn"
    assert "convert" in result.message


def test_has_parquets_missing_cache_warns(tmp_path: Path) -> None:
    config = _config(source=tmp_path, cache=tmp_path / "missing-cache")
    result = doctor.check_has_parquets(config)
    assert result.status == "warn"


def test_has_parquets_pass(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "sales.parquet").write_bytes(b"")
    (cache / "README.md").write_text("not a parquet")
    config = _config(source=tmp_path, cache=cache)
    result = doctor.check_has_parquets(config)
    assert result.status == "pass"
    assert "1 parquet" in result.message


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def test_state_file_skipped_without_config() -> None:
    assert doctor.check_state_file(None).status == "warn"


def test_state_file_missing_cache_warns(tmp_path: Path) -> None:
    config = _config(source=tmp_path, cache=tmp_path / "missing-cache")
    result = doctor.check_state_file(config)
    assert result.status == "warn"


def test_state_file_empty_entries_warns(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    config = _config(source=tmp_path, cache=cache)
    result = doctor.check_state_file(config)
    assert result.status == "warn"


def test_state_file_pass(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / ".qvd-mcp-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reader": "pyqvd",
                "reader_version": "2.3.2",
                "entries": {
                    "/some.qvd": {
                        "view_name": "some",
                        "parquet_path": "some.parquet",
                        "source_mtime_ns": 1,
                        "source_size": 2,
                        "converted_at": "2026-01-01T00:00:00Z",
                        "rows": 1,
                        "columns": 1,
                    }
                },
            }
        )
    )
    config = _config(source=tmp_path, cache=cache)
    result = doctor.check_state_file(config)
    assert result.status == "pass"
    assert "schema_version=1" in result.message


# ---------------------------------------------------------------------------
# Claude Desktop config
# ---------------------------------------------------------------------------


def _patch_claude_path(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(claude_config, "default_config_path", lambda: path)


def test_claude_config_missing_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_claude_path(monkeypatch, tmp_path / "claude_desktop_config.json")
    result = doctor.check_claude_desktop_config()
    assert result.status == "warn"


def test_claude_config_malformed_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "claude_desktop_config.json"
    target.write_text("{{{ not json", encoding="utf-8")
    _patch_claude_path(monkeypatch, target)
    result = doctor.check_claude_desktop_config()
    assert result.status == "fail"
    assert "malformed" in result.message.lower() or "json" in result.message.lower()


def test_claude_config_missing_qvd_entry_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "claude_desktop_config.json"
    target.write_text(
        json.dumps({"mcpServers": {"other": {"command": "node", "args": []}}}),
        encoding="utf-8",
    )
    _patch_claude_path(monkeypatch, target)
    result = doctor.check_claude_desktop_config()
    assert result.status == "warn"
    assert "qvd-mcp" in result.message


def test_claude_config_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "claude_desktop_config.json"
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "qvd-mcp": {"command": "uvx", "args": ["qvd-mcp", "serve"]},
                }
            }
        ),
        encoding="utf-8",
    )
    _patch_claude_path(monkeypatch, target)
    result = doctor.check_claude_desktop_config()
    assert result.status == "pass"


def test_claude_config_non_object_json_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "claude_desktop_config.json"
    target.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    _patch_claude_path(monkeypatch, target)
    result = doctor.check_claude_desktop_config()
    assert result.status == "fail"


# ---------------------------------------------------------------------------
# Recent logs
# ---------------------------------------------------------------------------


def test_recent_logs_no_file_passes(tmp_path: Path) -> None:
    config = _config(source=tmp_path, cache=tmp_path / "cache", log_dir=tmp_path / "logs")
    result = doctor.check_recent_logs(config)
    assert result.status == "pass"
    assert "no log file" in result.message


def test_recent_logs_filters_debug_and_tails_five(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    lines = []
    for i in range(10):
        level = "DEBUG" if i % 2 == 0 else "INFO "
        # Mirror LOG_FORMAT shape: "<ts> <LEVEL-5> <logger> <msg>".
        lines.append(f"2026-04-19 10:00:0{i} {level} qvd_mcp.test message-{i}")
    (log_dir / "qvd-mcp.log").write_text("\n".join(lines) + "\n", encoding="utf-8")

    config = _config(source=tmp_path, cache=tmp_path / "cache", log_dir=log_dir)
    result = doctor.check_recent_logs(config)
    assert result.status == "pass"
    # Five INFO lines only (indices 1, 3, 5, 7, 9); "DEBUG" ones suppressed.
    msg_lines = result.message.splitlines()
    assert len(msg_lines) == 5
    assert all("DEBUG" not in line for line in msg_lines)
    assert "message-9" in msg_lines[-1]


def test_recent_logs_falls_back_to_platform_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # With config=None we should probe the platformdirs location.
    monkeypatch.setattr(
        doctor.platformdirs, "user_log_dir", lambda _app: str(tmp_path / "plat-logs")
    )
    result = doctor.check_recent_logs(None)
    assert result.status == "pass"
    assert "no log file" in result.message


# ---------------------------------------------------------------------------
# Orchestration, render, exit_code
# ---------------------------------------------------------------------------


def test_run_all_returns_nine_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Isolate from host state so the test is deterministic.
    _patch_config_path(monkeypatch, tmp_path / "no-config.toml")
    _patch_claude_path(monkeypatch, tmp_path / "no-claude.json")
    monkeypatch.setattr(
        doctor.platformdirs, "user_log_dir", lambda _app: str(tmp_path / "logs")
    )
    # Keep cache isolated so a developer's populated cache doesn't leak in.
    monkeypatch.setattr(
        "qvd_mcp.config.default_cache_dir", lambda: tmp_path / "cache"
    )
    # Sanity: default_cache_dir may still be referenced by the Config default.
    _ = default_cache_dir

    results = doctor.run_all()
    assert len(results) == 9
    names = [r.name for r in results]
    assert names == [
        doctor.PY_CHECK_NAME,
        doctor.BINARY_CHECK_NAME,
        doctor.CONFIG_CHECK_NAME,
        doctor.SOURCE_CHECK_NAME,
        doctor.CACHE_CHECK_NAME,
        doctor.PARQUETS_CHECK_NAME,
        doctor.STATE_CHECK_NAME,
        doctor.CLAUDE_CHECK_NAME,
        doctor.LOGS_CHECK_NAME,
    ]


def test_render_returns_table() -> None:
    from rich.table import Table as RichTable

    results = [
        doctor.CheckResult("A", "pass", "ok"),
        doctor.CheckResult("B", "warn", "hmm"),
        doctor.CheckResult("C", "fail", "nope"),
    ]
    table = doctor.render(results)
    assert isinstance(table, RichTable)
    assert table.row_count == 3
    assert [c.header for c in table.columns] == ["#", "Check", "Status", "Detail"]


def test_render_with_emoji() -> None:
    results = [doctor.CheckResult("A", "pass", "ok")]
    table = doctor.render(results, use_emoji=True)
    # Spot-check: the emoji glyph should appear somewhere in the rendered cells.
    cell_text = "".join(
        str(cell) for column in table.columns for cell in column.cells
    )
    assert "\u2705" in cell_text


def test_exit_code_all_pass() -> None:
    results = [
        doctor.CheckResult(doctor.PY_CHECK_NAME, "pass", ""),
        doctor.CheckResult(doctor.CONFIG_CHECK_NAME, "pass", ""),
    ]
    assert doctor.exit_code(results) == 0


def test_exit_code_any_fail_returns_one() -> None:
    results = [
        doctor.CheckResult(doctor.PY_CHECK_NAME, "pass", ""),
        doctor.CheckResult(doctor.CONFIG_CHECK_NAME, "pass", ""),
        doctor.CheckResult(doctor.SOURCE_CHECK_NAME, "fail", "bad"),
    ]
    assert doctor.exit_code(results) == 1


def test_exit_code_config_fail_returns_two() -> None:
    results = [
        doctor.CheckResult(doctor.PY_CHECK_NAME, "pass", ""),
        doctor.CheckResult(doctor.CONFIG_CHECK_NAME, "fail", "broken"),
        doctor.CheckResult(doctor.SOURCE_CHECK_NAME, "fail", "also bad"),
    ]
    # Config-broken dominates over other failures.
    assert doctor.exit_code(results) == 2


def test_exit_code_warns_only_is_zero() -> None:
    results = [
        doctor.CheckResult(doctor.PY_CHECK_NAME, "pass", ""),
        doctor.CheckResult(doctor.CONFIG_CHECK_NAME, "warn", "no source"),
        doctor.CheckResult(doctor.CLAUDE_CHECK_NAME, "warn", "no entry"),
    ]
    assert doctor.exit_code(results) == 0
