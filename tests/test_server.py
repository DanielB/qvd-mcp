from __future__ import annotations

import dataclasses
import time
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyqvd import DoubleValue, IntegerValue, QvdTable

from qvd_mcp import server as server_module
from qvd_mcp import state as state_module
from qvd_mcp.config import Config
from qvd_mcp.convert import run_once
from qvd_mcp.state import State, StateEntry
from tests.fixtures.generate import generate_all


@pytest.fixture
def loaded_ctx(tmp_path: Path) -> Iterator[server_module.ServerContext]:
    source = tmp_path / "src"
    cache = tmp_path / "cache"
    generate_all(source)
    config = Config(source_dir=source, cache_dir=cache)
    report = run_once(config)
    assert not report.failed
    state = state_module.load(cache)
    conn = server_module._build_connection(config, state)
    ctx = server_module.ServerContext(config=config, conn=conn, state=state)
    server_module.set_context(ctx)
    try:
        yield ctx
    finally:
        server_module.set_context(None)


def test_list_qvds_returns_all_views(loaded_ctx: server_module.ServerContext) -> None:
    listing = server_module.list_qvds()
    names = {row["view_name"] for row in listing}
    # fixtures include: plain_numbers, strings, timestamps, duals, "Sales 2024"
    assert "plain_numbers" in names
    assert "sales_2024" in names
    assert all(row["rows"] >= 0 for row in listing)


def test_describe_unknown_view_returns_error(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.describe_qvd("does_not_exist")
    assert "error" in result
    assert result["error"]["type"] == "UnknownView"


def test_describe_known_view(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.describe_qvd("plain_numbers")
    assert result["view_name"] == "plain_numbers"
    assert any(c["name"] == "id" for c in result["columns"])
    assert any(c["name"] == "value" for c in result["columns"])


def test_sample_qvd_limits_rows(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.sample_qvd("plain_numbers", n=2)
    assert result["row_count"] == 2
    assert len(result["rows"]) == 2


def test_sample_qvd_caps_at_1000(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.sample_qvd("plain_numbers", n=999999)
    # fixture only has 5 rows, but the cap itself shouldn't crash
    assert result["row_count"] <= 1000


def test_sample_unknown_view(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.sample_qvd("nope")
    assert "error" in result


def test_query_basic(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.run_sql("SELECT COUNT(*) AS n FROM plain_numbers")
    assert "error" not in result
    # Rows are positional arrays aligned with ``columns``.
    assert result["columns"] == ["n"]
    assert result["rows"][0][0] == 5


def test_query_rejects_read_parquet(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.run_sql("SELECT * FROM read_parquet('/etc/passwd')")
    assert result.get("error", {}).get("type") == "Rejected"


def test_query_rejects_read_text(loaded_ctx: server_module.ServerContext) -> None:
    # DuckDB's read_text lets you slurp arbitrary files as a single row.
    # This is the bug the code reviewer flagged — it MUST be rejected.
    result = server_module.run_sql("SELECT * FROM read_text('/etc/hostname')")
    assert result.get("error", {}).get("type") == "Rejected"


def test_query_rejects_read_blob(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.run_sql("SELECT * FROM read_blob('/etc/hostname')")
    assert result.get("error", {}).get("type") == "Rejected"


def test_query_rejects_attach(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.run_sql("ATTACH '/tmp/something.db' AS foo")
    assert result.get("error", {}).get("type") == "Rejected"


def test_query_rejects_copy_statement(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.run_sql("COPY plain_numbers TO '/tmp/x.csv'")
    assert result.get("error", {}).get("type") == "Rejected"


def test_query_allows_reserved_words_as_identifiers(loaded_ctx: server_module.ServerContext) -> None:
    # ``load``, ``copy``, ``glob`` used to false-positive because the regex
    # matched them as bare words. They're legal SQL aliases and likely
    # business column names; they should pass through when not in
    # function-call or statement-prefix position.
    result = server_module.run_sql("SELECT 1 AS load, 2 AS copy, 3 AS glob, 4 AS pragma")
    assert "error" not in result
    assert result["columns"] == ["load", "copy", "glob", "pragma"]
    assert result["rows"][0] == [1, 2, 3, 4]


def test_query_allows_semicolon_terminated_select(loaded_ctx: server_module.ServerContext) -> None:
    # Trailing semicolon shouldn't trip the statement-form regex.
    result = server_module.run_sql("SELECT 1 AS x;")
    assert "error" not in result


def test_query_timeout_returns_clean_error(
    loaded_ctx: server_module.ServerContext,
) -> None:
    # Verify the error-shape contract for InterruptException without relying
    # on a reliably-slow query. We swap the connection for a stub that
    # raises InterruptException on ``execute`` — the same exception type
    # ``conn.interrupt()`` triggers in production.
    import duckdb

    class _InterruptingConn:
        def execute(self, _sql: str) -> object:
            raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")

        def interrupt(self) -> None:
            pass

    loaded_ctx.conn = _InterruptingConn()  # type: ignore[assignment]
    result = server_module.run_sql("SELECT 1")
    assert result.get("error", {}).get("type") == "Timeout"
    assert "exceeded" in result["error"]["message"]


def test_query_syntax_error_returns_clean_message(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.run_sql("SELECT NOT_A_REAL_COLUMN FROM plain_numbers WHERE")
    assert "error" in result
    # DuckDB error class surfaced, not a Python traceback
    assert result["error"]["type"] != "Exception"


def test_query_truncation_flag(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.run_sql("SELECT * FROM plain_numbers", max_rows=2)
    assert result["row_count"] == 2
    assert result["truncated"] is True


def test_search_columns_case_insensitive(loaded_ctx: server_module.ServerContext) -> None:
    hits = server_module.search_columns("VALUE")
    assert any(h["column_name"] == "value" for h in hits)


def test_search_columns_case_sensitive_no_match(loaded_ctx: server_module.ServerContext) -> None:
    hits = server_module.search_columns("VALUE", case_sensitive=True)
    assert not any(h["column_name"] == "value" for h in hits)


def test_refresh_runs_conversion(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.refresh()
    assert "summary" in result
    assert result["failed"] == 0


def _rewrite_plain_numbers(path: Path) -> None:
    """Overwrite ``plain_numbers.qvd`` with different content so mtime+size shift."""
    table = QvdTable.from_dict(
        {
            "columns": ["id", "value"],
            "data": [[IntegerValue(i), DoubleValue(i * 2.5)] for i in range(1, 11)],
        }
    )
    table.to_qvd(str(path))


def test_auto_refresh_triggers_on_mtime_change(
    loaded_ctx: server_module.ServerContext,
) -> None:
    src_key = str(loaded_ctx.config.source_dir / "plain_numbers.qvd")
    original_mtime = loaded_ctx.state.entries[src_key].source_mtime_ns

    time.sleep(0.02)  # APFS can coalesce mtimes; nudge past the resolution floor
    _rewrite_plain_numbers(Path(src_key))
    server_module._last_check = 0.0  # force a re-probe on the next tool call

    server_module.list_qvds()

    refreshed_entry = loaded_ctx.state.entries[src_key]
    assert refreshed_entry.source_mtime_ns != original_mtime


def test_auto_refresh_debounced(loaded_ctx: server_module.ServerContext) -> None:
    src_key = str(loaded_ctx.config.source_dir / "plain_numbers.qvd")
    original_mtime = loaded_ctx.state.entries[src_key].source_mtime_ns

    # Pretend we just finished a probe — next call should bail on the debounce.
    server_module._last_check = time.monotonic()

    time.sleep(0.02)
    _rewrite_plain_numbers(Path(src_key))

    server_module.list_qvds()
    assert loaded_ctx.state.entries[src_key].source_mtime_ns == original_mtime

    # Push the last-check timestamp past the debounce window; the next call
    # should now pick up the drift and refresh.
    server_module._last_check = time.monotonic() - (
        loaded_ctx.config.auto_refresh_debounce_s + 1
    )
    server_module.list_qvds()
    assert loaded_ctx.state.entries[src_key].source_mtime_ns != original_mtime


def test_auto_refresh_disabled_when_debounce_zero(
    loaded_ctx: server_module.ServerContext,
) -> None:
    loaded_ctx.config = dataclasses.replace(loaded_ctx.config, auto_refresh_debounce_s=0)
    src_key = str(loaded_ctx.config.source_dir / "plain_numbers.qvd")
    original_mtime = loaded_ctx.state.entries[src_key].source_mtime_ns

    server_module._last_check = 0.0
    time.sleep(0.02)
    _rewrite_plain_numbers(Path(src_key))

    server_module.list_qvds()
    assert loaded_ctx.state.entries[src_key].source_mtime_ns == original_mtime


# ---- Cache-only (no source_dir) tests -------------------------------------


def _config_no_source(cache: Path) -> Config:
    return Config(cache_dir=cache, source_dir=None)


def test_auto_refresh_skipped_when_source_dir_is_none(tmp_path: Path) -> None:
    """Without source_dir, _with_auto_refresh must not stat or prune."""
    cache = tmp_path / "cache"
    cache.mkdir()
    cfg = _config_no_source(cache)
    conn = duckdb.connect(":memory:")
    ctx = server_module.ServerContext(config=cfg, conn=conn, state=State())
    server_module.set_context(ctx)
    try:
        # list_qvds is wrapped with _with_auto_refresh. In live mode with no
        # state it would still run the probe; we want a clean no-op here.
        assert server_module.list_qvds() == []
    finally:
        server_module.set_context(None)


def test_list_qvds_omits_source_path_when_path_gone(tmp_path: Path) -> None:
    """Producer's state.json entries reference paths that don't exist on the
    consumer's disk; output should drop source_path rather than surface
    meaningless producer-local paths."""
    cache = tmp_path / "cache"
    cache.mkdir()
    pq.write_table(pa.table({"x": [1, 2]}), str(cache / "sales.parquet"))

    cfg = _config_no_source(cache)
    conn = duckdb.connect(":memory:")
    state = State(
        entries={
            "/Users/producer/data/sales.qvd": StateEntry(
                view_name="sales",
                parquet_path="sales.parquet",
                source_mtime_ns=0,
                source_size=0,
                converted_at=state_module.now_iso(),
                rows=2,
                columns=1,
            )
        }
    )
    conn.execute(
        f'CREATE OR REPLACE VIEW "sales" '
        f"AS SELECT * FROM read_parquet('{cache / 'sales.parquet'}')"
    )
    ctx = server_module.ServerContext(config=cfg, conn=conn, state=state)
    server_module.set_context(ctx)
    try:
        listing = server_module.list_qvds()
        assert len(listing) == 1
        assert "source_path" not in listing[0]
        assert listing[0]["view_name"] == "sales"
    finally:
        server_module.set_context(None)


def test_list_qvds_keeps_source_path_when_path_exists(tmp_path: Path) -> None:
    """Live producer workflow: source QVD exists on disk, surface its path."""
    cache = tmp_path / "cache"
    cache.mkdir()
    pq.write_table(pa.table({"x": [1]}), str(cache / "live.parquet"))
    source_qvd = tmp_path / "live.qvd"
    source_qvd.write_bytes(b"fake-qvd")  # just needs to exist as a file

    cfg = Config(cache_dir=cache, source_dir=tmp_path)
    conn = duckdb.connect(":memory:")
    state = State(
        entries={
            str(source_qvd): StateEntry(
                view_name="live",
                parquet_path="live.parquet",
                source_mtime_ns=0,
                source_size=0,
                converted_at=state_module.now_iso(),
                rows=1,
                columns=1,
            )
        }
    )
    conn.execute(
        f'CREATE OR REPLACE VIEW "live" '
        f"AS SELECT * FROM read_parquet('{cache / 'live.parquet'}')"
    )
    ctx = server_module.ServerContext(config=cfg, conn=conn, state=state)
    server_module.set_context(ctx)
    # Force the auto-refresh debounce to skip; otherwise the probe would see
    # the bogus ``source_mtime_ns=0`` and try to run a conversion pass on the
    # fake QVD, which would fail. Other tests in this module twiddle
    # ``_last_check`` the same way.
    server_module._last_check = time.monotonic()
    try:
        listing = server_module.list_qvds()
        assert listing[0]["source_path"] == str(source_qvd)
    finally:
        server_module.set_context(None)


# ---- run_sql byte-size and recommendation tests ---------------------------


def _ctx_with_view(
    tmp_path: Path, table: pa.Table, view_name: str = "big"
) -> server_module.ServerContext:
    """Build a server context backed by ``table`` materialised as a parquet.

    Used to exercise run_sql under realistic data shapes — narrow numeric,
    wide text, etc. — since the byte-based warning depends on actual
    serialisation size, not row count alone.
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    parquet = cache / f"{view_name}.parquet"
    pq.write_table(table, str(parquet))
    cfg = Config(cache_dir=cache, source_dir=None)
    conn = duckdb.connect(":memory:")
    conn.execute(
        f'CREATE OR REPLACE VIEW "{view_name}" '
        f"AS SELECT * FROM read_parquet('{parquet}')"
    )
    state = State(
        entries={
            f"cache://{view_name}.parquet": StateEntry(
                view_name=view_name,
                parquet_path=f"{view_name}.parquet",
                source_mtime_ns=0,
                source_size=0,
                converted_at=state_module.now_iso(),
                rows=table.num_rows,
                columns=table.num_columns,
            )
        }
    )
    return server_module.ServerContext(config=cfg, conn=conn, state=state)


def test_run_sql_small_result_has_no_warning(tmp_path: Path) -> None:
    table = pa.table({"n": list(range(50))})
    ctx = _ctx_with_view(tmp_path, table)
    server_module.set_context(ctx)
    try:
        result = server_module.run_sql("SELECT * FROM big", max_rows=10_000)
    finally:
        server_module.set_context(None)
    assert result["row_count"] == 50
    assert "warning" not in result
    assert result["truncated"] is False


def test_run_sql_wide_narrow_data_20k_rows_no_warning(tmp_path: Path) -> None:
    """User's honest case: 20 000 rows × 2 narrow integer columns is cheap.

    The row-based threshold this replaces would have warned here needlessly;
    the byte measure correctly stays silent because the actual payload is
    well under 500 KB.
    """
    rows = 20_000
    table = pa.table({"id": list(range(rows)), "n": list(range(rows))})
    ctx = _ctx_with_view(tmp_path, table)
    server_module.set_context(ctx)
    try:
        result = server_module.run_sql("SELECT * FROM big", max_rows=rows)
    finally:
        server_module.set_context(None)
    assert result["row_count"] == rows
    assert "warning" not in result, (
        f"narrow 20k-row result should not warn (got: {result.get('warning')})"
    )


def test_run_sql_text_heavy_result_warns_at_low_row_count(tmp_path: Path) -> None:
    """Counter-case: a thousand rows of long text columns crosses the byte
    threshold and should warn. Row-count thresholds would have missed this."""
    rows = 1_000
    long_str = "x" * 800  # 800-byte payload per row
    table = pa.table(
        {
            "id": list(range(rows)),
            "body": [long_str] * rows,
        }
    )
    ctx = _ctx_with_view(tmp_path, table)
    server_module.set_context(ctx)
    try:
        result = server_module.run_sql("SELECT * FROM big", max_rows=rows)
    finally:
        server_module.set_context(None)
    assert result["row_count"] == rows
    assert "warning" in result
    # The warning surfaces the actual size in KB, a token estimate, and
    # context-size reference points so the LLM can reason about whether
    # the result will overflow its current window.
    assert "KB" in result["warning"]
    assert "tokens" in result["warning"]
    assert "200k" in result["warning"]  # Sonnet/Haiku context reference
    assert "1M" in result["warning"]    # Opus context reference


def test_run_sql_returns_all_rows_when_under_byte_budget(tmp_path: Path) -> None:
    """With no row cap, a 50 000-row narrow-integer query serialises to
    ~650 KB and comes back in full — well under the 2 MB hard cap."""
    table = pa.table({"n": list(range(50_000))})
    ctx = _ctx_with_view(tmp_path, table)
    server_module.set_context(ctx)
    try:
        result = server_module.run_sql("SELECT * FROM big", max_rows=100_000)
    finally:
        server_module.set_context(None)
    assert result["row_count"] == 50_000
    assert result["truncated"] is False


def test_run_sql_byte_cap_truncates_oversized_result(tmp_path: Path) -> None:
    """A query whose response would exceed the 2 MB hard cap is truncated
    via the streaming byte budget. ``truncated=True`` and ``row_count``
    is the number of rows that actually fit — no row ceiling involved."""
    # 10 000 rows × 500-byte text column = ~5 MB raw, well over the 2 MB cap.
    long_str = "x" * 500
    table = pa.table(
        {
            "id": list(range(10_000)),
            "body": [long_str] * 10_000,
        }
    )
    ctx = _ctx_with_view(tmp_path, table)
    server_module.set_context(ctx)
    try:
        result = server_module.run_sql("SELECT * FROM big", max_rows=20_000)
    finally:
        server_module.set_context(None)
    assert result["truncated"] is True
    assert result["row_count"] < 10_000, "byte budget should have cut this short"
    # Measure the actual returned response to confirm we did not exceed 2 MB.
    import json as _json
    payload = _json.dumps(result, default=str)
    assert len(payload) <= 2_000_000, "response exceeded MAX_QUERY_BYTES hard cap"
    # And it should carry a warning (well above 500 KB).
    assert "warning" in result


def test_run_sql_user_max_rows_below_threshold_wins(tmp_path: Path) -> None:
    """User asked for a small result; even if the view is huge and wide,
    we return what they asked for and don't warn on a sub-threshold payload."""
    long_str = "x" * 500
    table = pa.table(
        {
            "id": list(range(50_000)),
            "body": [long_str] * 50_000,
        }
    )
    ctx = _ctx_with_view(tmp_path, table)
    server_module.set_context(ctx)
    try:
        result = server_module.run_sql("SELECT * FROM big", max_rows=500)
    finally:
        server_module.set_context(None)
    assert result["row_count"] == 500
    assert result["truncated"] is True
    # 500 rows × ~520 bytes ≈ 260 KB; under the 500 KB threshold → no warning.
    assert "warning" not in result
