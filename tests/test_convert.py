from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from qvd_mcp import state as state_module
from qvd_mcp.config import Config
from qvd_mcp.convert import discover_qvds, run_once
from tests.fixtures.generate import generate_all


def _cfg(
    source: Path,
    cache: Path,
    *,
    include: tuple[str, ...] = ("*.qvd",),
    exclude: tuple[str, ...] = (),
) -> Config:
    return Config(source_dir=source, cache_dir=cache, include=include, exclude=exclude)


def test_fresh_conversion_produces_parquets(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    files = generate_all(source)

    report = run_once(_cfg(source, cache))

    assert len(report.converted) == len(files)
    assert not report.failed
    assert not report.skipped
    parquets = sorted(p.name for p in cache.glob("*.parquet"))
    assert len(parquets) == len(files)

    loaded = state_module.load(cache)
    assert len(loaded.entries) == len(files)


def test_second_pass_skips_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    files = generate_all(source)
    run_once(_cfg(source, cache))

    report = run_once(_cfg(source, cache))
    assert not report.converted
    assert len(report.skipped) == len(files)
    assert not report.failed


def test_modified_qvd_is_reconverted(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    files = generate_all(source)
    run_once(_cfg(source, cache))

    # Touch one file to change mtime + size (overwriting with a fresh QVD)
    import time
    time.sleep(0.02)
    from pyqvd import IntegerValue, QvdTable

    new_table = QvdTable.from_dict(
        {"columns": ["x"], "data": [[IntegerValue(i)] for i in range(100)]}
    )
    new_table.to_qvd(str(files["plain_numbers"]))

    report = run_once(_cfg(source, cache))
    assert len(report.converted) == 1
    assert any("plain_numbers" in p for p in report.converted)


def test_deleted_qvd_is_pruned(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    files = generate_all(source)
    run_once(_cfg(source, cache))
    before = state_module.load(cache)
    victim_entry = next(e for key, e in before.entries.items() if key.endswith("duals.qvd"))
    orphan = cache / victim_entry.parquet_path
    assert orphan.exists()

    files["duals"].unlink()

    report = run_once(_cfg(source, cache))
    assert len(report.pruned) == 1
    assert not orphan.exists()


def test_corrupt_file_does_not_abort_the_pass(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    source.mkdir()
    (source / "bogus.qvd").write_bytes(b"definitely not a qvd")
    generate_all(source)

    report = run_once(_cfg(source, cache))
    assert len(report.failed) == 1
    assert report.converted  # the real ones still made it


def test_view_name_normalization_is_applied(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)  # includes "Sales 2024.qvd"

    run_once(_cfg(source, cache))
    st = state_module.load(cache)
    view_names = {e.view_name for e in st.entries.values()}
    assert "sales_2024" in view_names
    assert (cache / "sales_2024.parquet").is_file()


def test_discover_qvds_default_include_catches_all(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)
    found = discover_qvds(_cfg(source, cache))
    # All five synthetic fixtures show up with the default include.
    assert {p.name for p in found} == {
        "plain_numbers.qvd",
        "strings.qvd",
        "timestamps.qvd",
        "duals.qvd",
        "Sales 2024.qvd",
    }


def test_discover_qvds_include_scopes_set(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)
    found = discover_qvds(_cfg(source, cache, include=("plain_*.qvd", "duals.qvd")))
    assert {p.name for p in found} == {"plain_numbers.qvd", "duals.qvd"}


def test_discover_qvds_exclude_wins_over_include(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)
    found = discover_qvds(
        _cfg(source, cache, include=("*.qvd",), exclude=("*duals*", "Sales*"))
    )
    assert {p.name for p in found} == {
        "plain_numbers.qvd",
        "strings.qvd",
        "timestamps.qvd",
    }


def test_discover_qvds_exclude_matches_relative_subpath(tmp_path: Path) -> None:
    # Exclude patterns should match against the path relative to source_dir,
    # so ``archive/*`` drops a whole subdirectory.
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)
    archive = source / "archive"
    archive.mkdir()
    (archive / "old.qvd").write_bytes((source / "plain_numbers.qvd").read_bytes())

    found = discover_qvds(_cfg(source, cache, exclude=("archive/*",)))
    assert all("archive" not in str(p) for p in found)
    # The canonical fixtures at the top level survive.
    assert any(p.name == "plain_numbers.qvd" for p in found)


def test_run_once_respects_include_filter(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)

    report = run_once(_cfg(source, cache, include=("duals.qvd",)))

    assert len(report.converted) == 1
    parquets = {p.name for p in cache.glob("*.parquet")}
    assert parquets == {"duals.parquet"}


def test_atomic_write_leaves_no_tmp_files(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)
    run_once(_cfg(source, cache))
    leftover = list(cache.glob("*.tmp"))
    assert not leftover


def test_run_once_is_noop_when_source_dir_is_none(tmp_path: Path) -> None:
    # Regression: a cache-only config (source_dir=None) used to trip
    # the orphan-prune loop, which interpreted the empty discovery as
    # "every cached parquet has vanished" and unlinked them all.
    cache = tmp_path / "cache"
    cache.mkdir()
    shared_a = cache / "sales.parquet"
    shared_b = cache / "inventory.parquet"
    pq.write_table(pa.table({"x": [1, 2]}), str(shared_a))
    pq.write_table(pa.table({"y": [3]}), str(shared_b))

    prior = state_module.State(
        entries={
            "/producer/src/sales.qvd": state_module.StateEntry(
                view_name="sales",
                parquet_path="sales.parquet",
                source_mtime_ns=0,
                source_size=0,
                converted_at=state_module.now_iso(),
                rows=2,
                columns=1,
            ),
            "/producer/src/inventory.qvd": state_module.StateEntry(
                view_name="inventory",
                parquet_path="inventory.parquet",
                source_mtime_ns=0,
                source_size=0,
                converted_at=state_module.now_iso(),
                rows=1,
                columns=1,
            ),
        }
    )
    state_module.save(cache, prior)

    config = Config(cache_dir=cache, source_dir=None)
    report = run_once(config)

    assert shared_a.is_file()
    assert shared_b.is_file()
    assert report.converted == []
    assert report.skipped == []
    assert report.failed == []
    assert report.pruned == []
