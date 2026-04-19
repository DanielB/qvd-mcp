from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from qvd_mcp import server as server_module
from qvd_mcp import state as state_module
from qvd_mcp.config import Config
from qvd_mcp.convert import run_once
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
    result = server_module.query("SELECT COUNT(*) AS n FROM plain_numbers")
    assert "error" not in result
    assert result["rows"][0]["n"] == 5


def test_query_rejects_read_parquet(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.query("SELECT * FROM read_parquet('/etc/passwd')")
    assert result.get("error", {}).get("type") == "Rejected"


def test_query_rejects_attach(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.query("ATTACH '/tmp/something.db' AS foo")
    assert result.get("error", {}).get("type") == "Rejected"


def test_query_syntax_error_returns_clean_message(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.query("SELECT NOT_A_REAL_COLUMN FROM plain_numbers WHERE")
    assert "error" in result
    # DuckDB error class surfaced, not a Python traceback
    assert result["error"]["type"] != "Exception"


def test_query_truncation_flag(loaded_ctx: server_module.ServerContext) -> None:
    result = server_module.query("SELECT * FROM plain_numbers", max_rows=2)
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
