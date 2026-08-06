---
id: change-20260806-meta-horizon-portable-collector
kind: change
title: Extract the Meta Horizon collection engine as an app_id-independent standalone package
status: active
created: '2026-08-06'
profile: sdd@1
intent: The Meta Horizon Dashboard collection mechanism only worked inside data-analysis and could not be used from a different app (different app_id) or a different project. Extract the collection engine as an app_id-independent standalone package usable from both CLI and MCP, keeping DuckDB registration on the caller side.
outcomes:
- The collection engine accepts app_id as a runtime argument and can read a different app's Dashboard without duplicating the allowlist
- The package has no dependency on DuckDB / pandas and can go from collect to immutable JSON entirely on its own
- Chromium profile exclusion is guaranteed in code and cannot be opened concurrently regardless of which host calls it
- If a different app_id is mixed into the same snapshot-dir, collect fails and the contamination is detected
- The storage location has no default rooted at the working directory, so the profile stays fixed even under MCP startup where cwd is unpredictable
- The MCP server can switch its target app via startup arguments (--app-id / --snapshot-dir), while the published tools remain the same 4 read-only ones
- It can be referenced as a submodule from mcp-gateway's servers/ as a standalone repository
scope:
- 'pyproject.toml (new: standalone package definition)'
- 'src/meta_horizon_developer/core.py (new: collection engine body)'
- 'src/meta_horizon_developer/cli.py (new: meta-horizon-developer command)'
- 'src/meta_horizon_developer/mcp_server.py (new: read-only stdio MCP)'
- 'src/meta_horizon_developer/dashboard.json (new: Dashboard contract without app_id)'
- 'bin/meta-horizon-developer, bin/meta-horizon-developer-mcp (new: path-resolving launchers)'
- 'tests/test_core.py (new: engine tests)'
- 'README.md, AGENTS.md, ARCHITECTURE.md (new: usage, maintenance, and implementation docs)'
- 'docs/.docs-config.yaml, docs/.gitignore, docs/.transition-table.yaml, docs/aliases.yaml, docs/schemas/** (new: dev-docs init scaffolding for this package)'
- 'docs/adr/adr-20260806-meta-horizon-portable-collector.md (translated to English)'
non_goals:
- Caller-side (data-analysis) adaptation — turning DuckDB registration into a thin wrapper, injecting default paths, and maintaining staging/mart are changes on the data-analysis repository side
- Registration with mcp-gateway (an agent-module responsibility; the gateway decides the published name and how tools appear)
- Multiple projects collecting concurrently (structurally impossible, since there is one cookie set and the profile cannot be opened concurrently)
- Changes to the target operations, the default aggregation period, or the redaction rules
- Adding writes (Mutations) to Meta Horizon
change_classes:
- boundary
- dependency
- capability
governance:
  gate: auto
  reasons: []
members:
- role: requirements
  path: changes/change-20260806-meta-horizon-portable-collector/requirements.md
  required: true
- role: implementation
  path: changes/change-20260806-meta-horizon-portable-collector/implementation.md
  required: true
- role: verification
  path: changes/change-20260806-meta-horizon-portable-collector/verification.md
  required: true
evidence_refs:
- type: test
  ref: tests/test_core.py
- type: command
  ref: 'uv run python -m pytest  # 923 passed (run in the source workspace)'
- type: command
  ref: 'uv run ruff check  # All checks passed'
promotion:
- target: none
  section: none
  action: none
  reason: Implementation-only change; no architecture/component doc promotion applies.
unresolved_decisions:
- 'UD-001: pytest / ruff have not been re-run in the post-move repository. This is grounded in results verified as a workspace member of the source repository (owner: owner)'
tags:
- meta-horizon
- connector
- mcp
- portability
owners: []
relations:
- {type: implements, target: adr-20260806-meta-horizon-portable-collector}
source_paths:
- src/meta_horizon_developer/core.py
- src/meta_horizon_developer/cli.py
- src/meta_horizon_developer/mcp_server.py
- src/meta_horizon_developer/dashboard.json
summary: A vertical slice that extracts the Meta Horizon collection engine as an app_id-independent standalone package, giving it two entry points — CLI and read-only MCP.
updated: '2026-08-06'
---

## Summary

Meta Horizon Dashboard collection (opening the Analytics page with an
authenticated browser, intercepting and redacting only successful responses
of persisted GraphQL `Query` calls within the Dashboard's own allowlist, and
writing them to immutable JSON) was extracted from the data-analysis
repository into a standalone package.

This package's responsibility ends at **writing the immutable JSON**;
loading the collected results into DuckDB's staging/mart remains on the
caller side.

The full rationale for this decision is in ADR
`adr-20260806-meta-horizon-portable-collector`.

## Why

In response to the need to "use the same mechanism for a different app,"
before the extraction the allowlist JSON bundled `app_id`, which meant
**duplicating the whole allowlist even though the Dashboard's structure was
the same**. Duplication meant every copy had to be fixed whenever Meta
renamed an operation, and detection via `missing_operations` would drift
between copies.

## Structural constraint (not solved by this change)

Chromium's persistent profile can only be opened by one process at a time,
and there is only one cookie set. Therefore **there is exactly one collection
owner**, and other projects are limited to reading (MCP's `status` /
`read_operation`). Given that constraint, this change builds up to being able
to "swap out the collection owner" and "connect for reads from any host."
