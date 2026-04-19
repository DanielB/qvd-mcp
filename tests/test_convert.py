from pathlib import Path

from qvd_mcp import state as state_module
from qvd_mcp.config import Config
from qvd_mcp.convert import run_once
from tests.fixtures.generate import generate_all


def _cfg(source: Path, cache: Path) -> Config:
    return Config(source_dir=source, cache_dir=cache)


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


def test_atomic_write_leaves_no_tmp_files(tmp_path: Path) -> None:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)
    run_once(_cfg(source, cache))
    leftover = list(cache.glob("*.tmp"))
    assert not leftover
