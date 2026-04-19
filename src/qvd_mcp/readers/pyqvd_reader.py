"""Reader backed by the PyQvd library."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
from pyqvd import QvdTable

from qvd_mcp.readers import ReaderError


class PyQvdReader:
    """PyQvd-backed reader. Handles Qlik's quirkier value types sensibly:

    - **Dual fields** (``(number, string)``): PyQvd's ``to_dict`` unwraps to
      the numeric side, which is usually what SQL wants. The display side is
      dropped — acceptable trade-off for Phase 1.
    - **Timestamps / dates**: already Python ``datetime`` / ``date`` when
      they reach us, so Arrow infers the right columnar type.
    - **Money**: comes back as :class:`decimal.Decimal`. We cast to
      ``float64`` rather than fight Arrow's precision/scale rules; users
      who need exact decimals can revisit in a later phase.
    - **Nulls**: plain Python ``None``; Arrow handles them natively.
    """

    name = "pyqvd"

    def read(self, qvd_path: Path) -> pa.Table:
        try:
            source = QvdTable.from_qvd(str(qvd_path))
        except Exception as exc:  # noqa: BLE001 — wrap any underlying failure
            raise ReaderError(qvd_path, exc) from exc

        dump = source.to_dict()
        columns: list[str] = list(dump["columns"])
        data: list[list[Any]] = list(dump["data"])

        if not data:
            # No rows means Arrow can't infer types. Emit a typed-but-empty
            # table with string columns; callers get a valid schema.
            return pa.table({c: pa.array([], type=pa.string()) for c in columns})

        arrays: dict[str, pa.Array] = {}
        for i, name in enumerate(columns):
            values = [row[i] for row in data]
            values = [float(v) if isinstance(v, Decimal) else v for v in values]
            try:
                arrays[name] = pa.array(values)
            except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
                # Mixed/unsupported types in a single column — fall back to
                # string representation so the table still lands in parquet.
                arrays[name] = pa.array([None if v is None else str(v) for v in values])
                del exc  # swallow intentionally; fallback is the recovery path
        return pa.table(arrays)
