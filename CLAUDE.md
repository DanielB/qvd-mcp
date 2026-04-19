# Claude guidance for qvd-mcp

## What this is

An open-source MCP server that exposes Qlik QVD files as SQL-queryable
views. Stack: `PyQvd → PyArrow → Parquet → DuckDB views → FastMCP stdio
→ MCP client`. MIT-licensed, Python 3.11+, built and run with `uv`.

Public pitch: "Query QVD files directly from disk with SQL and AI."

## Phase status

- **Phase 1 — shipped.** Core converter + MCP server + minimal CLI.
- **Phase 2 — shipped.** Lazy auto-refresh, Claude Desktop config
  merge, `setup` / `doctor` / `uninstall` commands, local-dev
  checkout detection, canonical `qvd-mcp` server key, `run_sql` tool.
- **Phase 3 — mostly shipped.** Expanded README, CHANGELOG,
  CONTRIBUTING, SECURITY, Contributor Covenant, issue/PR templates,
  GitHub Actions CI matrix (3 OS × 3 Python, all green), Dependabot,
  OIDC-ready `release.yml` workflow, repo **public** at
  <https://github.com/DanielB/qvd-mcp>.
  Still pending: PyPI trusted publisher registration (blocked on
  email verification), tag `v0.1.0`, first published release.
- **Phase 4 — deferred.** `qvd-rs` reader extra, S3 / Azure /
  SharePoint backends, advanced server features.

## Conventions (enforced by tests and lint)

- `from __future__ import annotations` at the top of every module.
- Fully type-annotated. `mypy --strict` must pass on `src/qvd_mcp`.
  Missing-stubs overrides for `pyqvd`, `pyarrow`, `mcp`, `duckdb` live
  in `pyproject.toml` — don't add more unless a new dep lacks stubs.
- `ruff check .` clean. 100-char lines. Rules: `E, F, I, UP, B, SIM, N`.
- Frozen dataclasses for config; use `dataclasses.replace` when tests
  need to vary a field.
- `logging` only; never `print()` in library code. The MCP server must
  never write to stdout — stdio transport reserves it for JSON-RPC
  frames. Use `log.info(...)` / `log.warning(...)` / etc., all of which
  go to stderr by `configure_server()`.
- No new runtime dependencies without discussion. Every dep is a cost.
- Comments explain *why*, not *what*. Default to no comment.

## Safety invariants (do not remove without replacement)

- The `run_sql` MCP tool applies a two-regex blocklist in `server.py`:
  `_FORBIDDEN_FUNCTIONS` (function-call form, e.g. `read_parquet(`) and
  `_FORBIDDEN_STATEMENTS` (statement-start form, e.g. `^|;` + `ATTACH`).
  Adding a new file-reading DuckDB function? Add it to
  `_FORBIDDEN_FUNCTIONS`. A new DDL keyword? Add it to
  `_FORBIDDEN_STATEMENTS`. Never loosen the rejection to allow
  user-supplied paths.
- `describe_qvd` and `sample_qvd` take `view_name`, not raw paths.
  Any new tool must follow the same convention — paths must not cross
  the tool boundary.
- Tools (except `refresh()`) are wrapped with `_with_auto_refresh`. New
  tools should either get the wrapper or have an explicit reason not to.
- Query results are capped (`max_query_rows` default 1000, ceiling
  10 000). The per-query timeout is enforced via `threading.Timer` +
  `conn.interrupt()` because DuckDB 1.x has no `SET statement_timeout`.

## Repo layout

```
src/qvd_mcp/
├── __init__.py, __main__.py
├── cli.py             (Typer app, commands live here)
├── config.py          (frozen Config dataclass + TOML loader)
├── convert.py         (QVD → Parquet, skip-if-unchanged, atomic writes)
├── state.py           (.qvd-mcp-state.json read/write)
├── naming.py          (view name normalization + collision handling)
├── logging_setup.py   (CLI vs server logging modes)
├── server.py          (FastMCP app, six tools, safety regexes)
├── claude_config.py   (Claude Desktop config merge/unmerge) [Phase 2]
├── setup_wizard.py    (setup flow) [Phase 2]
├── doctor.py          (diagnostic checks) [Phase 2]
└── readers/
    ├── __init__.py    (Reader Protocol)
    └── pyqvd_reader.py

tests/
├── fixtures/generate.py     (synthetic QVDs via PyQvd writer)
└── test_*.py                (pure-unit; no VMs, no network)
```

## Tone and legal posture

- QVD format is officially undocumented. This project uses
  community-maintained parsers. README carries two disclaimer blocks
  (licence and access-control) — keep them verbatim.
- Copyright line is pseudonymous: `Copyright (c) 2026 DanielB`.
- Never reference employer, location, or other projects in code,
  commits, or docs.
- Describe what the tool *does*, never what it lets users *avoid*. No
  Qlik-bashing, no license-cost comparisons.

## Verification workflow before any commit

```
uv run pytest
uv run ruff check .
uv run mypy src/qvd_mcp
```

All three exit 0, or investigate before committing. When the work
involves the MCP server, also run a manual smoke test against Claude
Desktop (it can't be automated).

## Where to look for context

- `README.md` — user-facing docs, both disclaimer blocks, quickstart
- `NOTICE.md` — third-party attributions (MIT + Apache 2.0 for PyArrow)
- `/Users/daniel/.claude/plans/now-go-through-this-nested-kahan.md` —
  current implementation plan (Phase 2 as of 2026-04-19)
- `examples/config.example.toml`, `examples/claude_desktop_config.example.json`
