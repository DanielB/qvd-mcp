# Contributing to qvd-mcp

Small project, narrow scope. Issues and PRs welcome — bug reports against
real QVDs, README fixes, and reader edge cases are the most useful. For
anything larger, file an issue first so we can agree the shape before
you write code.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Dev setup

`qvd-mcp` uses [uv](https://github.com/astral-sh/uv):

```bash
git clone https://github.com/DanielB/qvd-mcp.git
cd qvd-mcp
uv sync --all-extras --dev
```

## Verification

Three commands must stay green for every change:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/qvd_mcp
```

CI runs them on Linux, macOS, and Windows × Python 3.11, 3.12, 3.13.

Conventions the suite enforces:

- `from __future__ import annotations` in every module.
- Full type annotations — `mypy --strict` passes on `src/qvd_mcp`.
- `logging` only, never `print()` in library code. The MCP server must
  not write to stdout; the stdio transport reserves it for JSON-RPC.

## Commits and PRs

One-line commit subject that describes the change; body optional. No
Conventional Commits required — write something a reviewer can skim six
months from now and still understand.

PR flow: branch off `main`, keep commits atomic, run the three
verification commands locally, open against `main`, CI must go green
before review. Small PRs merge faster than large ones.

## Scope

**In:** new MCP tools with clear use cases, reader edge-case fixes,
docs, platform-compatibility work, better error messages.

**Out, deliberately:**

- **Remote backends** (S3, Azure, SharePoint) — Phase 5 roadmap, not
  accepted as drive-by PRs until that phase opens.
- **Write-back into QVDs** — format is undocumented, writing is out of
  scope.
- **Telemetry or outbound network calls** during normal operation.
- **Weakening `run_sql` blocklists** — if your use case needs raw file
  access, it wants a different tool, not a weakened blocklist. See
  [SECURITY.md](SECURITY.md) for the threat model.

If you're not sure whether an idea fits, an issue is a cheap way to find
out.
