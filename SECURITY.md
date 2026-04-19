# Security policy

## Supported versions

`qvd-mcp` is pre-1.0 and ships fixes on the current minor. Older minors
stop receiving updates once a newer one is out.

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |

## Reporting a vulnerability

Please report suspected security issues privately to
**d.bengtsson@gmail.com**. Do not open a public GitHub issue for a
suspected vulnerability — it exposes other users until a fix ships.

A useful report includes:

- A short description of what you found.
- A minimal reproduction — the shortest path from a fresh install to
  the unsafe behaviour.
- The commit or released version you tested against.
- Your assessment of the impact (what can an attacker actually do?).

You can expect an initial reply within a few days and a fix or
disposition decision within two weeks for anything that clearly
warrants one.

## Threat model

`qvd-mcp` is designed for the curious LLM, not a malicious adversary.

The `run_sql` MCP tool is the main attack surface. It accepts arbitrary
SQL from an upstream LLM and runs it against DuckDB views over a
local Parquet cache. Two regex-based blocklists in `server.py` reject
SQL that would touch files outside the cache or attach external
databases:

- `_FORBIDDEN_FUNCTIONS` blocks table functions like `read_parquet`,
  `read_csv`, `read_json`, `read_text`, `glob`, and their variants.
- `_FORBIDDEN_STATEMENTS` blocks `ATTACH`, `DETACH`, `COPY`, `INSTALL`,
  `LOAD`, `PRAGMA`, `EXPORT`, and `IMPORT` at the start of a statement.

Those blocklists are intentionally conservative. They are not a
complete sandbox and should not be treated as one. If a determined
attacker already has shell access to the machine running `qvd-mcp`,
they can read the same files directly — the tool is not a defence
against that. The blocklists exist to keep an overeager LLM from
ad-libbing its way into `/etc/passwd` or an arbitrary database.

Anyone running `qvd-mcp` should still:

- Point `source_dir` only at QVDs where the full-row contents are
  acceptable for the user at the other end of the MCP client. The
  server does not enforce Qlik section-access rules; see the
  access-control note in the README.
- Treat the Parquet cache as sensitive — it contains the same data
  the source QVDs do.
- Keep `qvd-mcp` updated. Fixes for the blocklist will ship as
  point releases.

If you find an angle that bypasses those constraints — a SQL shape
that slips past the blocklists, a path the LLM can coax onto disk
through the tool surface, a way to exfiltrate cache contents through
the MCP protocol — that is a vulnerability and the reporting process
above applies.
