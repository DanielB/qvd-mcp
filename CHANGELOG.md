# Changelog

All notable changes to `qvd-mcp` are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-04-19

First public release. Covers the Phase 1 and Phase 2 deliverables: a
working end-to-end pipeline from QVDs on disk through a Parquet cache
to an MCP client, plus a CLI that sets the whole thing up in one
command.

### Added

- **CLI commands.** `convert` runs a single QVD to Parquet pass and
  prints a summary. `serve` boots the MCP server over stdio. `setup`
  is an interactive wizard that writes `config.toml`, patches the
  Claude Desktop config (with `--yes` for scripting), and runs the
  first conversion. `doctor` runs nine diagnostic checks and exits
  0 / 1 / 2 for pass / fail / broken-config. `uninstall` reverses
  `setup`, with an opt-in `--delete-cache` for the Parquet cache;
  source QVDs are never touched.
- **MCP tools.** Six tools on a FastMCP stdio server: `list_qvds`
  for an inventory, `describe_qvd` for a full schema, `sample_qvd`
  for a capped row preview, `run_sql` for arbitrary read-only SQL
  across the views, `search_columns` for substring column-name
  lookups, and `refresh` for an explicit conversion pass.
- **Lazy auto-refresh.** Before each tool call (except `refresh`
  itself), the server checks source `(mtime_ns, size)` against the
  state sidecar and re-runs conversion if anything drifted. Debounced
  at 10 seconds by default; `auto_refresh_debounce_s = 0` in config
  disables it and makes `refresh` the only path.
- **Skip-if-unchanged conversion.** Each QVD is reconverted only when
  its `(mtime_ns, size)` differs from the last recorded pair.
  Unchanged files are skipped entirely.
- **Claude Desktop config auto-merge.** `setup` writes the `qvd-mcp`
  server entry into the platform-specific Claude Desktop config,
  preserving any other servers already there. A `.bak` copy of the
  prior config is kept next to it so a botched merge can be rolled
  back by hand.
- **Platform-aware config and cache paths** via `platformdirs`:
  `~/.config/qvd-mcp/` on macOS and Linux, `%APPDATA%\qvd-mcp\` on
  Windows; parallel cache and log directories.
- **PyQvd-backed reader.** Handles Qlik's quirkier value types: dual
  fields unwrap to the numeric side, timestamps and dates arrive as
  Python `datetime` / `date`, money as `Decimal` is cast to `float64`
  for Arrow, nulls become Arrow nulls, empty tables emit a typed
  string-column schema rather than failing to materialise.
- **Atomic Parquet writes** with zstd compression. The target path
  is always written as `<name>.parquet.tmp` and promoted with
  `os.replace`, so a killed conversion can never leave a torn file.
- **DuckDB view auto-registration.** One view per QVD, named by
  slugifying the filename stem (`Sales 2024.qvd` becomes the view
  `sales_2024`). Collisions get a numeric suffix; leading digits,
  reserved words, Unicode, and punctuation are all normalised.
- **Safety blocklists for `run_sql`.** Two regexes reject SQL that
  would let a curious LLM read files outside the cache: function-call
  forms (`read_parquet(`, `read_csv(`, `read_text(`, `glob(`, and
  variants) and statement-start forms (`ATTACH`, `DETACH`, `COPY`,
  `INSTALL`, `LOAD`, `PRAGMA`, `EXPORT`, `IMPORT`).
- **Per-query timeout.** `run_sql` runs each query under a
  `threading.Timer` that calls `conn.interrupt()` when the window
  expires; DuckDB 1.x has no server-side statement timeout.
- **Clean stdout discipline.** The MCP server logs only to stderr so
  the stdio JSON-RPC stream stays uncontaminated.
- **Local-dev checkout detection in `setup`.** Walks up from CWD
  looking for a `pyproject.toml` whose `[project].name` is `qvd-mcp`;
  when found, writes the `uv --directory <path> run qvd-mcp serve`
  form into Claude Desktop so pre-PyPI installs work end-to-end.
  Falls back to `uvx qvd-mcp serve` once the package is on PyPI.
- **Windows MSIX-packaged Claude Desktop support.** Claude Desktop's
  Microsoft Store / MSIX build virtualises `%APPDATA%` into
  `%LOCALAPPDATA%\Packages\Claude_<suffix>\LocalCache\Roaming\...`.
  `claude_config` detects the packaged install and co-writes to both
  the packaged and unpackaged locations, so either flavour picks up
  the entry transparently.
- **Preserves existing Claude Desktop entries.** Setup never removes
  user-placed entries — not even a legacy `qvd` key from a pre-rename
  release. Only `uninstall` removes qvd-mcp-owned entries, and it
  removes both the canonical `qvd-mcp` key and the legacy `qvd` key
  so pre-rename installs clean up fully.

### Changed

- **Depend on the official `mcp` SDK directly** (using
  `mcp.server.fastmcp.FastMCP`) instead of the standalone `fastmcp`
  2.x package. Same decorator-based surface; around 45 fewer
  transitive dependencies on a fresh install (~98 → ~53 packages).
- **SQL tool renamed from `query` to `run_sql`.** The old name
  collided with built-in concepts in some MCP clients (tools named
  `query` were occasionally filtered from surfaced tool lists).
  `run_sql` follows the verb-phrase pattern of the other tools.
- **Canonical Claude Desktop server key is `qvd-mcp`** (matches the
  package name), not `qvd`. Legacy entries are left alone on upgrade.
- **Token-efficient MCP response shapes.** `sample_qvd` and `run_sql`
  now return `rows` as positional arrays aligned with `columns`
  (``[[1, "a"], [2, "b"]]``) instead of repeating column-name keys
  per row — roughly 50–60% smaller payload on wide results. `run_sql`
  no longer echoes the submitted `sql` back in its response; the
  caller already has it. `describe_qvd` drops the `nullable` field
  (DuckDB reports ``YES`` for every column in a ``read_parquet``
  view, so the bit is noise). `list_qvds` drops `converted_at` from
  its always-visible inventory; ask `describe_qvd` when you need it.

### Known limitations

- **One reader only.** PyQvd is the only backend in this release.
  `qvd-rs` as an opt-in extra is on the Phase 4 roadmap.
- **No remote backends.** S3, Azure Blob, and SharePoint Graph
  sources are Phase 4 work.
- **Pre-release.** The repo ships the first PyPI publication and
  public visibility flip as part of the `0.1.x` line; expect minor
  rough edges as CI and distribution settle.

[0.1.0]: https://github.com/DanielB/qvd-mcp/releases/tag/v0.1.0
