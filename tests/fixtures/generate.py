"""Synthesize QVDs for tests.

Uses PyQvd's own writer so the fixtures are guaranteed to round-trip through
the same parser under test. Generated at test-time from a tmp_path; we
never commit .qvd files to the repo.
"""
from __future__ import annotations

from pathlib import Path

from pyqvd import (
    DoubleValue,
    DualIntegerValue,
    IntegerValue,
    QvdTable,
    StringValue,
    TimestampValue,
)


def _plain_numbers() -> QvdTable:
    return QvdTable.from_dict(
        {
            "columns": ["id", "value"],
            "data": [[IntegerValue(i), DoubleValue(i * 1.5)] for i in range(1, 6)],
        }
    )


def _strings_and_nulls() -> QvdTable:
    return QvdTable.from_dict(
        {
            "columns": ["name", "note"],
            "data": [
                [StringValue("Alpha"), StringValue("first")],
                [StringValue("Beta"), None],
                [StringValue("Gamma"), StringValue("third")],
            ],
        }
    )


def _timestamps() -> QvdTable:
    return QvdTable.from_dict(
        {
            "columns": ["event", "when"],
            "data": [
                [StringValue("A"), TimestampValue(46203.0, "2026-04-19")],
                [StringValue("B"), TimestampValue(46204.5, "2026-04-20 12:00")],
            ],
        }
    )


def _duals() -> QvdTable:
    return QvdTable.from_dict(
        {
            "columns": ["code", "label"],
            "data": [
                [DualIntegerValue(1, "Alpha"), StringValue("a")],
                [DualIntegerValue(2, "Beta"), StringValue("b")],
                [DualIntegerValue(3, "Gamma"), StringValue("c")],
            ],
        }
    )


def _unicode_and_spaces() -> QvdTable:
    return QvdTable.from_dict(
        {
            "columns": ["produkt", "forsaljning"],
            "data": [
                [StringValue("Äpple"), IntegerValue(100)],
                [StringValue("Päron"), IntegerValue(50)],
            ],
        }
    )


def generate_all(dest: Path) -> dict[str, Path]:
    """Write a curated set of QVDs to ``dest``. Returns ``{case: path}``."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    cases: dict[str, tuple[QvdTable, Path]] = {
        "plain_numbers": (_plain_numbers(), dest / "plain_numbers.qvd"),
        "strings": (_strings_and_nulls(), dest / "strings.qvd"),
        "timestamps": (_timestamps(), dest / "timestamps.qvd"),
        "duals": (_duals(), dest / "duals.qvd"),
        "unicode": (_unicode_and_spaces(), dest / "Sales 2024.qvd"),
    }
    for table, path in cases.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        table.to_qvd(str(path))
    return {name: path for name, (_, path) in cases.items()}
