# qvd-mcp

**Query QVD files directly from disk with SQL and AI.**

`qvd-mcp` is an [MCP](https://modelcontextprotocol.io) server that exposes
Qlik QVD files as SQL-queryable views. It converts QVDs to Parquet on a
skip-if-unchanged basis, then answers questions from any MCP-compatible
client — Claude Desktop, Claude Code, Cursor, and friends — via DuckDB.

> **A note on access control.** QVD files on disk have forgotten whatever
> section-access rules the Qlik app that produced them may have enforced,
> and qvd-mcp doesn't know any better — it hands whole rows to whoever's
> asking. Point it only at QVDs where full-row access is appropriate for
> the person (or model) at the keyboard. *"I thought it would filter
> that out"* is not on the roadmap.

## Install

Requires Python 3.11 or newer plus [uv](https://docs.astral.sh/uv/).

`qvd-mcp` is **pre-release** — not on PyPI yet. Install directly from this
git repository:

```bash
# One-shot CLI (no clone — uvx builds a temp venv):
uvx --from git+https://github.com/DanielB/qvd-mcp qvd-mcp --version

# Or clone the repo — required if you want the setup wizard to auto-wire
# Claude Desktop at the correct local-dev command:
git clone https://github.com/DanielB/qvd-mcp
cd qvd-mcp
uv sync
```

Once `v0.1.0` ships on PyPI, the simpler `uvx qvd-mcp ...` and
`pipx install qvd-mcp` forms will work.

## Quickstart

From inside a cloned checkout:

```bash
# Interactive — prompts for source dir, patches Claude Desktop, runs first pass:
uvx --from . qvd-mcp setup

# Non-interactive — for scripts or CI:
uvx --from . qvd-mcp setup --yes --source ~/Documents/QVDs
```

Restart Claude Desktop and ask *"list the QVDs you can see"* to confirm the
wiring. Claude Desktop caches the MCP tool list at connect time, so a fresh
launch after `setup` is what makes the server visible.

Setup detects whether you're running from a local checkout and writes the
appropriate Claude Desktop command — either `uvx qvd-mcp serve`
(post-PyPI) or `uv --directory /path/to/qvd-mcp run qvd-mcp serve`
(pre-PyPI / local dev). On Windows, it co-writes to both the MSIX-packaged
and unpackaged Claude Desktop config locations so either install variant
picks up the entry transparently.

Prefer to wire things by hand? `qvd-mcp convert --source ...` runs a single
conversion pass and `qvd-mcp serve` boots the stdio server — then add the
equivalent of the following to your `claude_desktop_config.json` (pick
whichever matches your environment):

```json
{
  "mcpServers": {
    "qvd-mcp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/DanielB/qvd-mcp", "qvd-mcp", "serve"]
    }
  }
}
```

Run `qvd-mcp doctor` any time to see what's wired up and what isn't.

## Sharing a converted cache

A parquet cache produced by `qvd-mcp` on one machine can be used on another
machine that does **not** have the source QVDs. Useful when one person owns
the conversion and the rest of the team only needs to query.

### Producer

```bash
qvd-mcp setup            # interactive, answer "yes" to the QVD question
# ... cache is built in, e.g., ~/Library/Caches/qvd-mcp (macOS)
#                         ~/.cache/qvd-mcp              (Linux)
#                         %LOCALAPPDATA%\qvd-mcp\Cache  (Windows)

# Package the cache for a colleague — any zip tool works:
cd ~/Library/Caches && zip -r ~/Desktop/qvd-mcp-cache.zip qvd-mcp
```

### Consumer

```bash
# Extract the zip somewhere local
mkdir -p ~/qvd-mcp-cache
cd ~/qvd-mcp-cache && unzip ~/Downloads/qvd-mcp-cache.zip

# Point setup at the extracted folder; answer "no" to the QVD question
qvd-mcp setup
#   Where is the shared cache folder? ~/qvd-mcp-cache/qvd-mcp
```

Non-interactive alternative:

```bash
qvd-mcp setup --yes --cache ~/qvd-mcp-cache/qvd-mcp
# No --source given → consumer mode. Skips conversion. Cache must already
# contain *.parquet files.
```

### How it works

The cache uses Parquet (an open format). The consumer's `qvd-mcp serve`
registers DuckDB views directly from the parquet files; no QVD-specific
tooling is needed on the consumer's side. Auto-refresh is automatically
disabled when `source_dir` is unset — nothing to probe.

If the consumer later gets new parquets (e.g. the producer drops them
into a shared Dropbox/OneDrive folder both machines point at), restart
the server to pick them up.

## How it works

```
QVD files (source directory on disk)
      │
      ▼  PyQvd
PyArrow Table
      │
      ▼  atomic parquet write (zstd)
Parquet cache (platform cache dir)
      │
      ▼  DuckDB in-process, one view per QVD
FastMCP server (stdio transport)
      │
      ▼
MCP client (Claude Desktop, Cursor, …)
```

**Discovery.** `qvd-mcp` recursively scans `source_dir` for `*.qvd` files
on every conversion pass. Nothing about the layout matters — subdirectories
are fine, filenames are the only identifier the rest of the pipeline sees.
Scope it with optional `include` and `exclude` glob lists in the config
file (or `--include` / `--exclude` on `qvd-mcp convert` and `qvd-mcp
setup`, repeatable) when you want a subset — e.g. `include =
["sales/*.qvd"]` plus `exclude = ["*.backup.qvd"]`.

**Reading.** PyQvd parses each QVD and returns row-major Python values.
Qlik's dual fields (number-plus-display-string pairs) are unwrapped to
the numeric side, because that is almost always what SQL wants.
Timestamps and dates arrive as `datetime` and `date`, money fields come
back as `Decimal` and get cast to `float64`, and empty cells become
Python `None`. Empty QVDs emit a typed-but-empty string-column schema
rather than failing to materialise.

**Arrow conversion.** Values land in a `pyarrow.Table`. If a column
turns out to hold mixed or unsupported types, the reader falls back to
a string representation so the whole file still makes it to Parquet
rather than failing the pass.

**Parquet write.** The table is written as `<view>.parquet.tmp` with
zstd compression, then promoted to `<view>.parquet` via `os.replace`.
A crashed conversion can never leave a torn file.

**State sidecar.** A `.qvd-mcp-state.json` file in the cache directory
records one entry per QVD: view name, Parquet filename, source
`mtime_ns` and `size`, conversion timestamp, and row/column counts.
It is the single source of truth for skip-if-unchanged and for view
registration on server startup.

**View registration.** Each Parquet file becomes one DuckDB view. The
view name comes from the filename stem, lowercased, with any run of
non-alphanumeric characters collapsed to a single underscore — so
`Sales 2024.qvd` becomes the view `sales_2024`, and you can type
`SELECT * FROM sales_2024` without quoting. Collisions get a numeric
suffix; leading digits, SQL reserved words, and Unicode are all
normalised.

### What data is queried

DuckDB reads the **Parquet cache**, not the QVDs, during a query.
Views are defined as `SELECT * FROM read_parquet(...)` and the
original QVDs are only touched during the conversion pass. That means
each query only loads the columns it actually references, not the
whole row — one of the reasons a Parquet-backed cache is worth the
extra copy on disk.

### When conversion runs

Four triggers re-run conversion, each with a specific purpose:

1. **Lazy auto-refresh.** Before every tool call (except `refresh`
   itself), the server probes source `(mtime_ns, size)` against the
   state sidecar and the `source_dir` for new files. If anything
   drifted, it runs a conversion pass and re-registers views before
   answering. Debounced at 10 seconds by default; set
   `auto_refresh_debounce_s = 0` in config to disable and use `refresh`
   explicitly.
2. **The `refresh()` MCP tool.** Runs one conversion pass and rebuilds
   views. Useful when the debounce window is still open but you want
   fresh data right now.
3. **`qvd-mcp convert`** on the CLI. One-shot conversion pass, prints
   a summary, exits. Handy in CI or a cron job.
4. **`qvd-mcp setup`**. The wizard runs one conversion pass after
   writing the config, so the cache is warm before you start the
   server.

Every pass applies the same **skip-if-unchanged** rule: if the recorded
`(mtime_ns, size)` still matches the file on disk and the Parquet is
present, the QVD is not re-read. One bad file never aborts a pass —
failures are per-file and reported in the summary.

## Commands

| Command | What it does |
| --- | --- |
| `qvd-mcp setup` | Interactive wizard: config + Claude Desktop merge + first conversion. Add `--yes --source <path>` to script it. |
| `qvd-mcp convert` | Run one QVD → Parquet conversion pass. |
| `qvd-mcp serve` | Run the MCP server over stdio. This is what Claude Desktop invokes. |
| `qvd-mcp doctor` | Nine diagnostic checks. Exit 0 all-pass, 1 any-fail, 2 config-broken. |
| `qvd-mcp uninstall` | Remove the Claude Desktop entry. `--delete-cache` also drops the Parquet cache. Source QVDs are never touched. |
| `qvd-mcp --version` | Print version and exit. |

## MCP tools reference

The server registers six tools on a FastMCP stdio transport. All tool
calls except `refresh()` are wrapped in the auto-refresh probe described
above.

### `list_qvds()`

```
list_qvds() -> list[dict]
```

List every loaded QVD with row and column counts, source path, and the
timestamp of its last conversion. Takes no arguments.

Example invocation:

```
list_qvds()
```

Expected shape (one entry per view, sorted by view name):

```json
[
  {
    "view_name": "sales_2024",
    "source_path": "/Users/you/Documents/QVDs/Sales 2024.qvd",
    "rows": 184503,
    "columns": 17
  }
]
```

Conversion timestamps are omitted on purpose — the server auto-refreshes
on source changes, so freshness is rarely load-bearing. Call
`describe_qvd` if you want the exact `converted_at`.

### `describe_qvd(view_name)`

```
describe_qvd(view_name: str) -> dict
```

Full schema for one view — column names, DuckDB types, plus the same
metadata `list_qvds` returns and the conversion timestamp. `view_name`
must match an entry from `list_qvds`; an unknown name returns a
structured `UnknownView` error.

Example invocation:

```
describe_qvd(view_name="sales_2024")
```

Expected shape:

```json
{
  "view_name": "sales_2024",
  "source_path": "/Users/you/Documents/QVDs/Sales 2024.qvd",
  "rows": 184503,
  "columns": [
    {"name": "OrderId", "type": "BIGINT"},
    {"name": "OrderDate", "type": "TIMESTAMP"}
  ],
  "converted_at": "2026-04-18T14:07:12Z"
}
```

### `sample_qvd(view_name, n=10)`

```
sample_qvd(view_name: str, n: int = 10) -> dict
```

Return the first `n` rows of a view. `n` is clamped to `[1, 1000]`.
Cheap shortcut for "show me some rows" without making the LLM
construct a `SELECT * … LIMIT n`.

Example invocation:

```
sample_qvd(view_name="sales_2024", n=5)
```

Expected shape — `rows` are positional arrays aligned with `columns`:

```json
{
  "view_name": "sales_2024",
  "columns": ["OrderId", "OrderDate", "Amount"],
  "rows": [
    [1001, "2024-01-03T00:00:00", 148.50],
    [1002, "2024-01-03T00:00:00", 99.00]
  ],
  "row_count": 5
}
```

### `run_sql(sql, max_rows=1000)`

```
run_sql(sql: str, max_rows: int = 1000) -> dict
```

Execute arbitrary read-only SQL across the registered views. `max_rows`
defaults to 1000 and is hard-capped at 30 000; anything beyond the cap
is truncated and `truncated: true` is returned. Results exceeding the
recommended 10 000-row threshold carry an extra `warning` field in the
response nudging toward aggregation — large result sets inflate LLM
context. SQL that touches filesystem-reading functions or DDL statements
is rejected with a `Rejected` error — see [SECURITY.md](SECURITY.md) for
the list. Per-query timeout defaults to 30 seconds and cancels the query
via `conn.interrupt()`.

Example invocation:

```
run_sql(
  sql="SELECT OrderDate, SUM(Amount) AS total FROM sales_2024 GROUP BY 1 ORDER BY 1",
  max_rows=100,
)
```

Expected shape — `rows` are positional arrays aligned with `columns`:

```json
{
  "columns": ["OrderDate", "total"],
  "rows": [
    ["2024-01-03T00:00:00", 148.50],
    ["2024-01-04T00:00:00", 2093.17]
  ],
  "row_count": 100,
  "truncated": false
}
```

On rejection or engine error the tool returns an `error` object instead:

```json
{"error": {"type": "Rejected", "message": "SQL uses a restricted function or statement …"}}
```

### `search_columns(term, case_sensitive=False)`

```
search_columns(term: str, case_sensitive: bool = False) -> list[dict]
```

Substring match over column names across every registered view. Useful
when the schema is sprawling and the LLM doesn't know which view holds
the column it needs.

Example invocation:

```
search_columns(term="amount")
```

Expected shape (one entry per matching column):

```json
[
  {"view_name": "sales_2024", "column_name": "Amount", "type": "DOUBLE"},
  {"view_name": "returns_2024", "column_name": "RefundAmount", "type": "DOUBLE"}
]
```

### `refresh()`

```
refresh() -> dict
```

Force a conversion pass, rebuild views, clear row-count caches. Use
after editing a source QVD when you don't want to wait for the
auto-refresh debounce window to re-open.

Example invocation:

```
refresh()
```

Expected shape:

```json
{
  "converted": 2,
  "skipped": 18,
  "failed": 0,
  "pruned": 1,
  "summary": "converted=2 skipped=18 failed=0 pruned=1"
}
```

## Configuration

Configuration lives at `~/.config/qvd-mcp/config.toml` on macOS and
Linux and `%APPDATA%\qvd-mcp\config.toml` on Windows. Every field has a
sensible default. See
[`examples/config.example.toml`](examples/config.example.toml) for a
commented skeleton.

| Option | Default | Purpose |
| --- | --- | --- |
| `source_dir` | *(optional — unset means cache-only)* | Directory of QVDs to scan recursively. |
| `cache_dir` | platformdirs user cache | Where the Parquet cache and state sidecar live. |
| `max_query_rows` | `1000` | Default `run_sql` row cap. Hard ceiling is 30 000; results above 10 000 carry a `warning` field. |
| `query_timeout_s` | `30` | Per-query timeout; `conn.interrupt()` fires when it expires. |
| `log_level` | `"INFO"` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |
| `auto_refresh_debounce_s` | `10` | Seconds between lazy-refresh probes. `0` disables the probe. |

## Dependencies

Runtime dependencies only. Dev and test extras are pulled in by
`uv sync --all-extras --dev`.

| Package | Role | License |
| --- | --- | --- |
| [PyQvd](https://github.com/MuellerConstantin/PyQvd) | QVD file parsing | MIT |
| [PyArrow](https://arrow.apache.org/docs/python/) | Columnar memory and Parquet I/O | Apache 2.0 |
| [DuckDB](https://duckdb.org/) | In-process SQL engine | MIT |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | MCP protocol + `FastMCP` 1.0 decorator API (`mcp.server.fastmcp`) | MIT |
| [platformdirs](https://github.com/platformdirs/platformdirs) | Cross-platform user directories | MIT |
| [Typer](https://typer.tiangolo.com/) | CLI argument parsing | MIT |
| [Rich](https://github.com/Textualize/rich) | CLI output formatting | MIT |

## Privacy & telemetry

`qvd-mcp` makes no outbound network calls during normal operation. There's
no telemetry, no analytics, and no phone-home. The server reads your
local QVDs, writes a local Parquet cache, and speaks to one local MCP
client over stdio. That's it.

## Troubleshooting / FAQ

**Claude Desktop shows the tool list but won't call one of the
`qvd-mcp` tools.** The MCP tool list is cached at connect time. Quit
Claude Desktop fully (not just close the window) and reopen it — the
new tool list is picked up on the next connect.

**I updated a QVD and Claude still sees the old data.** Auto-refresh
is debounced; the default window is 10 seconds. Either wait 10 seconds
and ask again, call the `refresh()` MCP tool to force a pass, or set
`auto_refresh_debounce_s = 0` in config to disable the probe and rely
on `refresh()` only.

**Why a Parquet cache — why not read QVDs directly on each query?**
Parquet is columnar, type-rich, and compressed; DuckDB's query engine
is heavily optimised for it. A query that touches three columns only
loads those three columns, not the whole row. Round-tripping through
Parquet once at conversion time is cheaper than re-parsing a QVD on
every query, especially for repeated aggregations over the same file.

**Can `run_sql` write or attach files?** No. The tool rejects the
file-reading DuckDB functions (`read_parquet`, `read_csv`,
`read_json`, `read_text`, `glob`, and variants) and the DDL statements
(`ATTACH`, `DETACH`, `COPY`, `INSTALL`, `LOAD`, `PRAGMA`, `EXPORT`,
`IMPORT`). SQL that tries to use them comes back with a `Rejected`
error rather than executing. See [SECURITY.md](SECURITY.md) for the
threat model.

**Where's the log file?** Under the platform user log directory for
`qvd-mcp` — `~/Library/Logs/qvd-mcp/` on macOS,
`~/.local/state/qvd-mcp/log/` on Linux, `%LOCALAPPDATA%\qvd-mcp\Logs\`
on Windows. `qvd-mcp doctor` prints the resolved path in its report.

## Attribution

Built on the shoulders of several fine open-source projects — PyQvd, DuckDB,
FastMCP, PyArrow, platformdirs, Typer, Rich. See [NOTICE.md](NOTICE.md) for
license notices and thanks.

## License

MIT. See [LICENSE](LICENSE).
