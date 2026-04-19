"""Reader protocol.

A reader's one job: take a QVD path, hand back a PyArrow table. Two
implementations are anticipated (PyQvd now; qvd-rs as an opt-in extra later),
which is the bar for earning a Protocol.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pyarrow as pa


class Reader(Protocol):
    name: str  # e.g. "pyqvd", recorded in state for observability.

    def read(self, qvd_path: Path) -> pa.Table:
        """Parse ``qvd_path`` and return an Arrow table.

        Raise :class:`ReaderError` on any failure. The caller treats this as
        a per-file error and continues the pass.
        """
        ...


class ReaderError(Exception):
    """Raised when a reader cannot parse a given QVD file."""

    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"failed to read {path}: {cause}")
        self.path = path
        self.cause = cause
