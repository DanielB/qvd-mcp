import json
from pathlib import Path
from typing import Any

from qvd_mcp.state import State, StateEntry, load, save


def _entry(**overrides: Any) -> StateEntry:
    base: dict[str, Any] = {
        "view_name": "sales",
        "parquet_path": "sales.parquet",
        "source_mtime_ns": 1000,
        "source_size": 42,
        "converted_at": "2026-01-01T00:00:00Z",
        "rows": 10,
        "columns": 3,
    }
    base.update(overrides)
    return StateEntry(**base)


def test_roundtrip(tmp_path: Path) -> None:
    state = State(reader="pyqvd", reader_version="2.3.2", entries={"/x.qvd": _entry()})
    save(tmp_path, state)
    got = load(tmp_path)
    assert got.reader == "pyqvd"
    assert got.reader_version == "2.3.2"
    assert got.entries["/x.qvd"] == _entry()


def test_load_missing_file(tmp_path: Path) -> None:
    got = load(tmp_path)
    assert got.schema_version == 1
    assert got.entries == {}


def test_load_corrupt_json(tmp_path: Path) -> None:
    (tmp_path / ".qvd-mcp-state.json").write_text("{{{ not json")
    got = load(tmp_path)
    assert got.entries == {}


def test_load_wrong_schema_version(tmp_path: Path) -> None:
    (tmp_path / ".qvd-mcp-state.json").write_text(
        json.dumps({"schema_version": 999, "entries": {}})
    )
    got = load(tmp_path)
    assert got.schema_version == 1
    assert got.entries == {}


def test_load_skips_malformed_entries(tmp_path: Path) -> None:
    (tmp_path / ".qvd-mcp-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reader": "pyqvd",
                "entries": {
                    "/good.qvd": {
                        "view_name": "good",
                        "parquet_path": "good.parquet",
                        "source_mtime_ns": 1,
                        "source_size": 2,
                        "converted_at": "x",
                        "rows": 1,
                        "columns": 1,
                    },
                    "/bad.qvd": {"view_name": "bad"},  # missing fields
                },
            }
        )
    )
    got = load(tmp_path)
    assert "/good.qvd" in got.entries
    assert "/bad.qvd" not in got.entries


def test_matches(tmp_path: Path) -> None:
    state = State(entries={"/x.qvd": _entry(source_mtime_ns=1, source_size=2)})
    assert state.matches("/x.qvd", 1, 2)
    assert not state.matches("/x.qvd", 1, 3)
    assert not state.matches("/y.qvd", 1, 2)


def test_atomic_save_leaves_no_tmp(tmp_path: Path) -> None:
    save(tmp_path, State(entries={"/x.qvd": _entry()}))
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [".qvd-mcp-state.json"]
