# meta-horizon-developer-mcp development guide

This file is the maintainer contract for this package. `README.md` is the user guide;
`ARCHITECTURE.md` describes the implementation and trust boundaries.

## Purpose

The package provides two entry points:

- `meta-horizon-developer`: an owner-operated CLI for login, collection, CSV capture, and
  snapshot inspection.
- `meta-horizon-developer-mcp`: a read-only stdio MCP server exposing the four tools defined
  in `src/meta_horizon_developer/mcp_server.py`.

The collector observes successful, allow-listed GraphQL Query responses emitted by the
Dashboard. It does not construct or replay requests.

## Invariants

- The MCP surface remains read-only: no mutations, arbitrary URLs, arbitrary operations,
  authentication flows, or cookie/token values are exposed.
- `dashboard.json` is the allowlist. Every operation must end in `Query`; required operations
  must be a subset of the allowlist.
- `app_id`, profile, and snapshot locations are explicit inputs or environment variables.
  Do not add a cwd-dependent default.
- A profile is single-process and a snapshot directory belongs to one application. Preserve
  the lock and app-id guard before changing collection behavior.
- Responses are redacted before they are written or returned. Never add credentials to
  configuration, snapshots, fixtures, logs, or MCP results.
- Snapshots are immutable JSON files with mode 0600. Profiles, snapshots, CSV output, and
  browser state are runtime data, not source files.

## Development

The package requires Python 3.13 or newer. From this directory:

```bash
uv run --project . pytest -q
uv run --project . ruff check src tests
```

When `uv` is unavailable, an equivalent Python 3.13 environment may run the same commands.
Do not add dependency installation to normal test or lint commands beyond the project
runner's standard environment setup.

When changing the Dashboard contract, update `src/meta_horizon_developer/dashboard.json`,
the corresponding tests, and the snapshot/tool documentation together. Keep the allowlist
read-only and verify that operation names remain stable.

When changing a CLI or MCP tool, update `README.md`, `ARCHITECTURE.md` when the flow or trust
boundary changes, and the relevant tests. Keep user-facing text in English.

## Submodule workflow

This repository is itself managed as a submodule by `mcp-gateway`. Commit package changes in
this repository first, then update the parent repository's gitlink. Do not commit runtime
state or credentials. Pushes require explicit approval.
