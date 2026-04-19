# Security policy

## Supported versions

Pre-1.0; fixes ship on the current minor.

| Version | Supported |
| --- | --- |
| 0.1.x | Yes |

## Reporting

Email **d.bengtsson@gmail.com** for suspected vulnerabilities. Do not
open a public issue — it exposes other users until a fix ships.

A useful report includes:

- What you found.
- Shortest reproduction from a fresh install.
- The commit or released version you tested.
- Your impact assessment — what can an attacker actually do?

Initial reply within a few days; fix or disposition decision within two
weeks for anything that clearly warrants one.

## Threat model

Designed for the curious LLM, not a malicious adversary. An attacker
with shell access already has everything the tool could leak.

The `run_sql` MCP tool is the main attack surface — arbitrary SQL from
an upstream LLM, run against DuckDB views over a local Parquet cache.
Two regex blocklists in `server.py` reject the obvious foot-guns:

- **`_FORBIDDEN_FUNCTIONS`** — `read_parquet`, `read_csv`, `read_json`,
  `read_text`, `glob`, and variants in function-call form.
- **`_FORBIDDEN_STATEMENTS`** — `ATTACH`, `DETACH`, `COPY`, `INSTALL`,
  `LOAD`, `PRAGMA`, `EXPORT`, `IMPORT` at the start of a statement.

These are conservative, not a sandbox. Operators should still:

- Point `source_dir` only at QVDs where full-row access is appropriate
  for the user at the other end of the MCP client. The server does not
  enforce Qlik section-access rules.
- Treat the Parquet cache as sensitive — it holds the same data as the
  sources.
- Keep `qvd-mcp` updated; fixes ship as point releases.

Found a bypass — a SQL shape that slips past the blocklists, a path
that exfiltrates cache contents through MCP — report it per the process
above.
