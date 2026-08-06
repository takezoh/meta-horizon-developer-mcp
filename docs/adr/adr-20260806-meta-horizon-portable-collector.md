---
id: adr-20260806-meta-horizon-portable-collector
kind: adr
title: Extract the Meta Horizon collection engine into an app_id-independent standalone package, keep DuckDB registration in data-analysis
status: accepted
created: '2026-08-06'
decision_makers:
- owner
tags:
- meta-horizon
- connector
- mcp
- portability
owners: []
relations: []
source_paths:
- src/meta_horizon_developer/core.py
- src/meta_horizon_developer/cli.py
- src/meta_horizon_developer/mcp_server.py
- src/meta_horizon_developer/dashboard.json
summary: Move the Meta Horizon Dashboard collection engine into an app_id-independent standalone repository, leaving only DuckDB registration and default-path injection on the caller side (data-analysis). This lets other apps and other projects use the same engine.
updated: '2026-08-06'
---

## Context

This package's predecessor was `src/da_core/connectors/meta_horizon.py` in the
data-analysis repository, and it only worked inside that repository. A need
arose for other projects to use the same mechanism (reading a Dashboard for a
different game / different `app_id` the same way), but the following stood in
the way.

| Obstacle | Details |
|---|---|
| `app_id` bundled into the allowlist | The allowlist JSON holds pages, operations, and `app_id` in a single file. Watching a different app would mean duplicating the whole allowlist |
| Coupling to DuckDB | `register_snapshot()` calls data-analysis's DuckDB. A project that just wants to collect is forced to depend on the warehouse |
| Coupling to default paths | The profile and storage location are determined relative to the data-analysis repository root |
| Concurrency protection lived outside the core | Chromium's persistent profile can only be opened by one process at a time, but the only exclusion was the cron script's `flock`. Being called directly from another project could corrupt the profile |

Meanwhile, the core of collection (opening the Dashboard with an authenticated
browser, intercepting only successful responses of persisted GraphQL `Query`
calls within the Dashboard's own allowlist, redacting them, and writing them
to immutable JSON) **depends on neither `app_id` nor DuckDB**.

## Decision

**Extract the collection engine into a standalone package, leaving only DuckDB
registration and default-value injection on the caller side.**

1. **The boundary is "up to writing the immutable JSON."** This package's
   responsibility is auth / collect / capture-csv / status / read-operation.
   It does not load collection results into staging VIEWs or aggregate
   tables. DuckDB is not a dependency.
2. **`app_id` is a runtime argument.** `dashboard.json` becomes a "Dashboard
   contract" holding only pages and operations (allowlist / required list).
   `app_id` is supplied as a required CLI/MCP argument, or via
   `META_HORIZON_APP_ID`. Since the Dashboard's screen layout is the same
   regardless of app, **the allowlist is shared across all apps**.
3. **The caller decides where things live.** `--snapshot-dir` /
   `--profile-dir` / `--csv-dir` are taken as arguments. **There is no
   default rooted at the working directory** — since the MCP server is
   started by the host with an unpredictable cwd, having a default would mean
   the profile ends up in a different location depending on the launcher,
   producing the failure mode of being redirected to a login screen despite
   already being authenticated.
4. **`collect` rejects `app_id` contamination.** If a snapshot for a
   different `app_id` already exists in the same snapshot-dir, collection
   fails. One app per directory.
5. **The exclusive lock lives in the core.** `auth` / `collect` /
   `capture-csv` take a non-blocking exclusive lock on the profile and fail
   if it cannot be acquired. The caller's cron `flock` may remain as
   protection against redundant launches, but **correctness is grounded in
   the core**.
6. **MCP exposes only 4 read-only tools.** status / collect /
   latest_snapshot / read_operation. Authentication, arbitrary URLs,
   arbitrary operations, Mutations, and cookie/token values are not exposed.
7. **Distribution is a standalone repository + MCP.** Placed as a submodule
   under mcp-gateway's `servers/`, published commonly to all hosts from the
   gateway's `servers:`. **How it is registered with hosts and under what
   public name is a decision for the consumer (the gateway); this package
   only provides the executable and its argument contract.**

## Consequences

- (+) A Dashboard for a different `app_id` can be read without duplicating
  the allowlist. Adding a new app is a single argument
- (+) Projects that only want to collect no longer need DuckDB / pandas.
  The only dependencies are playwright and mcp
- (+) Profile exclusion is guaranteed by code. The profile cannot be opened
  concurrently even when called directly from another project
- (+) Being distributable as an MCP server means it can be used from
  non-Python projects too
- (−) There is now one extra layer of indirection from the caller's point of
  view. Fault isolation requires looking at this repository
- (−) Collection is limited to a single owner (one cookie set, the profile
  cannot be opened concurrently). **Multiple projects cannot each run their
  own collect**. Other projects are limited to reading (MCP's status /
  read_operation)
- (○) Snapshots captured before the extraction can still be read as-is. The
  format has not changed

## Alternatives

- **Only emit the resulting JSON to a shared directory**: minimal change, but
  each reader would have to interpret the snapshot structure on its own, so
  the contract would live only in the file format. It also would not answer
  the motivating need (supporting a different `app_id`), so this was not
  adopted
- **Shared Python package only (no MCP)**: usable only from Python projects.
  Other MCP servers in the same workspace are already shared via the gateway,
  and there is no reason to deviate from that
- **Extract DuckDB registration as well**: would require the collector to
  know the warehouse's structure (staging/mart naming, data-layer
  conventions), crossing the caller's area of responsibility. Not adopted
- **Keep it in the source repository as a uv workspace member**: has the
  advantage of verifying the extraction's correctness within a single
  repository, but every other project using it would have to reference the
  source repository's path, defeating the purpose of portability
