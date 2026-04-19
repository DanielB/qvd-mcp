"""MCP server exposing QVDs as SQL-queryable views.

Tools are plain Python functions. They read/write module-level
``_ctx`` (a small struct holding the DuckDB connection and state).
Registration happens at module bottom via ``app.add_tool(fn)`` rather
than the ``@app.tool()`` decorator, which keeps the plain function
callable for unit tests.

We depend on the official ``mcp`` SDK directly (``mcp.server.fastmcp``)
rather than the standalone ``fastmcp`` 2.x package — same decorator-based
API, dramatically smaller transitive dependency tree.

Safety model (Phase 1):

1. DuckDB is in-memory, views lazily scan parquets on disk.
2. The ``run_sql`` tool applies a conservative regex blocklist that rejects
   ``read_parquet``, ``read_csv``, ``read_json``, ``copy``, ``attach``,
   and ``glob`` calls. This prevents an LLM-driven path-traversal via
   user-supplied SQL.
3. ``describe_qvd``/``sample_qvd`` take a ``view_name`` that must match
   an entry in state; raw filesystem paths never cross the tool boundary.
4. ``run_sql`` results are capped at ``max_query_rows`` (default 1000,
   ceiling 10000) and a per-query timeout.

The threat model is "curious LLM, not malicious adversary." A determined
attacker with shell access to the same machine already has everything the
tool could leak; we just want to avoid accidental foot-guns.
"""
from __future__ import annotations

import functools
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ParamSpec, TypeVar

import duckdb
from mcp.server.fastmcp import FastMCP

from qvd_mcp import state as state_module
from qvd_mcp.config import MAX_QUERY_ROW_CEILING, Config
from qvd_mcp.convert import run_once

log = logging.getLogger(__name__)

app: FastMCP = FastMCP("qvd-mcp")

# Reject user SQL that reads files from disk or opens external databases.
# Two shapes: function-call form (block only when followed by `(`), and
# statement-start form (block only at the top of a statement). This keeps
# legitimate column names like ``copy``, ``load``, ``glob`` usable — real
# Qlik data has columns with those names — while still catching the
# DuckDB table functions and DDL that would let a curious LLM read
# /etc/passwd or attach an arbitrary database.
_FORBIDDEN_FUNCTIONS = re.compile(
    r"\b(?:read_text|read_blob|read_parquet|read_csv|read_csv_auto|read_json|"
    r"read_json_auto|parquet_scan|csv_scan|glob|read_ndjson|read_ndjson_auto)\s*\(",
    re.IGNORECASE,
)
_FORBIDDEN_STATEMENTS = re.compile(
    r"(?:^|;)\s*(?:attach|detach|copy|install|load|pragma|export|import)\b",
    re.IGNORECASE,
)


def _is_rejected(sql: str) -> bool:
    return bool(_FORBIDDEN_FUNCTIONS.search(sql) or _FORBIDDEN_STATEMENTS.search(sql))


@dataclass
class ServerContext:
    config: Config
    conn: duckdb.DuckDBPyConnection
    state: state_module.State
    _counts: dict[str, tuple[float, int]] = field(default_factory=dict)


_ctx: ServerContext | None = None

# Tracks the last time ``_refresh_if_stale`` did a source-changed probe. Shared
# across tool calls so a burst of requests triggers at most one ``stat()`` sweep
# per debounce window.
_last_check: float = 0.0

P = ParamSpec("P")
R = TypeVar("R")


def _get_ctx() -> ServerContext:
    if _ctx is None:
        raise RuntimeError("server context not initialized; call serve() first")
    return _ctx


def set_context(ctx: ServerContext | None) -> None:
    """Install (or clear) the module-level context. Tests use this directly."""
    global _ctx
    _ctx = ctx


def _count(ctx: ServerContext, view_name: str) -> int:
    now = time.monotonic()
    cached = ctx._counts.get(view_name)
    if cached is not None and now - cached[0] < 30:
        return cached[1]
    row = ctx.conn.execute(f'SELECT COUNT(*) FROM "{_q(view_name)}"').fetchone()
    count = int(row[0]) if row else 0
    ctx._counts[view_name] = (now, count)
    return count


def _q(ident: str) -> str:
    """Quote-escape a SQL identifier (wrap in double quotes at call site)."""
    return ident.replace('"', '""')


def _qstr(text: str) -> str:
    """Escape a single-quoted SQL string literal."""
    return text.replace("'", "''")


def _entry_by_view(ctx: ServerContext, view_name: str) -> tuple[str, state_module.StateEntry] | None:
    for src, entry in ctx.state.entries.items():
        if entry.view_name == view_name:
            return src, entry
    return None


def _build_connection(config: Config, state: state_module.State) -> duckdb.DuckDBPyConnection:
    # Timeouts are applied per-query via ``conn.interrupt()`` from a timer
    # thread inside ``run_sql()``. DuckDB 1.x has no server-side statement
    # timeout setting, so we enforce it in Python.
    conn = duckdb.connect(":memory:")
    for entry in state.entries.values():
        parquet = config.cache_dir / entry.parquet_path
        if not parquet.is_file():
            log.warning("parquet missing for view %s: %s", entry.view_name, parquet)
            continue
        sql = (
            f'CREATE OR REPLACE VIEW "{_q(entry.view_name)}" AS '
            f"SELECT * FROM read_parquet('{_qstr(str(parquet))}')"
        )
        conn.execute(sql)
    return conn


def _rebuild_views(ctx: ServerContext) -> None:
    # Drop existing views, then recreate from current state.
    existing = ctx.conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
    ).fetchall()
    for (name,) in existing:
        ctx.conn.execute(f'DROP VIEW IF EXISTS "{_q(name)}"')
    for entry in ctx.state.entries.values():
        parquet = ctx.config.cache_dir / entry.parquet_path
        if not parquet.is_file():
            continue
        ctx.conn.execute(
            f'CREATE OR REPLACE VIEW "{_q(entry.view_name)}" AS '
            f"SELECT * FROM read_parquet('{_qstr(str(parquet))}')"
        )


def _any_source_changed(ctx: ServerContext) -> bool:
    """Cheap check for whether the source QVDs on disk drifted from state.

    Compares ``(mtime_ns, size)`` per known entry and scans ``source_dir`` for
    any new ``*.qvd`` that isn't tracked yet. A missing/unstat-able source is
    also "changed" — the conversion pass will prune it.
    """
    for src_key, entry in ctx.state.entries.items():
        try:
            st = Path(src_key).stat()
        except OSError:
            return True
        if st.st_mtime_ns != entry.source_mtime_ns or st.st_size != entry.source_size:
            return True

    # Path-key invariant: state keys and our rglob strings must match character
    # for character. ``convert.run_once`` iterates the same rglob and stores
    # ``str(qvd_path)`` without ``.resolve()``; keep both sides unresolved or
    # every tool call will trigger a full refresh.
    known = set(ctx.state.entries)
    try:
        for qvd in ctx.config.source_dir.rglob("*.qvd"):
            if str(qvd) not in known:
                return True
    except OSError:
        return True
    return False


def _refresh_if_stale(ctx: ServerContext) -> None:
    """Run a conversion pass if source QVDs drifted since the last probe.

    Gated by ``ctx.config.auto_refresh_debounce_s``; ``0`` disables the feature.
    """
    global _last_check
    if ctx.config.auto_refresh_debounce_s <= 0:
        return
    now = time.monotonic()
    if now - _last_check < ctx.config.auto_refresh_debounce_s:
        return
    _last_check = now
    if not _any_source_changed(ctx):
        return
    report = run_once(ctx.config)
    ctx.state = state_module.load(ctx.config.cache_dir)
    _rebuild_views(ctx)
    ctx._counts.clear()
    log.info("auto-refresh: %s", report.summary())


def _with_auto_refresh(fn: Callable[P, R]) -> Callable[P, R]:
    """Decorate a tool function so it probes for stale source QVDs first."""

    @functools.wraps(fn)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        _refresh_if_stale(_get_ctx())
        return fn(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Tool implementations (plain functions; registered at module bottom)
# ---------------------------------------------------------------------------


def list_qvds() -> list[dict[str, Any]]:
    """List all QVDs currently loaded.

    Returns one entry per view with its name, source path, row/column counts,
    and last-converted timestamp.
    """
    ctx = _get_ctx()
    out: list[dict[str, Any]] = []
    for src, entry in sorted(ctx.state.entries.items(), key=lambda kv: kv[1].view_name):
        # ``converted_at`` is deliberately omitted here — lazy auto-refresh
        # makes freshness a non-question, and skipping it shrinks the
        # always-visible inventory. Fetch from ``describe_qvd`` when needed.
        out.append(
            {
                "view_name": entry.view_name,
                "source_path": src,
                "rows": _count(ctx, entry.view_name),
                "columns": entry.columns,
            }
        )
    return out


list_qvds = _with_auto_refresh(list_qvds)


def describe_qvd(view_name: str) -> dict[str, Any]:
    """Return the full schema for a QVD view: column names, DuckDB types,
    nullability. ``view_name`` must match one from :func:`list_qvds`.
    """
    ctx = _get_ctx()
    found = _entry_by_view(ctx, view_name)
    if found is None:
        return {
            "error": {
                "type": "UnknownView",
                "message": f"no view named {view_name!r}. Call list_qvds to see available views.",
            }
        }
    src, entry = found
    described = ctx.conn.execute(f'DESCRIBE "{_q(view_name)}"').fetchall()
    # ``nullable`` is dropped deliberately: DuckDB reports ``"YES"`` for every
    # column in a ``read_parquet``-backed view because the scanner doesn't
    # enforce NOT NULL, so the field is effectively noise in our output.
    columns = [{"name": row[0], "type": str(row[1])} for row in described]
    return {
        "view_name": view_name,
        "source_path": src,
        "rows": _count(ctx, view_name),
        "columns": columns,
        "converted_at": entry.converted_at,
    }


describe_qvd = _with_auto_refresh(describe_qvd)


def sample_qvd(view_name: str, n: int = 10) -> dict[str, Any]:
    """Return the first ``n`` rows of a view (capped at 1000).

    Everyone's first question is "show me some rows," so we give the LLM a
    cheap shortcut instead of making it guess ``SELECT * ... LIMIT n``.
    """
    ctx = _get_ctx()
    if _entry_by_view(ctx, view_name) is None:
        return {
            "error": {
                "type": "UnknownView",
                "message": f"no view named {view_name!r}",
            }
        }
    n = max(1, min(int(n), 1000))
    rel = ctx.conn.execute(f'SELECT * FROM "{_q(view_name)}" LIMIT {n}')
    cols = [d[0] for d in rel.description]
    rows = rel.fetchall()
    # Columnar shape: ``rows`` is a list of positional arrays aligned with
    # ``columns``. Saves the per-row repetition of column-name keys that a
    # list-of-dicts shape carries, which adds up fast for wide tables.
    return {
        "view_name": view_name,
        "columns": cols,
        "rows": [list(row) for row in rows],
        "row_count": len(rows),
    }


sample_qvd = _with_auto_refresh(sample_qvd)


def run_sql(sql: str, max_rows: int = 1000) -> dict[str, Any]:
    """Execute read-only SQL against the loaded views.

    ``max_rows`` defaults to 1000, hard-capped at 10000. Results beyond the
    cap are truncated and ``truncated=True`` is set. Queries touching
    filesystem-reading functions are rejected.
    """
    ctx = _get_ctx()
    if _is_rejected(sql):
        return {
            "error": {
                "type": "Rejected",
                "message": (
                    "SQL uses a restricted function or statement (file-reading "
                    "table functions, ATTACH, COPY, LOAD, PRAGMA, etc.). Stick "
                    "to SELECT over the registered views."
                ),
            }
        }
    limit = max(1, min(int(max_rows), MAX_QUERY_ROW_CEILING))
    timeout_s = ctx.config.query_timeout_s
    timer = threading.Timer(timeout_s, ctx.conn.interrupt)
    timer.start()
    try:
        rel = ctx.conn.execute(sql)
        cols = [d[0] for d in rel.description]
        rows = rel.fetchmany(limit)
        truncated = len(rows) == limit and rel.fetchone() is not None
    except duckdb.InterruptException:
        return {
            "error": {
                "type": "Timeout",
                "message": f"query exceeded {timeout_s}s and was cancelled",
            }
        }
    except duckdb.Error as exc:
        return {"error": {"type": exc.__class__.__name__, "message": str(exc)}}
    finally:
        timer.cancel()
    # Columnar rows (see ``sample_qvd``); ``sql`` is not echoed back since
    # the caller already has it in the turn that sent this request.
    return {
        "columns": cols,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }


run_sql = _with_auto_refresh(run_sql)


def search_columns(term: str, case_sensitive: bool = False) -> list[dict[str, str]]:
    """Fuzzy-substring match column names across all views.

    Useful when the schema is sprawling and the LLM doesn't know which view
    has the column it needs.
    """
    ctx = _get_ctx()
    needle = term if case_sensitive else term.lower()
    hits: list[dict[str, str]] = []
    for entry in ctx.state.entries.values():
        schema = ctx.conn.execute(f'DESCRIBE "{_q(entry.view_name)}"').fetchall()
        for row in schema:
            col_name = str(row[0])
            col_type = str(row[1])
            haystack = col_name if case_sensitive else col_name.lower()
            if needle in haystack:
                hits.append(
                    {
                        "view_name": entry.view_name,
                        "column_name": col_name,
                        "type": col_type,
                    }
                )
    return hits


search_columns = _with_auto_refresh(search_columns)


def refresh() -> dict[str, Any]:
    """Run one conversion pass and re-register views.

    Use this after updating a source QVD and wanting to query the new data
    without restarting the server.
    """
    ctx = _get_ctx()
    report = run_once(ctx.config)
    ctx.state = state_module.load(ctx.config.cache_dir)
    _rebuild_views(ctx)
    ctx._counts.clear()
    return {
        "converted": len(report.converted),
        "skipped": len(report.skipped),
        "failed": len(report.failed),
        "pruned": len(report.pruned),
        "summary": report.summary(),
    }


# Register everything with the MCP SDK's FastMCP. Using ``add_tool`` (direct
# call) rather than the ``@tool()`` decorator leaves the function names bound
# to the originals so unit tests can invoke them as plain Python callables.
for _fn in (list_qvds, describe_qvd, sample_qvd, run_sql, search_columns, refresh):
    app.add_tool(_fn)


def serve(config: Config) -> None:
    """Start the MCP server over stdio. Blocks until the client disconnects."""
    current_state = state_module.load(config.cache_dir)
    conn = _build_connection(config, current_state)
    set_context(ServerContext(config=config, conn=conn, state=current_state))
    log.info(
        "qvd-mcp serving %d views from %s (source: %s)",
        len(current_state.entries),
        config.cache_dir,
        config.source_dir,
    )
    # ``mcp.server.fastmcp`` doesn't print a banner and doesn't phone home,
    # so there's nothing to silence here.
    app.run(transport="stdio")
