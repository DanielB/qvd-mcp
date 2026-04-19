# qvd-mcp

**Query QVD files directly from disk with SQL and AI.**

`qvd-mcp` is an [MCP](https://modelcontextprotocol.io) server that exposes
Qlik QVD files as SQL-queryable views. It converts QVDs to Parquet on a
skip-if-unchanged basis, then answers questions from any MCP-compatible
client — Claude Desktop, Claude Code, Cursor, and friends — via DuckDB.

> **A note on Qlik licenses.** This tool reads QVD files — a file format
> originally produced by Qlik software. The QVD format is not officially
> documented; this project uses community-maintained parsers. Whether reading
> your QVD files with third-party tools is permitted depends on your specific
> agreement with Qlik. Check your own license terms before use. This project
> is not affiliated with or endorsed by Qlik.
>
> **A note on access control.** This tool reads QVD files directly from disk
> and does not enforce any row-level security (section access) rules that may
> have been defined in the Qlik apps that originally produced the files. Only
> use with QVDs where full-row access is appropriate for the end user.

## Install

Requires Python 3.11 or newer.

```bash
# Zero-install (recommended for trying it out):
uvx qvd-mcp --help

# Persistent install:
pipx install qvd-mcp
```

## Quickstart

1. Point `qvd-mcp` at a folder of QVDs and run a conversion pass:

   ```bash
   uvx qvd-mcp convert --source ~/Documents/QVDs
   ```

   This produces a Parquet cache in your platform cache directory and
   prints a summary.

2. Wire it into Claude Desktop. Open your `claude_desktop_config.json`
   (on macOS: `~/Library/Application Support/Claude/`) and add:

   ```json
   {
     "mcpServers": {
       "qvd": {
         "command": "uvx",
         "args": ["qvd-mcp", "serve"]
       }
     }
   }
   ```

   Then restart Claude Desktop. Ask it *"list the QVDs you can see"* to
   check the wiring.

## What the MCP server exposes

| Tool | What it does |
| --- | --- |
| `list_qvds()` | List every loaded QVD with row/column counts and paths. |
| `describe_qvd(view_name)` | Full schema for one view — column names and DuckDB types. |
| `sample_qvd(view_name, n=10)` | First `n` rows of a view. Capped at 1000. |
| `query(sql, max_rows=1000)` | Arbitrary read-only SQL across the views. Hard-capped at 10 000 rows. |
| `search_columns(term)` | Substring match over column names across all views. |
| `refresh()` | Run a conversion pass and re-register views. Useful after editing source QVDs. |

View names come from filename stems, lowercased and normalized for SQL
(e.g. `Sales 2024.qvd` becomes the view `sales_2024`), so you can type
`SELECT * FROM sales_2024` without quoting.

## Architecture

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

Nothing exotic. Every hop is a boring, well-tested library doing the thing
it's best at.

## Configuration

See [`examples/config.example.toml`](examples/config.example.toml). The only
required field is `source_dir`; everything else has a sensible default.
Config lives at `~/.config/qvd-mcp/config.toml` on macOS/Linux and
`%APPDATA%\qvd-mcp\config.toml` on Windows.

## Privacy & telemetry

`qvd-mcp` makes no outbound network calls during normal operation. There's
no telemetry, no analytics, and no phone-home. The server reads your
local QVDs, writes a local Parquet cache, and speaks to one local MCP
client over stdio. That's it.

## Attribution

Built on the shoulders of several fine open-source projects — PyQvd, DuckDB,
FastMCP, PyArrow, platformdirs, Typer, Rich. See [NOTICE.md](NOTICE.md) for
license notices and thanks.

## License

MIT. See [LICENSE](LICENSE).
