from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from qvd_mcp.claude_config import default_config_path, merge, unmerge

# ---------------------------------------------------------------------------
# default_config_path
# ---------------------------------------------------------------------------


def test_default_config_path_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    path = default_config_path()
    # Compare structurally via Path.parts so this works regardless of
    # the host OS's native path separator.
    assert path.parts[-4:] == (
        "Library",
        "Application Support",
        "Claude",
        "claude_desktop_config.json",
    )
    assert not str(path).startswith("~")  # ~ expanded


def test_default_config_path_win32(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = default_config_path()
    assert path.parts[-2:] == ("Claude", "claude_desktop_config.json")
    # APPDATA is respected.
    assert str(tmp_path) in str(path)


def test_default_config_path_win32_without_appdata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    path = default_config_path()
    # Falls back to ~/AppData/Roaming/Claude/...
    assert path.parts[-4:] == (
        "AppData",
        "Roaming",
        "Claude",
        "claude_desktop_config.json",
    )
    assert not str(path).startswith("~")  # ~ expanded


def test_default_config_path_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    path = default_config_path()
    assert path.parts[-3:] == (".config", "Claude", "claude_desktop_config.json")
    assert not str(path).startswith("~")


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_merge_creates_missing_file_and_dir(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "dir"
    target = nested / "claude_desktop_config.json"
    assert not nested.exists()

    merge("qvd", "uvx", ["qvd-mcp", "serve"], config_path=target)

    assert target.is_file()
    data = _read_json(target)
    assert data == {
        "mcpServers": {
            "qvd": {"command": "uvx", "args": ["qvd-mcp", "serve"]},
        }
    }
    # No prior content means no backup was written.
    backup = target.with_suffix(target.suffix + ".bak")
    assert not backup.exists()
    # JSON file ends with a trailing newline.
    assert target.read_text(encoding="utf-8").endswith("\n")


def test_merge_second_call_updates_entry_and_writes_bak(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"

    merge("qvd", "uvx", ["qvd-mcp", "serve"], config_path=target)
    first_bytes = target.read_bytes()

    # Second call with different args — should overwrite the entry, not
    # append a duplicate, and should now produce a .bak of the first call.
    merge("qvd", "uv", ["run", "qvd-mcp", "serve"], config_path=target)

    data = _read_json(target)
    assert list(data["mcpServers"].keys()) == ["qvd"]
    assert data["mcpServers"]["qvd"] == {
        "command": "uv",
        "args": ["run", "qvd-mcp", "serve"],
    }

    backup = target.with_suffix(target.suffix + ".bak")
    assert backup.is_file()
    assert backup.read_bytes() == first_bytes


def test_merge_preserves_other_keys_and_servers(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    target.write_text(
        json.dumps(
            {
                "theme": "dark",
                "mcpServers": {
                    "other": {"command": "node", "args": ["server.js"]},
                },
                "misc": [1, 2, 3],
            }
        ),
        encoding="utf-8",
    )

    merge("qvd", "uvx", ["qvd-mcp", "serve"], config_path=target)

    data = _read_json(target)
    assert data["theme"] == "dark"
    assert data["misc"] == [1, 2, 3]
    assert data["mcpServers"]["other"] == {
        "command": "node",
        "args": ["server.js"],
    }
    assert data["mcpServers"]["qvd"] == {
        "command": "uvx",
        "args": ["qvd-mcp", "serve"],
    }


def test_merge_coerces_non_dict_mcp_servers(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    target.write_text(
        json.dumps({"mcpServers": "this should be a dict", "keep": True}),
        encoding="utf-8",
    )

    merge("qvd", "uvx", ["qvd-mcp", "serve"], config_path=target)

    data = _read_json(target)
    assert data["keep"] is True
    assert data["mcpServers"] == {
        "qvd": {"command": "uvx", "args": ["qvd-mcp", "serve"]},
    }


def test_merge_handles_malformed_json(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    garbage = "{{{ not json at all"
    target.write_text(garbage, encoding="utf-8")

    # Should not raise.
    merge("qvd", "uvx", ["qvd-mcp", "serve"], config_path=target)

    data = _read_json(target)
    assert data == {
        "mcpServers": {"qvd": {"command": "uvx", "args": ["qvd-mcp", "serve"]}},
    }

    # The .bak should preserve the garbage verbatim, not a re-serialize.
    backup = target.with_suffix(target.suffix + ".bak")
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == garbage


def test_merge_args_are_copied_not_aliased(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    args = ["qvd-mcp", "serve"]
    merge("qvd", "uvx", args, config_path=target)
    args.append("--mutated")

    data = _read_json(target)
    assert data["mcpServers"]["qvd"]["args"] == ["qvd-mcp", "serve"]


def test_merge_no_tmp_left_behind(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    merge("qvd", "uvx", ["qvd-mcp", "serve"], config_path=target)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["claude_desktop_config.json"]


# ---------------------------------------------------------------------------
# unmerge
# ---------------------------------------------------------------------------


def test_unmerge_missing_file_returns_false(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    assert unmerge("qvd", config_path=target) is False
    assert not target.exists()


def test_unmerge_removes_entry_preserves_siblings(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    target.write_text(
        json.dumps(
            {
                "theme": "dark",
                "mcpServers": {
                    "qvd": {"command": "uvx", "args": ["qvd-mcp", "serve"]},
                    "other": {"command": "node", "args": ["server.js"]},
                },
            }
        ),
        encoding="utf-8",
    )

    assert unmerge("qvd", config_path=target) is True

    data = _read_json(target)
    assert data["theme"] == "dark"
    assert data["mcpServers"] == {
        "other": {"command": "node", "args": ["server.js"]},
    }


def test_unmerge_removes_empty_mcp_servers_key(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    target.write_text(
        json.dumps(
            {
                "theme": "dark",
                "mcpServers": {
                    "qvd": {"command": "uvx", "args": ["qvd-mcp", "serve"]},
                },
            }
        ),
        encoding="utf-8",
    )

    assert unmerge("qvd", config_path=target) is True

    data = _read_json(target)
    assert "mcpServers" not in data
    assert data == {"theme": "dark"}


def test_unmerge_absent_entry_returns_false(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    original = {
        "mcpServers": {"other": {"command": "node", "args": ["server.js"]}},
    }
    target.write_text(json.dumps(original), encoding="utf-8")

    assert unmerge("qvd", config_path=target) is False

    # File unchanged.
    assert _read_json(target) == original


def test_unmerge_leaves_bak_alone(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    backup = target.with_suffix(target.suffix + ".bak")

    # Create a file + .bak via two merges.
    merge("qvd", "uvx", ["qvd-mcp", "serve"], config_path=target)
    merge("qvd", "uvx", ["qvd-mcp", "serve", "--v2"], config_path=target)
    assert backup.is_file()
    bak_bytes = backup.read_bytes()

    assert unmerge("qvd", config_path=target) is True

    # Backup still present and byte-identical.
    assert backup.is_file()
    assert backup.read_bytes() == bak_bytes


def test_unmerge_malformed_json_returns_false(tmp_path: Path) -> None:
    target = tmp_path / "claude_desktop_config.json"
    garbage = "}}} not json"
    target.write_text(garbage, encoding="utf-8")

    assert unmerge("qvd", config_path=target) is False
    # File unchanged — we don't rewrite garbage we can't safely modify.
    assert target.read_text(encoding="utf-8") == garbage
