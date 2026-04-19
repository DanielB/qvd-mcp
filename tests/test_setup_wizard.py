"""Tests for the ``--yes`` path of the setup wizard.

Interactive ``gather_interactive`` is not covered here — Phase 2 tests only
exercise the non-interactive flow that ``qvd-mcp setup --yes`` uses and that
our own smoke-test automation relies on.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from qvd_mcp import claude_config, setup_wizard
from qvd_mcp import config as qmcp_config
from qvd_mcp.setup_wizard import SetupError, run_setup
from tests.fixtures.generate import generate_all


def _patch_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path, Path]:
    """Redirect the three default paths that ``run_setup`` reaches for and
    move CWD to ``tmp_path`` so local-checkout detection can't find this
    project's pyproject by walking up from the real test process CWD.

    Returns ``(claude_config_path, config_toml_path, log_dir)``.
    """
    claude_path = tmp_path / "claude_desktop_config.json"
    config_path = tmp_path / "config.toml"
    log_dir = tmp_path / "logs"

    monkeypatch.setattr(
        claude_config, "default_config_path", lambda: claude_path
    )
    monkeypatch.setattr(
        qmcp_config, "default_config_path", lambda: config_path
    )
    monkeypatch.setattr(qmcp_config, "default_log_dir", lambda: log_dir)
    # Pytest runs from the repo root; without this, detect_local_checkout
    # would find the real qvd-mcp pyproject and flip the command to the
    # uv --directory form, breaking the pure-PyPI-form assertions below.
    monkeypatch.chdir(tmp_path)

    return claude_path, config_path, log_dir


def test_setup_yes_writes_config_and_converts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    files = generate_all(source)
    assert files  # sanity: fixtures actually produced QVDs

    claude_path, config_path, _log_dir = _patch_defaults(monkeypatch, tmp_path)

    run_setup(yes=True, source=source, cache=cache)

    # config.toml written and parses as valid TOML with both keys set to
    # the directories we asked for (resolved to absolute paths).
    assert config_path.is_file()
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert Path(parsed["source_dir"]).resolve() == source.resolve()
    assert Path(parsed["cache_dir"]).resolve() == cache.resolve()

    # Claude Desktop config has our qvd entry.
    assert claude_path.is_file()
    claude_data: Any = json.loads(claude_path.read_text(encoding="utf-8"))
    assert claude_data["mcpServers"]["qvd-mcp"] == {
        "command": "uvx",
        "args": ["qvd-mcp", "serve"],
    }

    # Conversion ran: cache dir has at least one parquet.
    parquets = list(cache.glob("*.parquet"))
    assert parquets, "expected at least one parquet in cache after setup"


def test_setup_yes_no_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)

    claude_path, config_path, _ = _patch_defaults(monkeypatch, tmp_path)

    run_setup(yes=True, source=source, cache=cache, no_claude=True)

    # Config was still written.
    assert config_path.is_file()
    # Claude Desktop file was NOT created.
    assert not claude_path.exists()


def test_setup_rejects_missing_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_defaults(monkeypatch, tmp_path)

    missing = tmp_path / "nope"
    with pytest.raises(SetupError):
        run_setup(yes=True, source=missing)


def test_detect_local_checkout_from_repo_root(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "qvd-mcp"\n', encoding="utf-8"
    )
    assert setup_wizard.detect_local_checkout(tmp_path) == tmp_path.resolve()


def test_detect_local_checkout_from_subdir(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "qvd-mcp"\n', encoding="utf-8"
    )
    sub = tmp_path / "src" / "deeper"
    sub.mkdir(parents=True)
    assert setup_wizard.detect_local_checkout(sub) == tmp_path.resolve()


def test_detect_local_checkout_returns_none_for_unrelated_project(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "other-project"\n', encoding="utf-8"
    )
    assert setup_wizard.detect_local_checkout(tmp_path) is None


def test_detect_local_checkout_returns_none_when_no_pyproject(
    tmp_path: Path,
) -> None:
    # tmp_path (/private/var/folders/... on macOS) has no qvd-mcp pyproject
    # in any ancestor, so detection should return None.
    assert setup_wizard.detect_local_checkout(tmp_path) is None


def test_setup_uses_local_command_when_checkout_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Build a fake checkout with a qvd-mcp pyproject plus a QVD source dir.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text(
        '[project]\nname = "qvd-mcp"\n', encoding="utf-8"
    )
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)

    claude_path, _config_path, _log = _patch_defaults(monkeypatch, tmp_path)
    # _patch_defaults chdir'd to tmp_path; override to the fake checkout so
    # detect_local_checkout finds it.
    monkeypatch.chdir(checkout)

    run_setup(yes=True, source=source, cache=cache)

    data: Any = json.loads(claude_path.read_text(encoding="utf-8"))
    entry = data["mcpServers"]["qvd-mcp"]
    assert entry["command"] == "uv"
    assert entry["args"] == [
        "--directory",
        str(checkout.resolve()),
        "run",
        "qvd-mcp",
        "serve",
    ]


def test_setup_migrates_legacy_qvd_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed the Claude Desktop config with a legacy "qvd" entry (the name used
    # before the canonical rename). Setup should remove it and insert the new
    # "qvd-mcp" entry instead.
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)
    claude_path, _config_path, _log = _patch_defaults(monkeypatch, tmp_path)
    claude_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "qvd": {"command": "uvx", "args": ["qvd-mcp", "serve"]}
                }
            }
        ),
        encoding="utf-8",
    )

    run_setup(yes=True, source=source, cache=cache)

    data: Any = json.loads(claude_path.read_text(encoding="utf-8"))
    assert "qvd" not in data["mcpServers"]
    assert "qvd-mcp" in data["mcpServers"]


def test_setup_yes_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)

    claude_path, config_path, _ = _patch_defaults(monkeypatch, tmp_path)

    run_setup(yes=True, source=source, cache=cache)
    run_setup(yes=True, source=source, cache=cache)

    # Claude Desktop JSON has exactly one qvd-mcp entry (no duplicates).
    claude_data: Any = json.loads(claude_path.read_text(encoding="utf-8"))
    servers = claude_data["mcpServers"]
    assert list(servers.keys()) == ["qvd-mcp"]
    assert servers["qvd-mcp"] == {
        "command": "uvx",
        "args": ["qvd-mcp", "serve"],
    }

    # config.toml still parses cleanly.
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "source_dir" in parsed
    assert "cache_dir" in parsed
