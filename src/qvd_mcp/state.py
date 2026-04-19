"""Cache state: tracks which QVDs are converted and their source signatures."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

STATE_FILENAME = ".qvd-mcp-state.json"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class StateEntry:
    view_name: str
    parquet_path: str  # relative to cache_dir
    source_mtime_ns: int
    source_size: int
    converted_at: str  # ISO 8601, UTC
    rows: int
    columns: int


@dataclass
class State:
    schema_version: int = SCHEMA_VERSION
    reader: str = "pyqvd"
    reader_version: str = ""
    entries: dict[str, StateEntry] = field(default_factory=dict)

    def matches(self, source_path: str, mtime_ns: int, size: int) -> bool:
        entry = self.entries.get(source_path)
        return bool(
            entry and entry.source_mtime_ns == mtime_ns and entry.source_size == size
        )


def now_iso() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def load(cache_dir: Path) -> State:
    """Load state. Missing or corrupt file is treated as 'no prior state'.

    A fresh conversion pass is cheaper than acting on bad bookkeeping.
    """
    path = cache_dir / STATE_FILENAME
    if not path.is_file():
        return State()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return State()

    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return State()

    entries_raw = raw.get("entries") or {}
    entries: dict[str, StateEntry] = {}
    if isinstance(entries_raw, dict):
        for key, value in entries_raw.items():
            if not isinstance(value, dict):
                continue
            try:
                entries[str(key)] = StateEntry(
                    view_name=str(value["view_name"]),
                    parquet_path=str(value["parquet_path"]),
                    source_mtime_ns=int(value["source_mtime_ns"]),
                    source_size=int(value["source_size"]),
                    converted_at=str(value["converted_at"]),
                    rows=int(value["rows"]),
                    columns=int(value["columns"]),
                )
            except (KeyError, TypeError, ValueError):
                continue

    return State(
        schema_version=SCHEMA_VERSION,
        reader=str(raw.get("reader", "pyqvd")),
        reader_version=str(raw.get("reader_version", "")),
        entries=entries,
    )


def save(cache_dir: Path, state: State) -> None:
    """Atomic write via ``.tmp`` + ``os.replace()``.

    Matters because ``convert`` and ``serve`` can run concurrently.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / STATE_FILENAME
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = {
        "schema_version": state.schema_version,
        "reader": state.reader,
        "reader_version": state.reader_version,
        "entries": {k: asdict(v) for k, v in state.entries.items()},
    }
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
