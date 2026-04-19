"""QVD → Parquet conversion with skip-if-unchanged.

Single entry point: :func:`run_once`. The CLI wraps it; the MCP ``refresh``
tool calls it. Failures are per-file: one bad QVD never aborts a pass.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version

import pyarrow as pa
import pyarrow.parquet as pq

from qvd_mcp import naming, state
from qvd_mcp.config import Config
from qvd_mcp.readers import Reader, ReaderError
from qvd_mcp.readers.pyqvd_reader import PyQvdReader

log = logging.getLogger(__name__)


@dataclass
class ConvertReport:
    converted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (path, reason)
    pruned: list[str] = field(default_factory=list)

    @property
    def any_failed(self) -> bool:
        return bool(self.failed)

    def summary(self) -> str:
        return (
            f"converted={len(self.converted)} skipped={len(self.skipped)} "
            f"failed={len(self.failed)} pruned={len(self.pruned)}"
        )


def _get_reader(name: str) -> Reader:
    if name == "pyqvd":
        return PyQvdReader()
    raise ValueError(f"unknown reader: {name!r}")


def _reader_version(name: str) -> str:
    pkg = {"pyqvd": "pyqvd"}.get(name, name)
    try:
        return version(pkg)
    except PackageNotFoundError:
        return ""


def run_once(config: Config) -> ConvertReport:
    """One full conversion pass over ``config.source_dir``.

    Skip-if-unchanged uses ``(mtime_ns, size)`` only — cheap, and plenty
    accurate for QVDs in practice.
    """
    reader = _get_reader(config.reader)
    config.cache_dir.mkdir(parents=True, exist_ok=True)

    prior = state.load(config.cache_dir)
    qvds = sorted(config.source_dir.rglob("*.qvd"))
    report = ConvertReport()

    # Seed taken names with the view names of files still present, so we
    # reuse them stably and only allocate fresh names for new files.
    taken_names: set[str] = set()
    present_keys = {str(p) for p in qvds}
    for src_key, entry in prior.entries.items():
        if src_key in present_keys:
            taken_names.add(entry.view_name)

    new_entries: dict[str, state.StateEntry] = {}

    for qvd_path in qvds:
        src_key = str(qvd_path)
        try:
            st = qvd_path.stat()
        except OSError as exc:
            log.warning("stat failed for %s: %s (placeholder? permission?)", qvd_path, exc)
            report.failed.append((src_key, f"stat: {exc}"))
            continue

        mtime_ns, size = st.st_mtime_ns, st.st_size
        prior_entry = prior.entries.get(src_key)

        if prior_entry and prior_entry.source_mtime_ns == mtime_ns and prior_entry.source_size == size:
            parquet = config.cache_dir / prior_entry.parquet_path
            if parquet.is_file():
                log.debug("skip %s (unchanged)", qvd_path.name)
                report.skipped.append(src_key)
                new_entries[src_key] = prior_entry
                continue
            # State says converted, parquet is missing — fall through and redo.

        view_name = prior_entry.view_name if prior_entry else naming.view_name_for(
            qvd_path, taken_names
        )
        taken_names.add(view_name)

        try:
            table = reader.read(qvd_path)
        except ReaderError as exc:
            log.error("read failed: %s", exc)
            report.failed.append((src_key, f"read: {exc.cause.__class__.__name__}: {exc.cause}"))
            continue

        parquet_name = f"{view_name}.parquet"
        final_path = config.cache_dir / parquet_name
        tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")

        try:
            pq.write_table(table, str(tmp_path), compression="zstd")
            os.replace(tmp_path, final_path)
        except (OSError, pa.ArrowException) as exc:
            log.error("write failed for %s: %s", final_path, exc)
            tmp_path.unlink(missing_ok=True)
            report.failed.append((src_key, f"write: {exc}"))
            continue

        new_entries[src_key] = state.StateEntry(
            view_name=view_name,
            parquet_path=parquet_name,
            source_mtime_ns=mtime_ns,
            source_size=size,
            converted_at=state.now_iso(),
            rows=table.num_rows,
            columns=table.num_columns,
        )
        report.converted.append(src_key)
        log.info(
            "converted %s → %s (%d rows, %d cols)",
            qvd_path.name,
            parquet_name,
            table.num_rows,
            table.num_columns,
        )

    # Orphan pruning: drop state entries and parquets for QVDs that disappeared.
    for src_key, entry in prior.entries.items():
        if src_key in new_entries:
            continue
        report.pruned.append(src_key)
        orphan = config.cache_dir / entry.parquet_path
        try:
            orphan.unlink(missing_ok=True)
            log.info("pruned orphan parquet: %s", entry.parquet_path)
        except OSError as exc:
            log.warning("could not delete orphan parquet %s: %s", orphan, exc)

    new_state = state.State(
        schema_version=state.SCHEMA_VERSION,
        reader=config.reader,
        reader_version=_reader_version(config.reader),
        entries=new_entries,
    )
    state.save(config.cache_dir, new_state)

    return report
