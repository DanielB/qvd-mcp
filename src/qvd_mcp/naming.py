"""View name normalization: QVD filename -> SQL identifier.

The rules are deliberately boring. QVD filenames can contain spaces,
punctuation, Unicode, SQL reserved words, and leading digits. We smooth
them into a predictable snake_case identifier so users can type
``SELECT * FROM sales`` instead of quoting.
"""
from __future__ import annotations

import re
from pathlib import Path

# Not exhaustive — just the reserved words that realistically turn up as
# filenames. DuckDB accepts most words unquoted, but ``Order.qvd`` is
# common enough that we handle it.
_RESERVED = frozenset(
    {
        "all", "alter", "and", "any", "as", "between", "by", "case", "check",
        "constraint", "create", "default", "delete", "distinct", "drop",
        "else", "end", "except", "false", "foreign", "from", "group", "having",
        "in", "index", "insert", "intersect", "is", "join", "key", "like",
        "limit", "not", "null", "offset", "on", "or", "order", "primary",
        "references", "select", "table", "then", "true", "union", "unique",
        "update", "user", "using", "view", "when", "where", "with",
    }
)

# Collapse *any* run of non-alphanumeric characters — including existing
# underscores — into a single underscore. That way ``Sales__Report`` and
# ``Sales  Report`` and ``Sales--Report`` all normalize identically.
_NON_IDENT = re.compile(r"[^a-z0-9]+")


def normalize(stem: str) -> str:
    """Pure rules, no collision handling. Exported for direct testing."""
    lowered = stem.lower()
    replaced = _NON_IDENT.sub("_", lowered)
    stripped = replaced.strip("_")
    if not stripped:
        return "qvd"
    if stripped[0].isdigit():
        stripped = "_" + stripped
    if stripped in _RESERVED:
        stripped = f"{stripped}_view"
    return stripped


def view_name_for(qvd_path: Path, taken: set[str]) -> str:
    """Return a unique SQL identifier for ``qvd_path``.

    On collision, appends ``_2``, ``_3``, ... until unique. Callers should
    feed QVDs in a deterministic order (e.g. sorted by absolute path) so
    the mapping is stable across runs.
    """
    base = normalize(qvd_path.stem)
    if base not in taken:
        return base
    i = 2
    while True:
        candidate = f"{base}_{i}"
        if candidate not in taken:
            return candidate
        i += 1
