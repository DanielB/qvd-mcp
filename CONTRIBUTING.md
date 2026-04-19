# Contributing to qvd-mcp

Thanks for taking the time. This is a small project with a narrow scope,
but issues and pull requests are welcome — especially bug reports against
real QVDs, README improvements, and fixes for edge cases in the reader.

Before opening a larger change, it helps to file an issue first so the
shape of the fix can be agreed before any code is written.

By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Dev setup

`qvd-mcp` builds and runs with [uv](https://github.com/astral-sh/uv).
Install `uv` first, then:

```bash
git clone https://github.com/DanielB/qvd-mcp.git
cd qvd-mcp
uv sync --all-extras --dev
```

Smoke-test the checkout:

```bash
uv run qvd-mcp --version
uv run pytest -q
```

The MCP server entry point during development is `uv run qvd-mcp serve`.
Pointing your local Claude Desktop config at it lets you exercise the
real pipeline end-to-end; the `examples/` directory has a config
skeleton you can crib from.

## Verification

Three commands must stay green for every change:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/qvd_mcp
```

CI runs them on Linux, macOS, and Windows across Python 3.11, 3.12, and
3.13. If they're green locally they're almost always green in CI.

A couple of conventions the suite enforces:

- `from __future__ import annotations` at the top of every module.
- Full type annotations — `mypy --strict` must pass on `src/qvd_mcp`.
- `logging` only, never `print()` in library code. The MCP server must
  not write to stdout; the stdio transport reserves it for JSON-RPC.
  Log via `log.info` / `log.warning` / etc.

## Commit messages

One-line subject that describes the change. An optional body explains
why. No Conventional Commits enforcement, no issue-number prefix
required — just write something a reviewer can skim six months from
now and understand. Examples that work:

```
fix auto-refresh when source dir contains a broken symlink

The stat() call in _any_source_changed raised OSError on dangling
symlinks and aborted the probe. Treat any stat failure as "changed"
so the conversion pass gets a chance to prune the entry.
```

```
readme: rewrite the how-it-works section
```

If the subject is enough, the body can be skipped.

## Pull request flow

1. Fork the repo and create a branch off `main`.
2. Make the change, keep commits reasonably atomic, run the three
   verification commands locally.
3. Open a PR against `main`. The PR template has a short checklist.
4. CI runs on push. It must go green before a review.
5. A maintainer reviews, may ask for changes, and merges when ready.

Small PRs get merged faster than large ones. If you're planning
something bigger than a few files, opening an issue to sketch the
approach first saves everyone time.

## What's in scope

The goal of `qvd-mcp` is "query QVDs from an MCP client with SQL,
safely and without surprises." Changes that fit that goal are welcome:
new MCP tools with clear use cases, reader edge-case fixes, docs,
platform-compatibility work, better error messages.

A few things are out of scope on purpose:

- **Remote backends** (S3, Azure, SharePoint) — on the Phase 4
  roadmap, but not accepted as drive-by PRs until that phase opens.
- **Write-back into QVDs** — the format is not officially documented
  and writing is out of scope for this project.
- **Telemetry or outbound network calls** during normal operation.
- **Bypassing the `run_sql` blocklists** — if your use case needs
  raw file access through the MCP tool, it probably wants a different
  tool rather than a weakened blocklist. See [SECURITY.md](SECURITY.md)
  for the threat model.

If you're not sure whether an idea fits, an issue is a cheap way to
find out.
